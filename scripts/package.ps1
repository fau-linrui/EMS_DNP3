[CmdletBinding()]
param(
    [ValidateSet('windows-msvc-debug', 'windows-msvc-release')]
    [string]$Preset = 'windows-msvc-release',
    [string]$OutputDirectory = '',
    [switch]$SkipBuild,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dnp3RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dnp3PackageRoot = Join-Path $dnp3RepoRoot 'out\package'
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $dnp3PackageRoot 'ems-dnp3-pytest-0.2.0'
}
$dnp3Stage = [System.IO.Path]::GetFullPath($OutputDirectory)
$dnp3AllowedRoot = [System.IO.Path]::GetFullPath($dnp3PackageRoot).TrimEnd('\') + '\'
if (-not $dnp3Stage.StartsWith($dnp3AllowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must remain below the repository out\package directory."
}

if (Test-Path -LiteralPath $dnp3Stage) {
    if (-not $Force) {
        throw "Package directory already exists: $dnp3Stage. Re-run with -Force to replace it."
    }
    Remove-Item -LiteralPath $dnp3Stage -Recurse -Force
}

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build.ps1') -Preset $Preset
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed before packaging with exit code $LASTEXITCODE."
    }
}

. (Join-Path $PSScriptRoot 'Initialize-BuildEnvironment.ps1')
New-Item -ItemType Directory -Path $dnp3Stage -Force | Out-Null
Push-Location $dnp3RepoRoot
try {
    & cmake --install "out\build\$Preset" --prefix $dnp3Stage
    if ($LASTEXITCODE -ne 0) {
        throw "CMake install failed with exit code $LASTEXITCODE."
    }

    $dnp3PythonStage = Join-Path $dnp3Stage 'python'
    New-Item -ItemType Directory -Path $dnp3PythonStage -Force | Out-Null
    Copy-Item -LiteralPath 'python\pyproject.toml' -Destination $dnp3PythonStage
    Copy-Item -LiteralPath 'python\README.md' -Destination $dnp3PythonStage
    Copy-Item -LiteralPath 'python\src' -Destination $dnp3PythonStage -Recurse

    $dnp3DocsStage = Join-Path $dnp3Stage 'docs'
    New-Item -ItemType Directory -Path $dnp3DocsStage -Force | Out-Null
    foreach ($dnp3Document in @(
        'docs\architecture.md',
        'docs\protocol.md',
        'docs\python_client.md',
        'docs\BEGINNER_MIGRATION_BUILD_USE_GUIDE.md',
        'docs\INTRANET_HANDOFF_REMAINING_TASKS.md'
    )) {
        Copy-Item -LiteralPath $dnp3Document -Destination $dnp3DocsStage
    }
    Copy-Item -LiteralPath 'docs\standards' -Destination $dnp3DocsStage -Recurse
    Copy-Item -LiteralPath 'README.md' -Destination $dnp3Stage
    Copy-Item -LiteralPath 'scripts\run-local-self-test.ps1' `
        -Destination (Join-Path $dnp3Stage 'self-test.ps1')
}
finally {
    Pop-Location
}

Write-Host "Portable integration package created: $dnp3Stage"
