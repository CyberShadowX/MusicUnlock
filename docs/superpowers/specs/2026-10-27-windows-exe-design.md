# Windows EXE design

The user approved this scope with `方案确认，开始执行` on 2026-10-27.

Build a Windows x64 portable, single-file `dist/MusicUnlock.exe` from the
upstream Python/Flask application. Double-clicking starts a loopback-only
service and opens its existing browser interface. Bundle Python, Python
dependencies, the static UI, the KGM public key and FFmpeg. No Python or
FFmpeg installation is required on the machine running the executable.

Reuse the existing browser UI and decoders. A console shows the local URL
and remains open while the service runs; closing it or pressing Ctrl+C
stops the application. Select an available port automatically, and support
`--port` and `--no-browser` for repeatable verification. Browser failure
must leave a usable URL. Source files remain untouched by conversion.

Approach: PyInstaller one-file packaging preserves the upstream architecture
and meets the approved executable deliverable. Rewriting the browser UI as
a native desktop app would add scope without helping this requirement.

Build only in this repository on `build/windows-exe`, with a project-local
virtual environment and caches. Use the installed Python 3.12 and copy the
installed FFmpeg into ignored build inputs. Preserve license notices and
record the dependency versions, source revision, FFmpeg identity and hashes.
No push, publication, system installation or managed runtime changes.

Acceptance: upstream tests, launcher tests, and an actual packaged-EXE smoke
test from a different working directory with Python/FFmpeg absent from PATH.
Exercise the index, health, synthetic NCM/QMC/KGM/KWM decode and exact
downloads, cover/tags, all transcode targets, ZIP and invalid input.
Independent task review precedes a fresh final review and main-session
verification. Upstream unsupported formats remain unsupported. Real music
files are outside this acceptance run unless supplied by the user.
