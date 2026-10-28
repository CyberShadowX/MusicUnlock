"""Build a controlled local source update, then activate it through the real EXE UI.

All repositories, executables and evidence are disposable fixtures under output.
No remote writes, production source advancement or production EXE replacement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_desktop_exe import ProcessAudit, Win32, assert_no_console, fetch_json, free_port
from check_saving_exe import run_on_page
from music_unlock.app_info import UPSTREAM_URL
from music_unlock.branding import validate_branding
from music_unlock.update_manager import UpdateManager
from music_unlock.update_source import GitSource


def git(root, *args):
    return subprocess.check_output([
        'git', '-c', 'core.hooksPath=NUL', '-c', 'core.fsmonitor=false',
        '-c', 'commit.gpgSign=false', '-c', 'user.name=MusicUnlock Fixture',
        '-c', 'user.email=fixture@localhost', '-C', str(root), *args,
    ], text=True, encoding='utf-8', stderr=subprocess.PIPE,
       creationflags=subprocess.CREATE_NO_WINDOW).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def wait_manager(manager, expected, timeout):
    deadline, previous = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        state = manager.snapshot()
        if state['phase'] != previous:
            print(json.dumps(state, ensure_ascii=False), flush=True)
            previous = state['phase']
        if state['phase'] == expected:
            return state
        assert state['phase'] != 'failed', state
        time.sleep(.2)
    raise TimeoutError(manager.snapshot())


def click_real_restart(cdp, report_path):
    code = r'''
const fs=require('fs'),path=require('path');
const [root,cdp,report]=process.argv.slice(1);
const cache=path.join(root,'.cache/npm/_npx');
const modulePath=fs.readdirSync(cache).map(d=>path.join(cache,d,'node_modules/playwright')).find(p=>fs.existsSync(p));
(async()=>{
  const browser=await require(modulePath).chromium.connectOverCDP(cdp,{noDefaults:true});
  try {
    const page=browser.contexts()[0].pages()[0];
    await page.locator('#settingsToggle').click();
    await page.waitForFunction(()=>document.querySelector('#updatePanel').dataset.phase==='ready');
    await page.locator('#restartUpdate').waitFor();
    if(!(await page.locator('#restartUpdate').isEnabled()))throw Error('Real restart is disabled');
    const state=await page.evaluate(()=>window.pywebview.api.get_update_state());
    fs.writeFileSync(report,JSON.stringify({state,realNativeRestart:true,noFakeBridge:true},null,2));
    await page.screenshot({path:path.join(path.dirname(report),'ready-before-restart.png')});
    await page.locator('#restartUpdate').click();
  } finally { await browser.close().catch(()=>{}); }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''
    subprocess.run(['node', '-e', code, str(ROOT), cdp, str(report_path)],
                   check=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)


def verify_installed_palette(cdp, win, audit, window, directory):
    configuration = directory / 'updated-palette-config.json'
    output = directory / 'updated-palette.json'
    configuration.write_text(json.dumps({'cdp': cdp, 'report': str(output),
                                         'menuScreenshot': str(directory / 'updated-gradient-menu.png'),
                                         'hostileLayer': True}), encoding='utf-8')
    subprocess.run(['node', str(ROOT / 'scripts/format_palette_e2e.cjs'), '--config', str(configuration)],
                   check=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    return {'computed': read_json(output), 'rendered_by_app': True}


def verify(release_exe):
    assert os.name == 'nt'
    original = (git(ROOT, 'rev-parse', 'HEAD'), git(ROOT, 'symbolic-ref', '--short', 'HEAD'))
    assert not git(ROOT, 'status', '--porcelain'), 'Commit reviewed code before actual fixture build'
    directory = ROOT / 'output' / 'update-e2e' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    report = {'passed': False, 'scope': 'controlled local upstream + actual candidate build/API + real frozen UI restart',
              'directory': str(directory), 'release_sha256': sha(release_exe)}
    manager = process = audit = build_audit = None
    win = Win32()
    try:
        remote, upstream, workspace = directory / 'upstream.git', directory / 'upstream-edit', directory / 'workspace'
        git(ROOT, 'clone', '--bare', '--no-hardlinks', str(ROOT), str(remote))
        baseline = git(ROOT, 'merge-base', 'HEAD', 'origin/main')
        git(remote, 'update-ref', 'refs/heads/main', baseline)
        git(ROOT, 'clone', '--branch', 'main', str(remote), str(upstream))
        probe = upstream / 'web/static/upstream-probe.js'
        probe.write_text('const upstreamProductLabel = "music-geshizhuanhuan";\n', encoding='utf-8')
        hostile_palette = upstream / 'web/static/upstream-palette-probe.css'
        hostile_css = ('@layer upstream { select#format, select#format option, #formatTrigger, #formatListbox, '
                       '#formatListbox [role="option"] { background: white !important; '
                       'color: white !important; -webkit-text-fill-color: white !important; '
                       'color-scheme: light !important; } }\n')
        hostile_palette.write_text(hostile_css, encoding='utf-8')
        upstream_index = upstream / 'web/static/index.html'
        text = upstream_index.read_text(encoding='utf-8')
        assert text.count('/* 开关 */') == 1
        # Change an original upstream CSS section, away from local head/header
        # customizations, so this remains a genuine conflict-free Git merge.
        upstream_index.write_text(text.replace('/* 开关 */', hostile_css + '\n  /* 开关 */', 1), encoding='utf-8')
        git(upstream, 'add', 'web/static/upstream-probe.js')
        git(upstream, 'add', 'web/static/upstream-palette-probe.css', 'web/static/index.html')
        git(upstream, 'commit', '-m', 'Controlled local source update fixture')
        latest = git(upstream, 'rev-parse', 'HEAD')
        git(remote, 'fetch', str(upstream), 'HEAD:refs/heads/main')
        git(ROOT, 'clone', '--no-hardlinks', str(ROOT), str(workspace))
        git(workspace, 'remote', 'set-url', 'origin', str(remote))
        shutil.copytree(ROOT / 'vendor/ffmpeg', workspace / 'vendor/ffmpeg')
        target = workspace / 'dist/MusicUnlock.exe'
        target.parent.mkdir()
        shutil.copy2(release_exe, target)
        data = target.parent / 'MusicUnlock-data'
        data.mkdir()
        (data / 'update-workspace.json').write_text(json.dumps({'workspace': str(workspace)}), encoding='utf-8')
        subprocess.run([sys.executable, '-m', 'venv', str(workspace / '.venv')], check=True,
                       timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)

        class FixtureSource(GitSource):
            def check(self):
                return {'revision': latest, 'summary': 'Controlled local upstream fixture'}

        manager = UpdateManager(workspace, data, {'source_revision': original[0], 'upstream_revision': baseline},
                                source=FixtureSource(expected_origin=str(remote)))
        manager.check_updates()
        wait_manager(manager, 'available', 60)
        build_audit = ProcessAudit(win, os.getpid())
        manager.start_update()
        wait_manager(manager, 'ready', 7200)
        report['build_process_audit'] = build_audit.finish()
        build_audit = None
        assert not any(window['visible'] for window in report['build_process_audit']['console_windows']), 'Build created a visible console window'
        candidate = manager.candidate()
        assert candidate
        assert git(workspace, 'rev-parse', 'HEAD') == original[0], 'Preparation advanced original fixture source'
        assert sha(target) == report['release_sha256'], 'Preparation replaced current fixture EXE'
        staged = Path(candidate['worktree'])
        assert 'MusicUnlock' in (staged / 'web/static/upstream-probe.js').read_text()
        validate_branding(staged)
        report['candidate'] = candidate
        manager.close()
        manager = None
        # Only this trusted fixture controller substitutes a local upstream.
        # Restore the real provenance URL for production frozen validation;
        # no network fetch/check follows and no production test flag exists.
        git(workspace, 'remote', 'set-url', 'origin', UPSTREAM_URL)
        debug_port = free_port()
        cdp = f'http://127.0.0.1:{debug_port}'
        startup = directory / 'baseline-startup.json'
        env = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1',
               'TEMP': str(directory), 'TMP': str(directory),
               'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': f'--remote-debugging-port={debug_port} --remote-debugging-address=127.0.0.1'}
        for key in list(env):
            if key.startswith('_PYI') or key in {'PYTHONPATH', 'PYTHONHOME'}:
                env.pop(key)
        process = subprocess.Popen([str(target), '--startup-report', str(startup)], cwd=target.parent, env=env)
        audit = ProcessAudit(win, process.pid)
        deadline = time.monotonic() + 90
        while read_json(startup).get('status') != 'ready':
            audit.sample()
            assert process.poll() is None and time.monotonic() < deadline, read_json(startup)
            time.sleep(.1)
        click_real_restart(cdp, directory / 'native-restart.json')
        result_path = data / 'updates/install' / candidate['job_id'] / 'result.json'
        deadline = time.monotonic() + 180
        while not result_path.exists():
            audit.sample()
            assert time.monotonic() < deadline, 'Installer completion timed out'
            time.sleep(.1)
        result = read_json(result_path)
        report['installation'] = result
        assert result['status'] == 'installed', result
        assert process.wait(15) == 0
        assert sha(target) == candidate['artifact_sha256']
        assert sha(result_path.parent / 'MusicUnlock.exe.backup') == report['release_sha256']
        assert git(workspace, 'rev-parse', 'HEAD') == candidate['prepared_head']
        assert not git(workspace, 'status', '--porcelain')
        assert 'MusicUnlock' in (workspace / 'web/static/upstream-probe.js').read_text()
        validate_branding(workspace)
        audit.sample()
        windows = [w for w in win.windows() if w['pid'] in audit.pids() and w['visible']
                   and w['title'] == 'MusicUnlock 音乐格式转换']
        assert len(windows) == 1, windows
        report['new_window'] = windows[0]
        report['no_console'] = assert_no_console(windows[0]['pid'])
        targets = fetch_json(cdp + '/json/list')
        page = next(item for item in targets if item.get('type') == 'page' and item.get('url', '').startswith('http://127.0.0.1:'))
        report['activated_health'] = fetch_json(page['url'].rstrip('/') + '/api/health')
        assert report['activated_health']['source_revision'] == candidate['prepared_head']
        assert report['activated_health']['upstream_revision'] == latest
        assert report['activated_health']['desktop_ready'] is True
        report['updated_palette'] = verify_installed_palette(cdp, win, audit, windows[0], directory)
        saving_directory = directory / 'installed-saving'
        saving_directory.mkdir()
        report['updated_saving'] = run_on_page(cdp, page['url'].rstrip('/'), win, audit, saving_directory)
        win.close(windows[0]['hwnd'])
        deadline = time.monotonic() + 20
        while audit.alive() and time.monotonic() < deadline:
            time.sleep(.1)
        assert not audit.alive(), audit.alive()
        report['process_audit'] = audit.finish()
        audit = None
        assert not report['process_audit']['console_processes']
        assert not report['process_audit']['external_browser_processes']
        assert not any(w['visible'] for w in report['process_audit']['console_windows'])
        report['passed'] = True
        return report
    finally:
        if build_audit:
            report['build_process_audit'] = build_audit.finish()
        if manager:
            manager.close()
        if audit:
            audit.sample()
            for w in win.windows():
                if w['pid'] in audit.pids() and w['visible'] and w['title'] == 'MusicUnlock 音乐格式转换':
                    win.close(w['hwnd'])
            time.sleep(1)
            for item in audit.alive():
                win.terminate_owned(item['pid'], item['created'])
            report['process_audit'] = audit.finish()
        report['original_unchanged'] = original == (git(ROOT, 'rev-parse', 'HEAD'), git(ROOT, 'symbolic-ref', '--short', 'HEAD')) and not git(ROOT, 'status', '--porcelain')
        assert report['original_unchanged']
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Update acceptance evidence: {directory / "report.json"}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release_exe', type=Path)
    arguments = parser.parse_args()
    verify(arguments.release_exe.resolve(strict=True))
