from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
from typing import Any, Mapping


_MAX_INPUT_BYTES = 128 * 1024 * 1024
_MAX_BUILD_INFO_BYTES = 1024 * 1024
_MAX_REPORT_TEXT_CHARS = 16_384
_BUILD_INFO_FIELDS = frozenset(
    {
        "schema_version",
        "host_version",
        "git_commit",
        "git_worktree_state",
        "opendnp3_version",
        "opendnp3_commit",
        "compiler_id",
        "compiler_version",
        "build_time_utc",
        "build_configuration",
        "target_architecture",
        "protocol_schema_version",
        "capability_matrix_sha256",
        "dependency_lock_sha256",
    }
)
_JSON_SECRET_PATTERN = re.compile(
    r'(?i)("(?:safety_token|password|secret|api_key|private_key|operator_id|dut_id)"'
    r'\s*:\s*")[^"]*(")'
)
_ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"(?i)\b(safety_token|password|secret|api_key|private_key|operator_id|dut_id)"
    r"\s*=\s*([^\s,;]+)"
)
_HEX_SAFETY_TOKEN_PATTERN = re.compile(r"(?i)\b[0-9a-f]{32}\b")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path, *, maximum_bytes: int = _MAX_INPUT_BYTES) -> str:
    size = path.stat().st_size
    if size > maximum_bytes:
        raise ValueError(f"evidence input exceeds {maximum_bytes} bytes: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def redact_text(value: object, *, maximum_chars: int = _MAX_REPORT_TEXT_CHARS) -> str:
    text = str(value)
    text = _JSON_SECRET_PATTERN.sub(r"\1<redacted>\2", text)
    text = _ASSIGNMENT_SECRET_PATTERN.sub(r"\1=<redacted>", text)
    text = _HEX_SAFETY_TOKEN_PATTERN.sub("<redacted-128-bit-token>", text)
    text = _BEARER_PATTERN.sub("Bearer <redacted>", text)
    if len(text) <= maximum_chars:
        return text
    head = maximum_chars // 2
    tail = maximum_chars - head
    omitted = len(text) - maximum_chars
    return (
        text[:head]
        + f"\n... <{omitted} characters omitted by evidence bound> ...\n"
        + text[-tail:]
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _input_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return {"present": False, "file_name": resolved.name}
    return {
        "present": True,
        "file_name": resolved.name,
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _load_build_info(host_executable: Path | None) -> dict[str, Any] | None:
    if host_executable is None:
        return None
    path = host_executable.expanduser().resolve().parent / "build-info.json"
    if not path.is_file() or path.stat().st_size > _MAX_BUILD_INFO_BYTES:
        return None
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    return {
        key: value
        for key, value in document.items()
        if key in _BUILD_INFO_FIELDS
        and isinstance(value, (str, int, bool))
        and not isinstance(value, float)
    }


def _git_identity(repository_root: Path | None) -> dict[str, Any]:
    if repository_root is None:
        return {"available": False}
    root = repository_root.expanduser().resolve()
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3.0,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    revision = invoke("rev-parse", "HEAD")
    if revision is None or revision.returncode != 0:
        return {"available": False}
    commit = revision.stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return {"available": False}
    status = invoke("status", "--porcelain", "--untracked-files=no")
    return {
        "available": True,
        "commit": commit,
        "tracked_worktree_state": (
            "unknown"
            if status is None or status.returncode != 0
            else "clean" if not status.stdout.strip() else "dirty"
        ),
    }


def _compile_path_redactions(
    paths: tuple[tuple[Path | None, str], ...],
) -> tuple[tuple[re.Pattern[str], str], ...]:
    flags = re.IGNORECASE if os.name == "nt" else 0
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path, replacement in paths:
        if path is None:
            continue
        resolved = path.expanduser().resolve()
        for spelling in (str(resolved), resolved.as_posix()):
            if not spelling:
                continue
            identity = spelling.casefold() if os.name == "nt" else spelling
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append((spelling, replacement))
    # Replace children before parents so a file receives the most precise label.
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(
        (re.compile(re.escape(spelling), flags), replacement)
        for spelling, replacement in candidates
    )


class EvidenceRecorder:
    """Bounded pytest evidence with hashes instead of private config contents."""

    def __init__(
        self,
        output_root: Path,
        *,
        repository_root: Path | None = None,
        execution_root: Path | None = None,
        host_executable: Path | None = None,
        inputs: Mapping[str, Path | None] | None = None,
        runner: Mapping[str, Any] | None = None,
    ) -> None:
        root = output_root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.run_id = f"pytest-{timestamp}-{secrets.token_hex(4)}"
        self.run_directory = root / self.run_id
        self.run_directory.mkdir(exist_ok=False)
        private_inputs = tuple(
            path for path in (inputs or {}).values() if path is not None
        )
        self._path_redactions = _compile_path_redactions(
            (
                (self.run_directory, "<evidence-run>"),
                (host_executable, "<host-executable>"),
                *((path, "<input-file>") for path in private_inputs),
                (repository_root, "<repository-root>"),
                (execution_root, "<test-root>"),
                (root, "<evidence-root>"),
            )
        )
        self._results: dict[str, dict[str, Any]] = {}
        self._finalized = False
        input_records: dict[str, Any] = {}
        for name, path in sorted((inputs or {}).items()):
            if path is not None:
                input_records[name] = _input_record(path)
        if host_executable is not None:
            input_records["host_executable"] = _input_record(host_executable)
        self._manifest: dict[str, Any] = {
            "schema_version": 1,
            "run_id": self.run_id,
            "state": "RUNNING",
            "started_at_utc": utc_now(),
            "completed_at_utc": None,
            "pytest_exit_status": None,
            "runner": {
                key: (
                    self._redact(value, maximum_chars=4096)
                    if isinstance(value, str)
                    else value
                )
                for key, value in {
                    "python_implementation": platform.python_implementation(),
                    "python_version": platform.python_version(),
                    "platform": platform.platform(),
                    **dict(runner or {}),
                }.items()
            },
            "source_control": _git_identity(repository_root),
            "build_info": _load_build_info(host_executable),
            "inputs": input_records,
            "results": None,
            "redaction": {
                "private_input_contents_stored": False,
                "absolute_input_paths_in_metadata_stored": False,
                "known_absolute_paths_redacted": True,
                "arbitrary_test_output_requires_review": True,
                "report_text_limit_chars": _MAX_REPORT_TEXT_CHARS,
                "sensitive_fields_redacted": True,
            },
        }
        _atomic_json(self.run_directory / "manifest.json", self._manifest)

    def _redact(
        self, value: object, *, maximum_chars: int = _MAX_REPORT_TEXT_CHARS
    ) -> str:
        text = str(value)
        for pattern, replacement in self._path_redactions:
            text = pattern.sub(lambda _: replacement, text)
        return redact_text(text, maximum_chars=maximum_chars)

    def record_phase(
        self,
        *,
        nodeid: str,
        phase: str,
        outcome: str,
        duration_seconds: float,
        markers: tuple[str, ...] = (),
        capabilities: tuple[str, ...] = (),
        was_xfail: str | None = None,
        failure: str = "",
        captured_output: str = "",
    ) -> None:
        if self._finalized:
            raise RuntimeError("evidence recorder has already been finalized")
        # Display names are deliberately lossy (redaction and truncation).
        # Key all phases by the original identity without retaining/persisting
        # the private nodeid. Export only an opaque, run-local case identifier;
        # even the identity digest must not appear in the evidence files.
        identity = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()
        safe_nodeid = self._redact(nodeid, maximum_chars=4096)
        test = self._results.setdefault(
            identity,
            {
                "case_id": f"case-{len(self._results) + 1:06d}",
                "nodeid": safe_nodeid,
                "markers": sorted(
                    {self._redact(value, maximum_chars=256) for value in markers}
                ),
                "capabilities": sorted(
                    {
                        self._redact(value, maximum_chars=256)
                        for value in capabilities
                    }
                ),
                "phases": [],
            },
        )
        test["phases"].append(
            {
                "phase": phase,
                "outcome": outcome,
                "duration_seconds": max(0.0, float(duration_seconds)),
                "was_xfail": self._redact(was_xfail) if was_xfail else None,
                "failure": self._redact(failure) if failure else None,
                "captured_output": (
                    self._redact(captured_output) if captured_output else None
                ),
            }
        )

    def finalize(self, pytest_exit_status: int) -> Path:
        if self._finalized:
            return self.run_directory / "manifest.json"
        results_document = {
            "schema_version": 1,
            "run_id": self.run_id,
            "tests": list(self._results.values()),
        }
        results_path = self.run_directory / "pytest-results.json"
        _atomic_json(results_path, results_document)
        self._manifest["state"] = "COMPLETED"
        self._manifest["completed_at_utc"] = utc_now()
        self._manifest["pytest_exit_status"] = int(pytest_exit_status)
        self._manifest["results"] = {
            "file_name": results_path.name,
            "sha256": sha256_file(results_path),
            "test_count": len(self._results),
            "phase_count": sum(
                len(test["phases"]) for test in self._results.values()
            ),
        }
        _atomic_json(self.run_directory / "manifest.json", self._manifest)
        self._finalized = True
        return self.run_directory / "manifest.json"


__all__ = ["EvidenceRecorder", "redact_text", "sha256_file", "utc_now"]
