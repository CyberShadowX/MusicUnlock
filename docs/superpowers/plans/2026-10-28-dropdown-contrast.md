# Output dropdown contrast and update preservation

User approved implementation on 2026-10-28 and explicitly requires both the
MusicUnlock identity and this palette to survive future source updates.
Repository F:/AI/AIProjects/Music Unlock; branch feature/branding-updates;
base dce481f9977f344b615c29d94e8b64baf7a30942; initially clean. Medium task.

Root cause: the native output select inherits light text, but option backgrounds
and the native control color scheme are unspecified. The supplied Windows
WebView2 screenshot shows a light menu with unreadable light text.

Implementation boundary:
- Keep native select semantics and existing format values.
- Use opaque #10141f menu background, #eef2fa text, dark color-scheme and a
  readable selected state, scoped to the output-format control.
- Keep one canonical owned style block. Reapply it through existing
  apply_branding after every upstream merge, including restoring a removed or
  changed block. validate_branding must require it and the expected select
  structure; incompatibility stops preparation rather than silently regressing.
- The owned block must declare its important palette layer before upstream
  author layers, at the start of head. Important declarations in later layers
  and unlayered styles must not defeat the palette.
- Preserve genuine repository URLs, attribution and user files. No installer,
  process ownership or update API redesign.

Task1: worker owns branding.py, index.html and affected branding/source fixture
tests. Meaningful RED->GREEN for upstream palette removal/change, idempotence,
preserved names/URLs, invalid style/structure rejection; actual Git merge case.
Independent review and fix loop before integration.

Task2: controller owns native/browser acceptance tools, update fixture probe,
shared contract/guide, build and delivery. Check computed control/option colors
and contrast in real WebView2; open the actual native popup and capture it.
Exercise a local upstream change through the real merge/build/install path and
verify the installed app retains name, palette and format selection.

Finish: fresh independent final reviewer; main-session fresh tests/build/API/
native popup/update acceptance, clean local commit, rebuilt windowed EXE and
concise evidence/risks/unverified report. No push/publication/system changes.
