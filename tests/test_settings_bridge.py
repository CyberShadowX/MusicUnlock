from pathlib import Path
import json
import os

import pytest


class Manager:
    def __init__(self):
        self.calls = []
        self.value = {"app_name": "MusicUnlock", "phase": "ready", "message": "候选已构建",
                      "can_restart": True, "can_update": False, "source_workspace": ""}

    def snapshot(self):
        return dict(self.value)

    def check_updates(self):
        self.calls.append("check")
        return self.snapshot()

    def start_update(self):
        self.calls.append("update")
        return self.snapshot()

    def candidate(self):
        return {"job_id": "fake"}


class Window:
    def __init__(self, url):
        self.url = url
        self.destroyed = False

    def get_current_url(self):
        return self.url

    def destroy(self):
        self.destroyed = True


@pytest.mark.parametrize("url", ["https://example.com/", "http://127.0.0.1:9001/",
                                  "http://127.0.0.1:9000/api/health", "file:///index.html"])
def test_native_update_bridge_rejects_foreign_page(tmp_path, url):
    from settings_bridge import SettingsBridge
    manager = Manager()
    bridge = SettingsBridge(manager, tmp_path)
    bridge.bind_window(Window(url), "http://127.0.0.1:9000")
    with pytest.raises(PermissionError):
        bridge.check_updates()
    with pytest.raises(PermissionError):
        bridge.start_update()
    assert manager.calls == []


def test_get_state_is_read_only_and_check_is_explicit(tmp_path):
    from settings_bridge import SettingsBridge
    manager = Manager()
    bridge = SettingsBridge(manager, tmp_path)
    bridge.bind_window(Window("http://127.0.0.1:9000/"), "http://127.0.0.1:9000")
    assert bridge.get_update_state()["app_name"] == "MusicUnlock"
    assert manager.calls == []
    bridge.check_updates()
    assert manager.calls == ["check"]


def test_source_mode_never_replaces_python_interpreter(tmp_path):
    from settings_bridge import SettingsBridge
    manager = Manager()
    window = Window("http://127.0.0.1:9000/")
    bridge = SettingsBridge(manager, tmp_path)
    bridge.bind_window(window, "http://127.0.0.1:9000")
    assert bridge.get_update_state()["can_restart"] is False
    assert bridge.restart_after_update()["phase"] == "failed"
    assert not window.destroyed


class Helper:
    def __init__(self):
        self.released = False
        self.killed = False

    def allow_survival(self):
        self.released = True

    def poll(self):
        return None

    def terminate_tree(self):
        self.killed = True

    def close(self, release=False):
        if release:
            self.released = True


def prepared_install(tmp_path):
    from music_unlock.update_process import process_identity
    return {"command": ["trusted-pythonw"], "result_path": str(tmp_path / "result.json"),
            "handoff_path": str(tmp_path / "handoff.json"), "handoff_token": "fresh-token",
            "parent_created": process_identity(os.getpid())}


def test_restart_closes_only_after_identity_and_token_handoff(tmp_path):
    from settings_bridge import SettingsBridge
    manager, helper, window = Manager(), Helper(), Window("http://127.0.0.1:9000/")
    prepared = prepared_install(tmp_path)

    def launch(command):
        assert not window.destroyed
        Path(prepared["handoff_path"]).write_text(json.dumps({
            "status": "parent-held", "parent_pid": os.getpid(),
            "parent_created": prepared["parent_created"], "handoff_token": prepared["handoff_token"]
        }))
        return helper

    bridge = SettingsBridge(manager, tmp_path, app_executable=tmp_path / "MusicUnlock.exe",
                            prepare=lambda *args: prepared, launch_helper=launch,
                            begin_restart=lambda: True, cancel_restart=lambda: None)
    bridge.bind_window(window, "http://127.0.0.1:9000")
    assert bridge.restart_after_update()["phase"] == "installing"
    assert helper.released and window.destroyed and not helper.killed


def test_invalid_handoff_preserves_window_and_kills_only_owned_helper(tmp_path):
    from settings_bridge import SettingsBridge
    helper, window, cancelled = Helper(), Window("http://127.0.0.1:9000/"), []
    prepared = prepared_install(tmp_path)
    Path(prepared["handoff_path"]).write_text(json.dumps({
        "status": "parent-held", "parent_pid": os.getpid(), "handoff_token": "stale-token",
        "parent_created": prepared["parent_created"]}))
    bridge = SettingsBridge(Manager(), tmp_path, app_executable=tmp_path / "MusicUnlock.exe",
                            prepare=lambda *args: prepared, launch_helper=lambda command: helper,
                            begin_restart=lambda: True, cancel_restart=lambda: cancelled.append(True),
                            handoff_timeout=0.01)
    bridge.bind_window(window, "http://127.0.0.1:9000")
    result = bridge.restart_after_update()
    assert result["phase"] == "ready" and "重试" in result["message"]
    assert not window.destroyed and helper.killed and cancelled and not helper.released


def test_active_conversion_blocks_even_direct_native_restart(tmp_path):
    from settings_bridge import SettingsBridge
    window = Window("http://127.0.0.1:9000/")
    bridge = SettingsBridge(Manager(), tmp_path, app_executable=tmp_path / "MusicUnlock.exe",
                            begin_restart=lambda: False)
    bridge.bind_window(window, "http://127.0.0.1:9000")
    assert "转换" in bridge.restart_after_update()["message"]
    assert not window.destroyed


def test_failed_window_close_terminates_retained_helper_before_retry(tmp_path):
    from settings_bridge import SettingsBridge
    helper, window, cancelled = Helper(), Window("http://127.0.0.1:9000/"), []
    prepared = prepared_install(tmp_path)
    Path(prepared["handoff_path"]).write_text(json.dumps({
        "status": "parent-held", "parent_pid": os.getpid(), "handoff_token": prepared["handoff_token"],
        "parent_created": prepared["parent_created"]}))
    window.destroy = lambda: (_ for _ in ()).throw(RuntimeError("native close failed"))
    bridge = SettingsBridge(Manager(), tmp_path, app_executable=tmp_path / "MusicUnlock.exe",
                            prepare=lambda *args: prepared, launch_helper=lambda command: helper,
                            begin_restart=lambda: True, cancel_restart=lambda: cancelled.append(True))
    bridge.bind_window(window, "http://127.0.0.1:9000")
    assert bridge.restart_after_update()["phase"] == "ready"
    assert helper.killed and cancelled


def test_real_request_gate_is_atomic_and_released_after_conversion(tmp_path, monkeypatch):
    import io
    import threading
    from types import SimpleNamespace
    from web import server
    entered, finish = threading.Event(), threading.Event()
    server.cancel_update_restart()

    def convert(*args):
        entered.set()
        assert finish.wait(5)
        return SimpleNamespace(status="error", output=None, message="fixture")

    monkeypatch.setattr(server, "process_one", convert)

    def request():
        with server.app.test_client() as client:
            client.post("/api/decode", data={"file": (io.BytesIO(b"fixture"), "x.qmc0")})

    worker = threading.Thread(target=request)
    worker.start()
    try:
        assert entered.wait(3)
        assert not server.begin_update_restart()
        finish.set()
        worker.join(5)
        assert not worker.is_alive()
        assert server.begin_update_restart()
        with server.app.test_client() as client:
            assert client.post("/api/decode").status_code == 503
        server.cancel_update_restart()
        with server.app.test_client() as client:
            assert client.post("/api/decode").status_code == 400
    finally:
        finish.set()
        worker.join(5)
        server.cancel_update_restart()
