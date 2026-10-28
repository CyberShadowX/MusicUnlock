"""Accept the actual windowed EXE, embedded UI, native saves and window-close exit.

Requires Windows, the project test dependencies, Node and an existing Playwright
module. All generated audio, temporary runtime data and evidence stay in this
repository. It never installs packages or starts an external browser.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit
import uuid
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from .format_popup_capture import FormatPopupCapture
else:
    from format_popup_capture import FormatPopupCapture
TITLE = "MusicUnlock 音乐格式转换"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "iexplore.exe", "chromium.exe", "vivaldi.exe"}
CONSOLES = {"cmd.exe", "powershell.exe", "pwsh.exe"}


def pe_subsystem(executable: Path) -> int:
    with executable.open("rb") as source:
        assert source.read(2) == b"MZ", "The supplied file is not a Windows executable"
        source.seek(0x3C)
        offset = struct.unpack("<I", source.read(4))[0]
        source.seek(offset)
        assert source.read(4) == b"PE\0\0", "Invalid PE signature"
        source.seek(offset + 24 + 68)
        return struct.unpack("<H", source.read(2))[0]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fetch_json(url: str, timeout: float = 1):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


class GuiThreadInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("flags", wintypes.DWORD), ("active", wintypes.HWND),
                ("focus", wintypes.HWND), ("capture", wintypes.HWND), ("menu", wintypes.HWND),
                ("move", wintypes.HWND), ("caret", wintypes.HWND), ("rectangle", wintypes.RECT)]


class Win32:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = [
            (self.kernel, "CreateToolhelp32Snapshot", (wintypes.DWORD, wintypes.DWORD), wintypes.HANDLE),
            (self.kernel, "Process32FirstW", (wintypes.HANDLE, ctypes.POINTER(ProcessEntry)), wintypes.BOOL),
            (self.kernel, "Process32NextW", (wintypes.HANDLE, ctypes.POINTER(ProcessEntry)), wintypes.BOOL),
            (self.kernel, "OpenProcess", (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
            (self.kernel, "GetProcessTimes", (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4, wintypes.BOOL),
            (self.kernel, "GetExitCodeProcess", (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)), wintypes.BOOL),
            (self.kernel, "TerminateProcess", (wintypes.HANDLE, wintypes.UINT), wintypes.BOOL),
            (self.kernel, "WaitForSingleObject", (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
            (self.kernel, "CloseHandle", (wintypes.HANDLE,), wintypes.BOOL),
            (self.user, "EnumWindows", (self.callback, wintypes.LPARAM), wintypes.BOOL),
            (self.user, "EnumChildWindows", (wintypes.HWND, self.callback, wintypes.LPARAM), wintypes.BOOL),
            (self.user, "GetWindowThreadProcessId", (wintypes.HWND, ctypes.POINTER(wintypes.DWORD)), wintypes.DWORD),
            (self.user, "GetWindowTextW", (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int), ctypes.c_int),
            (self.user, "GetClassNameW", (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int), ctypes.c_int),
            (self.user, "GetDlgCtrlID", (wintypes.HWND,), ctypes.c_int),
            (self.user, "GetDlgItem", (wintypes.HWND, ctypes.c_int), wintypes.HWND),
            (self.user, "IsWindowVisible", (wintypes.HWND,), wintypes.BOOL),
            (self.user, "IsWindow", (wintypes.HWND,), wintypes.BOOL),
            (self.user, "SendMessageTimeoutW", (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)), ctypes.c_ssize_t),
            (self.user, "GetGUIThreadInfo", (wintypes.DWORD, ctypes.POINTER(GuiThreadInfo)), wintypes.BOOL),
            (self.user, "PostMessageW", (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM), wintypes.BOOL),
            (self.user, "PrintWindow", (wintypes.HWND, wintypes.HDC, wintypes.UINT), wintypes.BOOL),
            (self.user, "GetWindowLongW", (wintypes.HWND, ctypes.c_int), wintypes.LONG),
            (self.user, "SetWindowPos", (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT), wintypes.BOOL),
            (self.user, "GetWindowRect", (wintypes.HWND, ctypes.POINTER(wintypes.RECT)), wintypes.BOOL),
            (self.user, "GetDC", (wintypes.HWND,), wintypes.HDC),
            (self.user, "ReleaseDC", (wintypes.HWND, wintypes.HDC), ctypes.c_int),
            (self.gdi, "CreateCompatibleDC", (wintypes.HDC,), wintypes.HDC),
            (self.gdi, "CreateCompatibleBitmap", (wintypes.HDC, ctypes.c_int, ctypes.c_int), wintypes.HBITMAP),
            (self.gdi, "SelectObject", (wintypes.HDC, wintypes.HGDIOBJ), wintypes.HGDIOBJ),
            (self.gdi, "BitBlt", (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD), wintypes.BOOL),
            (self.gdi, "GetDIBits", (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT), ctypes.c_int),
            (self.gdi, "DeleteObject", (wintypes.HGDIOBJ,), wintypes.BOOL),
            (self.gdi, "DeleteDC", (wintypes.HDC,), wintypes.BOOL),
        ]
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        try:
            self.user.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
            self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            self.user.SetProcessDPIAware()

    def processes(self) -> dict[int, dict]:
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            result = {}
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                result[entry.th32ProcessID] = {
                    "pid": entry.th32ProcessID, "parent_pid": entry.th32ParentProcessID,
                    "name": entry.szExeFile,
                }
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
            return result
        finally:
            self.kernel.CloseHandle(snapshot)

    def creation_time(self, pid: int) -> int | None:
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            return self._active_creation_time(handle)
        finally:
            self.kernel.CloseHandle(handle)

    def _active_creation_time(self, handle) -> int | None:
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        values = [wintypes.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            return None
        return values[0].dwLowDateTime | (values[0].dwHighDateTime << 32)

    def bind_process(self, pid: int):
        """Pair current parent/name metadata with a pinned process identity."""
        handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            return None
        retained = False
        try:
            created = self._active_creation_time(handle)
            if created is None:
                return None
            # This must be a NEW snapshot after OpenProcess. Keeping this handle
            # open prevents the PID being reused while metadata is collected.
            current = self.processes().get(pid)
            if current is None or self._active_creation_time(handle) != created:
                return None
            retained = True
            return handle, {**current, "created": created}
        finally:
            if not retained:
                self.kernel.CloseHandle(handle)

    def terminate_owned(self, pid: int, created: int) -> bool:
        # Query and terminate through one held handle. Reopening by PID, or
        # recursively following historical parent PIDs, can affect another app.
        handle = self.kernel.OpenProcess(0x1000 | 0x0001 | 0x100000, False, pid)
        if not handle:
            return False
        try:
            if self._active_creation_time(handle) != created:
                return False
            if not self.kernel.TerminateProcess(handle, 1):
                error = ctypes.get_last_error()
                if self._active_creation_time(handle) is None:
                    return False  # Exited normally after the identity check.
                raise ctypes.WinError(error)
            status = self.kernel.WaitForSingleObject(handle, 5000)
            if status == 258:
                raise TimeoutError(f"Owned process {pid} did not exit after termination")
            if status != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            return True
        finally:
            self.kernel.CloseHandle(handle)

    def windows(self, parent: int | None = None) -> list[dict]:
        result = []

        @self.callback
        def found(hwnd, _parameter):
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            text, name = ctypes.create_unicode_buffer(2048), ctypes.create_unicode_buffer(256)
            self.user.GetWindowTextW(hwnd, text, len(text))
            self.user.GetClassNameW(hwnd, name, len(name))
            result.append({"hwnd": int(hwnd), "pid": pid.value, "title": text.value, "class": name.value,
                           "visible": bool(self.user.IsWindowVisible(hwnd)), "control_id": self.user.GetDlgCtrlID(hwnd)})
            return True

        if parent is None:
            self.user.EnumWindows(found, 0)
        else:
            self.user.EnumChildWindows(parent, found, 0)
        return result

    def close(self, hwnd: int):
        if not self.user.PostMessageW(hwnd, 0x0010, 0, 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def control_text(self, hwnd: int) -> str:
        text, result = ctypes.create_unicode_buffer(2048), ctypes.c_size_t()
        assert self.user.SendMessageTimeoutW(hwnd, 0x000D, len(text), ctypes.addressof(text), 2, 2000, ctypes.byref(result)), "WM_GETTEXT timed out"
        return text.value

    def set_control_text(self, hwnd: int, value: str):
        # The shell maintains a filename value separate from the Edit's display
        # text. An atomic edit operation sends change notifications and avoids
        # per-character autocomplete. Address only this exact owned control.
        native_element(hwnd).SetFocus()
        pid, info = wintypes.DWORD(), GuiThreadInfo()
        thread = self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        info.size = ctypes.sizeof(info)
        assert self.user.GetGUIThreadInfo(thread, ctypes.byref(info)) and info.focus == hwnd, "Filename Edit did not receive focus; refusing keyboard input"
        result = ctypes.c_size_t()
        text = ctypes.create_unicode_buffer(value)
        for message, wparam, lparam, name in (
            (0x00B1, 0, -1, "EM_SETSEL"),
            (0x00C2, 0, ctypes.addressof(text), "EM_REPLACESEL"),
        ):
            ctypes.set_last_error(0)
            if not self.user.SendMessageTimeoutW(hwnd, message, wparam, lparam, 2, 2000, ctypes.byref(result)):
                raise OSError(ctypes.get_last_error(), f"Native filename {name} failed or timed out")

    def screenshot(self, hwnd: int, destination: Path, desktop: bool = False):
        was_topmost = bool(self.user.GetWindowLongW(hwnd, -20) & 8)
        if desktop:
            # PrintWindow cannot paint WebView2's GPU composition surface. Show
            # only our test window above others without changing input focus.
            assert self.user.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            time.sleep(0.3)
        try:
            return self._capture(hwnd, destination, desktop)
        finally:
            if desktop and not was_topmost:
                assert self.user.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0013)

    def _capture(self, hwnd: int, destination: Path, desktop: bool):
        rectangle = wintypes.RECT()
        assert self.user.GetWindowRect(hwnd, ctypes.byref(rectangle))
        width, height = rectangle.right - rectangle.left, rectangle.bottom - rectangle.top
        assert width > 0 and height > 0
        screen = self.user.GetDC(None)
        memory = self.gdi.CreateCompatibleDC(screen)
        bitmap = self.gdi.CreateCompatibleBitmap(screen, width, height)
        assert screen and memory and bitmap, "Cannot allocate native screenshot bitmap"
        previous = self.gdi.SelectObject(memory, bitmap)
        try:
            if desktop:
                assert self.gdi.BitBlt(memory, 0, 0, width, height, screen, rectangle.left, rectangle.top, 0x00CC0020)
            else:
                assert self.user.PrintWindow(hwnd, memory, 2), "Native PrintWindow capture failed"
            self.gdi.SelectObject(memory, previous)
            header = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0))
            pixels = ctypes.create_string_buffer(width * height * 4)
            assert self.gdi.GetDIBits(screen, bitmap, 0, height, pixels, header, 0) == height
            raw = pixels.raw
            rgba = bytearray(len(raw))
            rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4] = raw[2::4], raw[1::4], raw[0::4], b"\xff" * (width * height)

            def chunk(name: bytes, content: bytes) -> bytes:
                return struct.pack(">I", len(content)) + name + content + struct.pack(">I", zlib.crc32(name + content))

            scanlines = b"".join(b"\x00" + rgba[row * width * 4:(row + 1) * width * 4] for row in range(height))
            destination.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))
        finally:
            self.gdi.SelectObject(memory, previous)
            self.gdi.DeleteObject(bitmap)
            self.gdi.DeleteDC(memory)
            self.user.ReleaseDC(None, screen)
        return {"path": str(destination), "width": width, "height": height,
                "method": "desktop BitBlt; temporary non-activating topmost" if desktop else "PrintWindow(PW_RENDERFULLCONTENT)",
                "includes_native_title_bar": True}


class ProcessAudit:
    def __init__(self, win32: Win32, root_pid: int):
        self.win32, self.root_pid = win32, root_pid
        self.root_created = win32.creation_time(root_pid)
        self.owned: dict[int, dict] = {}
        self.handles = {}
        self.console_windows: dict[int, dict] = {}
        self.error = None
        self.lock = threading.RLock()
        self.stop = threading.Event()
        try:
            self.sample()
            self.thread = threading.Thread(target=self.monitor, daemon=True)
            self.thread.start()
        except BaseException:
            self._close_handles()
            raise

    def sample(self):
        processes = self.win32.processes()
        with self.lock:
            # Pin every accepted identity through UI checks and finish(). The
            # root's Popen handle also prevents reuse before initial binding.
            known = dict(self.owned)
            if self.root_pid not in known and self.root_created is not None:
                known[self.root_pid] = {"created": self.root_created}
            for pid, item in known.items():
                if pid in self.handles:
                    continue
                bound = self.win32.bind_process(pid)
                if bound is None:
                    continue
                handle, current = bound
                if current["created"] == item["created"]:
                    self.handles[pid], self.owned[pid] = handle, current
                else:
                    self.win32.kernel.CloseHandle(handle)
            verified = {
                pid: self.owned[pid]["created"] for pid, handle in self.handles.items()
                if self.win32._active_creation_time(handle) == self.owned[pid]["created"]
            }
            while True:
                added = False
                for pid, item in processes.items():
                    parent = item["parent_pid"]
                    if pid in self.handles or parent not in verified:
                        continue
                    parent_created = verified[parent]
                    bound = self.win32.bind_process(pid)
                    if bound is None:
                        continue
                    handle, current = bound
                    retained = False
                    try:
                        # The first snapshot is only a discovery hint. Use the
                        # candidate's FRESH bound PPID/name and hold its verified
                        # parent object throughout adoption. An older orphan
                        # cannot inherit authority from a now-reused PPID.
                        if current["parent_pid"] != parent or current["created"] < parent_created:
                            continue
                        if self.win32._active_creation_time(self.handles[parent]) != parent_created:
                            continue
                        if self.win32._active_creation_time(handle) != current["created"]:
                            continue
                        self.handles[pid], self.owned[pid] = handle, current
                        verified[pid] = current["created"]
                        retained = added = True
                    finally:
                        if not retained:
                            self.win32.kernel.CloseHandle(handle)
                if not added:
                    break
            for window in self.win32.windows():
                if window["pid"] in verified and window["class"] == "ConsoleWindowClass":
                    self.console_windows[window["hwnd"]] = window

    def monitor(self):
        try:
            while not self.stop.wait(0.05):
                self.sample()
        except Exception as exception:
            self.error = str(exception)

    def pids(self) -> set[int]:
        # UI ownership checks must not trust dead or reused historical PIDs.
        return {item["pid"] for item in self.alive()}

    def alive(self) -> list[dict]:
        with self.lock:
            return [dict(self.owned[pid]) for pid, handle in self.handles.items()
                    if self.win32._active_creation_time(handle) == self.owned[pid]["created"]]

    def _close_handles(self):
        with self.lock:
            for handle in self.handles.values():
                self.win32.kernel.CloseHandle(handle)
            self.handles.clear()

    def finish(self) -> dict:
        self.stop.set()
        try:
            self.thread.join(timeout=5)
            assert not self.thread.is_alive(), "Process audit did not stop"
            assert self.error is None, self.error
            self.sample()
            return {"sampling_interval_ms": 50, "owned_processes": list(self.owned.values()),
                    "console_windows": list(self.console_windows.values()),
                    "external_browser_processes": [item for item in self.owned.values() if item["name"].casefold() in BROWSERS],
                    "background_console_hosts": [item for item in self.owned.values() if item["name"].casefold() in {"conhost.exe", "openconsole.exe"}],
                    "console_processes": [item for item in self.owned.values() if item["name"].casefold() in CONSOLES]}
        finally:
            self._close_handles()


class NativeSaves:
    def __init__(self, win32: Win32, audit: ProcessAudit, destinations: list[Path], initial_names: list[str], *,
                 omit_extensions: bool = False, cancel_indices: set[int] | None = None):
        self.win32, self.audit, self.destinations = win32, audit, destinations
        self.initial_names = initial_names
        self.omit_extensions = omit_extensions
        self.cancel_indices = cancel_indices or set()
        self.records: list[dict] = []
        self.last_dialog_state = None
        self.error = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()

    def monitor(self):
        try:
            handled = set()
            while not self.stop.wait(0.1):
                dialogs = [item for item in self.win32.windows() if item["visible"] and item["pid"] in self.audit.pids() and item["class"] == "#32770"]
                handled.intersection_update(item["hwnd"] for item in dialogs)
                for dialog in dialogs:
                    if dialog["hwnd"] in handled:
                        continue
                    assert len(self.records) < len(self.destinations), f"Unexpected additional native dialog: {dialog}"
                    children = self.win32.windows(dialog["hwnd"])
                    edits = [item for item in children if item["visible"] and item["class"] == "Edit" and item["control_id"] in (1001, 1148)]
                    if not edits:
                        continue
                    assert len(edits) == 1, f"Ambiguous native filename field: {edits}"
                    # GetWindowText cannot retrieve a foreign Edit's text.
                    initial = self.win32.control_text(edits[0]["hwnd"])
                    self.last_dialog_state = {"dialog": dialog, "filename_control": edits[0], "filename_text": initial}
                    expected_name = self.initial_names[len(self.records)]
                    # Windows may hide a known filename extension in this Edit.
                    if initial not in (expected_name, Path(expected_name).stem):
                        continue
                    destination = self.destinations[len(self.records)]
                    assert not destination.exists(), f"Refusing to overwrite {destination}"
                    entered = destination.with_suffix('') if self.omit_extensions else destination
                    cancel = len(self.records) in self.cancel_indices
                    if cancel:
                        button = self.win32.user.GetDlgItem(dialog["hwnd"], 2)
                        assert button, 'Native Cancel button is absent'
                        self.records.append({'dialog': dialog, 'initial_filename': initial, 'cancelled': True})
                        handled.add(dialog['hwnd'])
                        invoke_native_save(int(button))
                        continue
                    self.win32.set_control_text(edits[0]["hwnd"], str(entered))
                    deadline = time.monotonic() + 8
                    while True:
                        current = self.win32.control_text(edits[0]["hwnd"])
                        if current == str(entered):
                            time.sleep(0.1)
                            if self.win32.control_text(edits[0]["hwnd"]) == str(entered):
                                break
                        assert time.monotonic() < deadline, f"Native filename did not settle before Save: {current}"
                        time.sleep(0.1)
                    button = self.win32.user.GetDlgItem(dialog["hwnd"], 1)
                    assert button, f"Save button is absent: {dialog}"
                    self.records.append({"dialog": dialog, "filename_control": edits[0], "destination": str(destination),
                                         "initial_filename": initial, "omitted_extension": self.omit_extensions,
                                         "filename_readback": current, "native_save_button": int(button),
                                         "filename_input": "atomic EM_SETSEL + EM_REPLACESEL to the verified native Edit",
                                         "save_action": "UI Automation InvokePattern on the genuine Save button"})
                    handled.add(dialog["hwnd"])
                    invoke_native_save(int(button))
        except Exception as exception:
            self.error = str(exception)

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "Native save observer did not stop"
        assert self.error is None, self.error
        assert len(self.records) == len(self.destinations), self.records
        return self.records


def assert_no_console(pid: int) -> dict:
    helper = """import ctypes,json,sys
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.FreeConsole()
attached=bool(k.AttachConsole(int(sys.argv[1])))
error=0 if attached else ctypes.get_last_error()
if attached:k.FreeConsole()
print(json.dumps({'pid':int(sys.argv[1]),'attached':attached,'error':error}))
"""
    result = subprocess.run([sys.executable, "-c", helper, str(pid)], capture_output=True, text=True,
                            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    report = json.loads(result.stdout)
    assert report["attached"] is False and report["error"] == 6, f"Application owns a console or probe failed: {report}"
    return report


def native_element(hwnd: int):
    import clr

    framework = "Framework64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "Framework"
    assemblies = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / framework / "v4.0.30319" / "WPF"
    clr.AddReference(str(assemblies / "UIAutomationClient.dll"))
    clr.AddReference(str(assemblies / "UIAutomationTypes.dll"))
    from System import IntPtr
    from System.Windows.Automation import AutomationElement

    return AutomationElement.FromHandle(IntPtr(hwnd))


def invoke_native_save(button: int):
    """Operate the genuine shell Save button through Windows UI Automation."""
    element = native_element(button)
    from System.Windows.Automation import InvokePattern

    assert element.Current.IsEnabled, "Native Save button is disabled"
    element.SetFocus()
    element.GetCurrentPattern(InvokePattern.Pattern).Invoke()


def fixtures(directory: Path) -> list[dict]:
    sys.path.insert(0, str(ROOT / "tests"))
    from builders import CROSS_PLAIN, build_ncm

    records = []
    for name, title in (("桌面 单曲.ncm", "桌面窗口验收"), ("桌面 批量.ncm", "桌面批量验收")):
        location = directory / name
        metadata = {"format": "mp3", "musicName": title, "artist": [["合成歌手", 1]], "album": "桌面测试专辑"}
        location.write_bytes(build_ncm(CROSS_PLAIN, b"0123456789abcdef", metadata, cover=PNG))
        records.append({"path": str(location), "name": name, "title": title, "output_name": location.with_suffix(".flac").name})
    invalid = directory / "无效合成文件.ncm"
    invalid.write_bytes(b"NOT AN NCM FILE" * 10)
    records.append({"path": str(invalid), "name": invalid.name})
    return records


def inspect_flac(blob: bytes, title: str) -> dict:
    from mutagen.flac import FLAC

    assert blob.startswith(b"fLaC"), "Saved single download is not a FLAC container"
    audio = FLAC(io.BytesIO(blob))
    expected = {"title": title, "artist": "合成歌手", "album": "桌面测试专辑"}
    for key, value in expected.items():
        assert audio.get(key) == [value], (key, audio.get(key), value)
    assert audio.info.sample_rate > 0 and audio.info.length > 0
    assert any(picture.data == PNG for picture in audio.pictures), "Saved FLAC lost the synthetic cover"
    return {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "tags": expected,
            "sample_rate": audio.info.sample_rate, "duration_seconds": audio.info.length, "cover_matches": True}


def verify(exe: Path, lifecycle_only: bool = False, requested_port: int | None = None) -> dict:
    assert os.name == "nt", "Desktop acceptance requires Windows"
    assert pe_subsystem(exe) == 2, "Expected PE subsystem 2 (Windows GUI); refusing to run a console artifact"
    node = shutil.which("node")
    if not lifecycle_only:
        assert node, "Node is required for embedded WebView2 E2E"
        subprocess.run([node, str(ROOT / "scripts" / "desktop_e2e.cjs"), "--check"], check=True, timeout=15)
    directory = ROOT / "output" / "playwright" / "desktop" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    working, runtime = directory / "中文 空格 working directory", directory / "runtime temp"
    working.mkdir()
    runtime.mkdir()
    debug_port = free_port()
    while debug_port == requested_port:
        debug_port = free_port()
    cdp = f"http://127.0.0.1:{debug_port}"
    restricted_path = str(Path(os.environ["SystemRoot"]) / "System32")
    git = shutil.which("git")
    if git:
        restricted_path += os.pathsep + str(Path(git).parent)
    assert shutil.which("python", path=restricted_path) is None
    assert shutil.which("ffmpeg", path=restricted_path) is None
    env = {**os.environ, "PATH": restricted_path, "TEMP": str(runtime), "TMP": str(runtime),
           "PYTHONIOENCODING": "utf-8", "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={debug_port} --remote-debugging-address=127.0.0.1"}
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    arguments = [] if requested_port is None else ["--port", str(requested_port)]
    report = {"passed": False, "executable": str(exe), "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
              "pe_subsystem": 2, "arguments": arguments, "evidence_directory": str(directory),
              "restricted_path": restricted_path, "lifecycle_only": lifecycle_only}
    report["harness_sha256"] = {
        name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
        for name in ("check_desktop_exe.py", "desktop_e2e.cjs", "settings_e2e.cjs", "format_palette_e2e.cjs", "format_popup_capture.py")
    }
    win32 = Win32()
    process = None
    audit = None
    saves = None
    popup = None
    window = None
    base = None
    try:
        with (directory / "application.log").open("wb") as output:
            # No CREATE_NO_WINDOW or hidden startup option: test normal launch.
            process = subprocess.Popen([str(exe), *arguments], cwd=working, env=env, stdout=output, stderr=subprocess.STDOUT)
        report["launcher_pid"] = process.pid
        audit = ProcessAudit(win32, process.pid)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            assert process.poll() is None, f"EXE exited during startup: {process.returncode}; see {directory}"
            audit.sample()
            windows = [item for item in win32.windows() if item["pid"] in audit.pids() and item["visible"] and item["title"] == TITLE]
            if windows:
                assert len(windows) == 1, windows
                window = windows[0]
                try:
                    targets = fetch_json(f"{cdp}/json/list")
                    pages = [item for item in targets if item.get("type") == "page" and item.get("url", "").startswith("http://127.0.0.1:")]
                    if len(pages) == 1:
                        parsed = urlsplit(pages[0]["url"])
                        base = f"http://127.0.0.1:{parsed.port}"
                        assert requested_port is None or parsed.port == requested_port, (parsed.port, requested_port)
                        assert fetch_json(f"{base}/api/health")["ok"] is True
                        break
                except (OSError, ValueError):
                    pass
            time.sleep(0.1)
        else:
            raise AssertionError(f"Native desktop window and embedded CDP page did not become ready; see {directory}")
        report.update(native_window=window, url=base, cdp=cdp)
        report["no_console_probes"] = [assert_no_console(pid) for pid in sorted({process.pid, window["pid"]})]
        print(f"PASS normal desktop launch: native HWND={window['hwnd']}; no console; {base}", flush=True)
        if not lifecycle_only:
            generated = fixtures(directory)
            single, archive = directory / "另存为 单曲.flac", directory / "另存为 全部.zip"
            saves = NativeSaves(win32, audit, [single, archive], [generated[0]["output_name"], "MusicUnlock.zip"], omit_extensions=True)
            config = {"cdp": cdp, "base": base, "fixtures": generated, "single": str(single), "zip": str(archive),
                      "report": str(directory / "embedded-e2e.json"), "domScreenshot": str(directory / "embedded-ui.png"),
                      "failureScreenshot": str(directory / "embedded-failure.png"),
                      "menuScreenshot": str(directory / "format-menu.png")}
            config_file = directory / "e2e-config.json"
            config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            with (directory / "embedded-e2e.log").open("wb") as log:
                subprocess.run([node, str(ROOT / "scripts" / "desktop_e2e.cjs"), "--config", str(config_file)],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True)
            report["native_save_dialogs"] = saves.finish()
            saves = None
            report["embedded_e2e"] = json.loads((directory / "embedded-e2e.json").read_text(encoding="utf-8"))
            report["single_flac"] = inspect_flac(single.read_bytes(), generated[0]["title"])
            with zipfile.ZipFile(archive) as bundle:
                assert sorted(bundle.namelist()) == sorted(item["output_name"] for item in generated[:2]), bundle.namelist()
                assert bundle.read(generated[0]["output_name"]) == single.read_bytes(), "ZIP and single download bytes differ"
                report["zip_members"] = {item["output_name"]: inspect_flac(bundle.read(item["output_name"]), item["title"]) for item in generated[:2]}
            print("PASS embedded WebView2 UI: upload; FLAC; readable invalid input; native single/ZIP saves; bytes/tags/cover", flush=True)
        report["native_screenshot"] = win32.screenshot(window["hwnd"], directory / "native-window.png", desktop=True)
        audit.sample()
        win32.close(window["hwnd"])
        code = process.wait(timeout=20)
        assert code == 0, f"Normal window close exited with code {code}"
        deadline = time.monotonic() + 20
        while audit.alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not audit.alive(), f"Owned processes survived native window close: {audit.alive()}"
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", urlsplit(base).port)) != 0, "Listener remained after native window close"
        assert not win32.user.IsWindow(window["hwnd"]), "Native window survived WM_CLOSE"
        report["shutdown"] = {"method": "WM_CLOSE to the real native window", "exit_code": code,
                              "listener_stopped": True, "owned_process_tree_exited": True, "taskkill_used": False}
        report["process_audit"] = audit.finish()
        audit = None
        assert report["process_audit"]["external_browser_processes"] == [], "Application launched an external browser"
        assert report["process_audit"]["console_processes"] == [], "Application launched a console process"
        assert not any(item["visible"] for item in report["process_audit"]["console_windows"]), "Application created a visible console window"
        assert any(item["name"].casefold() == "msedgewebview2.exe" for item in report["process_audit"]["owned_processes"]), "No application-owned WebView2 runtime was observed"
        report["passed"] = True
        print("PASS native window close: zero exit code; listener stopped; full owned tree exited; no external browser or console observed", flush=True)
        return report
    except BaseException as exception:
        report["error"] = f"{type(exception).__name__}: {exception}"
        if saves is not None:
            report["native_save_error"] = saves.error
            report["native_save_dialogs"] = saves.records
            report["native_save_last_state"] = saves.last_dialog_state
        raise
    finally:
        if popup is not None:
            popup.stop.set()
            popup.thread.join(timeout=5)
        if saves is not None:
            saves.stop.set()
            saves.thread.join(timeout=5)
        if process is not None and process.poll() is None and window is not None:
            win32.close(window["hwnd"])
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if audit is not None:
            # Each known identity is verified and terminated through one handle;
            # never enumerate an unverified tree by historical parent PID.
            survivors = audit.alive()
            if survivors:
                report["failure_cleanup"] = []
                for item in survivors:
                    record = {"pid": item["pid"], "created": item["created"]}
                    try:
                        record["terminated"] = win32.terminate_owned(item["pid"], item["created"])
                    except OSError as exception:
                        record["error"] = str(exception)
                    report["failure_cleanup"].append(record)
            report["process_audit"] = audit.finish()
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Desktop acceptance evidence: {directory / 'report.json'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--lifecycle-only", action="store_true", help="Check native launch and close without conversion E2E")
    parser.add_argument("--port", type=int, help="Pass an explicit port; omitted verifies the default no-argument GUI")
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be 1..65535")
    verify(args.executable.resolve(strict=True), args.lifecycle_only, args.port)


if __name__ == "__main__":
    main()
