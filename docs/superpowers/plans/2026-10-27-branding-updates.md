# Branding and Source Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development; tasks have isolated ownership and independent review gates.

**Goal:** MusicUnlock branding plus Settings source-check/build/restart updates.
**Architecture:** Native settings bridge fronts an async update manager. Git
worktrees preserve local changes; validated build candidates pass to a stdlib
external installer with backups and identity-bound process ownership.
**Tech Stack:** Python3.12/stdlib, Git, Flask, pywebview, PyInstaller, PowerShell.
**Spec:** `docs/superpowers/specs/2026-10-27-branding-updates-design.md`

## Global Constraints

- Preserve native window/no console/no external browser/close-to-exit.
- MusicUnlock stays the displayed name after every update.
- Only explicit clicks contact GitHub or modify source/build/install state.
- No hard resets, remote Git writes, credential use or system tool installs.
- Preserve current executable and unrelated user/source changes on failure.
- No public HTTP machine-update surface; exact-origin native bridge only.

## Review Focus

- Running custom commit must not produce false upstream update positives.
- Git conflicts or concurrent edits must not overwrite user source.
- Updater survives parent job exit without leaking ordinary app children.
- Replacement/rollback use verified files and held process/job identities.
- GUI stays responsive and truthful on no-update, network/build/install failures.

### Task 1: Branding, contracts and settings surface

Controller owns app_info.py, branding.py, their tests and runtime name fixes.
UI worker owns web/static/index.html/settings.css/settings.js and UI checks.
Consumes the native bridge/state contract in UPDATE_CONTRACT.md.
- [ ] Add meaningful branding/update-baseline tests and observe RED.
- [ ] Implement shared metadata/branding and existing runtime name changes.
- [ ] Implement settings panel/async bridge UI; validate UI and regressions.
- [ ] Independent review, fix/re-review, then checkpoint.

### Task 2: Source-check and candidate build engine

Engine worker owns music_unlock/update_manager.py, update_source.py and
tests/test_update_source.py/test_update_manager.py only.
- [ ] Tests first for commit comparison, failures, clean source and merge protection.
- [ ] Implement async state and GitHub source check, isolated merge/branding/build.
- [ ] Verify local Git fixture candidate path; independent review and fix loop.

### Task 3: Install/restart transaction

Installer worker owns update_bootstrap.py, music_unlock/update_install.py,
music_unlock/update_process.py and matching test_update_install/process tests.
- [ ] Tests first for manifest/files/source guards, backup/swap/rollback and handles.
- [ ] Implement detached stdlib helper and managed Windows process/job runner.
- [ ] Verify successful and failed fixture installs; independent review/fix loop.

### Task 4: Desktop integration and final executable

Controller owns settings_bridge.py, windows_launcher.py, desktop_runtime.py,
web/server.py, build_windows.ps1, MusicUnlock.spec, existing acceptance tools,
integration tests and guide. Workers do not modify these files.
- [ ] Wire exact-origin bridge, restart gate/helper breakaway, startup receipts.
- [ ] Add upstream/source-workspace metadata and bundle installer helper.
- [ ] Main-session integrated tests and actual controlled source-update fixture.
- [ ] Independent integration review; commit and clean final build.
- [ ] Main final source/API/native settings/save/update acceptance, fresh final
  reviewer and report completed work/evidence/risks/unverified boundaries.
