"""Exercise 34 real KWM conversions and native extensionless saves in an owned app instance."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import uuid
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from builders import build_kwm
from check_desktop_exe import NativeSaves, ProcessAudit, Win32, assert_no_console, fetch_json, free_port
from music_unlock.update_process import ManagedProcess


def prepare_fixtures(directory: Path) -> dict:
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        # Eight seconds of deterministic noise remains incompressible: this ZIP
        # acceptance is materially larger than the old two tiny NCM fixtures.
        # The legacy KWM decoder recovers its XOR key from a silent block.
        stream.writeframes(bytes(4096) + random.Random(34).randbytes(44100 * 4 * 8))
    encrypted = build_kwm(raw.getvalue())
    fixture_root = directory / 'fixtures'
    fixture_root.mkdir()
    batch = []
    for index in range(34):
        parent = fixture_root / str(index)
        parent.mkdir()
        name = '中文 同名音频.kwm' if index < 2 else f'中文 音频 {index + 1:02d}.kwm'
        location = parent / name
        location.write_bytes(encrypted)
        batch.append(str(location))
    outputs = {target: str(directory / f'另存 多个.点 {target}.{target}') for target in ('flac', 'mp3', 'm4a', 'wav', 'ogg')}
    return {'batch': batch, 'matrix': batch[2], 'outputs': outputs,
            'zip': str(directory / '批量 三十四首.zip'), 'zipAgain': str(directory / '再次批量 三十四首.zip')}


def observe_saves(win, audit, directory, config):
    destinations = [Path(config['zip']), directory / 'cancel.flac', Path(config['outputs']['flac']), Path(config['zipAgain'])]
    destinations += [Path(config['outputs'][target]) for target in ('mp3', 'm4a', 'wav', 'ogg')]
    initial_names = ['MusicUnlock.zip', '中文 同名音频.flac', '中文 同名音频.flac', 'MusicUnlock.zip']
    initial_names += [f'中文 音频 03.{target}' for target in ('mp3', 'm4a', 'wav', 'ogg')]
    return NativeSaves(win, audit, destinations, initial_names, omit_extensions=True, cancel_indices={1})


def run_on_page(cdp, base, win, audit, directory):
    config = prepare_fixtures(directory)
    config.update(cdp=cdp, base=base, report=str(directory / 'saving-ui.json'),
                  screenshot=str(directory / 'saved-ui.png'), menuScreenshot=str(directory / 'gradient-menu.png'))
    saves = observe_saves(win, audit, directory, config)
    config_file = directory / 'saving-config.json'
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        with (directory / 'saving-ui.log').open('wb') as log:
            subprocess.run(['node', str(ROOT / 'scripts/saving_e2e.cjs'), '--config', str(config_file)],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=360, creationflags=subprocess.CREATE_NO_WINDOW)
        dialogs = saves.finish()
        for format_name, filename in config['outputs'].items():
            location = Path(filename)
            assert location.is_file() and location.stat().st_size > 0
            assert not location.with_suffix('').exists(), 'Saving produced an extensionless file'
            binary = location.read_bytes()[:12]
            signature = {'flac': lambda b: b.startswith(b'fLaC'), 'mp3': lambda b: b.startswith(b'ID3') or b[:1] == b'\xff',
                         'm4a': lambda b: b[4:8] == b'ftyp', 'wav': lambda b: b.startswith(b'RIFF') and b[8:12] == b'WAVE',
                         'ogg': lambda b: b.startswith(b'OggS')}[format_name]
            assert signature(binary), f'Incorrect real container for {format_name}: {binary!r}'
        assert not (directory / 'cancel.flac').exists()
        archive_reports = []
        for filename in (config['zip'], config['zipAgain']):
            with zipfile.ZipFile(filename) as archive:
                names = archive.namelist()
                assert len(names) == 34 and len(set(name.casefold() for name in names)) == 34, names
                assert archive.testzip() is None
                assert len([name for name in names if name.startswith('中文 同名音频')]) == 2
                assert all(name.endswith('.flac') for name in names)
                single_bytes = Path(config['outputs']['flac']).read_bytes()
                assert archive.read('中文 同名音频.flac') == single_bytes
                archive_reports.append({'path': filename, 'bytes': Path(filename).stat().st_size, 'members': names,
                                        'crc_valid': True, 'single_matches': True})
        assert not list(directory.glob('.MusicUnlock-*.partial'))
        return {'passed': True, 'ui': json.loads((directory / 'saving-ui.json').read_text(encoding='utf-8')),
                'native_dialogs': dialogs, 'archives': archive_reports,
                'containers': list(config['outputs']), 'extensionless_entry': True, 'cancel_leaves_no_file': True}
    finally:
        saves.stop.set()
        saves.thread.join(timeout=5)


def verify(exe: Path | None = None):
    directory = ROOT / 'output/saving-e2e' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    runtime = directory / 'runtime'
    runtime.mkdir()
    receipt, port = directory / 'startup.json', free_port()
    cdp = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'TEMP': str(runtime), 'TMP': str(runtime),
           'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': f'--remote-debugging-port={port} --remote-debugging-address=127.0.0.1'}
    if exe is not None:
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        assert shutil.which('ffmpeg', path=env['PATH']) is None and shutil.which('python', path=env['PATH']) is None
    command = [str(exe)] if exe else [str(ROOT / '.venv/Scripts/pythonw.exe'), str(ROOT / 'windows_launcher.py')]
    command += ['--startup-report', str(receipt)]
    child = ManagedProcess.launch(command, env=env, show_window=True, log_path=str(directory / 'launch.log'))
    win, window = Win32(), None
    audit = ProcessAudit(win, child.pid)
    report = {'passed': False, 'directory': str(directory), 'executable': str(exe) if exe else 'source',
              'sha256': hashlib.sha256(exe.read_bytes()).hexdigest() if exe else None}
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            audit.sample()
            windows = [w for w in win.windows() if w['visible'] and w['pid'] in audit.pids() and w['title'] == 'MusicUnlock 音乐格式转换']
            if windows and receipt.exists():
                try:
                    pages = [t for t in fetch_json(cdp + '/json/list') if t.get('type') == 'page' and t.get('url', '').startswith('http://127.0.0.1:')]
                    if len(pages) == 1:
                        window, base = windows[0], pages[0]['url'].rstrip('/')
                        break
                except (OSError, ValueError):
                    pass
            assert child.poll() is None, 'Owned app exited before startup'
            time.sleep(.1)
        assert window, 'No owned native window became ready'
        report['no_console'] = assert_no_console(window['pid'])
        report['acceptance'] = run_on_page(cdp, base, win, audit, directory)
        win.close(window['hwnd'])
        assert child.wait(20) == 0
        deadline = time.monotonic() + 20
        while audit.alive() and time.monotonic() < deadline:
            time.sleep(.1)
        assert not audit.alive(), 'Owned processes survived native close'
        report['process_audit'] = audit.finish()
        audit = None
        assert not report['process_audit']['console_processes']
        assert not report['process_audit']['external_browser_processes']
        report['passed'] = True
    finally:
        child.close()
        if audit is not None:
            report['process_audit'] = audit.finish()
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Saving acceptance evidence: {directory / "report.json"}', flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', type=Path, nargs='?')
    args = parser.parse_args()
    verify(args.exe.resolve(strict=True) if args.exe else None)
