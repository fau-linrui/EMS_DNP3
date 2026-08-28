Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Initialize-Dnp3BuildEnvironment {
    # Some process hosts expose both `Path` and `PATH`. MSBuild copies the
    # environment into a case-insensitive dictionary and then fails before
    # launching cl.exe when both spellings are present. Merge them into one
    # process-local variable before loading the Visual Studio environment.
    $dnp3PathValues = @(
        [System.Environment]::GetEnvironmentVariables().GetEnumerator()
        | Where-Object { ([string]$_.Key) -ieq 'PATH' }
        | ForEach-Object { [string]$_.Value }
    )
    if ($dnp3PathValues.Count -gt 1) {
        $dnp3MergedPathEntries = @(
            $dnp3PathValues
            | ForEach-Object { $_ -split ';' }
            | Where-Object { $_ }
            | Select-Object -Unique
        )
        $dnp3MergedPath = $dnp3MergedPathEntries -join ';'
        Remove-Item -LiteralPath Env:Path -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath Env:PATH -ErrorAction SilentlyContinue
        [System.Environment]::SetEnvironmentVariable(
            'PATH',
            $dnp3MergedPath,
            [System.EnvironmentVariableTarget]::Process
        )
    }

    $dnp3RequiredCommands = @('cmake', 'ctest', 'cl', 'msbuild')
    $dnp3AllCommandsAvailable = $true
    foreach ($dnp3Command in $dnp3RequiredCommands) {
        if (-not (Get-Command $dnp3Command -ErrorAction SilentlyContinue)) {
            $dnp3AllCommandsAvailable = $false
            break
        }
    }

    if ($dnp3AllCommandsAvailable) {
        return
    }

    $dnp3VswherePath = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $dnp3VswherePath -PathType Leaf)) {
        throw "Visual Studio Installer discovery tool was not found at '$dnp3VswherePath'."
    }

    $dnp3VsInstallPath = & $dnp3VswherePath `
        -latest `
        -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -requires Microsoft.VisualStudio.Component.VC.CMake.Project `
        -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $dnp3VsInstallPath) {
        throw 'Visual Studio 2022 Build Tools with MSVC x64 and CMake components were not found.'
    }

    $dnp3VsInstallPath = $dnp3VsInstallPath.Trim()
    $dnp3DevShellModule = Join-Path $dnp3VsInstallPath 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll'
    if (-not (Test-Path -LiteralPath $dnp3DevShellModule -PathType Leaf)) {
        throw "Visual Studio Developer PowerShell module was not found at '$dnp3DevShellModule'."
    }

    Import-Module -Name $dnp3DevShellModule
    Enter-VsDevShell `
        -VsInstallPath $dnp3VsInstallPath `
        -SkipAutomaticLocation `
        -DevCmdArguments '-arch=x64 -host_arch=x64'

    foreach ($dnp3Command in $dnp3RequiredCommands) {
        if (-not (Get-Command $dnp3Command -ErrorAction SilentlyContinue)) {
            throw "Required build command '$dnp3Command' is unavailable after loading Visual Studio."
        }
    }
}

Initialize-Dnp3BuildEnvironment
