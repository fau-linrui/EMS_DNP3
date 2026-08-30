from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from .models import MeasurementRecord, ReadHeader


POINT_TABLE_SCHEMA_VERSION = 1
POINT_TABLE_COLUMNS = (
    "point_id",
    "point_name",
    "point_type",
    "index",
    "static_group",
    "static_variation",
    "event_group",
    "event_variation",
    "event_class",
    "read_qualifier",
    "engineering_unit",
    "expected_min",
    "expected_max",
    "enabled",
    "notes",
)

_POINT_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_UNSIGNED_PATTERN = re.compile(r"^[0-9]+$")
_POINT_GROUPS: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        "binary_input": (1, 2),
        "double_bit_binary_input": (3, 4),
        "binary_output_status": (10, 11),
        "counter": (20, 22),
        "frozen_counter": (21, 23),
        "analog_input": (30, 32),
        "analog_output_status": (40, 42),
        "octet_string": (110, 111),
    }
)
_POINT_VARIATIONS: Mapping[
    str, tuple[frozenset[int] | None, frozenset[int] | None]
] = MappingProxyType(
    {
        "binary_input": (frozenset({1, 2}), frozenset({1, 2, 3})),
        "double_bit_binary_input": (
            frozenset({1, 2}),
            frozenset({1, 2, 3}),
        ),
        "binary_output_status": (frozenset({1, 2}), frozenset({1, 2})),
        "counter": (frozenset({1, 2, 5, 6}), frozenset({1, 2, 5, 6})),
        "frozen_counter": (
            frozenset({1, 2, 5, 6, 9, 10}),
            frozenset({1, 2, 5, 6}),
        ),
        "analog_input": (
            frozenset({1, 2, 3, 4, 5, 6}),
            frozenset({1, 2, 3, 4, 5, 6, 7, 8}),
        ),
        "analog_output_status": (
            frozenset({1, 2, 3, 4}),
            frozenset({1, 2, 3, 4, 5, 6, 7, 8}),
        ),
        # For octet strings, the variation is the encoded string length.
        "octet_string": (None, None),
    }
)
_NUMERIC_POINT_TYPES = frozenset(
    {"analog_input", "analog_output_status", "counter", "frozen_counter"}
)
_MAX_POINT_TABLE_BYTES = 4 * 1024 * 1024
_MAX_POINT_COUNT = 100_000


class PointTableError(ValueError):
    """A stable, user-facing point-table validation failure."""


@dataclass(frozen=True, slots=True)
class PointDefinition:
    point_id: str
    point_name: str
    point_type: str
    index: int
    static_group: int
    static_variation: int
    event_group: int | None
    event_variation: int | None
    event_class: int | None
    read_qualifier: str
    engineering_unit: str | None
    expected_min: float | None
    expected_max: float | None
    enabled: bool
    notes: str | None

    @property
    def capability_id(self) -> str:
        """Return the capability-matrix ID for this exact static object."""

        if self.static_group == 110:
            return "OBJ.G110.LENGTH_VARIANTS"
        return f"OBJ.G{self.static_group}.V{self.static_variation}"

    @property
    def event_capability_id(self) -> str | None:
        """Return the exact event capability ID, or ``None`` for static-only rows."""

        if self.event_group is None or self.event_variation is None:
            return None
        if self.event_group == 111:
            return "OBJ.G111.LENGTH_VARIANTS"
        return f"OBJ.G{self.event_group}.V{self.event_variation}"

    @property
    def read_qualifier_capability_id(self) -> str:
        """Return the exact qualifier used by this row's one-point READ."""

        return (
            "QUAL.Q00.REVIEW"
            if self.read_qualifier == "range8"
            else "QUAL.Q01.REVIEW"
        )

    def read_header(self) -> ReadHeader:
        """Create the exact one-point static READ header for this row."""

        factory = (
            ReadHeader.range8
            if self.read_qualifier == "range8"
            else ReadHeader.range16
        )
        return factory(
            self.static_group,
            self.static_variation,
            self.index,
            self.index,
        )

    def matching_measurements(
        self, measurements: tuple[MeasurementRecord, ...]
    ) -> tuple[MeasurementRecord, ...]:
        return tuple(
            item
            for item in measurements
            if item.kind == self.point_type
            and item.index == self.index
            and item.group == self.static_group
            and item.variation == self.static_variation
            and not item.is_event
        )

    def matching_event_measurements(
        self, measurements: tuple[MeasurementRecord, ...]
    ) -> tuple[MeasurementRecord, ...]:
        """Return exact event-object matches for this point-table row."""

        if self.event_group is None or self.event_variation is None:
            return ()
        return tuple(
            item
            for item in measurements
            if item.kind == self.point_type
            and item.index == self.index
            and item.group == self.event_group
            and item.variation == self.event_variation
            and item.is_event
        )

    def value_in_expected_range(self, value: object) -> bool:
        if self.expected_min is None or self.expected_max is None:
            return True
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            normalized = float(value)
        except (OverflowError, TypeError, ValueError):
            return False
        if not (-float("inf") < normalized < float("inf")):
            return False
        return self.expected_min <= normalized <= self.expected_max

    def to_mapping(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "point_name": self.point_name,
            "point_type": self.point_type,
            "index": self.index,
            "static_group": self.static_group,
            "static_variation": self.static_variation,
            "event_group": self.event_group,
            "event_variation": self.event_variation,
            "event_class": self.event_class,
            "read_qualifier": self.read_qualifier,
            "engineering_unit": self.engineering_unit,
            "expected_min": self.expected_min,
            "expected_max": self.expected_max,
            "enabled": self.enabled,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class PointTable:
    source_path: Path
    points: tuple[PointDefinition, ...]
    schema_version: int = POINT_TABLE_SCHEMA_VERSION

    @property
    def enabled_points(self) -> tuple[PointDefinition, ...]:
        return tuple(point for point in self.points if point.enabled)

    @property
    def by_id(self) -> Mapping[str, PointDefinition]:
        return MappingProxyType({point.point_id: point for point in self.points})

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "points": [point.to_mapping() for point in self.points],
        }


def _error(path: Path, row: int | None, message: str) -> PointTableError:
    location = str(path) if row is None else f"{path}: row {row}"
    return PointTableError(f"{location}: {message}")


def _required_text(
    path: Path,
    row: int,
    value: str,
    field: str,
    maximum_utf8_bytes: int,
) -> str:
    normalized = value.strip()
    if not normalized:
        raise _error(path, row, f"{field} must not be empty")
    if len(normalized.encode("utf-8")) > maximum_utf8_bytes:
        raise _error(
            path,
            row,
            f"{field} must not exceed {maximum_utf8_bytes} UTF-8 bytes",
        )
    return normalized


def _optional_text(
    path: Path,
    row: int,
    value: str,
    field: str,
    maximum_utf8_bytes: int,
) -> str | None:
    if not value.strip():
        return None
    return _required_text(path, row, value, field, maximum_utf8_bytes)


def _unsigned(
    path: Path,
    row: int,
    value: str,
    field: str,
    minimum: int,
    maximum: int,
    *,
    optional: bool = False,
) -> int | None:
    normalized = value.strip()
    if optional and not normalized:
        return None
    if not _UNSIGNED_PATTERN.fullmatch(normalized):
        raise _error(path, row, f"{field} must be an unsigned decimal integer")
    parsed = int(normalized)
    if not minimum <= parsed <= maximum:
        raise _error(
            path,
            row,
            f"{field} must be between {minimum} and {maximum}",
        )
    return parsed


def _optional_number(
    path: Path, row: int, value: str, field: str
) -> float | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as error:
        raise _error(path, row, f"{field} must be a finite decimal number") from error
    if not parsed.is_finite():
        raise _error(path, row, f"{field} must be a finite decimal number")
    result = float(parsed)
    if result in {float("inf"), float("-inf")}:
        raise _error(path, row, f"{field} is outside the supported numeric range")
    return result


def _point_from_row(
    path: Path, row_number: int, row: Mapping[str, str]
) -> PointDefinition:
    point_id = _required_text(path, row_number, row["point_id"], "point_id", 128)
    if not _POINT_ID_PATTERN.fullmatch(point_id):
        raise _error(
            path,
            row_number,
            "point_id must start with an ASCII letter and contain only "
            "letters, digits, '.', '_', ':', or '-'",
        )
    point_name = _required_text(
        path, row_number, row["point_name"], "point_name", 512
    )
    point_type = row["point_type"].strip()
    if point_type not in _POINT_GROUPS:
        raise _error(
            path,
            row_number,
            "point_type must be one of: " + ", ".join(sorted(_POINT_GROUPS)),
        )
    index = _unsigned(path, row_number, row["index"], "index", 0, 65535)
    static_group = _unsigned(
        path, row_number, row["static_group"], "static_group", 1, 255
    )
    static_variation = _unsigned(
        path,
        row_number,
        row["static_variation"],
        "static_variation",
        1,
        255,
    )
    assert index is not None and static_group is not None
    assert static_variation is not None
    expected_static_group, expected_event_group = _POINT_GROUPS[point_type]
    if static_group != expected_static_group:
        raise _error(
            path,
            row_number,
            f"static_group {static_group} does not match {point_type}; "
            f"expected group {expected_static_group}",
        )
    allowed_static_variations, allowed_event_variations = _POINT_VARIATIONS[
        point_type
    ]
    if (
        allowed_static_variations is not None
        and static_variation not in allowed_static_variations
    ):
        allowed = ", ".join(str(value) for value in sorted(allowed_static_variations))
        raise _error(
            path,
            row_number,
            f"static_variation {static_variation} is not defined for "
            f"{point_type} group {static_group}; allowed: {allowed}",
        )

    event_group = _unsigned(
        path,
        row_number,
        row["event_group"],
        "event_group",
        1,
        255,
        optional=True,
    )
    event_variation = _unsigned(
        path,
        row_number,
        row["event_variation"],
        "event_variation",
        1,
        255,
        optional=True,
    )
    event_class = _unsigned(
        path,
        row_number,
        row["event_class"],
        "event_class",
        1,
        3,
        optional=True,
    )
    event_fields = (event_group, event_variation, event_class)
    if any(value is None for value in event_fields) and any(
        value is not None for value in event_fields
    ):
        raise _error(
            path,
            row_number,
            "event_group, event_variation, and event_class must be all blank "
            "or all populated",
        )
    if event_group is not None and event_group != expected_event_group:
        raise _error(
            path,
            row_number,
            f"event_group {event_group} does not match {point_type}; "
            f"expected group {expected_event_group}",
        )
    if (
        event_variation is not None
        and allowed_event_variations is not None
        and event_variation not in allowed_event_variations
    ):
        allowed = ", ".join(str(value) for value in sorted(allowed_event_variations))
        raise _error(
            path,
            row_number,
            f"event_variation {event_variation} is not defined for "
            f"{point_type} group {expected_event_group}; allowed: {allowed}",
        )

    read_qualifier = row["read_qualifier"].strip()
    if read_qualifier not in {"range8", "range16"}:
        raise _error(
            path,
            row_number,
            "read_qualifier must be 'range8' or 'range16'",
        )
    if read_qualifier == "range8" and index > 255:
        raise _error(
            path,
            row_number,
            "range8 cannot address an index greater than 255",
        )

    engineering_unit = _optional_text(
        path,
        row_number,
        row["engineering_unit"],
        "engineering_unit",
        64,
    )
    expected_min = _optional_number(
        path, row_number, row["expected_min"], "expected_min"
    )
    expected_max = _optional_number(
        path, row_number, row["expected_max"], "expected_max"
    )
    if (expected_min is None) != (expected_max is None):
        raise _error(
            path,
            row_number,
            "expected_min and expected_max must be both blank or both populated",
        )
    if expected_min is not None:
        if point_type not in _NUMERIC_POINT_TYPES:
            raise _error(
                path,
                row_number,
                "expected_min/expected_max are allowed only for numeric point types",
            )
        assert expected_max is not None
        if expected_min > expected_max:
            raise _error(
                path,
                row_number,
                "expected_min must not exceed expected_max",
            )

    enabled_text = row["enabled"].strip()
    if enabled_text not in {"true", "false"}:
        raise _error(path, row_number, "enabled must be exactly 'true' or 'false'")
    notes = _optional_text(path, row_number, row["notes"], "notes", 2048)
    return PointDefinition(
        point_id=point_id,
        point_name=point_name,
        point_type=point_type,
        index=index,
        static_group=static_group,
        static_variation=static_variation,
        event_group=event_group,
        event_variation=event_variation,
        event_class=event_class,
        read_qualifier=read_qualifier,
        engineering_unit=engineering_unit,
        expected_min=expected_min,
        expected_max=expected_max,
        enabled=enabled_text == "true",
        notes=notes,
    )


def load_point_table(path: str | Path) -> PointTable:
    """Load and strictly validate a v1 UTF-8 CSV point table."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise PointTableError(f"point table does not exist: {source}")
    try:
        size = source.stat().st_size
    except OSError as error:
        raise PointTableError(f"cannot inspect point table {source}: {error}") from error
    if size > _MAX_POINT_TABLE_BYTES:
        raise PointTableError(
            f"point table exceeds {_MAX_POINT_TABLE_BYTES} bytes: {source}"
        )

    points: list[PointDefinition] = []
    seen_ids: dict[str, int] = {}
    seen_addresses: dict[tuple[str, int], int] = {}
    try:
        with source.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            header = tuple(reader.fieldnames or ())
            if header != POINT_TABLE_COLUMNS:
                raise _error(
                    source,
                    1,
                    "CSV header must exactly equal: " + ",".join(POINT_TABLE_COLUMNS),
                )
            for row in reader:
                if len(points) >= _MAX_POINT_COUNT:
                    raise _error(
                        source,
                        reader.line_num,
                        f"point count exceeds {_MAX_POINT_COUNT}",
                    )
                if None in row:
                    raise _error(
                        source, reader.line_num, "row has more values than the header"
                    )
                if any(value is None for value in row.values()):
                    raise _error(
                        source, reader.line_num, "row has fewer values than the header"
                    )
                if not any(value.strip() for value in row.values()):
                    raise _error(source, reader.line_num, "blank rows are not allowed")
                point = _point_from_row(source, reader.line_num, row)
                folded_id = point.point_id.casefold()
                if folded_id in seen_ids:
                    raise _error(
                        source,
                        reader.line_num,
                        f"duplicate point_id {point.point_id!r}; first used at row "
                        f"{seen_ids[folded_id]}",
                    )
                address = (point.point_type, point.index)
                if address in seen_addresses:
                    raise _error(
                        source,
                        reader.line_num,
                        f"duplicate {point.point_type} index {point.index}; first used "
                        f"at row {seen_addresses[address]}",
                    )
                seen_ids[folded_id] = reader.line_num
                seen_addresses[address] = reader.line_num
                points.append(point)
    except (OSError, UnicodeError, csv.Error) as error:
        raise PointTableError(f"cannot read point table {source}: {error}") from error

    if not points:
        raise PointTableError(f"point table contains no data rows: {source}")
    return PointTable(source_path=source, points=tuple(points))


__all__ = [
    "POINT_TABLE_COLUMNS",
    "POINT_TABLE_SCHEMA_VERSION",
    "PointDefinition",
    "PointTable",
    "PointTableError",
    "load_point_table",
]
