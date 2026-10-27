import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()


def pe(path, text):
    content = bytearray(256)
    content[:2] = b'MZ'
    struct.pack_into('<I', content, 60, 64)
    content[64:68] = b'PE\0\0'
    struct.pack_into('<H', content, 88, 0x20b)
    struct.pack_into('<H', content, 156, 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content + text.encode())


@pytest.fixture
def install_fixture(tmp_path):
    workspace = tmp_path / 'source workspace'
    workspace.mkdir()
    git(workspace, 'init', '-b', 'main')
    git(workspace, 'config', 'user.email', 'fixture@example.invalid')
    git(workspace, 'config', 'user.name', 'Fixture')
    (workspace / '.gitignore').write_text('.musicunlock-updates/\n.venv/\npersonal*\n personal*\n')
    (workspace / 'source.txt').write_text('old')
    git(workspace, 'add', '.')
    git(workspace, 'commit', '-m', 'base')
    expected = git(workspace, 'rev-parse', 'HEAD')
    worktree = workspace / '.musicunlock-updates' / 'fixture-123' / 'source'
    git(workspace, 'worktree', 'add', '--detach', str(worktree), expected)
    (worktree / 'source.txt').write_text('new')
    git(worktree, 'add', '.')
    git(worktree, 'commit', '-m', 'prepared')
    prepared = git(worktree, 'rev-parse', 'HEAD')
    artifact = worktree / 'dist' / 'MusicUnlock.exe'
    pe(artifact, 'NEW')
    target = tmp_path / 'copied app' / 'MusicUnlock.exe'
    pe(target, 'OLD')
    pythonw = workspace / '.venv/Scripts/pythonw.exe'
    pythonw.parent.mkdir(parents=True)
    shutil.copyfile(Path(sys.executable).with_name('pythonw.exe'), pythonw)
    candidate = dict(job_id='fixture-123', workspace=str(workspace), worktree=str(worktree),
                     expected_head=expected, expected_branch='main', prepared_head=prepared,
                     upstream_revision=prepared, artifact=str(artifact),
                     artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    return candidate, target, target.parent / 'MusicUnlock-data', pythonw


def prepare(fixture):
    from music_unlock.update_install import prepare_install
    return prepare_install(*fixture)


def test_prepare_manifest_is_local_self_contained_and_does_not_launch(install_fixture):
    result = prepare(install_fixture)
    manifest = json.loads(Path(result['manifest_path']).read_text())
    assert manifest['schema'] == 1
    assert manifest['parent_pid'] == os.getpid()
    assert manifest['parent_identity'].isdigit()
    assert manifest['current_exe_sha256'] == hashlib.sha256(install_fixture[1].read_bytes()).hexdigest()
    assert result['command'] == [str(install_fixture[3]), str(Path(result['manifest_path']).with_name('update_bootstrap.py')),
                                 '--manifest', result['manifest_path']]
    assert Path(result['command'][1]).exists()
    assert Path(result['result_path']).is_relative_to(install_fixture[2])
    assert git(install_fixture[0]['workspace'], 'rev-parse', 'HEAD') == install_fixture[0]['expected_head']


@pytest.mark.parametrize('change', ['artifact', 'hash', 'head', 'job', 'target', 'data'])
def test_prepare_rejects_invalid_candidate_and_destination(install_fixture, change, tmp_path):
    candidate, target, data, pythonw = install_fixture
    if change == 'artifact':
        candidate['artifact'] = str(target)
    elif change == 'hash':
        candidate['artifact_sha256'] = 'G' * 64
    elif change == 'head':
        candidate['prepared_head'] = '--help'
    elif change == 'job':
        candidate['job_id'] = '../escape'
    elif change == 'target':
        renamed = target.with_name('other.exe')
        target.rename(renamed)
        target = renamed
    elif change == 'data':
        data = tmp_path / 'elsewhere'
    from music_unlock.update_install import prepare_install
    with pytest.raises((ValueError, OSError)):
        prepare_install(candidate, target, data, pythonw)


class ParentExited:
    def __init__(self, *args): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def wait(self, timeout): return True


class ReceiptRunner:
    launches = []
    launch_options = []
    fail = False
    terminated = False
    def __init__(self, command, **kwargs):
        self.command = command
        self.closed = False
        type(self).launches.append(command)
        type(self).launch_options.append(kwargs)
        if '--startup-report' in command:
            receipt = Path(command[-1])
            manifest = json.loads(receipt.with_name('manifest.json').read_text())
            receipt.write_text(json.dumps({'status': 'failed' if self.fail else 'ready',
                'app_name': 'MusicUnlock', 'source_revision': manifest['candidate']['prepared_head']}))
    def poll(self): return None
    def release(self): pass
    def terminate(self): type(self).terminated = True
    def wait(self, timeout): return 1
    def close(self): self.closed = True


def test_fixture_transaction_advances_source_and_swaps_binary(install_fixture):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    ReceiptRunner.launches = []
    ReceiptRunner.launch_options = []
    ReceiptRunner.fail = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'installed'
    assert install_fixture[1].read_bytes().endswith(b'NEW')
    assert git(install_fixture[0]['workspace'], 'rev-parse', 'HEAD') == install_fixture[0]['prepared_head']
    assert len(ReceiptRunner.launches) == 1
    assert ReceiptRunner.launch_options[0]['show_window'] is True
    assert json.loads(Path(result['result_path']).read_text())['status'] == 'installed'


def test_failed_startup_restores_binary_and_relaunches_old_without_rewinding_source(install_fixture):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    ReceiptRunner.launches = []
    ReceiptRunner.launch_options = []
    ReceiptRunner.fail = True
    ReceiptRunner.terminated = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'rolled_back'
    assert outcome['source_advanced'] is True
    assert install_fixture[1].read_bytes().endswith(b'OLD')
    assert git(install_fixture[0]['workspace'], 'rev-parse', 'HEAD') == install_fixture[0]['prepared_head']
    assert ReceiptRunner.terminated
    assert len(ReceiptRunner.launches) == 2
    assert all(options['show_window'] is True for options in ReceiptRunner.launch_options)


@pytest.mark.parametrize('change', ['old_binary', 'dirty_source', 'branch', 'result_escape', 'schema', 'artifact_hash'])
def test_transaction_rechecks_immutable_manifest_and_source(install_fixture, change, tmp_path):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    manifest_path = Path(result['manifest_path'])
    manifest = json.loads(manifest_path.read_text())
    candidate, target, _, _ = install_fixture
    if change == 'old_binary':
        target.write_bytes(target.read_bytes() + b'changed')
    elif change == 'dirty_source':
        (Path(candidate['workspace']) / 'source.txt').write_text('user edit')
    elif change == 'branch':
        git(candidate['workspace'], 'switch', '-c', 'other')
    elif change == 'result_escape':
        manifest['result_path'] = str(tmp_path / 'escape.json')
    elif change == 'schema':
        manifest['schema'] = 2
    elif change == 'artifact_hash':
        Path(candidate['artifact']).write_bytes(b'not candidate')
    manifest_path.write_text(json.dumps(manifest))
    ReceiptRunner.launches = []
    outcome = bootstrap.install(manifest_path, runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert ReceiptRunner.launches == []
    assert git(candidate['workspace'], 'rev-parse', 'HEAD') == candidate['expected_head']
    assert not (tmp_path / 'escape.json').exists()


def test_prepared_head_retry_is_allowed_without_destructive_reset(install_fixture):
    import update_bootstrap as bootstrap
    candidate = install_fixture[0]
    git(candidate['workspace'], 'merge', '--ff-only', candidate['prepared_head'])
    result = prepare(install_fixture)
    ReceiptRunner.fail = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'installed'
    assert outcome['source_advanced']


def test_changed_source_head_is_rejected_and_user_commit_preserved(install_fixture):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    candidate = install_fixture[0]
    workspace = Path(candidate['workspace'])
    (workspace / 'source.txt').write_text('user commit')
    git(workspace, 'commit', '-am', 'user commit')
    user_head = git(workspace, 'rev-parse', 'HEAD')
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert git(workspace, 'rev-parse', 'HEAD') == user_head
    assert install_fixture[1].read_bytes().endswith(b'OLD')


def test_result_path_through_directory_junction_is_rejected(install_fixture, tmp_path):
    candidate, target, _, pythonw = install_fixture
    outside = tmp_path / 'outside-data'
    outside.mkdir()
    data = target.parent / 'MusicUnlock-data'
    completed = subprocess.run(['cmd', '/c', 'mklink', '/J', str(data), str(outside)],
                               capture_output=True, text=True,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert completed.returncode == 0, completed.stderr
    from music_unlock.update_install import prepare_install
    with pytest.raises(ValueError, match='Reparse'):
        prepare_install(candidate, target, data, pythonw)
    assert list(outside.iterdir()) == []


def test_bootstrap_is_independent_of_application_imports():
    import ast
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'update_bootstrap.py').read_text())
    imports = {node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports.update(alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    assert imports <= sys.stdlib_module_names | {'__future__'}


def test_validation_failures_record_safe_local_result(install_fixture):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    install_fixture[1].write_bytes(install_fixture[1].read_bytes() + b'changed')
    bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    report = json.loads(Path(result['result_path']).read_text())
    assert report['status'] == 'failed'
    assert 'changed' in report['message']
    assert Path(result['manifest_path']).with_name('install.log').exists()


def test_receipt_with_wrong_revision_is_rejected_and_rollback_occurs(install_fixture):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    class WrongRevision(ReceiptRunner):
        def __init__(self, command, **kwargs):
            super().__init__(command, **kwargs)
            if '--startup-report' in command:
                Path(command[-1]).write_text(json.dumps({'status': 'ready', 'app_name': 'MusicUnlock',
                                                        'source_revision': 'f' * 40}))
    outcome = bootstrap.install(Path(result['manifest_path']), runner=WrongRevision,
                                parent_factory=ParentExited, startup_timeout=.05)
    assert outcome['status'] == 'rolled_back'
    assert install_fixture[1].read_bytes().endswith(b'OLD')


def test_parent_must_exit_before_source_or_binary_can_change(install_fixture):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    class ParentStillAlive(ParentExited):
        def wait(self, timeout): return False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentStillAlive)
    assert outcome['status'] == 'failed'
    assert 'did not exit' in outcome['message']
    assert install_fixture[1].read_bytes().endswith(b'OLD')
    candidate = install_fixture[0]
    assert git(candidate['workspace'], 'rev-parse', 'HEAD') == candidate['expected_head']


def test_parent_handle_is_acquired_before_slow_source_validation(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    held = []
    validate = bootstrap.validate_manifest
    class EarlyParent(ParentExited):
        def __enter__(self):
            held.append(True)
            return self
        def __exit__(self, *args): held.pop()
    def validate_while_held(*args):
        assert held, 'Parent must be held before Git/PE validation can take time'
        return validate(*args)
    monkeypatch.setattr(bootstrap, 'validate_manifest', validate_while_held)
    ReceiptRunner.fail = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=EarlyParent)
    assert outcome['status'] == 'installed'
    assert not held


def test_binary_swap_failure_reports_source_advance_and_preserves_old_binary(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    replace = bootstrap.os.replace
    target = install_fixture[1]
    def fail_swap(source, destination):
        if Path(destination) == target:
            raise PermissionError('fixture locked executable')
        return replace(source, destination)
    monkeypatch.setattr(bootstrap.os, 'replace', fail_swap)
    ReceiptRunner.launches = []
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert outcome['source_advanced'] is True
    assert target.read_bytes().endswith(b'OLD')
    assert Path(result['manifest_path']).with_name('MusicUnlock.exe.backup').read_bytes().endswith(b'OLD')
    assert outcome['rollback_relaunched'] is True
    assert ReceiptRunner.launches == [[str(target)]]


def test_parent_held_receipt_allows_controller_to_close_only_after_validation(install_fixture):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    handoff = Path(result['handoff_path'])
    assert not handoff.exists()
    class HandoffParent(ParentExited):
        def wait(self, timeout):
            data = json.loads(handoff.read_text())
            assert data['status'] == 'parent-held'
            assert data['parent_pid'] == os.getpid()
            assert data['parent_created'].isdigit()
            assert data['handoff_token'] == result['handoff_token']
            return True
    ReceiptRunner.fail = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=HandoffParent)
    assert outcome['status'] == 'installed'


def test_each_prepare_attempt_has_unique_handoff_token(install_fixture):
    first = prepare(install_fixture)
    Path(first['handoff_path']).write_text('stale')
    second = prepare(install_fixture)
    assert len(first['handoff_token']) == 64
    assert second['handoff_token'] != first['handoff_token']
    assert not Path(second['handoff_path']).exists()
    manifest = json.loads(Path(second['manifest_path']).read_text())
    assert manifest['handoff_token'] == second['handoff_token']


@pytest.mark.parametrize('missing', ['workspace', 'artifact', 'expected_head', 'job_id'])
def test_missing_candidate_fields_are_rejected_as_validation_errors(install_fixture, missing):
    install_fixture[0].pop(missing)
    with pytest.raises(ValueError):
        prepare(install_fixture)


@pytest.mark.parametrize('changed', ['source', 'candidate'])
def test_post_parent_exit_validation_failure_reopens_verified_old_app(install_fixture, changed):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    candidate, target, _, _ = install_fixture
    class ExitingParent(ParentExited):
        def wait(self, timeout):
            if changed == 'source':
                (Path(candidate['workspace']) / 'source.txt').write_text('concurrent user edit')
            else:
                Path(candidate['artifact']).write_bytes(b'corrupt')
            return True
    ReceiptRunner.launches = []
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ExitingParent)
    assert outcome['status'] == 'failed'
    assert outcome['rollback_relaunched'] is True
    assert ReceiptRunner.launches == [[str(target)]]
    assert target.read_bytes().endswith(b'OLD')
    if changed == 'source':
        assert (Path(candidate['workspace']) / 'source.txt').read_text() == 'concurrent user edit'


def test_source_fast_forward_holds_head_branch_and_index_against_concurrent_switch(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    candidate, target, _, _ = install_fixture
    workspace = Path(candidate['workspace'])
    git(workspace, 'branch', 'other', candidate['expected_head'])
    result = prepare(install_fixture)
    original_git = bootstrap.git
    switched = []
    def racing_git(root, *args, **kwargs):
        if args and args[0] in ('merge', 'read-tree'):
            attempt = subprocess.run(['git', '-C', str(workspace), 'switch', 'other'],
                                     capture_output=True, text=True,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            switched.append(attempt.returncode)
        return original_git(root, *args, **kwargs)
    monkeypatch.setattr(bootstrap, 'git', racing_git)
    ReceiptRunner.fail = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'installed', outcome
    assert switched and all(code != 0 for code in switched)
    assert git(workspace, 'symbolic-ref', '--short', 'HEAD') == 'main'
    assert git(workspace, 'rev-parse', 'main') == candidate['prepared_head']
    assert git(workspace, 'rev-parse', 'other') == candidate['expected_head']
    assert not git(workspace, 'status', '--porcelain')
    assert (workspace / 'source.txt').read_text() == 'new'


@pytest.mark.parametrize('user_edit', [False, True])
def test_ref_commit_failure_restores_only_owned_checkout_or_preserves_user_edit(install_fixture, monkeypatch, user_edit):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    candidate, target, _, _ = install_fixture
    workspace = Path(candidate['workspace'])
    def failed_commit(self):
        if user_edit:
            (workspace / 'source.txt').write_text('user edit during transaction')
        raise OSError('fixture ref commit failed')
    monkeypatch.setattr(bootstrap._RefTransaction, 'commit', failed_commit)
    ReceiptRunner.launches = []
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert outcome['rollback_relaunched'] is True
    assert git(workspace, 'rev-parse', 'HEAD') == candidate['expected_head']
    assert (workspace / 'source.txt').read_text() == ('user edit during transaction' if user_edit else 'old')
    if user_edit:
        assert outcome['source_recovery_required'] is True
        assert Path(result['manifest_path']).with_name('source-index.original').exists()
        assert Path(result['manifest_path']).with_name('source-index.next').exists()
    else:
        assert not git(workspace, 'status', '--porcelain')
    assert not (workspace / '.git/index.lock').exists()
    assert not (workspace / '.git/HEAD.lock').exists()
    assert target.read_bytes().endswith(b'OLD')


def test_index_finalize_failure_reports_recovery_evidence_and_reopens_old_app(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    candidate, target, _, _ = install_fixture
    workspace = Path(candidate['workspace'])
    original_replace = bootstrap.os.replace
    def failed_index_replace(source, destination):
        if Path(destination) == workspace / '.git/index':
            raise PermissionError('fixture index finalize failed')
        return original_replace(source, destination)
    monkeypatch.setattr(bootstrap.os, 'replace', failed_index_replace)
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert outcome['source_advanced'] is True
    assert outcome['source_recovery_required'] is True
    assert outcome['rollback_relaunched'] is True
    assert git(workspace, 'rev-parse', 'HEAD') == candidate['prepared_head']
    assert (workspace / 'source.txt').read_text() == 'new'
    assert Path(result['manifest_path']).with_name('source-index.next').exists()
    assert not (workspace / '.git/index.lock').exists()
    assert target.read_bytes().endswith(b'OLD')


def test_exact_branch_is_rechecked_after_git_reference_locks_are_prepared(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    candidate = install_fixture[0]
    workspace = Path(candidate['workspace'])
    git(workspace, 'branch', 'other', candidate['expected_head'])
    result = prepare(install_fixture)
    transaction = bootstrap._RefTransaction
    def branch_changed_before_prepare(*args):
        git(workspace, 'symbolic-ref', 'HEAD', 'refs/heads/other')
        return transaction(*args)
    monkeypatch.setattr(bootstrap, '_RefTransaction', branch_changed_before_prepare)
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert outcome['rollback_relaunched'] is True
    assert git(workspace, 'symbolic-ref', '--short', 'HEAD') == 'other'
    assert git(workspace, 'rev-parse', 'other') == candidate['expected_head']
    assert git(workspace, 'rev-parse', 'main') == candidate['expected_head']
    assert (workspace / 'source.txt').read_text() == 'old'
    assert not git(workspace, 'status', '--porcelain')


def test_lost_reference_commit_acknowledgement_never_rewinds_prepared_checkout(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    candidate = install_fixture[0]
    workspace = Path(candidate['workspace'])
    commit = bootstrap._RefTransaction.commit
    def lost_acknowledgement(self):
        commit(self)
        self.committed = False
        raise OSError('fixture lost reference commit acknowledgement')
    monkeypatch.setattr(bootstrap._RefTransaction, 'commit', lost_acknowledgement)
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert outcome['source_advanced'] is True
    assert outcome['source_recovery_required'] is True
    assert outcome['rollback_relaunched'] is True
    assert git(workspace, 'rev-parse', 'HEAD') == candidate['prepared_head']
    assert (workspace / 'source.txt').read_text() == 'new'


@pytest.mark.parametrize('lock_name', ['index.lock', 'HEAD.lock', 'refs/heads/main.lock'])
def test_existing_git_lock_is_preserved_and_old_app_reopened(install_fixture, lock_name):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    candidate = install_fixture[0]
    workspace = Path(candidate['workspace'])
    user_lock = workspace / '.git' / lock_name
    user_lock.write_text('user-owned lock')
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert outcome['rollback_relaunched'] is True
    assert user_lock.read_text() == 'user-owned lock'
    assert git(workspace, 'rev-parse', 'HEAD') == candidate['expected_head']
    assert (workspace / 'source.txt').read_text() == 'old'


def test_partial_checkout_io_failure_preserves_files_and_reports_recovery_evidence(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    candidate = install_fixture[0]
    workspace = Path(candidate['workspace'])
    original_git = bootstrap.git
    def partial_checkout(root, *args, **kwargs):
        if args and args[0] == 'read-tree':
            (workspace / 'source.txt').write_text('partial checkout or concurrent content')
            raise OSError('fixture disk error during checkout')
        return original_git(root, *args, **kwargs)
    monkeypatch.setattr(bootstrap, 'git', partial_checkout)
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['source_recovery_required'] is True
    assert outcome['rollback_relaunched'] is True
    assert (workspace / 'source.txt').read_text() == 'partial checkout or concurrent content'
    assert git(workspace, 'rev-parse', 'HEAD') == candidate['expected_head']


def test_completed_index_swap_never_deletes_a_new_other_process_lock(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    result = prepare(install_fixture)
    workspace = Path(install_fixture[0]['workspace'])
    index = workspace / '.git/index'
    new_user_lock = workspace / '.git/index.lock'
    original_replace = bootstrap.os.replace
    def next_process_takes_lock(source, destination):
        original_replace(source, destination)
        if Path(destination) == index:
            new_user_lock.write_text('next Git operation owns this lock')
    monkeypatch.setattr(bootstrap.os, 'replace', next_process_takes_lock)
    ReceiptRunner.fail = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'installed'
    assert new_user_lock.read_text() == 'next Git operation owns this lock'


@pytest.mark.parametrize('incoming,private', [
    ('personal.txt', 'personal.txt'),
    ('personal-folder/private.txt', 'personal-folder/private.txt'),
    ('personal-dir', 'personal-dir/private.txt'),
    ('personal-root/new.txt', 'personal-root'),
    (' personal.txt', ' personal.txt'),
    ('personal-资料.txt', 'personal-资料.txt'),
])
def test_incoming_tracked_path_never_overwrites_ignored_user_content(install_fixture, incoming, private):
    import update_bootstrap as bootstrap
    candidate, target, _, _ = install_fixture
    workspace, worktree = Path(candidate['workspace']), Path(candidate['worktree'])
    incoming_path = worktree / incoming
    incoming_path.parent.mkdir(parents=True, exist_ok=True)
    incoming_path.write_text('upstream new content', encoding='utf-8')
    git(worktree, 'add', '-f', '--', incoming)
    git(worktree, 'commit', '-m', 'upstream tracks previously ignored path')
    candidate['prepared_head'] = candidate['upstream_revision'] = git(worktree, 'rev-parse', 'HEAD')
    private_path = workspace / private
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_text('MY PRIVATE USER DATA', encoding='utf-8')
    assert not git(workspace, 'status', '--porcelain')
    result = prepare(install_fixture)
    ReceiptRunner.launches = []
    ReceiptRunner.fail = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed', outcome
    assert outcome['rollback_relaunched'] is True
    assert ReceiptRunner.launches == [[str(target)]]
    assert private_path.read_text(encoding='utf-8') == 'MY PRIVATE USER DATA'
    assert git(workspace, 'rev-parse', 'HEAD') == candidate['expected_head']
    assert not git(workspace, 'status', '--porcelain')
    assert target.read_bytes().endswith(b'OLD')


def test_new_tracked_file_can_share_directory_with_unrelated_ignored_user_file(install_fixture):
    import update_bootstrap as bootstrap
    candidate = install_fixture[0]
    workspace, worktree = Path(candidate['workspace']), Path(candidate['worktree'])
    incoming = worktree / 'personal-folder/new.txt'
    incoming.parent.mkdir()
    incoming.write_text('upstream new content')
    git(worktree, 'add', '-f', '--', 'personal-folder/new.txt')
    git(worktree, 'commit', '-m', 'new tracked file in shared directory')
    candidate['prepared_head'] = candidate['upstream_revision'] = git(worktree, 'rev-parse', 'HEAD')
    private = workspace / 'personal-folder/private.txt'
    private.parent.mkdir()
    private.write_text('MY PRIVATE USER DATA')
    result = prepare(install_fixture)
    ReceiptRunner.fail = False
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'installed', outcome
    assert private.read_text() == 'MY PRIVATE USER DATA'
    assert (workspace / 'personal-folder/new.txt').read_text() == 'upstream new content'
    assert not git(workspace, 'status', '--porcelain')


def test_failed_transaction_never_overwrites_new_ignored_file_during_reverse_checkout(install_fixture, monkeypatch):
    import update_bootstrap as bootstrap
    candidate = install_fixture[0]
    workspace, worktree = Path(candidate['workspace']), Path(candidate['worktree'])
    old_file = workspace / 'personal.txt'
    old_file.write_text('old tracked content')
    git(workspace, 'add', '-f', '--', 'personal.txt')
    git(workspace, 'commit', '-m', 'old source tracks personal file')
    candidate['expected_head'] = git(workspace, 'rev-parse', 'HEAD')
    git(worktree, 'merge', '--no-edit', candidate['expected_head'])
    git(worktree, 'rm', '--', 'personal.txt')
    git(worktree, 'commit', '-m', 'upstream removes personal file')
    candidate['prepared_head'] = candidate['upstream_revision'] = git(worktree, 'rev-parse', 'HEAD')
    result = prepare(install_fixture)
    def failed_commit(self):
        assert not old_file.exists()
        old_file.write_text('USER CREATED AFTER CHECKOUT')
        raise OSError('fixture commit failed after user wrote ignored file')
    monkeypatch.setattr(bootstrap._RefTransaction, 'commit', failed_commit)
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert outcome['source_recovery_required'] is True
    assert outcome['rollback_relaunched'] is True
    assert old_file.read_text() == 'USER CREATED AFTER CHECKOUT'
    assert git(workspace, 'rev-parse', 'HEAD') == candidate['expected_head']


@pytest.mark.skipif(os.name != 'nt', reason='Windows ignored junction boundary')
def test_incoming_path_below_ignored_junction_never_writes_external_directory(install_fixture):
    import update_bootstrap as bootstrap
    candidate, target, _, _ = install_fixture
    workspace, worktree = Path(candidate['workspace']), Path(candidate['worktree'])
    incoming = worktree / 'personal-link/new.txt'
    incoming.parent.mkdir()
    incoming.write_text('upstream new content')
    git(worktree, 'add', '-f', '--', 'personal-link/new.txt')
    git(worktree, 'commit', '-m', 'incoming path below ignored junction')
    candidate['prepared_head'] = candidate['upstream_revision'] = git(worktree, 'rev-parse', 'HEAD')
    outside = target.parent / 'private-external'
    outside.mkdir()
    (outside / 'private.txt').write_text('MY PRIVATE USER DATA')
    completed = subprocess.run(['cmd', '/c', 'mklink', '/J', str(workspace / 'personal-link'), str(outside)],
                               capture_output=True, text=True,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert completed.returncode == 0, completed.stderr
    result = prepare(install_fixture)
    outcome = bootstrap.install(Path(result['manifest_path']), runner=ReceiptRunner, parent_factory=ParentExited)
    assert outcome['status'] == 'failed'
    assert outcome['rollback_relaunched'] is True
    assert (outside / 'private.txt').read_text() == 'MY PRIVATE USER DATA'
    assert not (outside / 'new.txt').exists()
    assert git(workspace, 'rev-parse', 'HEAD') == candidate['expected_head']


def test_source_tree_nul_output_preserves_literal_carriage_return_and_newline(install_fixture):
    import update_bootstrap as bootstrap
    candidate = install_fixture[0]
    workspace = Path(candidate['workspace'])
    blob = git(workspace, 'rev-parse', candidate['expected_head'] + ':source.txt')
    filename = ' personal\r\n.txt'
    result = subprocess.run(['git', '-C', str(workspace), 'mktree', '-z'],
                            input=f'100644 blob {blob}\t{filename}\0'.encode('utf-8'),
                            capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr
    tree = result.stdout.decode('ascii').strip()
    output = bootstrap.git(workspace, 'ls-tree', '-r', '-z', '--name-only', tree, preserve_output=True)
    assert output == filename + '\0'
