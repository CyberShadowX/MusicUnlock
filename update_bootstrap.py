"""Standalone, stdlib-only update transaction. Copied outside the frozen bundle.

There is deliberately no import from the application: the old application's
temporary extraction directory may disappear as soon as its worker exits.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import queue
import shutil
import stat
import struct
import subprocess
import sys
import time
import threading


def _win32():
    if os.name != 'nt':
        raise OSError('Windows process handles are required')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    definitions = {
        'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        'GetProcessTimes': ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
        'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        'GetExitCodeProcess': ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        'TerminateProcess': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        'ResumeThread': ([wintypes.HANDLE], wintypes.DWORD),
        'CreateProcessW': ([wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                            ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL),
        'CreateFileW': ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
        'InitializeProcThreadAttributeList': ([ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                               ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
        'UpdateProcThreadAttribute': ([ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
                                      ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL),
        'DeleteProcThreadAttributeList': ([ctypes.c_void_p], None),
    }
    for name, (argtypes, restype) in definitions.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = argtypes, restype
    return kernel


def _checked(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def _identity(kernel, handle):
    created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    _checked(kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel_time), ctypes.byref(user_time)))
    return str((created.dwHighDateTime << 32) | created.dwLowDateTime)


class HeldProcess:
    """A held kernel identity; PID reuse cannot redirect a wait or cleanup."""
    def __init__(self, pid, identity):
        self.kernel = _win32()
        self.handle = _checked(self.kernel.OpenProcess(0x100000 | 0x1000, False, pid))
        try:
            if _identity(self.kernel, self.handle) != identity:
                raise ValueError('Parent process identity changed')
        except BaseException:
            self.close()
            raise

    def wait(self, timeout=None):
        milliseconds = 0xffffffff if timeout is None else min(int(timeout * 1000), 0xfffffffe)
        result = self.kernel.WaitForSingleObject(self.handle, milliseconds)
        if result == 0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def process_identity(pid):
    kernel = _win32()
    handle = _checked(kernel.OpenProcess(0x1000, False, pid))
    try:
        return _identity(kernel, handle)
    finally:
        kernel.CloseHandle(handle)


class _BasicLimit(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount',
                'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimit), ('IoInfo', _IOCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR),
                ('lpTitle', wintypes.LPWSTR), ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
                ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD), ('dwXCountChars', wintypes.DWORD),
                ('dwYCountChars', wintypes.DWORD), ('dwFillAttribute', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                ('lpReserved2', ctypes.c_void_p), ('hStdInput', wintypes.HANDLE),
                ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [('StartupInfo', _StartupInfo), ('lpAttributeList', ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
                ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [('nLength', wintypes.DWORD), ('lpSecurityDescriptor', ctypes.c_void_p),
                ('bInheritHandle', wintypes.BOOL)]


def clean_environment(env=None):
    result = dict(os.environ if env is None else env)
    for name in list(result):
        if name.upper().startswith(('PYTHON', '_PYI')) or (env is None and name.upper().startswith('GIT_')):
            result.pop(name)
    bundle = str(getattr(sys, '_MEIPASS', ''))
    if bundle:
        result['PATH'] = os.pathsep.join(part for part in result.get('PATH', '').split(os.pathsep)
                                        if part and not part.lower().startswith(bundle.lower()))
    return result


_DLL_DIRECTORY_LOCK = threading.RLock()


@contextmanager
def clean_dll_directory():
    """Temporarily remove PyInstaller's inherited global DLL search directory.

    Callers launching other subprocesses can share this same lock/context.
    The directory is restored immediately after spawn, before any child wait.
    """
    if os.name != 'nt':
        yield
        return
    kernel = _win32()
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    with _DLL_DIRECTORY_LOCK:
        previous = ctypes.create_unicode_buffer(32768)
        length = kernel.GetDllDirectoryW(len(previous), previous)
        if length >= len(previous):
            raise ValueError('DLL search directory exceeds Windows path limit')
        _checked(kernel.SetDllDirectoryW(None))
        try:
            yield
        finally:
            _checked(kernel.SetDllDirectoryW(previous.value or None))


def process_creation_flags(breakaway=False):
    return 0x4 | 0x400 | 0x08000000 | (0x01000000 if breakaway else 0)


class WindowsManagedProcess:
    """Spawn suspended, assign to a private kill-on-close job, then resume.

    Only the held job may be terminated. No ancestry enumeration or raw PID kill
    occurs. On successful GUI startup release() removes kill-on-close.
    """
    def __init__(self, command, *, cwd=None, env=None, log_path=None, breakaway=False):
        if not command or any(not isinstance(arg, str) or '\0' in arg for arg in command):
            raise ValueError('Expected a nonempty command argument list')
        self.kernel = _win32()
        self.job = self.process = None
        self.closed = False
        self.pid = None
        thread = None
        inherited = []
        attributes = None
        initialized_attributes = False
        assigned = False
        try:
            self.job = _checked(self.kernel.CreateJobObjectW(None, None))
            self._set_kill_on_close(True)
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(_StartupInfo)
            startup.StartupInfo.dwFlags = 1  # STARTF_USESHOWWINDOW
            startup.StartupInfo.wShowWindow = 0
            flags = process_creation_flags(breakaway)
            if log_path is not None:
                log = Path(log_path)
                log.parent.mkdir(parents=True, exist_ok=True)
                security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, True)
                # OPEN_ALWAYS, append-only access; no shell redirection or quoting.
                output = self.kernel.CreateFileW(str(log), 0x4, 0x1 | 0x2, ctypes.byref(security), 4, 0x80, None)
                if output == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                inherited.append(output)
                stdin = self.kernel.CreateFileW('NUL', 0x80000000, 0x1 | 0x2, ctypes.byref(security), 3, 0x80, None)
                if stdin == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                inherited.append(stdin)
                startup.StartupInfo.dwFlags |= 0x100
                startup.StartupInfo.hStdInput = stdin
                startup.StartupInfo.hStdOutput = startup.StartupInfo.hStdError = output
                size = ctypes.c_size_t()
                self.kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                attributes = ctypes.create_string_buffer(size.value)
                _checked(self.kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)))
                initialized_attributes = True
                handles = (wintypes.HANDLE * len(inherited))(*inherited)
                _checked(self.kernel.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles,
                                                               ctypes.sizeof(handles), None, None))
                startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
                startup.StartupInfo.cb = ctypes.sizeof(startup)
                flags |= 0x80000
            environment = clean_environment(env)
            block = ctypes.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in
                                                    sorted(environment.items(), key=lambda pair: pair[0].upper())) + '\0\0')
            info = _ProcessInfo()
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            executable = shutil.which(command[0]) or command[0]
            with clean_dll_directory():
                _checked(self.kernel.CreateProcessW(executable, command_line, None, None, bool(inherited), flags,
                                                    block, str(cwd) if cwd else None,
                                                    ctypes.byref(startup), ctypes.byref(info)))
                self.process, thread, self.pid = info.hProcess, info.hThread, info.dwProcessId
            if not self.kernel.AssignProcessToJobObject(self.job, self.process):
                raise ctypes.WinError(ctypes.get_last_error())
            assigned = True
            if self.kernel.ResumeThread(thread) == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            if self.process and not assigned:
                # Failed job assignment or DLL search restoration still leaves
                # an exact suspended-child handle; never fall back to a PID.
                self.kernel.TerminateProcess(self.process, 1)
            self.close()
            raise
        finally:
            if thread:
                self.kernel.CloseHandle(thread)
            if initialized_attributes:
                self.kernel.DeleteProcThreadAttributeList(attributes)
            for handle in inherited:
                self.kernel.CloseHandle(handle)

    @classmethod
    def launch(cls, command, cwd=None, env=None, log_path=None, breakaway=False):
        return cls(command, cwd=cwd, env=env, log_path=log_path, breakaway=breakaway)

    def _set_kill_on_close(self, enabled):
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x2000 if enabled else 0
        _checked(self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))

    def poll(self):
        result = self.kernel.WaitForSingleObject(self.process, 0)
        if result == 0x102:
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        _checked(self.kernel.GetExitCodeProcess(self.process, ctypes.byref(code)))
        return code.value

    def wait(self, timeout=None):
        milliseconds = 0xffffffff if timeout is None else min(int(timeout * 1000), 0xfffffffe)
        result = self.kernel.WaitForSingleObject(self.process, milliseconds)
        if result == 0x102:
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def terminate_tree(self, exit_code=1):
        if self.job:
            _checked(self.kernel.TerminateJobObject(self.job, exit_code))

    def terminate(self): self.terminate_tree()

    def allow_survival(self):
        """Allow parent exit while retaining exact job ownership for cancellation."""
        if self.job:
            self._set_kill_on_close(False)

    def release(self):
        if self.job:
            self.allow_survival()
            self.kernel.CloseHandle(self.job)
            self.job = None

    def close(self, release=False):
        if release:
            self.release()
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
        if self.process:
            self.kernel.CloseHandle(self.process)
            self.process = None
        self.closed = True


ManagedProcess = WindowsManagedProcess


def safe_path(value, *, exists=False):
    if not isinstance(value, (str, Path)) or not str(value) or '\0' in str(value):
        raise ValueError('Invalid filesystem path')
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('Only absolute paths are accepted')
    # Inspect every existing component before resolve; junctions are reparse
    # points even where is_symlink() returns False.
    for part in [path, *path.parents]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'Reparse/symlink path is not allowed: {part}')
    resolved = path.resolve(strict=exists)
    if resolved != path:
        raise ValueError('Noncanonical filesystem path')
    return resolved


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_pe(path):
    with Path(path).open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise ValueError('Candidate is not a PE executable')
        offset = struct.unpack_from('<I', header, 60)[0]
        if offset < 64 or offset > 16 * 1024 * 1024:
            raise ValueError('Invalid PE header offset')
        stream.seek(offset)
        pe = stream.read(94)
        if len(pe) < 94 or pe[:4] != b'PE\0\0' or struct.unpack_from('<H', pe, 24)[0] not in (0x10b, 0x20b):
            raise ValueError('Invalid PE executable header')
        if struct.unpack_from('<H', pe, 92)[0] != 2:
            raise ValueError('Only windowed PE subsystem 2 executables may be installed')


def _git_invocation(root, args, environment=None):
    executable = shutil.which('git')
    if not executable:
        raise ValueError('Git is unavailable')
    environment = clean_environment(environment)
    # Preserve local checkout semantics such as the installed core.autocrlf.
    # Credential helpers, hooks and fsmonitor are disabled explicitly below.
    environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    command = [executable, '-c', 'core.hooksPath=NUL', '-c', 'core.fsmonitor=false',
               '-c', 'credential.helper=', '-c', 'core.askPass=', '-C', str(root), *args]
    return command, environment


def git(root, *args, environment=None):
    command, environment = _git_invocation(root, args, environment)
    with clean_dll_directory():
        completed = subprocess.run(command,
                               capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=120, env=environment,
                               creationflags=0x08000000 if os.name == 'nt' else 0)
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or 'Git source validation failed')
    return completed.stdout.strip()


class SourceRecoveryError(ValueError):
    """A failed filesystem/ref operation left preserved recovery evidence."""


class _RefTransaction:
    """Git owns HEAD and its referent locks until commit/abort.

    A dereferenced update of HEAD locks both HEAD and the current branch. The
    caller checks the exact symbolic branch *after* prepare, while both locks
    are held, so a branch change before preparation is safely rejected.
    """
    def __init__(self, workspace, old, new):
        self.prepared = self.committed = False
        self.closed = False
        command, environment = _git_invocation(workspace, ['update-ref', '--stdin'])
        with clean_dll_directory():
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=environment,
                creationflags=0x08000000 if os.name == 'nt' else 0)
        self.responses = queue.Queue()
        def read_responses():
            try:
                for line in self.process.stdout:
                    self.responses.put(line)
            finally:
                self.responses.put(None)
        self.reader = threading.Thread(target=read_responses, daemon=True)
        self.reader.start()
        try:
            self._request('start', b'start: ok')
            # Binary pipes are intentional: Windows text pipes translate LF to
            # CRLF, which update-ref's command protocol rejects.
            self.process.stdin.write(f'update HEAD {new} {old}\n'.encode('ascii'))
            self.process.stdin.flush()
            self._request('prepare', b'prepare: ok')
            self.prepared = True
        except BaseException:
            self.close()
            raise

    def _request(self, command, expected):
        try:
            self.process.stdin.write(command.encode('ascii') + b'\n')
            self.process.stdin.flush()
            response = self.responses.get(timeout=15)
        except (BrokenPipeError, OSError, queue.Empty) as error:
            raise ValueError('Git reference transaction did not acknowledge ' + command) from error
        if response is None or response.strip() != expected:
            message = ''
            if self.process.poll() is not None:
                message = self.process.stderr.read().decode('utf-8', 'replace').strip()
            raise ValueError(message or 'Git reference transaction rejected ' + command)

    def commit(self):
        self._request('commit', b'commit: ok')
        self.committed = True

    def close(self):
        if self.closed:
            return
        if self.process.poll() is None and self.prepared and not self.committed:
            try:
                self._request('abort', b'abort: ok')
            except (ValueError, OSError):
                pass
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # An owned, exact Popen process handle; this command has hooks
            # disabled and never launches a provider, shell or network tool.
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        self.process.stdout.close()
        self.process.stderr.close()
        self.closed = True


def advance_source(candidate, job):
    """A locked fast-forward of source refs, index and working tree.

    Main index.lock excludes checkout/add/commit. Git's prepared ref update
    excludes symbolic HEAD or branch changes through the entire checkout.
    Alternate indexes preserve both old/new evidence if disk errors prevent
    finalization; failure recovery uses read-tree's user-edit protections.
    """
    workspace = Path(candidate['workspace'])
    gitdir = safe_path(git(workspace, 'rev-parse', '--absolute-git-dir'), exists=True)
    index = safe_path(gitdir / 'index', exists=True)
    index_lock = safe_path(gitdir / 'index.lock')
    original_index = safe_path(job / 'source-index.original')
    next_index = safe_path(job / 'source-index.next')
    transaction = None
    checkout_attempted = checked_out = committed = False
    index_lock_consumed = False
    descriptor = os.open(index_lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        shutil.copy2(index, original_index)
        shutil.copy2(index, next_index)
        old = check_source(candidate)
        transaction = _RefTransaction(workspace, old, candidate['prepared_head'])
        # These reads happen after Git holds HEAD plus its dereferenced branch.
        if check_source(candidate) != old:
            raise ValueError('Source changed while acquiring transaction locks')
        environment = clean_environment()
        environment['GIT_INDEX_FILE'] = str(next_index)
        if old != candidate['prepared_head']:
            checkout_attempted = True
            git(workspace, 'read-tree', '-m', '-u', old, candidate['prepared_head'], environment=environment)
            checked_out = True
        # Prepare the exact index content before the ref commit. The held main
        # lock is never replaced until the ref update is acknowledged.
        with os.fdopen(descriptor, 'wb', closefd=False) as stream, next_index.open('rb') as source:
            shutil.copyfileobj(source, stream)
            stream.flush()
            os.fsync(stream.fileno())
        transaction.commit()
        committed = True
        os.close(descriptor)
        descriptor = None
        os.replace(index_lock, index)
        index_lock_consumed = True
        # Main index.lock excludes checkout until refs, index and working tree
        # have all reached their prepared state.
    except Exception as error:
        if transaction is not None:
            # Stop/abort the exact writer before deciding whether an uncertain
            # commit acknowledgement actually advanced the reference.
            transaction.close()
        if checkout_attempted and not checked_out:
            raise SourceRecoveryError(f'{error}; checkout did not complete; verify source without resetting user files; '
                                      f'indexes preserved at {original_index} and {next_index}') from error
        if checked_out and not committed:
            current_branch = git(workspace, 'symbolic-ref', '--short', 'HEAD')
            current = git(workspace, 'rev-parse', 'HEAD')
            if current_branch != candidate['expected_branch']:
                raise SourceRecoveryError(f'{error}; source branch changed during failure recovery; '
                                          f'indexes preserved at {original_index} and {next_index}') from error
            committed = current == candidate['prepared_head'] and old != candidate['prepared_head']
            if current not in (old, candidate['prepared_head']):
                raise SourceRecoveryError(f'{error}; source revision changed during failure recovery; '
                                          f'indexes preserved at {original_index} and {next_index}') from error
        if transaction is not None and not committed and not transaction.committed and checked_out:
            try:
                environment = clean_environment()
                environment['GIT_INDEX_FILE'] = str(next_index)
                # Refuse to overwrite edits made after our forward checkout.
                git(workspace, 'read-tree', '-m', '-u', candidate['prepared_head'], old,
                    environment=environment)
            except Exception as recovery_error:
                raise SourceRecoveryError(f'{error}; source recovery required: {recovery_error}; '
                                          f'indexes preserved at {original_index} and {next_index}') from error
        if committed or (transaction is not None and transaction.committed):
            raise SourceRecoveryError(f'{error}; source fast-forward committed but index finalization failed; '
                                      f'indexes preserved at {original_index} and {next_index}') from error
        raise
    finally:
        if transaction is not None:
            transaction.close()
        if descriptor is not None:
            os.close(descriptor)
        if not index_lock_consumed and index_lock.exists():
            index_lock.unlink()


def validate_candidate(candidate):
    if not isinstance(candidate, dict):
        raise ValueError('Invalid candidate schema')
    for name in ('workspace', 'artifact'):
        if not isinstance(candidate.get(name), str) or not candidate[name]:
            raise ValueError(f'Invalid candidate {name} path')
    for name in ('expected_head', 'prepared_head', 'upstream_revision'):
        if not re.fullmatch('[0-9a-f]{40}', str(candidate.get(name, ''))):
            raise ValueError(f'Invalid candidate {name}')
    if not re.fullmatch('[0-9a-f]{64}', str(candidate.get('artifact_sha256', ''))):
        raise ValueError('Invalid candidate SHA256')
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}', str(candidate.get('job_id', ''))):
        raise ValueError('Invalid update job identity')
    branch = candidate.get('expected_branch')
    if not isinstance(branch, str) or not branch or branch.startswith('-') or '\0' in branch:
        raise ValueError('Invalid source branch')
    git(Path(candidate['workspace']), 'check-ref-format', '--branch', branch)
    workspace = safe_path(candidate['workspace'], exists=True)
    if Path(git(workspace, 'rev-parse', '--show-toplevel')).resolve() != workspace:
        raise ValueError('Source workspace must be repository root')
    worktree = safe_path(workspace / '.musicunlock-updates' / candidate['job_id'] / 'source', exists=True)
    if 'worktree' in candidate and safe_path(candidate['worktree'], exists=True) != worktree:
        raise ValueError('Candidate worktree is outside its job')
    artifact = safe_path(candidate['artifact'], exists=True)
    if artifact != worktree / 'dist' / 'MusicUnlock.exe':
        raise ValueError('Candidate artifact is outside its prepared worktree')
    registered = git(workspace, 'worktree', 'list', '--porcelain')
    registrations = [Path(line[9:]).resolve() for line in registered.splitlines() if line.startswith('worktree ')]
    if worktree not in registrations:
        raise ValueError('Candidate worktree is not registered')
    if git(worktree, 'rev-parse', 'HEAD') != candidate['prepared_head']:
        raise ValueError('Prepared worktree HEAD changed')
    git(workspace, 'merge-base', '--is-ancestor', candidate['expected_head'], candidate['prepared_head'])
    git(workspace, 'merge-base', '--is-ancestor', candidate['upstream_revision'], candidate['prepared_head'])
    if sha256(artifact) != candidate['artifact_sha256']:
        raise ValueError('Candidate artifact SHA256 changed')
    verify_pe(artifact)
    return workspace, worktree, artifact


def check_source(candidate):
    workspace = Path(candidate['workspace'])
    if git(workspace, 'symbolic-ref', '--short', 'HEAD') != candidate['expected_branch']:
        raise ValueError('Source branch changed')
    head = git(workspace, 'rev-parse', 'HEAD')
    if head not in (candidate['expected_head'], candidate['prepared_head']):
        raise ValueError('Source HEAD changed')
    if git(workspace, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Source workspace has user changes')
    return head


def atomic_json(path, data):
    path = safe_path(path)
    temporary = safe_path(path.with_name(path.name + '.tmp'))
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _validate_parent_fields(manifest):
    if type(manifest.get('parent_pid')) is not int or manifest['parent_pid'] <= 0:
        raise ValueError('Invalid parent process ID')
    identity = manifest.get('parent_identity')
    if not isinstance(identity, str) or not re.fullmatch('[0-9]{1,20}', identity):
        raise ValueError('Invalid parent creation identity')
    if not isinstance(manifest.get('handoff_token'), str) or not re.fullmatch('[0-9a-f]{64}', manifest['handoff_token']):
        raise ValueError('Invalid parent handoff token')


def validate_manifest(manifest_path, manifest):
    if not isinstance(manifest, dict) or type(manifest.get('schema')) is not int or manifest['schema'] != 1:
        raise ValueError('Unsupported installation manifest schema')
    candidate = manifest.get('candidate')
    validate_candidate(candidate)
    target = safe_path(manifest.get('app_executable'), exists=True)
    if target.name != 'MusicUnlock.exe' or not target.is_file():
        raise ValueError('Installation target must be the current MusicUnlock.exe')
    data = safe_path(manifest.get('data_dir'), exists=True)
    if data != target.parent / 'MusicUnlock-data':
        raise ValueError('Installation data directory must be beside current EXE')
    job = safe_path(data / 'updates' / 'install' / candidate['job_id'], exists=True)
    if manifest_path != job / 'manifest.json':
        raise ValueError('Manifest is outside its derived installation job')
    for name, filename in (('result_path', 'result.json'), ('startup_report', 'startup.json'),
                           ('backup_path', 'MusicUnlock.exe.backup')):
        if safe_path(manifest.get(name)) != job / filename:
            raise ValueError(f'Invalid installation {name} path')
    current_hash = manifest.get('current_exe_sha256')
    if not isinstance(current_hash, str) or not re.fullmatch('[0-9a-f]{64}', current_hash):
        raise ValueError('Invalid current executable SHA256')
    if sha256(target) != current_hash:
        raise ValueError('Current executable changed since preparation')
    verify_pe(target)
    _validate_parent_fields(manifest)
    check_source(candidate)
    return target, job


def _wait_startup(child, receipt, revision, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        safe_path(receipt)
        if receipt.exists():
            try:
                data = json.loads(receipt.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):
                if data.get('status') == 'failed':
                    raise ValueError(str(data.get('message', 'New application startup failed')))
                if (data.get('status') == 'ready' and data.get('app_name') == 'MusicUnlock'
                        and data.get('source_revision') == revision and child.poll() is None):
                    return
        if child.poll() is not None:
            raise ValueError('New application exited before native startup was ready')
        time.sleep(.1)
    raise ValueError('New application startup receipt timed out')


def _reporting_job(path, manifest):
    """Derive the report location even when later validation must fail.

    The result_path field is never used to choose a write destination.
    """
    if not isinstance(manifest, dict) or not isinstance(manifest.get('candidate'), dict):
        raise ValueError('Invalid manifest reporting identity')
    job_id = manifest['candidate'].get('job_id')
    if not isinstance(job_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', job_id):
        raise ValueError('Invalid manifest job identity')
    target = safe_path(manifest.get('app_executable'), exists=True)
    if target.name != 'MusicUnlock.exe':
        raise ValueError('Invalid reporting executable')
    data = safe_path(manifest.get('data_dir'), exists=True)
    if data != target.parent / 'MusicUnlock-data':
        raise ValueError('Invalid reporting data directory')
    job = safe_path(data / 'updates' / 'install' / job_id, exists=True)
    if path != job / 'manifest.json':
        raise ValueError('Invalid reporting manifest path')
    return job


def _log(job, message):
    if job is None:
        return
    try:
        path = safe_path(job / 'install.log')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(time.strftime('%Y-%m-%dT%H:%M:%S') + ' ' + message.replace('\n', ' ') + '\n')
    except (OSError, ValueError):
        pass


def install(manifest_path, *, runner=WindowsManagedProcess, parent_factory=HeldProcess, startup_timeout=45):
    """Install a validated candidate, preserving source advancement on rollback."""
    outcome = {'status': 'failed', 'message': '', 'source_advanced': False, 'rollback_relaunched': False}
    job = target = manifest = child = parent_context = None
    replaced = False
    parent_exited = False
    try:
        path = safe_path(manifest_path, exists=True)
        manifest = json.loads(path.read_text(encoding='utf-8'))
        job = _reporting_job(path, manifest)
        _validate_parent_fields(manifest)
        parent_context = parent_factory(manifest['parent_pid'], manifest['parent_identity'])
        parent = parent_context.__enter__()
        _log(job, 'Validating installation manifest and immutable candidate')
        target, job = validate_manifest(path, manifest)
        candidate = manifest['candidate']
        outcome['source_advanced'] = (git(Path(candidate['workspace']), 'rev-parse', 'HEAD') == candidate['prepared_head']
                                      and candidate['expected_head'] != candidate['prepared_head'])
        atomic_json(job / 'parent-held.json', {
            'schema': 1, 'status': 'parent-held', 'helper_pid': os.getpid(),
            'parent_pid': manifest['parent_pid'], 'parent_created': manifest['parent_identity'],
            'handoff_token': manifest['handoff_token']})
        _log(job, 'Waiting on identity-verified old application process handle')
        if not parent.wait(120):
            raise ValueError('Old application did not exit; installation cancelled')
        parent_exited = True
        # Parent shutdown can take time; repeat all mutable safety checks.
        validate_manifest(path, manifest)
        artifact = safe_path(candidate['artifact'], exists=True)
        backup = safe_path(manifest['backup_path'])
        if backup.exists() and sha256(backup) != manifest['current_exe_sha256']:
            raise ValueError('Existing backup does not match the old executable')
        if not backup.exists():
            backup_tmp = safe_path(job / 'backup.tmp')
            shutil.copy2(target, backup_tmp)
            if sha256(backup_tmp) != manifest['current_exe_sha256']:
                raise ValueError('Old executable changed while making backup')
            os.replace(backup_tmp, backup)
        staged = safe_path(target.with_name('MusicUnlock.exe.update-' + candidate['job_id']))
        shutil.copy2(artifact, staged)
        if sha256(staged) != candidate['artifact_sha256']:
            raise ValueError('Candidate changed during staging')
        verify_pe(staged)
        advance_source(candidate, job)
        outcome['source_advanced'] = candidate['expected_head'] != candidate['prepared_head']
        if git(Path(candidate['workspace']), 'rev-parse', 'HEAD') != candidate['prepared_head']:
            raise ValueError('Source HEAD changed while advancing update')
        if sha256(target) != manifest['current_exe_sha256']:
            raise ValueError('Current executable changed before replacement')
        os.replace(staged, target)
        replaced = True
        _log(job, 'Source advanced; executable replaced; verifying native startup')
        receipt = safe_path(manifest['startup_report'])
        if receipt.exists():
            receipt.unlink()
        child = runner([str(target), '--startup-report', str(receipt)], cwd=target.parent, env=clean_environment())
        _wait_startup(child, receipt, candidate['prepared_head'], startup_timeout)
        child.release()
        child.close()
        child = None
        outcome.update(status='installed', message='MusicUnlock update installed and native startup verified')
    except Exception as error:
        outcome['message'] = str(error)
        if isinstance(error, SourceRecoveryError):
            outcome['source_recovery_required'] = True
        if parent_exited and isinstance(manifest, dict):
            try:
                candidate = manifest['candidate']
                outcome['source_advanced'] = (git(Path(candidate['workspace']), 'rev-parse', 'HEAD')
                                              == candidate['prepared_head']
                                              and candidate['prepared_head'] != candidate['expected_head'])
            except Exception:
                pass
        if child is not None:
            try:
                child.terminate()
                child.wait(10)
            except Exception as cleanup_error:
                outcome['message'] += f'; child cleanup: {cleanup_error}'
            finally:
                child.close()
        if parent_exited:
            try:
                if replaced:
                    backup = safe_path(manifest['backup_path'], exists=True)
                    if sha256(backup) != manifest['current_exe_sha256']:
                        raise ValueError('Backup hash changed; refusing unsafe rollback')
                    rollback = safe_path(target.with_name('MusicUnlock.exe.rollback-' + manifest['candidate']['job_id']))
                    shutil.copy2(backup, rollback)
                    if sha256(rollback) != manifest['current_exe_sha256']:
                        raise ValueError('Rollback copy hash mismatch')
                    os.replace(rollback, target)
                target = safe_path(target, exists=True)
                if sha256(target) != manifest['current_exe_sha256']:
                    raise ValueError('Old executable was externally changed; refusing unsafe relaunch')
                verify_pe(target)
                old = runner([str(target)], cwd=target.parent, env=clean_environment())
                old.release()
                old.close()
                outcome.update(rollback_relaunched=True)
                if replaced:
                    outcome['status'] = 'rolled_back'
            except Exception as rollback_error:
                outcome['message'] += f'; rollback failed: {rollback_error}'
    finally:
        if parent_context is not None:
            parent_context.__exit__(None, None, None)
    if job is not None:
        _log(job, outcome['status'] + ': ' + outcome['message'])
        try:
            atomic_json(job / 'result.json', outcome)
        except (OSError, ValueError):
            pass
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description='MusicUnlock installation helper')
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args(argv)
    if os.name == 'nt':
        kernel = _win32()
        kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
        kernel.SetDllDirectoryW(None)
    result = install(Path(args.manifest))
    return 0 if result['status'] == 'installed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
