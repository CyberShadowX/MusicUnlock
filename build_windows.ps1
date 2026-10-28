param(
    [string]$FFmpegPath,
    [string]$FFmpegLicensePath,
    [string]$UpstreamRevision,
    [string]$WorkspaceRoot,
    [string]$OutputDirectory = 'dist'
)

$ErrorActionPreference = 'Stop'
$BuildRoot = $PSScriptRoot
$BuildPython = Join-Path $BuildRoot '.venv\Scripts\python.exe'
$PreviousTemp = $env:TEMP
$PreviousTmp = $env:TMP
$PreviousPipCache = $env:PIP_CACHE_DIR

function Invoke-BuildPython {
    param([string[]]$Arguments)
    & $BuildPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

Push-Location -LiteralPath $BuildRoot
try {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'This build requires 64-bit Windows.'
    }
    if (-not $WorkspaceRoot) { $WorkspaceRoot = $BuildRoot }
    $WorkspaceRoot = (Resolve-Path -LiteralPath $WorkspaceRoot).Path
    if (-not (Test-Path -LiteralPath (Join-Path $WorkspaceRoot '.git'))) { throw 'WorkspaceRoot must be the original Git source directory.' }
    $BuildTemp = Join-Path $BuildRoot '.cache\tmp'
    New-Item -ItemType Directory -Force -Path $BuildTemp | Out-Null
    $env:TEMP = $BuildTemp
    $env:TMP = $BuildTemp
    $env:PIP_CACHE_DIR = Join-Path $BuildRoot '.cache\pip'
    if (-not (Test-Path -LiteralPath $BuildPython)) {
        & py -3.12 -m venv (Join-Path $BuildRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required for building.' }
    }
    Invoke-BuildPython -Arguments @('-c', "import sys, struct; assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8, 'Build requires Python 3.12 x64'")
    Invoke-BuildPython -Arguments @('-m', 'pip', '--disable-pip-version-check', 'install', '-r', 'requirements-build.txt')
    # Nested update worktrees must not nest pytest's Git fixtures again under
    # their already-long build path (Git for Windows has a GIT_DIR limit).
    # mkdtemp owns a fresh, bounded path and never reuses user directories.
    $TestRunRoot = & $BuildPython -c "from pathlib import Path; import sys,tempfile; root=Path(sys.argv[1]).resolve(strict=True); cache=root/'.cache'/'t'; assert cache.resolve().is_relative_to(root), 'Test temp escaped source workspace'; cache.mkdir(parents=True,exist_ok=True); print(tempfile.mkdtemp(prefix='b-',dir=cache))" $WorkspaceRoot
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create a safe short project test directory.' }
    Write-Output "Test temp: $TestRunRoot"
    Invoke-BuildPython -Arguments @('-m', 'pytest', 'tests', '-q', '--basetemp', $TestRunRoot)

    if (-not $FFmpegPath) {
        $FoundFFmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
        if (-not $FoundFFmpeg) { throw 'Supply -FFmpegPath with an installed Windows ffmpeg.exe.' }
        $FFmpegPath = $FoundFFmpeg.Source
    }
    $FFmpegPath = (Resolve-Path -LiteralPath $FFmpegPath).Path
    if (-not $FFmpegLicensePath) {
        $FFmpegLicensePath = Join-Path (Split-Path (Split-Path $FFmpegPath -Parent) -Parent) 'LICENSE'
    }
    $FFmpegLicensePath = (Resolve-Path -LiteralPath $FFmpegLicensePath).Path
    $VendorRoot = Join-Path $BuildRoot 'vendor\ffmpeg'
    New-Item -ItemType Directory -Force -Path $VendorRoot | Out-Null
    $StagedFFmpeg = Join-Path $VendorRoot 'ffmpeg.exe'
    $StagedLicense = Join-Path $VendorRoot 'LICENSE'
    if ($FFmpegPath -ne $StagedFFmpeg) { Copy-Item -LiteralPath $FFmpegPath -Destination $StagedFFmpeg }
    if ($FFmpegLicensePath -ne $StagedLicense) { Copy-Item -LiteralPath $FFmpegLicensePath -Destination $StagedLicense }
    $FFmpegVersion = & $StagedFFmpeg -version
    if ($LASTEXITCODE -ne 0) { throw 'The supplied FFmpeg executable could not run.' }

    $SourceRevision = & git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Run this build from the cloned Git repository.' }
    if (-not $UpstreamRevision) {
        $UpstreamRevision = & git merge-base HEAD origin/main
        if ($LASTEXITCODE -ne 0) { throw 'Cannot determine upstream baseline; supply -UpstreamRevision.' }
    }
    if ($UpstreamRevision -notmatch '^[a-fA-F0-9]{40}$') { throw 'UpstreamRevision must be a full Git commit SHA.' }
    & git merge-base --is-ancestor $UpstreamRevision HEAD
    if ($LASTEXITCODE -ne 0) { throw 'UpstreamRevision must already be included in this source HEAD.' }
    $SourceDirty = [bool](& git status --porcelain)
    $PythonVersion = & $BuildPython -c 'import sys; print(sys.version)'
    $Dependencies = & $BuildPython -m pip freeze
    New-Item -ItemType Directory -Force -Path (Join-Path $BuildRoot 'build') | Out-Null
    $BuildInfo = [ordered]@{
        app_name = 'MusicUnlock'
        built_at = [DateTimeOffset]::Now.ToString('o')
        source_revision = $SourceRevision
        upstream_repository = 'HRuiCcc/music-geshizhuanhuan'
        upstream_branch = 'main'
        upstream_revision = $UpstreamRevision.ToLowerInvariant()
        source_workspace = $WorkspaceRoot
        source_dirty = $SourceDirty
        application_mode = 'native_desktop_window'
        renderer = 'EdgeChromium / installed Windows WebView2'
        python = $PythonVersion
        dependencies = @($Dependencies)
        ffmpeg_version = $FFmpegVersion[0]
        ffmpeg_sha256 = (Get-FileHash -LiteralPath $StagedFFmpeg -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $BuildInfo | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $BuildRoot 'build\build-info.json') -Encoding UTF8
    # Stage a release separately while an existing user instance keeps running.
    $DistRoot = & $BuildPython -c "from pathlib import Path; import sys; root=Path(sys.argv[1]).resolve(strict=True); out=(root/sys.argv[2]).resolve(); assert out.is_relative_to(root) and out != root, 'OutputDirectory must stay inside the source project'; print(out)" $BuildRoot $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Invalid release output directory.' }
    Invoke-BuildPython -Arguments @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', $DistRoot, 'MusicUnlock.spec')
    Copy-Item -LiteralPath (Join-Path $BuildRoot 'WINDOWS使用说明.md') -Destination $DistRoot
    Copy-Item -LiteralPath (Join-Path $BuildRoot 'THIRD_PARTY_NOTICES.txt') -Destination $DistRoot
    Copy-Item -LiteralPath (Join-Path $BuildRoot 'build\build-info.json') -Destination $DistRoot
    $LicenseRoot = Join-Path $DistRoot 'licenses'
    New-Item -ItemType Directory -Force -Path $LicenseRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $BuildRoot 'LICENSE') -Destination (Join-Path $LicenseRoot 'MusicUnlock-LICENSE.txt')
    Copy-Item -LiteralPath $StagedLicense -Destination (Join-Path $LicenseRoot 'FFmpeg-LICENSE.txt')
    $BinaryHash = (Get-FileHash -LiteralPath (Join-Path $DistRoot 'MusicUnlock.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
    "$BinaryHash  MusicUnlock.exe" | Set-Content -LiteralPath (Join-Path $DistRoot 'SHA256SUMS.txt') -Encoding ascii
    Write-Output "Built: $(Join-Path $DistRoot 'MusicUnlock.exe')"
    Write-Output "SHA256: $BinaryHash"
}
finally {
    Pop-Location
    $env:TEMP = $PreviousTemp
    $env:TMP = $PreviousTmp
    $env:PIP_CACHE_DIR = $PreviousPipCache
}
