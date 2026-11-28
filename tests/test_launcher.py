from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import socket
import struct
import threading
import unittest
from pathlib import Path
from unittest import mock

from aglauncher import cli, client, core, launcher, procs, proxy, store

# 进程名在 Windows 带 .exe 后缀，macOS / Linux 不带，用例需与平台一致
CLIENT_PROC, LS_PROC = (
    ("Antigravity.exe", "language_server.exe") if os.name == "nt" else ("Antigravity", "language_server")
)


def make_client() -> client.Client:
    exe = Path("/opt/Antigravity/Antigravity")
    return client.Client(
        exe=exe,
        language_server=Path("/opt/Antigravity/resources/bin/language_server"),
        root=exe.parent,
        source="缓存",
    )


def make_endpoint(green: bool = True) -> proxy.EndpointReport:
    return proxy.EndpointReport(
        host="127.0.0.1",
        port=7897,
        proto_http=True,
        proto_socks=True,
        login=proxy.Channel(
            "login", "登录端点", proxy.LOGIN_HOST, ok=green, proto="http", detail="CONNECT · TLSv1.3", ms=120
        ),
        model=proxy.Channel(
            "model", "模型端点", proxy.MODEL_HOST, ok=green, proto="socks5", detail="SOCKS5 · TLSv1.3", ms=130
        ),
        elapsed=2.5,
        origin="系统代理",
    )


def make_report(green: bool = True, residuals: list[procs.ProcInfo] | None = None) -> core.Report:
    ep = make_endpoint(green)
    return core.Report(
        client=make_client(),
        endpoint=ep,
        residuals=residuals or [],
        plan=launcher.build_plan(ep),
    )


def residual(pid: int = 500) -> procs.ProcInfo:
    return procs.ProcInfo(
        pid=pid, ppid=1, name="Antigravity.exe", exe="C:/AG/Antigravity.exe", created=1.0
    )


class TestProxyParsingAndAuth(unittest.TestCase):
    def test_parse_proxy_url_with_encoded_auth(self) -> None:
        host, port, user, pwd, scheme = proxy.parse_proxy_url("socks5h://alice%40mail:p%3Ass@127.0.0.1:1080")
        self.assertEqual((host, port, user, pwd, scheme), ("127.0.0.1", 1080, "alice@mail", "p:ss", "socks5"))

    def test_http_tunnel_sends_basic_auth(self) -> None:
        captured: list[bytes] = []

        def serve(srv: socket.socket) -> None:
            for _ in range(2):
                conn, _ = srv.accept()
                with conn:
                    data = conn.recv(4096)
                    captured.append(data)
                    expected_auth = base64.b64encode(b"u1:s3cr3t").decode("ascii")
                    if f"Proxy-Authorization: Basic {expected_auth}".encode("ascii") in data:
                        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    else:
                        conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")

        srv = socket.create_server(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        t = threading.Thread(target=serve, args=(srv,), daemon=True)
        t.start()
        try:
            sock, detail = proxy._open_http_tunnel(("127.0.0.1", port), "accounts.google.com", 443, 2.0, "u1", "s3cr3t")
            self.assertIsNotNone(sock)
            assert sock is not None
            sock.close()
            self.assertIn("200", detail)

            sock_no_auth, detail_fail = proxy._open_http_tunnel(("127.0.0.1", port), "accounts.google.com", 443, 2.0)
            self.assertIsNone(sock_no_auth)
            self.assertIn("407", detail_fail)
        finally:
            srv.close()

    def test_socks5_tunnel_rfc1929_auth(self) -> None:
        def serve(srv: socket.socket) -> None:
            conn, _ = srv.accept()
            with conn:
                greet = conn.recv(16)
                # Client offers 0x00 and 0x02; server picks 0x02
                if greet == b"\x05\x02\x00\x02":
                    conn.sendall(b"\x05\x02")
                else:
                    conn.sendall(b"\x05\xff")
                    return
                auth_pkt = conn.recv(256)
                # ver=1, ulen=2, 'u1', plen=3, 'pw2'
                if auth_pkt == b"\x01\x02u1\x03pw2":
                    conn.sendall(b"\x01\x00")
                else:
                    conn.sendall(b"\x01\x01")
                    return
                req = conn.recv(256)
                if req.startswith(b"\x05\x01\x00\x03"):
                    conn.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01" + struct.pack(">H", 443))

        srv = socket.create_server(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        t = threading.Thread(target=serve, args=(srv,), daemon=True)
        t.start()
        try:
            sock, detail = proxy._open_socks5_tunnel(("127.0.0.1", port), "accounts.google.com", 443, 2.0, "u1", "pw2")
            self.assertIsNotNone(sock)
            assert sock is not None
            sock.close()
            self.assertEqual(detail, "SOCKS5 CONNECT 00")
        finally:
            srv.close()

    def test_parse_scutil_proxy_preserves_remote_host_and_pac(self) -> None:
        sample = """
        <dictionary> {
          HTTPEnable : 1
          HTTPPort : 7897
          HTTPProxy : 192.168.1.50
          HTTPSEnable : 0
          HTTPSPort : 7897
          HTTPSProxy : 192.168.1.50
          SOCKSEnable : 1
          SOCKSPort : 1080
          SOCKSProxy : [::1]
          ProxyAutoConfigEnable : 1
          ProxyAutoConfigURLString : http://192.168.1.50/proxy.pac
        }
        """
        cands, notes = proxy._parse_scutil_proxy(sample)
        self.assertEqual(
            [(c.host, c.port, c.origin) for c in cands],
            [
                ("192.168.1.50", 7897, "系统代理(HTTP)"),
                ("::1", 1080, "系统代理(SOCKS)"),
            ],
        )
        self.assertTrue(any("PAC" in n and "proxy.pac" in n for n in notes))

    def test_linux_gsettings_proxy_reads_host_and_mode(self) -> None:
        manual_cfg = {
            ("org.gnome.system.proxy", "mode"): "'manual'",
            ("org.gnome.system.proxy.http", "host"): "'10.0.0.8'",
            ("org.gnome.system.proxy.http", "port"): "8080",
            ("org.gnome.system.proxy.https", "host"): "''",
            ("org.gnome.system.proxy.https", "port"): "7897",
            ("org.gnome.system.proxy.socks", "host"): "'192.168.2.2'",
            ("org.gnome.system.proxy.socks", "port"): "0",
        }
        cands, notes = proxy._linux_gsettings_proxy(lambda s, k: manual_cfg.get((s, k), ""))
        self.assertEqual(
            [(c.host, c.port, c.origin) for c in cands],
            [
                ("10.0.0.8", 8080, "系统代理(HTTP)"),
                ("127.0.0.1", 7897, "系统代理(HTTPS)"),
            ],
        )
        self.assertEqual(notes, [])

        auto_cfg = {
            ("org.gnome.system.proxy", "mode"): "'auto'",
            ("org.gnome.system.proxy", "autoconfig-url"): "'http://10.0.0.8/wpad.dat'",
        }
        cands2, notes2 = proxy._linux_gsettings_proxy(lambda s, k: auto_cfg.get((s, k), ""))
        self.assertEqual(cands2, [])
        self.assertTrue(any("wpad.dat" in n for n in notes2))

        disabled_cfg = {
            ("org.gnome.system.proxy", "mode"): "'none'",
            ("org.gnome.system.proxy.http", "host"): "'10.0.0.9'",
            ("org.gnome.system.proxy.http", "port"): "7890",
        }
        cands3, notes3 = proxy._linux_gsettings_proxy(lambda s, k: disabled_cfg.get((s, k), ""))
        self.assertEqual([(c.host, c.port, c.origin) for c in cands3], [("10.0.0.9", 7890, "系统代理(HTTP)(未启用)")])
        self.assertTrue(any("mode=none" in n for n in notes3))


class TestProcsAndTree(unittest.TestCase):
    def test_split_running_recognizes_own_and_descendant_ls(self) -> None:
        table = [
            procs.ProcInfo(pid=100, ppid=1, name=CLIENT_PROC, exe="C:/AG/Antigravity.exe", created=1000.0),
            procs.ProcInfo(pid=101, ppid=100, name=LS_PROC, exe="C:/AG/ls.exe", created=1001.0),
            procs.ProcInfo(pid=102, ppid=100, name=CLIENT_PROC, exe="C:/AG/Antigravity.exe", created=1001.2),
            procs.ProcInfo(pid=103, ppid=101, name=CLIENT_PROC, exe="C:/AG/Antigravity.exe", created=1002.0),
            procs.ProcInfo(pid=200, ppid=1, name=CLIENT_PROC, exe="C:/AG/Antigravity.exe", created=2000.0),
        ]
        own, residuals = procs.split_running(table, {"pid": 100, "created": 1000.5, "exe": "C:/AG/Antigravity.exe"})
        self.assertIsNotNone(own)
        assert own is not None
        self.assertEqual(own.pid, 100)
        self.assertEqual([r.pid for r in residuals], [200])

    def test_pid_reuse_rejected_by_creation_time(self) -> None:
        table = [
            procs.ProcInfo(pid=100, ppid=1, name=CLIENT_PROC, exe="C:/AG/Antigravity.exe", created=5000.0),
        ]
        own, residuals = procs.split_running(table, {"pid": 100, "created": 1000.0, "exe": "C:/AG/Antigravity.exe"})
        self.assertIsNone(own)
        self.assertEqual([r.pid for r in residuals], [100])

    def test_wait_for_tree_early_exit(self) -> None:
        fake_popen = mock.Mock()
        fake_popen.poll.return_value = 0
        with mock.patch("aglauncher.procs.snapshot", return_value=[]):
            res = procs.wait_for_tree(9999, timeout=10.0, poll=0.05, popen=fake_popen)
        self.assertIsNone(res.server)
        self.assertEqual(res.early_exit_code, 0)

    def test_find_residuals_matches_suffixed_language_server_and_ignores_launcher(self) -> None:
        ls_suffixed = "language_server_windows_x64.exe" if os.name == "nt" else "language_server_macos_arm"
        launcher_name = "AntigravityLauncher.exe" if os.name == "nt" else "AntigravityLauncher"
        table = [
            procs.ProcInfo(pid=300, ppid=1, name=ls_suffixed, exe=f"/AG/{ls_suffixed}", created=10.0),
            procs.ProcInfo(pid=301, ppid=1, name=launcher_name, exe=f"/AG/{launcher_name}", created=10.0),
            procs.ProcInfo(pid=302, ppid=1, name="language_serverx.exe", exe="/AG/x.exe", created=10.0),
        ]
        found = procs.find_residuals(table)
        self.assertEqual([p.pid for p in found], [300])
        self.assertTrue(found[0].is_language_server)
        self.assertFalse(table[1].is_client)
        self.assertFalse(table[2].is_language_server)


class TestLauncherAndClient(unittest.TestCase):
    def test_build_plan_and_commands(self) -> None:
        ep = proxy.EndpointReport(
            host="127.0.0.1",
            port=7897,
            proto_http=True,
            proto_socks=True,
            login=proxy.Channel("login", "登录端点", proxy.LOGIN_HOST, ok=True, proto="http"),
            model=proxy.Channel("model", "模型端点", proxy.MODEL_HOST, ok=True, proto="http"),
        )
        plan = launcher.build_plan(ep, user="u", pwd="p")
        self.assertEqual(plan.env["HTTP_PROXY"], "http://u:p@127.0.0.1:7897")
        self.assertEqual(plan.env["ALL_PROXY"], "socks5://u:p@127.0.0.1:7897")
        self.assertEqual(plan.env["NO_PROXY"], "localhost,127.0.0.1,::1")
        self.assertEqual(plan.argv, [])  # --proxy-server omitted when auth is present

        cmds = launcher.build_proxy_commands(ep, user="u", pwd="p", mask_secret=True)
        self.assertIn("***:***@127.0.0.1:7897", cmds["powershell"])
        self.assertIn("export HTTP_PROXY=", cmds["bash"])

    def test_cli_parser_new_flags(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["--doctor", "--diag", "--no-preflight", "--json"])
        self.assertTrue(args.doctor)
        self.assertTrue(args.diag)
        self.assertFalse(args.preflight)
        self.assertTrue(args.json)

    def test_store_defaults_include_new_keys(self) -> None:
        for key in ("cli_path", "preflight_check", "launched_desktop", "work_dir"):
            self.assertIn(key, store.DEFAULTS)

    def test_locate_cli_from_which(self) -> None:
        with mock.patch("aglauncher.client._cli_candidates", return_value=[]), \
             mock.patch("shutil.which", return_value=__file__), \
             mock.patch.object(Path, "is_file", return_value=True):
            found, _ = client.locate_cli()
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.source, "PATH")

    def test_language_server_for_accepts_suffixed_binary_and_ignores_logs(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / ("Antigravity.exe" if os.name == "nt" else "Antigravity")
            exe.write_bytes(b"")
            bin_dir = root / "resources" / "bin"
            bin_dir.mkdir(parents=True)
            # 非二进制日志/符号文件必须忽略
            (bin_dir / "language_server.log").write_text("log", encoding="utf-8")
            (bin_dir / "language_server.pdb").write_bytes(b"")
            self.assertIsNone(client.language_server_for(exe))

            # 带架构后缀的二进制应被识别
            suffixed = bin_dir / ("language_server_windows_x64.exe" if os.name == "nt" else "language_server_macos_arm")
            suffixed.write_bytes(b"bin")
            self.assertEqual(client.language_server_for(exe), suffixed)

            # 若同时存在标准文件名，优先命中标准文件名
            exact = bin_dir / ("language_server.exe" if os.name == "nt" else "language_server")
            exact.write_bytes(b"bin")
            self.assertEqual(client.language_server_for(exe), exact)


class TestCoreInspect(unittest.TestCase):
    def test_ready_report_builds_launch_plan(self) -> None:
        ep = make_endpoint()
        with mock.patch("aglauncher.core.client_mod.locate", return_value=(make_client(), ["定位自缓存"])), \
             mock.patch("aglauncher.core.client_mod.locate_cli", return_value=(None, [])), \
             mock.patch("aglauncher.core.procs.snapshot", return_value=[]), \
             mock.patch("aglauncher.core.procs.split_running", return_value=(None, [])), \
             mock.patch("aglauncher.core.procs.find_cli_sessions", return_value=[]), \
             mock.patch("aglauncher.core.proxy.autodetect", return_value=(ep, ["自动探测说明"], [])):
            report = core.inspect(attempts=1, timeout=1.0)

        self.assertTrue(report.ready)
        self.assertFalse(report.degraded)
        self.assertIsNotNone(report.plan)
        assert report.plan is not None
        self.assertEqual(report.endpoint, ep)
        self.assertIn("自动探测说明", report.notes)
        self.assertIn("定位自缓存", report.notes)
        self.assertIn("--proxy-server=http://127.0.0.1:7897", report.plan.argv)

    def test_residual_instance_blocks_launch(self) -> None:
        ep = make_endpoint()
        with mock.patch("aglauncher.core.client_mod.locate", return_value=(make_client(), [])), \
             mock.patch("aglauncher.core.client_mod.locate_cli", return_value=(None, [])), \
             mock.patch("aglauncher.core.procs.snapshot", return_value=[]), \
             mock.patch("aglauncher.core.procs.split_running", return_value=(None, [residual()])), \
             mock.patch("aglauncher.core.procs.find_cli_sessions", return_value=[]), \
             mock.patch("aglauncher.core.proxy.autodetect", return_value=(ep, [], [])):
            report = core.inspect(attempts=1, timeout=1.0)

        self.assertTrue(report.blocked)
        self.assertIn("1 个", report.block_reason or "")
        self.assertEqual(len(report.residuals), 1)

    def test_invalid_manual_proxy_is_reported_not_raised(self) -> None:
        with mock.patch("aglauncher.core.client_mod.locate", return_value=(make_client(), [])), \
             mock.patch("aglauncher.core.client_mod.locate_cli", return_value=(None, [])), \
             mock.patch("aglauncher.core.procs.snapshot", return_value=[]), \
             mock.patch("aglauncher.core.procs.split_running", return_value=(None, [])), \
             mock.patch("aglauncher.core.procs.find_cli_sessions", return_value=[]), \
             mock.patch("aglauncher.core.proxy.parse_proxy_url", side_effect=ValueError("缺少端口")):
            report = core.inspect(proxy_url="http://127.0.0.1")

        self.assertIsNone(report.endpoint)
        self.assertIsNone(report.plan)
        self.assertTrue(report.blocked)
        self.assertIn("缺少端口", report.notes)


class TestCliDoctor(unittest.TestCase):
    def _run_doctor(self, report: core.Report, argv: tuple[str, ...] = ("--doctor", "--json")) -> tuple[int, str]:
        args = cli.build_parser().parse_args(list(argv))
        buf = io.StringIO()
        with mock.patch("aglauncher.cli.store.load", return_value={}), \
             mock.patch("aglauncher.cli.core.inspect", return_value=report), \
             contextlib.redirect_stdout(buf):
            code = cli.run_doctor(args)
        return code, buf.getvalue()

    def test_doctor_json_ready(self) -> None:
        code, out = self._run_doctor(make_report())
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        self.assertTrue(payload["proxy"]["login_ok"])
        self.assertTrue(payload["proxy"]["model_ok"])
        self.assertIsNone(payload["blocked"])
        self.assertIn("HTTP_PROXY", payload["env"])

    def test_doctor_exit_codes(self) -> None:
        code, _ = self._run_doctor(core.Report())
        self.assertEqual(code, cli.EXIT_CLIENT_MISSING)

        code, _ = self._run_doctor(make_report(residuals=[residual()]))
        self.assertEqual(code, cli.EXIT_RESIDUAL)

        code, _ = self._run_doctor(core.Report(client=make_client()))
        self.assertEqual(code, cli.EXIT_PROXY_DEAD)


class TestCliLaunch(unittest.TestCase):
    def _args(self, *extra: str):
        return cli.build_parser().parse_args(["--launch", *extra])

    def test_launch_missing_client_short_circuits(self) -> None:
        buf = io.StringIO()
        with mock.patch("aglauncher.cli.store.load", return_value={}), \
             mock.patch("aglauncher.cli.client_mod.locate", return_value=(None, [])), \
             contextlib.redirect_stdout(buf):
            code = cli.run_launch(self._args())
        self.assertEqual(code, cli.EXIT_CLIENT_MISSING)
        self.assertIn("未找到 Antigravity 客户端", buf.getvalue())

    def test_launch_own_instance_is_noop(self) -> None:
        own = procs.ProcInfo(pid=100, ppid=1, name="Antigravity.exe", exe="C:/AG/Antigravity.exe", created=1000.0)
        buf = io.StringIO()
        with mock.patch("aglauncher.cli.store.load", return_value={}), \
             mock.patch("aglauncher.cli.client_mod.locate", return_value=(make_client(), [])), \
             mock.patch("aglauncher.cli.procs.split_running", return_value=(own, [])), \
             contextlib.redirect_stdout(buf):
            code = cli.run_launch(self._args())
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("无需重复启动", buf.getvalue())

    def test_launch_residual_blocks_without_kill_flag(self) -> None:
        buf = io.StringIO()
        with mock.patch("aglauncher.cli.store.load", return_value={}), \
             mock.patch("aglauncher.cli.client_mod.locate", return_value=(make_client(), [])), \
             mock.patch("aglauncher.cli.procs.split_running", return_value=(None, [residual()])), \
             contextlib.redirect_stdout(buf):
            code = cli.run_launch(self._args())
        self.assertEqual(code, cli.EXIT_RESIDUAL)
        self.assertIn("--kill-residuals", buf.getvalue())

    def test_launch_success_persists_record(self) -> None:
        report = make_report()
        fake_popen = mock.Mock(pid=4242)
        saved: dict = {}
        proc_rec = procs.ProcInfo(
            pid=4242, ppid=1, name="Antigravity.exe", exe="C:/AG/Antigravity.exe", created=111.0
        )
        buf = io.StringIO()
        with mock.patch("aglauncher.cli.store.load", return_value={"launched_desktop": None}), \
             mock.patch("aglauncher.cli.store.save", side_effect=lambda cfg: saved.update(cfg)), \
             mock.patch("aglauncher.cli.client_mod.locate", return_value=(make_client(), [])), \
             mock.patch("aglauncher.cli.procs.split_running", return_value=(None, [])), \
             mock.patch("aglauncher.cli.core.inspect", return_value=report), \
             mock.patch("aglauncher.cli.procs.snapshot", return_value=[proc_rec]), \
             mock.patch("aglauncher.cli.spawn", return_value=fake_popen), \
             mock.patch("aglauncher.cli.verify_injection", return_value=(True, "language_server 已启动")), \
             contextlib.redirect_stdout(buf):
            code = cli.run_launch(self._args("--no-preflight"))

        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(saved["launched_desktop"], {"pid": 4242, "created": 111.0, "exe": str(make_client().exe)})
        self.assertIn("已拉起 PID 4242", buf.getvalue())

    def test_launch_preflight_failure_returns_proxy_dead(self) -> None:
        report = make_report()
        buf = io.StringIO()
        with mock.patch("aglauncher.cli.store.load", return_value={}), \
             mock.patch("aglauncher.cli.client_mod.locate", return_value=(make_client(), [])), \
             mock.patch("aglauncher.cli.procs.split_running", return_value=(None, [])), \
             mock.patch("aglauncher.cli.core.inspect", return_value=report), \
             mock.patch("aglauncher.cli._run_preflight_or_fail", return_value=False), \
             mock.patch("aglauncher.cli.spawn") as spawn_mock, \
             contextlib.redirect_stdout(buf):
            code = cli.run_launch(self._args())

        self.assertEqual(code, cli.EXIT_PROXY_DEAD)
        spawn_mock.assert_not_called()


class TestGuiResidualFlow(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("_tkinter") is not None
        and importlib.util.find_spec("tkinter") is not None,
        "tkinter 不可用",
    )
    def test_residual_close_clears_busy_before_detect(self) -> None:
        from aglauncher import gui

        app = gui.LauncherApp.__new__(gui.LauncherApp)
        events: list[tuple] = []
        app._log = lambda message, tag=None: events.append(("log", message))
        app._set_busy = lambda busy, label="": events.append(("busy", busy))
        app._detect = lambda: events.append(("detect", None))

        gui.LauncherApp._on_residuals_closed(app, [1, 2], [])
        self.assertIn("已关闭 2 个实例", events[0][1])
        self.assertIn(("busy", False), events)
        self.assertEqual([kind for kind, _ in events[-2:]], ["busy", "detect"])

        events.clear()
        gui.LauncherApp._on_residuals_closed(app, [], [9], "OSError: boom")
        self.assertIn("关闭实例失败", events[0][1])
        self.assertIn(("busy", False), events)
        self.assertEqual(events[-1][0], "detect")


def load_build_script():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("build_script", root / "scripts" / "build.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestReleaseArtifactsConfig(unittest.TestCase):
    def test_icons_and_build_script_exist(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "aglauncher" / "assets" / "icon.ico").is_file())
        self.assertTrue((root / "aglauncher" / "assets" / "icon.png").is_file())
        self.assertTrue((root / "LICENSE").is_file())
        self.assertTrue((root / "AntigravityLauncher.bat").is_file())

        mod = load_build_script()
        self.assertRegex(mod.read_version(), r"^\d+\.\d+\.\d+")
        self.assertTrue(mod.platform_tag().startswith(("win-", "mac-", "linux-")))

    def test_check_tag_matches_package_version(self) -> None:
        mod = load_build_script()
        version = mod.read_version()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(["--check-tag", f"v{version}"]), 0)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mod.main(["--check-tag", "v9.9.9"]), 1)

    def test_cross_platform_packaging_helpers(self) -> None:
        mod = load_build_script()
        self.assertEqual(mod.arch_name("arm64"), "arm64")
        self.assertEqual(mod.arch_name("aarch64"), "arm64")
        self.assertEqual(mod.arch_name("x86_64"), "x64")
        self.assertTrue(mod.platform_tag().startswith(("win-", "mac-", "linux-")))

    def test_linux_desktop_scan_skips_launcher_wrapper(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            apps_dir = Path(tmp) / ".local" / "share" / "applications"
            apps_dir.mkdir(parents=True)
            (apps_dir / "antigravity-launcher.desktop").write_text(
                "[Desktop Entry]\nExec=/usr/bin/antigravity-launcher\n", encoding="utf-8"
            )
            (apps_dir / "antigravity.desktop").write_text(
                "[Desktop Entry]\nExec=/opt/antigravity/antigravity --flag\n", encoding="utf-8"
            )
            with mock.patch("aglauncher.client.Path.home", return_value=Path(tmp)):
                cands = client._linux_candidates()
            self.assertIn(Path("/opt/antigravity/antigravity"), cands)
            self.assertNotIn(Path("/usr/bin/antigravity-launcher"), cands)


if __name__ == "__main__":
    unittest.main()
