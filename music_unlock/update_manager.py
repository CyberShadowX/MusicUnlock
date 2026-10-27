"""Asynchronous desktop source checks and validated build candidates."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import threading
import time
import urllib.request
import uuid
import wave

from .app_info import APP_NAME
from .branding import validate_branding
from .update_source import GitSource, UpdateError, git_environment, revision, run_owned


ACTIVE_PHASES = {"checking", "preparing", "validating", "building", "installing"}


def artifact_sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def verify_windowed_pe(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError("missing MZ")
            stream.seek(0x3c)
            offset = struct.unpack("<I", stream.read(4))[0]
            stream.seek(offset)
            if stream.read(4) != b"PE\0\0":
                raise ValueError("missing PE")
            stream.seek(offset + 24)
            if struct.unpack("<H", stream.read(2))[0] not in {0x10b, 0x20b}:
                raise ValueError("invalid optional header")
            stream.seek(offset + 24 + 68)
            if struct.unpack("<H", stream.read(2))[0] != 2:
                raise ValueError("requires windowed subsystem 2")
    except (OSError, ValueError, struct.error) as exc:
        raise UpdateError(f"候选 EXE 未通过 Windows 窗口模式校验：{exc}") from exc


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def build_candidate(worktree: Path, workspace: Path, upstream_revision: str,
                    log_path: Path, cancel: threading.Event) -> Path:
    """The build owns its venv/caches; FFmpeg is read from original workspace."""
    if os.name != "nt":
        raise UpdateError("软件更新构建需要 Windows、Git 和本地 Python 3.12。")
    powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        raise UpdateError("未找到 Windows PowerShell，无法构建更新。")
    script = worktree / "build_windows.ps1"
    if not script.is_file():
        raise UpdateError("候选源码缺少 build_windows.ps1，停止构建。")
    ffmpeg = workspace / "vendor" / "ffmpeg" / "ffmpeg.exe"
    license_path = workspace / "vendor" / "ffmpeg" / "LICENSE"
    if not ffmpeg.is_file():
        found = shutil.which("ffmpeg.exe")
        if not found:
            raise UpdateError("原始源码目录未暂存 FFmpeg，且 PATH 中没有 FFmpeg。")
        ffmpeg = Path(found).resolve(strict=True)
        license_path = ffmpeg.parent.parent / "LICENSE"
    if not license_path.is_file():
        raise UpdateError("未找到 FFmpeg 许可证，请补全原始源码 vendor/ffmpeg。")
    temporary = worktree / ".cache" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env = git_environment()
    # Let the selected Windows PowerShell discover its own edition's modules.
    # A parent PowerShell 7 module list can hide Windows PowerShell cmdlets.
    for name in list(env):
        if name.upper() == "PSMODULEPATH":
            env.pop(name)
    env.update(TEMP=str(temporary), TMP=str(temporary), PIP_CACHE_DIR=str(worktree / ".cache" / "pip"))
    command = [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
               "-UpstreamRevision", upstream_revision, "-WorkspaceRoot", str(workspace),
               "-FFmpegPath", str(ffmpeg.resolve()), "-FFmpegLicensePath", str(license_path.resolve())]
    run_owned(command, cwd=worktree, env=env, log_path=log_path, cancel=cancel, timeout=7200)
    return worktree / "dist" / "MusicUnlock.exe"


def verify_candidate_api(artifact: Path, worktree: Path, log_path: Path, cancel: threading.Event) -> None:
    """Run the packaged API using only a generated WAV/QMC fixture, no GUI."""
    from .ciphers import qmc1_transform
    from .formats.qmc import V1_STATIC_KEY
    from .update_process import ManagedProcess

    validate_branding(worktree)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    temporary = worktree / ".cache" / "update-smoke"
    temporary.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("_PYI", "PYTHON"))}
    env.update(TEMP=str(temporary), TMP=str(temporary), PYINSTALLER_RESET_ENVIRONMENT="1")
    process = ManagedProcess.launch(
        [str(artifact), "--no-browser", "--port", str(port)], cwd=str(temporary), env=env,
        log_path=str(log_path.with_name("api-smoke.log")),
    )
    process_lock = threading.Lock()
    finished = threading.Event()

    def cancel_owned_smoke():
        while not finished.wait(0.1):
            if cancel.is_set():
                with process_lock:
                    if not finished.is_set():
                        process.terminate_tree()
                return

    monitor = threading.Thread(target=cancel_owned_smoke, daemon=True, name="MusicUnlock-smoke-cancel")
    monitor.start()
    # Loopback verification never follows a proxy configured for public HTTP.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(path):
        if cancel.is_set():
            raise UpdateError("更新已取消。")
        with opener.open(base + path, timeout=10) as response:
            return response.read()

    try:
        deadline = time.monotonic() + 120
        while True:
            if cancel.is_set():
                raise UpdateError("更新已取消。")
            with process_lock:
                if process.poll() is not None:
                    raise UpdateError("候选 EXE 在 API 冒烟验证中提前退出。")
            try:
                health = json.loads(read("/api/health"))
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise UpdateError("候选 EXE 的本地 API 启动超时。")
                cancel.wait(0.2)
                continue
            if health.get("ok") is not True:
                raise UpdateError("候选 EXE 的健康检查未通过。")
            break
        html = read("/").decode("utf-8-sig")
        if APP_NAME not in html or "<title>MusicUnlock" not in html:
            raise UpdateError("候选 EXE 的界面品牌校验失败。")
        wav = io.BytesIO()
        with wave.open(wav, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0" * 8000)
        plain = wav.getvalue()
        encrypted = qmc1_transform(plain, V1_STATIC_KEY)
        for target in ("", "flac"):
            boundary = uuid.uuid4().hex
            body = (
                (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="update-smoke.tkm"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n').encode() + encrypted + b"\r\n"
                + f'--{boundary}\r\nContent-Disposition: form-data; name="embed_cover"\r\n\r\n0\r\n'.encode()
                + f'--{boundary}\r\nContent-Disposition: form-data; name="format"\r\n\r\n{target}\r\n'.encode()
                + f"--{boundary}--\r\n".encode()
            )
            request = urllib.request.Request(base + "/api/decode", data=body,
                                            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with opener.open(request, timeout=60) as response:
                result = json.load(response)
            if result.get("ok") is not True:
                raise UpdateError(f"候选 EXE 合成音频验证失败：{result.get('error', '')}")
            payload = read("/api/download/" + result["result"]["id"])
            if (not target and payload != plain) or (target and not payload.startswith(b"fLaC")):
                raise UpdateError("候选 EXE 解密下载或内置 FFmpeg 转码结果不正确。")
        with log_path.open("a", encoding="utf-8") as output:
            output.write("PASS packaged API health, MusicUnlock HTML, synthetic QMC/WAV roundtrip and FFmpeg FLAC\n")
    finally:
        finished.set()
        with process_lock:
            process.close()
        monitor.join(timeout=1)


class UpdateManager:
    def __init__(self, workspace: Path | None, data_dir: Path, current_info: dict,
                 *, source=None, build_runner=None, artifact_verifier=None):
        self.data_dir = Path(data_dir).resolve()
        self.current_info = dict(current_info)
        self.source = source if source is not None else GitSource()
        self.build_runner = build_runner if build_runner is not None else build_candidate
        self.artifact_verifier = artifact_verifier if artifact_verifier is not None else verify_candidate_api
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._closed = False
        self._thread = None
        self._candidate = None
        self._checked = None
        saved = self._read_json(self.data_dir / "update-workspace.json")
        saved_workspace = saved.get("workspace")
        metadata_workspace = current_info.get("source_workspace")
        location = (workspace or (saved_workspace if isinstance(saved_workspace, str) else None)
                    or (metadata_workspace if isinstance(metadata_workspace, str) else None))
        self.workspace = Path(location).resolve() if location else None
        upstream = current_info.get("upstream_revision", "")
        try:
            upstream = revision(upstream)
        except UpdateError:
            upstream = ""
        self._state = {
            "app_name": APP_NAME, "phase": "idle", "message": "点击检测更新后才会连接 GitHub。",
            "current_revision": upstream, "latest_revision": "", "ready_revision": "",
            "current_build": str(current_info.get("built_at") or current_info.get("source_revision") or "未知构建"),
            "latest_summary": "", "checked_at": "", "build_ready": False,
            "source_workspace": str(self.workspace) if self.workspace else "",
            "can_update": False, "can_restart": False, "log_path": "",
        }
        self._restore_candidate()

    @staticmethod
    def _read_json(path):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _ready_path(self):
        return self.data_dir / "update-candidate.json"

    def _verify_ready(self, candidate):
        self.source.validate_candidate(candidate)
        path = Path(candidate["artifact"])
        verify_windowed_pe(path)
        if artifact_sha256(path) != candidate["artifact_sha256"]:
            raise UpdateError("候选 EXE 的 SHA256 已改变，停止激活。")
        metadata = self._read_json(path.parent / "build-info.json")
        if (metadata.get("app_name") != APP_NAME
                or metadata.get("source_revision") != candidate["prepared_head"]
                or metadata.get("upstream_revision") != candidate["upstream_revision"]
                or metadata.get("source_dirty") is not False
                or not metadata.get("source_workspace")
                or Path(metadata["source_workspace"]).resolve() != Path(candidate["workspace"]).resolve()):
            raise UpdateError("候选构建元数据与检查的上游提交或本地源码不匹配。")

    def _restore_candidate(self):
        candidate = self._read_json(self._ready_path())
        if not candidate:
            return
        try:
            if self.workspace and Path(candidate["workspace"]).resolve() != self.workspace:
                return
            self._verify_ready(candidate)
            result = self._read_json(self.data_dir / "updates" / "install" / candidate["job_id"] / "result.json")
            if (self.current_info.get("source_revision") == candidate["prepared_head"]
                    and self.current_info.get("upstream_revision") == candidate["upstream_revision"]):
                self._state.update(phase="up_to_date", message="MusicUnlock 更新已激活。",
                                   latest_revision=candidate["upstream_revision"])
                # The new GUI starts before the bootstrap can record its final
                # result. Preserve retry evidence until startup is accepted;
                # a bootstrap rollback will relaunch the previous build.
                if result.get("status") == "installed":
                    self._ready_path().unlink(missing_ok=True)
                return
            self.workspace = Path(candidate["workspace"])
            self._candidate = candidate
            message = "已找到验证完成的更新，可重启激活。"
            if result.get("status") in {"failed", "rolled_back"}:
                message = "上次安装未完成，可重试激活：" + str(result.get("message", ""))
                if result.get("source_advanced"):
                    message += " 源码已准备完成，当前 EXE 保持原版本。"
            self._state.update(phase="ready", message=message, latest_revision=candidate["upstream_revision"],
                               ready_revision=candidate["upstream_revision"],
                               source_workspace=str(self.workspace), build_ready=True, can_restart=True,
                               log_path=candidate.get("log_path", ""))
        except Exception as exc:
            self._state.update(phase="failed", message=f"之前的候选更新不可用：{exc}")

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._state)

    def _publish(self, **values):
        with self._lock:
            if not self._closed:
                self._state.update(values)

    def _fail(self, exc):
        if self._candidate:
            try:
                self._verify_ready(self._candidate)
            except Exception:
                self._candidate = None
        self._publish(phase="failed", message=str(exc), can_update=False,
                      ready_revision=self._candidate["upstream_revision"] if self._candidate else "",
                      build_ready=bool(self._candidate), can_restart=bool(self._candidate))

    def _launch(self, phase, message, target):
        with self._lock:
            if self._closed or self._state["phase"] in ACTIVE_PHASES:
                return self.snapshot()
            self._cancel.clear()
            self._state.update(phase=phase, message=message, can_update=False, can_restart=False)
            initial = self.snapshot()
            self._thread = threading.Thread(target=self._worker, args=(target,), daemon=True,
                                            name="MusicUnlock-update")
            self._thread.start()
            return initial

    def _worker(self, target):
        try:
            target()
        except Exception as exc:
            self._fail(exc)

    def check_updates(self) -> dict:
        return self._launch("checking", "正在检查 GitHub main 源码提交……", self._check)

    def _check(self):
        self._checked = None
        expected = self.source.inspect(self.workspace, require_clean=False) if self.workspace else None
        latest = self.source.check()
        sha = revision(latest["revision"])
        if self._cancel.is_set():
            raise UpdateError("更新已取消。")
        self._checked = {"revision": sha, "expected": expected}
        available = sha != self._state["current_revision"]
        message = "检测到新的上游源码提交。" if available else "当前构建已包含最新上游源码。"
        if available and not self.workspace:
            message += " 请选择原始源码目录后重新检查。"
        self._publish(phase="available" if available else "up_to_date", message=message,
                      latest_revision=sha, latest_summary=str(latest.get("summary", "")),
                      ready_revision=self._candidate["upstream_revision"] if self._candidate else "",
                      checked_at=datetime.now(timezone.utc).isoformat(),
                      can_update=bool(available and expected), build_ready=bool(self._candidate),
                      can_restart=bool(self._candidate))

    def start_update(self) -> dict:
        with self._lock:
            if self._closed or self._state["phase"] in ACTIVE_PHASES:
                return self.snapshot()
            if not self._checked or not self._checked["expected"] or not self._state["can_update"]:
                self._state.update(message="请先成功检测更新并配置原始源码目录。")
                return self.snapshot()
            checked = deepcopy(self._checked)
        return self._launch("preparing", "正在创建隔离源码并保留本地桌面功能……",
                            lambda: self._prepare(checked))

    def _prepare(self, checked):
        job_id = uuid.uuid4().hex
        workspace = Path(checked["expected"]["workspace"])
        log_path = workspace / ".musicunlock-updates" / job_id / "build.log"
        self._publish(log_path=str(log_path))
        candidate = self.source.prepare(workspace, checked["expected"], checked["revision"], job_id, self._cancel)
        if self._cancel.is_set():
            raise UpdateError("更新已取消。")
        worktree = Path(candidate["worktree"])
        self._publish(phase="validating", message="正在验证合并后的 MusicUnlock 品牌和源码……")
        validate_branding(worktree)
        self.source.assert_unchanged(workspace, checked["expected"])
        self._publish(phase="building", message="正在测试源码并构建窗口版 EXE，请稍候……")
        artifact = Path(self.build_runner(worktree, workspace, checked["revision"], log_path, self._cancel)).resolve(strict=True)
        if artifact != worktree / "dist" / "MusicUnlock.exe":
            raise UpdateError("构建产物必须位于候选源码 dist/MusicUnlock.exe。")
        if self._cancel.is_set():
            raise UpdateError("更新已取消。")
        self._publish(phase="validating", message="正在验证窗口版 EXE 和真实本地 API……")
        verify_windowed_pe(artifact)
        self.artifact_verifier(artifact, worktree, log_path, self._cancel)
        candidate.update(artifact=str(artifact), artifact_sha256=artifact_sha256(artifact))
        self.source.assert_unchanged(workspace, checked["expected"])
        self._verify_ready(candidate)
        with self._lock:
            if self._closed or self._cancel.is_set():
                raise UpdateError("更新已取消。")
            _write_json(self._ready_path(), candidate)
            self._candidate = candidate
            self._state.update(phase="ready", message="更新已构建并验证完成，可重启激活。",
                               ready_revision=candidate["upstream_revision"],
                               build_ready=True, can_restart=True, can_update=False)

    def set_workspace(self, path: Path) -> dict:
        with self._lock:
            if self._closed or self._state["phase"] in ACTIVE_PHASES:
                return self.snapshot()
            try:
                expected = self.source.inspect(Path(path), require_clean=False)
                _write_json(self.data_dir / "update-workspace.json", {"workspace": expected["workspace"]})
                self.workspace = Path(expected["workspace"])
                self._checked = None
                self._candidate = None
                self._state.update(phase="idle", message="源码目录已配置，请重新检测更新。",
                                   ready_revision="",
                                   source_workspace=str(self.workspace), can_update=False,
                                   can_restart=False, build_ready=False)
            except Exception as exc:
                self._state.update(message=f"源码目录无法使用：{exc}")
            return self.snapshot()

    def candidate(self) -> dict | None:
        with self._lock:
            candidate = deepcopy(self._candidate)
            if not candidate or self._closed or self._state["phase"] in ACTIVE_PHASES:
                return None
            try:
                self._verify_ready(candidate)
            except Exception as exc:
                self._candidate = None
                self._state.update(phase="failed", message=str(exc), ready_revision="",
                                   build_ready=False, can_restart=False)
                return None
            return candidate

    def mark_installing(self) -> dict:
        with self._lock:
            if self._candidate and not self._closed:
                self._state.update(phase="installing", message="正在准备重启激活……", can_restart=False, can_update=False)
            return self.snapshot()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            self._state.update(can_update=False, can_restart=False)
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
