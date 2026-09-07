# =============================================================================
# `opengrader` from anywhere (Windows) — a launcher that finds its own checkout.
#
# The PowerShell twin of packaging/opengrader. Same two jobs, same rules, same
# reasons; read that file's header for the full rationale and keep the two in
# step when either changes.
#
# Job 1 — find the checkout. This script holds NO path to one. Baking a path in
# at install time is the obvious design and the wrong one: a checkout moves when
# you clone it elsewhere, and a project on a removable drive changes letter
# (D:\ today, E:\ tomorrow) depending only on what was plugged in first — the
# Windows counterpart of the macOS remount that renames "Samsung_T5" to
# "Samsung_T5 1". Order, first match wins:
#
#   1. $env:OPENGRADER_HOME — explicit wins; a wrong value is an error, never a
#      silent fallback.
#   2. The directory above this script, when that is a checkout (so
#      `packaging\opengrader.ps1 doctor` works straight from a clone).
#   3. Walking up from the current directory — inside a checkout, that checkout
#      is the one you meant, which is what makes two clones behave.
#   4. The path remembered from last time.
#   5. A bounded search of the places a checkout plausibly lives.
#
# Job 2 — decide what runs it. uv is what this project is developed with and is
# preferred where present, but is NOT required: a Poetry environment, a plain
# `python -m venv`, or an activated Conda environment all work.
#
#   1. $env:OPENGRADER_PYTHON — an interpreter you name yourself; skips uv.
#   2. A project venv some OTHER tool built (no `uv = ` line in pyvenv.cfg).
#      Used directly and never handed to uv, because `uv run` would sync a
#      Poetry-managed venv against uv.lock and quietly take it over.
#   3. uv — the only runner that also installs what is missing.
#   4. A uv-made venv, on a machine where uv has since gone away.
#   5. Poetry's environment for this project.
#   6. An activated environment ($env:VIRTUAL_ENV, or Conda's $env:CONDA_PREFIX).
#   7. Any python on PATH that can already import opengrader.
#
# Everything but uv runs as `<python> -m opengrader`, never the venv's
# Scripts\opengrader.exe: those launchers embed the absolute interpreter path
# they were built with and stop working when it moves — the same rot that left
# a stale .venv\Scripts entry behind on the macOS side. `python -m` embeds
# nothing, which is what src/opengrader/__main__.py exists for.
#
# Environment variables:
#   OPENGRADER_HOME           pin the checkout explicitly
#   OPENGRADER_PYTHON         run with this interpreter; skips uv
#   OPENGRADER_SEARCH_ROOTS   ';'-separated list of places the search should look
#   OPENGRADER_WRAPPER_DEBUG  set to 1 to print which rule matched
# =============================================================================

# Two style rules run through this whole file, both of them load-bearing:
#
# * PowerShell adds *any* uncaptured output inside a function to that
#   function's return value, so a stray `New-Item` or bare expression would
#   silently staple a directory listing onto a returned checkout path. Every
#   such call is piped to Out-Null.
# * Every diagnostic goes to stderr via Write-Stderr, never Write-Host. This
#   launcher forwards a CLI whose stdout may be JSON (`opengrader commands
#   --json`), and Write-Host output lands in a redirected stdout — one warning
#   would be enough to break a caller piping into ConvertFrom-Json.

$MarkerFile = 'pyproject.toml'
$MarkerLine = '^name = "opengrader"$'

# Where the resolved path is remembered. Deliberately a *cache*: deleting it
# costs one search, never correctness.
# LOCALAPPDATA is the right home on Windows; the two fallbacks matter because
# this script is also exercised by the test suite under pwsh on Linux, where
# neither LOCALAPPDATA nor TEMP is set and Join-Path against an empty string
# would yield a stray relative path in the working directory.
$CacheDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'opengrader' }
            elseif ($env:TEMP) { Join-Path $env:TEMP 'opengrader' }
            else { Join-Path ([System.IO.Path]::GetTempPath()) 'opengrader' }
$CacheFile = Join-Path $CacheDir 'checkout-path'

# How deep to look under each search root. A checkout typically sits two or
# three levels below a drive root or the user profile; every extra level
# multiplies the cost of the one slow path in this script.
$MaxDepth = 5

# Directory names never worth descending into. The first three are large and
# can hold a pyproject.toml of their own, which would be a wrong answer as well
# as a slow one; the rest are Windows' own trees, where no checkout lives.
$PruneNames = @(
    '.venv', 'venv', 'env', 'node_modules', '.git', '__pycache__',
    'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
    '$Recycle.Bin', 'System Volume Information', 'AppData', 'OneDriveTemp'
)

function Write-Stderr([string]$Message) {
    # [Console]::Error is the real stderr handle. Write-Host would render to
    # the host and end up in a redirected *stdout*, which would corrupt the
    # output of a command like `opengrader commands --json`.
    [Console]::Error.WriteLine($Message)
}

function Write-WrapperDebug([string]$Message) {
    if ($env:OPENGRADER_WRAPPER_DEBUG) {
        Write-Stderr "opengrader[wrapper]: $Message"
    }
}

function Fail([string]$Message) {
    Write-Stderr "opengrader: $Message"
    # `exit` from inside a function ends the whole script here. This is the one
    # place the POSIX twin cannot do the same: there, the equivalent resolver
    # runs inside a command substitution, so its `exit` would end only the
    # subshell and the caller would print a second error on top. Keep that in
    # mind when porting a change between the two.
    exit 1
}

# --- Job 1: find the checkout ------------------------------------------------

# Is this directory an Opengrader checkout? The marker is deliberately the
# project name inside pyproject.toml, not the folder name: this repo's
# directory is called `group-project-group-14`, a GitHub Classroom artifact
# that says nothing about what is inside it and that anyone may rename.
function Test-Checkout([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    $marker = Join-Path $Path $MarkerFile
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { return $false }
    try {
        return [bool](Select-String -LiteralPath $marker -Pattern $MarkerLine -Quiet)
    } catch {
        return $false
    }
}

# Rule 3: the nearest checkout at or above a starting directory.
function Find-CheckoutUpwards([string]$StartDir) {
    $dir = $StartDir
    while ($dir) {
        if (Test-Checkout $dir) { return $dir }
        $parent = Split-Path -Parent $dir
        # Split-Path on a drive root returns '' — that is the loop's exit.
        if ($parent -eq $dir -or [string]::IsNullOrEmpty($parent)) { return $null }
        $dir = $parent
    }
    return $null
}

# A hand-rolled walk rather than `Get-ChildItem -Recurse -Depth`, for two
# reasons: -Recurse descends into pruned directories before anything can filter
# them out, and it follows reparse points (junctions such as the legacy
# "C:\Users\All Users") which can loop or wander into the whole system drive.
# This is the same problem the POSIX script solves by skipping symlinked
# volumes.
# Collects *every* checkout under a root rather than stopping at the first.
# Running the wrong clone is an expensive, silent mistake, so the caller warns
# when there is more than one — matching what the POSIX twin does. The extra
# cost is a full walk of one already depth-limited, already pruned root, paid
# only when the remembered path misses.
function Find-CheckoutsUnder([string]$Root, [int]$Depth) {
    $results = @()
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $results }
    if (Test-Checkout $Root) { return @($Root) }
    if ($Depth -le 0) { return $results }

    $children = @()
    try {
        $children = Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction Stop
    } catch {
        # Unreadable directory (permissions, a disconnected network drive).
        # Not finding a checkout here is not an error worth reporting.
        return $results
    }

    foreach ($child in $children) {
        if ($PruneNames -contains $child.Name) { continue }
        if ($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
        # @() around the call keeps a single returned string from being
        # flattened into its characters by +=.
        $results += @(Find-CheckoutsUnder $child.FullName ($Depth - 1))
    }
    return $results
}

# Rule 5: where to look, cheapest and likeliest first.
function Get-SearchRoots {
    if ($env:OPENGRADER_SEARCH_ROOTS) {
        return $env:OPENGRADER_SEARCH_ROOTS -split ';' | Where-Object { $_ }
    }

    $roots = @()
    # The user's own profile first: on Windows a clone almost always lands
    # under Documents, Desktop, or a source folder in here.
    if ($env:USERPROFILE) { $roots += $env:USERPROFILE }

    # Then every other ready drive — where an external disk shows up, and the
    # Windows counterpart of this project living on a removable volume.
    $systemDrive = if ($env:SystemDrive) { "$env:SystemDrive\" } else { 'C:\' }
    try {
        foreach ($drive in [System.IO.DriveInfo]::GetDrives()) {
            if (-not $drive.IsReady) { continue }
            if ($drive.DriveType -eq [System.IO.DriveType]::CDRom) { continue }
            if ($drive.RootDirectory.FullName -eq $systemDrive) { continue }
            $roots += $drive.RootDirectory.FullName
        }
    } catch {
        # DriveInfo can throw on an unusual mount; the profile search above is
        # still worth doing.
    }

    # The system drive last and shallow. C:\dev\opengrader is a real habit, but
    # a deep sweep of C:\ is minutes, not seconds — the prune list above and
    # this shallower depth are what keep it bounded.
    $roots += $systemDrive
    # A profile on a non-system drive (D:\Users\me) would otherwise make that
    # drive's root get walked twice.
    return ($roots | Select-Object -Unique)
}

function Save-CheckoutPath([string]$Path) {
    # Best effort by design: an unwritable profile should cost a search on the
    # next run, never the run in front of us.
    try {
        New-Item -ItemType Directory -Force -Path $CacheDir -ErrorAction Stop | Out-Null
        Set-Content -LiteralPath $CacheFile -Value $Path -Encoding UTF8 -ErrorAction Stop
    } catch {
        # Ignored deliberately.
    }
}

function Resolve-Checkout {
    # 1. Explicit override.
    if ($env:OPENGRADER_HOME) {
        if (Test-Checkout $env:OPENGRADER_HOME) {
            Write-WrapperDebug 'using $env:OPENGRADER_HOME'
            return $env:OPENGRADER_HOME
        }
        Fail ("`$env:OPENGRADER_HOME is set to '$env:OPENGRADER_HOME', which is not an Opengrader checkout.`n" +
              "  A checkout is the directory holding $MarkerFile and src\opengrader\.`n" +
              '  Clear it to let opengrader find the checkout itself.')
    }

    # 2. Running in place, from a checkout's packaging\ directory.
    if ($PSScriptRoot) {
        $inPlace = Split-Path -Parent $PSScriptRoot
        if (Test-Checkout $inPlace) {
            Write-WrapperDebug 'using the checkout this script lives in'
            return $inPlace
        }
    }

    # 3. The checkout the caller is standing in.
    $here = (Get-Location).Path
    $found = Find-CheckoutUpwards $here
    if ($found) {
        Write-WrapperDebug 'using the checkout containing the current directory'
        Save-CheckoutPath $found
        return $found
    }

    # 4. Last known good.
    if (Test-Path -LiteralPath $CacheFile -PathType Leaf) {
        $cached = $null
        try {
            $raw = Get-Content -LiteralPath $CacheFile -Raw -ErrorAction Stop
            # Strip a leading BOM before trimming. Windows PowerShell 5.1's
            # `Set-Content -Encoding UTF8` writes one, and U+FEFF is not
            # whitespace as far as String.Trim() is concerned — so without this
            # the remembered path could come back as "\uFEFFC:\..." , match
            # nothing, and quietly force a full search on every single run.
            $cached = $raw.TrimStart([char]0xFEFF).Trim()
        } catch { }
        if (Test-Checkout $cached) {
            Write-WrapperDebug "using the remembered path ($CacheFile)"
            return $cached
        }
        # Stale — the drive letter changed, or the checkout moved. Drop it so
        # the search gets a clean shot and the next run is fast again.
        Write-WrapperDebug 'remembered path is stale, searching'
        Remove-Item -LiteralPath $CacheFile -Force -ErrorAction SilentlyContinue
    }

    # 5. Search. The first root that yields anything wins.
    foreach ($root in Get-SearchRoots) {
        Write-WrapperDebug "searching $root (depth $MaxDepth)"
        # Not named $matches: that is an automatic variable PowerShell fills
        # in from -match, and shadowing it is a subtle way to break unrelated
        # code later in the same scope.
        $candidates = @(Find-CheckoutsUnder $root $MaxDepth)
        if ($candidates.Count -eq 0) { continue }

        $chosen = $candidates[0]
        if ($candidates.Count -gt 1) {
            # Report rather than silently pick.
            Write-Stderr "opengrader: found more than one checkout under $root; using $chosen"
            foreach ($other in $candidates[1..($candidates.Count - 1)]) {
                Write-Stderr "opengrader: also found: $other"
            }
            Write-Stderr 'opengrader: set $env:OPENGRADER_HOME to choose a different one.'
        }
        Write-WrapperDebug "found $chosen"
        Save-CheckoutPath $chosen
        return $chosen
    }
    return $null
}

# --- Job 2: decide what runs it ----------------------------------------------

# Can this interpreter import the package? One Python start-up (~100ms), so it
# runs only on the fallback runners — never on the uv path — and it buys a real
# error message instead of a traceback.
function Test-PythonCanImport([string]$Python) {
    if ([string]::IsNullOrWhiteSpace($Python)) { return $false }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return $false }
    try {
        & $Python -c 'import opengrader' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Is the package installed in this virtualenv? A few existence checks rather
# than a process: an editable install leaves a .pth, a regular one a dist-info,
# and either way Scripts\opengrader.exe appears.
function Test-VenvHasPackage([string]$Venv) {
    if (Test-Path -LiteralPath (Join-Path $Venv 'Scripts\opengrader.exe')) { return $true }
    $sitePackages = Join-Path $Venv 'Lib\site-packages'
    if (-not (Test-Path -LiteralPath $sitePackages -PathType Container)) { return $false }
    foreach ($pattern in @('opengrader', 'opengrader-*.dist-info', '*editable*opengrader*.pth')) {
        if (Get-ChildItem -LiteralPath $sitePackages -Filter $pattern -Force -ErrorAction SilentlyContinue) {
            return $true
        }
    }
    return $false
}

# The project virtualenv, if there is one. `.venv` is what uv and Poetry both
# use by default; the other two are what `python -m venv` users pick.
function Find-ProjectVenv([string]$Project) {
    foreach ($name in @('.venv', 'venv', 'env')) {
        $candidate = Join-Path $Project $name
        $python = Join-Path $candidate 'Scripts\python.exe'
        if ((Test-Path -LiteralPath $python -PathType Leaf) -and (Test-VenvHasPackage $candidate)) {
            return $candidate
        }
    }
    return $null
}

# Did uv build this virtualenv? uv stamps its version into pyvenv.cfg, which is
# the only reliable way to tell its venv from Poetry's or virtualenv's.
function Test-VenvIsUvMade([string]$Venv) {
    $config = Join-Path $Venv 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { return $false }
    try {
        return [bool](Select-String -LiteralPath $config -Pattern '^uv = ' -Quiet)
    } catch {
        return $false
    }
}

# Run the chosen command and hand its exit code back to the shell. PowerShell
# has no exec(), so the tool runs as a child process; it inherits this console,
# which is what the TUI needs for keyboard input and image output.
function Invoke-Opengrader([string]$Exe, [string[]]$PreArgs, [string[]]$UserArgs) {
    & $Exe @PreArgs @UserArgs
    exit $LASTEXITCODE
}

# --- Main --------------------------------------------------------------------

$project = Resolve-Checkout
if (-not $project) {
    $searched = if ($env:OPENGRADER_SEARCH_ROOTS) {
        "`$env:OPENGRADER_SEARCH_ROOTS ($env:OPENGRADER_SEARCH_ROOTS)"
    } else {
        'your user profile, every ready drive, and the system drive'
    }
    Fail ("could not find an Opengrader checkout.`n" +
          "  Looked under $searched.`n" +
          "  If the project is on a removable drive, check that it is connected.`n" +
          '  Otherwise point at it directly:  $env:OPENGRADER_HOME = "C:\path\to\the\checkout"')
}

$projectVenv = Find-ProjectVenv $project

# 1. An interpreter named outright.
if ($env:OPENGRADER_PYTHON) {
    if (-not (Test-PythonCanImport $env:OPENGRADER_PYTHON)) {
        Fail ("`$env:OPENGRADER_PYTHON is set to '$env:OPENGRADER_PYTHON', which cannot import opengrader.`n" +
              '  It must be a Python 3.12 interpreter with the project installed into it.')
    }
    Write-WrapperDebug 'running with $env:OPENGRADER_PYTHON'
    Invoke-Opengrader $env:OPENGRADER_PYTHON @('-m', 'opengrader') $args
}

# 2. A virtualenv another tool built — use it, and keep uv away from it.
if ($projectVenv -and -not (Test-VenvIsUvMade $projectVenv)) {
    Write-WrapperDebug "running with $projectVenv (not uv-made; uv left alone)"
    Invoke-Opengrader (Join-Path $projectVenv 'Scripts\python.exe') @('-m', 'opengrader') $args
}

# 3. uv — the project's own setup, and the only runner that installs what is
#    missing rather than failing on it.
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-WrapperDebug 'running with uv'
    Invoke-Opengrader 'uv' @('run', '--project', $project, 'opengrader') $args
}

# 4. The virtualenv uv made before uv went away.
if ($projectVenv) {
    Write-WrapperDebug "running with $projectVenv (uv not installed)"
    Invoke-Opengrader (Join-Path $projectVenv 'Scripts\python.exe') @('-m', 'opengrader') $args
}

# 5. Poetry's environment for this project.
if (Get-Command poetry -ErrorAction SilentlyContinue) {
    $poetryEnv = $null
    # Push outside the try: if Push-Location itself failed, the finally
    # below would pop a location this script never pushed.
    Push-Location -LiteralPath $project -ErrorAction SilentlyContinue
    try {
        $poetryEnv = (& poetry env info --path 2>$null | Select-Object -First 1)
    } catch {
        # Poetry not configured for this project, or too old to read its
        # PEP 621 metadata. Not an error — the next runner gets a turn.
    } finally {
        Pop-Location -ErrorAction SilentlyContinue
    }
    if ($poetryEnv) {
        $poetryPython = Join-Path $poetryEnv.Trim() 'Scripts\python.exe'
        if (Test-PythonCanImport $poetryPython) {
            Write-WrapperDebug "running with poetry's environment ($poetryEnv)"
            Invoke-Opengrader $poetryPython @('-m', 'opengrader') $args
        }
    }
}

# 6. Whatever the caller has activated. Conda does not set VIRTUAL_ENV, so both
#    variables are checked.
foreach ($activated in @($env:VIRTUAL_ENV, $env:CONDA_PREFIX)) {
    if (-not $activated) { continue }
    # A Conda root has python.exe at its top level; a venv has it in Scripts\.
    foreach ($relative in @('Scripts\python.exe', 'python.exe')) {
        $candidate = Join-Path $activated $relative
        if (Test-PythonCanImport $candidate) {
            Write-WrapperDebug "running with the activated environment ($activated)"
            Invoke-Opengrader $candidate @('-m', 'opengrader') $args
        }
    }
}

# 7. A python on PATH that already has it — pipx, or `pip install -e .`.
foreach ($name in @('python', 'python3', 'py')) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $command) { continue }
    if (Test-PythonCanImport $command.Source) {
        Write-WrapperDebug "running with $($command.Source) (already has opengrader installed)"
        Invoke-Opengrader $command.Source @('-m', 'opengrader') $args
    }
}

Fail ("found the project at $project, but no Python environment that can run it.`n" +
      "  The project needs Python 3.12 and its dependencies. Set one up with whichever`n" +
      "  tool you use, from that directory:`n`n" +
      "    uv sync                                (what this project is developed with)`n" +
      "    poetry install`n" +
      "    py -3.12 -m venv .venv; .venv\Scripts\pip install -e .`n`n" +
      "  Or point at an interpreter that already has it:`n" +
      '    $env:OPENGRADER_PYTHON = "C:\path\to\python.exe"')
