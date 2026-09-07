# =============================================================================
# Put `opengrader` on your PATH (Windows), so it works from any directory.
#
#   powershell -ExecutionPolicy Bypass -File packaging\install-command.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\install-command.ps1 -AddToPath
#
# The PowerShell twin of packaging/install-command.sh. It installs exactly ONE
# command, `opengrader`, which is the whole reason it exists rather than the
# more obvious `uv tool install .`: that works, but installs all thirty-odd
# console scripts from [project.scripts] — `doctor`, `curve`, `claim`, `tui`,
# `calibrate`, `regrade` — onto your PATH as global names generic enough to
# shadow other tools, re-creating the exact problem the `opengrader` front door
# was built to solve. uv offers no way to filter which entry points a tool
# install exposes (checked on uv 0.12).
#
# Two files land on disk, both copied verbatim:
#   opengrader.ps1   the launcher — finds the checkout and a Python to run it
#   opengrader.cmd   a shim, because .PS1 is not in PATHEXT so `opengrader`
#                    alone would otherwise find nothing
#
# Neither holds a path to this checkout: the launcher resolves one on every
# run, so this installer never needs re-running when the project moves, is
# cloned elsewhere, or sits on a removable drive that comes back as a different
# letter. It also assumes nothing about *how* the environment was built — uv is
# preferred where installed, but Poetry, a plain `python -m venv` and an
# activated Conda environment all work.
#
# Re-running is safe. Uninstall: delete the two files it reports.
# =============================================================================
[CmdletBinding()]
param(
    # Add the install directory to your *user* PATH. Off by default: editing
    # the environment is the kind of change that should be asked for, not
    # assumed, so without this the script prints the one-liner and leaves it
    # to you.
    [switch]$AddToPath
)

$ErrorActionPreference = 'Stop'

$Checkout = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# %USERPROFILE%\.local\bin is where uv itself installs on Windows (see the note
# in packaging/bootstrap.ps1), so on most machines this is already on PATH and
# already holds tools of exactly this kind.
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

# uv is what this project is developed with, but the launcher does not require
# it. So a missing uv is worth saying out loud and is not a reason to refuse to
# install a script that may well work.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'note: uv is not on this PATH.' -ForegroundColor Yellow
    Write-Host '      opengrader will use the project virtualenv, Poetry, or an activated'
    Write-Host '      environment instead. If none of those exist yet, set one up with:'
    Write-Host '        winget install --id astral-sh.uv   (then: uv sync)'
    Write-Host '        ...or:  poetry install'
    Write-Host ''
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

# Refuse to clobber something this script did not write. A stray `opengrader`
# here is far more likely to be a previous `uv tool install` than a name
# collision, but either way overwriting someone else's file is not its call.
# Both installed files carry the marker string this looks for.
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

# Prime the launcher's cache with this checkout, so the first run outside the
# project does not pay for a filesystem search it can avoid.
try {
    $cacheDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'opengrader' }
                else { Join-Path $env:TEMP 'opengrader' }
    New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
    Set-Content -LiteralPath (Join-Path $cacheDir 'checkout-path') -Value $Checkout -Encoding UTF8
} catch {
    # Best effort: an unwritable profile costs one search, not the install.
}

Write-Host ''
Write-Host "Installed: $(Join-Path $BinDir 'opengrader.cmd')"
Write-Host "           $(Join-Path $BinDir 'opengrader.ps1')"
Write-Host "Checkout:  $Checkout"
Write-Host ''

# --- PATH ---------------------------------------------------------------------
# Compared case-insensitively and without trailing slashes, because
# "C:\Users\x\.local\bin\" and "c:\users\x\.local\bin" are the same directory
# and a literal match would send someone to add a duplicate entry.
$normalized = $BinDir.TrimEnd('\')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$onPath = ($env:Path -split ';' | ForEach-Object { $_.TrimEnd('\') }) -contains $normalized

if ($onPath) {
    Write-Host 'Try it:'
    Write-Host '  opengrader doctor'
    return
}

if ($AddToPath) {
    # Only the *user* PATH is touched — never the machine one, which needs
    # admin rights and affects everybody who logs in.
    $updated = if ([string]::IsNullOrEmpty($userPath)) { $normalized } else { "$userPath;$normalized" }
    [Environment]::SetEnvironmentVariable('Path', $updated, 'User')
    Write-Host "Added to your user PATH: $normalized" -ForegroundColor Green
    Write-Host 'Open a new terminal (this one keeps the old PATH), then run:'
    Write-Host '  opengrader doctor'
} else {
    # Built up rather than written as one interpolated literal: the inline form
    # needs escaped quotes nested inside an interpolated string, which is the
    # easiest thing in this file to break with a later edit.
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
