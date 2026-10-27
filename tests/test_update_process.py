import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def test_process_module_has_owned_runner():
    from music_unlock import update_process
    assert callable(update_process.WindowsManagedProcess)


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle ownership')
def test_held_parent_uses_creation_identity():
    from music_unlock.update_process import HeldProcess, process_identity
    identity = process_identity(os.getpid())
    with HeldProcess(os.getpid(), identity) as held:
        assert held.wait(0) is False
    with pytest.raises(ValueError, match='identity'):
        HeldProcess(os.getpid(), str(int(identity) + 1))


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership')
def test_job_kills_only_its_child_and_closes_handles(tmp_path):
    from music_unlock.update_process import WindowsManagedProcess
    marker = tmp_path / 'child.txt'
    command = [sys.executable, '-c',
               'import pathlib,time,sys; pathlib.Path(sys.argv[1]).write_text("ready"); time.sleep(60)',
               str(marker)]
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    child = WindowsManagedProcess(command)
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(.02)
        assert marker.exists()
        assert child.poll() is None
        child.terminate()
        assert child.wait(5) != 0
        assert unrelated.poll() is None
    finally:
        child.close()
        unrelated.terminate()
        unrelated.wait(5)
    assert child.closed


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership')
def test_ready_release_preserves_new_child(tmp_path):
    from music_unlock.update_process import WindowsManagedProcess
    child = WindowsManagedProcess([sys.executable, '-c', 'import time; time.sleep(.4)'])
    child.release()
    assert child.wait(5) == 0
    child.close()
    assert child.closed


@pytest.mark.skipif(os.name != 'nt', reason='Windows log handles')
def test_managed_launch_logs_unicode_arguments_and_environment(tmp_path):
    from music_unlock.update_process import ManagedProcess
    log = tmp_path / '日志.txt'
    argument = '带空格的路径 \\ "quoted"'
    env = dict(os.environ, MUSIC_UNLOCK_FIXTURE='环境', GIT_TERMINAL_PROMPT='0')
    script = ('import os,sys; os.write(1, (sys.argv[1]+os.environ["MUSIC_UNLOCK_FIXTURE"]'
              '+os.environ["GIT_TERMINAL_PROMPT"]).encode("utf-8")); os.write(2,b"stderr")')
    child = ManagedProcess.launch([sys.executable, '-c', script, argument], env=env, log_path=log)
    try:
        assert child.wait(10) == 0
    finally:
        child.close()
    assert log.read_text(encoding='utf-8') == argument + '环境0stderr'


@pytest.mark.skipif(os.name != 'nt', reason='Windows descendant job membership')
def test_private_job_also_owns_grandchild_before_parent_termination(tmp_path):
    from music_unlock.update_process import HeldProcess, ManagedProcess, process_identity
    marker = tmp_path / 'grandchild.txt'
    grandchild = ('import pathlib,os,time; pathlib.Path(' + repr(str(marker)) +
                  ').write_text(str(os.getpid())); time.sleep(60)')
    command = [sys.executable, '-c',
               'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",sys.argv[1]]); time.sleep(60)',
               grandchild]
    child = ManagedProcess.launch(command)
    try:
        for _ in range(200):
            if marker.exists(): break
            time.sleep(.02)
        assert marker.exists()
        pid = int(marker.read_text())
        with HeldProcess(pid, process_identity(pid)) as held:
            assert not held.wait(0)
            child.terminate_tree()
            assert held.wait(5)
            assert child.wait(5) != 0
    finally:
        child.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle accounting')
def test_process_handles_do_not_leak_on_success_and_failed_launch():
    import ctypes
    from ctypes import wintypes
    from music_unlock.update_process import ManagedProcess
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    def handles():
        result = wintypes.DWORD()
        assert kernel.GetProcessHandleCount(wintypes.HANDLE(-1), ctypes.byref(result))
        return result.value
    # WinError lazily loads Windows message resources on the first failure.
    # Measure repeated launch cleanup after that one-time runtime initialization.
    with pytest.raises(OSError):
        ManagedProcess.launch(['F:\\not-a-real-executable\\missing.exe'])
    warmup = ManagedProcess.launch([sys.executable, '-c', 'pass'])
    assert warmup.wait(10) == 0
    warmup.close()
    before = handles()
    for _ in range(8):
        child = ManagedProcess.launch([sys.executable, '-c', 'pass'])
        assert child.wait(10) == 0
        child.close()
        with pytest.raises(OSError):
            ManagedProcess.launch(['F:\\not-a-real-executable\\missing.exe'])
    assert handles() <= before + 1


def test_breakaway_flag_is_explicit_and_never_default():
    import inspect
    from music_unlock.update_process import ManagedProcess
    assert inspect.signature(ManagedProcess.launch).parameters['breakaway'].default is False
    import update_bootstrap
    assert update_bootstrap.process_creation_flags(False) & 0x01000000 == 0
    assert update_bootstrap.process_creation_flags(True) & 0x01000000
    assert update_bootstrap.process_creation_flags(True) & 0x4


@pytest.mark.skipif(os.name != 'nt', reason='Windows DLL search state')
def test_child_spawn_clears_and_restores_parent_dll_directory(tmp_path):
    import ctypes
    from ctypes import wintypes
    from music_unlock.update_process import ManagedProcess
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    def directory():
        buffer = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(buffer), buffer)
        return buffer.value
    original = directory()
    log = tmp_path / 'dll.txt'
    child = None
    try:
        assert kernel.SetDllDirectoryW(str(tmp_path))
        command = [sys.executable, '-c',
                   'import ctypes; b=ctypes.create_unicode_buffer(32768); '
                   'ctypes.windll.kernel32.GetDllDirectoryW(32768,b); print(repr(b.value))']
        child = ManagedProcess.launch(command, log_path=log)
        assert directory() == str(tmp_path)
        assert child.wait(10) == 0
        assert log.read_text().strip() == "''"
    finally:
        if child is not None: child.close()
        kernel.SetDllDirectoryW(original or None)


@pytest.mark.skipif(os.name != 'nt', reason='Windows retained job ownership')
def test_allow_survival_retains_job_for_cancellation_and_protects_unrelated_child():
    from music_unlock.update_process import ManagedProcess
    child = ManagedProcess.launch([sys.executable, '-c', 'import time; time.sleep(60)'])
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        job, process = child.job, child.process
        child.allow_survival()
        assert child.job == job and child.process == process
        assert child.poll() is None
        child.terminate_tree()
        assert child.wait(5) != 0
        assert unrelated.poll() is None
    finally:
        child.close()
        unrelated.terminate()
        unrelated.wait(5)
