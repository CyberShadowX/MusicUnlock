"""Resources whose lifetime belongs to one desktop application process."""
from __future__ import annotations

import ctypes
import os
import threading

from werkzeug.serving import make_server

from web.server import app

_job_handle = None
_job_limits_type = None
_job_lock = threading.RLock()


class DesktopStartup:
    """Turn asynchronous renderer failure into a bounded, reportable startup."""
    def __init__(self, window, renderer, timeout: float = 30):
        self.window = window
        self.renderer = renderer
        self.timeout = timeout
        self.error = None
        self._loaded = False
        self._closed = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        window.events.loaded += self._on_loaded
        window.events.closed += self.cancel

    def _on_loaded(self, *_):
        with self._lock:
            self._loaded = True
        self._wake.set()

    def cancel(self, *_):
        with self._lock:
            self._closed = True
        self._wake.set()

    def wait(self):
        with self._lock:
            if self._closed:
                return
        if str(self.renderer()) != "edgechromium":
            message = "桌面窗口需要 Microsoft WebView2 Runtime，当前组件不可用。"
        else:
            self._wake.wait(self.timeout)
            with self._lock:
                if self._loaded or self._closed:
                    return
            message = "桌面界面加载失败或超时，请重新启动并查看日志。"
        with self._lock:
            if self._closed:
                return
            self.error = RuntimeError(message)
        self.window.destroy()


class LocalService:
    def __init__(self, port: int = 0):
        self.port = port

    def __enter__(self):
        try:
            self.server = make_server("127.0.0.1", self.port, app, threaded=True)
        except SystemExit as exc:
            # Werkzeug uses sys.exit for bind failures; turn it into an ordinary
            # startup exception so the windowed entry point can show its dialog.
            raise RuntimeError(f"无法启动本机服务（端口 {self.port}），请改用空闲端口或自动选择端口。") from exc
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.server.server_close()
            raise
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def own_child_processes():
    """Have Windows reap this app's descendants when the app process exits."""
    global _job_handle, _job_limits_type
    if os.name != "nt" or _job_handle is not None:
        return
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD), ("minimum_set", ctypes.c_size_t),
            ("maximum_set", ctypes.c_size_t), ("process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel.CloseHandle(job)
        raise
    # Keep the handle open for the process lifetime. Closing it during Python
    # cleanup would kill this process before its service/files can be closed.
    _job_handle = job
    _job_limits_type = ExtendedLimits


def launch_update_helper(command):
    """Detach only the held installer child; leave normal descendants owned."""
    from ctypes import wintypes
    from pathlib import Path
    from music_unlock.update_process import ManagedProcess, clean_dll_directory, clean_environment
    if os.name != "nt":
        raise RuntimeError("更新安装仅支持 Windows")
    own_child_processes()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL

    def flags(value):
        limits = _job_limits_type()
        limits.basic.flags = value
        if not kernel.SetInformationJobObject(_job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())

    helper = None
    with _job_lock, clean_dll_directory():
        flags(0x2000 | 0x800)  # KILL_ON_CLOSE | explicit BREAKAWAY_OK
        try:
            log_path = str(Path(command[-1]).parent / "helper-launch.log") if "--manifest" in command else None
            helper = ManagedProcess.launch(command, env=clean_environment(), log_path=log_path, breakaway=True)
        finally:
            try:
                flags(0x2000)
            except BaseException:
                if helper is not None:
                    helper.close()  # still private kill-on-close job
                raise
    return helper
