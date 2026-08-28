[CmdletBinding()]
param(
    [ValidateRange(1, 100000)]
    [int]$Iterations = 1000,

    [ValidateSet('windows-msvc-debug', 'windows-msvc-release', 'windows-msvc-asan')]
    [string]$Preset = 'windows-msvc-release'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dnp3RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dnp3HostExecutable = Join-Path $dnp3RepoRoot "out\build\$Preset\bin\dnp3-master-host.exe"
if (-not (Test-Path -LiteralPath $dnp3HostExecutable -PathType Leaf)) {
    throw "Host executable was not found at '$dnp3HostExecutable'. Run build.ps1 first."
}

$dnp3VenvPython = Join-Path $dnp3RepoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $dnp3VenvPython -PathType Leaf) {
    $dnp3Python = $dnp3VenvPython
}
else {
    $dnp3PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $dnp3PythonCommand) {
        throw 'Python was not found. Create .venv or add a supported Python interpreter to PATH.'
    }
    $dnp3Python = $dnp3PythonCommand.Source
}

$dnp3PreviousHostExecutable = [Environment]::GetEnvironmentVariable(
    'DNP3_MASTER_HOST_EXE',
    'Process'
)
$dnp3PreviousLifecycleIterations = [Environment]::GetEnvironmentVariable(
    'DNP3_LIFECYCLE_ITERATIONS',
    'Process'
)
[Environment]::SetEnvironmentVariable(
    'DNP3_MASTER_HOST_EXE',
    $dnp3HostExecutable,
    'Process'
)
[Environment]::SetEnvironmentVariable(
    'DNP3_LIFECYCLE_ITERATIONS',
    $Iterations.ToString([Globalization.CultureInfo]::InvariantCulture),
    'Process'
)

Push-Location (Join-Path $dnp3RepoRoot 'python')
try {
    & $dnp3Python -m pytest tests/test_lifecycle_stress.py -q -s --tb=short
    if ($LASTEXITCODE -ne 0) {
        throw "Lifecycle stress test failed with exit code $LASTEXITCODE."
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        'DNP3_MASTER_HOST_EXE',
        $dnp3PreviousHostExecutable,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'DNP3_LIFECYCLE_ITERATIONS',
        $dnp3PreviousLifecycleIterations,
        'Process'
    )
    Pop-Location
}
