"""Bounded atomic persistence for machine-readable benchmark reports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid


_DEFAULT_MAX_REPORT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PersistedReport:
    """Identity of one report after its bytes are durably written."""

    path: Path
    size_bytes: int
    sha256: str


def write_json_report(
    report: Mapping[str, Any],
    path: Path | str,
    *,
    overwrite: bool = False,
    max_bytes: int = _DEFAULT_MAX_REPORT_BYTES,
) -> PersistedReport:
    """Write one bounded JSON report atomically and return its SHA-256 identity."""

    if not isinstance(report, Mapping):
        raise TypeError("report must be a mapping")
    if report.get("schema_version") != 1:
        raise ValueError("report schema_version must be exactly 1")
    report_type = report.get("report_type")
    if not isinstance(report_type, str) or not report_type.strip():
        raise ValueError("report_type must be a non-empty string")
    if type(overwrite) is not bool:
        raise TypeError("overwrite must be a boolean")
    if (
        type(max_bytes) is not int
        or not 1024 <= max_bytes <= 1024 * 1024 * 1024
    ):
        raise ValueError("max_bytes must be between 1024 and 1073741824")

    try:
        encoded = (
            json.dumps(
                dict(report),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"report is not finite JSON data: {error}") from error
    if not encoded or len(encoded) > max_bytes:
        raise ValueError(f"encoded report exceeds the {max_bytes}-byte limit")

    target = Path(path).expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise FileExistsError(f"report already exists: {target}")
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, target)
        else:
            # The supported platform is Windows, where rename fails if another
            # writer created the destination after the existence check.
            os.rename(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    digest = hashlib.sha256(encoded).hexdigest()
    return PersistedReport(path=target, size_bytes=len(encoded), sha256=digest)


__all__ = ["PersistedReport", "write_json_report"]
