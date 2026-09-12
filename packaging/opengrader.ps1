$MarkerFile = 'pyproject.toml'
$MarkerLine = '^name = "opengrader"$'

$CacheDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'opengrader' }
            elseif ($env:TEMP) { Join-Path $env:TEMP 'opengrader' }
            else { Join-Path ([System.IO.Path]::GetTempPath()) 'opengrader' }
$CacheFile = Join-Path $CacheDir 'checkout-path'

$MaxDepth = 5

$PruneNames = @(
    '.venv', 'venv', 'env', 'node_modules', '.git', '__pycache__',
    'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
    '$Recycle.Bin', 'System Volume Information', 'AppData', 'OneDriveTemp'
)

function Write-Stderr([string]$Message) {
    [Console]::Error.WriteLine($Message)
}

function Write-WrapperDebug([string]$Message) {
    if ($env:OPENGRADER_WRAPPER_DEBUG) {
        Write-Stderr "opengrader[wrapper]: $Message"
    }
}

function Fail([string]$Message) {
    Write-Stderr "opengrader: $Message"
    exit 1
}


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

function Find-CheckoutUpwards([string]$StartDir) {
    $dir = $StartDir
    while ($dir) {
        if (Test-Checkout $dir) { return $dir }
        $parent = Split-Path -Parent $dir
        if ($parent -eq $dir -or [string]::IsNullOrEmpty($parent)) { return $null }
        $dir = $parent
    }
    return $null
}

function Find-CheckoutsUnder([string]$Root, [int]$Depth) {
    $results = @()
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $results }
    if (Test-Checkout $Root) { return @($Root) }
    if ($Depth -le 0) { return $results }

    $children = @()
    try {
        $children = Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction Stop
    } catch {
        return $results
    }

    foreach ($child in $children) {
        if ($PruneNames -contains $child.Name) { continue }
        if ($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
        $results += @(Find-CheckoutsUnder $child.FullName ($Depth - 1))
    }
    return $results
}

function Get-SearchRoots {
    if ($env:OPENGRADER_SEARCH_ROOTS) {
        return $env:OPENGRADER_SEARCH_ROOTS -split ';' | Where-Object { $_ }
    }

    $roots = @()
    if ($env:USERPROFILE) { $roots += $env:USERPROFILE }

    $systemDrive = if ($env:SystemDrive) { "$env:SystemDrive\" } else { 'C:\' }
    try {
        foreach ($drive in [System.IO.DriveInfo]::GetDrives()) {
            if (-not $drive.IsReady) { continue }
            if ($drive.DriveType -eq [System.IO.DriveType]::CDRom) { continue }
            if ($drive.RootDirectory.FullName -eq $systemDrive) { continue }
            $roots += $drive.RootDirectory.FullName
        }
    } catch {
    }

    $roots += $systemDrive
    return ($roots | Select-Object -Unique)
}

function Save-CheckoutPath([string]$Path) {
    try {
        New-Item -ItemType Directory -Force -Path $CacheDir -ErrorAction Stop | Out-Null
        Set-Content -LiteralPath $CacheFile -Value $Path -Encoding UTF8 -ErrorAction Stop
    } catch {
    }
}

function Resolve-Checkout {
    if ($env:OPENGRADER_HOME) {
        if (Test-Checkout $env:OPENGRADER_HOME) {
            Write-WrapperDebug 'using $env:OPENGRADER_HOME'
            return $env:OPENGRADER_HOME
        }
        Fail ("`$env:OPENGRADER_HOME is set to '$env:OPENGRADER_HOME', which is not an Opengrader checkout.`n" +
              "  A checkout is the directory holding $MarkerFile and src\opengrader\.`n" +
              '  Clear it to let opengrader find the checkout itself.')
    }

    if ($PSScriptRoot) {
        $inPlace = Split-Path -Parent $PSScriptRoot
        if (Test-Checkout $inPlace) {
            Write-WrapperDebug 'using the checkout this script lives in'
            return $inPlace
        }
    }

    $here = (Get-Location).Path
    $found = Find-CheckoutUpwards $here
    if ($found) {
        Write-WrapperDebug 'using the checkout containing the current directory'
        Save-CheckoutPath $found
        return $found
    }

    if (Test-Path -LiteralPath $CacheFile -PathType Leaf) {
        $cached = $null
        try {
            $raw = Get-Content -LiteralPath $CacheFile -Raw -ErrorAction Stop
            $cached = $raw.TrimStart([char]0xFEFF).Trim()
        } catch { }
        if (Test-Checkout $cached) {
            Write-WrapperDebug "using the remembered path ($CacheFile)"
            return $cached
        }
        Write-WrapperDebug 'remembered path is stale, searching'
        Remove-Item -LiteralPath $CacheFile -Force -ErrorAction SilentlyContinue
    }

    foreach ($root in Get-SearchRoots) {
        Write-WrapperDebug "searching $root (depth $MaxDepth)"
        $candidates = @(Find-CheckoutsUnder $root $MaxDepth)
        if ($candidates.Count -eq 0) { continue }

        $chosen = $candidates[0]
        if ($candidates.Count -gt 1) {
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

function Test-VenvIsUvMade([string]$Venv) {
    $config = Join-Path $Venv 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { return $false }
    try {
        return [bool](Select-String -LiteralPath $config -Pattern '^uv = ' -Quiet)
    } catch {
        return $false
    }
}

function Invoke-Opengrader([string]$Exe, [string[]]$PreArgs, [string[]]$UserArgs) {
    & $Exe @PreArgs @UserArgs
    exit $LASTEXITCODE
}


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

if ($env:OPENGRADER_PYTHON) {
    if (-not (Test-PythonCanImport $env:OPENGRADER_PYTHON)) {
        Fail ("`$env:OPENGRADER_PYTHON is set to '$env:OPENGRADER_PYTHON', which cannot import opengrader.`n" +
              '  It must be a Python 3.12 interpreter with the project installed into it.')
    }
    Write-WrapperDebug 'running with $env:OPENGRADER_PYTHON'
    Invoke-Opengrader $env:OPENGRADER_PYTHON @('-m', 'opengrader') $args
}

if ($projectVenv -and -not (Test-VenvIsUvMade $projectVenv)) {
    Write-WrapperDebug "running with $projectVenv (not uv-made; uv left alone)"
    Invoke-Opengrader (Join-Path $projectVenv 'Scripts\python.exe') @('-m', 'opengrader') $args
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-WrapperDebug 'running with uv'
    Invoke-Opengrader 'uv' @('run', '--project', $project, 'opengrader') $args
}

if ($projectVenv) {
    Write-WrapperDebug "running with $projectVenv (uv not installed)"
    Invoke-Opengrader (Join-Path $projectVenv 'Scripts\python.exe') @('-m', 'opengrader') $args
}

if (Get-Command poetry -ErrorAction SilentlyContinue) {
    $poetryEnv = $null
    Push-Location -LiteralPath $project -ErrorAction SilentlyContinue
    try {
        $poetryEnv = (& poetry env info --path 2>$null | Select-Object -First 1)
    } catch {
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

foreach ($activated in @($env:VIRTUAL_ENV, $env:CONDA_PREFIX)) {
    if (-not $activated) { continue }
    foreach ($relative in @('Scripts\python.exe', 'python.exe')) {
        $candidate = Join-Path $activated $relative
        if (Test-PythonCanImport $candidate) {
            Write-WrapperDebug "running with the activated environment ($activated)"
            Invoke-Opengrader $candidate @('-m', 'opengrader') $args
        }
    }
}

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
