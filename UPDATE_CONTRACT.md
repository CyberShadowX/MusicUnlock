# MusicUnlock updater implementation contract (v1)

Controller-owned shared interfaces; communicate before changing.

Constants/helpers in `music_unlock.app_info`:
- APP_NAME = "MusicUnlock"
- UPSTREAM_REPOSITORY = "HRuiCcc/music-geshizhuanhuan"
- UPSTREAM_BRANCH = "main"
- UPSTREAM_URL = "https://github.com/HRuiCcc/music-geshizhuanhuan.git"
- `load_build_info() -> dict`: bundled build-info.json, or source build metadata.
Brand helpers in `music_unlock.branding`:
- `apply_branding(root: Path) -> list[str]`: rebrand tracked runtime text files;
  return relative paths actually changed; preserve real upstream URLs.
- `validate_branding(root: Path) -> None`: raise on missing MusicUnlock HTML
  title/h1 or old displayed runtime brand. Do not delete user/cache files.

`music_unlock.update_manager.UpdateManager(workspace: Path | None, data_dir: Path,
 current_info: dict, **dependencies)`:
- `snapshot() -> dict`
- `check_updates() -> dict`: async check, returns initial state.
- `start_update() -> dict`: async prepare/test/build from immutable checked tip.
- `set_workspace(path: Path) -> dict`: validate/configure trusted local source.
- `candidate() -> dict | None`: internal validated ready candidate.
- `close() -> None`: cancel/terminate owned build work, no user-source reset.
Optional dependencies (source/build/runner) are constructor injection, not
runtime URL overrides or production fixture flags.

State fields (always present; additional internal fields allowed):
`app_name, phase, message, current_revision, latest_revision, current_build,
latest_summary, checked_at, build_ready, source_workspace, can_update,
can_restart, log_path`. Revisions are 40-hex strings or empty. Phases:
idle/checking/up_to_date/available/preparing/validating/building/ready/installing/failed.
Network errors are failed checks, never "up to date". No network at construction.

Candidate dict required fields:
`job_id, workspace, expected_head, expected_branch, prepared_head,
upstream_revision, artifact, artifact_sha256` (absolute paths; SHA256 lowercase).
Prepared HEAD must descend from expected local HEAD and include checked
upstream commit. Original source checkout remains unchanged during preparation.

Native JS bridge methods exposed by controller:
`get_update_state()`, `check_updates()`, `start_update()`,
`choose_source_workspace()`, `restart_after_update()`.
No method accepts a client command, URL, SHA or arbitrary install destination.
Authorize every call against the bound window's exact local origin/path.
JS should use these promises, polling only while panel open or work active.
Before bridge ready, disable actions and show Chinese desktop availability.
When queue has waiting/running items, disable restart; backend independently
rejects active conversions. UI has no externally navigating upstream links.

Installer native-side helper:
`music_unlock.update_install.prepare_install(candidate: dict,
 app_executable: Path, data_dir: Path, pythonw: Path) -> dict` returns
`command: list[str], manifest_path: str, result_path: str`.
Also returns fresh `handoff_path` and random `handoff_token`. Before closing the
old native window, controller holds helper's private job and waits for matching
`status=parent-held`, token, parent_pid and parent_created receipt. Only then
clear helper job's kill-on-close flag while retaining the handles, close native
window, then close those handles. A failed window close can still terminate the
held helper job before retry. Timeout/failure kills only helper job and keeps GUI.
Copies standalone update_bootstrap.py into job dir (frozen bundle data lives
at sys._MEIPASS/update_bootstrap.py); writes schema1 manifest including
expected current EXE SHA256, GUI worker parent_pid/creation identity, candidate,
source expected/prepared head/branch and result/startup receipt paths. No launch.
Controller launches command outside its job and then closes its native window.

`update_bootstrap.py --manifest ABS_PATH` stdlib only, logs/result JSON in job
directory; validates all paths and hashes, waits on identity-verified parent,
CAS-checks clean expected source branch (or prepared head on retry), advances
only that branch through a locked fast-forward transaction (Git symbolic HEAD
and branch compare-and-swap plus index protection), backup/atomic EXE
replacement, startup verify/rollback. After verified old-parent exit, failures
must restore/relaunch the old EXE even if replacement had not yet occurred.
New EXE supports `--startup-report ABS_PATH`: writes JSON
`{"status":"ready", "source_revision":..., "app_name":"MusicUnlock"}`
when native loaded, or `{"status":"failed", "message":...}` and exits1
without a blocking error dialog for install launches. Initial errors before GUI
are also reported. Normal user launch still shows actionable native errors.
Installer starts new EXE under a child-only Windows job before execution;
successful ready releases it without killing the app; failures kill only that
job then restore/relaunch old EXE. Never taskkill arbitrary PID trees.

Build script new parameters (controller): `-UpstreamRevision SHA` and
`-WorkspaceRoot ABS_PATH`; staged builder remains `build_windows.ps1` and
outputs its own `dist/MusicUnlock.exe`. Default upstream baseline merge-base
HEAD origin/main; explicit revision must be an ancestor of staged HEAD.
Engine supplies staged FFmpeg with -FFmpegPath/-FFmpegLicensePath from original
workspace vendor/ffmpeg (or validated PATH fallback). Worktree owns its .venv;
cache can be reused safely. Candidate verification includes PE subsystem2,
branding/source tests and actual headless API smoke with synthetic fixtures.
