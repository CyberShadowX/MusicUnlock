# MusicUnlock menu and saving fixes

Approved by the user with `方案确认，开始执行` on 2026-10-28.

## Required behavior

- Replace the operating-system output-format popup with an accessible app-rendered menu. Match the existing dark purple gradient, remove the white outer border, retain readable hover/selection/focus and all existing format values. Preserve the native select as the source of truth and a fallback.
- A batch of 34 successful conversion results can be saved as a valid ZIP through the actual desktop executable. Show preparing/saving/completed/cancelled/failed feedback; do not navigate the main page to a download response. Use disk-backed archives and deterministic unique archive member names. Missing results must be reported rather than silently dropped.
- Native saving supplies the real output filename, a corresponding file-type filter and default extension. Renaming without an extension must retain the actual container suffix. Cover MP3, FLAC, M4A, WAV, OGG, original-container results and ZIP; never invent a different audio format by renaming its suffix.
- Keep MusicUnlock branding and all these customizations after upstream merges and local EXE rebuilds. Reapply owned UI assets/markup where safe, validate the download integration, and reject incompatible candidates before replacement. Retain the previous working executable.
- Do not edit installed third-party packages or global Windows settings. Only the bound local desktop page can invoke saving; JavaScript supplies result IDs, never arbitrary source/destination paths or URLs. Native dialogs alone choose destinations. Saving and update restart/clear must not race.
- Do not close a user's existing instance or discard conversion results for testing. Use isolated fixture instances and existing scoped process-ownership helpers.

## Acceptance

Unit/integration checks for invalid and missing IDs, duplicate and Chinese names, extension handling, cancellation/failure, bounded memory and cleanup. Actual WebView2/WinForms dialogs must save renamed files without explicitly typing extensions. Test a 34-result batch and validate ZIP bytes/count/names; test all output targets. Exercise keyboard and pointer menu interactions and capture only the owned application. Run a controlled upstream update/build/install and repeat branding/menu/saving checks on the installed EXE.
