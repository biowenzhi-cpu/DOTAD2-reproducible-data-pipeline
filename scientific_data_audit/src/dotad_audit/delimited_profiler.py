from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator, TextIO

from charset_normalizer import from_bytes


def detect_encoding_and_delimiter(sample: bytes, suffix: str = "") -> tuple[str, str]:
    match = from_bytes(sample).best()
    encoding = match.encoding if match and match.encoding else "utf-8"
    text = sample.decode(encoding, errors="replace")
    delimiter = "\t" if suffix.lower() == ".tsv" else ","
    try:
        delimiter = csv.Sniffer().sniff(text, delimiters=",\t;|").delimiter
    except csv.Error:
        if text.count("\t") > text.count(","):
            delimiter = "\t"
    return encoding, delimiter


def iter_delimited_chunks(
    path: Path,
    *,
    chunksize: int = 50_000,
) -> Iterator[list[dict[str, str]]]:
    with path.open("rb") as raw:
        sample = raw.read(128 * 1024)
    encoding, delimiter = detect_encoding_and_delimiter(sample, path.suffix)
    with path.open("r", encoding=encoding, errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        chunk: list[dict[str, str]] = []
        for row in reader:
            chunk.append(dict(row))
            if len(chunk) >= chunksize:
                yield chunk
                chunk = []
        if chunk:
            yield chunk


def profile_delimited(path: Path, table_id: str, max_samples: int = 3) -> dict[str, Any]:
    with path.open("rb") as raw:
        sample = raw.read(128 * 1024)
    encoding, delimiter = detect_encoding_and_delimiter(sample, path.suffix)
    return profile_text_stream(
        path.open("r", encoding=encoding, errors="replace", newline=""),
        table_id,
        delimiter=delimiter,
        max_samples=max_samples,
        notes=f"encoding={encoding}; delimiter={repr(delimiter)}; streaming exact row count",
    )


def profile_text_stream(
    handle: TextIO,
    table_id: str,
    *,
    delimiter: str,
    max_samples: int = 3,
    notes: str = "",
) -> dict[str, Any]:
    try:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, [])
        header = [str(value).strip() or f"unnamed_{index + 1}" for index, value in enumerate(header)]
        row_count = 0
        duplicate_count = 0
        empty_rows = 0
        seen_rows: set[tuple[str, ...]] = set()
        column_stats = [_new_column_stat(name) for name in header]
        for row in reader:
            row_count += 1
            normalized = tuple((row[index] if index < len(row) else "").strip() for index in range(len(header)))
            if not any(normalized):
                empty_rows += 1
            if normalized in seen_rows:
                duplicate_count += 1
            elif len(seen_rows) < 200_000:
                seen_rows.add(normalized)
            for index, value in enumerate(normalized):
                _update_column_stat(column_stats[index], value, max_samples=max_samples)
        return {
            "table_id": table_id,
            "row_count": row_count,
            "column_count": len(header),
            "column_names": header,
            "duplicate_full_row_count": duplicate_count,
            "fully_empty_row_count": empty_rows,
            "fully_empty_column_count": sum(stat["non_missing_count"] == 0 for stat in column_stats),
            "column_stats": _finalize_stats(column_stats, row_count),
            "read_status": "readable",
            "notes": notes,
        }
    finally:
        handle.close()


def profile_json(path: Path, table_id: str, max_samples: int = 3) -> list[dict[str, Any]]:
    size = path.stat().st_size
    if size > 50 * 1024 * 1024:
        return [
            {
                "table_id": table_id,
                "row_count": "",
                "column_count": "",
                "column_names": [],
                "duplicate_full_row_count": "",
                "fully_empty_row_count": "",
                "fully_empty_column_count": "",
                "column_stats": [],
                "read_status": "partially_read",
                "notes": f"Large JSON ({size} bytes); metadata-only audit to avoid unbounded memory.",
            }
        ]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [{"table_id": table_id, "read_status": "unreadable", "notes": str(exc)}]
    tables: list[tuple[str, Any]]
    if isinstance(data, list):
        tables = [(table_id, data)]
    elif isinstance(data, dict):
        list_values = [(str(key), value) for key, value in data.items() if isinstance(value, list)]
        if list_values:
            tables = [(f"{table_id}:{name}", values) for name, values in list_values]
        elif data and all(isinstance(value, dict) for value in data.values()):
            records = [{"_mapping_key": key, **value} for key, value in data.items()]
            tables = [(table_id, records)]
        else:
            tables = [(table_id, [data])]
    else:
        tables = [(table_id, [{"value": data}])]
    return [_profile_record_list(records, tid, max_samples) for tid, records in tables]


def profile_sqlite(path: Path, file_id: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for index, name in enumerate(tables, start=1):
            quoted = name.replace('"', '""')
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{quoted}")')]
            output.append(
                {
                    "table_id": f"{file_id}:SQL{index:03d}",
                    "sheet_or_table_name": name,
                    "row_count": row_count,
                    "column_count": len(columns),
                    "column_names": columns,
                    "duplicate_full_row_count": "",
                    "fully_empty_row_count": "",
                    "fully_empty_column_count": "",
                    "column_stats": [],
                    "read_status": "readable",
                    "notes": "SQLite opened read-only; content rows not materialized.",
                }
            )
        connection.close()
    except sqlite3.Error as exc:
        output.append({"table_id": f"{file_id}:SQL", "read_status": "unreadable", "notes": str(exc)})
    return output


def _profile_record_list(records: list[Any], table_id: str, max_samples: int) -> dict[str, Any]:
    mapping_rows = [row for row in records if isinstance(row, dict)]
    columns = sorted({str(key) for row in mapping_rows for key in row})
    stats = [_new_column_stat(name) for name in columns]
    duplicates = 0
    seen: set[str] = set()
    empty_rows = 0
    for row in mapping_rows:
        marker = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        if marker in seen:
            duplicates += 1
        else:
            seen.add(marker)
        values = [row.get(column) for column in columns]
        if not any(value not in (None, "") for value in values):
            empty_rows += 1
        for index, value in enumerate(values):
            _update_column_stat(stats[index], value, max_samples=max_samples)
    return {
        "table_id": table_id,
        "row_count": len(records),
        "column_count": len(columns),
        "column_names": columns,
        "duplicate_full_row_count": duplicates,
        "fully_empty_row_count": empty_rows,
        "fully_empty_column_count": sum(stat["non_missing_count"] == 0 for stat in stats),
        "column_stats": _finalize_stats(stats, len(records)),
        "read_status": "readable" if len(mapping_rows) == len(records) else "partially_read",
        "notes": "JSON loaded exactly." if len(mapping_rows) == len(records) else "Non-object JSON rows omitted from column profile.",
    }


def _new_column_stat(name: str) -> dict[str, Any]:
    return {
        "column_name": name,
        "non_missing_count": 0,
        "samples": [],
        "unique": set(),
        "types": Counter(),
        "unique_capped": False,
    }


def _update_column_stat(stat: dict[str, Any], value: Any, *, max_samples: int) -> None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return
    stat["non_missing_count"] += 1
    rendered = str(value).strip()
    if len(stat["samples"]) < max_samples and rendered not in stat["samples"]:
        stat["samples"].append(rendered[:160])
    if len(stat["unique"]) < 100_000:
        stat["unique"].add(rendered)
    else:
        stat["unique_capped"] = True
    stat["types"][_infer_type(value)] += 1


def _finalize_stats(stats: list[dict[str, Any]], row_count: int) -> list[dict[str, Any]]:
    output = []
    for stat in stats:
        dominant = stat["types"].most_common(1)[0][0] if stat["types"] else "empty"
        output.append(
            {
                "column_name": stat["column_name"],
                "inferred_data_type": dominant,
                "non_missing_count": stat["non_missing_count"],
                "missing_count": max(0, row_count - stat["non_missing_count"]),
                "missing_fraction": (
                    round((row_count - stat["non_missing_count"]) / row_count, 6)
                    if row_count
                    else 0.0
                ),
                "unique_count": len(stat["unique"]),
                "sample_values": stat["samples"],
                "unique_count_is_lower_bound": stat["unique_capped"],
            }
        )
    return output


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    rendered = str(value).strip()
    try:
        int(rendered)
        return "integer"
    except ValueError:
        pass
    try:
        float(rendered)
        return "number"
    except ValueError:
        return "string"
