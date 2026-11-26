"""进程枚举、终止与进程树验证。

Windows 上不依赖 psutil，全部走 ctypes 直调内核接口：
- Toolhelp32 快照拿全量进程表（含 PPID）
- GetProcessTimes 拿创建时间，用来区分"这次拉起的"和"之前残留的"
- 关闭优先走 WM_CLOSE 让编辑器自己收尾，超时才 TerminateProcess
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass

CODENAME = "language_server"

TARGET_NAMES = {
    "win": ("antigravity.exe", "language_server.exe"),
    "darwin": ("antigravity", "language_server"),
    "linux": ("antigravity", "language_server"),
}


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    name: str
    exe: str
    created: float  # epoch 秒；拿不到时为 0

    @property
    def is_language_server(self) -> bool:
        return self.name.lower().startswith(CODENAME)

    @property
    def is_client(self) -> bool:
        return self.name.lower().startswith("antigravity")

    @property
    def label(self) -> str:
        return f"{self.name} (PID {self.pid})"


def _names() -> tuple[str, ...]:
    if sys.platform.startswith("win"):
        return TARGET_NAMES["win"]
    if sys.platform == "darwin":
        return TARGET_NAMES["darwin"]
    return TARGET_NAMES["linux"]


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #

if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001
    WM_CLOSE = 0x0010
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    _k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ]
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    SW_RESTORE = 9

    def _filetime_to_epoch(ft: wintypes.FILETIME) -> float:
        ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        if ticks == 0:
            return 0.0
        return ticks / 10_000_000 - 11644473600

    def _exe_path(pid: int) -> str:
        handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            _k32.CloseHandle(handle)

    def _created(pid: int) -> float:
        handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return 0.0
        try:
            c = wintypes.FILETIME()
            e = wintypes.FILETIME()
            k = wintypes.FILETIME()
            u = wintypes.FILETIME()
            if _k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                return _filetime_to_epoch(c)
            return 0.0
        finally:
            _k32.CloseHandle(handle)

    def _enumerate() -> list[ProcInfo]:
        snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            return []
        out: list[ProcInfo] = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                name = entry.szExeFile
                out.append(
                    ProcInfo(
                        pid=int(entry.th32ProcessID),
                        ppid=int(entry.th32ParentProcessID),
                        name=name,
                        exe=_exe_path(int(entry.th32ProcessID)),
                        created=_created(int(entry.th32ProcessID)),
                    )
                )
                ok = _k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            _k32.CloseHandle(snap)
        return out

    def _terminate(pid: int) -> bool:
        handle = _k32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(_k32.TerminateProcess(handle, 1))
        finally:
            _k32.CloseHandle(handle)

    def _post_close(pid: int) -> int:
        sent = 0
        targets = {pid}

        @_WNDENUMPROC
        def _cb(hwnd, _lparam):  # noqa: ANN001
            nonlocal sent
            owner = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in targets:
                _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                sent += 1
            return True

        _user32.EnumWindows(_cb, 0)
        return sent

    def raise_window(pid: int) -> bool:
        """将指定 PID 的可见顶层窗口切到前台，避免客户端窗口被启动器遮挡。"""
        raised = False

        @_WNDENUMPROC
        def _cb(hwnd, _lparam):  # noqa: ANN001
            nonlocal raised
            owner = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and _user32.IsWindowVisible(hwnd):
                if _user32.IsIconic(hwnd):
                    _user32.ShowWindow(hwnd, SW_RESTORE)
                _user32.SetForegroundWindow(hwnd)
                raised = True
                return False
            return True

        try:
            _user32.EnumWindows(_cb, 0)
        except Exception:  # noqa: BLE001
            pass
        return raised

else:  # pragma: no cover - 本机为 Windows，POSIX 走 ps 兜底
    def _enumerate() -> list[ProcInfo]:
        try:
            out = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True, timeout=6
            ).stdout
        except Exception:  # noqa: BLE001
            return []
        procs: list[ProcInfo] = []
        for line in out.splitlines():
            bits = line.split(None, 2)
            if len(bits) < 3:
                continue
            try:
                pid, ppid = int(bits[0]), int(bits[1])
            except ValueError:
                continue
            comm = bits[2].strip()
            procs.append(ProcInfo(pid, ppid, os.path.basename(comm), comm, 0.0))
        return procs

    def _terminate(pid: int) -> bool:
        try:
            os.kill(pid, 9)
            return True
        except OSError:
            return False

    def _post_close(pid: int) -> int:
        try:
            os.kill(pid, 15)
            return 1
        except OSError:
            return 0

    def raise_window(pid: int) -> bool:
        return False


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #

def snapshot() -> list[ProcInfo]:
    return _enumerate()


def find_residuals(table: list[ProcInfo] | None = None) -> list[ProcInfo]:
    procs = table if table is not None else snapshot()
    names = _names()
    self_pid = os.getpid()
    return [p for p in procs if p.pid != self_pid and p.name.lower() in names]


def is_launched_by_us(proc: ProcInfo, record: dict | None) -> bool:
    """结合 PID 与进程创建时间判断某实例是否由本启动器拉起（防止 PID 复用误认）。"""
    if not record or not isinstance(record, dict):
        return False
    if proc.pid != record.get("pid"):
        return False
    rec_created = float(record.get("created") or 0.0)
    if proc.created > 0 and rec_created > 0:
        return abs(proc.created - rec_created) < 15.0
    rec_exe = str(record.get("exe") or "").strip().lower()
    if rec_exe and proc.exe:
        return proc.exe.strip().lower() == rec_exe
    return True


def split_running(
    table: list[ProcInfo] | None = None,
    record: dict | None = None,
) -> tuple[ProcInfo | None, list[ProcInfo]]:
    """将运行中的目标进程拆分为 (己方已注入实例, 外部未受控残留实例)。"""
    snap = table if table is not None else snapshot()
    running = find_residuals(snap)
    if not running:
        return None, []

    own = next((p for p in running if p.is_client and is_launched_by_us(p, record)), None)
    if own is None:
        return None, running

    residuals: list[ProcInfo] = []
    for p in running:
        if p.pid == own.pid:
            continue
        if any(a.pid == own.pid for a in ancestors(p.pid, snap)):
            continue
        residuals.append(p)
    return own, residuals


def find_cli_sessions(table: list[ProcInfo] | None = None) -> list[ProcInfo]:
    """统计正在运行的 agy 终端会话（仅用于状态展示，不阻断启动）。"""
    snap = table if table is not None else snapshot()
    cli_name = "agy.exe" if sys.platform.startswith("win") else "agy"
    return [p for p in snap if p.name.lower() == cli_name]


def ancestors(pid: int, table: list[ProcInfo]) -> list[ProcInfo]:
    by_pid = {p.pid: p for p in table}
    chain: list[ProcInfo] = []
    seen: set[int] = set()
    cur = by_pid.get(pid)
    while cur and cur.ppid and cur.ppid not in seen:
        seen.add(cur.ppid)
        parent = by_pid.get(cur.ppid)
        if parent is None:
            break
        chain.append(parent)
        cur = parent
    return chain


def close_instances(procs: list[ProcInfo], graceful_wait: float = 6.0) -> tuple[list[int], list[int]]:
    """先礼后兵。返回 (成功关闭, 仍在运行)。"""
    if not procs:
        return [], []
    pids = [p.pid for p in procs]
    # 先让有窗口的客户端自己退出，language_server 会随父进程一起走
    for p in procs:
        if p.is_client:
            _post_close(p.pid)

    deadline = time.monotonic() + graceful_wait
    while time.monotonic() < deadline:
        alive = {p.pid for p in snapshot()}
        if not any(pid in alive for pid in pids):
            return pids, []
        time.sleep(0.2)

    for pid in pids:
        if any(p.pid == pid for p in snapshot()):
            _terminate(pid)

    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        alive = {p.pid for p in snapshot()}
        if not any(pid in alive for pid in pids):
            break
        time.sleep(0.2)

    alive = {p.pid for p in snapshot()}
    still = [pid for pid in pids if pid in alive]
    return [pid for pid in pids if pid not in alive], still


@dataclass
class TreeWaitResult:
    server: ProcInfo | None
    table: list[ProcInfo]
    early_exit_code: int | None = None


def wait_for_tree(
    root_pid: int,
    timeout: float = 60.0,
    poll: float = 0.7,
    not_before: float = 0.0,
    popen: subprocess.Popen | None = None,
) -> TreeWaitResult:
    """等这次拉起的进程树里长出 language_server，若父进程提前退出则立即返回。"""
    deadline = time.monotonic() + timeout
    last_table: list[ProcInfo] = []
    while time.monotonic() < deadline:
        table = snapshot()
        last_table = table
        for p in table:
            if not p.is_language_server:
                continue
            if not_before and p.created and p.created < not_before - 1:
                continue
            chain = ancestors(p.pid, table)
            if any(a.pid == root_pid for a in chain):
                return TreeWaitResult(server=p, table=table)
        if popen is not None:
            rc = popen.poll()
            if rc is not None:
                return TreeWaitResult(server=None, table=last_table, early_exit_code=rc)
        time.sleep(poll)
    return TreeWaitResult(server=None, table=last_table)


def wait_for_language_server(
    root_pid: int,
    timeout: float = 60.0,
    poll: float = 0.7,
    not_before: float = 0.0,
) -> tuple[ProcInfo | None, list[ProcInfo]]:
    """等这次拉起的进程树里长出 language_server，并确认它的祖先确实是我们拉的那个。"""
    res = wait_for_tree(root_pid, timeout=timeout, poll=poll, not_before=not_before)
    return res.server, res.table
