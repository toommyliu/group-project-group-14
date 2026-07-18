# =============================================================================
# Opengrader one-shot installer (Windows)
#
# Run once from a repo checkout (no admin rights needed):
#   powershell -ExecutionPolicy Bypass -File packaging\install.ps1
#
# It copies the application to  %LOCALAPPDATA%\Opengrader\app  and creates an
# "Opengrader.lnk" shortcut on the Desktop with the bundled .ico. The shortcut
# opens Windows Terminal when available (better fonts/graphics), otherwise a
# classic PowerShell console, running bootstrap.ps1.
#
# Re-running is safe: it refreshes the copy and regenerates the shortcut.
# =============================================================================
$ErrorActionPreference = 'Stop'

$Src  = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path   # repo root
$Root = Join-Path $env:LOCALAPPDATA 'Opengrader'

if (-not (Test-Path (Join-Path $Src 'pyproject.toml'))) {
    Write-Host 'error: run this from a repo checkout (packaging\install.ps1)' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host "Installing Opengrader to: $Root"

# --- 1. Copy application files ------------------------------------------------
# robocopy /MIR keeps re-installs clean; exclude VCS, venvs, caches.
# NB: robocopy exit codes 0-7 all mean success (>=8 is a real failure).
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'app') | Out-Null
robocopy $Src (Join-Path $Root 'app') /MIR /NFL /NDL /NJH /NJS /NP `
    /XD .git .venv __pycache__ .pytest_cache .tui_cache out | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "error: file copy failed (robocopy exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
$global:LASTEXITCODE = 0

# The shortcut targets this copy of bootstrap.ps1 at the install root, whose
# "installed layout" detection finds the app in .\app.
Copy-Item (Join-Path $Src 'packaging\bootstrap.ps1') (Join-Path $Root 'bootstrap.ps1') -Force

# Record where we installed from: bootstrap re-syncs from this checkout on
# every launch (when it still exists), so the desktop icon always runs the
# latest code without re-running the installer.
Set-Content -Path (Join-Path $Root 'source-path') -Value $Src
Write-Host '  OK Application files copied'

# --- 2. Desktop shortcut ------------------------------------------------------
$Bootstrap = Join-Path $Root 'bootstrap.ps1'
$Icon      = Join-Path $Root 'app\packaging\icons\opengrader.ico'
$Desktop   = [Environment]::GetFolderPath('Desktop')
$LnkPath   = Join-Path $Desktop 'Opengrader.lnk'

$psArgs = "-NoLogo -ExecutionPolicy Bypass -File `"$Bootstrap`""
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($LnkPath)
if (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    # Windows Terminal hosts the console (modern rendering); it launches
    # PowerShell inside itself running the bootstrap script.
    $lnk.TargetPath = 'wt.exe'
    $lnk.Arguments  = "powershell.exe $psArgs"
} else {
    # Plain PowerShell shortcut still opens its own console window.
    $lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments  = $psArgs
}
$lnk.WorkingDirectory = Join-Path $Root 'app'
$lnk.Description      = 'Opengrader - local exam grading'
if (Test-Path $Icon) { $lnk.IconLocation = "$Icon,0" }   # attach custom .ico
$lnk.Save()

Write-Host "  OK Shortcut: $LnkPath"
Write-Host ''
Write-Host 'Done. Double-click the Opengrader icon on your Desktop to launch.'
Write-Host 'First launch installs Python + dependencies (one-time, ~1-2 min).'
Write-Host ''
