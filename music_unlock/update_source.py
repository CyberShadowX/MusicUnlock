"""Read upstream source and merge it in an isolated, ignored Git worktree.

Only the installer may advance the original checkout. Fetch writes objects to
the shared Git database, but never changes the user's branch or FETCH_HEAD.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import threading
import urllib.request

from .app_info import UPSTREAM_BRANCH, UPSTREAM_REPOSITORY, UPSTREAM_URL
from .branding import apply_branding, validate_branding


class UpdateError(RuntimeError):
    """An update failed safely; the current application remains available."""


def revision(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise UpdateError("上游提交响应无效，必须是完整的 Git commit SHA。")
    return value.lower()


def git_environment() -> dict:
    # No credential helper, interactive authentication or inherited global
    # signing configuration is needed for this public, read-only source.
    return {
        **{key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")},
        "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never",
        "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GIT_OPTIONAL_LOCKS": "0",
    }


def run_owned(command, *, cwd, env, log_path, cancel, timeout):
    """Wait cooperatively while a native job owns the entire launched tree."""
    from .update_process import ManagedProcess
    import time

    if cancel.is_set():
        raise UpdateError("更新已取消。")
    process = ManagedProcess.launch(command, cwd=str(cwd), env=env, log_path=str(log_path))
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancel.wait(0.1):
                raise UpdateError("更新已取消。")
            if time.monotonic() >= deadline:
                raise UpdateError("更新命令超时，请查看构建日志。")
        code = process.wait(timeout=10)
        if code:
            raise UpdateError(f"更新命令失败（退出码 {code}），请查看日志：{log_path}")
    finally:
        # close() kills only the pre-resume assigned child job, including
        # remaining descendants even if its PowerShell/Git parent exited.
        process.close()


class GitSource:
    def __init__(self, *, expected_origin: str = UPSTREAM_URL):
        # Injection is for a trusted controller/test harness, never a URL from
        # JavaScript. The desktop bridge has no origin/SHA/command argument.
        self.expected_origin = expected_origin

    def check(self) -> dict:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{UPSTREAM_REPOSITORY}/commits/{UPSTREAM_BRANCH}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "MusicUnlock-source-updater"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read(1024 * 1024))
            sha = revision(payload.get("sha"))
            message = payload.get("commit", {}).get("message", "")
            if not isinstance(message, str):
                raise ValueError("invalid commit message")
            return {"revision": sha, "summary": message.splitlines()[0][:500] if message else ""}
        except (OSError, ValueError, AttributeError) as exc:
            raise UpdateError(f"GitHub 源码检查失败：{exc}") from exc

    def _command(self, root: Path, *args) -> list[str]:
        return [
            "git", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.fsmonitor=false",
            "-c", "credential.helper=", "-c", "core.askPass=",
            "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false",
            "-c", "user.name=MusicUnlock Updater", "-c", "user.email=updater@localhost",
            "-C", str(root), *args,
        ]

    def _git(self, root: Path, *args, check=True) -> str:
        from .update_process import clean_dll_directory
        try:
            # Frozen Python modifies the DLL search path for its own extensions.
            # Clear it only while spawning an external Git executable.
            with clean_dll_directory():
                process = subprocess.Popen(
                    self._command(root, *args), env=git_environment(), stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()  # held Popen handle, never an unverified PID
                process.communicate()
                raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateError(f"Git 本地命令失败：{exc}") from exc
        if check and process.returncode:
            raise UpdateError(f"Git {args[0]} 失败：{(stderr or stdout).strip()[-3000:]}")
        return stdout.strip()

    def inspect(self, workspace: Path, *, require_clean=True) -> dict:
        try:
            root = Path(workspace).resolve(strict=True)
        except OSError as exc:
            raise UpdateError(f"源码目录不可用：{workspace}") from exc
        top = self._git(root, "rev-parse", "--show-toplevel")
        if Path(top).resolve() != root:
            raise UpdateError("请选择 Git 仓库根目录作为源码目录。")
        origin = self._git(root, "remote", "get-url", "origin")
        if origin != self.expected_origin:
            raise UpdateError("源码 origin 来源不匹配，必须使用原始上游仓库。")
        branch = self._git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        head = revision(self._git(root, "rev-parse", "HEAD"))
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
            marker_path = Path(self._git(root, "rev-parse", "--git-path", marker))
            if not marker_path.is_absolute():
                marker_path = root / marker_path
            if marker_path.exists():
                raise UpdateError("源码正在合并或变基，请先完成现有 Git 操作。")
        dirty = bool(self._git(root, "status", "--porcelain", "--untracked-files=normal"))
        if require_clean and dirty:
            raise UpdateError("源码有未提交修改（dirty），请先自行提交或保存修改后重试。")
        return {"workspace": str(root), "head": head, "branch": branch, "origin": origin, "dirty": dirty}

    def assert_unchanged(self, workspace: Path, expected: dict, *, prepared_head=None) -> dict:
        actual = self.inspect(workspace)
        heads = {expected["head"]}
        if prepared_head:
            heads.add(prepared_head)
        if (actual["head"] not in heads or actual["branch"] != expected["branch"]
                or actual["workspace"] != expected["workspace"] or actual["origin"] != expected["origin"]):
            raise UpdateError("源码分支或提交已改变（changed），请重新检查更新。")
        return actual

    def prepare(self, workspace: Path, expected: dict, checked_revision: str, job_id: str,
                cancel: threading.Event) -> dict:
        sha = revision(checked_revision)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,79}", job_id):
            raise UpdateError("更新任务标识无效。")
        self.assert_unchanged(workspace, expected)
        root = Path(expected["workspace"])
        updates = root / ".musicunlock-updates"
        job = updates / job_id
        worktree = job / "source"
        if not worktree.resolve().is_relative_to(root) or updates.is_symlink() or job.exists():
            raise UpdateError("更新工作目录不安全或任务已经存在。")
        if not self._git(root, "check-ignore", "--", str(worktree), check=False):
            raise UpdateError("源码缺少 .musicunlock-updates/ 忽略规则，请更新本地桌面源码。")
        if cancel.is_set():
            raise UpdateError("更新已取消。")
        job.mkdir(parents=True)
        log = job / "build.log"
        log.write_text("MusicUnlock: prepare isolated source\n", encoding="utf-8")
        self._git(root, "worktree", "add", "--detach", str(worktree), expected["head"])
        run_owned(
            self._command(worktree, "fetch", "--no-tags", "--no-write-fetch-head", "--", self.expected_origin, sha),
            cwd=worktree, env=git_environment(), log_path=log, cancel=cancel, timeout=180,
        )
        if cancel.is_set():
            raise UpdateError("更新已取消。")
        # No refs are taken from a moving branch name after the check.
        self._git(worktree, "cat-file", "-e", sha + "^{commit}")
        self._git(worktree, "merge", "--no-edit", "--no-gpg-sign", sha)
        changed = apply_branding(worktree)
        validate_branding(worktree)
        if changed:
            for name in changed:
                path = (worktree / name).resolve(strict=True)
                if not path.is_relative_to(worktree):
                    raise UpdateError("品牌补丁路径超出候选源码目录。")
                self._git(worktree, "ls-files", "--error-unmatch", "--", name)
            self._git(worktree, "add", "--", *changed)
            self._git(worktree, "commit", "--no-gpg-sign", "-m", "Preserve MusicUnlock desktop branding")
        prepared = revision(self._git(worktree, "rev-parse", "HEAD"))
        self._git(worktree, "merge-base", "--is-ancestor", expected["head"], prepared)
        self._git(worktree, "merge-base", "--is-ancestor", sha, prepared)
        self.assert_unchanged(root, expected)
        if self._git(worktree, "status", "--porcelain"):
            raise UpdateError("候选源码仍有未提交修改，已停止更新。")
        return {"job_id": job_id, "workspace": str(root), "worktree": str(worktree),
                "expected_head": expected["head"], "expected_branch": expected["branch"],
                "prepared_head": prepared, "upstream_revision": sha, "log_path": str(log)}

    def validate_candidate(self, candidate: dict, *, allow_prepared=True) -> dict:
        """Revalidate persisted ready candidates, including failed-install retry."""
        root = Path(candidate["workspace"]).resolve(strict=True)
        job_id = candidate["job_id"]
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,79}", job_id):
            raise UpdateError("候选任务标识无效。")
        worktree = (root / ".musicunlock-updates" / job_id / "source").resolve(strict=True)
        if not worktree.is_relative_to(root) or Path(candidate["worktree"]).resolve(strict=True) != worktree:
            raise UpdateError("候选源码路径无效。")
        expected = {"workspace": str(root), "head": revision(candidate["expected_head"]),
                    "branch": candidate["expected_branch"], "origin": self.expected_origin}
        prepared = revision(candidate["prepared_head"])
        self.assert_unchanged(root, expected, prepared_head=prepared if allow_prepared else None)
        registered = self._git(root, "worktree", "list", "--porcelain", "-z").split("\0")
        roots = {Path(item[9:]).resolve() for item in registered if item.startswith("worktree ")}
        if worktree not in roots or self._git(worktree, "rev-parse", "HEAD") != prepared:
            raise UpdateError("候选源码已改变或不再是登记的 Git worktree。")
        if self._git(worktree, "status", "--porcelain"):
            raise UpdateError("候选源码有未提交修改。")
        self._git(worktree, "merge-base", "--is-ancestor", expected["head"], prepared)
        self._git(worktree, "merge-base", "--is-ancestor", revision(candidate["upstream_revision"]), prepared)
        artifact = Path(candidate["artifact"]).resolve(strict=True)
        if artifact != worktree / "dist" / "MusicUnlock.exe":
            raise UpdateError("候选可执行文件路径无效。")
        validate_branding(worktree)
        return candidate
