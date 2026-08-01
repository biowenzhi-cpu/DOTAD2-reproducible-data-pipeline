from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

import openpyxl

from .ids import stable_source_record_id
from .models import BuildContext, ExportResult
from .workbook_reader import (
    SourceRow,
    iter_source_rows,
    ordered_payload_json,
    write_tsv,
)


FIELDS = (
    "record_id",
    "literature_record_id",
    "antibody_id",
    "antibody_name",
    "source_study_label",
    "pmid",
    "doi",
    "source_url",
    "evidence_type",
    "source_workbook",
    "input_file",
    "input_file_sha256",
    "source_sheet",
    "source_excel_row",
    "source_input_sha256",
    "release_version",
    "source_payload_json",
    "lineage_sources_json",
)


def export_literature(context: BuildContext) -> ExportResult:
    workbook_path = context.core_inputs["literature"]
    excluded = set(context.sheet_roles["literature"].get("excluded", []))
    source_index = _source_metadata_index(context)
    workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        sheet_names = [
            name for name in workbook.sheetnames if name not in excluded
        ]
    finally:
        workbook.close()
    rows: list[dict[str, Any]] = []
    for sheet_name in sheet_names:
        for source_row in iter_source_rows(workbook_path, sheet_name):
            lookup = _first_value_lookup(source_row)
            source_matches = source_index.get(sheet_name, ())
            source_lookup = (
                source_matches[0][0] if len(source_matches) == 1 else {}
            )
            lineage_sources = [_source_ref(workbook_path, context, source_row)]
            if len(source_matches) == 1:
                lineage_sources.append(
                    _dictionary_ref(context, source_matches[0][1])
                )
            rows.append(
                {
                    "record_id": stable_source_record_id(
                        "LIT", source_row.sheet_name, source_row.excel_row
                    ),
                    "literature_record_id": stable_source_record_id(
                        "LIT", source_row.sheet_name, source_row.excel_row
                    ),
                    "antibody_id": _pick(
                        lookup, "antibody_id", "antibodyid", "dotad_id"
                    ),
                    "antibody_name": _pick(
                        lookup, "antibody_name", "antibodyname", "name", "inn"
                    ),
                    "source_study_label": source_row.sheet_name,
                    "pmid": _pick(lookup, "pmid", "pubmed_id")
                    or _pick(source_lookup, "pmid", "pubmed_id"),
                    "doi": _pick(lookup, "doi")
                    or _pick(source_lookup, "doi"),
                    "source_url": _pick(
                        lookup, "source_url", "url", "link", "source_link"
                    )
                    or _pick(
                        source_lookup,
                        "source_url",
                        "url",
                        "link",
                        "source_link",
                    ),
                    "evidence_type": _pick(
                        lookup,
                        "evidence_type",
                        "evidence_class",
                        "data_type",
                    )
                    or _pick(
                        source_lookup,
                        "evidence_type",
                        "evidence_class",
                        "data_type",
                    ),
                    "source_workbook": workbook_path.name,
                    "input_file": workbook_path.name,
                    "input_file_sha256": context.input_shas["literature"],
                    "source_sheet": source_row.sheet_name,
                    "source_excel_row": source_row.excel_row,
                    "source_input_sha256": context.input_shas["literature"],
                    "release_version": context.release_version,
                    "source_payload_json": ordered_payload_json(source_row),
                    "lineage_sources_json": json.dumps(
                        lineage_sources,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
    path = context.output_dir / "evidence" / "literature_curated_records.tsv"
    write_tsv(path, rows, FIELDS)
    return ExportResult(
        table_name="literature_curated_records",
        path=path,
        fieldnames=FIELDS,
        rows=tuple(rows),
        row_definition=(
            "one non-empty source row from each literature study sheet; "
            "the link metadata sheet is excluded"
        ),
    )


def _source_metadata_index(
    context: BuildContext,
) -> dict[str, tuple[tuple[dict[str, Any], SourceRow], ...]]:
    dictionary = context.core_inputs.get("data_dictionary")
    roles = context.sheet_roles.get("data_dictionary", {})
    sheet_name = roles.get("literature_sources")
    if dictionary is None or not sheet_name:
        return {}
    grouped: dict[str, list[tuple[dict[str, Any], SourceRow]]] = {}
    for source_row in iter_source_rows(dictionary, sheet_name):
        lookup = _first_value_lookup(source_row)
        source_sheet = str(_pick(lookup, "source_sheet")).strip()
        if source_sheet:
            grouped.setdefault(source_sheet, []).append((lookup, source_row))
    return {
        key: tuple(value)
        for key, value in grouped.items()
    }


def _source_ref(
    workbook_path: Any,
    context: BuildContext,
    source_row: SourceRow,
) -> dict[str, Any]:
    return {
        "input_file": workbook_path.name,
        "input_file_sha256": context.input_shas["literature"],
        "input_sheet": source_row.sheet_name,
        "input_excel_row": source_row.excel_row,
        "input_column_or_columns": "*",
    }


def _dictionary_ref(
    context: BuildContext,
    source_row: SourceRow,
) -> dict[str, Any]:
    dictionary = context.core_inputs["data_dictionary"]
    return {
        "input_file": dictionary.name,
        "input_file_sha256": context.input_shas["data_dictionary"],
        "input_sheet": source_row.sheet_name,
        "input_excel_row": source_row.excel_row,
        "input_column_or_columns": "*",
    }


def _first_value_lookup(row: SourceRow) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for item in row.payload:
        key = _normalized_header(item.header)
        if key and key not in lookup:
            lookup[key] = item.value
    return lookup


def _normalized_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return re.sub(r"[^0-9a-z]+", "_", normalized).strip("_")


def _pick(lookup: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in lookup:
            return lookup[key]
    return ""
