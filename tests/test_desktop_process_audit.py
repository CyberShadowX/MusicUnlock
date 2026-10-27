"""Ownership regression tests without launching, selecting or killing processes."""
from __future__ import annotations

import threading

import pytest

from scripts.check_desktop_exe import ProcessAudit, Win32


def process(pid: int, parent: int, created: int, name: str = "MusicUnlock.exe") -> dict:
    return {"pid": pid, "parent_pid": parent, "created": created, "name": name}


class SnapshotKernel:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.handles = {}
        self.closed = []

    def OpenProcess(self, _access, _inherit, pid):
        entry = self.snapshot.entries.get(pid)
        if entry is None:
            return None
        handle = object()
        self.handles[handle] = entry
        return handle

    def GetExitCodeProcess(self, handle, code):
        entry = self.handles[handle]
        active = self.snapshot.entries.get(entry["pid"]) is entry and entry.get("active", True)
        code._obj.value = 259 if active else 0
        return True

    def GetProcessTimes(self, handle, created, *_):
        identity = self.handles[handle]["created"]
        created._obj.dwLowDateTime, created._obj.dwHighDateTime = identity & 0xFFFFFFFF, identity >> 32
        return True

    def CloseHandle(self, handle):
        del self.handles[handle]
        self.closed.append(handle)
        return True


class Snapshot(Win32):
    def __init__(self, *entries: dict):
        self.entries = {entry["pid"]: dict(entry) for entry in entries}
        self.kernel = SnapshotKernel(self)

    def processes(self):
        return {pid: {key: value for key, value in entry.items() if key != "created"}
                for pid, entry in self.entries.items()}

    def windows(self):
        return []


def audit(snapshot: Snapshot, *owned: dict) -> ProcessAudit:
    # Exercise production traversal synchronously instead of starting its poller.
    result = ProcessAudit.__new__(ProcessAudit)
    result.win32 = snapshot
    result.root_pid = 100
    result.root_created = owned[0]["created"]
    result.owned = {entry["pid"]: dict(entry) for entry in owned}
    result.handles = {}
    for entry in owned:
        bound = snapshot.bind_process(entry["pid"])
        if bound is not None:
            handle, current = bound
            if current["created"] == entry["created"]:
                result.handles[entry["pid"]] = handle
            else:
                snapshot.kernel.CloseHandle(handle)
    result.console_windows = {}
    result.lock = threading.RLock()
    result.stop = threading.Event()
    result.error = None
    result.thread = threading.Thread()
    result.thread.start()
    result.thread.join()
    return result


def test_reused_historical_helper_does_not_authorize_its_unrelated_child():
    root = process(100, 1, 100)
    old_helper = process(101, 100, 110, "ffmpeg.exe")
    unrelated = process(101, 1, 200, "unrelated-parent.exe")
    personal_editor = process(102, 101, 210, "personal-editor.exe")
    owner = audit(Snapshot(root, unrelated, personal_editor), root, old_helper)

    owner.sample()

    assert 102 not in owner.owned
    assert [entry["pid"] for entry in owner.alive()] == [100]


def test_reused_root_does_not_authorize_unrelated_children():
    old_root = process(100, 1, 100)
    owner = audit(Snapshot(process(100, 1, 200), process(101, 100, 210)), old_root)

    owner.sample()

    assert 101 not in owner.owned
    assert owner.pids() == set()


def test_orphan_older_than_its_reused_parent_pid_is_not_owned():
    root = process(100, 1, 200)
    orphan = process(101, 100, 150, "personal-editor.exe")
    owner = audit(Snapshot(root, orphan), root)

    owner.sample()

    assert 101 not in owner.owned


def test_live_verified_parent_authorizes_newer_multilevel_descendants():
    root = process(100, 1, 100)
    child = process(101, 100, 110)
    grandchild = process(102, 101, 120, "msedgewebview2.exe")
    owner = audit(Snapshot(grandchild, child, root), root)

    owner.sample()

    assert owner.pids() == {100, 101, 102}


def test_genuine_new_child_can_reuse_old_owned_pid_under_verified_parent():
    root = process(100, 1, 100)
    old_child = process(101, 100, 110, "ffmpeg.exe")
    new_child = process(101, 100, 300, "msedgewebview2.exe")
    owner = audit(Snapshot(root, new_child), root, old_child)

    owner.sample()

    assert owner.owned[101] == new_child
    assert owner.pids() == {100, 101}


def test_ui_pid_membership_excludes_dead_or_reused_historical_identities():
    root, old_child, dead_child = process(100, 1, 100), process(101, 100, 110), process(102, 100, 120)
    owner = audit(Snapshot(root, process(101, 1, 300)), root, old_child, dead_child)

    assert owner.pids() == {100}


def test_child_reused_after_snapshot_cannot_borrow_the_old_parent_metadata():
    root = process(100, 1, 100)

    class ReplacedChild(Snapshot):
        def replace(self, pid):
            if pid == 101:
                self.entries[101] = process(101, 999, 300, "personal-editor.exe")

        def creation_time(self, pid):
            self.replace(pid)
            return super().creation_time(pid)

        def bind_process(self, pid):
            self.replace(pid)
            return super().bind_process(pid)

    owner = audit(ReplacedChild(root, process(101, 100, 110, "ffmpeg.exe")), root)

    owner.sample()

    assert 101 not in owner.owned
    assert owner.pids() == {100}


def test_parent_exiting_during_child_binding_rejects_and_closes_candidate():
    root = process(100, 1, 100)

    class ExitingParent(Snapshot):
        def bind_process(self, pid):
            bound = super().bind_process(pid)
            if pid == 101:
                self.entries[100]["active"] = False
            return bound

    snapshot = ExitingParent(root, process(101, 100, 110))
    owner = audit(snapshot, root)

    owner.sample()

    assert 101 not in owner.owned
    assert all(entry["pid"] != 101 for entry in snapshot.kernel.handles.values())
    owner.finish()
    assert snapshot.kernel.handles == {}


def test_finish_closes_all_accepted_process_handles():
    root = process(100, 1, 100)
    snapshot = Snapshot(root, process(101, 100, 110))
    owner = audit(snapshot, root)
    owner.sample()
    assert {entry["pid"] for entry in snapshot.kernel.handles.values()} == {100, 101}

    report = owner.finish()

    assert {entry["pid"] for entry in report["owned_processes"]} == {100, 101}
    assert snapshot.kernel.handles == {}


def test_finish_closes_all_handles_when_monitor_reports_failure():
    root = process(100, 1, 100)
    snapshot = Snapshot(root, process(101, 100, 110))
    owner = audit(snapshot, root)
    owner.sample()
    owner.error = "simulated monitor failure"

    with pytest.raises(AssertionError, match="simulated monitor failure"):
        owner.finish()

    assert snapshot.kernel.handles == {}


class Kernel:
    """A held handle identifies an object, independently of later PID lookups."""
    def __init__(self, created: int = 100, active: bool = True):
        self.current = {"created": created, "active": active}
        self.handles = {}
        self.opened = []
        self.closed = []
        self.terminated = []
        self.replace_after_open = None
        self.termination_error = None

    def OpenProcess(self, access, inherit, pid):
        handle = object()
        self.handles[handle] = self.current
        self.opened.append((handle, access, inherit, pid))
        if self.replace_after_open is not None:
            self.current = self.replace_after_open
        return handle

    def GetExitCodeProcess(self, handle, code):
        code._obj.value = 259 if self.handles[handle]["active"] else 0
        return True

    def GetProcessTimes(self, handle, created, *_):
        identity = self.handles[handle]["created"]
        created._obj.dwLowDateTime = identity & 0xFFFFFFFF
        created._obj.dwHighDateTime = identity >> 32
        return True

    def TerminateProcess(self, handle, _code):
        if self.termination_error:
            raise self.termination_error
        self.terminated.append(self.handles[handle])
        self.handles[handle]["active"] = False
        return True

    def WaitForSingleObject(self, handle, _timeout):
        return 258 if self.handles[handle]["active"] else 0

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


def api(kernel: Kernel) -> Win32:
    result = Win32.__new__(Win32)
    result.kernel = kernel
    return result


def test_bind_process_pairs_fresh_metadata_with_the_same_held_identity():
    kernel = Kernel(created=300)
    win32 = api(kernel)

    def current_metadata():
        assert len(kernel.handles) == 1
        assert kernel.closed == []
        return {101: {"pid": 101, "parent_pid": 999, "name": "personal-editor.exe"}}

    win32.processes = current_metadata
    handle, entry = win32.bind_process(101)
    try:
        assert entry == process(101, 999, 300, "personal-editor.exe")
        assert kernel.closed == []
    finally:
        kernel.CloseHandle(handle)


def test_bind_process_closes_candidate_when_fresh_metadata_is_absent():
    kernel = Kernel()
    win32 = api(kernel)
    win32.processes = lambda: {}

    assert win32.bind_process(101) is None
    assert kernel.closed == [kernel.opened[0][0]]


def test_failure_cleanup_rejects_identity_mismatch_and_closes_handle():
    kernel = Kernel(created=200)

    assert api(kernel).terminate_owned(100, 100) is False
    assert kernel.terminated == []
    assert kernel.closed == [kernel.opened[0][0]]


def test_failure_cleanup_rejects_exited_process_object_and_closes_handle():
    kernel = Kernel(active=False)

    assert api(kernel).terminate_owned(100, 100) is False
    assert kernel.terminated == []
    assert kernel.closed == [kernel.opened[0][0]]


def test_failure_cleanup_terminates_only_the_verified_held_handle():
    kernel = Kernel()
    original = kernel.current
    replacement = {"created": 200, "active": True}
    kernel.replace_after_open = replacement

    assert api(kernel).terminate_owned(100, 100) is True
    assert kernel.terminated == [original]
    assert replacement["active"] is True
    assert len(kernel.opened) == 1
    handle, access, inherit, pid = kernel.opened[0]
    assert pid == 100 and inherit is False
    assert access & 0x1000 and access & 0x0001 and access & 0x100000
    assert kernel.closed == [handle]


def test_failure_cleanup_closes_handle_when_termination_raises():
    kernel = Kernel()
    kernel.termination_error = OSError("simulated termination failure")

    with pytest.raises(OSError, match="simulated termination failure"):
        api(kernel).terminate_owned(100, 100)

    assert kernel.closed == [kernel.opened[0][0]]
