"""Only the local desktop page may invoke these five update capabilities."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from music_unlock.update_install import prepare_install
from music_unlock.update_process import process_identity


class SettingsBridge:
    def __init__(self, manager, data_dir: Path, app_executable: Path | None = None,
                 *, prepare=prepare_install, launch_helper=None, begin_restart=None,
                 cancel_restart=None, handoff_timeout: float = 15):
        from web.server import begin_update_restart, cancel_update_restart
        self._manager = manager
        self._data_dir = data_dir
        self._executable = app_executable
        self._prepare = prepare
        self._launch_helper = launch_helper
        self._begin_restart = begin_restart or begin_update_restart
        self._cancel_restart = cancel_restart or cancel_update_restart
        self._timeout = handoff_timeout
        self._window = None
        self._origin = None
        self._lock = threading.RLock()
        self._installing = False
        self._message = ""

    def bind_window(self, window, origin: str):
        self._window = window
        self._origin = urlsplit(origin)

    def _authorize(self):
        if self._window is None or self._origin is None:
            raise PermissionError("桌面更新连接尚未就绪")
        actual = urlsplit(self._window.get_current_url() or "")
        if (self._origin.scheme != "http" or self._origin.hostname != "127.0.0.1"
                or actual.scheme != self._origin.scheme or actual.netloc != self._origin.netloc
                or actual.path not in {"", "/"} or actual.query):
            raise PermissionError("仅允许 MusicUnlock 本机主界面操作更新")

    def _snapshot(self):
        state = self._manager.snapshot()
        if self._executable is None:
            state["can_restart"] = False
        if self._message:
            state["message"] = self._message
        if self._installing:
            state.update(phase="installing", can_update=False, can_restart=False,
                         message="正在准备重启并应用更新……")
        return state

    def get_update_state(self):
        self._authorize()
        return self._snapshot()

    def check_updates(self):
        self._authorize()
        with self._lock:
            if not self._installing:
                self._message = ""
                self._manager.check_updates()
            return self._snapshot()

    def start_update(self):
        self._authorize()
        with self._lock:
            if not self._installing:
                self._message = ""
                self._manager.start_update()
            return self._snapshot()

    def choose_source_workspace(self):
        self._authorize()
        with self._lock:
            if self._installing:
                return self._snapshot()
            import webview
            selected = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if selected:
                self._message = ""
                self._manager.set_workspace(Path(selected[0]))
            return self._snapshot()

    @staticmethod
    def _read(path):
        try:
            result = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError):
            return {}

    def _wait_handoff(self, prepared, helper):
        identity = process_identity(os.getpid())
        deadline = time.monotonic() + self._timeout
        while True:
            receipt = self._read(prepared["handoff_path"])
            if (receipt.get("status") == "parent-held"
                    and receipt.get("parent_pid") == os.getpid()
                    and receipt.get("parent_created") == identity
                    and receipt.get("handoff_token") == prepared["handoff_token"]):
                return
            result = self._read(prepared["result_path"])
            if result.get("status") in {"failed", "rolled_back"}:
                raise RuntimeError(result.get("message") or "安装进程验证失败")
            if helper.poll() is not None:
                raise RuntimeError("安装进程提前退出")
            if time.monotonic() >= deadline:
                raise TimeoutError("安装进程交接超时")
            time.sleep(0.05)

    def restart_after_update(self):
        self._authorize()
        with self._lock:
            if self._installing:
                return self._snapshot()
            if self._executable is None:
                state = self._snapshot()
                state.update(phase="failed", message="源码运行模式请使用构建后的 MusicUnlock.exe 激活更新。")
                return state
            candidate = self._manager.candidate()
            if candidate is None:
                self._message = "请先成功构建并验证更新。"
                return self._snapshot()
            if not self._begin_restart():
                self._message = "正在转换文件，请等待转换完成后重启。"
                return self._snapshot()
            self._installing = True
        helper = None
        try:
            workspace = Path(candidate.get("workspace", self._data_dir))
            prepared = self._prepare(candidate, self._executable, self._data_dir,
                                     workspace / ".venv" / "Scripts" / "pythonw.exe")
            if self._launch_helper is None:
                from desktop_runtime import launch_update_helper
                launch = launch_update_helper
            else:
                launch = self._launch_helper
            helper = launch(prepared["command"])
            self._wait_handoff(prepared, helper)
            # The helper now holds our exact process. Release its private job
            # before normal GUI close, which also closes this app's child job.
            helper.allow_survival()
            self._window.destroy()
            helper.close()
            return self._snapshot()
        except Exception as exc:
            if helper is not None:
                try:
                    helper.terminate_tree()
                finally:
                    helper.close()
            self._cancel_restart()
            with self._lock:
                self._installing = False
                self._message = f"更新未激活，当前窗口保留；可重试重启：{exc}"
            return self._snapshot()
