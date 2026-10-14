param(
    # Build the browser app now, from this checkout, so the first "Open in
    # browser" a user presses doesn't have to. Optional and best effort: no
    # switch, no npm, or a failed build all fall back to what happens
    # without this — classic offers to build it itself, once, on first use.
    [switch]$BuildWeb
)

$ErrorActionPreference = 'Stop'

$Src  = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Root = Join-Path $env:LOCALAPPDATA 'Opengrader'

if (-not (Test-Path (Join-Path $Src 'pyproject.toml'))) {
    Write-Host 'error: run this from a repo checkout (packaging\install.ps1)' -ForegroundColor Red
    exit 1
}

if ($BuildWeb) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        $webSrc = Join-Path $Src 'src\opengrader\web'
        Write-Host 'Building the browser app (needs the internet once)...'
        & $npm.Source --prefix $webSrc ci
        if ($LASTEXITCODE -eq 0) {
            & $npm.Source --prefix $webSrc run build
        }
        if ($LASTEXITCODE -eq 0) {
            Write-Host '  OK Browser app built'
        } else {
            Write-Host '  WARN Browser app build failed - continuing without it (classic will offer to build it later)' -ForegroundColor Yellow
        }
        $global:LASTEXITCODE = 0
    } else {
        Write-Host '  WARN -BuildWeb given but npm was not found - continuing without it' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host "Installing Opengrader to: $Root"

New-Item -ItemType Directory -Force -Path (Join-Path $Root 'app') | Out-Null
# Keep a browser app built in the installed copy unless this checkout has one.
$keepWebDist = @()
if (-not (Test-Path (Join-Path $Src 'src\opengrader\web\dist\index.html'))) {
    $keepWebDist = @((Join-Path $Root 'app\src\opengrader\web\dist'))
}
robocopy $Src (Join-Path $Root 'app') /MIR /NFL /NDL /NJH /NJS /NP `
    /XD .git .venv __pycache__ .pytest_cache .tui_cache out node_modules @keepWebDist | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "error: file copy failed (robocopy exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
$global:LASTEXITCODE = 0

Copy-Item (Join-Path $Src 'packaging\bootstrap.ps1') (Join-Path $Root 'bootstrap.ps1') -Force

Set-Content -Path (Join-Path $Root 'source-path') -Value $Src
Write-Host '  OK Application files copied'

$Bootstrap = Join-Path $Root 'bootstrap.ps1'
$Icon      = Join-Path $Root 'app\packaging\icons\opengrader.ico'
$Desktop   = [Environment]::GetFolderPath('Desktop')
$LnkPath   = Join-Path $Desktop 'Opengrader.lnk'

$psArgs = "-NoLogo -ExecutionPolicy Bypass -File `"$Bootstrap`""
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($LnkPath)
if (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    $lnk.TargetPath = 'wt.exe'
    $lnk.Arguments  = "powershell.exe $psArgs"
} else {
    $lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments  = $psArgs
}
$lnk.WorkingDirectory = Join-Path $Root 'app'
$lnk.Description      = 'Opengrader - local exam grading'
if (Test-Path $Icon) { $lnk.IconLocation = "$Icon,0" }
$lnk.Save()

Write-Host "  OK Shortcut: $LnkPath"
Write-Host ''
Write-Host 'Done. Double-click the Opengrader icon on your Desktop to launch.'
Write-Host 'First launch installs Python + dependencies (one-time, ~1-2 min).'
Write-Host ''
