# =============================================================================
# Opengrader bootstrap / launcher (Windows PowerShell 5.1+)
#
# Entry point executed by the desktop shortcut. On every launch it:
#   1. Ensures `uv` is installed (winget, falling back to the official installer)
#   2. Ensures Python 3.12 is available (uv downloads a managed build)
#   3. Ensures project dependencies are synced from uv.lock
#   4. Ensures Tesseract OCR is installed (best-effort, non-fatal)
# then launches the TUI in this console window. Every step is a fast no-op
# once satisfied, so subsequent launches start almost immediately.
#
# Works from two layouts:
#   - installed:  <root>\bootstrap.ps1  with the project in  <root>\app\
#   - repo:       <repo>\packaging\bootstrap.ps1
# =============================================================================
$ErrorActionPreference = 'Stop'

# --- Locate the application ---------------------------------------------------
if (Test-Path (Join-Path $PSScriptRoot 'app\pyproject.toml')) {
    $AppDir = Join-Path $PSScriptRoot 'app'            # installed layout
} elseif (Test-Path (Join-Path $PSScriptRoot '..\pyproject.toml')) {
    $AppDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path   # repo layout
} else {
    Write-Host 'error: cannot find Opengrader next to this script' -ForegroundColor Red
    Read-Host 'Press Enter to close'; exit 1
}

# uv installs to %USERPROFILE%\.local\bin; a fresh shortcut console may not
# have it on PATH yet, so add it (and Tesseract's default location) explicitly.
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path;$env:ProgramFiles\Tesseract-OCR"

# Installer output is captured to a log; the console shows only progress.
$Log = Join-Path $env:TEMP 'opengrader-bootstrap.log'
Set-Content -Path $Log -Value ''

# Installed layout only: install.ps1 records the checkout it copied from in
# <root>\source-path. When that checkout still exists, mirror it before
# launching so the desktop icon always runs the latest code.
$SyncSource = $null
$sourcePathFile = Join-Path $PSScriptRoot 'source-path'
if (Test-Path $sourcePathFile) {
    $candidate = (Get-Content $sourcePathFile -First 1).Trim()
    if ($candidate -and (Test-Path (Join-Path $candidate 'pyproject.toml'))) {
        $SyncSource = $candidate
    }
}

$script:Step = 0
$Total = if ($SyncSource) { 5 } else { 4 }

function Fail([string]$Title) {
    Write-Progress -Activity 'Opengrader setup' -Completed
    Write-Host "  x $Title failed" -ForegroundColor Red
    Write-Host "  Last lines of $($Log):"
    Get-Content $Log -Tail 15 | ForEach-Object { Write-Host "    $_" }
    # Keep the window open so a double-clicking user can read the error.
    Read-Host 'Press Enter to close'
    exit 1
}

# Run a quiet setup step behind Write-Progress, logging its output.
function Invoke-Step([string]$Title, [scriptblock]$Action) {
    $script:Step++
    Write-Progress -Activity 'Opengrader setup' -Status $Title `
        -PercentComplete (($script:Step - 1) / $Total * 100)
    try {
        & $Action *>> $Log
        if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { Fail $Title }
    } catch { $_ | Out-File $Log -Append; Fail $Title }
    Write-Host "  [$script:Step/$Total] OK $Title" -ForegroundColor Green
}

function Skip-Step([string]$Title) {
    $script:Step++
    Write-Host "  [$script:Step/$Total] OK $Title" -ForegroundColor Green
}

Write-Host ''
Write-Host '  Opengrader - checking environment'
Write-Host ''

# --- Step 0 (installed layout only): refresh from the source checkout ---------
# Mirrors the recorded checkout into the local copy (same excludes as the
# installer; .venv is protected by /XD so it survives). Afterwards, stage the
# possibly-updated launcher for the NEXT run - PowerShell has already read
# this whole file, so replacing it is safe.
if ($SyncSource) {
    Invoke-Step 'Refreshing application files' {
        robocopy $SyncSource $AppDir /MIR /NFL /NDL /NJH /NJS /NP `
            /XD .git .venv __pycache__ .pytest_cache .tui_cache out
        # robocopy exit codes 0-7 all mean success
        if ($LASTEXITCODE -lt 8) { $global:LASTEXITCODE = 0 }
    }
    $updatedLauncher = Join-Path $AppDir 'packaging\bootstrap.ps1'
    if (Test-Path $updatedLauncher) {
        Copy-Item $updatedLauncher (Join-Path $PSScriptRoot 'bootstrap.ps1') -Force
    }
}

# --- Step 1: uv ---------------------------------------------------------------
# uv is the single tool we bootstrap ourselves; it then owns the Python
# runtime and all pip dependencies, pinned by uv.lock.
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Skip-Step 'uv package manager'
} else {
    Invoke-Step 'Installing uv package manager' {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id astral-sh.uv --silent --accept-source-agreements --accept-package-agreements
        } else {
            # Official installer; TLS 1.2 for Windows PowerShell 5.1
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        }
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail 'uv not on PATH after install' }
}

# --- Step 2: Python 3.12 ------------------------------------------------------
# Opengrader pins >=3.12,<3.13; uv fetches a managed build without touching
# any system Python. No-op when already present.
Invoke-Step 'Python 3.12 runtime' { uv python install 3.12 }

# --- Step 3: project dependencies --------------------------------------------
# --frozen: exactly what uv.lock records. --no-dev: skip test/dev tooling.
Invoke-Step 'Project dependencies' { uv sync --frozen --no-dev --project $AppDir }

# --- Step 4: Tesseract OCR (best-effort) --------------------------------------
# Needed only for scan extraction; the TUI runs fine without it, so a failed
# or unavailable install just warns.
if (Get-Command tesseract -ErrorAction SilentlyContinue) {
    Skip-Step 'Tesseract OCR'
} elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    $script:Step++
    Write-Host "  [$script:Step/$Total] Installing Tesseract OCR (winget)..."
    try {
        winget install --id UB-Mannheim.TesseractOCR --silent `
            --accept-source-agreements --accept-package-agreements *>> $Log
    } catch { }
    if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
        Write-Host '        warning: Tesseract unavailable - scan extraction disabled until installed.' -ForegroundColor Yellow
    }
} else {
    $script:Step++
    Write-Host "  [$script:Step/$Total] warning: Tesseract OCR not found (install from UB-Mannheim releases)." -ForegroundColor Yellow
}
Write-Progress -Activity 'Opengrader setup' -Completed

# Ollama is only needed for handwriting extraction - mention, don't install.
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host '  note: Ollama not found - handwriting extraction requires it (https://ollama.com)'
}

Write-Host ''
Write-Host '  Launching Opengrader...'
Write-Host ''

# --- Launch -------------------------------------------------------------------
# --no-sync: we just synced; skip the re-check for a snappy start.
Set-Location $AppDir
uv run --no-sync tui @args
$code = $LASTEXITCODE
if ($code -ne 0) { Read-Host 'Opengrader exited with an error. Press Enter to close' }
exit $code
