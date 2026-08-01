from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import openpyxl

from .ids import stable_content_id, stable_source_record_id
from .models import BuildContext, ExportResult
from .workbook_reader import (
    SourceRow,
    iter_source_rows,
    ordered_payload_json,
    serialize_scalar,
    write_tsv,
)


SCRIPT_PATH = "scientific_data_release/src/dotad_release/metadata_export.py"
PROVENANCE_FIELDS = (
    "record_id",
    "metadata_record_id",
    "input_file",
    "input_file_sha256",
    "source_workbook",
    "source_sheet",
    "source_excel_row",
    "source_input_sha256",
    "source_payload_json",
    "transformation_rule",
    "script_path",
    "release_version",
)
ALIAS_FIELDS = (
    "record_id",
    "alias_id",
    "source_record_id",
    "antibody_id",
    "alias",
    "normalized_alias",
    "alias_role",
    "canonical_name",
    "source_workbook",
    "source_sheet",
    "source_excel_row",
    "source_input_sha256",
    "transformation_rule",
    "script_path",
    "release_version",
)


def export_metadata(context: BuildContext) -> ExportResult:
    workbook_path = context.core_inputs["metadata_sequences"]
    sheet_name = context.sheet_roles["metadata_sequences"]["metadata"]
    headers = _read_headers(workbook_path, sheet_name)
    source_fields = _source_fields(headers)
    fieldnames = source_fields + PROVENANCE_FIELDS
    rows = [
        _metadata_record(row, source_fields, workbook_path, context)
        for row in iter_source_rows(workbook_path, sheet_name)
    ]

    path = context.output_dir / "metadata" / "antibody_metadata.tsv"
    write_tsv(path, rows, fieldnames)
    write_alias_rows(
        context.output_dir / "metadata" / "antibody_aliases.tsv",
        build_alias_rows(rows, SCRIPT_PATH),
    )
    return ExportResult(
        table_name="antibody_metadata",
        path=path,
        fieldnames=fieldnames,
        rows=tuple(rows),
        row_definition="one non-empty source row from metadata",
    )


def _metadata_record(
    source_row: SourceRow,
    source_fields: tuple[str, ...],
    workbook_path: Path,
    context: BuildContext,
) -> dict[str, Any]:
    record = {
        field: source_row.payload[index].value
        for index, field in enumerate(source_fields)
    }
    record.update(
        {
            "record_id": stable_source_record_id(
                "META", source_row.sheet_name, source_row.excel_row
            ),
            "metadata_record_id": stable_source_record_id(
                "META", source_row.sheet_name, source_row.excel_row
            ),
            "input_file": workbook_path.name,
            "input_file_sha256": context.input_shas["metadata_sequences"],
            "source_workbook": workbook_path.name,
            "source_sheet": source_row.sheet_name,
            "source_excel_row": source_row.excel_row,
            "source_input_sha256": context.input_shas["metadata_sequences"],
            "source_payload_json": ordered_payload_json(source_row),
            "transformation_rule": "source_row_preserved",
            "script_path": SCRIPT_PATH,
            "release_version": context.release_version,
        }
    )
    return record


def build_alias_rows(
    source_rows: Iterable[Mapping[str, Any]],
    script_path: str,
) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    for row in source_rows:
        alias = row.get("antibody_name")
        normalized_alias = normalize_name(alias)
        if not normalized_alias:
            continue
        antibody_id = row.get("antibody_id")
        aliases.append(
            {
                "record_id": stable_content_id(
                    "ALIAS",
                    [
                        serialize_scalar(row["record_id"]),
                        serialize_scalar(antibody_id),
                        normalized_alias,
                    ],
                ),
                "alias_id": stable_content_id(
                    "ALIAS",
                    [
                        serialize_scalar(row["record_id"]),
                        serialize_scalar(antibody_id),
                        normalized_alias,
                    ],
                ),
                "source_record_id": row["record_id"],
                "antibody_id": antibody_id,
                "alias": alias,
                "normalized_alias": normalized_alias,
                "alias_role": "source_name_occurrence",
                "canonical_name": "",
                "source_workbook": row["source_workbook"],
                "source_sheet": row["source_sheet"],
                "source_excel_row": row["source_excel_row"],
                "source_input_sha256": row["source_input_sha256"],
                "transformation_rule": "source_name_occurrence_only",
                "script_path": script_path,
                "release_version": row["release_version"],
            }
        )
    return aliases


def write_alias_rows(
    path: Path,
    alias_rows: Iterable[Mapping[str, Any]],
) -> None:
    combined: dict[str, dict[str, Any]] = {}
    destination = Path(path)
    if destination.is_file():
        with destination.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row.get("alias_id"):
                    combined[row["alias_id"]] = dict(row)
    for row in alias_rows:
        alias_id = serialize_scalar(row.get("alias_id"))
        if alias_id:
            row = dict(row)
            row.setdefault("record_id", alias_id)
            combined[alias_id] = dict(row)
    ordered = sorted(
        combined.values(),
        key=lambda row: (
            serialize_scalar(row.get("source_workbook")),
            serialize_scalar(row.get("source_sheet")),
            _excel_row_sort_key(row.get("source_excel_row")),
            serialize_scalar(row.get("alias_id")),
        ),
    )
    write_tsv(destination, ordered, ALIAS_FIELDS)


def normalize_name(value: Any) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _excel_row_sort_key(value: Any) -> tuple[int, str]:
    serialized = serialize_scalar(value)
    try:
        return int(serialized), ""
    except ValueError:
        return 2**63 - 1, serialized


def _read_headers(path: Path, sheet_name: str) -> tuple[str, ...]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        row = next(
            workbook[sheet_name].iter_rows(min_row=1, max_row=1, values_only=True),
            (),
        )
        return tuple("" if value is None else str(value) for value in row)
    finally:
        workbook.close()


def _source_fields(headers: tuple[str, ...]) -> tuple[str, ...]:
    counts = Counter(headers)
    fields: list[str] = []
    for index, header in enumerate(headers, start=1):
        if header and counts[header] == 1 and header not in PROVENANCE_FIELDS:
            fields.append(header)
        elif header:
            fields.append(f"{header}__source_column_{index}")
        else:
            fields.append(f"source_column_{index}")
    return tuple(fields)
