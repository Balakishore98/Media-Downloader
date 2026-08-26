<#
.SYNOPSIS
    Installs MediaForge for the current user and creates its shortcuts.

.DESCRIPTION
    Copies the PyInstaller build from .\dist\MediaForge into
    %LOCALAPPDATA%\Programs\MediaForge, adds Start Menu and Desktop shortcuts,
    registers an entry in Apps & features, and tries to pin it to the taskbar.

    No administrator rights are needed - nothing is written outside the user
    profile.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -NoDesktopShortcut
    .\install.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\MediaForge'),
    [switch]$NoDesktopShortcut,
    [switch]$NoPin,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$appName = 'MediaForge'
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$startLink = Join-Path $startMenu "$appName.lnk"
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) "$appName.lnk"
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appName"
$exePath = Join-Path $InstallDir "$appName.exe"

function New-Shortcut {
    param([string]$Path, [string]$Target, [string]$WorkDir, [string]$Description)
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($Path)
    $lnk.TargetPath = $Target
    $lnk.WorkingDirectory = $WorkDir
    $lnk.IconLocation = "$Target,0"
    $lnk.Description = $Description
    $lnk.Save()
    [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
}

# --------------------------------------------------------------------- remove
if ($Uninstall) {
    Write-Host "== Removing $appName ==" -ForegroundColor Cyan
    Get-Process -Name $appName -ErrorAction SilentlyContinue | Stop-Process -Force
    foreach ($p in @($startLink, $desktopLink)) {
        if (Test-Path $p) { Remove-Item $p -Force; Write-Host "  removed $p" }
    }
    if (Test-Path $uninstallKey) { Remove-Item $uninstallKey -Recurse -Force }
    if (Test-Path $InstallDir) {
        Start-Sleep -Milliseconds 400
        Remove-Item $InstallDir -Recurse -Force
        Write-Host "  removed $InstallDir"
    }
    Write-Host 'Uninstalled. Settings in %LOCALAPPDATA%\MediaForge were kept.' -ForegroundColor Green
    return
}

# -------------------------------------------------------------------- install
# accept either build layout: dist\MediaForge\ (folder) or dist\MediaForge.exe
$folderBuild = Join-Path $PSScriptRoot 'dist\MediaForge'
$singleBuild = Join-Path $PSScriptRoot 'dist\MediaForge.exe'
if (Test-Path $folderBuild) {
    $source = $folderBuild
    $layout = 'folder'
} elseif (Test-Path $singleBuild) {
    $source = $singleBuild
    $layout = 'single-file'
} else {
    throw "No build found in $PSScriptRoot\dist. Run .\build_exe.ps1 (single file) or .\build_exe.ps1 -OneDir (folder) first."
}
Write-Host "  using the $layout build" -ForegroundColor DarkGray

Write-Host "== Installing $appName ==" -ForegroundColor Cyan
Get-Process -Name $appName -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host '  closing the running instance...'
    $_ | Stop-Process -Force
    Start-Sleep -Milliseconds 600
}

if (Test-Path $InstallDir) {
    Write-Host '  replacing the previous install'
    Remove-Item $InstallDir -Recurse -Force
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
if ($layout -eq 'folder') {
    Copy-Item -Path (Join-Path $source '*') -Destination $InstallDir -Recurse -Force
} else {
    Copy-Item -Path $source -Destination $InstallDir -Force
}
$size = [math]::Round(((Get-ChildItem $InstallDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "  copied to $InstallDir ($size MB)" -ForegroundColor Green

New-Shortcut -Path $startLink -Target $exePath -WorkDir $InstallDir -Description 'Media acquisition suite'
Write-Host '  Start Menu shortcut created' -ForegroundColor Green
if (-not $NoDesktopShortcut) {
    New-Shortcut -Path $desktopLink -Target $exePath -WorkDir $InstallDir -Description 'Media acquisition suite'
    Write-Host '  Desktop shortcut created' -ForegroundColor Green
}

New-Item -Path $uninstallKey -Force | Out-Null
$version = (Select-String -Path (Join-Path $PSScriptRoot 'core.py') -Pattern "^APP_VERSION = '(.+)'").Matches[0].Groups[1].Value
Set-ItemProperty $uninstallKey DisplayName    $appName
Set-ItemProperty $uninstallKey DisplayVersion $version
Set-ItemProperty $uninstallKey DisplayIcon    $exePath
Set-ItemProperty $uninstallKey InstallLocation $InstallDir
Set-ItemProperty $uninstallKey EstimatedSize  ([int]($size * 1024)) -Type DWord
Set-ItemProperty $uninstallKey NoModify 1 -Type DWord
Set-ItemProperty $uninstallKey NoRepair 1 -Type DWord
Set-ItemProperty $uninstallKey UninstallString "powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\install.ps1`" -Uninstall"
Write-Host '  registered in Apps & features' -ForegroundColor Green

# ------------------------------------------------------------------- taskbar
if (-not $NoPin) {
    Write-Host '  attempting taskbar pin...'
    $pinned = $false
    try {
        $shell = New-Object -ComObject Shell.Application
        $folder = $shell.Namespace($startMenu)
        $item = $folder.ParseName("$appName.lnk")
        # the verb is localised, so match on the accelerator-stripped text
        foreach ($verb in $item.Verbs()) {
            $name = $verb.Name -replace '&', ''
            if ($name -match 'taskbar' -and $name -notmatch 'Unpin') {
                $verb.DoIt()
                $pinned = $true
                break
            }
        }
    } catch {
        $pinned = $false
    }

    if ($pinned) {
        Write-Host '  pinned to the taskbar' -ForegroundColor Green
    } else {
        Write-Warning @'
Windows 11 blocks apps from pinning themselves to the taskbar.
Pin it by hand - it is two clicks:
  Press Start, type MediaForge, right-click it -> Pin to taskbar
'@
    }
}

Write-Host ''
Write-Host "Installed -> $exePath" -ForegroundColor Green
Write-Host 'Launch it from the Start Menu, the Desktop shortcut, or that path.'
