"""命令行模式。

`--doctor` 只读体检：不杀进程、不启动客户端，适合先看清现状。
`--launch` 无界面启动：判定链与 GUI 完全一致。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict

from . import __version__, client as client_mod, core, procs, proxy, store
from .launcher import (
    build_plan,
    describe_argv,
    format_preview,
    open_proxy_terminal,
    spawn,
    verify_injection,
)

EXIT_OK = 0
EXIT_CLIENT_MISSING = 4
EXIT_PROXY_DEAD = 3
EXIT_RESIDUAL = 2
EXIT_ERROR = 1


def _hr(char: str = "─", width: int = 68) -> str:
    return char * width


def _tolerant_stdio() -> None:
    """Windows 控制台默认 GBK，报告里的 ✔/✘ 不在 GBK 内，直接 print 会抛 UnicodeEncodeError。

    这里只把编码错误降级为替换字符，既不改终端编码，也不影响 UTF-8 终端下的正常显示。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass


def _print_report(report: core.Report, verbose: bool) -> None:
    print()
    print("客户端")
    print(_hr())
    if report.client:
        print(f"  主程序      {report.client.exe}")
        print(f"  核心网关    {report.client.language_server}")
        print(f"  定位方式    {report.client.source}")
    else:
        print("  未找到 Antigravity 客户端")
    if report.cli:
        print(f"  终端 CLI    {report.cli.exe}（{report.cli.source}）")
    else:
        print("  终端 CLI    未检测到 agy")

    print()
    print("代理校验")
    print(_hr())
    ep = report.endpoint
    if ep is None:
        print("  未发现通过真实握手校验的端点")
    else:
        scheme = {"mixed": "mixed（HTTP + SOCKS5）", "http": "HTTP CONNECT", "socks5": "SOCKS5"}.get(
            ep.scheme, "不可用"
        )
        print(f"  端点        {ep.addr}")
        print(f"  协议能力    {scheme}")
        print(f"  登录端点    {'✔ 通过' if ep.login.ok else '✘ 未通过'}  {ep.login.detail}")
        print(f"  模型端点    {'✔ 通过' if ep.model.ok else '✘ 未通过'}  {ep.model.detail}")
        print(f"  校验耗时    {ep.elapsed:.1f}s")
        if verbose:
            print()
            print("  逐次尝试")
            for a in ep.attempts:
                mark = "✔" if a.ok else "✘"
                print(f"    {mark} {a.proto:<6} {a.key:<6} {a.ms:>5}ms  {a.detail}")

    print()
    print("运行状态 / 残留实例")
    print(_hr())
    if report.own_desktop:
        print(f"  ✔ 客户端运行中（代理已注入，PID {report.own_desktop.pid}）")
    if report.residuals:
        print(f"  {len(report.residuals)} 个未注入代理的实例正在运行：")
        for p in report.residuals:
            print(f"    · {p.name:<22} PID {p.pid:<7} 父进程 {p.ppid}")
    elif not report.own_desktop:
        print("  无残留实例")
    if report.cli_sessions:
        print(f"  终端 agy 会话：{len(report.cli_sessions)} 个（不阻断启动）")

    if report.notes:
        print()
        print("探测说明")
        print(_hr())
        for n in report.notes:
            print(f"  · {n}")

    print()
    print("判定")
    print(_hr())
    if report.own_desktop and not report.residuals and report.endpoint and report.endpoint.usable:
        print(f"  ✔ 代理已注入并运行中（PID {report.own_desktop.pid}）")
    elif report.blocked:
        print(f"  ✘ 已阻断：{report.block_reason}")
    elif report.degraded:
        print("  ! 可启动，但有通道未通过（建议先换节点再试）")
    else:
        print("  ✔ 三条通道全绿，可以启动")

    if report.plan:
        print()
        print("将注入的环境变量")
        print(_hr())
        for k, v in report.plan.preview:
            print(f"  {format_preview(k, v)}")
        for w in report.plan.warnings:
            print(f"  注意：{w}")
    print()


def _print_diag(diag: proxy.NetDiagResult) -> None:
    print("网络诊断")
    print(_hr())
    if diag.direct_ip:
        loc = " ".join(x for x in (diag.direct_ip.country, diag.direct_ip.city) if x)
        print(f"  直连出口    {diag.direct_ip.ip} ({loc})")
    else:
        print(f"  直连出口    查询失败（{diag.direct_error}）")
    if diag.proxy_ip:
        loc = " ".join(x for x in (diag.proxy_ip.country, diag.proxy_ip.city) if x)
        print(f"  代理出口    {diag.proxy_ip.ip} ({loc})")
    else:
        print(f"  代理出口    查询失败（{diag.proxy_error}）")
    for p in diag.pings:
        mark = "✔" if p.ok else "✘"
        print(f"  {mark} {p.label:<8} {p.ms:>4}ms  {p.host}  ({p.detail})")
    print()


def _use_preflight(args: argparse.Namespace, cfg: dict) -> bool:
    if args.preflight is not None:
        return bool(args.preflight)
    return bool(cfg.get("preflight_check", True))


def run_doctor(args: argparse.Namespace) -> int:
    cfg = store.load()
    report = core.inspect(
        client_path=args.client or cfg.get("client_path"),
        cli_path=args.cli_path or cfg.get("cli_path"),
        proxy_url=args.proxy or cfg.get("proxy_url"),
        attempts=args.attempts,
        timeout=args.timeout,
        launched_record=cfg.get("launched_desktop"),
        on_log=lambda m: print(f"  · {m}") if args.verbose else None,
    )
    diag: proxy.NetDiagResult | None = None
    if args.diag:
        diag = proxy.diagnose_network(
            report.endpoint, user=report.proxy_user, pwd=report.proxy_pwd, timeout=args.timeout
        )

    if args.json:
        payload = {
            "client": str(report.client.exe) if report.client else None,
            "language_server": str(report.client.language_server) if report.client else None,
            "cli": str(report.cli.exe) if report.cli else None,
            "proxy": {
                "endpoint": report.endpoint.addr if report.endpoint else None,
                "scheme": report.endpoint.scheme if report.endpoint else None,
                "login_ok": bool(report.endpoint and report.endpoint.login.ok),
                "model_ok": bool(report.endpoint and report.endpoint.model.ok),
                "attempts": [
                    {"proto": a.proto, "target": a.key, "ok": a.ok, "ms": a.ms, "detail": a.detail}
                    for a in (report.endpoint.attempts if report.endpoint else [])
                ],
            },
            "own_desktop": (
                {"pid": report.own_desktop.pid, "created": report.own_desktop.created}
                if report.own_desktop else None
            ),
            "residuals": [{"pid": p.pid, "ppid": p.ppid, "name": p.name} for p in report.residuals],
            "cli_sessions": [{"pid": p.pid, "name": p.name} for p in report.cli_sessions],
            "blocked": report.block_reason if not (report.own_desktop and not report.residuals) else None,
            "env": dict(report.plan.preview) if report.plan else {},
        }
        if diag is not None:
            payload["diagnostics"] = {
                "direct_ip": asdict(diag.direct_ip) if diag.direct_ip else None,
                "proxy_ip": asdict(diag.proxy_ip) if diag.proxy_ip else None,
                "direct_error": diag.direct_error,
                "proxy_error": diag.proxy_error,
                "pings": [asdict(p) for p in diag.pings],
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(report, args.verbose)
        if diag is not None:
            _print_diag(diag)

    if report.client is None:
        return EXIT_CLIENT_MISSING
    if report.residuals:
        return EXIT_RESIDUAL
    if report.endpoint is None or not report.endpoint.usable:
        return EXIT_PROXY_DEAD
    return EXIT_OK


def _run_preflight_or_fail(report: core.Report, timeout: float) -> bool:
    if report.endpoint is None:
        return False
    print("正在执行 Google 真实可达性校验（www.google.com/generate_204）…")
    pf = proxy.check_google_reachability(
        report.endpoint,
        user=report.proxy_user,
        pwd=report.proxy_pwd,
        attempts=2,
        timeout=timeout,
        on_attempt=lambda idx, ok, detail: print(
            f"  {'✔' if ok else '·'} 第 {idx} 次：{detail}"
        ),
    )
    if not pf.ok:
        print(f"  ✘ Google 可达性校验未通过：{pf.detail}")
        return False
    print(f"  ✔ 前置校验通过：{pf.detail}（{pf.ms}ms）")
    return True


def run_launch(args: argparse.Namespace) -> int:
    cfg = store.load()
    client_path = args.client or cfg.get("client_path")
    # 无网络快速前置拦截：客户端缺失 / 己方已在跑 / 存在未清理的外部残留实例时，不浪费时间扫代理
    fast_client, _ = client_mod.locate(client_path)
    if fast_client is None:
        print("未找到 Antigravity 客户端，请先安装或通过 --client 指定路径。")
        return EXIT_CLIENT_MISSING
    own, residuals = procs.split_running(record=cfg.get("launched_desktop"))
    if own and not residuals:
        print(f"Antigravity 已由本启动器拉起并正在运行（PID {own.pid}），无需重复启动。")
        return EXIT_OK
    if residuals and not args.kill_residuals:
        print(f"发现 {len(residuals)} 个未注入代理的残留实例，已阻断。可加 --kill-residuals 自动清理。")
        return EXIT_RESIDUAL

    report = core.inspect(
        client_path=client_path,
        cli_path=args.cli_path or cfg.get("cli_path"),
        proxy_url=args.proxy or cfg.get("proxy_url"),
        attempts=args.attempts,
        timeout=args.timeout,
        launched_record=cfg.get("launched_desktop"),
        on_log=lambda m: print(f"  · {m}"),
    )
    _print_report(report, args.verbose)

    if report.residuals:
        if args.kill_residuals:
            print("正在关闭残留实例…")
            closed, still = procs.close_instances(report.residuals)
            print(f"  已关闭 {len(closed)} 个，仍未退出 {len(still)} 个")
            if still:
                return EXIT_RESIDUAL
            report.residuals = []
        else:
            print("已阻断。清理实例后可加 --kill-residuals 重试。")
            return EXIT_RESIDUAL

    if report.client is None:
        return EXIT_CLIENT_MISSING
    if report.endpoint is None or not report.endpoint.usable:
        return EXIT_PROXY_DEAD

    if _use_preflight(args, cfg) and not _run_preflight_or_fail(report, args.timeout):
        return EXIT_PROXY_DEAD

    plan = build_plan(report.endpoint, user=report.proxy_user, pwd=report.proxy_pwd, extra_args=args.arg)
    launched_at = time.time()
    popen = spawn(report.client, plan)
    print(f"已拉起 PID {popen.pid}，等待 language_server 接管…")
    print(f"  {describe_argv(report.client, plan)}")
    ok, message = verify_injection(popen.pid, launched_at, timeout=args.wait, popen=popen)
    print(("  ✔ " if ok else "  ! ") + message)
    if ok:
        snap = procs.snapshot()
        proc_rec = next((p for p in snap if p.pid == popen.pid), None)
        cfg["launched_desktop"] = {
            "pid": popen.pid,
            "created": proc_rec.created if proc_rec else launched_at,
            "exe": str(report.client.exe),
        }
        store.save(cfg)
    return EXIT_OK if ok else EXIT_ERROR


def run_terminal(args: argparse.Namespace) -> int:
    cfg = store.load()
    report = core.inspect(
        client_path=args.client or cfg.get("client_path"),
        cli_path=args.cli_path or cfg.get("cli_path"),
        proxy_url=args.proxy or cfg.get("proxy_url"),
        attempts=args.attempts,
        timeout=args.timeout,
        launched_record=cfg.get("launched_desktop"),
        on_log=lambda m: print(f"  · {m}"),
    )
    if args.agy and report.cli is None:
        print("未检测到 agy 可执行文件，请先安装 Antigravity CLI 或用 --cli-path 指定。")
        return EXIT_CLIENT_MISSING
    if report.endpoint is None or not report.endpoint.usable:
        print("代理未通过真实握手校验。")
        return EXIT_PROXY_DEAD
    if args.agy and _use_preflight(args, cfg) and not _run_preflight_or_fail(report, args.timeout):
        return EXIT_PROXY_DEAD

    wd = open_proxy_terminal(
        report.endpoint,
        user=report.proxy_user,
        pwd=report.proxy_pwd,
        work_dir=args.work_dir or cfg.get("work_dir"),
        shell_type=args.shell,
        run_exe=report.cli.exe if args.agy and report.cli else None,
    )
    target_desc = f"agy ({report.cli.exe})" if args.agy and report.cli else args.shell
    print(f"  ✔ 已在 {wd} 打开注入代理的终端：{target_desc}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aglaunch",
        description="Antigravity 进程级代理启动器：只给被拉起的这一棵进程树注入代理。",
    )
    p.add_argument("--version", action="version", version=f"Antigravity Launcher {__version__}")
    p.add_argument("--doctor", action="store_true", help="只读体检，不杀进程也不启动客户端")
    p.add_argument("--launch", action="store_true", help="体检通过后直接无界面启动桌面客户端")
    p.add_argument("--agy", action="store_true", help="打开注入代理的终端并启动 agy CLI")
    p.add_argument("--terminal", action="store_true", help="打开注入代理的交互终端")
    p.add_argument("--diag", action="store_true", help="配合 --doctor 运行出口 IP 对比与延迟诊断")
    p.add_argument("--client", help="手动指定 Antigravity 主程序路径")
    p.add_argument("--cli-path", help="手动指定 agy CLI 路径")
    p.add_argument("--proxy", help="手动指定代理，如 http://127.0.0.1:7897 或 socks5://127.0.0.1:1080")
    p.add_argument("--work-dir", help="打开终端或 agy 时的工作目录")
    p.add_argument("--shell", choices=("powershell", "cmd"), default="powershell", help="Windows 终端类型")
    p.add_argument("--attempts", type=int, default=None, help="每个（协议,目标）的重试次数，默认 2")
    p.add_argument("--timeout", type=float, default=None, help="单次握手超时秒数，默认 6")
    p.add_argument("--verify-timeout", dest="wait", type=float, default=90.0, help="等待 language_server 的超时秒数")
    p.add_argument("--kill-residuals", action="store_true", help="启动前关掉残留实例")
    p.add_argument("--preflight", dest="preflight", action="store_true", default=None, help="启动前验证 Google 可达性")
    p.add_argument("--no-preflight", dest="preflight", action="store_false", help="跳过启动前 Google 可达性校验")
    p.add_argument("--arg", action="append", default=[], help="透传给客户端的额外参数，可重复")
    p.add_argument("--json", action="store_true", help="以 JSON 输出体检结果")
    p.add_argument("-v", "--verbose", action="store_true", help="输出逐次握手明细")
    return p


def main(argv: list[str] | None = None) -> int:
    _tolerant_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = store.load()
    if args.attempts is None:
        args.attempts = int(cfg.get("verify_attempts", 2))
    if args.timeout is None:
        args.timeout = float(cfg.get("verify_timeout", 6.0))

    if args.doctor:
        return run_doctor(args)
    if args.launch:
        return run_launch(args)
    if args.agy or args.terminal:
        return run_terminal(args)

    from .gui import main as gui_main

    gui_main()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
