"""探测流水线：定位 → 校验代理 → 查残留 → 出计划。

GUI 与 CLI 共用这一条路径，保证"界面上看到的"和"命令行体检的"是同一套判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import client as client_mod
from . import procs, proxy
from .launcher import LaunchPlan, build_plan


@dataclass
class Report:
    client: client_mod.Client | None = None
    cli: client_mod.CliTool | None = None
    endpoint: proxy.EndpointReport | None = None
    own_desktop: procs.ProcInfo | None = None
    residuals: list[procs.ProcInfo] = field(default_factory=list)
    cli_sessions: list[procs.ProcInfo] = field(default_factory=list)
    plan: LaunchPlan | None = None
    notes: list[str] = field(default_factory=list)
    candidates: list[proxy.Candidate] = field(default_factory=list)
    proxy_user: str | None = None
    proxy_pwd: str | None = None

    @property
    def blocked(self) -> bool:
        return self.block_reason is not None

    @property
    def block_reason(self) -> str | None:
        if self.client is None:
            return "未找到 Antigravity 客户端"
        if self.own_desktop is not None:
            return f"客户端已由本启动器拉起并运行中（PID {self.own_desktop.pid}）"
        if self.residuals:
            return f"检测到 {len(self.residuals)} 个未注入代理的残留实例"
        if self.endpoint is None or not self.endpoint.usable:
            return "代理未通过真实握手校验"
        return None

    @property
    def ready(self) -> bool:
        return self.block_reason is None

    @property
    def degraded(self) -> bool:
        return bool(self.endpoint and self.endpoint.usable and not self.endpoint.all_green)


def inspect(
    client_path: str | None = None,
    cli_path: str | None = None,
    proxy_url: str | None = None,
    attempts: int = 2,
    timeout: float = 6.0,
    launched_record: dict | None = None,
    on_log: Callable[[str], None] | None = None,
) -> Report:
    log = on_log or (lambda _m: None)
    report = Report()

    # 1) 定位客户端 & CLI (agy)
    found, notes = client_mod.locate(client_path)
    report.client = found
    report.notes.extend(notes)
    if found:
        log(f"客户端：{found.exe}（{found.source}）")
        log(f"核心网关：{found.language_server}")
    else:
        log("未找到 Antigravity 客户端，需要手动指定 Antigravity.exe")
        for n in notes:
            log(f"  · {n}")

    cli_found, cli_notes = client_mod.locate_cli(cli_path)
    report.cli = cli_found
    report.notes.extend(cli_notes)
    if cli_found:
        log(f"终端 CLI：{cli_found.exe}（{cli_found.source}）")

    # 2) 检查运行状态（区分己方实例与未注入代理的残留实例）
    snap = procs.snapshot()
    report.own_desktop, report.residuals = procs.split_running(snap, launched_record)
    report.cli_sessions = procs.find_cli_sessions(snap)
    if report.own_desktop:
        log(f"检测到由本启动器拉起的运行中实例：{report.own_desktop.name} #{report.own_desktop.pid}")
    if report.residuals:
        log(f"发现未注入代理的残留实例 {len(report.residuals)} 个，启动前必须清干净：")
        for p in report.residuals:
            log(f"  · {p.name} #{p.pid}")
    elif not report.own_desktop:
        log("无残留实例")

    # 3) 校验代理
    if proxy_url:
        try:
            host, port, user, pwd, hint = proxy.parse_proxy_url(proxy_url)
        except ValueError as exc:
            log(f"手动指定的代理无效：{exc}")
            report.notes.append(str(exc))
        else:
            log(f"校验手动指定端点 {host}:{port}（声明协议 {hint}）")
            report.proxy_user, report.proxy_pwd = user, pwd
            report.endpoint = proxy.probe_endpoint(
                host, port, attempts=attempts, timeout=timeout, on_log=log, user=user, pwd=pwd
            )
            report.endpoint.origin = "手动指定"
            report.candidates = [proxy.Candidate(host, port, "手动指定")]
    else:
        report.endpoint, auto_notes, cands = proxy.autodetect(
            attempts=attempts, timeout=timeout, on_log=log
        )
        report.notes.extend(auto_notes)
        report.candidates = cands

    if report.endpoint is None:
        log("没有任何端点通过真实握手校验")
    elif not report.endpoint.all_green:
        log("端点可用，但通道未全绿：登录与模型端点可能走不同线路")

    # 4) 启动计划
    if report.client and report.endpoint and report.endpoint.usable:
        try:
            report.plan = build_plan(
                report.endpoint, user=report.proxy_user, pwd=report.proxy_pwd
            )
            for warn in report.plan.warnings:
                log(f"注意：{warn}")
        except ValueError as exc:
            log(f"无法构建启动计划：{exc}")

    return report
