from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import openpyxl

from .ids import stable_content_id, stable_source_record_id
from .metadata_export import build_alias_rows, write_alias_rows
from .models import BuildContext, ExportResult
from .workbook_reader import (
    SourceRow,
    iter_source_rows,
    ordered_payload_json,
    serialize_scalar,
    write_tsv,
)


SCRIPT_PATH = "scientific_data_release/src/dotad_release/sequence_export.py"
ALLOWED_SEQUENCE_CLASSES = frozenset(
    {
        "paired_vh_vl",
        "heavy_only",
        "light_only",
        "full_length_only",
        "cdr_only",
        "mixed_or_other",
        "no_sequence",
    }
)
PROVENANCE_FIELDS = (
    "sequence_class",
    "sequence_availability_class",
    "record_id",
    "sequence_record_id",
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
FINGERPRINT_FIELDS = (
    "record_id",
    "fingerprint_id",
    "sequence_record_id",
    "antibody_id",
    "normalized_vh_sha256",
    "normalized_vl_sha256",
    "normalized_vh_vl_pair_sha256",
    "normalized_heavy_only_sha256",
    "normalized_light_only_sha256",
    "normalized_full_sequence_sha256",
    "vh_sha256",
    "vl_sha256",
    "pair_sha256",
    "fingerprint_basis",
    "normalization_rule",
    "source_workbook",
    "source_input_sha256",
    "source_sheet",
    "source_excel_row",
    "source_columns",
)
QC_FIELDS = (
    "record_id",
    "sequence_record_id",
    "antibody_id",
    "sequence_class",
    "vh_available",
    "vl_available",
    "heavy_full_length_available",
    "light_full_length_available",
    "cdr_available",
    "vh_length",
    "vl_length",
    "heavy_full_length_length",
    "light_full_length_length",
    "cdr_total_length",
    "has_illegal_characters",
    "illegal_characters",
    "has_whitespace",
    "has_stop_symbol",
    "duplicate_pair_group_id",
    "duplicate_pair_record_count",
    "qc_notes",
    "source_workbook",
    "source_input_sha256",
    "source_sheet",
    "source_excel_row",
    "source_columns",
)

_VH = ("vh",)
_VL = ("vl",)
_HEAVY_PROTEIN = ("hc_protein_sequence",)
_LIGHT_PROTEIN = ("lc_protein_sequence",)
_HEAVY_DNA = ("hc_dna_sequence",)
_LIGHT_DNA = ("lc_dna_sequence",)
_HEAVY_CDR = ("hcdr1", "hcdr2", "hcdr3")
_LIGHT_CDR = ("lcdr1", "lcdr2", "lcdr3")
_PROTEIN_ALLOWED = frozenset("ACDEFGHIKLMNPQRSTVWYBXZJUO")
_DNA_ALLOWED = frozenset("ACGTUNRYKMSWBDHVX")


def export_sequences(context: BuildContext) -> ExportResult:
    workbook_path = context.core_inputs["metadata_sequences"]
    sheet_name = context.sheet_roles["metadata_sequences"]["sequences"]
    headers = _read_headers(workbook_path, sheet_name)
    source_fields = _source_fields(headers)
    fieldnames = source_fields + PROVENANCE_FIELDS
    rows = [
        _sequence_record(row, source_fields, workbook_path, context)
        for row in iter_source_rows(workbook_path, sheet_name)
    ]
    fingerprints = _fingerprint_rows(rows)
    duplicate_counts = Counter(
        row["pair_sha256"] for row in fingerprints if row["pair_sha256"]
    )
    qc_rows = [
        _qc_record(row, duplicate_counts)
        for row in rows
    ]

    path = context.output_dir / "sequences" / "antibody_sequences.tsv"
    write_tsv(path, rows, fieldnames)
    write_tsv(
        context.output_dir / "sequences" / "sequence_fingerprints.tsv",
        fingerprints,
        FINGERPRINT_FIELDS,
    )
    write_tsv(
        context.output_dir / "sequences" / "sequence_qc.tsv",
        qc_rows,
        QC_FIELDS,
    )
    write_alias_rows(
        context.output_dir / "metadata" / "antibody_aliases.tsv",
        build_alias_rows(rows, SCRIPT_PATH),
    )
    return ExportResult(
        table_name="antibody_sequences",
        path=path,
        fieldnames=fieldnames,
        rows=tuple(rows),
        row_definition="one non-empty source row from sequences",
    )


def _sequence_record(
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
            "sequence_class": classify_sequence(record),
            "sequence_availability_class": classify_sequence(record),
            "record_id": stable_source_record_id(
                "SEQ", source_row.sheet_name, source_row.excel_row
            ),
            "sequence_record_id": stable_source_record_id(
                "SEQ", source_row.sheet_name, source_row.excel_row
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


def classify_sequence(row: dict[str, Any]) -> str:
    lookup = _normalized_lookup(row)
    vh = _present(_pick(lookup, _VH))
    vl = _present(_pick(lookup, _VL))
    heavy_full = _any_present(lookup, _HEAVY_PROTEIN + _HEAVY_DNA)
    light_full = _any_present(lookup, _LIGHT_PROTEIN + _LIGHT_DNA)
    heavy_cdr = _any_present(lookup, _HEAVY_CDR)
    light_cdr = _any_present(lookup, _LIGHT_CDR)

    if vh and vl:
        return "paired_vh_vl"
    if vh:
        return "mixed_or_other" if light_full or light_cdr else "heavy_only"
    if vl:
        return "mixed_or_other" if heavy_full or heavy_cdr else "light_only"

    full_length = heavy_full or light_full
    cdr = heavy_cdr or light_cdr
    if full_length and not cdr:
        return "full_length_only"
    if cdr and not full_length:
        return "cdr_only"
    if not full_length and not cdr:
        return "no_sequence"
    return "mixed_or_other"


def _fingerprint_rows(
    sequence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fingerprints: list[dict[str, Any]] = []
    for row in sequence_rows:
        lookup = _normalized_lookup(row)
        vh = normalize_sequence(_pick(lookup, _VH))
        vl = normalize_sequence(_pick(lookup, _VL))
        heavy_full, heavy_full_type, heavy_column = _full_chain_component(
            lookup,
            _HEAVY_PROTEIN,
            _HEAVY_DNA,
            "hc_protein_sequence",
            "hc_dna_sequence",
        )
        light_full, light_full_type, light_column = _full_chain_component(
            lookup,
            _LIGHT_PROTEIN,
            _LIGHT_DNA,
            "lc_protein_sequence",
            "lc_dna_sequence",
        )
        if not any((vh, vl, heavy_full, light_full)):
            continue
        vh_sha = _sha256_text(vh) if vh else ""
        vl_sha = _sha256_text(vl) if vl else ""
        pair_sha = _sha256_text(f"{vh}\x1f{vl}") if vh and vl else ""
        heavy_component = vh or heavy_full
        light_component = vl or light_full
        heavy_component_type = "vh_protein" if vh else heavy_full_type
        light_component_type = "vl_protein" if vl else light_full_type
        heavy_only_sha = (
            _sha256_text(f"{heavy_component_type}\x1f{heavy_component}")
            if heavy_component and not light_component
            else ""
        )
        light_only_sha = (
            _sha256_text(f"{light_component_type}\x1f{light_component}")
            if light_component and not heavy_component
            else ""
        )
        full_types = {
            value for value in (heavy_full_type, light_full_type) if value
        }
        full_sequence_sha = (
            _sha256_text(
                f"heavy:{heavy_full_type}:{heavy_full}\x1f"
                f"light:{light_full_type}:{light_full}"
            )
            if heavy_full or light_full
            else ""
        )
        if pair_sha:
            basis = "normalized_vh_vl_sha256"
        elif full_sequence_sha:
            if full_types == {"protein"}:
                basis = "normalized_full_protein_sequence_sha256"
            elif full_types == {"dna"}:
                basis = "normalized_full_dna_sequence_sha256"
            else:
                basis = "normalized_mixed_full_sequence_sha256"
        elif vh_sha:
            basis = "normalized_vh_sha256"
        else:
            basis = "normalized_vl_sha256"
        used_columns = []
        if vh:
            used_columns.append("VH")
        if vl:
            used_columns.append("VL")
        if heavy_column:
            used_columns.append(heavy_column)
        if light_column:
            used_columns.append(light_column)
        fingerprint_id = stable_content_id(
            "SEQFP",
            [
                row["record_id"],
                vh_sha,
                vl_sha,
                pair_sha,
                heavy_only_sha,
                light_only_sha,
                full_sequence_sha,
            ],
        )
        fingerprints.append(
            {
                "record_id": fingerprint_id,
                "fingerprint_id": fingerprint_id,
                "sequence_record_id": row["record_id"],
                "antibody_id": row.get("antibody_id"),
                "normalized_vh_sha256": vh_sha,
                "normalized_vl_sha256": vl_sha,
                "normalized_vh_vl_pair_sha256": pair_sha,
                "normalized_heavy_only_sha256": heavy_only_sha,
                "normalized_light_only_sha256": light_only_sha,
                "normalized_full_sequence_sha256": full_sequence_sha,
                "vh_sha256": vh_sha,
                "vl_sha256": vl_sha,
                "pair_sha256": pair_sha,
                "fingerprint_basis": basis,
                "normalization_rule": "NFKC;uppercase;remove_all_whitespace",
                "source_workbook": row["source_workbook"],
                "source_input_sha256": row["source_input_sha256"],
                "source_sheet": row["source_sheet"],
                "source_excel_row": row["source_excel_row"],
                "source_columns": "|".join(used_columns),
            }
        )
    return fingerprints


def _full_chain_component(
    lookup: dict[str, Any],
    protein_fields: tuple[str, ...],
    dna_fields: tuple[str, ...],
    protein_column: str,
    dna_column: str,
) -> tuple[str, str, str]:
    protein = _pick(lookup, protein_fields)
    if _present(protein):
        return normalize_sequence(protein), "protein", protein_column
    dna = _pick(lookup, dna_fields)
    if _present(dna):
        return normalize_sequence(dna), "dna", dna_column
    return "", "", ""


def _qc_record(
    sequence_row: dict[str, Any],
    duplicate_counts: Counter[str],
) -> dict[str, Any]:
    lookup = _normalized_lookup(sequence_row)
    vh_raw = _pick(lookup, _VH)
    vl_raw = _pick(lookup, _VL)
    heavy_protein = _pick(lookup, _HEAVY_PROTEIN)
    light_protein = _pick(lookup, _LIGHT_PROTEIN)
    heavy_dna = _pick(lookup, _HEAVY_DNA)
    light_dna = _pick(lookup, _LIGHT_DNA)
    cdr_values = [
        _pick(lookup, (field,)) for field in _HEAVY_CDR + _LIGHT_CDR
    ]
    protein_values = [vh_raw, vl_raw, heavy_protein, light_protein, *cdr_values]
    dna_values = [heavy_dna, light_dna]
    all_values = protein_values + dna_values
    illegal = _illegal_characters(protein_values, dna_values)
    has_whitespace = any(_contains_whitespace(value) for value in all_values)
    has_stop = any("*" in serialize_scalar(value) for value in all_values)
    vh = normalize_sequence(vh_raw)
    vl = normalize_sequence(vl_raw)
    pair_sha = _sha256_text(f"{vh}\x1f{vl}") if vh and vl else ""
    duplicate_count = duplicate_counts.get(pair_sha, 0)
    duplicate_group = (
        stable_content_id("DUPPAIR", [pair_sha])
        if pair_sha and duplicate_count > 1
        else ""
    )
    notes = []
    if illegal:
        notes.append("illegal_characters_present")
    if has_whitespace:
        notes.append("whitespace_present_in_source")
    if has_stop:
        notes.append("stop_symbol_present")
    if duplicate_group:
        notes.append("duplicate_pair_fingerprint")
    return {
        "record_id": stable_content_id(
            "SEQQC", [sequence_row["record_id"]]
        ),
        "sequence_record_id": sequence_row["record_id"],
        "antibody_id": sequence_row.get("antibody_id"),
        "sequence_class": sequence_row["sequence_class"],
        "vh_available": bool(vh),
        "vl_available": bool(vl),
        "heavy_full_length_available": _present(heavy_protein)
        or _present(heavy_dna),
        "light_full_length_available": _present(light_protein)
        or _present(light_dna),
        "cdr_available": any(_present(value) for value in cdr_values),
        "vh_length": len(vh),
        "vl_length": len(vl),
        "heavy_full_length_length": len(
            normalize_sequence(
                heavy_protein if _present(heavy_protein) else heavy_dna
            )
        ),
        "light_full_length_length": len(
            normalize_sequence(
                light_protein if _present(light_protein) else light_dna
            )
        ),
        "cdr_total_length": sum(
            len(normalize_sequence(value)) for value in cdr_values
        ),
        "has_illegal_characters": bool(illegal),
        "illegal_characters": "".join(illegal),
        "has_whitespace": has_whitespace,
        "has_stop_symbol": has_stop,
        "duplicate_pair_group_id": duplicate_group,
        "duplicate_pair_record_count": duplicate_count if duplicate_group else 0,
        "qc_notes": ";".join(notes),
        "source_workbook": sequence_row["source_workbook"],
        "source_input_sha256": sequence_row["source_input_sha256"],
        "source_sheet": sequence_row["source_sheet"],
        "source_excel_row": sequence_row["source_excel_row"],
        "source_columns": "VH|VL|hc_protein_sequence|lc_protein_sequence|hc_dna_sequence|lc_dna_sequence|HCDR1|HCDR2|HCDR3|LCDR1|LCDR2|LCDR3",
    }


def normalize_sequence(value: Any) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).upper()
    return re.sub(r"\s+", "", normalized)


def _illegal_characters(
    protein_values: list[Any],
    dna_values: list[Any],
) -> tuple[str, ...]:
    illegal: set[str] = set()
    for value in protein_values:
        illegal.update(
            character
            for character in normalize_sequence(value)
            if character not in _PROTEIN_ALLOWED and character != "*"
        )
    for value in dna_values:
        illegal.update(
            character
            for character in normalize_sequence(value)
            if character not in _DNA_ALLOWED and character != "*"
        )
    return tuple(sorted(illegal))


def _contains_whitespace(value: Any) -> bool:
    return value is not None and bool(re.search(r"\s", str(value)))


def _normalized_lookup(row: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for key, value in row.items():
        normalized = _normalized_header(key)
        if normalized and normalized not in lookup:
            lookup[normalized] = value
    return lookup


def _pick(lookup: dict[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        if field in lookup:
            return lookup[field]
    return None


def _any_present(lookup: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return any(_present(_pick(lookup, (field,))) for field in fields)


def _present(value: Any) -> bool:
    return value is not None and (
        not isinstance(value, str) or value.strip() != ""
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return re.sub(r"[^0-9a-z]+", "_", normalized).strip("_")


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
