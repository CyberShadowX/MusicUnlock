from pathlib import Path
import subprocess

import pytest


FORMAT_SELECT = ('<select id="format"><option value="">保持原样</option>'
                 '<option value="mp3">MP3</option><option value="flac">FLAC</option>'
                 '<option value="m4a">M4A</option><option value="wav">WAV</option>'
                 '<option value="ogg">OGG</option></select>')


def html_document(name="music-geshizhuanhuan", styles="", select=FORMAT_SELECT):
    integration = '''<script>
const dl = document.createElement('a'); dl.className = 'dl'; dl.href = '/api/download/' + item.id;
      dl.innerHTML = '<button class="primary">⬇ 下载</button>';
const ids = state.items.filter(i => i.status === 'ok' && i.id).map(i => i.id).join(',');
  if (ids) window.location = '/api/zip?ids=' + ids;
$('clear').onclick = async () => {
  await fetch('/api/clear', { method: 'POST' });
};
</script>'''
    return (f'<!DOCTYPE html>\n<html>\n<head>\n<title>{name} · 音乐</title>\n\n\n\n{styles}\n\n\n\n</head>\n'
            f'<body>\n<h1>{name}</h1>\n\n\n\n{select}\n\n\n\n'
            '<a href="https://github.com/HRuiCcc/music-geshizhuanhuan">GitHub</a>'
            f'\n\n\n\n{integration}\n</body>\n</html>')


def tracked_tree(tmp_path):
    (tmp_path / "web/static").mkdir(parents=True)
    (tmp_path / "music_unlock").mkdir()
    (tmp_path / "web/static/index.html").write_text(
        html_document(), encoding="utf-8")
    (tmp_path / "music_unlock/cli.py").write_text('BANNER="music-geshizhuanhuan"', encoding="utf-8")
    (tmp_path / "music_unlock/branding.py").write_text('OLD_NAME="music-geshizhuanhuan"', encoding="utf-8")
    for name in ("format_picker.js", "downloads.js"):
        (tmp_path / "web/static" / name).write_bytes((Path(__file__).resolve().parents[1] / "web/static" / name).read_bytes())
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return tmp_path


def test_runtime_branding_reapplies_without_breaking_real_upstream_url(tmp_path):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    changed = apply_branding(root)
    text = (root / "web/static/index.html").read_text(encoding="utf-8")
    assert '<h1>MusicUnlock</h1>' in text
    assert 'https://github.com/HRuiCcc/music-geshizhuanhuan' in text
    assert (root / "music_unlock/cli.py").read_text() == 'BANNER="MusicUnlock"'
    assert (root / "music_unlock/branding.py").read_text() == 'OLD_NAME="music-geshizhuanhuan"'
    assert "web/static/index.html" in changed
    validate_branding(root)
    assert apply_branding(root) == []


def test_old_visible_name_rejected_even_with_new_title(tmp_path):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    apply_branding(root)
    path = root / "web/static/index.html"
    path.write_text(path.read_text(encoding="utf-8").replace('<h1>MusicUnlock</h1>', '<h1>music-geshizhuanhuan</h1>'), encoding="utf-8")
    with pytest.raises(ValueError, match="名称校验"):
        validate_branding(root)


@pytest.mark.parametrize("name,content", [
    ("music_unlock/cli.py", 'BANNER="music-geshizhuanhuan"'),
    ("windows_launcher.py", 'TITLE="music-geshizhuanhuan"'),
    ("desktop_saves.py", 'TITLE="music-geshizhuanhuan"'),
    ("web/static/settings.js", 'label="music-geshizhuanhuan"'),
    ("run.bat", 'echo [music-geshizhuanhuan]'),
    ("run.sh", 'echo "music-geshizhuanhuan"'),
    ("README.md", '# music-geshizhuanhuan'),
])
def test_new_html_cannot_hide_old_runtime_brand(tmp_path, name, content):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    apply_branding(root)
    (root / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", name], check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with pytest.raises(ValueError, match="显示名称"):
        validate_branding(root)


def test_build_metadata_keeps_explicit_upstream_baseline(tmp_path):
    from music_unlock.app_info import load_build_info
    import json
    path = tmp_path / "build-info.json"
    path.write_text(json.dumps({"app_name": "wrong", "source_revision": "a" * 40,
                               "upstream_revision": "b" * 40}), encoding="utf-8")
    info = load_build_info(path)
    assert info["app_name"] == "MusicUnlock"
    assert info["upstream_revision"] == "b" * 40
    assert info["source_revision"] == "a" * 40


def test_missing_upstream_baseline_is_not_invented(tmp_path):
    from music_unlock.app_info import load_build_info
    assert load_build_info(tmp_path / "missing.json")["upstream_revision"] == ""


def test_current_interface_has_valid_native_dropdown_palette():
    from music_unlock.branding import validate_branding
    validate_branding(Path(__file__).resolve().parents[1])
    # This catches the original native popup bug even before the validator grows.
    text = (Path(__file__).resolve().parents[1] / "web/static/index.html").read_text(encoding="utf-8")
    assert "color-scheme: dark !important" in text
    assert "background: #10141f !important" in text
    assert "color: #eef2fa !important" in text


def test_current_interface_uses_owned_menu_and_shared_save_pipeline():
    root = Path(__file__).resolve().parents[1]
    text = (root / "web/static/index.html").read_text(encoding="utf-8")
    assert '<script src="/static/format_picker.js"></script>' in text
    assert '<script src="/static/downloads.js"></script>' in text
    assert "MusicUnlockSaves.save([item.id], false)" in text
    assert "MusicUnlockSaves.save(ids, true)" in text
    assert "window.location = '/api/zip" not in text
    assert "aria-live=\"polite\"" in text


def test_branding_rejects_missing_or_corrupted_owned_assets_before_any_write(tmp_path):
    from music_unlock.branding import apply_branding
    root = tracked_tree(tmp_path)
    original = (root / "web/static/index.html").read_text(encoding="utf-8")
    (root / "web/static/format_picker.js").write_text("corrupted", encoding="utf-8")
    with pytest.raises(ValueError, match="托管|资源"):
        apply_branding(root)
    assert (root / "web/static/index.html").read_text(encoding="utf-8") == original


def test_branding_restores_menu_save_script_references_and_live_status(tmp_path):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    apply_branding(root)
    path = root / "web/static/index.html"
    source = path.read_text(encoding="utf-8")
    removed = source.replace('<script src="/static/format_picker.js"></script>\n', '').replace(
        '<script src="/static/downloads.js"></script>\n', '').replace(
        '<div id="saveStatus" role="status" aria-live="polite" aria-atomic="true"></div>\n', '')
    path.write_text(removed, encoding="utf-8")
    with pytest.raises(ValueError, match="托管"):
        validate_branding(root)
    apply_branding(root)
    assert path.read_text(encoding="utf-8") == source
    validate_branding(root)


@pytest.mark.parametrize("damage", [
    lambda source: source.replace("MusicUnlockSaves.save(ids, true)", "changedSave(ids)"),
    lambda source: source.replace('/static/downloads.js', '/static/downloads.js?unknown=1'),
    lambda source: source.replace('aria-live="polite"', 'aria-live="off"'),
    lambda source: source.replace('<script src="/static/format_picker.js"></script>',
                                  '<script src="/static/format_picker.js"></script>' * 2),
])
def test_unknown_save_integration_is_rejected_before_branding_writes(tmp_path, damage):
    from music_unlock.branding import apply_branding
    root = tracked_tree(tmp_path)
    apply_branding(root)
    path = root / "web/static/index.html"
    broken = damage(path.read_text(encoding="utf-8"))
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="托管"):
        apply_branding(root)
    assert path.read_text(encoding="utf-8") == broken


@pytest.mark.parametrize("name", ["format_picker.js", "downloads.js"])
def test_missing_owned_ui_asset_is_rejected_before_writes(tmp_path, name):
    from music_unlock.branding import apply_branding
    root = tracked_tree(tmp_path)
    (root / "web/static" / name).unlink()
    original = (root / "web/static/index.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="托管"):
        apply_branding(root)
    assert (root / "web/static/index.html").read_text(encoding="utf-8") == original


@pytest.mark.parametrize("damage", [
    lambda source, zip_code: source.replace('<script>\n', '<script type="text/plain">\n', 1),
    lambda source, zip_code: source.replace(zip_code, '/*' + zip_code + '*/'),
    lambda source, zip_code: source.replace(zip_code, '`' + zip_code + '`'),
    lambda source, zip_code: source.replace(zip_code, '<!--' + zip_code + '\n'),
    lambda source, zip_code: source.replace('<script>\n', '<template><script>\n', 1).replace('</script>', '</script></template>', 1),
    lambda source, zip_code: source.replace('<script>\n', '<script src="/static/unknown.js">\n', 1),
])
def test_inert_owned_wiring_is_rejected_before_any_write(tmp_path, damage):
    from music_unlock.branding import apply_branding, validate_branding, _OWN_ZIP
    root = tracked_tree(tmp_path)
    apply_branding(root)
    path = root / "web/static/index.html"
    broken = damage(path.read_text(encoding="utf-8"), _OWN_ZIP)
    path.write_text(broken, encoding="utf-8")
    for operation in (validate_branding, apply_branding):
        with pytest.raises(ValueError, match="托管|脚本"):
            operation(root)
        assert path.read_text(encoding="utf-8") == broken


@pytest.mark.parametrize("attributes", ['type="text/plain"', 'type="module"', 'async', 'defer', 'nomodule', 'src="/static/other.js"'])
def test_owned_script_attributes_cannot_disable_or_reorder_execution(tmp_path, attributes):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    apply_branding(root)
    path = root / "web/static/index.html"
    source = path.read_text(encoding="utf-8")
    broken = source.replace('<script src="/static/downloads.js">', f'<script {attributes} src="/static/downloads.js">')
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="托管|脚本"):
        validate_branding(root)


def test_owned_scripts_inside_comments_or_inert_parents_do_not_validate(tmp_path):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    apply_branding(root)
    path = root / "web/static/index.html"
    source = path.read_text(encoding="utf-8")
    for wrapper in ('<!--{}-->', '<template>{}</template>'):
        broken = source.replace('<script src="/static/downloads.js"></script>', wrapper.format('<script src="/static/downloads.js"></script>'))
        path.write_text(broken, encoding="utf-8")
        for operation in (validate_branding, apply_branding):
            with pytest.raises(ValueError, match="托管|脚本"):
                operation(root)


@pytest.mark.parametrize("damage", [
    lambda source: source.replace('<script>\n', '<script async>\n', 1),
    lambda source: source.replace('<script>\n', '<script defer>\n', 1),
    lambda source: source.replace('<script>\n', '<script type="module">\n', 1),
    lambda source: source.replace('<script src="/static/format_picker.js"></script>\n', '').replace(
        '<script>\n', '<script src="/static/format_picker.js"></script>\n<script>\n', 1),
    lambda source: source.replace('<script src="/static/format_picker.js"></script>\n<script src="/static/downloads.js"></script>',
                                  '<script src="/static/downloads.js"></script>\n<script src="/static/format_picker.js"></script>'),
])
def test_script_execution_order_is_fail_closed(tmp_path, damage):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    apply_branding(root)
    path = root / "web/static/index.html"
    broken = damage(path.read_text(encoding="utf-8"))
    path.write_text(broken, encoding="utf-8")
    for operation in (validate_branding, apply_branding):
        with pytest.raises(ValueError, match="托管|脚本"):
            operation(root)
        assert path.read_text(encoding="utf-8") == broken


def test_unrelated_javascript_comments_literals_and_classic_mime_remain_supported(tmp_path):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    path = root / "web/static/index.html"
    unrelated = "/* unrelated upstream comment */ const pattern = /['\"]/; const caption = `hello ${'world'}`;\n"
    source = path.read_text(encoding="utf-8").replace('<script>\n', '<script type="text/javascript">\n' + unrelated, 1)
    path.write_text(source, encoding="utf-8")
    apply_branding(root)
    validate_branding(root)
    assert unrelated in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("owned", ["", '<style id="musicunlock-format-palette">select {color:white}</style>'])
def test_branding_restores_removed_or_modified_palette_without_touching_other_markup(tmp_path, owned):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    path = root / "web/static/index.html"
    unrelated = ('<style>select#format { background: white !important; color: white !important }</style>'
                 '<script>const example="</head>"; const url="/music-geshizhuanhuan";</script>')
    path.write_text(html_document(styles=owned + unrelated), encoding="utf-8")
    apply_branding(root)
    result = path.read_text(encoding="utf-8")
    assert unrelated in result
    assert FORMAT_SELECT in result
    assert result.count('id="musicunlock-format-palette"') == 1
    assert result.index('id="musicunlock-format-palette"') < result.index('<title>')
    assert "color-scheme: dark !important" in result
    assert "background: #10141f !important" in result
    assert "color: #eef2fa !important" in result
    validate_branding(root)
    assert apply_branding(root) == []


@pytest.mark.parametrize("damage", [
    lambda text: text.replace("#10141f", "white"),
    lambda text: text.replace("color-scheme: dark !important", "color-scheme: light !important"),
    lambda text: text.replace('id="musicunlock-format-palette"', 'id="unowned"'),
    lambda text: text.replace('id="format"', 'id="renamed"'),
    lambda text: text.replace('value="ogg"', 'value="mp3"'),
    lambda text: text.replace('id="format"', 'id="format" style="color:white!important"'),
    lambda text: text.replace('value="mp3"', 'value="mp3" style="background:white!important"'),
])
def test_validation_rejects_palette_or_native_structure_regression(tmp_path, damage):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    apply_branding(root)
    path = root / "web/static/index.html"
    path.write_text(damage(path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ValueError, match="格式|配色"):
        validate_branding(root)


@pytest.mark.parametrize("select", [
    FORMAT_SELECT.replace('id="format"', 'id="renamed"'),
    FORMAT_SELECT + FORMAT_SELECT,
    FORMAT_SELECT.replace('value="mp3"', 'value="mp3" style="color:white!important"'),
    FORMAT_SELECT.replace('<select id="format">', '<select id="format" multiple>'),
    '<template>' + FORMAT_SELECT + '</template>',
    FORMAT_SELECT.replace('id="format"', 'id="format" id="renamed"'),
    FORMAT_SELECT.replace('value="mp3"', 'value="mp3" value="renamed"'),
    '<select id="other">' + FORMAT_SELECT + '</select>',
    '<template/>' + FORMAT_SELECT,
    '<plaintext>' + FORMAT_SELECT,
    '<noembed>' + FORMAT_SELECT + '</noembed>',
    '<noframes>' + FORMAT_SELECT + '</noframes>',
])
def test_apply_fails_closed_on_unsupported_controls_before_writing_files(tmp_path, select):
    from music_unlock.branding import apply_branding
    root = tracked_tree(tmp_path)
    path = root / "web/static/index.html"
    original = html_document(select=select)
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="格式|配色"):
        apply_branding(root)
    assert path.read_text(encoding="utf-8") == original
    assert (root / "music_unlock/cli.py").read_text() == 'BANNER="music-geshizhuanhuan"'


def test_new_upstream_format_options_keep_native_semantics(tmp_path):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    path = root / "web/static/index.html"
    select = FORMAT_SELECT.replace('</select>', '<option value="opus">OPUS</option></select>')
    path.write_text(html_document(select=select), encoding="utf-8")
    apply_branding(root)
    validate_branding(root)
    assert select in path.read_text(encoding="utf-8")


def test_unrelated_native_controls_and_inline_layout_are_preserved(tmp_path):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    path = root / "web/static/index.html"
    other = '<select id="quality"><option value="high">High</option></select>'
    select = FORMAT_SELECT.replace('id="format"', 'id="format" style="width:12em"')
    path.write_text(html_document(select=other + select), encoding="utf-8")
    apply_branding(root)
    validate_branding(root)
    assert other + select in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("styles", [
    '<style id="musicunlock-format-palette">bad',
    '<style id="musicunlock-format-palette">bad</style>' * 2,
    '<div id="musicunlock-format-palette">bad</div>',
])
def test_ambiguous_managed_style_is_rejected_without_stripping_other_html(tmp_path, styles):
    from music_unlock.branding import apply_branding
    root = tracked_tree(tmp_path)
    path = root / "web/static/index.html"
    original = html_document(styles=styles)
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="格式|配色"):
        apply_branding(root)
    assert path.read_text(encoding="utf-8") == original


def test_owned_important_layer_is_first_before_upstream_layered_or_unlayered_styles(tmp_path):
    from music_unlock.branding import apply_branding, validate_branding
    root = tracked_tree(tmp_path)
    path = root / "web/static/index.html"
    upstream = ('<style>@layer upstream {select,option {background:white!important;'
                'color:white!important;-webkit-text-fill-color:white!important}}</style>'
                '<link rel="stylesheet" href="/static/upstream.css">')
    path.write_text(html_document(styles=upstream), encoding="utf-8")
    apply_branding(root)
    result = path.read_text(encoding="utf-8")
    assert result.index('@layer musicunlock-format-palette {') < result.index('@layer upstream')
    assert result.split('<head>', 1)[1].lstrip().startswith('<style id="musicunlock-format-palette">')
    assert upstream in result
    validate_branding(root)
    assert apply_branding(root) == []


@pytest.mark.parametrize("before_head", [
    '<style>@layer earlier { select {color:white!important} }</style>',
    '<link rel="stylesheet" href="upstream.css">',
    '<script>document.write("<style>@layer earlier;</style>")</script>',
    '<body>',
])
def test_unsupported_head_placement_cannot_precede_the_owned_layer(tmp_path, before_head):
    from music_unlock.branding import apply_branding
    root = tracked_tree(tmp_path)
    path = root / "web/static/index.html"
    original = html_document().replace('<head>', before_head + '<head>')
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="格式|配色"):
        apply_branding(root)
    assert path.read_text(encoding="utf-8") == original
