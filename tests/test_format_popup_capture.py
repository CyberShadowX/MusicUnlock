"""Native popup ownership and cleanup checks without touching real windows."""
import json
from types import SimpleNamespace

import pytest

from scripts import format_popup_capture as capture_module


class FakeUser:
    def __init__(self):
        self.owners = {10: 41}
        self.topmost = set()
        self.changes = []
        self.fail_position = None

    def IsWindow(self, hwnd):
        return hwnd in self.owners

    def GetWindowThreadProcessId(self, hwnd, pid):
        pid._obj.value = self.owners.get(hwnd, 0)
        return 1 if hwnd in self.owners else 0

    def GetWindowLongW(self, hwnd, _index):
        return 8 if hwnd in self.topmost else 0

    def SetWindowPos(self, hwnd, position, *_args):
        self.changes.append((hwnd, position))
        if position == self.fail_position:
            return False
        if position == -1:
            self.topmost.add(hwnd)
        else:
            self.topmost.discard(hwnd)
        return True


class FakeAudit:
    def __init__(self):
        self.active = {41}

    def pids(self):
        return set(self.active)

    def sample(self):
        pass


class DeferredThread:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def join(self, **_kwargs):
        pass

    def is_alive(self):
        return False


@pytest.fixture
def capture(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(capture_module.time, "sleep", lambda _seconds: None)
    user, audit = FakeUser(), FakeAudit()
    win32 = SimpleNamespace(user=user, windows=lambda: [],
                            _capture=lambda *_args, **_kwargs: {"path": "fixture.png"})
    result = capture_module.FormatPopupCapture(win32, audit, {"hwnd": 10, "pid": 41}, tmp_path)
    user.changes.clear()
    return result


@pytest.mark.parametrize("owner,active", [(99, {41, 99}), (41, set()), (None, {41})])
def test_restore_skips_replaced_destroyed_or_inactive_window(capture, owner, active):
    if owner is None:
        del capture.win32.user.owners[10]
    else:
        capture.win32.user.owners[10] = owner
    capture.audit.active = active

    capture._restore()

    assert capture.win32.user.changes == []


@pytest.mark.parametrize("owner,active", [(99, {41, 99}), (41, set()), (None, {41})])
def test_raise_skips_stale_window_snapshot(capture, owner, active):
    if owner is not None:
        capture.win32.user.owners[20] = owner
    capture.audit.active = active

    capture._raise({"hwnd": 20, "pid": 41})

    assert capture.win32.user.changes == []


def test_restore_returns_owned_window_to_previous_non_topmost_state(capture):
    capture._restore()

    assert capture.win32.user.changes == [(10, -2)]
    assert 10 not in capture.win32.user.topmost


def test_restore_failure_is_acknowledged_and_finish_reports_error(capture):
    capture.win32.user.fail_position = -2
    capture.ready.write_text(json.dumps({"nativeOpen": True}), encoding="utf-8")

    capture._monitor()

    acknowledgement = json.loads(capture.captured.read_text(encoding="utf-8"))
    assert "restore" in acknowledgement["error"].lower()
    with pytest.raises(AssertionError, match="restore"):
        capture.finish()


def test_popup_raise_failure_is_acknowledged(capture):
    capture.win32.user.owners[20] = 41
    capture.win32.windows = lambda: [{"hwnd": 20, "pid": 41, "visible": True, "class": "#32768"}]
    capture.win32.user.fail_position = -1
    capture.ready.write_text(json.dumps({"nativeOpen": True}), encoding="utf-8")

    capture._monitor()

    assert "error" in json.loads(capture.captured.read_text(encoding="utf-8"))
    assert (10, -2) in capture.win32.user.changes


def test_initial_raise_failure_writes_error_acknowledgement(tmp_path):
    user, audit = FakeUser(), FakeAudit()
    user.fail_position = -1

    with pytest.raises(RuntimeError, match="expose"):
        capture_module.FormatPopupCapture(SimpleNamespace(user=user), audit,
                                          {"hwnd": 10, "pid": 41}, tmp_path)

    acknowledgement = json.loads((tmp_path / "format-popup-captured.json").read_text(encoding="utf-8"))
    assert "expose" in acknowledgement["error"]
