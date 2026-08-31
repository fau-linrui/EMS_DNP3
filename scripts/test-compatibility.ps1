[CmdletBinding()]
param(
    [string]$PackageRoot = '',
    [string[]]$PythonExecutable = @(),
    [string]$ReportPath = '',
    [switch]$SkipLoopbackSelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-Dnp3PythonExecutable {
    param([Parameter(Mandatory = $true)][string]$Value)

    if (Test-Path -LiteralPath $Value -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Value).Path
    }
    $dnp3Command = Get-Command $Value -ErrorAction SilentlyContinue
    if (-not $dnp3Command -or -not $dnp3Command.Source) {
        throw "Python executable was not found: $Value"
    }
    return $dnp3Command.Source
}

function Invoke-Dnp3CommandCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $dnp3Output = @(& $Executable @Arguments 2>&1)
    $dnp3ExitCode = $LASTEXITCODE
    foreach ($dnp3Line in $dnp3Output) {
        Write-Host ([string]$dnp3Line)
    }
    if ($dnp3ExitCode -ne 0) {
        throw "$Description failed with exit code $dnp3ExitCode."
    }
    return ,$dnp3Output
}

function Set-Dnp3ProcessEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()][string]$Value
    )

    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}

$dnp3ScriptDirectory = $PSScriptRoot
$dnp3PackagedMode = Test-Path -LiteralPath (
    Join-Path $dnp3ScriptDirectory 'bin\dnp3-master-host.exe'
) -PathType Leaf
if ($dnp3PackagedMode) {
    $dnp3DefaultPackageRoot = $dnp3ScriptDirectory
    $dnp3FixtureRoot = Join-Path $dnp3ScriptDirectory 'migration-consumer'
    $dnp3RepositoryRoot = $null
}
else {
    $dnp3RepositoryRoot = (Resolve-Path (Join-Path $dnp3ScriptDirectory '..')).Path
    $dnp3DefaultPackageRoot = Join-Path $dnp3RepositoryRoot (
        'out\package\ems-dnp3-pytest-0.6.1'
    )
    $dnp3FixtureRoot = Join-Path $dnp3ScriptDirectory (
        'fixtures\pytest_consumer'
    )
}

if (-not $PackageRoot) {
    $PackageRoot = $dnp3DefaultPackageRoot
}
$dnp3PackageRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
$dnp3PackagePrefix = $dnp3PackageRoot.TrimEnd('\') + '\'
$dnp3ReportFullPath = $null
if ($ReportPath) {
    $dnp3ReportFullPath = [System.IO.Path]::GetFullPath($ReportPath)
    if (
        $dnp3ReportFullPath.Equals(
            $dnp3PackageRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        $dnp3ReportFullPath.StartsWith(
            $dnp3PackagePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'ReportPath must remain outside the manifest-protected package root.'
    }
}
$dnp3RequiredPaths = @(
    'bin\dnp3-master-host.exe',
    'bin\build-info.json',
    'tools\dnp3-local-test-outstation.exe',
    'python\pyproject.toml',
    'python\src\dnp3_master\__init__.py',
    'package-manifest.json',
    'self-test.ps1'
)
foreach ($dnp3RelativePath in $dnp3RequiredPaths) {
    $dnp3RequiredPath = Join-Path $dnp3PackageRoot $dnp3RelativePath
    if (-not (Test-Path -LiteralPath $dnp3RequiredPath -PathType Leaf)) {
        throw "Portable package is missing required file: $dnp3RequiredPath"
    }
}
if (-not (Test-Path -LiteralPath $dnp3FixtureRoot -PathType Container)) {
    throw "Blank pytest consumer fixture was not found: $dnp3FixtureRoot"
}
$dnp3ProbeScript = Join-Path $dnp3FixtureRoot 'compatibility_probe.py'
if (-not (Test-Path -LiteralPath $dnp3ProbeScript -PathType Leaf)) {
    throw "Compatibility probe was not found: $dnp3ProbeScript"
}

$dnp3Manifest = Get-Content -Raw -LiteralPath (
    Join-Path $dnp3PackageRoot 'package-manifest.json'
) | ConvertFrom-Json
$dnp3BuildInfo = Get-Content -Raw -LiteralPath (
    Join-Path $dnp3PackageRoot 'bin\build-info.json'
) | ConvertFrom-Json
$dnp3WheelFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $dnp3PackageRoot 'python-dist') `
        -Filter '*.whl' `
        -File `
        -ErrorAction SilentlyContinue
)
if ($dnp3WheelFiles.Count -gt 1) {
    throw 'Portable package contains more than one Python wheel.'
}
$dnp3WheelPath = if ($dnp3WheelFiles.Count -eq 1) {
    $dnp3WheelFiles[0].FullName
} else {
    $null
}
$dnp3PackageVersion = [string]$dnp3Manifest.package_version
if ($dnp3BuildInfo.host_version -ne $dnp3PackageVersion) {
    throw (
        "Package manifest version '$dnp3PackageVersion' does not match host " +
        "version '$($dnp3BuildInfo.host_version)'."
    )
}

if ($PythonExecutable.Count -eq 0) {
    if (
        $dnp3RepositoryRoot -and
        (Test-Path -LiteralPath (
            Join-Path $dnp3RepositoryRoot '.venv\Scripts\python.exe'
        ) -PathType Leaf)
    ) {
        $PythonExecutable = @(
            Join-Path $dnp3RepositoryRoot '.venv\Scripts\python.exe'
        )
    }
    else {
        $dnp3PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $dnp3PythonCommand) {
            throw 'No Python executable was supplied and python was not found on PATH.'
        }
        $PythonExecutable = @($dnp3PythonCommand.Source)
    }
}

$dnp3ResolvedPython = [System.Collections.Generic.List[string]]::new()
$dnp3SeenPython = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($dnp3Candidate in $PythonExecutable) {
    $dnp3Resolved = Resolve-Dnp3PythonExecutable -Value $dnp3Candidate
    if ($dnp3SeenPython.Add($dnp3Resolved)) {
        $dnp3ResolvedPython.Add($dnp3Resolved)
    }
}
if ($dnp3ResolvedPython.Count -eq 0) {
    throw 'At least one Python executable is required.'
}
if ($dnp3ResolvedPython.Count -gt 32) {
    throw 'At most 32 Python compatibility candidates may be tested in one run.'
}

$dnp3EnvironmentNames = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($dnp3EnvironmentEntry in @(Get-ChildItem Env:)) {
    if ($dnp3EnvironmentEntry.Name -like 'DNP3_*') {
        [void]$dnp3EnvironmentNames.Add($dnp3EnvironmentEntry.Name)
    }
}
foreach ($dnp3EnvironmentName in @(
    'PYTHONPATH',
    'PYTHONNOUSERSITE',
    'PYTHONDONTWRITEBYTECODE',
    'PYTEST_ADDOPTS',
    'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
    'PYTEST_PLUGINS',
    'PIP_NO_INDEX'
)) {
    [void]$dnp3EnvironmentNames.Add($dnp3EnvironmentName)
}
$dnp3SavedEnvironment = @{}
foreach ($dnp3EnvironmentName in $dnp3EnvironmentNames) {
    $dnp3EnvironmentValue = [Environment]::GetEnvironmentVariable(
        $dnp3EnvironmentName,
        'Process'
    )
    $dnp3SavedEnvironment[$dnp3EnvironmentName] = [pscustomobject]@{
        exists = $null -ne $dnp3EnvironmentValue
        value = $dnp3EnvironmentValue
    }
    Set-Dnp3ProcessEnvironment -Name $dnp3EnvironmentName -Value $null
}

$dnp3Results = [System.Collections.Generic.List[object]]::new()
$dnp3AnyFailure = $false
$dnp3StartedUtc = [DateTime]::UtcNow.ToString('o')
$dnp3TemporaryBase = [System.IO.Path]::GetFullPath(
    [System.IO.Path]::GetTempPath()
).TrimEnd('\') + '\'

try {
    Set-Dnp3ProcessEnvironment -Name 'PYTHONNOUSERSITE' -Value '1'
    Set-Dnp3ProcessEnvironment -Name 'PYTHONDONTWRITEBYTECODE' -Value '1'
    Set-Dnp3ProcessEnvironment -Name 'PYTEST_ADDOPTS' -Value ''
    Set-Dnp3ProcessEnvironment -Name 'PYTEST_DISABLE_PLUGIN_AUTOLOAD' -Value '1'
    Set-Dnp3ProcessEnvironment -Name 'PIP_NO_INDEX' -Value '1'

    foreach ($dnp3Python in $dnp3ResolvedPython) {
        $dnp3TemporaryRoot = $null
        $dnp3Result = [ordered]@{
            python_executable = $dnp3Python
            python_version = $null
            pytest_version = $null
            pip_version = $null
            setuptools_version = $null
            installation_mode = if ($dnp3WheelPath) {
                'packaged_wheel'
            } else {
                'source_copy_fallback'
            }
            installed_import_path = $null
            package_integrity = 'NOT_RUN'
            loopback_self_test = if ($SkipLoopbackSelfTest) { 'SKIPPED' } else { 'NOT_RUN' }
            blank_pytest_consumer = 'NOT_RUN'
            status = 'FAIL'
            error = $null
        }
        Write-Host "Compatibility candidate: $dnp3Python"
        try {
            $dnp3ProbeOutput = @(& $dnp3Python $dnp3ProbeScript environment 2>&1)
            if ($LASTEXITCODE -ne 0) {
                throw (
                    'Python must provide pytest and pip before offline compatibility ' +
                    'testing; setuptools 68+ is additionally required only for the ' +
                    'legacy source-copy fallback: ' +
                    ($dnp3ProbeOutput -join [Environment]::NewLine)
                )
            }
            $dnp3Probe = ($dnp3ProbeOutput -join "`n") | ConvertFrom-Json
            $dnp3Result.python_version = [string]$dnp3Probe.python
            $dnp3Result.pytest_version = [string]$dnp3Probe.pytest
            $dnp3Result.pip_version = [string]$dnp3Probe.pip
            if ($null -ne $dnp3Probe.setuptools) {
                $dnp3Result.setuptools_version = [string]$dnp3Probe.setuptools
            }
            if ([Version]$dnp3Result.python_version -lt [Version]'3.10.0') {
                throw "Python $($dnp3Result.python_version) is unsupported; 3.10+ is required."
            }
            if ($dnp3Result.pytest_version -notmatch '^(8|9)(\.|$)') {
                throw (
                    "pytest $($dnp3Result.pytest_version) is unsupported; " +
                    'pytest 8.x or 9.x is required.'
                )
            }
            if (
                -not $dnp3WheelPath -and
                (
                    -not $dnp3Result.setuptools_version -or
                    $dnp3Result.setuptools_version -notmatch '^(\d+)\.' -or
                    [int]$Matches[1] -lt 68
                )
            ) {
                throw (
                    "setuptools $($dnp3Result.setuptools_version) is unsupported; " +
                    'setuptools 68+ is required for the legacy source-copy fallback.'
                )
            }

            $dnp3PackagePythonSource = Join-Path $dnp3PackageRoot 'python\src'
            Set-Dnp3ProcessEnvironment -Name 'PYTHONPATH' -Value $dnp3PackagePythonSource
            [void](Invoke-Dnp3CommandCapture `
                -Executable $dnp3Python `
                -Arguments @(
                    '-m',
                    'dnp3_master.package_verify',
                    '--root',
                    $dnp3PackageRoot
                ) `
                -Description 'Portable package integrity verification')
            $dnp3Result.package_integrity = 'PASS'

            if (-not $SkipLoopbackSelfTest) {
                & (Join-Path $dnp3PackageRoot 'self-test.ps1') `
                    -PythonExecutable $dnp3Python
                $dnp3Result.loopback_self_test = 'PASS'
            }

            $dnp3TemporaryName = (
                'ems-dnp3-migration-' + [Guid]::NewGuid().ToString('N')
            )
            $dnp3TemporaryRoot = [System.IO.Path]::GetFullPath((
                Join-Path ([System.IO.Path]::GetTempPath()) $dnp3TemporaryName
            ))
            if (-not $dnp3TemporaryRoot.StartsWith(
                $dnp3TemporaryBase,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw 'Temporary compatibility directory escaped the system temp root.'
            }
            $dnp3InstallRoot = Join-Path $dnp3TemporaryRoot 'site'
            $dnp3ConsumerRoot = Join-Path $dnp3TemporaryRoot 'consumer'
            New-Item -ItemType Directory -Path $dnp3InstallRoot -Force | Out-Null
            New-Item -ItemType Directory -Path $dnp3ConsumerRoot -Force | Out-Null
            Get-ChildItem -LiteralPath $dnp3FixtureRoot | Copy-Item `
                -Destination $dnp3ConsumerRoot `
                -Recurse `
                -Force

            if ($dnp3WheelPath) {
                $dnp3InstallArtifact = $dnp3WheelPath
            }
            else {
                $dnp3PythonBuildSource = Join-Path $dnp3TemporaryRoot (
                    'python-source'
                )
                Copy-Item -LiteralPath (Join-Path $dnp3PackageRoot 'python') `
                    -Destination $dnp3PythonBuildSource `
                    -Recurse
                $dnp3InstallArtifact = $dnp3PythonBuildSource
            }

            Set-Dnp3ProcessEnvironment -Name 'PYTHONPATH' -Value $null
            [void](Invoke-Dnp3CommandCapture `
                -Executable $dnp3Python `
                -Arguments @(
                    '-m',
                    'pip',
                    'install',
                    '--disable-pip-version-check',
                    '--no-index',
                    '--no-deps',
                    '--no-build-isolation',
                    '--no-compile',
                    '--no-cache-dir',
                    '--target',
                    $dnp3InstallRoot,
                    $dnp3InstallArtifact
                ) `
                -Description 'Offline isolated Python package installation')

            Set-Dnp3ProcessEnvironment -Name 'PYTHONPATH' -Value $dnp3InstallRoot
            Set-Dnp3ProcessEnvironment `
                -Name 'DNP3_EXPECTED_INSTALLED_ROOT' `
                -Value $dnp3InstallRoot
            Set-Dnp3ProcessEnvironment `
                -Name 'DNP3_EXPECTED_PACKAGE_ROOT' `
                -Value $dnp3PackageRoot
            Set-Dnp3ProcessEnvironment `
                -Name 'DNP3_EXPECTED_PACKAGE_VERSION' `
                -Value $dnp3PackageVersion

            Push-Location $dnp3ConsumerRoot
            try {
                $dnp3ConsumerProbe = Join-Path $dnp3ConsumerRoot (
                    'compatibility_probe.py'
                )
                $dnp3ImportOutput = @(
                    & $dnp3Python $dnp3ConsumerProbe installed-package 2>&1
                )
                if ($LASTEXITCODE -ne 0) {
                    throw (
                        'The isolated dnp3_master import failed: ' +
                        ($dnp3ImportOutput -join [Environment]::NewLine)
                    )
                }
                $dnp3Import = ($dnp3ImportOutput -join "`n") | ConvertFrom-Json
                $dnp3Result.installed_import_path = [string]$dnp3Import.path
                if ($dnp3Import.version -ne $dnp3PackageVersion) {
                    throw (
                        "Installed Python version '$($dnp3Import.version)' does not " +
                        "match package version '$dnp3PackageVersion'."
                    )
                }

                $dnp3HelpOutput = @(& $dnp3Python -m pytest --help 2>&1)
                if ($LASTEXITCODE -ne 0) {
                    throw 'pytest --help failed in the blank consumer project.'
                }
                if (($dnp3HelpOutput -join "`n") -notmatch '--dnp3-host-exe') {
                    throw 'The DNP3 pytest plugin options were not registered.'
                }
                [void](Invoke-Dnp3CommandCapture `
                    -Executable $dnp3Python `
                    -Arguments @('-m', 'pytest', '-q') `
                    -Description 'Blank pytest consumer acceptance')
            }
            finally {
                Pop-Location
            }

            $dnp3Result.blank_pytest_consumer = 'PASS'
            $dnp3Result.status = 'PASS'
            Write-Host (
                "PASS: Python $($dnp3Result.python_version), pytest " +
                "$($dnp3Result.pytest_version)"
            )
        }
        catch {
            $dnp3AnyFailure = $true
            $dnp3FailureMessage = $_.Exception.Message
            if ([string]::IsNullOrWhiteSpace($dnp3FailureMessage)) {
                $dnp3FailureMessage = 'unspecified compatibility failure'
            }
            if ($dnp3FailureMessage.Length -gt 8192) {
                $dnp3FailureMessage = $dnp3FailureMessage.Substring(0, 8192)
            }
            $dnp3Result.error = $dnp3FailureMessage
            Write-Warning "Compatibility candidate failed: $dnp3FailureMessage"
        }
        finally {
            Set-Dnp3ProcessEnvironment -Name 'PYTHONPATH' -Value $null
            foreach ($dnp3ExpectedName in @(
                'DNP3_EXPECTED_INSTALLED_ROOT',
                'DNP3_EXPECTED_PACKAGE_ROOT',
                'DNP3_EXPECTED_PACKAGE_VERSION'
            )) {
                Set-Dnp3ProcessEnvironment -Name $dnp3ExpectedName -Value $null
            }
            if ($dnp3TemporaryRoot -and (Test-Path -LiteralPath $dnp3TemporaryRoot)) {
                $dnp3CleanupPath = [System.IO.Path]::GetFullPath($dnp3TemporaryRoot)
                if (-not $dnp3CleanupPath.StartsWith(
                    $dnp3TemporaryBase,
                    [System.StringComparison]::OrdinalIgnoreCase
                )) {
                    throw 'Refusing to clean a compatibility path outside system temp.'
                }
                Remove-Item -LiteralPath $dnp3CleanupPath -Recurse -Force
            }
            $dnp3Results.Add([pscustomobject]$dnp3Result)
        }
    }

    $dnp3Report = [ordered]@{
        schema_version = 1
        started_utc = $dnp3StartedUtc
        completed_utc = [DateTime]::UtcNow.ToString('o')
        evidence_scope = 'LOCAL_PACKAGE_AND_LOOPBACK_ONLY'
        formal_dut_conclusion = $false
        package_root = $dnp3PackageRoot
        package_version = $dnp3PackageVersion
        package_git_commit = [string]$dnp3BuildInfo.git_commit
        package_git_worktree_state = [string]$dnp3BuildInfo.git_worktree_state
        loopback_scope = 'packaged 127.0.0.1 test outstation only; no DUT access'
        overall_passed = -not $dnp3AnyFailure
        results = @($dnp3Results)
    }
    $dnp3ReportJson = $dnp3Report | ConvertTo-Json -Depth 8
    if ($dnp3ReportFullPath) {
        $dnp3ReportDirectory = Split-Path -Parent $dnp3ReportFullPath
        if ($dnp3ReportDirectory) {
            New-Item -ItemType Directory -Path $dnp3ReportDirectory -Force | Out-Null
        }
        [System.IO.File]::WriteAllText(
            $dnp3ReportFullPath,
            $dnp3ReportJson + [Environment]::NewLine,
            [System.Text.UTF8Encoding]::new($false)
        )
        Write-Host "Compatibility report: $dnp3ReportFullPath"
    }
    Write-Host ($dnp3Report | ConvertTo-Json -Depth 8 -Compress)
    if ($dnp3AnyFailure) {
        throw 'One or more Python/pytest compatibility candidates failed.'
    }
}
finally {
    foreach ($dnp3EnvironmentName in $dnp3SavedEnvironment.Keys) {
        $dnp3Saved = $dnp3SavedEnvironment[$dnp3EnvironmentName]
        Set-Dnp3ProcessEnvironment `
            -Name $dnp3EnvironmentName `
            -Value $(if ($dnp3Saved.exists) { $dnp3Saved.value } else { $null })
    }
}
