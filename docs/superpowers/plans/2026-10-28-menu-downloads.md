# Menu and download reliability implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Follow each task's test and independent review gate.

**Goal:** Deliver a gradient output menu and reliable native single/34-result ZIP saving, retained by source updates.

**Architecture:** A progressive-enhancement format picker wraps the existing select. A repo-owned desktop save bridge validates result IDs and calls a native save dialog before disk-backed export, exposing progress without main-page navigation. Existing local-update preparation reapplies and validates owned customizations before rebuilding.

**Tech Stack:** Python 3.12, Flask, pywebview/WinForms/WebView2, JavaScript/CSS, pytest, Playwright, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-10-28-menu-downloads-design.md`

## Global Constraints

- All work remains in `F:\AI\AIProjects\Music Unlock`, branch `feature/branding-updates`.
- Preserve existing MusicUnlock branding, upstream URL/provenance, user files and the working executable until verification.
- Do not edit site-packages or expose arbitrary filesystem/URL capabilities to page JavaScript.
- Isolated writes: Task 1 owns UI/branding files; Task 2 owns Python saving/server/launcher; Controller owns integration, acceptance harnesses and docs. No worker commits shared work.
- Native saving bridge contract: `save_results(ids: list[str], archive: bool = False) -> dict`, `get_save_state() -> dict`. State has `phase` in `idle/choosing/preparing/saving/completed/cancelled/failed`, `message`, and optional `completed/total`. A running call returns final state. Reject concurrent saves. All errors/cancellations settle visibly.

## Review Focus

- Large already-compressed audio archives must not accumulate the complete ZIP in RAM or trigger silent browser download restrictions.
- User renames without an extension, uses Chinese/multiple dots, cancels or declines overwrite: correct format, no partial destination or false success.
- Clear, update restart and simultaneous saving cannot delete in-use files or terminate an active save.
- Upstream changes to native select options and hostile CSS must not silently undo menu, branding or saving integration.
- Keyboard focus, Escape, outside click, zoom/viewport and disabled states must remain usable.

### Task 1: Gradient menu and save UI

**Files:** `web/static/index.html`, new `web/static/format_picker.js`, new `web/static/downloads.js`, `music_unlock/branding.py`, `tests/test_branding.py`, focused UI harness as needed.

**Interface:** Keep select `#format` and its change event authoritative; native fallback until enhancement succeeds. Call the native save bridge contract above. Browser-only fallback uses explicit downloads with feedback; no main-page navigation. Emit save-state events so settings can disable restart.

- [ ] Add meaningful regression checks before implementation.
- [ ] Implement gradient menu with labelled keyboard listbox and strict owned CSS layer.
- [ ] Route individual/ZIP buttons through shared save UI with visible live status, no duplicate operations, unchanged conversion queue.
- [ ] Reapply/validate owned assets and required HTML integration after upstream merge.
- [ ] Run focused tests and real browser interaction checks; independent task review and resolve blocking findings.

### Task 2: Safe native saving and disk-backed archives

**Files:** new `desktop_saves.py`, `web/server.py`, `windows_launcher.py`, Python tests for export/bridge/launcher.

**Interface:** Expose exactly the two save capabilities above in addition to the existing five settings capabilities. Validate bound local page identically to settings. Actual WinForms save dialog sets filename, filter, DefaultExt, AddExtension and overwrite prompting. Result IDs resolve only through the live result registry.

- [ ] Reproduce current save behavior on isolated fixtures; record confirmed defects separately from hypotheses.
- [ ] Add failing regression tests for extension/default-filter, 34 files, duplicate/missing IDs, cancellation/failure/atomic output and lifecycle races.
- [ ] Implement scoped native dialog and progress-bearing bridge plus disk-backed ZIPs and reliable cleanup.
- [ ] Coordinate active export admission with clear/update restart; release gates on all outcomes.
- [ ] Run relevant tests; independent task review and resolve blocking findings.

### Task 3: Integrated acceptance, update preservation and release

**Files:** `scripts/check_desktop_exe.py`, `scripts/desktop_e2e.cjs`, `scripts/format_palette_e2e.cjs`, `scripts/check_update_exe.py`, supporting acceptance/tests/docs as necessary.

- [ ] Integrate reviewed tasks, extend candidate customization guards and end-to-end assertions.
- [ ] Actual source/native preview: 34-result ZIP, default and omitted-extension native saves, all five output targets, cancelled/missing/duplicate/Chinese cases, gradient-menu keyboard/pointer/viewport screenshot.
- [ ] Commit verified source locally, build console-free EXE, run fresh main-session tests and packaged API/native E2E.
- [ ] Controlled upstream source change -> local build -> native restart/install -> repeat customization checks; retain old EXE and prove original workspace not altered by fixture update.
- [ ] Fresh independent whole-change review; fix/re-review any Critical/Important findings, repeat affected checks, report exact artifact and residual limitations.
