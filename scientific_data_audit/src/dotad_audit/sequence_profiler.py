from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any, Iterable


AA_ALLOWED = set("ACDEFGHIKLMNPQRSTVWYBXZJUO")
DNA_ALLOWED = set("ACGTUNRYKMSWBDHV")


def normalize_sequence_for_audit(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper().replace("*", "")


def sequence_hash(value: Any) -> str:
    normalized = normalize_sequence_for_audit(value)
    return hashlib.sha256(normalized.encode("ascii", errors="ignore")).hexdigest()


def vh_vl_pair_hash(vh: Any, vl: Any) -> str:
    payload = f"{normalize_sequence_for_audit(vh)}|{normalize_sequence_for_audit(vl)}"
    return hashlib.sha256(payload.encode("ascii", errors="ignore")).hexdigest()


def invalid_characters(value: Any, sequence_type: str) -> list[str]:
    raw = re.sub(r"\s+", "", str(value or "")).upper().replace("*", "")
    allowed = DNA_ALLOWED if sequence_type == "dna" else AA_ALLOWED
    return sorted(set(raw) - allowed)


def identify_sequence_columns(columns: Iterable[str]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for column in columns:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(column).lower()).strip("_")
        role = ""
        sequence_type = "amino_acid"
        if "cdrh" in normalized:
            role = "CDRH"
        elif "cdrl" in normalized:
            role = "CDRL"
        elif normalized in {"vh", "vh_sequence", "heavy_variable_sequence"} or "vh_seq" in normalized:
            role = "VH"
        elif normalized in {"vl", "vl_sequence", "light_variable_sequence"} or "vl_seq" in normalized:
            role = "VL"
        elif normalized in {"hc", "heavy_chain", "heavy_chain_sequence"} or "hc_seq" in normalized:
            role = "HC"
        elif normalized in {"lc", "light_chain", "light_chain_sequence"} or "lc_seq" in normalized:
            role = "LC"
        elif "vhh" in normalized:
            role = "VHH"
        elif "sequence" in normalized or normalized.endswith("_seq"):
            role = "full_or_unspecified"
        if any(token in normalized for token in ("dna", "nt", "nucleotide")):
            sequence_type = "dna"
        if role:
            output.append({"column": str(column), "role": role, "sequence_type": sequence_type})
    return output


def profile_sequence_table(
    table_id: str,
    records: Iterable[dict[str, Any]],
    *,
    id_columns: list[str] | None = None,
    name_columns: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    records = list(records)
    if not records:
        return [], [], [], []
    columns = list(records[0])
    fields = identify_sequence_columns(columns)
    field_inventory: list[dict[str, Any]] = []
    qc_rows: list[dict[str, Any]] = []
    duplicate_rows: list[dict[str, Any]] = []
    conflict_rows: list[dict[str, Any]] = []
    id_columns = id_columns or _matching_columns(columns, ("dotad_id", "antibody_id", "id"))
    name_columns = name_columns or _matching_columns(columns, ("antibody_name", "name", "inn", "alias"))

    normalized_by_field: dict[str, list[tuple[int, str]]] = defaultdict(list)
    roles = {field["role"]: field["column"] for field in fields}
    for field in fields:
        column = field["column"]
        values = [record.get(column) for record in records]
        nonempty = [value for value in values if str(value or "").strip()]
        raw_space = sum(bool(re.search(r"\s", str(value))) for value in nonempty)
        lower = sum(any(character.islower() for character in str(value)) for value in nonempty)
        stop = sum("*" in str(value) for value in nonempty)
        invalid = Counter()
        lengths: list[int] = []
        for index, value in enumerate(values):
            if not str(value or "").strip():
                continue
            normalized = normalize_sequence_for_audit(value)
            lengths.append(len(normalized))
            invalid.update(invalid_characters(value, field["sequence_type"]))
            normalized_by_field[column].append((index, normalized))
        cdr_only_risk = field["role"].startswith("CDR")
        field_inventory.append(
            {
                "table_id": table_id,
                "column_name": column,
                "sequence_role_guess": field["role"],
                "sequence_type_guess": field["sequence_type"],
                "row_count": len(records),
                "non_missing_count": len(nonempty),
                "missing_count": len(records) - len(nonempty),
                "cdr_only_misclassification_risk": cdr_only_risk,
                "notes": "Role inferred from column name; no source values were modified.",
            }
        )
        qc_rows.append(
            {
                "table_id": table_id,
                "column_name": column,
                "sequence_role_guess": field["role"],
                "sequence_type_guess": field["sequence_type"],
                "non_missing_count": len(nonempty),
                "raw_values_with_whitespace": raw_space,
                "raw_values_with_lowercase": lower,
                "raw_values_with_stop_symbol": stop,
                "values_with_illegal_characters": sum(invalid.values()),
                "illegal_characters_json": json.dumps(sorted(invalid), ensure_ascii=False),
                "min_audit_normalized_length": min(lengths) if lengths else "",
                "median_audit_normalized_length": _median(lengths),
                "max_audit_normalized_length": max(lengths) if lengths else "",
                "very_short_count": sum(length < (3 if cdr_only_risk else 50) for length in lengths),
                "very_long_count": sum(length > (80 if cdr_only_risk else 2000) for length in lengths),
                "notes": "Lengths and characters use an audit-only uppercase/whitespace/stop-stripped representation.",
            }
        )
        groups: dict[str, list[int]] = defaultdict(list)
        for index, normalized in normalized_by_field[column]:
            groups[hashlib.sha256(normalized.encode()).hexdigest()].append(index)
        duplicate_rows.append(
            {
                "table_id": table_id,
                "duplicate_basis": field["role"],
                "non_missing_sequences": len(nonempty),
                "unique_audit_normalized_sequences": len(groups),
                "duplicate_groups": sum(len(indices) > 1 for indices in groups.values()),
                "rows_in_duplicate_groups": sum(len(indices) for indices in groups.values() if len(indices) > 1),
                "notes": "Hashes only are reported; full sequences are not copied to audit outputs.",
            }
        )

    vh_column = roles.get("VH") or roles.get("HC")
    vl_column = roles.get("VL") or roles.get("LC")
    if vh_column or vl_column:
        status = Counter()
        pair_groups: dict[str, list[int]] = defaultdict(list)
        pair_groups_missing_allowed: dict[str, list[int]] = defaultdict(list)
        for index, record in enumerate(records):
            vh = normalize_sequence_for_audit(record.get(vh_column)) if vh_column else ""
            vl = normalize_sequence_for_audit(record.get(vl_column)) if vl_column else ""
            if vh or vl:
                pair_groups_missing_allowed[vh_vl_pair_hash(vh, vl)].append(index)
            if vh and vl:
                status["paired_vh_vl"] += 1
                pair_groups[vh_vl_pair_hash(vh, vl)].append(index)
            elif vh:
                status["heavy_only"] += 1
            elif vl:
                status["light_only"] += 1
            else:
                status["neither"] += 1
        for label, count in status.items():
            duplicate_rows.append(
                {
                    "table_id": table_id,
                    "duplicate_basis": label,
                    "non_missing_sequences": count,
                    "unique_audit_normalized_sequences": "",
                    "duplicate_groups": "",
                    "rows_in_duplicate_groups": "",
                    "notes": "Paired availability status.",
                }
            )
        duplicate_rows.append(
            {
                "table_id": table_id,
                "duplicate_basis": "VH/VL pair",
                "non_missing_sequences": status["paired_vh_vl"],
                "unique_audit_normalized_sequences": len(pair_groups),
                "duplicate_groups": sum(len(indices) > 1 for indices in pair_groups.values()),
                "rows_in_duplicate_groups": sum(len(indices) for indices in pair_groups.values() if len(indices) > 1),
                "notes": "Pair uniqueness uses audit-normalized VH and VL joined in chain order.",
            }
        )
        duplicate_rows.append(
            {
                "table_id": table_id,
                "duplicate_basis": "ordered VH/VL pair (single missing chain allowed)",
                "non_missing_sequences": sum(
                    count for label, count in status.items() if label != "neither"
                ),
                "unique_audit_normalized_sequences": len(pair_groups_missing_allowed),
                "duplicate_groups": sum(
                    len(indices) > 1 for indices in pair_groups_missing_allowed.values()
                ),
                "rows_in_duplicate_groups": sum(
                    len(indices)
                    for indices in pair_groups_missing_allowed.values()
                    if len(indices) > 1
                ),
                "notes": (
                    "Audit candidate only: ordered heavy/light values are normalized independently; "
                    "a missing chain is represented as an empty component."
                ),
            }
        )
        conflict_rows.extend(
            _identity_conflicts(table_id, records, pair_groups, id_columns, name_columns, vh_column, vl_column)
        )
    return field_inventory, qc_rows, duplicate_rows, conflict_rows


def infer_sequence_unique_definition(
    sequence_tables: list[dict[str, Any]],
    script_texts: list[tuple[str, str]],
) -> dict[str, str]:
    evidence: list[str] = []
    duplicate_pair_evidence: list[str] = []
    for row in sequence_tables:
        if "pair" not in str(row.get("duplicate_basis", "")).lower():
            continue
        try:
            duplicate_groups = int(row.get("duplicate_groups") or 0)
        except (TypeError, ValueError):
            duplicate_groups = 0
        if duplicate_groups:
            duplicate_pair_evidence.append(
                f"{row.get('table_id')}: {duplicate_groups} duplicate sequence-pair groups "
                f"cover {row.get('rows_in_duplicate_groups')} rows under `{row.get('duplicate_basis')}`"
            )
    for path, text in script_texts:
        lower = text.lower()
        if "vh" in lower and "vl" in lower and any(token in lower for token in ("drop_duplicates", "duplicated", "unique")):
            evidence.append(f"{path}: contains VH/VL plus explicit uniqueness operation")
        if "canonical" in lower and "sequence" in lower:
            evidence.append(f"{path}: mentions canonical sequence logic")
    if evidence:
        return {
            "status": "probable",
            "definition": "VH/VL pair uniqueness is suggested by static code evidence.",
            "evidence": "; ".join(evidence[:10]),
        }
    return {
        "status": "unable_to_confirm",
        "definition": (
            "The archive does not contain an explicit, end-to-end identity construction script. "
            "Sequence-unique could mean VH, VL, ordered VH/VL pair, full-length sequence, or a source-specific rule. "
            "Candidate duplicate sequence-pair groups across archived rows also show that an antibody entry ID "
            "cannot be assumed to be sequence-unique."
        ),
        "evidence": (
            "No explicit uniqueness implementation found in archived scripts. "
            + ("; ".join(duplicate_pair_evidence[:8]) if duplicate_pair_evidence else "No pair-level evidence was available.")
        ),
    }


def _identity_conflicts(
    table_id: str,
    records: list[dict[str, Any]],
    pair_groups: dict[str, list[int]],
    id_columns: list[str],
    name_columns: list[str],
    vh_column: str,
    vl_column: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair_hash, indices in pair_groups.items():
        if len(indices) < 2:
            continue
        ids = sorted({_first_nonempty(records[index], id_columns) for index in indices} - {""})
        names = sorted({_first_nonempty(records[index], name_columns) for index in indices} - {""})
        if len(ids) > 1 or len(names) > 1:
            rows.append(
                {
                    "table_id": table_id,
                    "conflict_type": "different_identity_labels_same_VH_VL_pair",
                    "record_count": len(indices),
                    "identity_values_json": json.dumps(ids[:20], ensure_ascii=False),
                    "name_values_json": json.dumps(names[:20], ensure_ascii=False),
                    "sequence_pair_sha256": pair_hash,
                    "notes": f"Sequence columns: {vh_column}, {vl_column}; candidate only, no merge performed.",
                }
            )
    name_groups: dict[str, set[str]] = defaultdict(set)
    for record in records:
        name = _first_nonempty(record, name_columns).strip().lower()
        vh = normalize_sequence_for_audit(record.get(vh_column))
        vl = normalize_sequence_for_audit(record.get(vl_column))
        if name and vh and vl:
            name_groups[name].add(vh_vl_pair_hash(vh, vl))
    for name, hashes in name_groups.items():
        if len(hashes) > 1:
            rows.append(
                {
                    "table_id": table_id,
                    "conflict_type": "same_name_different_VH_VL_pair",
                    "record_count": len(hashes),
                    "identity_values_json": "[]",
                    "name_values_json": json.dumps([name], ensure_ascii=False),
                    "sequence_pair_sha256": "",
                    "notes": "Candidate conflict only; variants may be biologically intentional.",
                }
            )
    return rows


def _matching_columns(columns: list[str], preferred: tuple[str, ...]) -> list[str]:
    output = []
    for column in columns:
        normalized = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
        if normalized in preferred or any(token in normalized for token in preferred[:-1]):
            output.append(column)
    return output


def _first_nonempty(record: dict[str, Any], columns: list[str]) -> str:
    for column in columns:
        value = str(record.get(column) or "").strip()
        if value:
            return value
    return ""


def _median(values: list[int]) -> float | str:
    if not values:
        return ""
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2
