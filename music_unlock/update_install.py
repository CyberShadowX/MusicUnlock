"""Prepare a constrained installation manifest without launching any process."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import secrets
import sys

from update_bootstrap import (atomic_json, check_source, process_identity, safe_path,
                              sha256, validate_candidate, verify_pe)


def prepare_install(candidate: dict, app_executable: Path, data_dir: Path, pythonw: Path) -> dict:
    workspace, _, _ = validate_candidate(candidate)
    check_source(candidate)
    target = safe_path(app_executable, exists=True)
    if target.name != 'MusicUnlock.exe' or not target.is_file():
        raise ValueError('Current executable must be MusicUnlock.exe')
    # A frozen caller cannot nominate any other executable with the right name.
    if getattr(sys, 'frozen', False) and target != safe_path(sys.executable, exists=True):
        raise ValueError('Installer target is not the running executable')
    verify_pe(target)
    data = safe_path(data_dir)
    if data != target.parent / 'MusicUnlock-data':
        raise ValueError('MusicUnlock-data must be beside the current executable')
    python = safe_path(pythonw, exists=True)
    if python != workspace / '.venv' / 'Scripts' / 'pythonw.exe':
        raise ValueError('Installer requires the trusted source project pythonw.exe')
    if not os.access(target.parent, os.W_OK):
        raise ValueError('Executable directory is not writable')
    job = safe_path(data / 'updates' / 'install' / candidate['job_id'])
    job.mkdir(parents=True, exist_ok=True)
    safe_path(job, exists=True)
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    helper_source = safe_path(bundle / 'update_bootstrap.py', exists=True)
    helper = safe_path(job / 'update_bootstrap.py')
    shutil.copy2(helper_source, helper)
    manifest_path = safe_path(job / 'manifest.json')
    result_path = safe_path(job / 'result.json')
    handoff_path = safe_path(job / 'parent-held.json')
    handoff_token = secrets.token_hex(32)
    for stale in (result_path, handoff_path):
        if stale.exists():
            stale.unlink()
    manifest = {
        'schema': 1, 'app_executable': str(target), 'data_dir': str(data),
        'parent_pid': os.getpid(), 'parent_identity': process_identity(os.getpid()),
        'current_exe_sha256': sha256(target), 'candidate': dict(candidate),
        'result_path': str(result_path), 'startup_report': str(job / 'startup.json'),
        'backup_path': str(job / 'MusicUnlock.exe.backup'),
        'handoff_token': handoff_token,
    }
    atomic_json(manifest_path, manifest)
    return {'command': [str(python), str(helper), '--manifest', str(manifest_path)],
            'manifest_path': str(manifest_path), 'result_path': str(result_path),
            'handoff_path': str(handoff_path), 'handoff_token': handoff_token}
