import io
from pathlib import Path
import threading
import time
import zipfile
import uuid

import pytest

from web import server


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_results", {})
    monkeypatch.setattr(server, "STORE_DIR", tmp_path / "store")
    server.STORE_DIR.mkdir()
    server.cancel_update_restart()
    yield
    server.cancel_update_restart()


def result(tmp_path, name="歌曲.flac", data=b"fLaC-fixture"):
    folder = tmp_path / uuid.uuid4().hex
    folder.mkdir()
    path = folder / name
    path.write_bytes(data)
    return server._store_put({"path": str(path), "output_name": name,
                              "created": time.time(), "container": path.suffix[1:]})


class Window:
    def __init__(self, url="http://127.0.0.1:9000/"):
        self.url = url

    def get_current_url(self):
        return self.url


def bridge(dialog, url="http://127.0.0.1:9000/"):
    from desktop_saves import DesktopSaveBridge
    value = DesktopSaveBridge(dialog=dialog)
    value.bind_window(Window(url), "http://127.0.0.1:9000")
    return value


def test_zip_missing_id_fails_instead_of_silent_partial_archive(registry, tmp_path):
    rid = result(tmp_path)
    with server.app.test_client() as client:
        response = client.get(f"/api/zip?ids={rid},aaaaaaaaaaaa")
        assert response.status_code == 404
        assert response.json["ok"] is False


def test_browser_zip_is_disk_backed_and_34_members_unique(registry, tmp_path, monkeypatch):
    ids = [result(tmp_path, "歌曲.flac", bytes([i]) * 4096) for i in range(34)]
    sent = []
    original = server.send_file

    def send_file(path, **kwargs):
        sent.append(path)
        assert isinstance(path, Path), "archive must be disk-backed, not BytesIO"
        return original(path, **kwargs)

    monkeypatch.setattr(server, "send_file", send_file)
    with server.app.test_client() as client:
        response = client.get(f"/api/zip?ids={','.join(ids)}")
        assert not server.begin_update_restart()
        assert client.post("/api/clear").status_code == 409
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            assert len(archive.namelist()) == len(set(archive.namelist())) == 34
            assert archive.namelist()[:2] == ["歌曲.flac", "歌曲 (2).flac"]
            assert all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())
            assert archive.testzip() is None
        response.close()
        assert not sent[0].exists()


@pytest.mark.parametrize("url", ["https://example.com/", "http://127.0.0.1:9001/",
    "http://127.0.0.1:9000/api/health", "http://127.0.0.1:9000/?x=1", "file:///index.html"])
def test_save_bridge_rejects_other_pages(registry, url):
    value = bridge(lambda *args: pytest.fail("unauthorized dialog"), url)
    with pytest.raises(PermissionError):
        value.save_results(["aaaaaaaaaaaa"])
    with pytest.raises(PermissionError):
        value.get_save_state()


@pytest.mark.parametrize("ids,archive", [(None, False), (["../secret"], False),
    ([], False), (["aaaaaaaaaaaa", "bbbbbbbbbbbb"], False), ([1], True)])
def test_invalid_save_input_has_visible_failure(registry, ids, archive):
    value = bridge(lambda *args: pytest.fail("invalid input opened dialog"))
    assert value.save_results(ids, archive)["phase"] == "failed"


@pytest.mark.parametrize("extension", ["mp3", "flac", "m4a", "wav", "ogg", "aac"])
def test_single_save_preserves_real_container_and_atomic_destination(registry, tmp_path, extension):
    rid = result(tmp_path, f"歌曲.live.{extension}", b"actual-content")
    destination = tmp_path / f"renamed.{extension}"
    destination.write_bytes(b"old")

    def choose(window, filename, suffix):
        assert filename == f"歌曲.live.{extension}" and suffix == extension
        return destination

    value = bridge(choose)
    assert value.save_results([rid])["phase"] == "completed"
    assert destination.read_bytes() == b"actual-content"
    assert value.get_save_state()["completed"] == 1
    assert not list(tmp_path.glob("*.partial"))


def test_cancel_and_wrong_suffix_do_not_write(registry, tmp_path):
    rid = result(tmp_path)
    value = bridge(lambda *args: None)
    assert value.save_results([rid])["phase"] == "cancelled"
    destination = tmp_path / "renamed.wav"
    destination.write_bytes(b"unchanged")
    value = bridge(lambda *args: destination)
    assert value.save_results([rid])["phase"] == "failed"
    assert destination.read_bytes() == b"unchanged"


def test_native_zip_progress_cleanup_and_unique_names(registry, tmp_path):
    ids = [result(tmp_path, "歌曲.flac", bytes([i])) for i in range(34)]
    destination = tmp_path / "MusicUnlock.zip"
    value = bridge(lambda window, name, ext: destination)
    state = value.save_results(ids, True)
    assert state["phase"] == "completed" and state["completed"] == state["total"] == 34
    with zipfile.ZipFile(destination) as archive:
        assert len(set(archive.namelist())) == 34
        assert archive.read("歌曲 (34).flac") == bytes([33])
    assert not list(tmp_path.glob("*.partial"))


def test_write_failure_preserves_existing_destination_and_removes_partial(registry, tmp_path, monkeypatch):
    import desktop_saves
    rid = result(tmp_path)
    destination = tmp_path / "existing.flac"
    destination.write_bytes(b"old")

    def fail(source, target, **kwargs):
        target.write(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr(desktop_saves.shutil, "copyfileobj", fail)
    value = bridge(lambda *args: destination)
    assert value.save_results([rid])["phase"] == "failed"
    assert destination.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.partial"))
    assert server.begin_update_restart()


def test_save_lease_blocks_clear_restart_and_expiry_and_concurrent_save(registry, tmp_path):
    rid = result(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    def choose(*args):
        entered.set()
        assert finish.wait(5)
        return None

    value = bridge(choose)
    thread = threading.Thread(target=lambda: value.save_results([rid]))
    thread.start()
    try:
        assert entered.wait(3)
        assert value.get_save_state()["phase"] == "choosing"
        assert value.save_results([rid])["phase"] == "failed"
        assert value.get_save_state()["phase"] == "choosing"
        assert not server.begin_update_restart()
        with server.app.test_client() as client:
            assert client.post("/api/clear").status_code == 409
        server._cleanup_expired(ttl_sec=-1)
        assert Path(server._store_get(rid)["path"]).exists()
    finally:
        finish.set()
        thread.join(5)
    assert server.begin_update_restart()
    assert value.save_results([rid])["phase"] == "failed"
    server.cancel_update_restart()
    with server.app.test_client() as client:
        assert client.post("/api/clear").status_code == 200
    assert server._store_get(rid) is None


def test_browser_download_holds_lease_until_response_closes(registry, tmp_path):
    rid = result(tmp_path)
    with server.app.test_client() as client:
        response = client.get(f"/api/download/{rid}", buffered=False)
        assert response.status_code == 200
        assert client.post("/api/clear").status_code == 409
        assert not server.begin_update_restart()
        assert response.data == b"fLaC-fixture"
        response.close()
        assert client.post("/api/clear").status_code == 200


def test_zip_write_and_cleanup_failure_still_releases_lifecycle_gate(registry, tmp_path, monkeypatch):
    rid = result(tmp_path)
    leftovers = []
    original_unlink = Path.unlink

    def fail_write(destination, entries, progress=None):
        leftovers.append(destination)
        raise OSError("disk full")

    def deny_cleanup(path, *args, **kwargs):
        if path in leftovers:
            raise PermissionError("temporary file scanner lock")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(server, "write_archive", fail_write)
    monkeypatch.setattr(Path, "unlink", deny_cleanup)
    try:
        with server.app.test_client() as client:
            assert client.get(f"/api/zip?ids={rid}").status_code == 500
        assert server.begin_update_restart(), "failed cleanup must not retain an export lease"
    finally:
        for leftover in leftovers:
            original_unlink(leftover, missing_ok=True)


def test_missing_file_and_duplicate_ids_never_open_dialog(registry, tmp_path):
    rid = result(tmp_path)
    value = bridge(lambda *args: pytest.fail("invalid result opened dialog"))
    assert value.save_results([rid, rid], True)["phase"] == "failed"
    Path(server._store_get(rid)["path"]).unlink()
    assert value.save_results([rid])["phase"] == "failed"
    assert server.begin_update_restart()


def test_archive_memory_is_bounded_by_streaming_buffers(registry, tmp_path):
    import tracemalloc
    ids = [result(tmp_path, f"audio-{i}.flac", b"fLaC" * (1024 * 1024)) for i in range(8)]
    destination = tmp_path / "MusicUnlock.zip"
    value = bridge(lambda *args: destination)
    tracemalloc.start()
    try:
        assert value.save_results(ids, True)["phase"] == "completed"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert destination.stat().st_size > 32 * 1024 * 1024
    assert peak < 8 * 1024 * 1024


def test_native_adapter_sets_default_extension_filter_and_parent_on_ui_thread(monkeypatch):
    import sys
    from types import SimpleNamespace
    from desktop_saves import WinFormsSaveDialog
    active, owner_thread, records = [], [], []

    class Owner:
        InvokeRequired = True

        def Invoke(self, callback):
            owner_thread.append(True)
            callback()
            owner_thread.pop()

    owner = Owner()

    class Dialog:
        def __init__(self):
            assert owner_thread
            active.append(self)

        def ShowDialog(self, parent):
            assert parent is owner
            assert self.FileName == "歌曲.live.flac"
            assert self.Filter == "FLAC 文件 (*.flac)|*.flac"
            assert self.DefaultExt == "flac"
            assert self.AddExtension and self.OverwritePrompt
            assert self.CheckPathExists and self.ValidateNames
            self.FileName = r"F:\fixture\renamed.flac"
            records.append("shown")
            return "OK"

        def Dispose(self):
            records.append("disposed")

    monkeypatch.setitem(sys.modules, "System", SimpleNamespace(Action=lambda callback: callback))
    monkeypatch.setitem(sys.modules, "webview.platforms.winforms", SimpleNamespace(
        WinForms=SimpleNamespace(SaveFileDialog=Dialog, DialogResult=SimpleNamespace(OK="OK"))))
    selected = WinFormsSaveDialog()(SimpleNamespace(native=owner), "歌曲.live.flac", "flac")
    assert selected == Path(r"F:\fixture\renamed.flac")
    assert records == ["shown", "disposed"]
