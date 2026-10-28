"""Candidate builds must retain native saving and its lifecycle integration."""
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def saving_source(tmp_path):
    for name in ('desktop_saves.py', 'windows_launcher.py', 'web/server.py'):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    return tmp_path


def test_current_native_saving_integration_is_valid(saving_source):
    from music_unlock.saving_guard import validate_saving
    validate_saving(saving_source)


@pytest.mark.parametrize('name,before,after', [
    ('desktop_saves.py', 'dialog.DefaultExt = extension', 'dialog.DefaultExt = ""'),
    ('windows_launcher.py', 'saves.save_results, saves.get_save_state', 'saves.get_save_state'),
    ('web/server.py', 'zipfile.ZIP_STORED', 'zipfile.ZIP_DEFLATED'),
    ('web/server.py', 'if _active_conversions or _active_exports or _update_restart:', 'if _active_conversions or _update_restart:'),
])
def test_candidate_with_lost_download_fix_is_rejected(saving_source, name, before, after):
    from music_unlock.saving_guard import validate_saving
    path = saving_source / name
    text = path.read_text(encoding='utf-8-sig')
    assert before in text
    path.write_text(text.replace(before, after, 1), encoding='utf-8')
    with pytest.raises(ValueError, match='保存定制'):
        validate_saving(saving_source)


def test_unrelated_upstream_decode_and_comments_remain_mergeable(saving_source):
    from music_unlock.saving_guard import validate_saving
    path = saving_source / 'web/server.py'
    text = path.read_text(encoding='utf-8-sig').replace('def decode():', 'def decode():\n    """Upstream decoding improvement."""')
    path.write_text('# upstream comment\n' + text, encoding='utf-8')
    validate_saving(saving_source)


def test_missing_bridge_fails_before_build(saving_source):
    from music_unlock.saving_guard import validate_saving
    (saving_source / 'desktop_saves.py').unlink()
    with pytest.raises(ValueError, match='保存定制'):
        validate_saving(saving_source)
