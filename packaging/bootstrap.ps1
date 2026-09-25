$ErrorActionPreference = 'Stop'

if (Test-Path (Join-Path $PSScriptRoot 'app\pyproject.toml')) {
    $AppDir = Join-Path $PSScriptRoot 'app'
} elseif (Test-Path (Join-Path $PSScriptRoot '..\pyproject.toml')) {
    $AppDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
} else {
    Write-Host 'error: cannot find Opengrader next to this script' -ForegroundColor Red
    Read-Host 'Press Enter to close'; exit 1
}

$env:Path = "$env:USERPROFILE\.local\bin;$env:Path;$env:ProgramFiles\Tesseract-OCR"

$Log = Join-Path $env:TEMP 'opengrader-bootstrap.log'
Set-Content -Path $Log -Value ''

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
    Read-Host 'Press Enter to close'
    exit 1
}

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

if ($SyncSource) {
    Invoke-Step 'Refreshing application files' {
        robocopy $SyncSource $AppDir /MIR /NFL /NDL /NJH /NJS /NP `
            /XD .git .venv __pycache__ .pytest_cache .tui_cache out
        if ($LASTEXITCODE -lt 8) { $global:LASTEXITCODE = 0 }
    }
    $updatedLauncher = Join-Path $AppDir 'packaging\bootstrap.ps1'
    if (Test-Path $updatedLauncher) {
        Copy-Item $updatedLauncher (Join-Path $PSScriptRoot 'bootstrap.ps1') -Force
    }
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Skip-Step 'uv package manager'
} else {
    Invoke-Step 'Installing uv package manager' {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id astral-sh.uv --silent --accept-source-agreements --accept-package-agreements
        } else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        }
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail 'uv not on PATH after install' }
}

Invoke-Step 'Python 3.12 runtime' { uv python install 3.12 }

Invoke-Step 'Project dependencies' { uv sync --frozen --no-dev --project $AppDir }

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

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host '  note: Ollama not found - handwriting extraction requires it (https://ollama.com)'
}

Write-Host ''
Write-Host '  Launching Opengrader...'
Write-Host ''

Set-Location $AppDir
# The icon opens the classic app (Tools & Settings, plain folders, no
# workspace service); `opengrader tui` is the workspace app for power users.
uv run --no-sync opengrader classic @args
$code = $LASTEXITCODE
if ($code -ne 0) { Read-Host 'Opengrader exited with an error. Press Enter to close' }
exit $code
