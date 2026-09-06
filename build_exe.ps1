<#
.SYNOPSIS
    Builds a standalone Windows executable of MediaForge.

.EXAMPLE
    .\build_exe.ps1
    .\build_exe.ps1 -IncludeFFmpeg        # bundle the ffmpeg/ffprobe found on PATH
    .\build_exe.ps1 -OneDir               # folder build, noticeably faster startup
#>
[CmdletBinding()]
param(
    [switch]$IncludeFFmpeg,
    [switch]$OneDir
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$appName = 'MediaForge'

Write-Host '== Checking build dependencies ==' -ForegroundColor Cyan
python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing PyInstaller...' -ForegroundColor Yellow
    python -m pip install --upgrade pyinstaller
}
python -c "import yt_dlp" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing the extraction engine...' -ForegroundColor Yellow
    python -m pip install --upgrade yt-dlp
}

$pyiArgs = @(
    '--noconfirm'
    '--clean'
    '--windowed'
    '--name', $appName
    '--collect-submodules', 'yt_dlp'
    '--hidden-import', 'yt_dlp.compat._legacy'
    '--hidden-import', 'yt_dlp.utils._legacy'
    '--add-data', 'icon.ico;.'
)
if (Test-Path 'icon.ico') { $pyiArgs += @('--icon', 'icon.ico') }
if ($OneDir) { $pyiArgs += '--onedir' } else { $pyiArgs += '--onefile' }

if ($IncludeFFmpeg) {
    # Prefer vendor\ if it has been populated: those builds are roughly half the
    # size of a full static ffmpeg, which matters a lot inside a one-file exe.
    $vendor = Join-Path $PSScriptRoot 'vendor'
    if (Test-Path (Join-Path $vendor 'ffmpeg.exe')) {
        $ffmpeg = Join-Path $vendor 'ffmpeg.exe'
        $ffprobe = Join-Path $vendor 'ffprobe.exe'
        if (-not (Test-Path $ffprobe)) { $ffprobe = $null }
        Write-Host '  using the compact build in vendor' -ForegroundColor DarkGray
    } else {
        $ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
        $ffprobe = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
    }
    if ($ffmpeg) {
        Write-Host "Bundling $ffmpeg" -ForegroundColor Green
        $pyiArgs += @('--add-binary', "$ffmpeg;.")
    } else {
        Write-Warning 'ffmpeg not found on PATH - skipping.'
    }
    if ($ffprobe) {
        Write-Host "Bundling $ffprobe" -ForegroundColor Green
        $pyiArgs += @('--add-binary', "$ffprobe;.")
    }
}

$pyiArgs += 'app.py'

Write-Host '== Building ==' -ForegroundColor Cyan
# PyInstaller logs progress to stderr; with EAP=Stop PowerShell 5.1 would treat
# the first log line as a terminating error, so relax it around the call only.
$previousEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
python -m PyInstaller @pyiArgs
$code = $LASTEXITCODE
$ErrorActionPreference = $previousEAP
if ($code -ne 0) { throw "PyInstaller failed with exit code $code" }

# PyInstaller's scratch folder holds a stub exe with no Python DLL beside it.
# Leaving it around invites double-clicking the wrong file, so bin it.
if (Test-Path 'build') { Remove-Item 'build' -Recurse -Force -ErrorAction SilentlyContinue }

$out = if ($OneDir) { "dist\$appName\$appName.exe" } else { "dist\$appName.exe" }
Write-Host ''
Write-Host "Done -> $((Resolve-Path $out).Path)" -ForegroundColor Green
