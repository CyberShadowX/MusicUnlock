"""Updater state is asynchronous and an executable is never offered unchecked."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import threading
import time

import pytest

from test_update_source import commit, git, history, local_source


def settled(manager):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = manager.snapshot()
        if state["phase"] not in {"checking", "preparing", "validating", "building"}:
            return state
        time.sleep(0.01)
    pytest.fail(f"Update did not settle: {manager.snapshot()}")


def windowed_pe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 128 + 24, 0x20b)
    struct.pack_into("<H", data, 128 + 24 + 68, 2)
    path.write_bytes(data)
    return path


def built(worktree, workspace, revision, log_path, cancel):
    artifact = windowed_pe(worktree / "dist" / "MusicUnlock.exe")
    metadata = {"app_name": "MusicUnlock", "source_revision": git(worktree, "rev-parse", "HEAD"),
                "upstream_revision": revision, "source_workspace": str(workspace), "source_dirty": False}
    (artifact.parent / "build-info.json").write_text(json.dumps(metadata), encoding="utf-8")
    return artifact


def manager_for(history, tmp_path, **kwargs):
    from music_unlock.update_manager import UpdateManager
    return UpdateManager(
        history["workspace"], tmp_path / "data", {"upstream_revision": history["baseline"], "source_revision": history["custom"]},
        source=kwargs.pop("source", local_source(history)), build_runner=kwargs.pop("build_runner", built),
        artifact_verifier=kwargs.pop("artifact_verifier", lambda *args: None), **kwargs,
    )


def test_no_network_at_construction_and_same_upstream_is_not_update(history, tmp_path):
    source = local_source(history)
    source.check = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
    manager = manager_for(history, tmp_path, source=source)
    assert manager.snapshot()["phase"] == "idle"
    source.check = lambda: {"revision": history["baseline"], "summary": "same source"}
    manager.check_updates()
    state = settled(manager)
    assert state["phase"] == "up_to_date"
    assert state["current_revision"] == history["baseline"]
    assert state["can_update"] is False
    manager.close()


def test_corrupt_saved_candidate_reports_failure_without_breaking_startup(history, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "update-candidate.json").write_text('{"workspace": ["broken metadata"]}', encoding="utf-8")
    manager = manager_for(history, tmp_path)
    assert manager.snapshot()["phase"] == "failed"
    assert manager.candidate() is None
    manager.close()


def test_check_returns_immediately_and_network_failure_is_never_current(history, tmp_path):
    entered, release = threading.Event(), threading.Event()
    source = local_source(history)

    def network():
        entered.set()
        release.wait(5)
        raise RuntimeError("GitHub rate limit 403")

    source.check = network
    manager = manager_for(history, tmp_path, source=source)
    assert manager.check_updates()["phase"] == "checking"
    assert entered.wait(2)
    assert manager.snapshot()["phase"] == "checking"
    release.set()
    state = settled(manager)
    assert state["phase"] == "failed"
    assert "403" in state["message"]
    assert not state["can_update"]
    manager.close()


def test_candidate_ready_only_after_build_and_validation_with_original_untouched(history, tmp_path):
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    latest = commit(history["remote"], "update available")
    manager = manager_for(history, tmp_path)
    manager.check_updates()
    assert settled(manager)["phase"] == "available"
    manager.start_update()
    state = settled(manager)
    assert state["phase"] == "ready", state
    assert state["build_ready"] and state["can_restart"]
    candidate = manager.candidate()
    assert candidate["expected_head"] == history["custom"]
    assert candidate["expected_branch"] == "desktop"
    assert candidate["upstream_revision"] == latest
    assert Path(candidate["workspace"]) == history["workspace"].resolve()
    assert candidate["artifact_sha256"] == hashlib.sha256(Path(candidate["artifact"]).read_bytes()).hexdigest()
    assert git(history["workspace"], "rev-parse", "HEAD") == history["custom"]
    assert git(history["workspace"], "status", "--porcelain") == ""
    manager.close()


@pytest.mark.parametrize("failure", ["build", "smoke", "console"])
def test_build_smoke_or_pe_failure_keeps_current_executable(history, tmp_path, failure):
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")
    current_exe = tmp_path / "MusicUnlock.exe"
    current_exe.write_bytes(b"current working executable")

    def build(*args):
        if failure == "build": raise RuntimeError("build failed")
        artifact = built(*args)
        if failure == "console":
            content = bytearray(artifact.read_bytes())
            struct.pack_into("<H", content, 128 + 24 + 68, 3)
            artifact.write_bytes(content)
        return artifact

    def verify(*args):
        if failure == "smoke": raise RuntimeError("API smoke failed")

    manager = manager_for(history, tmp_path, build_runner=build, artifact_verifier=verify)
    manager.check_updates()
    settled(manager)
    manager.start_update()
    state = settled(manager)
    assert state["phase"] == "failed", state
    assert not state["build_ready"] and not state["can_restart"]
    assert manager.candidate() is None
    assert current_exe.read_bytes() == b"current working executable"
    assert git(history["workspace"], "rev-parse", "HEAD") == history["custom"]
    manager.close()


def test_edit_after_check_blocks_and_edit_during_build_blocks_ready(history, tmp_path):
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")

    def edit_during_build(*args):
        (history["workspace"] / "my notes.txt").write_text("do not overwrite", encoding="utf-8")
        return built(*args)

    manager = manager_for(history, tmp_path, build_runner=edit_during_build)
    manager.check_updates()
    settled(manager)
    manager.start_update()
    assert settled(manager)["phase"] == "failed"
    assert (history["workspace"] / "my notes.txt").read_text(encoding="utf-8") == "do not overwrite"
    assert manager.candidate() is None
    manager.close()


def test_edit_after_check_is_rejected_before_builder_runs(history, tmp_path):
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")

    def forbidden_build(*args):
        pytest.fail("a dirty source must fail before building")

    manager = manager_for(history, tmp_path, build_runner=forbidden_build)
    manager.check_updates()
    settled(manager)
    (history["workspace"] / "desktop.py").write_text("my concurrent edits", encoding="utf-8")
    manager.start_update()
    assert settled(manager)["phase"] == "failed"
    assert (history["workspace"] / "desktop.py").read_text(encoding="utf-8") == "my concurrent edits"
    manager.close()


def test_candidate_with_wrong_built_revision_is_never_ready(history, tmp_path):
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")

    def stale_build(*args):
        artifact = built(*args)
        metadata_path = artifact.parent / "build-info.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["upstream_revision"] = history["baseline"]
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return artifact

    manager = manager_for(history, tmp_path, build_runner=stale_build)
    manager.check_updates()
    settled(manager)
    manager.start_update()
    assert settled(manager)["phase"] == "failed"
    assert manager.candidate() is None
    manager.close()


def test_ready_candidate_survives_restart_and_prepared_source_retry(history, tmp_path):
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")
    manager = manager_for(history, tmp_path)
    manager.check_updates()
    settled(manager)
    manager.start_update()
    assert settled(manager)["phase"] == "ready"
    candidate = manager.candidate()
    manager.close()
    git(history["workspace"], "merge", "--ff-only", candidate["prepared_head"])
    restarted = manager_for(history, tmp_path)
    assert restarted.snapshot()["phase"] == "ready"
    assert restarted.candidate()["prepared_head"] == candidate["prepared_head"]
    restarted.close()


def test_successful_install_result_is_recognized_by_the_new_running_build(history, tmp_path):
    from music_unlock.update_manager import UpdateManager
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")
    manager = manager_for(history, tmp_path)
    manager.check_updates()
    settled(manager)
    manager.start_update()
    assert settled(manager)["phase"] == "ready"
    candidate = manager.candidate()
    manager.close()
    git(history["workspace"], "merge", "--ff-only", candidate["prepared_head"])
    result = tmp_path / "data" / "updates" / "install" / candidate["job_id"] / "result.json"
    result.parent.mkdir(parents=True)
    result.write_text(json.dumps({"status": "installed", "source_advanced": True}), encoding="utf-8")
    restarted = UpdateManager(history["workspace"], tmp_path / "data",
                              {"source_revision": candidate["prepared_head"], "upstream_revision": candidate["upstream_revision"]},
                              source=local_source(history))
    assert restarted.snapshot()["phase"] == "up_to_date"
    assert restarted.candidate() is None
    restarted.close()


def test_new_candidate_launch_does_not_offer_itself_again_before_installer_writes_result(history, tmp_path):
    from music_unlock.update_manager import UpdateManager
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")
    manager = manager_for(history, tmp_path)
    manager.check_updates()
    settled(manager)
    manager.start_update()
    assert settled(manager)["phase"] == "ready"
    candidate = manager.candidate()
    manager.close()
    git(history["workspace"], "merge", "--ff-only", candidate["prepared_head"])
    restarted = UpdateManager(history["workspace"], tmp_path / "data",
                              {"source_revision": candidate["prepared_head"], "upstream_revision": candidate["upstream_revision"]},
                              source=local_source(history))
    assert restarted.snapshot()["phase"] == "up_to_date"
    assert restarted.candidate() is None
    # Installer has not yet accepted native startup. Retain the persisted
    # candidate so a rollback to the old build can still retry activation.
    assert (tmp_path / "data" / "update-candidate.json").exists()
    restarted.close()


def test_changed_ready_artifact_is_rejected_before_install(history, tmp_path):
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")
    manager = manager_for(history, tmp_path)
    manager.check_updates()
    settled(manager)
    manager.start_update()
    assert settled(manager)["phase"] == "ready"
    artifact = Path(manager.candidate()["artifact"])
    artifact.write_bytes(artifact.read_bytes() + b"tampered")
    assert manager.candidate() is None
    assert manager.snapshot()["can_restart"] is False
    manager.close()


def test_close_cancels_active_job_and_cannot_publish_late_ready(history, tmp_path):
    (history["remote"] / "new.txt").write_text("upstream", encoding="utf-8")
    commit(history["remote"], "update available")
    building = threading.Event()

    def slow_build(worktree, workspace, revision, log_path, cancel):
        building.set()
        assert cancel.wait(5)
        return windowed_pe(worktree / "dist" / "MusicUnlock.exe")

    manager = manager_for(history, tmp_path, build_runner=slow_build)
    manager.check_updates()
    settled(manager)
    manager.start_update()
    assert building.wait(5)
    manager.close()
    assert manager.candidate() is None
    assert not manager.snapshot()["can_restart"]
