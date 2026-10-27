# Desktop window correction

The user clarified and confirmed the desired experience: double-click the
EXE to open an independent application window, no console and no external
browser; closing that window exits the application and local service.
This supersedes the console/browser interaction in the original EXE design.

Reuse the existing converters and HTML inside a native pywebview window
using EdgeChromium. This machine already has Microsoft Edge WebView2
140.0.3485.54. Use that installed Windows component without installing or
modifying system runtimes. Bundle Python, pywebview/.NET interop loaders,
converter dependencies, page assets, KGM key and FFmpeg in the same single EXE.
Keep the native file picker and enable native Save As dialogs for both single
files and ZIP. Normal launch must not call an external browser.

Own a loopback-only server for the window's lifetime. Stop it when the native
event loop ends, including startup failure. Place child processes in an
application-owned Windows job so converter/runtime children cannot outlive
the app. Logs go to MusicUnlock-data next to the EXE, without a console.
Use a unique private WebView profile per instance within that directory.
Preserve legacy --no-browser as an advanced service-only compatibility mode;
it is not the normal user interface. Dynamic ports avoid instance conflicts.

Work at F:\AI\AIProjects\Music Unlock on feature/desktop-window, based on
ae42f05ed431fe89b9000a3b747e5b8bee0911a8. Preserve the old binary in ignored
build history before replacement. No system installs, publication or push.
The user's confirmation authorizes this correction; no repeat plan approval.

Acceptance: source tests, existing exact synthetic conversion smoke against
the actual windowed EXE, and actual native-window E2E. Verify GUI PE subsystem,
no app console/external browser, upload and FLAC conversion in the embedded
view, native single/ZIP save dialogs, and ordinary window close stopping the
service and app tree. Check missing GUI/startup-error cleanup. Independent
task review, then a fresh final reviewer and main-session final verification.
Real user music and other Windows PCs remain outside this local acceptance.
