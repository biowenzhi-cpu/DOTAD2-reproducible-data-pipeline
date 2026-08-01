from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterable


ID_PATTERN = re.compile(r"(?i)(^|_)(dotad|antibody|source|record|sequence|assay|dataset|task)?_?id$")


def candidate_key_columns(columns: Iterable[str]) -> list[str]:
    output: list[str] = []
    for column in columns:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(column).lower()).strip("_")
        if normalized == "id" or normalized.endswith("_id") or ID_PATTERN.search(normalized):
            output.append(str(column))
        elif normalized in {"pmid", "doi"}:
            output.append(str(column))
    return output


def audit_key_column(table_id: str, records: Iterable[dict[str, Any]], column: str) -> dict[str, Any]:
    records = list(records)
    values = [str(record.get(column) or "").strip() for record in records]
    nonempty = [value for value in values if value]
    counts = Counter(nonempty)
    duplicate_count = sum(count - 1 for count in counts.values() if count > 1)
    unique_count = len(counts)
    missing_count = len(records) - len(nonempty)
    fraction = unique_count / len(nonempty) if nonempty else 0.0
    if nonempty and missing_count == 0 and duplicate_count == 0:
        status = "confirmed_candidate"
    elif fraction >= 0.98 and missing_count / max(1, len(records)) <= 0.02:
        status = "probable_candidate"
    else:
        status = "not_unique"
    return {
        "table_id": table_id,
        "column_name": column,
        "row_count": len(records),
        "non_missing_count": len(nonempty),
        "unique_count": unique_count,
        "duplicate_count": duplicate_count,
        "missing_count": missing_count,
        "uniqueness_fraction": round(fraction, 6),
        "candidate_key_status": status,
        "notes": "Candidate status is statistical only; semantic key meaning requires documentation.",
    }


def audit_foreign_key(
    child_table: str,
    child_column: str,
    child_records: Iterable[dict[str, Any]],
    parent_table: str,
    parent_column: str,
    parent_records: Iterable[dict[str, Any]],
    *,
    confidence: str = "possible",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parent_values = {
        _normalize_identifier(record.get(parent_column))
        for record in parent_records
        if _normalize_identifier(record.get(parent_column))
    }
    child_values = [
        _normalize_identifier(record.get(child_column))
        for record in child_records
        if _normalize_identifier(record.get(child_column))
    ]
    unmatched = [value for value in child_values if value not in parent_values]
    matched = len(child_values) - len(unmatched)
    result = {
        "child_table": child_table,
        "child_column": child_column,
        "parent_table": parent_table,
        "parent_column": parent_column,
        "relationship_confidence": confidence,
        "child_non_missing": len(child_values),
        "matched_count": matched,
        "unmatched_count": len(unmatched),
        "match_fraction": round(matched / len(child_values), 6) if child_values else 0.0,
        "examples_of_unmatched_json": json.dumps(sorted(set(unmatched))[:10], ensure_ascii=False),
        "notes": "Identifier comparison trims whitespace and uppercases values; no source records were changed.",
    }
    orphan_rows = [
        {
            "child_table": child_table,
            "child_column": child_column,
            "orphan_identifier": value,
            "candidate_parent_table": parent_table,
            "candidate_parent_column": parent_column,
            "relationship_confidence": confidence,
            "notes": "Reported as candidate orphan under this relationship.",
        }
        for value in sorted(set(unmatched))[:1000]
    ]
    return result, orphan_rows


def identify_probable_dotad_relationships(
    tables: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str, dict[str, Any], str, str]]:
    candidates: list[tuple[dict[str, Any], str]] = []
    for table in tables:
        columns = list(table.get("columns", []))
        for column in columns:
            normalized = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
            if normalized in {"dotad_id", "antibody_id"} or (
                "dotad" in normalized and "id" in normalized
            ):
                candidates.append((table, column))
    output = []
    for child_table, child_column in candidates:
        for parent_table, parent_column in candidates:
            if child_table["table_id"] == parent_table["table_id"]:
                continue
            parent_count = int(parent_table.get("row_count") or 0)
            child_count = int(child_table.get("row_count") or 0)
            confidence = "probable" if parent_count and parent_count <= child_count else "possible"
            output.append((child_table, child_column, parent_table, parent_column, confidence))
    return output


def _normalize_identifier(value: Any) -> str:
    return str(value or "").strip().upper()
