"""代理发现与真实握手校验。

三条原则：
1. 端口能连上 ≠ 它是代理；必须真的建立隧道。
2. 隧道建立 ≠ 线路通；必须穿过隧道完成一次 TLS 握手。
3. 域名交给代理去解析（本机 DNS 正是被污染的那一环），所以 SOCKS5 用域名寻址。

登录端点与模型端点分开验证：这两个域名走的线路可能完全不同（Google 账号体系 vs
Cloud Code PA 的推理后端），一个通一个不通是常态，不能合并成一个"代理是否可用"。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urlsplit

LOGIN_HOST = "accounts.google.com"
MODEL_HOST = "daily-cloudcode-pa.googleapis.com"
PREFLIGHT_HOST = "www.google.com"
PREFLIGHT_PATH = "/generate_204"
PORT = 443

# (key, 显示名, host)
TARGETS: tuple[tuple[str, str, str], ...] = (
    ("login", "登录端点", LOGIN_HOST),
    ("model", "模型端点", MODEL_HOST),
)

DIAG_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("google", "Google", LOGIN_HOST),
    ("model", "模型端点", MODEL_HOST),
    ("github", "GitHub", "github.com"),
)

COMMON_PORTS = (
    7890, 7891, 7892, 7893, 7896, 7897, 7898, 7899,   # Clash / Clash Verge / mihomo
    1080, 1081, 1087, 10808, 10809, 10810,           # SOCKS 系 / v2rayN
    2080, 2081, 20171, 20172,                         # 其他本地客户端的常见落点
    8080, 8118, 8888, 8889, 6152, 6153,               # 通用 HTTP 代理
)

MAX_PROBE_WORKERS = 8


@dataclass
class Candidate:
    host: str
    port: int
    origin: str


@dataclass
class Attempt:
    proto: str          # http | socks5
    key: str            # login | model
    ok: bool
    detail: str
    ms: int


@dataclass
class Channel:
    key: str
    label: str
    host: str
    ok: bool = False
    proto: str | None = None
    detail: str = "未检测"
    ms: int | None = None


@dataclass
class EndpointReport:
    host: str
    port: int
    proto_http: bool = False
    proto_socks: bool = False
    login: Channel = field(default=None)   # type: ignore[assignment]
    model: Channel = field(default=None)   # type: ignore[assignment]
    attempts: list[Attempt] = field(default_factory=list)
    elapsed: float = 0.0
    origin: str = ""

    @property
    def scheme(self) -> str:
        if self.proto_http and self.proto_socks:
            return "mixed"
        if self.proto_http:
            return "http"
        if self.proto_socks:
            return "socks5"
        return "dead"

    @property
    def usable(self) -> bool:
        return self.proto_http or self.proto_socks

    @property
    def all_green(self) -> bool:
        return bool(self.login and self.login.ok and self.model and self.model.ok)

    @property
    def addr(self) -> str:
        return f"{self.host}:{self.port}"

    def channels(self) -> list[Channel]:
        return [c for c in (self.login, self.model) if c is not None]


# --------------------------------------------------------------------------- #
# 系统代理读取
# --------------------------------------------------------------------------- #

def _split_proxy_server(raw: str) -> list[tuple[str, int, str]]:
    """ProxyServer 可能是 `127.0.0.1:7897`，也可能是 `http=..;https=..;socks=..`。"""
    out: list[tuple[str, int, str]] = []
    raw = (raw or "").strip()
    if not raw:
        return out
    if "=" in raw:
        for part in raw.split(";"):
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            key = key.strip().lower()
            parsed = _host_port(val.strip())
            if parsed:
                out.append((parsed[0], parsed[1], f"系统代理({key})"))
    else:
        parsed = _host_port(raw)
        if parsed:
            out.append((parsed[0], parsed[1], "系统代理"))
    return out


def _host_port(raw: str) -> tuple[str, int] | None:
    raw = raw.strip().rstrip("/")
    if not raw:
        return None
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if raw.startswith("["):  # IPv6
        end = raw.find("]")
        if end == -1:
            return None
        host = raw[1:end]
        port = raw[end + 2:] if raw[end + 1:end + 2] == ":" else "80"
    else:
        if raw.count(":") != 1:
            return None
        host, port = raw.rsplit(":", 1)
    try:
        return host or "127.0.0.1", int(port)
    except ValueError:
        return None


def system_proxy_candidates() -> tuple[list[Candidate], list[str]]:
    """读操作系统的系统代理设置。Windows 读注册表，macOS 读 scutil，Linux 读 gsettings。"""
    notes: list[str] = []
    cands: list[Candidate] = []

    if sys.platform.startswith("win"):
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                def q(name: str, default=None):
                    try:
                        return winreg.QueryValueEx(key, name)[0]
                    except FileNotFoundError:
                        return default

                enable = q("ProxyEnable", 0)
                server = q("ProxyServer", "") or ""
                pac = q("AutoConfigURL", "") or ""
            if pac:
                notes.append(f"系统启用了 PAC 自动配置（{pac}），PAC 里的代理无法静态读取")
            if enable:
                for host, port, origin in _split_proxy_server(server):
                    cands.append(Candidate(host, port, origin))
            elif server and not pac:
                notes.append("系统代理已关闭（ProxyEnable=0），但残留了地址配置，仍作为候选探测")
                for host, port, origin in _split_proxy_server(server):
                    cands.append(Candidate(host, port, origin + "(未启用)"))
        except OSError as exc:
            notes.append(f"读取系统代理失败：{exc}")

    elif sys.platform == "darwin":
        try:
            import subprocess

            out = subprocess.run(
                ["scutil", "--proxy"], capture_output=True, text=True, timeout=5
            ).stdout
            info: dict[str, str] = {}
            for line in out.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
            if info.get("HTTPEnable") == "1" and info.get("HTTPPort"):
                cands.append(Candidate("127.0.0.1", int(info["HTTPPort"]), "系统代理(HTTP)"))
            if info.get("HTTPSEnable") == "1" and info.get("HTTPSPort"):
                cands.append(Candidate("127.0.0.1", int(info["HTTPSPort"]), "系统代理(HTTPS)"))
            if info.get("SOCKSEnable") == "1" and info.get("SOCKSPort"):
                cands.append(Candidate("127.0.0.1", int(info["SOCKSPort"]), "系统代理(SOCKS)"))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"读取 scutil 代理失败：{exc}")

    else:
        import subprocess

        for schema, key, origin in (
            ("org.gnome.system.proxy.http", "port", "系统代理(HTTP)"),
            ("org.gnome.system.proxy.https", "port", "系统代理(HTTPS)"),
            ("org.gnome.system.proxy.socks", "port", "系统代理(SOCKS)"),
        ):
            try:
                port = subprocess.run(
                    ["gsettings", "get", schema, key], capture_output=True, text=True, timeout=4
                ).stdout.strip()
                if port.isdigit() and int(port) > 0:
                    cands.append(Candidate("127.0.0.1", int(port), origin))
            except Exception:  # noqa: BLE001
                pass

    return cands, notes


def env_proxy_candidates() -> list[Candidate]:
    out: list[Candidate] = []
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        parsed = _host_port(raw)
        if parsed:
            out.append(Candidate(parsed[0], parsed[1], f"环境变量 {name}"))
    return out


def tcp_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_ports(timeout: float = 0.35) -> list[int]:
    """并扫常见本地端口，返回正在监听的。"""
    open_ports: list[int] = []
    with ThreadPoolExecutor(max_workers=len(COMMON_PORTS)) as pool:
        results = pool.map(lambda p: (p, tcp_open("127.0.0.1", p, timeout)), COMMON_PORTS)
        for port, ok in results:
            if ok:
                open_ports.append(port)
    return open_ports


# --------------------------------------------------------------------------- #
# 真实握手
# --------------------------------------------------------------------------- #

def _tls_through(sock: socket.socket, host: str, timeout: float) -> tuple[bool, str]:
    ctx = ssl.create_default_context()
    # 只验证"隧道能承载真实 TLS 流量"，不校验证书链：
    # 部分代理会做 MITM，证书由本地根签发，用系统信任库判定会把可用线路误判成死线。
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    sock.settimeout(timeout)
    try:
        tls = ctx.wrap_socket(sock, server_hostname=host)
        try:
            return True, tls.version() or "TLS"
        finally:
            try:
                tls.close()
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        try:
            sock.close()
        except OSError:
            pass
        return False, _short_err(exc)


def _short_err(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc) or ""
    if "timed out" in text.lower() or name == "TimeoutError":
        return "握手超时"
    if "EOF occurred" in text:
        return "对端提前断开（TLS EOF）"
    if isinstance(exc, ConnectionRefusedError):
        return "连接被拒绝"
    if isinstance(exc, socket.gaierror):
        return f"域名解析失败：{text}"
    return f"{name}: {text}"[:120]


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _basic_auth_header(user: str | None, pwd: str | None) -> str:
    if not user:
        return ""
    token = base64.b64encode(f"{user}:{pwd or ''}".encode("utf-8")).decode("ascii")
    return f"Proxy-Authorization: Basic {token}\r\n"


def _open_http_tunnel(
    proxy: tuple[str, int],
    host: str,
    port: int,
    timeout: float,
    user: str | None = None,
    pwd: str | None = None,
) -> tuple[socket.socket | None, str]:
    try:
        sock = socket.create_connection(proxy, timeout=timeout)
    except OSError as exc:
        return None, _short_err(exc)
    try:
        sock.settimeout(timeout)
        auth_hdr = _basic_auth_header(user, pwd)
        req = (
            f"CONNECT {host}:{port} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"{auth_hdr}"
            f"Proxy-Connection: keep-alive\r\n\r\n"
        )
        sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 16384:
                break
        if not buf:
            sock.close()
            return None, "CONNECT 无响应"
        status_line = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = status_line.split()
        code = parts[1] if len(parts) > 1 else "?"
        if code != "200":
            sock.close()
            return None, f"CONNECT 被拒（{status_line.strip()}）"
        return sock, status_line.strip()
    except Exception as exc:  # noqa: BLE001
        try:
            sock.close()
        except OSError:
            pass
        return None, _short_err(exc)


def _open_socks5_tunnel(
    proxy: tuple[str, int],
    host: str,
    port: int,
    timeout: float,
    user: str | None = None,
    pwd: str | None = None,
) -> tuple[socket.socket | None, str]:
    try:
        sock = socket.create_connection(proxy, timeout=timeout)
    except OSError as exc:
        return None, _short_err(exc)
    try:
        sock.settimeout(timeout)
        if user is not None:
            sock.sendall(b"\x05\x02\x00\x02")
        else:
            sock.sendall(b"\x05\x01\x00")
        greet = _recv_exact(sock, 2)
        if len(greet) < 2 or greet[0] != 0x05:
            sock.close()
            return None, "非 SOCKS5 响应"
        method = greet[1]
        if method == 0x02:
            if user is None:
                sock.close()
                return None, "SOCKS5 要求认证（未提供用户名/密码）"
            u_bytes = user.encode("utf-8")
            p_bytes = (pwd or "").encode("utf-8")
            if len(u_bytes) > 255 or len(p_bytes) > 255:
                sock.close()
                return None, "SOCKS5 用户名或密码过长"
            sock.sendall(
                b"\x01" + bytes([len(u_bytes)]) + u_bytes + bytes([len(p_bytes)]) + p_bytes
            )
            auth_resp = _recv_exact(sock, 2)
            if len(auth_resp) < 2 or auth_resp[1] != 0x00:
                sock.close()
                return None, "SOCKS5 认证被拒绝"
        elif method != 0x00:
            sock.close()
            return None, f"SOCKS5 不支持的认证方式（method={method}）"

        name = host.encode("idna")
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port))
        head = _recv_exact(sock, 4)
        if len(head) < 4:
            sock.close()
            return None, "SOCKS5 应答不完整"
        if head[1] != 0x00:
            sock.close()
            reasons = {
                1: "代理内部故障", 2: "规则不允许", 3: "网络不可达",
                4: "主机不可达", 5: "连接被拒", 6: "TTL 超时",
                7: "不支持该命令", 8: "不支持该地址类型",
            }
            return None, f"SOCKS5 拒绝：{reasons.get(head[1], f'code={head[1]}')}"
        atyp = head[3]
        if atyp == 0x01:
            _recv_exact(sock, 4)
        elif atyp == 0x04:
            _recv_exact(sock, 16)
        elif atyp == 0x03:
            n = _recv_exact(sock, 1)
            if n:
                _recv_exact(sock, n[0])
        _recv_exact(sock, 2)
        return sock, "SOCKS5 CONNECT 00"
    except Exception as exc:  # noqa: BLE001
        try:
            sock.close()
        except OSError:
            pass
        return None, _short_err(exc)


def probe_http_connect(
    proxy: tuple[str, int],
    host: str,
    port: int,
    timeout: float,
    user: str | None = None,
    pwd: str | None = None,
) -> tuple[bool, str]:
    sock, detail = _open_http_tunnel(proxy, host, port, timeout, user=user, pwd=pwd)
    if sock is None:
        return False, detail
    return _tls_through(sock, host, timeout)


def probe_socks5(
    proxy: tuple[str, int],
    host: str,
    port: int,
    timeout: float,
    user: str | None = None,
    pwd: str | None = None,
) -> tuple[bool, str]:
    sock, detail = _open_socks5_tunnel(proxy, host, port, timeout, user=user, pwd=pwd)
    if sock is None:
        return False, detail
    return _tls_through(sock, host, timeout)


_PROBES = {"http": probe_http_connect, "socks5": probe_socks5}


def probe_endpoint(
    host: str,
    port: int,
    attempts: int = 2,
    timeout: float = 6.0,
    on_log: Callable[[str], None] | None = None,
    user: str | None = None,
    pwd: str | None = None,
) -> EndpointReport:
    """对一个候选端点做完整校验：2 种协议 × 2 个目标 × N 次重试。"""
    log = on_log or (lambda _m: None)
    report = EndpointReport(host=host, port=port)
    report.login = Channel("login", "登录端点", LOGIN_HOST)
    report.model = Channel("model", "模型端点", MODEL_HOST)

    attempts = max(1, int(attempts))
    started = time.monotonic()
    jobs = [(proto, key, thost) for proto in ("http", "socks5") for key, _lbl, thost in TARGETS]

    def run(job: tuple[str, str, str]) -> list[Attempt]:
        proto, key, thost = job
        out: list[Attempt] = []
        for _idx in range(attempts):
            t0 = time.monotonic()
            ok, detail = _PROBES[proto]((host, port), thost, PORT, timeout, user=user, pwd=pwd)
            ms = int((time.monotonic() - t0) * 1000)
            out.append(Attempt(proto=proto, key=key, ok=ok, detail=detail, ms=ms))
            if ok:
                break
        return out

    with ThreadPoolExecutor(max_workers=MAX_PROBE_WORKERS) as pool:
        for batch in pool.map(run, jobs):
            report.attempts.extend(batch)

    report.elapsed = time.monotonic() - started
    report.proto_http = any(a.ok for a in report.attempts if a.proto == "http")
    report.proto_socks = any(a.ok for a in report.attempts if a.proto == "socks5")

    for ch in (report.login, report.model):
        wins = [a for a in report.attempts if a.key == ch.key and a.ok]
        if wins:
            best = min(wins, key=lambda a: a.ms)
            ch.ok = True
            ch.proto = best.proto
            ch.ms = best.ms
            ch.detail = (
                f"{'CONNECT' if best.proto == 'http' else 'SOCKS5'} · "
                f"{best.detail} · {best.ms / 1000:.1f}s"
            )
        else:
            fails = [a for a in report.attempts if a.key == ch.key]
            if fails:
                last = fails[-1]
                ch.ok = False
                ch.detail = f"HTTP 与 SOCKS5 均未通过 · 末次：{last.detail}"
            else:
                ch.detail = "未检测"

    log(f"校验 {host}:{port} → {report.scheme.upper()}，耗时 {report.elapsed:.1f}s")
    return report


# --------------------------------------------------------------------------- #
# 候选发现 + 手动指定
# --------------------------------------------------------------------------- #

def build_candidate_list(include_scan: bool = True) -> tuple[list[Candidate], list[str]]:
    notes: list[str] = []
    ordered: list[Candidate] = []
    seen: set[tuple[str, int]] = set()

    def push(items: Iterable[Candidate]) -> None:
        for c in items:
            key = (c.host, c.port)
            if key in seen:
                continue
            if not tcp_open(c.host, c.port, 0.4):
                continue
            seen.add(key)
            ordered.append(c)

    sys_cands, sys_notes = system_proxy_candidates()
    notes.extend(sys_notes)
    push(sys_cands)
    push(env_proxy_candidates())

    if include_scan:
        for port in scan_ports():
            push([Candidate("127.0.0.1", port, "端口扫描")])

    if not ordered:
        notes.append("没有发现任何在监听的本地代理端口")
    return ordered, notes


def parse_proxy_url(raw: str) -> tuple[str, int, str | None, str | None, str]:
    """解析手动输入：`127.0.0.1:7897` / `http://127.0.0.1:7897` / `socks5://u:p@h:p`。"""
    from urllib.parse import unquote

    raw = (raw or "").strip()
    if not raw:
        raise ValueError("代理地址为空")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if not parts.hostname:
        raise ValueError(f"无法解析代理地址：{raw}")
    scheme = (parts.scheme or "http").lower()
    if scheme not in ("http", "https", "socks5", "socks5h", "socks"):
        raise ValueError(f"不支持的代理协议：{scheme}")
    if scheme == "https":
        scheme = "http"
    if scheme in ("socks", "socks5h"):
        scheme = "socks5"
    if not parts.port:
        raise ValueError("代理地址缺少端口")
    user = unquote(parts.username) if parts.username is not None else None
    pwd = unquote(parts.password) if parts.password is not None else None
    return parts.hostname, int(parts.port), user, pwd, scheme


def autodetect(
    attempts: int = 2,
    timeout: float = 6.0,
    on_log: Callable[[str], None] | None = None,
    include_scan: bool = True,
) -> tuple[EndpointReport | None, list[str], list[Candidate]]:
    """按可信度顺序逐个校验，第一个确认可用的即采用。

    只接受"至少有一种协议扛住了真实握手"的端点；只探到端口开着不算。
    """
    log = on_log or (lambda _m: None)
    cands, notes = build_candidate_list(include_scan=include_scan)
    if not cands:
        return None, notes, cands

    fallback: EndpointReport | None = None
    for c in cands:
        log(f"尝试 {c.host}:{c.port}（来源：{c.origin}）")
        report = probe_endpoint(c.host, c.port, attempts=attempts, timeout=timeout, on_log=log)
        report.origin = c.origin
        if report.all_green:
            return report, notes, cands
        if report.usable and fallback is None:
            fallback = report
            log(f"{c.host}:{c.port} 协议可用但通道不全绿，继续找更优端点")

    # 没有全绿端点时退回第一个"协议可用"的端点，把真实状态如实摆到界面上
    return fallback, notes, cands


# --------------------------------------------------------------------------- #
# 前置真实可达性校验 & 网络诊断
# --------------------------------------------------------------------------- #

@dataclass
class PreflightResult:
    ok: bool
    status: int | None = None
    ms: int = 0
    detail: str = ""
    attempts: int = 0


def _open_tunnel_for_endpoint(
    endpoint: EndpointReport,
    host: str,
    port: int,
    timeout: float,
    user: str | None = None,
    pwd: str | None = None,
) -> tuple[socket.socket | None, str]:
    proxy = (endpoint.host, endpoint.port)
    if endpoint.proto_http:
        return _open_http_tunnel(proxy, host, port, timeout, user=user, pwd=pwd)
    return _open_socks5_tunnel(proxy, host, port, timeout, user=user, pwd=pwd)


def _https_get_over_proxy(
    endpoint: EndpointReport,
    host: str,
    path: str,
    timeout: float,
    user: str | None = None,
    pwd: str | None = None,
) -> tuple[bool, int | None, int, str]:
    """穿过同一条代理隧道发起真实 HTTPS 请求，保留完整证书校验以防劫持或死节点。"""
    t0 = time.monotonic()
    sock, err = _open_tunnel_for_endpoint(endpoint, host, PORT, timeout, user=user, pwd=pwd)
    if sock is None:
        return False, None, int((time.monotonic() - t0) * 1000), err

    left = max(timeout - (time.monotonic() - t0), 0.8)
    ctx = ssl.create_default_context()
    tls_sock: ssl.SSLSocket | None = None
    try:
        sock.settimeout(left)
        tls_sock = ctx.wrap_socket(sock, server_hostname=host)
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: AntigravityLauncher\r\n"
            f"Connection: close\r\n\r\n"
        )
        tls_sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n" not in buf:
            chunk = tls_sock.recv(2048)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 8192:
                break
        ms = int((time.monotonic() - t0) * 1000)
        if not buf:
            return False, None, ms, "对端未返回 HTTP 响应"
        status_line = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = status_line.split()
        if len(parts) < 2 or not parts[1].isdigit():
            return False, None, ms, f"异常 HTTP 响应：{status_line.strip()}"
        code = int(parts[1])
        if code >= 500:
            return False, code, ms, f"上游返回 HTTP {code}"
        return True, code, ms, f"HTTP {code}"
    except Exception as exc:  # noqa: BLE001
        return False, None, int((time.monotonic() - t0) * 1000), _short_err(exc)
    finally:
        try:
            if tls_sock is not None:
                tls_sock.close()
            else:
                sock.close()
        except OSError:
            pass


def check_google_reachability(
    endpoint: EndpointReport,
    user: str | None = None,
    pwd: str | None = None,
    attempts: int = 2,
    timeout: float = 6.0,
    on_attempt: Callable[[int, bool, str], None] | None = None,
) -> PreflightResult:
    """启动前验证节点是否真正能访问 Google（www.google.com/generate_204）。"""
    attempts = max(1, int(attempts))
    last_detail = "未检测"
    last_status: int | None = None
    last_ms = 0
    for idx in range(1, attempts + 1):
        ok, status, ms, detail = _https_get_over_proxy(
            endpoint, PREFLIGHT_HOST, PREFLIGHT_PATH, timeout=timeout, user=user, pwd=pwd
        )
        last_detail, last_status, last_ms = detail, status, ms
        if on_attempt:
            on_attempt(idx, ok, detail)
        if ok:
            return PreflightResult(ok=True, status=status, ms=ms, detail=detail, attempts=idx)
    return PreflightResult(ok=False, status=last_status, ms=last_ms, detail=last_detail, attempts=attempts)


@dataclass
class PingItem:
    key: str
    label: str
    host: str
    ok: bool
    ms: int
    detail: str


@dataclass
class IpEndpointInfo:
    ip: str
    country: str = ""
    city: str = ""
    country_code: str = ""


@dataclass
class NetDiagResult:
    direct_ip: IpEndpointInfo | None = None
    proxy_ip: IpEndpointInfo | None = None
    pings: list[PingItem] = field(default_factory=list)
    direct_error: str = ""
    proxy_error: str = ""


def _parse_ip_json(raw: bytes) -> IpEndpointInfo:
    data = json.loads(raw.decode("utf-8", "replace"))
    if data.get("status") != "success":
        raise ValueError(data.get("message") or "ip-api 查询失败")
    return IpEndpointInfo(
        ip=str(data.get("query") or ""),
        country=str(data.get("country") or ""),
        city=str(data.get("city") or ""),
        country_code=str(data.get("countryCode") or ""),
    )


def _read_http_body(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if sum(len(c) for c in chunks) > 65536:
            break
    raw = b"".join(chunks)
    sep = raw.find(b"\r\n\r\n")
    if sep == -1:
        raise ValueError("HTTP 响应不完整")
    return raw[sep + 4:]


def _fetch_direct_ip(timeout: float = 4.0) -> tuple[IpEndpointInfo | None, str]:
    # 明确禁用环境变量代理，确保测到的是直连出口
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        "http://ip-api.com/json?fields=status,message,country,countryCode,city,query",
        headers={"User-Agent": "AntigravityLauncher"},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            return _parse_ip_json(resp.read(65536)), ""
    except Exception as exc:  # noqa: BLE001
        return None, _short_err(exc)


def _fetch_proxy_ip(
    endpoint: EndpointReport | None,
    user: str | None = None,
    pwd: str | None = None,
    timeout: float = 5.0,
) -> tuple[IpEndpointInfo | None, str]:
    if endpoint is None or not endpoint.usable:
        return None, "无可用代理"
    try:
        if endpoint.proto_http:
            sock = socket.create_connection((endpoint.host, endpoint.port), timeout=timeout)
            try:
                sock.settimeout(timeout)
                auth_hdr = _basic_auth_header(user, pwd)
                req = (
                    "GET http://ip-api.com/json?fields=status,message,country,countryCode,city,query HTTP/1.1\r\n"
                    "Host: ip-api.com\r\n"
                    f"{auth_hdr}"
                    "Connection: close\r\n\r\n"
                )
                sock.sendall(req.encode("ascii"))
                return _parse_ip_json(_read_http_body(sock)), ""
            finally:
                sock.close()
        sock, err = _open_socks5_tunnel((endpoint.host, endpoint.port), "ip-api.com", 80, timeout, user=user, pwd=pwd)
        if sock is None:
            return None, err
        try:
            sock.settimeout(timeout)
            req = (
                "GET /json?fields=status,message,country,countryCode,city,query HTTP/1.1\r\n"
                "Host: ip-api.com\r\n"
                "Connection: close\r\n\r\n"
            )
            sock.sendall(req.encode("ascii"))
            return _parse_ip_json(_read_http_body(sock)), ""
        finally:
            sock.close()
    except Exception as exc:  # noqa: BLE001
        return None, _short_err(exc)


def diagnose_network(
    endpoint: EndpointReport | None,
    user: str | None = None,
    pwd: str | None = None,
    timeout: float = 5.0,
) -> NetDiagResult:
    """并发探测直连 IP、代理出口 IP 以及核心域名 RTT 延迟。"""
    def ping_one(item: tuple[str, str, str]) -> PingItem:
        key, label, host = item
        if endpoint is None or not endpoint.usable:
            return PingItem(key=key, label=label, host=host, ok=False, ms=0, detail="无可用代理")
        t0 = time.monotonic()
        if endpoint.proto_http:
            ok, detail = probe_http_connect((endpoint.host, endpoint.port), host, PORT, timeout, user=user, pwd=pwd)
        else:
            ok, detail = probe_socks5((endpoint.host, endpoint.port), host, PORT, timeout, user=user, pwd=pwd)
        ms = int((time.monotonic() - t0) * 1000)
        return PingItem(key=key, label=label, host=host, ok=ok, ms=ms, detail=detail)

    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_direct = pool.submit(_fetch_direct_ip, timeout)
        fut_proxy = pool.submit(_fetch_proxy_ip, endpoint, user, pwd, timeout)
        fut_pings = [pool.submit(ping_one, t) for t in DIAG_TARGETS]

        direct_ip, direct_err = fut_direct.result()
        proxy_ip, proxy_err = fut_proxy.result()
        pings = [f.result() for f in fut_pings]

    return NetDiagResult(
        direct_ip=direct_ip,
        proxy_ip=proxy_ip,
        pings=pings,
        direct_error=direct_err,
        proxy_error=proxy_err,
    )
