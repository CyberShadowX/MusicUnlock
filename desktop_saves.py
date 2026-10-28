"""Scoped desktop saving; JavaScript may name registry IDs, never filesystem paths."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from urllib.parse import urlsplit


class WinFormsSaveDialog:
    """Own the native dialog configuration without patching pywebview packages."""

    def __call__(self, window, filename: str, extension: str) -> Path | None:
        from System import Action
        from webview.platforms.winforms import WinForms

        owner = window.native
        if owner is None:
            raise RuntimeError("桌面保存窗口尚未就绪")
        outcome, errors = [], []

        def choose():
            dialog = None
            try:
                dialog = WinForms.SaveFileDialog()
                dialog.Title = "保存 MusicUnlock 转换结果"
                dialog.FileName = filename
                dialog.Filter = f"{extension.upper()} 文件 (*.{extension})|*.{extension}"
                dialog.DefaultExt = extension
                dialog.AddExtension = True
                dialog.OverwritePrompt = True
                dialog.CheckPathExists = True
                dialog.ValidateNames = True
                dialog.RestoreDirectory = True
                outcome.append(Path(str(dialog.FileName)) if dialog.ShowDialog(owner) == WinForms.DialogResult.OK else None)
            except Exception as exc:
                errors.append(exc)
            finally:
                if dialog is not None:
                    dialog.Dispose()

        if owner.InvokeRequired:
            owner.Invoke(Action(choose))
        else:
            choose()
        if errors:
            raise errors[0]
        return outcome[0]


class DesktopSaveBridge:
    def __init__(self, *, dialog=None):
        self._dialog = dialog or WinFormsSaveDialog()
        self._window = None
        self._origin = None
        self._saving = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = {"phase": "idle", "message": "请选择需要保存的转换结果。"}

    def bind_window(self, window, origin: str):
        self._window = window
        self._origin = urlsplit(origin)

    def _authorize(self):
        if self._window is None or self._origin is None:
            raise PermissionError("桌面保存连接尚未就绪")
        actual = urlsplit(self._window.get_current_url() or "")
        if (self._origin.scheme != "http" or self._origin.hostname != "127.0.0.1"
                or actual.scheme != self._origin.scheme or actual.netloc != self._origin.netloc
                or actual.path not in {"", "/"} or actual.query):
            raise PermissionError("仅允许 MusicUnlock 本机主界面保存转换结果")

    def _set_state(self, phase, message, **progress):
        with self._state_lock:
            self._state = {"phase": phase, "message": message, **progress}
            return dict(self._state)

    def get_save_state(self) -> dict:
        self._authorize()
        with self._state_lock:
            return dict(self._state)

    def save_results(self, ids: list[str], archive: bool = False) -> dict:
        self._authorize()
        if not self._saving.acquire(blocking=False):
            return {"phase": "failed", "message": "已有保存任务，请等待完成。"}
        temporary = None
        try:
            from web.server import result_export, write_archive

            if not isinstance(archive, bool) or (not archive and (not isinstance(ids, list) or len(ids) != 1)):
                raise ValueError("请选择一个文件保存，或使用 ZIP 批量保存。")
            with result_export(ids) as entries:
                if archive:
                    filename, extension = "MusicUnlock.zip", "zip"
                else:
                    source = Path(entries[0]["path"])
                    filename = Path(entries[0].get("output_name") or source.name).name
                    extension = source.suffix.lstrip(".").lower()
                    if not re.fullmatch(r"[a-z0-9]{1,16}", extension):
                        raise ValueError("转换文件缺少有效的容器扩展名。")
                    if Path(filename).suffix.lower() != f".{extension}":
                        filename = f"{Path(filename).stem}.{extension}"
                self._set_state("choosing", "请选择保存位置……", completed=0, total=len(entries))
                destination = self._dialog(self._window, filename, extension)
                if destination is None:
                    return self._set_state("cancelled", "已取消保存。")
                destination = Path(destination)
                # Native AddExtension applies before its overwrite confirmation. Never
                # append an extension here, which could silently bypass that prompt.
                if destination.suffix.lower() != f".{extension}":
                    raise ValueError(f"请使用真实输出格式 .{extension}，或省略扩展名后重新保存。")
                if any(destination.resolve() == Path(entry["path"]).resolve() for entry in entries):
                    raise ValueError("请保存到转换缓存以外的位置。")
                self._set_state("preparing", "正在准备保存……", completed=0, total=len(entries))
                fd, name = tempfile.mkstemp(prefix=".MusicUnlock-", suffix=".partial", dir=destination.parent)
                temporary = Path(name)
                os.close(fd)

                def progress(completed, total):
                    self._set_state("saving", f"正在保存 {completed}/{total}……", completed=completed, total=total)

                progress(0, len(entries))
                if archive:
                    write_archive(temporary, entries, progress)
                else:
                    with Path(entries[0]["path"]).open("rb") as source, temporary.open("wb") as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                        target.flush()
                        os.fsync(target.fileno())
                    progress(1, 1)
                os.replace(temporary, destination)
                temporary = None
                return self._set_state("completed", "保存完成。", completed=len(entries), total=len(entries))
        except Exception as exc:
            logging.exception("Desktop save failed")
            return self._set_state("failed", f"保存失败：{exc}")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logging.exception("Could not remove failed save temporary file")
            self._saving.release()
