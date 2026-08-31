[CmdletBinding()]
param(
    [ValidateRange(1, 100000)]
    [int]$LifecycleIterations = 1000,
    [string[]]$PythonExecutable = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-Dnp3GitOutput {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $dnp3Output = @(& git @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($dnp3Output -join ' ')"
    }
    return ($dnp3Output -join "`n").TrimEnd()
}

function Assert-Dnp3ReleaseBuildInfo {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedCommit
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Release build metadata was not found: $Path"
    }
    $dnp3Info = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    if ($dnp3Info.git_commit -ne $ExpectedCommit) {
        throw (
            "build-info.json commit '$($dnp3Info.git_commit)' does not match " +
            "release commit '$ExpectedCommit'."
        )
    }
    if ($dnp3Info.git_worktree_state -ne 'clean') {
        throw (
            "build-info.json worktree state is '$($dnp3Info.git_worktree_state)', " +
            'not clean.'
        )
    }
    if ($dnp3Info.build_configuration -ne 'Release') {
        throw "Release package was built as '$($dnp3Info.build_configuration)'."
    }
    if ($dnp3Info.target_architecture -ne 'x64') {
        throw "Release package target is '$($dnp3Info.target_architecture)', not x64."
    }
    return $dnp3Info
}

$dnp3RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dnp3GitCommand = Get-Command git -ErrorAction SilentlyContinue
if (-not $dnp3GitCommand) {
    throw 'Git is required for a traceable clean release.'
}

Push-Location $dnp3RepoRoot
try {
    $dnp3TopLevel = [System.IO.Path]::GetFullPath(
        (Get-Dnp3GitOutput -Arguments @('rev-parse', '--show-toplevel'))
    ).TrimEnd('\')
    if (-not $dnp3TopLevel.Equals(
        [System.IO.Path]::GetFullPath($dnp3RepoRoot).TrimEnd('\'),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "release.ps1 must run against the repository root: $dnp3TopLevel"
    }
    $dnp3Commit = Get-Dnp3GitOutput -Arguments @('rev-parse', 'HEAD')
    if ($dnp3Commit -notmatch '^[0-9a-f]{40}$') {
        throw "Git returned an invalid release commit: $dnp3Commit"
    }
    $dnp3InitialStatus = Get-Dnp3GitOutput -Arguments @(
        'status',
        '--porcelain',
        '--untracked-files=normal'
    )
    if ($dnp3InitialStatus) {
        throw (
            'A formal release requires a clean tracked/untracked worktree. ' +
            'Commit, archive, or intentionally ignore local files first:' +
            [Environment]::NewLine + $dnp3InitialStatus
        )
    }
    [void](Get-Dnp3GitOutput -Arguments @('diff', '--check'))

    Write-Host "Release commit: $dnp3Commit"
    & (Join-Path $PSScriptRoot 'doctor.ps1')
    & (Join-Path $PSScriptRoot 'build.ps1') -Preset windows-msvc-release

    $dnp3BuildInfoPath = Join-Path $dnp3RepoRoot (
        'out\build\windows-msvc-release\bin\build-info.json'
    )
    $dnp3BuildInfo = Assert-Dnp3ReleaseBuildInfo `
        -Path $dnp3BuildInfoPath `
        -ExpectedCommit $dnp3Commit

    & (Join-Path $PSScriptRoot 'test.ps1') -Preset windows-msvc-release
    & (Join-Path $PSScriptRoot 'test-lifecycle.ps1') `
        -Preset windows-msvc-release `
        -Iterations $LifecycleIterations
    & (Join-Path $PSScriptRoot 'package.ps1') `
        -Preset windows-msvc-release `
        -SkipBuild `
        -Force

    $dnp3PackageRoot = Join-Path $dnp3RepoRoot (
        "out\package\ems-dnp3-pytest-$($dnp3BuildInfo.host_version)"
    )
    $dnp3Archive = "$dnp3PackageRoot.zip"
    $dnp3ArchiveSidecar = "$dnp3Archive.sha256"
    $dnp3PackagedBuildInfo = Assert-Dnp3ReleaseBuildInfo `
        -Path (Join-Path $dnp3PackageRoot 'bin\build-info.json') `
        -ExpectedCommit $dnp3Commit
    $dnp3FirstHash = (
        Get-FileHash -LiteralPath $dnp3Archive -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    & (Join-Path $PSScriptRoot 'package.ps1') `
        -Preset windows-msvc-release `
        -SkipBuild `
        -Force
    $dnp3SecondHash = (
        Get-FileHash -LiteralPath $dnp3Archive -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($dnp3FirstHash -ne $dnp3SecondHash) {
        throw (
            "Deterministic package check failed: $dnp3FirstHash != " +
            $dnp3SecondHash
        )
    }
    $dnp3SidecarHash = (
        (Get-Content -Raw -LiteralPath $dnp3ArchiveSidecar).Trim() -split '\s+'
    )[0].ToLowerInvariant()
    if ($dnp3SidecarHash -ne $dnp3SecondHash) {
        throw 'The release ZIP SHA-256 sidecar does not match the archive.'
    }

    $dnp3ReleaseDirectory = Join-Path $dnp3RepoRoot 'out\release'
    New-Item -ItemType Directory -Path $dnp3ReleaseDirectory -Force | Out-Null
    $dnp3CompatibilityReport = Join-Path $dnp3ReleaseDirectory (
        'migration-compatibility-report.json'
    )
    if ($PythonExecutable.Count -gt 0) {
        & (Join-Path $PSScriptRoot 'test-compatibility.ps1') `
            -PackageRoot $dnp3PackageRoot `
            -PythonExecutable $PythonExecutable `
            -ReportPath $dnp3CompatibilityReport
    }
    else {
        & (Join-Path $PSScriptRoot 'test-compatibility.ps1') `
            -PackageRoot $dnp3PackageRoot `
            -ReportPath $dnp3CompatibilityReport
    }

    $dnp3FinalStatus = Get-Dnp3GitOutput -Arguments @(
        'status',
        '--porcelain',
        '--untracked-files=normal'
    )
    if ($dnp3FinalStatus) {
        throw (
            'The source worktree changed during release verification:' +
            [Environment]::NewLine + $dnp3FinalStatus
        )
    }
    $dnp3FinalCommit = Get-Dnp3GitOutput -Arguments @('rev-parse', 'HEAD')
    if ($dnp3FinalCommit -ne $dnp3Commit) {
        throw "HEAD changed during release verification: $dnp3FinalCommit"
    }

    $dnp3Compatibility = Get-Content -Raw -LiteralPath (
        $dnp3CompatibilityReport
    ) | ConvertFrom-Json
    $dnp3ReleaseReport = [ordered]@{
        schema_version = 1
        completed_utc = [DateTime]::UtcNow.ToString('o')
        evidence_scope = 'LOCAL_RELEASE_AND_LOOPBACK_ONLY'
        formal_dut_conclusion = $false
        version = [string]$dnp3PackagedBuildInfo.host_version
        git_commit = $dnp3Commit
        git_worktree_state = 'clean'
        preset = 'windows-msvc-release'
        target_architecture = [string]$dnp3PackagedBuildInfo.target_architecture
        lifecycle_iterations = $LifecycleIterations
        full_test = 'PASS'
        lifecycle_test = 'PASS'
        unpacked_loopback_self_test = 'PASS'
        migration_compatibility = if ($dnp3Compatibility.overall_passed) {
            'PASS'
        } else {
            'FAIL'
        }
        deterministic_package = $true
        archive = $dnp3Archive
        archive_sha256 = $dnp3SecondHash
        archive_sidecar = $dnp3ArchiveSidecar
        compatibility_report = $dnp3CompatibilityReport
    }
    $dnp3ReleaseReportPath = Join-Path $dnp3ReleaseDirectory (
        'release-closure-report.json'
    )
    [System.IO.File]::WriteAllText(
        $dnp3ReleaseReportPath,
        ($dnp3ReleaseReport | ConvertTo-Json -Depth 8) + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "Release closure report: $dnp3ReleaseReportPath"
    Write-Host "Release ZIP: $dnp3Archive"
    Write-Host "Release SHA-256: $dnp3SecondHash"
}
finally {
    Pop-Location
}
