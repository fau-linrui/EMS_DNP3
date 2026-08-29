[CmdletBinding()]
param(
    [ValidateSet('windows-msvc-debug', 'windows-msvc-release', 'windows-msvc-asan')]
    [string]$Preset = 'windows-msvc-release'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dnp3RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'Initialize-BuildEnvironment.ps1')

$dnp3HostExecutable = Join-Path $dnp3RepoRoot "out\build\$Preset\bin\dnp3-master-host.exe"
if (-not (Test-Path -LiteralPath $dnp3HostExecutable -PathType Leaf)) {
    throw "Host executable was not found at '$dnp3HostExecutable'. Run build.ps1 first."
}
$dnp3TestOutstationExecutable = Join-Path $dnp3RepoRoot "out\build\$Preset\bin\dnp3-local-test-outstation.exe"
if (-not (Test-Path -LiteralPath $dnp3TestOutstationExecutable -PathType Leaf)) {
    throw "Local test outstation was not found at '$dnp3TestOutstationExecutable'. Run build.ps1 first."
}
$dnp3PreviousHostExecutable = [Environment]::GetEnvironmentVariable(
    'DNP3_MASTER_HOST_EXE',
    'Process'
)
[Environment]::SetEnvironmentVariable(
    'DNP3_MASTER_HOST_EXE',
    $dnp3HostExecutable,
    'Process'
)
$dnp3PreviousTestOutstationExecutable = [Environment]::GetEnvironmentVariable(
    'DNP3_TEST_OUTSTATION_EXE',
    'Process'
)
[Environment]::SetEnvironmentVariable(
    'DNP3_TEST_OUTSTATION_EXE',
    $dnp3TestOutstationExecutable,
    'Process'
)

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

Push-Location $dnp3RepoRoot
try {
    & ctest --preset $Preset --output-on-failure
    if ($LASTEXITCODE -ne 0) {
        throw "CTest failed for preset '$Preset' with exit code $LASTEXITCODE."
    }

    Push-Location (Join-Path $dnp3RepoRoot 'python')
    try {
        & $dnp3Python -m pytest tests -q
        if ($LASTEXITCODE -ne 0) {
            throw "Python package tests failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }

    & $dnp3Python -m pytest scripts/tests -q
    if ($LASTEXITCODE -ne 0) {
        throw "Repository-validator tests failed with exit code $LASTEXITCODE."
    }

    & $dnp3Python scripts/validate_capabilities.py config/capability_matrix.csv
    if ($LASTEXITCODE -ne 0) {
        throw "Capability-matrix validation failed with exit code $LASTEXITCODE."
    }

    & $dnp3Python scripts/validate_dependencies.py third_party/opendnp3-dependencies.lock.json
    if ($LASTEXITCODE -ne 0) {
        throw "OpenDNP3 dependency validation failed with exit code $LASTEXITCODE."
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        'DNP3_MASTER_HOST_EXE',
        $dnp3PreviousHostExecutable,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'DNP3_TEST_OUTSTATION_EXE',
        $dnp3PreviousTestOutstationExecutable,
        'Process'
    )
    Pop-Location
}
