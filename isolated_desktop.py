"""Start a GUI on a non-input Win32 desktop without touching shared input.

This isolates windows, not same-user IPC, COM, Shell, or a single-instance
application's forwarding behavior. Call verify_process_windows after startup.
All launched processes join a kill-on-close Job Object before their first
instruction runs. Close terminates any remaining job processes. The caller
retains this object until its isolated work has completed.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
import json
import os
import subprocess
import sys
import time
import uuid

if os.name != "nt":
    raise OSError("isolated_desktop requires Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
DESKTOP_READOBJECTS = 0x0001
DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_CREATEMENU = 0x0004
DESKTOP_ENUMERATE = 0x0040
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_ACCESS = (DESKTOP_READOBJECTS | DESKTOP_CREATEWINDOW |
                  DESKTOP_CREATEMENU | DESKTOP_ENUMERATE | DESKTOP_WRITEOBJECTS)
UOI_NAME = 2
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ULONG_PTR = ctypes.c_size_t
WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR),
                ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD),
                ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD), ("wShowWindow", w.WORD),
                ("cbReserved2", w.WORD), ("lpReserved2", ctypes.POINTER(w.BYTE)),
                ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                ("hStdError", w.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ULONG_PTR),
                ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG),
                ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD),
                ("hwndActive", w.HWND), ("hwndFocus", w.HWND),
                ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND),
                ("rcCaret", w.RECT)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD), ("Affinity", ULONG_PTR),
                ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", w.DWORD), ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD), ("TotalTerminatedProcesses", w.DWORD)]


user32.CreateDesktopW.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p,
                                   w.DWORD, w.DWORD, ctypes.c_void_p]
user32.CreateDesktopW.restype = w.HANDLE
user32.CloseDesktop.argtypes = [w.HANDLE]
user32.CloseDesktop.restype = w.BOOL
user32.OpenDesktopW.argtypes = [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD]
user32.OpenDesktopW.restype = w.HANDLE
user32.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
user32.OpenInputDesktop.restype = w.HANDLE
user32.GetThreadDesktop.argtypes = [w.DWORD]
user32.GetThreadDesktop.restype = w.HANDLE
user32.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int,
                                             ctypes.c_void_p, w.DWORD,
                                             ctypes.POINTER(w.DWORD)]
user32.GetUserObjectInformationW.restype = w.BOOL
user32.EnumDesktopWindows.argtypes = [w.HANDLE, WNDENUMPROC, w.LPARAM]
user32.EnumDesktopWindows.restype = w.BOOL
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.GetWindowThreadProcessId.restype = w.DWORD
user32.GetWindowTextLengthW.argtypes = [w.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [w.HWND]
user32.IsWindowVisible.restype = w.BOOL
user32.GetForegroundWindow.restype = w.HWND
user32.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = w.BOOL
kernel32.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p,
                                    ctypes.c_void_p, w.BOOL, w.DWORD,
                                    ctypes.c_void_p, w.LPCWSTR,
                                    ctypes.POINTER(STARTUPINFOW),
                                    ctypes.POINTER(PROCESS_INFORMATION)]
kernel32.CreateProcessW.restype = w.BOOL
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.CloseHandle.restype = w.BOOL
kernel32.GetCurrentThreadId.restype = w.DWORD
kernel32.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
kernel32.GetExitCodeProcess.restype = w.BOOL
kernel32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
kernel32.WaitForSingleObject.restype = w.DWORD
kernel32.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
kernel32.TerminateProcess.restype = w.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = w.HANDLE
kernel32.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
kernel32.CreateJobObjectW.restype = w.HANDLE
kernel32.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, w.DWORD]
kernel32.SetInformationJobObject.restype = w.BOOL
kernel32.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, w.DWORD,
                                                ctypes.POINTER(w.DWORD)]
kernel32.QueryInformationJobObject.restype = w.BOOL
kernel32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
kernel32.AssignProcessToJobObject.restype = w.BOOL
kernel32.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
kernel32.TerminateJobObject.restype = w.BOOL
kernel32.ResumeThread.argtypes = [w.HANDLE]
kernel32.ResumeThread.restype = w.DWORD
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.OpenProcess.restype = w.HANDLE


def _name(handle: int) -> str:
    needed = w.DWORD()
    user32.GetUserObjectInformationW(handle, UOI_NAME, None, 0,
                                     ctypes.byref(needed))
    if not needed.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(needed.value // ctypes.sizeof(w.WCHAR) + 1)
    if not user32.GetUserObjectInformationW(handle, UOI_NAME, buffer,
                                           ctypes.sizeof(buffer),
                                           ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def thread_desktop_name(thread_id: int) -> str:
    """The returned GetThreadDesktop handle is borrowed and must not be closed."""
    handle = user32.GetThreadDesktop(thread_id)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return _name(handle)


def input_snapshot() -> dict:
    """Read shared-input state from a caller on the normal/default desktop."""
    desktop = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        input_name = _name(desktop)
    finally:
        user32.CloseDesktop(desktop)
    foreground = user32.GetForegroundWindow()
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"input_desktop": input_name, "foreground": int(foreground or 0),
            "focus": int(info.hwndFocus or 0)}


def _process_family(root_pid: int) -> set[int]:
    handle = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        more = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            more = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    family = {root_pid}
    while True:
        added = {pid for pid, parent in parents.items() if parent in family} - family
        if not added:
            return family
        family.update(added)


def _windows(handle: int, family: set[int]) -> list[dict]:
    rows = []
    errors = []
    enumerated_desktop = _name(handle)

    @WNDENUMPROC
    def callback(hwnd, _):
        pid = w.DWORD()
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in family:
            return True
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            row = {"hwnd": int(hwnd), "pid": int(pid.value), "tid": int(tid),
                   "title": title.value, "visible": bool(user32.IsWindowVisible(hwnd)),
                   "desktop": enumerated_desktop,
                   "desktop_verification": "EnumDesktopWindows"}
            # The association is established by enumeration on this exact
            # desktop handle. Windows can deny GetThreadDesktop for another
            # process even though enumeration succeeds; preserve that result.
            try:
                actual = thread_desktop_name(tid)
                row["thread_desktop"] = actual
                if actual != enumerated_desktop:
                    raise RuntimeError("Window thread desktop disagrees with enumeration")
            except PermissionError as error:
                row["thread_desktop_query_error"] = error.winerror
            rows.append(row)
        except (OSError, RuntimeError) as error:
            errors.append(str(error))
        return True

    ctypes.set_last_error(0)
    enumerated = user32.EnumDesktopWindows(handle, callback, 0)
    error_code = ctypes.get_last_error()
    # A newly created desktop may have no windows yet and return zero without
    # an error. Never confuse an actual access/handle error with an empty list.
    if not enumerated and error_code:
        raise ctypes.WinError(error_code)
    if errors:
        raise OSError("Unable to verify a window desktop: " + errors[0])
    return rows


@dataclass
class IsolatedProcess:
    pid: int
    thread_id: int
    handle: int

    def poll(self) -> int | None:
        if not self.handle:
            raise RuntimeError("Process handle is closed")
        status = kernel32.WaitForSingleObject(self.handle, 0)
        if status == 0x00000102:
            return None
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = w.DWORD()
        if not kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(code.value)

    def wait(self, timeout: float = 10) -> int:
        status = kernel32.WaitForSingleObject(self.handle, max(0, int(timeout * 1000)))
        if status == 0x00000102:
            raise TimeoutError("Isolated process has not exited")
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def terminate(self, exit_code: int = 1) -> None:
        """Explicit opt-in; affects this owned process only, not other instances."""
        if self.poll() is None and not kernel32.TerminateProcess(self.handle, exit_code):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            if not kernel32.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = 0


class IsolatedDesktop:
    def __init__(self, name: str | None = None):
        self.name = name or "WorkBuddyClaim_" + uuid.uuid4().hex
        if not self.name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in self.name):
            raise ValueError("Desktop name must contain ASCII letters, digits, - or _")
        if self.name.casefold() in {"default", "winlogon", "disconnect"}:
            raise ValueError("The normal/input desktops cannot be used for isolation")
        existing = user32.OpenDesktopW(self.name, 0, False, DESKTOP_READOBJECTS)
        if existing:
            user32.CloseDesktop(existing)
            raise FileExistsError("An isolated desktop name must be new")
        self.handle = user32.CreateDesktopW(self.name, None, None, 0,
                                            DESKTOP_ACCESS, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.processes: list[IsolatedProcess] = []
        self.job_handle = kernel32.CreateJobObjectW(None, None)
        if not self.job_handle:
            error = ctypes.WinError(ctypes.get_last_error())
            user32.CloseDesktop(self.handle)
            self.handle = 0
            raise error
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        # No BREAKAWAY_OK/SILENT_BREAKAWAY_OK: descendants must remain in job.
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(self.job_handle, 9,
                                                ctypes.byref(limits),
                                                ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel32.CloseHandle(self.job_handle)
            user32.CloseDesktop(self.handle)
            self.job_handle = self.handle = 0
            raise error

    def launch(self, executable: str, args=(), cwd: str | None = None) -> IsolatedProcess:
        if not self.handle:
            raise RuntimeError("Desktop is closed")
        executable = os.path.abspath(executable)
        if not os.path.isfile(executable):
            raise FileNotFoundError(executable)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline([executable, *map(str, args)]))
        startup = STARTUPINFOW(cb=ctypes.sizeof(STARTUPINFOW),
                               lpDesktop="WinSta0\\" + self.name,
                               dwFlags=0x00000001, wShowWindow=4)
        info = PROCESS_INFORMATION()
        # Launch suspended so no GUI or descendant can escape before job
        # assignment. No handle inheritance and no breakaway launch flag.
        if not kernel32.CreateProcessW(executable, command, None, None, False,
                                      0x08000004, None, cwd, ctypes.byref(startup),
                                      ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel32.AssignProcessToJobObject(self.job_handle, info.hProcess):
                raise ctypes.WinError(ctypes.get_last_error())
            if kernel32.ResumeThread(info.hThread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            # Fail closed even when a parent job prevents nested assignment.
            kernel32.TerminateProcess(info.hProcess, 1)
            kernel32.WaitForSingleObject(info.hProcess, 10000)
            kernel32.CloseHandle(info.hProcess)
            raise
        finally:
            kernel32.CloseHandle(info.hThread)
        process = IsolatedProcess(int(info.dwProcessId), int(info.dwThreadId), int(info.hProcess))
        self.processes.append(process)
        return process

    def windows(self, root_pid: int) -> list[dict]:
        if not self.handle:
            raise RuntimeError("Desktop is closed")
        return _windows(self.handle, _process_family(root_pid))

    def verify_process_windows(self, root_pid: int, require_windows: bool = True) -> dict:
        """Verify observed windows on this desktop and reject escape to Default.

        Zero observed windows is not proof that a process has no GUI. The caller
        must wait for startup, verify its own application's business result, and
        assess same-user single-instance or Shell forwarding separately.
        """
        if not self.handle:
            raise RuntimeError("Desktop is closed")
        family = _process_family(root_pid)
        own = _windows(self.handle, family)
        default = user32.OpenDesktopW("Default", 0, False,
                                      DESKTOP_READOBJECTS | DESKTOP_ENUMERATE)
        if not default:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            escaped = _windows(default, family)
        finally:
            user32.CloseDesktop(default)
        if escaped:
            raise RuntimeError("A launched-process window appeared on Default")
        if any(row["desktop"] != self.name for row in own):
            raise RuntimeError("A window belongs to a different desktop")
        if require_windows and not own:
            raise RuntimeError("No isolated application window has been verified")
        if require_windows and not any(row["visible"] for row in own):
            raise RuntimeError("No visible isolated application window has been verified")
        return {"desktop": self.name, "process_ids": sorted(family),
                "windows": own, "default_window_count": len(escaped)}

    def job_process_count(self) -> int:
        if not self.job_handle:
            raise RuntimeError("Job is closed")
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not kernel32.QueryInformationJobObject(self.job_handle, 1,
                                                  ctypes.byref(info),
                                                  ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(info.ActiveProcesses)

    def close(self, timeout: float = 10) -> None:
        """End this job's remaining processes and then release owned handles."""
        error = None
        if self.job_handle:
            try:
                if self.job_process_count():
                    if not kernel32.TerminateJobObject(self.job_handle, 1):
                        raise ctypes.WinError(ctypes.get_last_error())
                    deadline = time.monotonic() + timeout
                    while self.job_process_count():
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Isolated job has not fully stopped")
                        time.sleep(.02)
            except BaseException as caught:
                error = caught
            finally:
                # If verification failed, KILL_ON_JOB_CLOSE is still enforced.
                if not kernel32.CloseHandle(self.job_handle) and error is None:
                    error = ctypes.WinError(ctypes.get_last_error())
                self.job_handle = 0
        for process in self.processes:
            process.close()
        self.processes.clear()
        if self.handle:
            if not user32.CloseDesktop(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = 0
        if error:
            raise error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _probe_child(duration_ms: int = 3000, with_descendant: bool = False) -> None:
    """A short-lived visible test GUI, never run on the input desktop."""
    import win32api
    import win32con
    import win32gui

    if with_descendant:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--job-sleep"],
                         creationflags=0x08000000)

    def handler(hwnd, message, wp, lp):
        if message == win32con.WM_TIMER:
            win32gui.DestroyWindow(hwnd)
            return 0
        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wp, lp)

    klass = win32gui.WNDCLASS()
    klass.lpszClassName = "WorkBuddyIsolatedDesktopTest"
    klass.lpfnWndProc = handler
    klass.hInstance = win32api.GetModuleHandle(None)
    win32gui.RegisterClass(klass)
    own_desktop = thread_desktop_name(kernel32.GetCurrentThreadId())
    title = "Isolated desktop startup test | " + own_desktop
    hwnd = win32gui.CreateWindow(klass.lpszClassName, title,
                                 win32con.WS_OVERLAPPEDWINDOW, 10, 10, 300, 150,
                                 0, 0, klass.hInstance, None)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    user32.SetTimer.argtypes = [w.HWND, ULONG_PTR, w.UINT, ctypes.c_void_p]
    user32.SetTimer.restype = ULONG_PTR
    if not user32.SetTimer(hwnd, 1, duration_ms, None):
        raise ctypes.WinError(ctypes.get_last_error())
    win32gui.PumpMessages()


def _self_test() -> dict:
    baseline = input_snapshot()
    changes = []
    observed = []
    with IsolatedDesktop() as desktop:
        process = desktop.launch(sys.executable, [os.path.abspath(__file__), "--probe-child"])
        deadline = time.monotonic() + 5
        verification = None
        while time.monotonic() < deadline:
            snap = input_snapshot()
            if snap != baseline:
                changes.append(snap)
            rows = desktop.windows(process.pid)
            target_ready = any(row["title"] == "Isolated desktop startup test | " + desktop.name for row in rows)
            if target_ready and verification is None:
                verification = desktop.verify_process_windows(process.pid)
                observed = rows
            if process.poll() is not None:
                break
            time.sleep(.01)
        if process.poll() is None:
            process.terminate()
            process.wait()
        exit_code = process.poll()
        if not verification:
            raise RuntimeError("Test child GUI was never verified")
        reported_thread_desktop = any(row["title"] == "Isolated desktop startup test | " + desktop.name for row in observed)
        result = {"baseline": baseline, "final": input_snapshot(),
                  "input_state_change_samples": len(changes),
                  "isolated_desktop": desktop.name, "observed_windows": observed,
                  "child_self_reported_thread_desktop_matches": reported_thread_desktop,
                  "process_exit_code": exit_code,
                  "passed": not changes and exit_code == 0 and reported_thread_desktop}
    return result


def _job_owner_for_test() -> None:
    # The test parent assigns this process to its outer job before sending go.
    # The desktop creates an inner job, proving nested job support on this host.
    if sys.stdin.readline().strip() != "go":
        raise RuntimeError("Missing test handshake")
    desktop = IsolatedDesktop()
    process = desktop.launch(sys.executable,
                             [os.path.abspath(__file__), "--job-tree-child"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = desktop.windows(process.pid)
        if any(row["visible"] for row in rows) and desktop.job_process_count() >= 2:
            break
        if process.poll() is not None:
            raise RuntimeError("Probe GUI exited before crash test")
        time.sleep(.02)
    verification = desktop.verify_process_windows(process.pid)
    family = sorted(_process_family(process.pid))
    if len(family) < 2 or desktop.job_process_count() < 2:
        raise RuntimeError("Probe descendant did not join the inner job")
    print(json.dumps({"owner_pid": os.getpid(), "root_pid": process.pid,
                      "child_pids": family, "job_process_count": desktop.job_process_count(),
                      "desktop": verification["desktop"]}), flush=True)
    if sys.stdin.readline().strip() != "crash":
        desktop.close()
        raise RuntimeError("Missing crash test handshake")
    # Deliberately bypass close/finally. The OS must close the job handle.
    os._exit(23)


def _job_crash_self_test() -> dict:
    baseline = input_snapshot()
    owner = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--job-owner"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True,
                             creationflags=0x08000000)
    handles = []
    outer_job = kernel32.CreateJobObjectW(None, None)
    try:
        if not outer_job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(outer_job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.AssignProcessToJobObject(outer_job, int(owner._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        owner.stdin.write("go\n")
        owner.stdin.flush()
        line = owner.stdout.readline()
        if not line:
            raise RuntimeError("Owner probe failed: " + owner.stderr.read())
        evidence = json.loads(line)
        for pid in evidence["child_pids"]:
            handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append((pid, handle))
        owner.stdin.write("crash\n")
        owner.stdin.flush()
        owner_code = owner.wait(timeout=10)
        stopped = {pid: kernel32.WaitForSingleObject(handle, 10000) == 0
                   for pid, handle in handles}
        final = input_snapshot()
        return {"baseline": baseline, "final": final,
                "nested_job_assignment_succeeded": True,
                "inner_job_process_count_before_crash": evidence["job_process_count"],
                "owner_exit_code": owner_code,
                "descendants_stopped_after_owner_crash": stopped,
                "passed": owner_code == 23 and all(stopped.values()) and final == baseline}
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)
        for _, handle in handles:
            kernel32.CloseHandle(handle)
        if outer_job:
            kernel32.CloseHandle(outer_job)


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe-child"]:
        _probe_child()
    elif sys.argv[1:] == ["--job-tree-child"]:
        _probe_child(30000, with_descendant=True)
    elif sys.argv[1:] == ["--job-sleep"]:
        time.sleep(30)
    elif sys.argv[1:] == ["--job-owner"]:
        _job_owner_for_test()
    elif sys.argv[1:] == ["--job-crash-self-test"]:
        result = _job_crash_self_test()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["passed"] else 1)
    elif sys.argv[1:] == ["--self-test"]:
        result = _self_test()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["passed"] else 1)
    else:
        print("Use --self-test or --job-crash-self-test for isolated GUI/job tests.")
