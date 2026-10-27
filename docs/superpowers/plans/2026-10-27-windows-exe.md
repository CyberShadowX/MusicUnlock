# Windows EXE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a directly runnable, self-contained Windows x64 MusicUnlock.exe.

**Architecture:** A small launcher serves the existing Flask app on loopback
and opens the local browser. A PyInstaller spec bundles static resources,
Python dependencies and FFmpeg. The conversion engine keeps its behavior.

**Tech Stack:** Python 3.12, Flask/Werkzeug, PyInstaller, FFmpeg, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-windows-exe-design.md`

## Global Constraints

- Work inside `F:\AI\AIProjects\Music Unlock` on `build/windows-exe`.
- Deliver `dist/MusicUnlock.exe`, including Python and FFmpeg.
- Bind only to `127.0.0.1`; preserve upstream format support.
- No system installs, push, publication or real user music changes.
- The user already authorized execution; internal planning needs no second approval.

## Review Focus

- Launch from a directory containing spaces and Chinese characters.
- Run with Python and FFmpeg removed from PATH.
- Start concurrent instances without an occupied default port breaking startup.
- Browser unavailable: the console URL still permits use.
- Include KGM key, static UI, all crypto modules and FFmpeg in the final file.

### Task 1: Portable executable and repeatable build

**Files:**
- Create `windows_launcher.py`: port validation, loopback binding, browser startup, shutdown.
- Modify `music_unlock/transcode.py`: resolve bundled FFmpeg and suppress child consoles.
- Create `MusicUnlock.spec`, `build_windows.ps1`, `requirements-build.txt`.
- Create `tests/test_windows_packaging.py` and `scripts/smoke_windows_exe.py`.
- Create `WINDOWS使用说明.md`; update `.gitignore` for build artifacts.

**Interfaces:**
- Consumes `web.server.app`, `music_unlock.transcode.resolve_ffmpeg(explicit=None)`.
- Produces `windows_launcher.main(argv=None) -> int` and `dist/MusicUnlock.exe`.
- Smoke command: `.venv\Scripts\python.exe scripts\smoke_windows_exe.py dist\MusicUnlock.exe`.

- [ ] Install project-local dependencies and run `python -m pytest tests -q` as baseline.
- [ ] Add tests proving frozen FFmpeg resolution with empty PATH and a source-launch
  integration test with dynamic ports, a foreign working directory and loopback binding.
- [ ] Run new tests and confirm failures correspond to missing packaging support.
- [ ] Implement the launcher and FFmpeg resolution; run the full test suite.
- [ ] Pin the verified build environment and add the spec, build script and user guide.
- [ ] Build `MusicUnlock.exe`; verify the actual binary with a restricted PATH and
  independent working directory, exact synthetic decode outputs and five transcodes.
- [ ] Independent task reviewer checks the exact diff and artifacts; fix and re-review
  any Critical/Important findings before accepting this task.
- [ ] Commit verified source/build changes locally.
- [ ] Fresh final reviewer checks the deliverable; main session runs fresh tests,
  packaged smoke and appropriate browser E2E before reporting completion.
