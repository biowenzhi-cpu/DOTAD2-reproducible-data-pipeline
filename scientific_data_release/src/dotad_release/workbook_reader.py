from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl


@dataclass(frozen=True)
class PayloadItem:
    column_index: int
    header: str
    value: Any


@dataclass(frozen=True)
class SourceRow:
    sheet_name: str
    excel_row: int
    payload: tuple[PayloadItem, ...]

    def values(self, header: str) -> tuple[Any, ...]:
        return tuple(item.value for item in self.payload if item.header == header)

    def first(self, header: str, default: Any = None) -> Any:
        values = self.values(header)
        return values[0] if values else default


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def iter_source_rows(path: Path, sheet_name: str) -> Iterator[SourceRow]:
    workbook = openpyxl.load_workbook(
        filename=Path(path), read_only=True, data_only=True
    )
    try:
        sheet = workbook[sheet_name]
        row_iter = sheet.iter_rows(values_only=True)
        headers = next(row_iter, ())
        normalized_headers = tuple("" if value is None else str(value) for value in headers)
        for excel_row, values in enumerate(row_iter, start=2):
            width = max(len(normalized_headers), len(values))
            padded_headers = normalized_headers + ("",) * (width - len(normalized_headers))
            padded_values = tuple(values) + (None,) * (width - len(values))
            if all(_is_blank(value) for value in padded_values):
                continue
            payload = tuple(
                PayloadItem(index, padded_headers[index - 1], padded_values[index - 1])
                for index in range(1, width + 1)
            )
            yield SourceRow(sheet_name=sheet_name, excel_row=excel_row, payload=payload)
    finally:
        workbook.close()


def serialize_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return format(value, ".15g")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=isinstance(value, dict),
            separators=(",", ":"),
            default=serialize_scalar,
        )
    return str(value)


def ordered_payload_json(row: SourceRow) -> str:
    payload = [
        {
            "column_index": item.column_index,
            "header": item.header,
            "value": _json_safe_value(item.value),
        }
        for item in row.payload
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return serialize_scalar(value)


def write_tsv(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    fieldnames: Sequence[str],
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: serialize_scalar(row.get(field)) for field in fieldnames}
            )


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
