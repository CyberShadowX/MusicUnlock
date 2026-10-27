"""Real Git histories protect the user's checkout during source preparation."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading

import pytest


def git(root, *args):
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=NUL", "-c", "commit.gpgSign=false", "-C", str(root), *args],
        text=True, encoding="utf-8", capture_output=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stdout.strip()


def commit(root, message):
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path):
    remote = tmp_path / "upstream"
    remote.mkdir()
    git(remote, "init", "-b", "main")
    (remote / ".gitignore").write_text(".musicunlock-updates/\n.cache/\n.venv/\nbuild/\ndist/\n", encoding="utf-8")
    (remote / "web" / "static").mkdir(parents=True)
    (remote / "web" / "static" / "index.html").write_text(
        "<title>music-geshizhuanhuan</title><h1>music-geshizhuanhuan</h1>", encoding="utf-8"
    )
    (remote / "shared.txt").write_text("baseline\n", encoding="utf-8")
    baseline = commit(remote, "upstream baseline")
    workspace = tmp_path / "workspace 中文"
    git(tmp_path, "clone", str(remote), str(workspace))
    git(workspace, "checkout", "-b", "desktop")
    (workspace / "desktop.py").write_text("LOCAL_DESKTOP = True\n", encoding="utf-8")
    custom = commit(workspace, "local desktop support")
    return {"remote": remote, "workspace": workspace, "baseline": baseline, "custom": custom}


def validate_branding(root):
    text = (root / "web" / "static" / "index.html").read_text(encoding="utf-8")
    if "<title>MusicUnlock</title>" not in text or "<h1>MusicUnlock</h1>" not in text:
        raise RuntimeError("branding invalid")


def local_source(history):
    from music_unlock.update_source import GitSource

    class LocalSource(GitSource):
        def check(self):
            revision = git(history["remote"], "rev-parse", "main")
            return {"revision": revision, "summary": git(history["remote"], "log", "-1", "--format=%s")}

    return LocalSource(expected_origin=str(history["remote"]))


def prepare(history, source=None):
    source = source or local_source(history)
    workspace = history["workspace"]
    expected = source.inspect(workspace)
    return source.prepare(workspace, expected, source.check()["revision"], "job-fixture", threading.Event())


def test_source_merge_preserves_desktop_and_rebrands_without_moving_user_head(history):
    (history["remote"] / "new-upstream.txt").write_text("new functionality", encoding="utf-8")
    latest = commit(history["remote"], "new source commit")
    candidate = prepare(history)
    worktree = Path(candidate["worktree"])
    assert (worktree / "new-upstream.txt").read_text(encoding="utf-8") == "new functionality"
    assert (worktree / "desktop.py").read_text(encoding="utf-8") == "LOCAL_DESKTOP = True\n"
    validate_branding(worktree)
    assert git(history["workspace"], "rev-parse", "HEAD") == history["custom"]
    assert git(history["workspace"], "status", "--porcelain") == ""
    git(worktree, "merge-base", "--is-ancestor", latest, "HEAD")
    git(worktree, "merge-base", "--is-ancestor", history["custom"], "HEAD")
    assert candidate["upstream_revision"] == latest


def test_dirty_workspace_blocks_before_preparation_without_deleting_user_file(history):
    from music_unlock.update_source import UpdateError
    user_file = history["workspace"] / "personal music.txt"
    user_file.write_text("keep me", encoding="utf-8")
    with pytest.raises(UpdateError, match="未提交|clean|dirty"):
        prepare(history)
    assert user_file.read_text(encoding="utf-8") == "keep me"
    assert git(history["workspace"], "rev-parse", "HEAD") == history["custom"]


def test_concurrent_branch_or_commit_change_is_rejected(history):
    from music_unlock.update_source import UpdateError
    source = local_source(history)
    expected = source.inspect(history["workspace"])
    git(history["workspace"], "checkout", "-b", "another")
    with pytest.raises(UpdateError, match="改变|changed"):
        source.prepare(history["workspace"], expected, history["baseline"], "job-changed", threading.Event())


def test_conflicting_merge_keeps_original_files_and_head(history):
    from music_unlock.update_source import UpdateError
    (history["workspace"] / "shared.txt").write_text("my desktop behavior\n", encoding="utf-8")
    custom = commit(history["workspace"], "local shared change")
    (history["remote"] / "shared.txt").write_text("upstream incompatible behavior\n", encoding="utf-8")
    commit(history["remote"], "upstream conflict")
    with pytest.raises(UpdateError, match="冲突|merge|CONFLICT"):
        prepare(history)
    assert git(history["workspace"], "rev-parse", "HEAD") == custom
    assert (history["workspace"] / "shared.txt").read_text(encoding="utf-8") == "my desktop behavior\n"
    assert git(history["workspace"], "status", "--porcelain") == ""


def test_production_source_rejects_untrusted_local_origin(history):
    from music_unlock.update_source import GitSource, UpdateError
    with pytest.raises(UpdateError, match="来源|origin"):
        GitSource().inspect(history["workspace"])


def test_inherited_git_environment_cannot_redirect_the_checked_repository(history, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(history["remote"] / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(history["remote"]))
    inspected = local_source(history).inspect(history["workspace"])
    assert inspected["head"] == history["custom"]
    assert inspected["branch"] == "desktop"


def test_fetch_is_pinned_to_checked_commit_when_remote_advances(history):
    source = local_source(history)
    expected = source.inspect(history["workspace"])
    checked = source.check()["revision"]
    (history["remote"] / "too-new.txt").write_text("not checked", encoding="utf-8")
    commit(history["remote"], "advance after check")
    candidate = source.prepare(history["workspace"], expected, checked, "job-pinned", threading.Event())
    assert candidate["upstream_revision"] == checked
    assert not (Path(candidate["worktree"]) / "too-new.txt").exists()


def test_github_check_validates_commit_response_without_using_releases(monkeypatch):
    from music_unlock.update_source import GitSource, UpdateError
    requests = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_): return json.dumps({"sha": "a" * 40, "commit": {"message": "source change\nbody"}}).encode()

    def opened(request, **kwargs):
        requests.append(request.full_url)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", opened)
    assert GitSource().check() == {"revision": "a" * 40, "summary": "source change"}
    assert requests == ["https://api.github.com/repos/HRuiCcc/music-geshizhuanhuan/commits/main"]
    monkeypatch.setattr(Response, "read", lambda *_: b'{"sha":"not-a-sha"}')
    with pytest.raises(UpdateError, match="commit|提交|响应"):
        GitSource().check()
