from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from .ids import stable_content_id, stable_source_record_id
from .models import BuildContext, ExportResult
from .workbook_reader import (
    SourceRow,
    iter_source_rows,
    ordered_payload_json,
    write_tsv,
)


PROVENANCE_FIELDS = (
    "source_dictionary_workbook",
    "source_dictionary_sheet",
    "source_excel_row",
    "source_input_sha256",
    "source_payload_json",
)
MAPPING_FIELDS = (
    "record_id",
    "source_workbook",
    "source_sheet",
    "source_field",
    "source_definition_workbook",
    "source_definition_field",
    "field_dictionary_record_id",
    "source_excel_row",
    "source_input_sha256",
)
REVIEW_FIELDS = (
    "record_id",
    "issue_type",
    "review_status",
    "fallback_source_sheet",
    "review_reason",
    "reviewer_decision",
)


def export_dictionaries(context: BuildContext) -> list[ExportResult]:
    workbook_path = context.core_inputs["data_dictionary"]
    roles = context.sheet_roles["data_dictionary"]
    field_sheet = roles["field_dictionary"]
    assay_sheet = roles["assay_endpoint_dictionary"]
    dedicated_controlled_sheet = _dedicated_controlled_sheet(roles)
    controlled_sheet = (
        dedicated_controlled_sheet or roles["controlled_vocabulary_source"]
    )

    field_result, field_source_rows = _export_source_sheet(
        context,
        workbook_path,
        field_sheet,
        "field_dictionary",
        "one non-empty source row from Field Dictionary",
    )
    assay_result, _ = _export_source_sheet(
        context,
        workbook_path,
        assay_sheet,
        "assay_endpoint_dictionary",
        "one non-empty source row from Assay Endpoint Dictionary",
    )
    controlled_definition = (
        f"one non-empty source row from {controlled_sheet}"
        if dedicated_controlled_sheet
        else (
            "one non-empty source row from Terminology Missingness; "
            "no dedicated controlled-vocabulary sheet is present"
        )
    )
    controlled_result, _ = _export_source_sheet(
        context,
        workbook_path,
        controlled_sheet,
        "controlled_vocabularies",
        controlled_definition,
    )
    mapping_result = _export_source_field_mapping(
        context, field_source_rows
    )
    review_result = _export_semantics_review(
        context,
        controlled_sheet,
        dedicated_controlled_sheet is None,
    )
    return [
        field_result,
        assay_result,
        controlled_result,
        mapping_result,
        review_result,
    ]


def _export_source_sheet(
    context: BuildContext,
    workbook_path: Path,
    sheet_name: str,
    table_name: str,
    row_definition: str,
) -> tuple[ExportResult, tuple[SourceRow, ...]]:
    source_rows = tuple(iter_source_rows(workbook_path, sheet_name))
    source_headers = _source_headers(source_rows)
    rows = tuple(
        _dictionary_record(context, workbook_path, source_row)
        for source_row in source_rows
    )
    fields = ("record_id", *source_headers, *PROVENANCE_FIELDS)
    path = context.output_dir / "dictionaries" / f"{table_name}.tsv"
    write_tsv(path, rows, fields)
    return (
        ExportResult(
            table_name=table_name,
            path=path,
            fieldnames=fields,
            rows=rows,
            row_definition=row_definition,
        ),
        source_rows,
    )


def _dictionary_record(
    context: BuildContext,
    workbook_path: Path,
    source_row: SourceRow,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "record_id": stable_source_record_id(
            "DICT", source_row.sheet_name, source_row.excel_row
        )
    }
    for item in source_row.payload:
        if item.header and item.header not in record:
            record[item.header] = item.value
    record.update(
        {
            "source_dictionary_workbook": workbook_path.name,
            "source_dictionary_sheet": source_row.sheet_name,
            "source_excel_row": source_row.excel_row,
            "source_input_sha256": context.input_shas["data_dictionary"],
            "source_payload_json": ordered_payload_json(source_row),
        }
    )
    return record


def _export_source_field_mapping(
    context: BuildContext,
    source_rows: tuple[SourceRow, ...],
) -> ExportResult:
    rows: list[dict[str, Any]] = []
    for source_row in source_rows:
        lookup = _explicit_field_mapping_lookup(source_row)
        source_definition_workbook = lookup.get(
            "source_definition_workbook"
        )
        source_definition_field = lookup.get("source_definition_field")
        if _is_blank(source_definition_workbook) or _is_blank(
            source_definition_field
        ):
            continue
        dictionary_record_id = stable_source_record_id(
            "DICT", source_row.sheet_name, source_row.excel_row
        )
        rows.append(
            {
                "record_id": stable_source_record_id(
                    "MAP", source_row.sheet_name, source_row.excel_row
                ),
                "source_workbook": lookup.get("source_workbook", ""),
                "source_sheet": lookup.get("source_sheet", ""),
                "source_field": lookup.get("field_name", ""),
                "source_definition_workbook": source_definition_workbook,
                "source_definition_field": source_definition_field,
                "field_dictionary_record_id": dictionary_record_id,
                "source_excel_row": source_row.excel_row,
                "source_input_sha256": context.input_shas["data_dictionary"],
            }
        )
    path = context.output_dir / "dictionaries" / "source_field_mapping.tsv"
    write_tsv(path, rows, MAPPING_FIELDS)
    return ExportResult(
        table_name="source_field_mapping",
        path=path,
        fieldnames=MAPPING_FIELDS,
        rows=tuple(rows),
        row_definition=(
            "one Field Dictionary row with both explicit "
            "source_definition_workbook and source_definition_field values"
        ),
    )


def _export_semantics_review(
    context: BuildContext,
    fallback_sheet: str,
    review_required: bool,
) -> ExportResult:
    rows: tuple[dict[str, Any], ...] = ()
    if review_required:
        rows = (
            {
                "record_id": stable_content_id(
                    "SEMREV",
                    [
                        "MISSING_CONTROLLED_VOCABULARY_SHEET",
                        fallback_sheet,
                    ],
                ),
                "issue_type": "MISSING_CONTROLLED_VOCABULARY_SHEET",
                "review_status": "NEEDS_REVIEW",
                "fallback_source_sheet": fallback_sheet,
                "review_reason": (
                    "A dedicated controlled-vocabulary table is absent; "
                    f"{fallback_sheet} source rows are exported without "
                    "synthesizing controlled terms."
                ),
                "reviewer_decision": "",
            },
        )
    path = (
        context.output_dir
        / "review"
        / "field_semantics_review_queue.tsv"
    )
    write_tsv(path, rows, REVIEW_FIELDS)
    return ExportResult(
        table_name="field_semantics_review_queue",
        path=path,
        fieldnames=REVIEW_FIELDS,
        rows=rows,
        row_definition=(
            "one deterministic review item for each unresolved field "
            "semantics condition"
        ),
    )


def _source_headers(rows: tuple[SourceRow, ...]) -> tuple[str, ...]:
    if not rows:
        return ()
    headers: list[str] = []
    for item in rows[0].payload:
        if item.header and item.header not in headers:
            headers.append(item.header)
    return tuple(headers)


def _explicit_field_mapping_lookup(row: SourceRow) -> dict[str, Any]:
    allowed = {
        "source_workbook",
        "source_sheet",
        "field_name",
        "source_definition_workbook",
        "source_definition_field",
    }
    lookup: dict[str, Any] = {}
    for item in row.payload:
        normalized = _normalized_header(item.header)
        if normalized in allowed and normalized not in lookup:
            lookup[normalized] = item.value
    return lookup


def _normalized_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return re.sub(r"[^0-9a-z]+", "_", normalized).strip("_")


def _dedicated_controlled_sheet(roles: dict[str, Any]) -> str | None:
    for key in ("controlled_vocabulary", "controlled_vocabularies"):
        value = roles.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
