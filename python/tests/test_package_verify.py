from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dnp3_master.package_verify import (
    PackageVerificationError,
    verify_unpacked_package,
)


def write_manifest(root: Path, files: list[Path]) -> None:
    entries = []
    for path in sorted(files):
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    (root / "package-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package_version": "1.2.3",
                "manifest_scope": "all packaged files except package-manifest.json",
                "files": entries,
            }
        ),
        encoding="utf-8",
    )


def test_verify_unpacked_package_accepts_exact_file_set(tmp_path: Path) -> None:
    first = tmp_path / "bin" / "host.exe"
    first.parent.mkdir()
    first.write_bytes(b"host")
    second = tmp_path / "README.md"
    second.write_text("read me", encoding="utf-8")
    write_manifest(tmp_path, [first, second])

    result = verify_unpacked_package(tmp_path)

    assert result == {"ok": True, "package_version": "1.2.3", "verified_files": 2}


@pytest.mark.parametrize("mutation", ["changed", "extra", "missing"])
def test_verify_unpacked_package_rejects_tampering(
    tmp_path: Path, mutation: str
) -> None:
    packaged = tmp_path / "data.txt"
    packaged.write_text("original", encoding="utf-8")
    write_manifest(tmp_path, [packaged])
    if mutation == "changed":
        packaged.write_text("modified", encoding="utf-8")
    elif mutation == "extra":
        (tmp_path / "extra.txt").write_text("extra", encoding="utf-8")
    else:
        packaged.unlink()

    with pytest.raises(PackageVerificationError):
        verify_unpacked_package(tmp_path)
