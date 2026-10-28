"""Reapply the local product identity after a three-way upstream merge."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import hashlib
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
  #format#format[hidden], #formatListbox#formatListbox[hidden] { display: none !important; }
  #formatTrigger#formatTrigger, #formatListbox#formatListbox, #formatListbox#formatListbox [role="option"] {
    color-scheme: dark !important;
    color: #eef2fa !important;
    -webkit-text-fill-color: #eef2fa !important;
    font: 400 13px/1.5 -apple-system, "Segoe UI", sans-serif !important;
    box-sizing: border-box !important;
    opacity: 1 !important;
    filter: none !important;
    mix-blend-mode: normal !important;
  }
  #formatTrigger#formatTrigger {
    background: linear-gradient(135deg, #30234f, #191626) !important;
    border: 0 !important;
    border-radius: 8px !important;
    padding: 8px 12px !important;
    min-width: 116px !important;
    text-align: left !important;
    cursor: pointer !important;
  }
  #formatTrigger#formatTrigger:focus-visible { outline: 2px solid #c4b5fd !important; outline-offset: 3px !important; }
  #formatTrigger#formatTrigger:disabled { opacity: .55 !important; cursor: default !important; }
  #formatListbox#formatListbox {
    position: fixed !important;
    inset: auto;
    margin: 0 !important;
    z-index: 20 !important;
    background: linear-gradient(145deg, #2a2045, #171322) !important;
    border: 0 !important;
    border-radius: 10px !important;
    padding: 5px !important;
    overflow: auto !important;
    overscroll-behavior: contain !important;
  }
  #formatListbox#formatListbox [role="option"] {
    background: transparent !important;
    border: 0 !important;
    border-radius: 6px !important;
    padding: 9px 12px !important;
    cursor: pointer !important;
  }
  #formatListbox#formatListbox [role="option"][aria-selected="true"] { background: #463064 !important; font-weight: 600 !important; }
  #formatListbox#formatListbox [role="option"][data-active="true"] { background: #594079 !important; outline: 1px solid #c4b5fd !important; outline-offset: -2px !important; }
  #formatListbox#formatListbox [role="option"][aria-disabled="true"] { color: #c7bdde !important; -webkit-text-fill-color: #c7bdde !important; cursor: default !important; }
  #saveStatus#saveStatus {
    position: fixed !important;
    bottom: 12px !important;
    left: 20px !important;
    right: 20px !important;
    z-index: 30 !important;
    max-width: 860px !important;
    margin: 0 auto !important;
    padding: 10px 14px !important;
    border-radius: 8px !important;
    background: #241b37 !important;
    color: #e3d9ff !important;
    -webkit-text-fill-color: #e3d9ff !important;
    font-size: 13px !important;
    line-height: 1.6 !important;
    pointer-events: none !important;
  }
  #saveStatus#saveStatus:empty { display: none !important; }
}
</style>'''

_OWNED_ASSET_HASHES = {'format_picker.js': '6ff455b3d60e0e1fbed0aec6eee259107007dac790dfda0126c38ac3752263d4', 'downloads.js': '4cc2805f1de49210f9f8918facbbf607abe28c22521d9201acb680a35818a0d4'}  # SHA256 values are set alongside reviewed asset changes.
_OLD_SINGLE = """const dl = document.createElement('a'); dl.className = 'dl'; dl.href = '/api/download/' + item.id;
      dl.innerHTML = '<button class="primary">⬇ 下载</button>';"""
_OWN_SINGLE = """const dl = document.createElement('button'); dl.className = 'primary dl'; dl.dataset.saveAction = 'single';
      dl.textContent = '⬇ 保存';
      dl.onclick = () => window.MusicUnlockSaves.save([item.id], false);"""
_OLD_ZIP = """const ids = state.items.filter(i => i.status === 'ok' && i.id).map(i => i.id).join(',');
  if (ids) window.location = '/api/zip?ids=' + ids;"""
_OWN_ZIP = """const ids = state.items.filter(i => i.status === 'ok' && i.id).map(i => i.id);
  if (ids.length) window.MusicUnlockSaves.save(ids, true);"""
_OLD_CLEAR = """$('clear').onclick = async () => {
  await fetch('/api/clear', { method: 'POST' });"""
_OWN_CLEAR = """$('clear').onclick = async () => {
  if (window.MusicUnlockSaves?.isBusy() || window.MusicUnlockUpdates?.isInstalling()) return;
  const response = await fetch('/api/clear', { method: 'POST' });
  if (!response.ok) return;"""
_OWN_STATUS = '<div id="saveStatus" role="status" aria-live="polite" aria-atomic="true"></div>'
_OWN_SCRIPTS = '<script src="/static/format_picker.js"></script>\n<script src="/static/downloads.js"></script>'


def _validate_owned_assets(root: Path):
    for name, expected in _OWNED_ASSET_HASHES.items():
        path = root / "web/static" / name
        if (not path.is_file() or not path.resolve().is_relative_to(root.resolve())
                or hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != expected):
            raise ValueError(f"托管界面资源缺失或已改变，停止构建候选版本：{name}")


class _SavingScripts(HTMLParser):
    """Record real script elements and their HTML execution context."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.lines = [0] + [match.end() for match in re.finditer('\n', source)]
        self.parents = []
        self.scripts = []
        self.current = None
        self.format_seen = False
        self.feed(source)
        self.close()
        if self.current is not None:
            raise ValueError('托管下载脚本标签未闭合')

    def _position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == 'select' and dict(attrs).get('id') == 'format':
            self.format_seen = True
        if tag == 'script':
            self.current = dict(start=self._position(), body=self._position() + len(self.get_starttag_text()),
                                attrs=attrs, inert=bool(_INERT_FORMAT_PARENTS.intersection(self.parents)),
                                after_format=self.format_seen, in_body='body' in self.parents)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
                       'param', 'source', 'track', 'wbr'}:
            self.parents.append(tag)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.current['code'] = self.source[self.current['body']:self._position()]
            self.scripts.append(self.current)
            self.current = None
        if tag in self.parents:
            self.parents = self.parents[:len(self.parents) - 1 - self.parents[::-1].index(tag)]

    def handle_startendtag(self, tag, attrs):
        if tag == 'script':
            raise ValueError('托管下载不支持自闭合脚本标签')


def _javascript_code_positions(code):
    """Conservative lexical guard, not a JavaScript evaluator or flow analyser.

    Mark code positions outside strings, template text, regexes and comments.
    Owned handlers must start in executable code, never in a quoted example.
    """
    positions = set()
    index = 0
    previous = ''
    while index < len(code):
        char = code[index]
        if code.startswith(('//', '<!--'), index) or (code.startswith('-->', index) and not code[:index].rsplit('\n', 1)[-1].strip()):
            end = code.find('\n', index)
            index = len(code) if end < 0 else end + 1
            continue
        if code.startswith('/*', index):
            end = code.find('*/', index + 2)
            if end < 0:
                raise ValueError('托管下载脚本注释未闭合')
            index = end + 2
            continue
        if char in "\"'`":
            quote = char
            index += 1
            while index < len(code):
                if code[index] == '\\':
                    index += 2
                elif code[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            else:
                raise ValueError('托管下载脚本字符串未闭合')
            previous = 'literal'
            continue
        # A regular expression can begin after an operator/delimiter. Division
        # after identifiers/literals stays executable. Unusual grammar fails closed.
        if char == '/' and (not previous or previous in '=([{,:;!?&|'):
            index += 1
            character_class = False
            while index < len(code):
                token = code[index]
                if token == '\\':
                    index += 2
                    continue
                if token == '[':
                    character_class = True
                elif token == ']':
                    character_class = False
                elif token == '/' and not character_class:
                    index += 1
                    break
                elif token in '\r\n':
                    raise ValueError('托管下载脚本正则表达式不受支持')
                index += 1
            else:
                raise ValueError('托管下载脚本正则表达式未闭合')
            previous = 'literal'
            continue
        positions.add(index)
        if not char.isspace():
            previous = char
        index += 1
    return positions


def _validate_saving_execution(source):
    scripts = _SavingScripts(source).scripts
    variants = ((_OLD_SINGLE, _OWN_SINGLE), (_OLD_ZIP, _OWN_ZIP), (_OLD_CLEAR, _OWN_CLEAR))
    owners = []
    for choices in variants:
        occurrences = [(script, script['code'].find(snippet)) for script in scripts
                       for snippet in choices if snippet in script['code']]
        if len(occurrences) != 1:
            raise ValueError('托管下载处理必须位于唯一的实际脚本中')
        script, offset = occurrences[0]
        attrs = dict(script['attrs'])
        if (script['inert'] or not script['in_body'] or not script['after_format']
                or len(attrs) != len(script['attrs'])
                or set(attrs) - {'type', 'nonce'}
                or (attrs.get('type') or '').strip().lower() not in {'', 'text/javascript', 'application/javascript'}
                or offset not in _javascript_code_positions(script['code'])):
            raise ValueError('托管下载处理必须保留在可执行的同步经典脚本中')
        owners.append(script)
    if not all(script is owners[0] for script in owners):
        raise ValueError('托管下载处理脚本顺序不兼容')
    main = owners[0]
    previous = main['start']
    for name in ('format_picker.js', 'downloads.js'):
        expected = '/static/' + name
        found = [script for script in scripts if dict(script['attrs']).get('src') == expected]
        if source.count(f'<script src="{expected}"></script>') > len(found):
            raise ValueError('托管界面资源脚本不能放在注释或字符串中')
        # Missing references are safely reconstructed; existing ones must already
        # be active synchronous classics in the required order after the main code.
        if found:
            script = found[0]
            if (len(found) != 1 or script['attrs'] != [('src', expected)] or script['inert']
                    or script['code'].strip() or script['start'] <= previous):
                raise ValueError('托管界面资源脚本必须同步执行且顺序正确')
            previous = script['start']


def _canonical_saving(source: str):
    _validate_saving_execution(source)
    for original, owned in ((_OLD_SINGLE, _OWN_SINGLE), (_OLD_ZIP, _OWN_ZIP), (_OLD_CLEAR, _OWN_CLEAR)):
        if source.count(original) == 1 and owned not in source:
            source = source.replace(original, owned)
        elif source.count(owned) != 1 or original in source:
            raise ValueError("托管下载集成不兼容，停止构建候选版本")
    # Only remove precisely recognized owned nodes, never arbitrary upstream content.
    for script in _OWN_SCRIPTS.splitlines():
        if source.count(script) > 1:
            raise ValueError("托管界面脚本引用重复")
        source = source.replace(script + "\n", "").replace(script, "")
    if source.count(_OWN_STATUS) > 1 or source.count('</body>') != 1:
        raise ValueError("托管下载状态结构不兼容")
    source = source.replace(_OWN_STATUS + '\n', '').replace(_OWN_STATUS, '')
    # Refuse disguised/modified integration instead of injecting a duplicate control.
    if re.search(r'id\s*=\s*[\"\']saveStatus[\"\']|src\s*=\s*[\"\'][^\"\']*(?:format_picker|downloads)\.js', source):
        raise ValueError("托管下载集成标记已改变")
    return source.replace('</body>', _OWN_STATUS + '\n' + _OWN_SCRIPTS + '\n</body>')


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
         "windows_launcher.py", "desktop_runtime.py", "settings_bridge.py", "desktop_saves.py", "unlocker.py",
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
    _validate_owned_assets(root)
    planned = []
    for name, path in _runtime_files(root):
        text = path.read_text(encoding="utf-8-sig")
        updated = _DISPLAY_NAME.sub(APP_NAME, text)
        if name == "web/static/index.html":
            updated = _canonical_saving(_FormatAppearance(updated).canonical())
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
    _validate_owned_assets(root)
    text = (root / "web" / "static" / "index.html").read_text(encoding="utf-8-sig")
    appearance = _FormatAppearance(text)
    if appearance.owned_span is None or appearance.canonical() != text:
        raise ValueError("输出格式配色缺失或已改变，停止构建候选版本")
    if _canonical_saving(text) != text:
        raise ValueError("托管菜单或下载集成缺失或已改变")
    parser = _VisibleText()
    parser.feed(text)
    if APP_NAME not in "".join(parser.title) or APP_NAME not in "".join(parser.headings):
        raise ValueError("主界面名称校验失败：标题和页头必须保留 MusicUnlock")
    if _DISPLAY_NAME.search(" ".join(parser.visible)):
        raise ValueError("主界面仍包含上游旧显示名称，停止构建候选版本")
    for name, path in _runtime_files(root):
        if _DISPLAY_NAME.search(path.read_text(encoding="utf-8-sig")):
            raise ValueError(f"运行文件仍包含上游旧显示名称：{name}")
