[CmdletBinding()]
param(
    [ValidateSet('windows-msvc-debug', 'windows-msvc-release')]
    [string]$Preset = 'windows-msvc-release',
    [string]$OutputDirectory = '',
    [switch]$SkipBuild,
    [switch]$SkipUnpackedSelfTest,
    [switch]$AllowNonCleanBuild,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dnp3RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dnp3PackageRoot = Join-Path $dnp3RepoRoot 'out\package'
$dnp3PackageVersion = '0.6.1'
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

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build.ps1') -Preset $Preset
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed before packaging with exit code $LASTEXITCODE."
    }
}

$dnp3SourceBuildInfoPath = Join-Path $dnp3RepoRoot (
    "out\build\$Preset\bin\build-info.json"
)
if (-not (Test-Path -LiteralPath $dnp3SourceBuildInfoPath -PathType Leaf)) {
    throw "Build metadata was not found: $dnp3SourceBuildInfoPath"
}
$dnp3SourceBuildInfo = Get-Content -Raw -LiteralPath $dnp3SourceBuildInfoPath |
    ConvertFrom-Json
if (-not $AllowNonCleanBuild) {
    if ($Preset -ne 'windows-msvc-release') {
        throw (
            'A formal portable package must use windows-msvc-release. Use ' +
            '-AllowNonCleanBuild only for a local non-release inspection package.'
        )
    }
    $dnp3GitCommand = Get-Command git -ErrorAction SilentlyContinue
    if (-not $dnp3GitCommand) {
        throw 'Git is required to prove a clean, traceable portable package.'
    }
    Push-Location $dnp3RepoRoot
    try {
        $dnp3GitCommit = (& git rev-parse HEAD 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $dnp3GitCommit -notmatch '^[0-9a-f]{40}$') {
            throw 'Git could not resolve a valid 40-character HEAD commit.'
        }
        $dnp3GitStatus = (& git status --porcelain --untracked-files=normal 2>&1 |
            Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw 'Git could not inspect the source worktree state.'
        }
    }
    finally {
        Pop-Location
    }
    if ($dnp3GitStatus) {
        throw (
            'A formal portable package requires a clean worktree. Use ' +
            '-AllowNonCleanBuild only for local inspection; never publish that artifact.'
        )
    }
    if ($dnp3SourceBuildInfo.git_commit -ne $dnp3GitCommit) {
        throw (
            "Build commit '$($dnp3SourceBuildInfo.git_commit)' does not match " +
            "current HEAD '$dnp3GitCommit'. Rebuild before packaging."
        )
    }
    if ($dnp3SourceBuildInfo.git_worktree_state -ne 'clean') {
        throw (
            "Build worktree state is '$($dnp3SourceBuildInfo.git_worktree_state)', " +
            'not clean. Rebuild from the clean checkout.'
        )
    }
    if ($dnp3SourceBuildInfo.build_configuration -ne 'Release') {
        throw 'A formal portable package requires Release build metadata.'
    }
    if ($dnp3SourceBuildInfo.target_architecture -ne 'x64') {
        throw 'A formal portable package requires x64 build metadata.'
    }
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

$dnp3SourceManifest = Join-Path $PSScriptRoot 'package-source-files.json'
$dnp3SourceStager = Join-Path $PSScriptRoot 'stage_package_sources.py'
& $dnp3Python $dnp3SourceStager `
    --repository $dnp3RepoRoot --manifest $dnp3SourceManifest --check-only
if ($LASTEXITCODE -ne 0) {
    throw 'Package source allowlist validation failed before staging.'
}

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

New-Item -ItemType Directory -Path $dnp3Stage -Force | Out-Null
# Fixed native outputs plus an explicit source allowlist; never recurse over
# source directories (Git-ignored local files must not become release inputs).
foreach ($dnp3NativeFile in @(
    @{ Name = 'dnp3-master-host.exe'; Directory = 'bin' },
    @{ Name = 'build-info.json'; Directory = 'bin' },
    @{ Name = 'dnp3-local-test-outstation.exe'; Directory = 'tools' }
)) {
    $dnp3NativeSource = Join-Path $dnp3RepoRoot (
        "out\build\$Preset\bin\" + $dnp3NativeFile.Name
    )
    $dnp3NativeTarget = Join-Path $dnp3Stage $dnp3NativeFile.Directory
    New-Item -ItemType Directory -Path $dnp3NativeTarget -Force | Out-Null
    Copy-Item -LiteralPath $dnp3NativeSource -Destination $dnp3NativeTarget
}
& $dnp3Python $dnp3SourceStager `
    --repository $dnp3RepoRoot --manifest $dnp3SourceManifest --stage $dnp3Stage
if ($LASTEXITCODE -ne 0) {
    throw 'Allowlisted package source staging failed.'
}
$dnp3PythonStage = Join-Path $dnp3Stage 'python'

$dnp3BuildInfoPath = Join-Path $dnp3Stage 'bin\build-info.json'
$dnp3BuildInfo = Get-Content -LiteralPath $dnp3BuildInfoPath -Raw | ConvertFrom-Json
if ($dnp3BuildInfo.host_version -ne $dnp3PackageVersion) {
    throw "Installed host version '$($dnp3BuildInfo.host_version)' does not match package version '$dnp3PackageVersion'. Rebuild before packaging."
}

$dnp3WheelStage = Join-Path $dnp3Stage 'python-dist'
$dnp3WheelBuildDirectory = Join-Path $dnp3PackageRoot (
    'wheel-build-' + [Guid]::NewGuid().ToString('N')
)
$dnp3WheelBuildFullPath = [System.IO.Path]::GetFullPath($dnp3WheelBuildDirectory)
if (-not $dnp3WheelBuildFullPath.StartsWith(
    $dnp3AllowedRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw 'Internal wheel build directory escaped out\package.'
}
$dnp3PreviousSourceDateEpoch = [Environment]::GetEnvironmentVariable(
    'SOURCE_DATE_EPOCH',
    'Process'
)
try {
    $dnp3WheelSource = Join-Path $dnp3WheelBuildFullPath 'python'
    New-Item -ItemType Directory -Path $dnp3WheelBuildFullPath -Force | Out-Null
    New-Item -ItemType Directory -Path $dnp3WheelStage -Force | Out-Null
    Copy-Item -LiteralPath $dnp3PythonStage `
        -Destination $dnp3WheelSource `
        -Recurse
    [Environment]::SetEnvironmentVariable(
        'SOURCE_DATE_EPOCH',
        '315532800',
        'Process'
    )
    & $dnp3Python -m pip wheel `
        --disable-pip-version-check `
        --no-index `
        --no-deps `
        --no-build-isolation `
        --no-cache-dir `
        --wheel-dir $dnp3WheelStage `
        $dnp3WheelSource
    if ($LASTEXITCODE -ne 0) {
        throw "Offline Python wheel creation failed with exit code $LASTEXITCODE."
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        'SOURCE_DATE_EPOCH',
        $dnp3PreviousSourceDateEpoch,
        'Process'
    )
    if (Test-Path -LiteralPath $dnp3WheelBuildFullPath) {
        Remove-Item -LiteralPath $dnp3WheelBuildFullPath -Recurse -Force
    }
}
$dnp3Wheels = @(Get-ChildItem -LiteralPath $dnp3WheelStage -Filter '*.whl' -File)
if ($dnp3Wheels.Count -ne 1) {
    throw "Expected exactly one portable Python wheel, found $($dnp3Wheels.Count)."
}

if (-not $AllowNonCleanBuild) {
    Push-Location $dnp3RepoRoot
    try {
        $dnp3FinalGitCommit = (& git rev-parse HEAD 2>&1 | Out-String).Trim()
        $dnp3FinalGitStatus = (
            & git status --porcelain --untracked-files=normal 2>&1 | Out-String
        ).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw 'Git could not recheck the source worktree before archiving.'
        }
    }
    finally {
        Pop-Location
    }
    if ($dnp3FinalGitCommit -ne $dnp3GitCommit -or $dnp3FinalGitStatus) {
        throw 'HEAD or the source worktree changed while the package was staged.'
    }
}

& $dnp3Python $dnp3SourceStager `
    --repository $dnp3RepoRoot --manifest $dnp3SourceManifest `
    --stage $dnp3Stage --verify-stage --version $dnp3PackageVersion
if ($LASTEXITCODE -ne 0) {
    throw 'Staged package contains missing or non-allowlisted files.'
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
