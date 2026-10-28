# MusicUnlock repository rules

- Keep project work in `F:\AI\AIProjects\Music Unlock`; preserve unrelated changes and follow the user's Controller review and approval rules.
- `origin` remains `https://github.com/HRuiCcc/music-geshizhuanhuan.git`: the application checks this original upstream's `main` branch. Do not repoint origin to the Fork; source-update validation depends on it.
- `fork` is `https://github.com/CyberShadowX/MusicUnlock.git`. The user explicitly authorized synchronizing completed local changes to their Fork when committing/building. After tests, review and build verification, push to `fork/main` by ordinary fast-forward only. Never push to the original author's repository or force-push. If the Fork has diverged, preserve its work and reconcile before pushing.
- A software update merges original upstream source and retains MusicUnlock, the app-rendered gradient format menu and native saving. Keep customization guards/regressions current when deliberately changing protected code.
- Do not add credential use or automatic Git pushes to the running application/build script. Repository synchronization is a development step; source updates remain explicit local builds.
- New releases must pass the Python suite, real native-dialog saving (including extensionless names and 34-result ZIP), and controlled update/build/install acceptance. Retain the previous EXE until the new artifact is verified. Do not commit generated binaries, logs, user audio or build caches.
