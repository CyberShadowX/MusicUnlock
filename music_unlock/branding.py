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

# This product-owned block is reapplied after every upstream source merge. The
# first author layer owns important priority over later layered and unlayered
# upstream rules. Repeated IDs protect against ordinary selectors in that layer.
FORMAT_PALETTE_ID = "musicunlock-format-palette"
_INERT_FORMAT_PARENTS = {"template", "noscript", "svg", "math", "textarea", "title", "iframe",
                         "xmp", "noembed", "noframes", "plaintext"}
FORMAT_PALETTE_STYLE = '''<style id="musicunlock-format-palette">
@layer musicunlock-format-palette {
  select#format#format,
  select#format#format option {
    color-scheme: dark !important;
    background: #10141f !important;
    color: #eef2fa !important;
    -webkit-text-fill-color: #eef2fa !important;
  }
  select#format#format option:checked {
    background: #263552 !important;
    color: #eef2fa !important;
    -webkit-text-fill-color: #eef2fa !important;
  }
}
</style>'''


class _FormatAppearance(HTMLParser):
    """Locate owned HTML without rewriting unrelated markup or script strings."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", source))
        self.stack = []
        self.heads = 0
        self.head_start = None
        self.head_end = None
        self.owned_start = None
        self.owned_span = None
        self.formats = 0
        self.options = []
        self.feed(source)
        self.close()
        if self.stack or self.heads != 1 or self.head_end is None:
            raise ValueError("输出格式配色无法应用：HTML head/style/select 结构不受支持")
        if self.formats != 1 or not self.options or len(set(self.options)) != len(self.options):
            raise ValueError("输出格式配色无法应用：需要唯一的原生 select#format 和有效且不重复的选项")

    def _offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    @staticmethod
    def _check_inline(attrs):
        # Inline appearance changes, especially !important, can defeat a style
        # sheet. Stop preparation instead of deleting upstream behavior.
        style = attrs.get("style", "") or ""
        style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
        for declaration in style.split(";"):
            property_name = declaration.partition(":")[0].strip().lower()
            if (property_name.startswith("background") or property_name in {
                    "color", "color-scheme", "-webkit-text-fill-color", "all", "appearance",
                    "-webkit-appearance", "opacity", "filter", "mix-blend-mode"} or "\\" in property_name):
                raise ValueError("输出格式配色存在内联视觉覆盖，停止构建候选版本")

    def handle_starttag(self, tag, attrs):
        # Earlier author styles (even invalidly placed before an explicit head)
        # could reserve a layer with higher !important priority. Reject such HTML.
        if self.heads == 0 and tag not in {"html", "head"}:
            raise ValueError("输出格式配色无法应用：head 前存在不受支持的内容")
        if tag == "head":
            if self.heads or self.stack:
                raise ValueError("输出格式配色无法应用：head 位置不受支持")
            self.head_start = self._offset() + len(self.get_starttag_text())
        attributes = dict(attrs)
        owned_id = any(key == "id" and value in {"format", FORMAT_PALETTE_ID} for key, value in attrs)
        if (owned_id or "format" in self.stack) and len(attributes) != len(attrs):
            raise ValueError("输出格式配色存在重复属性")
        if attributes.get("id") == FORMAT_PALETTE_ID:
            if tag != "style" or self.stack != ["head"] or self.owned_start is not None:
                raise ValueError("输出格式配色托管样式结构不受支持")
            self.owned_start = self._offset()
        if tag == "head":
            self.heads += 1
        if attributes.get("id") == "format":
            self.formats += 1
            if (tag != "select" or "head" in self.stack or "select" in self.stack
                    or _INERT_FORMAT_PARENTS.intersection(self.stack)
                    or "multiple" in attributes or attributes.get("size", "1") != "1"):
                raise ValueError("输出格式必须保留原生单选下拉框")
            self._check_inline(attributes)
            self.stack.append("format")
        elif "format" in self.stack:
            if tag != "option" or self.stack[-1] != "format":
                raise ValueError("输出格式下拉框内部结构不受支持")
            self._check_inline(attributes)
            value = attributes.get("value")
            if value is None or not re.fullmatch(r"[A-Za-z0-9_.+\-]{0,32}", value):
                raise ValueError("输出格式选项值无效")
            self.options.append(value)
            self.stack.append("option")
        elif tag in {"head", "style", "select"} | _INERT_FORMAT_PARENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if (tag in {"html", "head", "body", "style", "script", "select", "option"} | _INERT_FORMAT_PARENTS
                or dict(attrs).get("id") in {"format", FORMAT_PALETTE_ID}):
            raise ValueError("输出格式配色不支持自闭合的结构标签")
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.heads == 0 and data.strip():
            raise ValueError("输出格式配色无法应用：head 前存在文本内容")

    def handle_endtag(self, tag):
        if tag == "option" and "format" not in self.stack:
            return
        expected = "format" if tag == "select" and self.stack and self.stack[-1] == "format" else tag
        if tag not in {"head", "style", "select", "option"} | _INERT_FORMAT_PARENTS:
            return
        if not self.stack or self.stack[-1] != expected:
            raise ValueError("输出格式配色 HTML 结构不完整")
        self.stack.pop()
        if tag == "head":
            self.head_end = self._offset()
        if tag == "style" and self.owned_start is not None and self.owned_span is None:
            end = self.source.index(">", self._offset()) + 1
            self.owned_span = (self.owned_start, end)

    def canonical(self):
        """Replace only our block, reserving the first author layer in head."""
        source = self.source
        insert = self.head_start
        if self.owned_span is not None:
            start, end = self.owned_span
            # Preserve a canonical block already first in head byte-for-byte.
            if source[start:end] == FORMAT_PALETTE_STYLE and not source[insert:start].strip():
                return source
            source = source[:start] + source[end:]
        return source[:insert] + "\n" + FORMAT_PALETTE_STYLE + "\n" + source[insert:]


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
        if name == "web/static/index.html":
            updated = _FormatAppearance(updated).canonical()
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
    text = (root / "web" / "static" / "index.html").read_text(encoding="utf-8-sig")
    appearance = _FormatAppearance(text)
    if appearance.owned_span is None or appearance.canonical() != text:
        raise ValueError("输出格式配色缺失或已改变，停止构建候选版本")
    parser = _VisibleText()
    parser.feed(text)
    if APP_NAME not in "".join(parser.title) or APP_NAME not in "".join(parser.headings):
        raise ValueError("主界面名称校验失败：标题和页头必须保留 MusicUnlock")
    if _DISPLAY_NAME.search(" ".join(parser.visible)):
        raise ValueError("主界面仍包含上游旧显示名称，停止构建候选版本")
    for name, path in _runtime_files(root):
        if _DISPLAY_NAME.search(path.read_text(encoding="utf-8-sig")):
            raise ValueError(f"运行文件仍包含上游旧显示名称：{name}")
