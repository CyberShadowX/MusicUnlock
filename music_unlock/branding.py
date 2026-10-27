"""Reapply the local product identity after a three-way upstream merge."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess

from .app_info import APP_NAME

OLD_NAME = "music-geshizhuanhuan"
# Repository IDs and real URL/path components are provenance, not display labels.
_DISPLAY_NAME = re.compile(r"(?<![/\w])" + re.escape(OLD_NAME) + r"(?!\w)")


def _runtime_files(root: Path):
    root = root.resolve(strict=True)
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "web", "music_unlock",
         "windows_launcher.py", "desktop_runtime.py", "settings_bridge.py", "unlocker.py",
         "run.bat", "run.sh", "README.md"],
        check=True, capture_output=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for name in completed.stdout.split("\0"):
        if not name or name == "music_unlock/branding.py":
            continue
        path = root / name
        if path.suffix not in {".py", ".html", ".js", ".css", ".bat", ".sh", ".md"}:
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"品牌文件超出源码目录：{name}")
        yield name, path


def apply_branding(root: Path) -> list[str]:
    planned = []
    for name, path in _runtime_files(root):
        text = path.read_text(encoding="utf-8-sig")
        updated = _DISPLAY_NAME.sub(APP_NAME, text)
        if updated != text:
            planned.append((name, path, updated))
    for _, path, updated in planned:
        path.write_text(updated, encoding="utf-8")
    return [name for name, _, _ in planned]


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.title = []
        self.headings = []
        self.visible = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_endtag(self, tag):
        if tag in self.tags:
            self.tags = self.tags[:len(self.tags) - 1 - self.tags[::-1].index(tag)]

    def handle_data(self, data):
        if "title" in self.tags:
            self.title.append(data)
        if "h1" in self.tags:
            self.headings.append(data)
        if not any(tag in self.tags for tag in ("script", "style")):
            self.visible.append(data)


def validate_branding(root: Path) -> None:
    parser = _VisibleText()
    parser.feed((root / "web" / "static" / "index.html").read_text(encoding="utf-8-sig"))
    if APP_NAME not in "".join(parser.title) or APP_NAME not in "".join(parser.headings):
        raise ValueError("主界面名称校验失败：标题和页头必须保留 MusicUnlock")
    if _DISPLAY_NAME.search(" ".join(parser.visible)):
        raise ValueError("主界面仍包含上游旧显示名称，停止构建候选版本")
    for name, path in _runtime_files(root):
        if _DISPLAY_NAME.search(path.read_text(encoding="utf-8-sig")):
            raise ValueError(f"运行文件仍包含上游旧显示名称：{name}")
