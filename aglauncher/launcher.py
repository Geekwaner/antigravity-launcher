"""环境注入与进程拉起。

关键点：环境变量只能在 CreateProcess 时被继承，事后无法注入正在运行的进程。
所以这个工具必须"先退干净、再由我拉起"，而不是"给我一个正在跑的客户端打补丁"。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .client import Client
from .proxy import EndpointReport

NO_PROXY_VALUE = "localhost,127.0.0.1,::1"

_ENV_PAIRS = (
    ("HTTP_PROXY", "http_proxy"),
    ("HTTPS_PROXY", "https_proxy"),
    ("ALL_PROXY", "all_proxy"),
    ("NO_PROXY", "no_proxy"),
)

_MASK = "***"


@dataclass
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    preview: list[tuple[str, str]]
    warnings: list[str] = field(default_factory=list)
    proxy_url: str = ""

    def env_only(self) -> dict[str, str]:
        keys = {k for pair in _ENV_PAIRS for k in pair}
        return {k: v for k, v in self.env.items() if k in keys}


def _auth_segment(user: str | None, pwd: str | None) -> str:
    if not user:
        return ""
    from urllib.parse import quote

    seg = quote(user, safe="")
    if pwd:
        seg += ":" + quote(pwd, safe="")
    return seg + "@"


def build_plan(
    report: EndpointReport,
    user: str | None = None,
    pwd: str | None = None,
    extra_args: list[str] | None = None,
) -> LaunchPlan:
    warnings: list[str] = []
    scheme = report.scheme
    if scheme == "dead":
        raise ValueError("代理未通过校验，拒绝构建启动计划")

    auth = _auth_segment(user, pwd)
    netloc = f"{auth}{report.host}:{report.port}"
    http_url = f"http://{netloc}"
    socks_url = f"socks5://{netloc}"

    if scheme == "socks5":
        primary, all_url = socks_url, socks_url
    elif scheme == "http":
        primary, all_url = http_url, http_url
    else:  # mixed：具体协议各走各的最优路径
        primary, all_url = http_url, socks_url

    env = dict(os.environ)
    for pair in _ENV_PAIRS:
        for key in pair:
            env.pop(key, None)

    env["HTTP_PROXY"] = primary
    env["HTTPS_PROXY"] = primary
    env["ALL_PROXY"] = all_url
    env["NO_PROXY"] = NO_PROXY_VALUE
    env["http_proxy"] = primary
    env["https_proxy"] = primary
    env["all_proxy"] = all_url
    env["no_proxy"] = NO_PROXY_VALUE

    chromium_url = http_url if report.proto_http else socks_url
    if user or pwd:
        warnings.append("代理带认证信息：环境变量可以携带，但 --proxy-server 无法携带，Chromium 层会回落到直连")
        argv = []
    else:
        argv = [f"--proxy-server={chromium_url}"]
    argv.extend(extra_args or [])

    def mask(url: str) -> str:
        if not auth:
            return url
        return url.replace(auth, f"{_MASK}:{_MASK}@")

    preview = [
        ("HTTP_PROXY", mask(env["HTTP_PROXY"])),
        ("HTTPS_PROXY", mask(env["HTTPS_PROXY"])),
        ("ALL_PROXY", mask(env["ALL_PROXY"])),
        ("NO_PROXY", env["NO_PROXY"]),
        ("--proxy-server", mask(chromium_url) if argv else "（带认证时不附加）"),
    ]

    return LaunchPlan(
        argv=argv,
        env=env,
        preview=preview,
        warnings=warnings,
        proxy_url=mask(primary),
    )


def format_preview(key: str, value: str) -> str:
    """环境变量按 KEY = VALUE 排，Chromium 参数按原样拼接。"""
    if key.startswith("-"):
        return f"{key} {value}"
    return f"{key:<12} = {value}"


def spawn(client: Client, plan: LaunchPlan) -> subprocess.Popen:
    cmd = [str(client.exe), *plan.argv]
    kwargs: dict = {
        "env": plan.env,
        "cwd": str(client.root),
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        # 独立进程组，启动器退出后客户端继续跑
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def describe_argv(client: Client, plan: LaunchPlan) -> str:
    parts = [f'"{client.exe}"'] + [a if " " not in a else f'"{a}"' for a in plan.argv]
    return " ".join(parts)


def verify_injection(
    root_pid: int,
    launched_at: float,
    timeout: float = 60.0,
    popen: subprocess.Popen | None = None,
) -> tuple[bool, str]:
    """确认 language_server 已经挂在本次拉起的进程树下面，若父进程提前退出则立刻告警。"""
    from . import procs

    res = procs.wait_for_tree(root_pid, timeout=timeout, not_before=launched_at, popen=popen)
    if res.early_exit_code is not None:
        return False, (
            f"客户端启动后提前退出（退出码 {res.early_exit_code}），"
            "可能被单实例锁拦截或可执行文件异常"
        )
    if res.server is None:
        return False, "在超时时间内没有等到 language_server 启动（客户端可能仍在加载，或已被单实例锁拦下）"
    procs.raise_window(root_pid)
    chain = procs.ancestors(res.server.pid, res.table)
    path = " ← ".join([res.server.name] + [a.name for a in chain[:4]])
    return True, f"language_server 已在本进程树内启动：{path}（PID {res.server.pid}）"


def build_proxy_commands(
    report: EndpointReport,
    user: str | None = None,
    pwd: str | None = None,
    mask_secret: bool = False,
) -> dict[str, str]:
    """生成可直接复制到各类终端 / Git 中的代理设置命令。"""
    plan = build_plan(report, user=user, pwd=pwd)
    if mask_secret and (user or pwd):
        http = dict(plan.preview)["HTTP_PROXY"]
        socks = dict(plan.preview)["ALL_PROXY"]
    else:
        http = plan.env["HTTP_PROXY"]
        socks = plan.env["ALL_PROXY"]

    return {
        "powershell": (
            f'$env:HTTP_PROXY="{http}"; $env:HTTPS_PROXY="{http}"; '
            f'$env:ALL_PROXY="{socks}"; $env:NO_PROXY="{NO_PROXY_VALUE}"'
        ),
        "cmd": (
            f'set "HTTP_PROXY={http}" && set "HTTPS_PROXY={http}" && '
            f'set "ALL_PROXY={socks}" && set "NO_PROXY={NO_PROXY_VALUE}"'
        ),
        "bash": (
            f'export HTTP_PROXY="{http}" && export HTTPS_PROXY="{http}" && '
            f'export ALL_PROXY="{socks}" && export NO_PROXY="{NO_PROXY_VALUE}"'
        ),
        "git": f"git config --global http.proxy {http} && git config --global https.proxy {http}",
        "unset_powershell": "Remove-Item env:HTTP_PROXY, env:HTTPS_PROXY, env:ALL_PROXY, env:NO_PROXY -ErrorAction SilentlyContinue",
        "unset_bash": "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy",
    }


def open_proxy_terminal(
    report: EndpointReport | None,
    user: str | None = None,
    pwd: str | None = None,
    work_dir: str | None = None,
    shell_type: str = "powershell",
    run_exe: Path | None = None,
) -> Path:
    """打开一个已注入代理环境变量的独立终端，可选直接进入 agy CLI。"""
    wd = Path(work_dir) if work_dir and Path(work_dir).is_dir() else Path.home()
    env = dict(os.environ)
    banner = "[AGY] Terminal ready"
    if report is not None and report.usable:
        plan = build_plan(report, user=user, pwd=pwd)
        env = plan.env
        banner = f"[AGY] Proxy injected -> {plan.proxy_url}"

    if sys.platform.startswith("win"):
        if shell_type == "cmd":
            tail = f' & "{run_exe}"' if run_exe else ""
            cmd_line = f'cd /d "{wd}" & echo {banner}{tail}'
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", "cmd.exe", "/K", cmd_line],
                cwd=str(wd),
                env=env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            def _ps_quote(s: str) -> str:
                return "'" + s.replace("'", "''") + "'"

            tail = f"; & {_ps_quote(str(run_exe))}" if run_exe else ""
            ps_cmd = f"Set-Location {_ps_quote(str(wd))}; Write-Host {_ps_quote(banner)} -ForegroundColor Cyan{tail}"
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", "powershell.exe", "-NoExit", "-Command", ps_cmd],
                cwd=str(wd),
                env=env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
    elif sys.platform == "darwin":
        import shlex

        tail = f" && {shlex.quote(str(run_exe))}" if run_exe else ""
        shell_cmd = f"cd {shlex.quote(str(wd))} && echo {shlex.quote(banner)}{tail}"
        escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(
            ["osascript", "-e", f'tell application "Terminal" to do script "{escaped}"'],
            env=env,
            start_new_session=True,
        )
    else:
        import shlex

        tail = f"; {shlex.quote(str(run_exe))}" if run_exe else ""
        bash_cmd = f"cd {shlex.quote(str(wd))}; echo {shlex.quote(banner)}{tail}; exec bash"
        for term, args in (
            ("x-terminal-emulator", ["-e", "bash", "-c", bash_cmd]),
            ("gnome-terminal", ["--", "bash", "-c", bash_cmd]),
        ):
            try:
                subprocess.Popen([term, *args], cwd=str(wd), env=env, start_new_session=True)
                break
            except FileNotFoundError:
                continue
    return wd


def ensure_exited(popen: subprocess.Popen, timeout: float = 5.0) -> None:
    try:
        popen.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


def state_dir() -> Path:
    from .store import config_dir

    return config_dir() / "logs"
