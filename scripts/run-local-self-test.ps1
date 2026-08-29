[CmdletBinding()]
param(
    [ValidateSet('windows-msvc-debug', 'windows-msvc-release', 'windows-msvc-asan')]
    [string]$Preset = 'windows-msvc-release',
    [string]$PythonExecutable = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dnp3ScriptDirectory = $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $dnp3ScriptDirectory 'bin\dnp3-master-host.exe')) {
    $dnp3Root = $dnp3ScriptDirectory
    $dnp3Host = Join-Path $dnp3Root 'bin\dnp3-master-host.exe'
    $dnp3Outstation = Join-Path $dnp3Root 'tools\dnp3-local-test-outstation.exe'
}
else {
    $dnp3Root = (Resolve-Path (Join-Path $dnp3ScriptDirectory '..')).Path
    $dnp3Host = Join-Path $dnp3Root "out\build\$Preset\bin\dnp3-master-host.exe"
    $dnp3Outstation = Join-Path $dnp3Root "out\build\$Preset\bin\dnp3-local-test-outstation.exe"
}

foreach ($dnp3RequiredExecutable in @($dnp3Host, $dnp3Outstation)) {
    if (-not (Test-Path -LiteralPath $dnp3RequiredExecutable -PathType Leaf)) {
        throw "Required self-test executable was not found: $dnp3RequiredExecutable"
    }
}

if ($PythonExecutable) {
    $dnp3Python = (Resolve-Path -LiteralPath $PythonExecutable).Path
}
elseif (Test-Path -LiteralPath (Join-Path $dnp3Root '.venv\Scripts\python.exe')) {
    $dnp3Python = Join-Path $dnp3Root '.venv\Scripts\python.exe'
}
else {
    $dnp3PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $dnp3PythonCommand) {
        throw 'Python 3.10 or newer was not found.'
    }
    $dnp3Python = $dnp3PythonCommand.Source
}

$dnp3PythonSource = Join-Path $dnp3Root 'python\src'
if (-not (Test-Path -LiteralPath $dnp3PythonSource -PathType Container)) {
    throw "Python package source was not found: $dnp3PythonSource"
}
$dnp3PreviousPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$dnp3PreviousNoBytecode = [Environment]::GetEnvironmentVariable(
    'PYTHONDONTWRITEBYTECODE',
    'Process'
)
[Environment]::SetEnvironmentVariable('PYTHONPATH', $dnp3PythonSource, 'Process')
[Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '1', 'Process')
try {
    $dnp3PackageManifest = Join-Path $dnp3Root 'package-manifest.json'
    if (Test-Path -LiteralPath $dnp3PackageManifest -PathType Leaf) {
        & $dnp3Python -m dnp3_master.package_verify --root $dnp3Root
        if ($LASTEXITCODE -ne 0) {
            throw "Portable package integrity verification failed with exit code $LASTEXITCODE."
        }
    }
    & $dnp3Python -m dnp3_master.self_test `
        --host-exe $dnp3Host `
        --outstation-exe $dnp3Outstation
    if ($LASTEXITCODE -ne 0) {
        throw "DNP3 loopback self-test failed with exit code $LASTEXITCODE."
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        'PYTHONPATH',
        $dnp3PreviousPythonPath,
        'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'PYTHONDONTWRITEBYTECODE',
        $dnp3PreviousNoBytecode,
        'Process'
    )
}
