[CmdletBinding()]
param(
    [ValidateSet('windows-msvc-debug', 'windows-msvc-release', 'windows-msvc-asan')]
    [string]$Preset = 'windows-msvc-release'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dnp3RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'Initialize-BuildEnvironment.ps1')

Push-Location $dnp3RepoRoot
try {
    & cmake --preset $Preset
    if ($LASTEXITCODE -ne 0) {
        throw "CMake configure failed for preset '$Preset' with exit code $LASTEXITCODE."
    }

    & cmake --build --preset $Preset --parallel
    if ($LASTEXITCODE -ne 0) {
        throw "CMake build failed for preset '$Preset' with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

