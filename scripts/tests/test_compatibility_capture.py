from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "test-compatibility.ps1"
SHELLS = [shell for shell in ("powershell.exe", "pwsh.exe") if shutil.which(shell)]
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELLS, reason="Windows PowerShell integration")


def capture(shell: str, code: str, arguments: list[str] | None = None, options: str = "") -> dict:
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = f"""
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile({literal(str(SCRIPT))}, [ref]$tokens, [ref]$errors)
if (@($errors).Count -gt 0) {{ throw 'Script does not parse on this PowerShell version' }}
foreach ($name in @('ConvertTo-Dnp3NativeArgument','Invoke-Dnp3CommandCapture')) {{
    $function = $ast.Find({{param($entry) $entry -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $entry.Name -eq $name}}, $true)
    Invoke-Expression $function.Extent.Text
}}
$arguments = ({literal(json.dumps(['-c', code, *(arguments or [])], ensure_ascii=False))} | ConvertFrom-Json)
try {{
    $result = Invoke-Dnp3CommandCapture -Executable {literal(sys.executable)} -Arguments $arguments -Description 'synthetic capture probe' -Quiet {options}
    $result | ConvertTo-Json -Compress
}}
catch {{
    @{{error=$_.Exception.Message; stack=$_.ScriptStackTrace; position=$_.InvocationInfo.PositionMessage}} | ConvertTo-Json -Compress
}}
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.lstrip("\ufeff"))


@pytest.mark.parametrize("shell", SHELLS or ["powershell.exe"])
def test_successful_stderr_warning_does_not_pollute_json_stdout(shell: str) -> None:
    result = capture(shell, "import sys,json; sys.stderr.write('benign warning\\n'); print(json.dumps({'ok':True,'text':'中文'}))")
    assert result["exit_code"] == 0
    assert result["stderr"].replace("\r\n", "\n") == "benign warning\n"
    assert json.loads(result["stdout"]) == {"ok": True, "text": "中文"}


@pytest.mark.parametrize("shell", SHELLS or ["powershell.exe"])
@pytest.mark.parametrize("code,expected", [
    ("pass", None),
    ("import sys; sys.exit(7)", "exit code 7"),
    ("import sys; print('stdout detail'); sys.stderr.write('stderr detail'); sys.exit(3)", "stderr detail"),
])
def test_empty_output_and_failed_exit_codes(shell: str, code: str, expected: str | None) -> None:
    result = capture(shell, code)
    if expected is None:
        assert result == {"stdout": "", "stderr": "", "exit_code": 0}
    else:
        assert expected in result["error"]


@pytest.mark.parametrize("shell", SHELLS or ["powershell.exe"])
def test_native_arguments_round_trip_empty_spaces_quotes_and_backslashes(shell: str) -> None:
    arguments = ["", "space separated", 'a"b', "ends in a slash\\", 'backslash\\"quote', "two\\\\", "中文路径\\", "single'quote", "$no_expansion"]
    result = capture(shell, "import sys,json; print(json.dumps(sys.argv[1:]))", arguments)
    assert json.loads(result["stdout"]) == arguments


@pytest.mark.parametrize("shell", SHELLS or ["powershell.exe"])
def test_both_pipes_are_drained_and_output_is_bounded(shell: str) -> None:
    code = "import sys; sys.stderr.write('E'*100000); sys.stderr.flush(); sys.stdout.write('O'*100000)"
    result = capture(shell, code)
    assert len(result["stdout"]) == len(result["stderr"]) == 100000
    assert result["exit_code"] == 0
    limited = capture(shell, code, options="-MaxOutputChars 1024")
    assert "output limit" in limited["error"]


@pytest.mark.parametrize("shell", SHELLS or ["powershell.exe"])
def test_process_timeout_is_bounded(shell: str) -> None:
    result = capture(shell, "import time; time.sleep(20)", options="-TimeoutSeconds 1")
    assert "timeout" in result["error"]
