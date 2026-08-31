[CmdletBinding()]
param(
    [ValidateSet('windows-msvc-debug', 'windows-msvc-release')]
    [string]$Preset = 'windows-msvc-release',
    [string]$OutputDirectory = '',
    [switch]$SkipBuild,
    [switch]$SkipUnpackedSelfTest,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dnp3RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dnp3PackageRoot = Join-Path $dnp3RepoRoot 'out\package'
$dnp3PackageVersion = '0.6.0'
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $dnp3PackageRoot "ems-dnp3-pytest-$dnp3PackageVersion"
}
$dnp3Stage = [System.IO.Path]::GetFullPath($OutputDirectory)
$dnp3AllowedRoot = [System.IO.Path]::GetFullPath($dnp3PackageRoot).TrimEnd('\') + '\'
if (-not $dnp3Stage.StartsWith($dnp3AllowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must remain below the repository out\package directory."
}
$dnp3Archive = "$dnp3Stage.zip"
$dnp3ArchiveHash = "$dnp3Archive.sha256"

if (Test-Path -LiteralPath $dnp3Stage) {
    if (-not $Force) {
        throw "Package directory already exists: $dnp3Stage. Re-run with -Force to replace it."
    }
    Remove-Item -LiteralPath $dnp3Stage -Recurse -Force
}
foreach ($dnp3ExistingArtifact in @($dnp3Archive, $dnp3ArchiveHash)) {
    if (Test-Path -LiteralPath $dnp3ExistingArtifact) {
        if (-not $Force) {
            throw "Package artifact already exists: $dnp3ExistingArtifact. Re-run with -Force to replace it."
        }
        Remove-Item -LiteralPath $dnp3ExistingArtifact -Force
    }
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

    $dnp3ExamplesStage = Join-Path $dnp3Stage 'examples'
    New-Item -ItemType Directory -Path $dnp3ExamplesStage -Force | Out-Null
    Copy-Item -LiteralPath 'examples\pytest_ems' `
        -Destination $dnp3ExamplesStage -Recurse
    Copy-Item -LiteralPath 'examples\pytest_performance' `
        -Destination $dnp3ExamplesStage -Recurse

    $dnp3DocsStage = Join-Path $dnp3Stage 'docs'
    New-Item -ItemType Directory -Path $dnp3DocsStage -Force | Out-Null
    foreach ($dnp3Document in @(
        'docs\architecture.md',
        'docs\protocol.md',
        'docs\python_client.md',
        'docs\LOCAL_TEST_OUTSTATION.md',
        'docs\OFFLINE_PREFLIGHT.md',
        'docs\SAFETY_INCIDENT_RUNBOOK.md',
        'docs\PERFORMANCE_AND_SOAK_GUIDE.md',
        'docs\BEGINNER_MIGRATION_BUILD_USE_GUIDE.md',
        'docs\INTRANET_HANDOFF_REMAINING_TASKS.md'
    )) {
        Copy-Item -LiteralPath $dnp3Document -Destination $dnp3DocsStage
    }
    Copy-Item -LiteralPath 'docs\standards' -Destination $dnp3DocsStage -Recurse
    Copy-Item -LiteralPath 'README.md' -Destination $dnp3Stage
    Copy-Item -LiteralPath 'CHANGELOG.md' -Destination $dnp3Stage
    Copy-Item -LiteralPath 'scripts\run-local-self-test.ps1' `
        -Destination (Join-Path $dnp3Stage 'self-test.ps1')
}
finally {
    Pop-Location
}

# Python bytecode is interpreter-specific runtime cache, not a portable input.
# Resolve every removal target below the already validated staging directory.
$dnp3StagePrefix = $dnp3Stage.TrimEnd('\') + '\'
foreach ($dnp3CacheDirectory in @(
    Get-ChildItem -LiteralPath $dnp3Stage -Directory -Recurse -Force |
        Where-Object { $_.Name -eq '__pycache__' }
)) {
    $dnp3CachePath = [System.IO.Path]::GetFullPath($dnp3CacheDirectory.FullName)
    if (-not $dnp3CachePath.StartsWith($dnp3StagePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Python cache cleanup target escaped the package staging directory.'
    }
    Remove-Item -LiteralPath $dnp3CachePath -Recurse -Force
}
foreach ($dnp3BytecodeFile in @(
    Get-ChildItem -LiteralPath $dnp3Stage -File -Recurse -Force |
        Where-Object { $_.Extension -in @('.pyc', '.pyo') }
)) {
    $dnp3BytecodePath = [System.IO.Path]::GetFullPath($dnp3BytecodeFile.FullName)
    if (-not $dnp3BytecodePath.StartsWith($dnp3StagePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Python bytecode cleanup target escaped the package staging directory.'
    }
    Remove-Item -LiteralPath $dnp3BytecodePath -Force
}

$dnp3BuildInfoPath = Join-Path $dnp3Stage 'bin\build-info.json'
$dnp3BuildInfo = Get-Content -LiteralPath $dnp3BuildInfoPath -Raw | ConvertFrom-Json
if ($dnp3BuildInfo.host_version -ne $dnp3PackageVersion) {
    throw "Installed host version '$($dnp3BuildInfo.host_version)' does not match package version '$dnp3PackageVersion'. Rebuild before packaging."
}

$dnp3VenvPython = Join-Path $dnp3RepoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $dnp3VenvPython -PathType Leaf) {
    $dnp3Python = $dnp3VenvPython
}
else {
    $dnp3PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $dnp3PythonCommand) {
        throw 'Python 3.10 or newer was not found for deterministic packaging.'
    }
    $dnp3Python = $dnp3PythonCommand.Source
}

& $dnp3Python (Join-Path $PSScriptRoot 'create_deterministic_zip.py') `
    --source $dnp3Stage `
    --output $dnp3Archive `
    --package-version $dnp3PackageVersion
if ($LASTEXITCODE -ne 0) {
    throw "Deterministic ZIP creation failed with exit code $LASTEXITCODE."
}

$dnp3ExpectedHash = ((Get-Content -LiteralPath $dnp3ArchiveHash -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
$dnp3ActualHash = (Get-FileHash -LiteralPath $dnp3Archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($dnp3ExpectedHash -ne $dnp3ActualHash) {
    throw 'The generated ZIP does not match its SHA-256 sidecar.'
}

if (-not $SkipUnpackedSelfTest) {
    $dnp3VerifyDirectory = Join-Path $dnp3PackageRoot ("verify-" + [Guid]::NewGuid().ToString('N'))
    $dnp3VerifyFullPath = [System.IO.Path]::GetFullPath($dnp3VerifyDirectory)
    if (-not $dnp3VerifyFullPath.StartsWith($dnp3AllowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Internal package verification directory escaped out\package.'
    }
    try {
        New-Item -ItemType Directory -Path $dnp3VerifyFullPath -Force | Out-Null
        Expand-Archive -LiteralPath $dnp3Archive -DestinationPath $dnp3VerifyFullPath
        & (Join-Path $dnp3VerifyFullPath 'self-test.ps1') -PythonExecutable $dnp3Python
        if ($LASTEXITCODE -ne 0) {
            throw "Unpacked package self-test failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        if (Test-Path -LiteralPath $dnp3VerifyFullPath) {
            Remove-Item -LiteralPath $dnp3VerifyFullPath -Recurse -Force
        }
    }
}

Write-Host "Portable integration directory: $dnp3Stage"
Write-Host "Deterministic ZIP: $dnp3Archive"
Write-Host "SHA-256 sidecar: $dnp3ArchiveHash"
if (-not $SkipUnpackedSelfTest) {
    Write-Host 'Unpacked loopback self-test: PASS'
}
