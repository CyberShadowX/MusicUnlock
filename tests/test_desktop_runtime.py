"""Desktop lifetime owns its real local service and child processes."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace
import urllib.request
from urllib.parse import urlsplit

import pytest

from desktop_runtime import LocalService


class NativeEvent:
    def __init__(self):
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self

    def fire(self):
        for callback in self.callbacks:
            callback()


class NativeWindow:
    def __init__(self):
        self.events = SimpleNamespace(loaded=NativeEvent(), closed=NativeEvent())
        self.destroyed = False
        self.exposed = []

    def expose(self, *methods):
        self.exposed.extend(method.__name__ for method in methods)

    def destroy(self):
        self.destroyed = True
        self.events.closed.fire()


def test_missing_native_loaded_event_closes_failed_window():
    from desktop_runtime import DesktopStartup

    window = NativeWindow()
    startup = DesktopStartup(window, lambda: "edgechromium", timeout=0.01)
    startup.wait()
    assert window.destroyed
    assert isinstance(startup.error, RuntimeError)


def test_unsupported_renderer_closes_window():
    from desktop_runtime import DesktopStartup

    window = NativeWindow()
    startup = DesktopStartup(window, lambda: "mshtml", timeout=0.01)
    startup.wait()
    assert window.destroyed
    assert isinstance(startup.error, RuntimeError)


def test_ready_native_window_stays_open():
    from desktop_runtime import DesktopStartup

    window = NativeWindow()
    startup = DesktopStartup(window, lambda: "edgechromium", timeout=0.01)
    window.events.loaded.fire()
    startup.wait()
    assert not window.destroyed
    assert startup.error is None


def test_close_before_loaded_cancels_startup_wait():
    from desktop_runtime import DesktopStartup

    window = NativeWindow()
    startup = DesktopStartup(window, lambda: "edgechromium", timeout=30)
    monitor = threading.Thread(target=startup.wait, daemon=True)
    monitor.start()
    window.events.closed.fire()
    monitor.join(timeout=1)
    assert not monitor.is_alive()
    assert startup.error is None


def assert_stopped(url: str):
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(("127.0.0.1", urlsplit(url).port)) != 0


def test_service_context_owns_listener_lifetime():
    with LocalService() as service:
        url = service.url
        with urllib.request.urlopen(f"{url}/api/health", timeout=3) as response:
            assert json.load(response)["ok"] is True
    assert_stopped(url)


def test_exception_during_window_start_releases_listener():
    with pytest.raises(RuntimeError, match="window unavailable"):
        with LocalService() as service:
            url = service.url
            raise RuntimeError("window unavailable")
    assert_stopped(url)


def test_occupied_port_is_a_reportable_startup_error():
    with socket.socket() as occupied:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(RuntimeError, match="本机服务"):
            with LocalService(occupied.getsockname()[1]):
                pytest.fail("Occupied port unexpectedly accepted")


@pytest.mark.parametrize("gui_mode", ["ready", "sync_failure", "async_failure", "renderer_fallback"])
def test_desktop_event_loop_owns_service_without_external_browser(tmp_path, monkeypatch, gui_mode):
    import webbrowser
    import webview
    import windows_launcher

    observed = {}

    def create_window(title, url, **options):
        observed["url"] = url
        observed["window"] = NativeWindow()
        return observed["window"]

    def start(**options):
        with urllib.request.urlopen(f"{observed['url']}/api/health", timeout=3) as response:
            assert json.load(response)["ok"] is True
        if gui_mode == "sync_failure":
            raise RuntimeError("native GUI failed")
        if gui_mode == "ready":
            observed["window"].events.loaded.fire()
        options["func"]()

    def forbidden_browser(_url):
        raise AssertionError("Desktop launch opened an external browser")

    monkeypatch.setattr(webbrowser, "open", forbidden_browser)
    monkeypatch.setattr(webview, "create_window", create_window)
    monkeypatch.setattr(webview, "start", start)
    monkeypatch.setattr(webview, "renderer", "mshtml" if gui_mode == "renderer_fallback" else "edgechromium")
    startup_type = windows_launcher.DesktopStartup
    monkeypatch.setattr(windows_launcher, "DesktopStartup",
                        lambda window, renderer: startup_type(window, renderer, timeout=0.01))
    if gui_mode != "ready":
        with pytest.raises(RuntimeError):
            windows_launcher.run_desktop(tmp_path)
        if gui_mode != "sync_failure":
            assert observed["window"].destroyed
    else:
        report = tmp_path / "startup-ready.json"
        assert windows_launcher.run_desktop(tmp_path, startup_report=report) == 0
        assert json.loads(report.read_text())["status"] == "ready"
        assert json.loads(report.read_text())["app_name"] == "MusicUnlock"
        assert set(observed["window"].exposed) == {
            "get_update_state", "check_updates", "start_update",
            "choose_source_workspace", "restart_after_update"}
    assert_stopped(observed["url"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process ownership")
def test_owned_child_process_exits_with_desktop_process():
    import ctypes
    from ctypes import wintypes

    root = Path(__file__).resolve().parents[1]
    code = (
        "from desktop_runtime import own_child_processes; import subprocess,sys,time; "
        "own_child_processes(); "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW); "
        "print(child.pid,flush=True); time.sleep(1)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], cwd=root, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    child_pid = int(process.stdout.readline().strip())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    child_handle = kernel.OpenProcess(0x00100000, False, child_pid)
    assert child_handle
    try:
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 0, errors
        assert kernel.WaitForSingleObject(child_handle, 5000) == 0, "Child outlived application"
    finally:
        kernel.CloseHandle(child_handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job breakaway")
def test_update_helper_survives_old_app_job_after_explicit_release(tmp_path):
    marker = tmp_path / "helper-survived.json"
    root = Path(__file__).resolve().parents[1]
    child_code = "import time,pathlib; time.sleep(2); pathlib.Path(" + repr(str(marker)) + ").write_text('survived')"
    parent_code = (
        "from desktop_runtime import own_child_processes,launch_update_helper; import sys; "
        "own_child_processes(); helper=launch_update_helper([sys.executable,'-c'," + repr(child_code) + "]); "
        "helper.close(release=True)"
    )
    result = subprocess.run([sys.executable, "-c", parent_code], cwd=root, capture_output=True,
                            text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    import time
    deadline = time.monotonic() + 8
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert marker.read_text() == "survived"


def test_managed_launch_failure_writes_receipt_without_error_dialog(tmp_path, monkeypatch):
    import windows_launcher
    import ctypes
    monkeypatch.setattr(windows_launcher, "run_desktop", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fixture-failure")))
    monkeypatch.setattr(windows_launcher, "__file__", str(tmp_path / "windows_launcher.py"))
    if sys.platform == "win32":
        monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *a: pytest.fail("Managed startup blocked on dialog"))
    report = tmp_path / "startup.json"
    assert windows_launcher.main(["--startup-report", str(report)]) == 1
    assert json.loads(report.read_text())["status"] == "failed"
