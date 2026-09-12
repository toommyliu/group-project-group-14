[CmdletBinding()]
param(
    [switch]$AddToPath
)

$ErrorActionPreference = 'Stop'

$Checkout = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

$BinDir = if ($env:OPENGRADER_BIN) { $env:OPENGRADER_BIN }
          else { Join-Path $env:USERPROFILE '.local\bin' }

function Fail([string]$Message) {
    Write-Host "error: $Message" -ForegroundColor Red
    exit 1
}

foreach ($name in @('opengrader.ps1', 'opengrader.cmd')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) {
        Fail "cannot find $name next to this script — run it from a checkout of the repo."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $Checkout 'pyproject.toml') -PathType Leaf)) {
    Fail "$Checkout does not look like an Opengrader checkout."
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'note: uv is not on this PATH.' -ForegroundColor Yellow
    Write-Host '      opengrader will use the project virtualenv, Poetry, or an activated'
    Write-Host '      environment instead. If none of those exist yet, set one up with:'
    Write-Host '        winget install --id astral-sh.uv   (then: uv sync)'
    Write-Host '        ...or:  poetry install'
    Write-Host ''
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

foreach ($name in @('opengrader.ps1', 'opengrader.cmd')) {
    $target = Join-Path $BinDir $name
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        if (-not (Select-String -LiteralPath $target -Pattern 'opengrader\[wrapper\]' -Quiet)) {
            Fail ("$target already exists and was not installed by this script.`n" +
                  "  If it came from ``uv tool install``, remove that first:  uv tool uninstall opengrader`n" +
                  '  Then run this again.')
        }
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $target -Force
}

try {
    $cacheDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'opengrader' }
                else { Join-Path $env:TEMP 'opengrader' }
    New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
    Set-Content -LiteralPath (Join-Path $cacheDir 'checkout-path') -Value $Checkout -Encoding UTF8
} catch {
}

Write-Host ''
Write-Host "Installed: $(Join-Path $BinDir 'opengrader.cmd')"
Write-Host "           $(Join-Path $BinDir 'opengrader.ps1')"
Write-Host "Checkout:  $Checkout"
Write-Host ''

$normalized = $BinDir.TrimEnd('\')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$onPath = ($env:Path -split ';' | ForEach-Object { $_.TrimEnd('\') }) -contains $normalized

if ($onPath) {
    Write-Host 'Try it:'
    Write-Host '  opengrader doctor'
    return
}

if ($AddToPath) {
    $updated = if ([string]::IsNullOrEmpty($userPath)) { $normalized } else { "$userPath;$normalized" }
    [Environment]::SetEnvironmentVariable('Path', $updated, 'User')
    Write-Host "Added to your user PATH: $normalized" -ForegroundColor Green
    Write-Host 'Open a new terminal (this one keeps the old PATH), then run:'
    Write-Host '  opengrader doctor'
} else {
    $currentUserPath = "[Environment]::GetEnvironmentVariable('Path','User')"
    $addLine = "[Environment]::SetEnvironmentVariable('Path', $currentUserPath + ';$normalized', 'User')"

    Write-Host "$normalized is not on your PATH yet." -ForegroundColor Yellow
    Write-Host 'Either re-run this script with -AddToPath, or add it yourself:'
    Write-Host ''
    Write-Host "  $addLine"
    Write-Host ''
    Write-Host 'Then open a new terminal and run:'
    Write-Host '  opengrader doctor'
}
