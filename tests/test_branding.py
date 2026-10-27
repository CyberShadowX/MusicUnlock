from pathlib import Path
import subprocess

import pytest


def tracked_tree(tmp_path):
    (tmp_path / "web/static").mkdir(parents=True)
    (tmp_path / "music_unlock").mkdir()
    (tmp_path / "web/static/index.html").write_text(
        '<title>music-geshizhuanhuan · 音乐</title><h1>music-geshizhuanhuan</h1>'
        '<a href="https://github.com/HRuiCcc/music-geshizhuanhuan">GitHub</a>', encoding="utf-8")
    (tmp_path / "music_unlock/cli.py").write_text('BANNER="music-geshizhuanhuan"', encoding="utf-8")
    (tmp_path / "music_unlock/branding.py").write_text('OLD_NAME="music-geshizhuanhuan"', encoding="utf-8")
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
    from music_unlock.branding import validate_branding
    root = tracked_tree(tmp_path)
    (root / "web/static/index.html").write_text('<title>MusicUnlock</title><h1>music-geshizhuanhuan</h1>', encoding="utf-8")
    with pytest.raises(ValueError):
        validate_branding(root)


@pytest.mark.parametrize("name,content", [
    ("music_unlock/cli.py", 'BANNER="music-geshizhuanhuan"'),
    ("windows_launcher.py", 'TITLE="music-geshizhuanhuan"'),
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
