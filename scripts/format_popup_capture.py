"""Capture the real native select menu, rather than a DOM-only screenshot."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import threading
import time


class FormatPopupCapture:
    def __init__(self, win32, audit, window, directory: Path, prefix="format"):
        self.win32, self.audit, self.window = win32, audit, window
        self.ready = directory / f"{prefix}-popup-ready.json"
        self.captured = directory / f"{prefix}-popup-captured.json"
        self.screenshot = directory / f"{prefix}-popup.png"
        self.error = None
        self.record = None
        assert not any(path.exists() for path in (self.ready, self.captured, self.screenshot)), "Capture paths must be fresh"
        assert window["pid"] in audit.pids(), "Capture window is not owned by this test"
        self._raised = []
        try:
            self._raise(window)
        except Exception as error:
            self._report_error(error)
            raise
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()

    def _raise(self, window):
        hwnd = window["hwnd"]
        if not self._is_owned(window):
            return
        previous = bool(self.win32.user.GetWindowLongW(hwnd, -20) & 8)
        if not previous and self._is_owned(window):
            if not self.win32.user.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013):
                raise RuntimeError("Cannot expose owned native capture window")
            self._raised.append({"hwnd": hwnd, "pid": window["pid"]})

    def _is_owned(self, window):
        pid = wintypes.DWORD()
        return (bool(self.win32.user.GetWindowThreadProcessId(window["hwnd"], ctypes.byref(pid)))
                and pid.value == window["pid"] and pid.value in self.audit.pids())

    def _restore(self):
        errors = []
        for window in reversed(self._raised):
            try:
                if self._is_owned(window):
                    if not self.win32.user.SetWindowPos(window["hwnd"], -2, 0, 0, 0, 0, 0x0013):
                        raise RuntimeError("Cannot restore owned native capture window")
            except Exception as error:
                errors.append(str(error))
        self._raised.clear()
        if errors:
            raise RuntimeError("; ".join(errors))

    def _report_error(self, error):
        self.error = f"{self.error}; {error}" if self.error else str(error)
        self.captured.write_text(json.dumps({"error": self.error}), encoding="utf-8")

    def _monitor(self):
        try:
            while not self.stop.wait(0.05):
                try:
                    state = json.loads(self.ready.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                self.audit.sample()
                assert self.window["pid"] in self.audit.pids(), "Native capture lost its held app identity"
                others = [w for w in self.win32.windows() if w["visible"] and w["pid"] in self.audit.pids()
                          and w["hwnd"] != self.window["hwnd"]]
                assert state.get("nativeOpen") is True or others, "No open native menu was observed"
                for popup in others:
                    self._raise(popup)
                time.sleep(0.15)
                # Compose only our exposed app/menu rather than capturing an
                # unrelated foreground window covering this screen rectangle.
                self.record = {"native_open": state.get("nativeOpen"), "other_owned_windows": others,
                               "image": self.win32._capture(self.window["hwnd"], self.screenshot, desktop=True)}
                self.record["image"]["method"] = "desktop BitBlt; only held app/menu temporarily topmost, no activation"
                menus = [popup for popup in others if popup["class"] in {"Chrome_WidgetWin_0", "Chrome_WidgetWin_1", "#32768"}]
                if len(menus) == 1:
                    menu_image = self.win32._capture(menus[0]["hwnd"], self.screenshot.with_name(self.screenshot.stem + "-menu.png"), desktop=True)
                    menu_image["method"] = "desktop BitBlt of held native popup bounds"
                    menu_image["includes_native_title_bar"] = False
                    self.record["menu_image"] = menu_image
                break
        except Exception as error:
            self._report_error(error)
        finally:
            try:
                self._restore()
            except Exception as error:
                self._report_error(error)
        if self.record is not None and not self.error:
            self.captured.write_text(json.dumps(self.record), encoding="utf-8")

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive() and not self.error, self.error
        assert self.record is not None, "Native dropdown was not captured"
        return self.record
