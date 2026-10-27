# MusicUnlock branding and local source updates

User approved execution with `方案确认，开始执行` after the six-point proposal.
Large task; base c0bc0a10937568aae0a0d138ee712cbfa57b9753, branch
feature/branding-updates, workspace F:\AI\AIProjects\Music Unlock.

## Behavior

All displayed product names (HTML title/header, native window, CLI/help,
messages/version/log labels) are MusicUnlock. Real upstream URLs and required
attribution are preserved. Existing cache data/user music is never deleted.

Header Settings opens a keyboard-accessible secondary software-update panel
using existing styles. Show current build/upstream commit, check status,
Detect Update and Update Software buttons, progress/failure and Restart to
Activate after successful build. No network check at launch. GitHub main
SOURCE commits, not releases, determine updates. Compare remote tip against
the running build's explicit upstream_revision, not its custom local HEAD.

Update Software prepares an isolated worktree from the configured local
workspace HEAD, fetches the checked upstream commit, merges it preserving
local desktop/updater changes, reapplies and validates MusicUnlock branding,
tests and builds a windowed EXE. Conflicts, dirty user source, bad paths,
failed verification/build or unavailable tools fail clearly without replacing
the executable. No remote writes, credentials, global tool installs or hard resets.

Build uses project-local Python3.12/venv, Git, pinned dependencies and staged
FFmpeg; these already exist locally. Settings can select a source workspace
if a copied EXE cannot locate its source. Source/build activity stays within
the project; source worktrees live in ignored .musicunlock-updates.

Restart activation validates a manifest and candidate SHA, waits for the
old app's identity-verified process to exit, fast-forwards ONLY a clean
unchanged expected source branch, backs up the old EXE, atomically replaces
the EXE, launches the candidate and waits for a native startup receipt.
Failure restores/relaunches the old executable where replacement occurred.
Never overwrite concurrent user source edits or hard-reset source history;
source may be ahead if EXE replacement failed, and that state is reported.
Allow retry when source already equals the prepared commit.

The standalone stdlib installer helper is copied outside the PyInstaller
temporary bundle before launch and explicitly survives old-app exit. Its
new-app validation process is owned by a Windows job created before the
child starts, enabling handle/job-based failure cleanup without unrelated
PID termination. Ordinary app close still reaps normal descendants. Startup
receipts are a production installation handshake, not a test-only bypass.

Machine-mutating actions are exposed ONLY through the native JS bridge,
authorized against the exact local page origin/path; no unauthenticated
HTTP update/apply endpoint. Native restart rejects active conversions and
prevents new work once committed to shutdown. No CMD/external browser.

## Acceptance

Core conversion/native saves remain functional. Cover up-to-date, available,
network/rate failure, dirty/changed workspace, conflict, preserved local
desktop/branding, failed build, successful local build, installer rollback,
process identity and source/executable replacement boundaries. Use local Git
fixtures to demonstrate an actual update because current real upstream main
is still 28f4ed0724f85978405d961c0e955266eed383ee. Verify actual GitHub check,
final EXE settings/native flow and candidate/update fixtures without remote
Git writes. Independent task review gates, fixes/re-review and a fresh final
review; controller runs final tests/build and GUI acceptance.
