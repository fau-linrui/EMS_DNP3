[CmdletBinding()]
param(
    [switch]$Json,
    [switch]$SkipSourceIntegrity
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dnp3RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dnp3Checks = [System.Collections.Generic.List[object]]::new()

function Add-Dnp3DoctorCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet('PASS', 'FAIL', 'INFO')][string]$Status,
        [Parameter(Mandatory = $true)][string]$Detail
    )
    $dnp3Checks.Add([pscustomobject]@{
        name = $Name
        status = $Status
        detail = $Detail
    })
}

if ($env:OS -eq 'Windows_NT' -and [Environment]::Is64BitOperatingSystem) {
    Add-Dnp3DoctorCheck 'Windows x64' 'PASS' ([Environment]::OSVersion.VersionString)
}
else {
    Add-Dnp3DoctorCheck 'Windows x64' 'FAIL' 'This project supports a 64-bit Windows build host.'
}

if ($PSVersionTable.PSVersion -ge [Version]'5.1') {
    Add-Dnp3DoctorCheck 'PowerShell' 'PASS' $PSVersionTable.PSVersion.ToString()
}
else {
    Add-Dnp3DoctorCheck 'PowerShell' 'FAIL' 'PowerShell 5.1 or newer is required.'
}

try {
    . (Join-Path $PSScriptRoot 'Initialize-BuildEnvironment.ps1') *> $null
    Add-Dnp3DoctorCheck 'Visual Studio C++ x64 tools' 'PASS' ((Get-Command cl).Source)
    Add-Dnp3DoctorCheck 'MSBuild' 'PASS' ((Get-Command msbuild).Source)
}
catch {
    Add-Dnp3DoctorCheck 'Visual Studio C++ x64 tools' 'FAIL' $_.Exception.Message
    Add-Dnp3DoctorCheck 'MSBuild' 'FAIL' 'Unavailable because the Visual Studio environment could not be loaded.'
}

try {
    $dnp3CmakeVersionText = (& cmake --version | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or $dnp3CmakeVersionText -notmatch '(\d+\.\d+\.\d+)') {
        throw 'cmake --version did not return a version.'
    }
    $dnp3CmakeVersion = [Version]$Matches[1]
    if ($dnp3CmakeVersion -lt [Version]'3.25.0') {
        throw "CMake $dnp3CmakeVersion is too old; 3.25 or newer is required."
    }
    if (-not (Get-Command ctest -ErrorAction SilentlyContinue)) {
        throw 'ctest is not available.'
    }
    Add-Dnp3DoctorCheck 'CMake and CTest' 'PASS' $dnp3CmakeVersion.ToString()
}
catch {
    Add-Dnp3DoctorCheck 'CMake and CTest' 'FAIL' $_.Exception.Message
}

$dnp3VenvPython = Join-Path $dnp3RepoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $dnp3VenvPython -PathType Leaf) {
    $dnp3Python = $dnp3VenvPython
}
else {
    $dnp3PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $dnp3Python = if ($dnp3PythonCommand) { $dnp3PythonCommand.Source } else { $null }
}

if (-not $dnp3Python) {
    Add-Dnp3DoctorCheck 'Python' 'FAIL' 'Python 3.10 or newer was not found.'
    Add-Dnp3DoctorCheck 'pytest' 'FAIL' 'Cannot inspect pytest without Python.'
}
else {
    try {
        $dnp3PythonVersion = & $dnp3Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
        if ($LASTEXITCODE -ne 0) {
            throw 'Python version query failed.'
        }
        if ([Version]$dnp3PythonVersion -lt [Version]'3.10.0') {
            throw "Python $dnp3PythonVersion is too old; 3.10 or newer is required."
        }
        Add-Dnp3DoctorCheck 'Python' 'PASS' "$dnp3PythonVersion ($dnp3Python)"
    }
    catch {
        Add-Dnp3DoctorCheck 'Python' 'FAIL' $_.Exception.Message
    }
    try {
        $dnp3PytestVersion = & $dnp3Python -c "import pytest; print(pytest.__version__)"
        if ($LASTEXITCODE -ne 0) {
            throw 'pytest import failed.'
        }
        Add-Dnp3DoctorCheck 'pytest' 'PASS' $dnp3PytestVersion
    }
    catch {
        Add-Dnp3DoctorCheck 'pytest' 'FAIL' 'pytest is missing; install python[ test ] dependencies in .venv.'
    }
}

$dnp3RequiredSources = @(
    'CMakeLists.txt',
    'CMakePresets.json',
    'third_party\opendnp3\CMakeLists.txt',
    'third_party\asio\asio\include\asio.hpp',
    'third_party\exe4cpp\CMakeLists.txt',
    'third_party\ser4cpp\CMakeLists.txt',
    'third_party\nlohmann_json\single_include\nlohmann\json.hpp',
    'third_party\opendnp3.lock.json',
    'third_party\opendnp3-dependencies.lock.json'
)
$dnp3MissingSources = @(
    $dnp3RequiredSources
    | Where-Object { -not (Test-Path -LiteralPath (Join-Path $dnp3RepoRoot $_)) }
)
if ($dnp3MissingSources.Count -eq 0) {
    Add-Dnp3DoctorCheck 'Offline source bundle' 'PASS' 'OpenDNP3 3.1.2 and all required build dependencies are present.'
}
else {
    Add-Dnp3DoctorCheck 'Offline source bundle' 'FAIL' ("Missing: " + ($dnp3MissingSources -join ', '))
}

if (-not $SkipSourceIntegrity -and $dnp3Python -and $dnp3MissingSources.Count -eq 0) {
    try {
        $dnp3DependencyOutput = & $dnp3Python `
            (Join-Path $dnp3RepoRoot 'scripts\validate_dependencies.py') `
            (Join-Path $dnp3RepoRoot 'third_party\opendnp3-dependencies.lock.json') `
            --project-root $dnp3RepoRoot
        if ($LASTEXITCODE -ne 0) {
            throw ($dnp3DependencyOutput -join [Environment]::NewLine)
        }
        Add-Dnp3DoctorCheck 'Vendored dependency hashes' 'PASS' ($dnp3DependencyOutput -join ' ')
    }
    catch {
        Add-Dnp3DoctorCheck 'Vendored dependency hashes' 'FAIL' $_.Exception.Message
    }
    try {
        $dnp3CapabilityOutput = & $dnp3Python `
            (Join-Path $dnp3RepoRoot 'scripts\validate_capabilities.py') `
            (Join-Path $dnp3RepoRoot 'config\capability_matrix.csv')
        if ($LASTEXITCODE -ne 0) {
            throw ($dnp3CapabilityOutput -join [Environment]::NewLine)
        }
        Add-Dnp3DoctorCheck 'Capability matrix' 'PASS' ($dnp3CapabilityOutput -join ' ')
    }
    catch {
        Add-Dnp3DoctorCheck 'Capability matrix' 'FAIL' $_.Exception.Message
    }
}
elseif ($SkipSourceIntegrity) {
    Add-Dnp3DoctorCheck 'Source integrity' 'INFO' 'Skipped by request.'
}

$dnp3GitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($dnp3GitCommand) {
    $dnp3GitVersion = (& git --version) -join ' '
    Add-Dnp3DoctorCheck 'Git' 'INFO' $dnp3GitVersion
}
else {
    Add-Dnp3DoctorCheck 'Git' 'INFO' 'Not found; Git is needed to clone/update, but not to build an already downloaded source tree.'
}
Add-Dnp3DoctorCheck 'Network requirement' 'INFO' 'No network access is required for configure, build, test, or packaging.'

$dnp3Failed = @($dnp3Checks | Where-Object { $_.status -eq 'FAIL' })
$dnp3Report = [pscustomobject]@{
    ready = ($dnp3Failed.Count -eq 0)
    repository = $dnp3RepoRoot
    checks = @($dnp3Checks)
}

if ($Json) {
    $dnp3Report | ConvertTo-Json -Depth 5
}
else {
    $dnp3Checks | Format-Table -AutoSize
    if ($dnp3Report.ready) {
        Write-Host 'READY: this machine can build and test the project offline.'
    }
    else {
        Write-Host "NOT READY: $($dnp3Failed.Count) required check(s) failed."
    }
}

if ($dnp3Report.ready) { exit 0 }
exit 1
