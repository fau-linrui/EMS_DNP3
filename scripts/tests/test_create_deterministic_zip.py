from __future__ import annotations

import json
from pathlib import Path
import zipfile

from scripts.create_deterministic_zip import (
    PACKAGE_MANIFEST_NAME,
    create_archive,
    sha256_file,
)


def test_archive_is_stable_sorted_and_has_verified_manifest(tmp_path: Path) -> None:
    source = tmp_path / "stage"
    (source / "nested").mkdir(parents=True)
    (source / "z.txt").write_text("z\n", encoding="utf-8")
    (source / "nested" / "a.txt").write_text("a\n", encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    _, first_sidecar, first_digest = create_archive(
        source, first, package_version="9.8.7"
    )
    _, second_sidecar, second_digest = create_archive(
        source, second, package_version="9.8.7"
    )

    assert first.read_bytes() == second.read_bytes()
    assert first_digest == second_digest == sha256_file(first)
    assert first_sidecar.read_text(encoding="ascii") == f"{first_digest}  first.zip\n"
    assert second_sidecar.read_text(encoding="ascii") == f"{second_digest}  second.zip\n"
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        manifest = json.loads(archive.read(PACKAGE_MANIFEST_NAME))
        assert manifest["schema_version"] == 1
        assert manifest["package_version"] == "9.8.7"
        assert [entry["path"] for entry in manifest["files"]] == [
            "nested/a.txt",
            "z.txt",
        ]


def test_archive_rejects_output_inside_staging_tree(tmp_path: Path) -> None:
    source = tmp_path / "stage"
    source.mkdir()
    (source / "data.txt").write_text("data", encoding="utf-8")

    try:
        create_archive(source, source / "bad.zip", package_version="1.0.0")
    except ValueError as error:
        assert "must not be inside" in str(error)
    else:
        raise AssertionError("archive output inside staging tree was accepted")
