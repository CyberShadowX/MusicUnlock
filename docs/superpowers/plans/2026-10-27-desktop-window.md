# Desktop Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the console/browser EXE with a native desktop window.

**Architecture:** A desktop host owns the local Flask service and pywebview
event loop. A Windows job owns runtime/converter descendants. The same
PyInstaller single file bundles desktop interop modules, with console=False.

**Tech Stack:** Python3.12, Flask/Werkzeug, pywebview6.2.1, installed WebView2,
Python.NET, PyInstaller, FFmpeg, pytest; embedded-CDP/Win32 acceptance tooling.

**Spec:** `docs/superpowers/specs/2026-10-27-desktop-window-design.md`

## Global Constraints

- Default launch: independent native window, no CMD, no external browser.
- Window close stops application-owned service and child processes.
- Bind only to 127.0.0.1 and preserve existing decoder/format behavior.
- Build inside F:\AI\AIProjects\Music Unlock on feature/desktop-window.
- Use installed Windows components; no global installation or remote writes.

## Review Focus

- Windowed builds have no stdout/stderr; logging and startup errors still work.
- Desktop single/ZIP downloads must open a usable Save As dialog.
- Native startup failure must not leave a listening HTTP service.
- Window close must release the listener and application descendants.
- Verify actual GUI modules/resources and empty-PATH/Unicode-path portability.

### Task 1: Desktop deliverable

**Ownership:** Controller owns desktop_runtime.py, windows_launcher.py,
MusicUnlock.spec, requirements-build.txt, build script/notices/guide,
tests/test_desktop_runtime.py, tests/test_windows_packaging.py and
scripts/smoke_windows_exe.py. Isolated acceptance worker owns only
scripts/check_desktop_exe.py, scripts/desktop_e2e.cjs and the legacy lifecycle
entry-point wrapper. No other shared writes.

**Interfaces:** `LocalService(port=0)` context exposes .url; `own_child_processes()`
keeps its Windows job alive until process exit; `run_desktop(data_dir: Path,
port: int = 0) -> int` runs the native GUI and closes its service in finally.
Existing CLI --no-browser and --port remain compatible for API smoke.

- [ ] Write lifecycle/startup-failure/no-external-browser tests and observe RED.
- [ ] Implement desktop lifetime, logging/error dialog, job and GUI host; run suite.
- [ ] Update pinned GUI dependencies, package modules/licenses, windowed flag,
  documentation and existing HTTP smoke endpoint discovery; build actual EXE.
- [ ] Acceptance worker verifies real embedded UI, native dialogs and window close;
  controller reruns accepted tooling and verifies actual artifacts independently.
- [ ] Independent task review; fix and re-review all Critical/Important findings.
- [ ] Commit verified changes; rebuild from a clean source revision.
- [ ] Fresh final reviewer and main-session final tests/build/E2E; report completed
  work, verification, verdict, residual limitations and unverified checks.
