from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .ids import stable_content_id
from .models import BuildContext, ExportResult
from .workbook_reader import write_tsv


LINEAGE_FIELDS = (
    "lineage_id",
    "output_table",
    "output_record_id",
    "input_file",
    "input_file_sha256",
    "input_sheet",
    "input_excel_row",
    "input_column_or_columns",
    "transformation_rule_id",
    "transformation_script",
    "transformation_script_commit",
    "release_version",
)

COVERAGE_FIELDS = (
    "output_table",
    "released_rows",
    "rows_with_lineage",
    "rows_without_lineage",
    "coverage_fraction",
    "lineage_required",
)

RULE_FIELDS = (
    "transformation_rule_id",
    "rule_name",
    "rule_description",
    "inputs",
    "outputs",
    "lossless",
    "code_location",
    "notes",
)


RULES = {
    "COPY_WITH_METADATA": {
        "rule_name": "Copy source row with provenance metadata",
        "rule_description": (
            "Preserve the source-row values and append stable identifiers and "
            "source location metadata."
        ),
        "inputs": "non-empty workbook source row",
        "outputs": "source-preserving release row",
        "lossless": "true",
        "code_location": "dotad_release export modules",
        "notes": "No scientific-value normalization is performed.",
    },
    "NORMALIZE_SEQUENCE_FOR_HASH": {
        "rule_name": "Normalize sequence for fingerprinting",
        "rule_description": (
            "Apply the documented sequence normalization only for SHA-256 "
            "fingerprint calculation; retain the original source sequence."
        ),
        "inputs": "source sequence field or paired VH/VL fields",
        "outputs": "sequence fingerprint row",
        "lossless": "false",
        "code_location": "dotad_release.sequence_export",
        "notes": "Fingerprints are not canonical antibody identities.",
    },
    "ANALYZE_SEQUENCE_QC": {
        "rule_name": "Derive sequence quality-control indicators",
        "rule_description": (
            "Report availability, length, whitespace, stop-symbol, illegal-"
            "character, and duplicate-group indicators."
        ),
        "inputs": "source sequence fields",
        "outputs": "sequence QC row",
        "lossless": "false",
        "code_location": "dotad_release.sequence_export",
        "notes": "QC notes do not reproduce full sequence strings.",
    },
    "EXTRACT_ALIAS": {
        "rule_name": "Extract source-linked name occurrence",
        "rule_description": (
            "Record a source-provided antibody name as an alias occurrence "
            "without promoting it to a canonical name."
        ),
        "inputs": "source name field",
        "outputs": "antibody alias row",
        "lossless": "true",
        "code_location": "dotad_release.metadata_export",
        "notes": "",
    },
    "DERIVE_IDENTITY_REVIEW_CANDIDATE": {
        "rule_name": "Derive identity review candidate",
        "rule_description": (
            "Detect a deterministic identity conflict or possible relationship "
            "without merging or rewriting source identifiers."
        ),
        "inputs": "metadata, aliases, and sequence fingerprints",
        "outputs": "identity mapping candidate or review row",
        "lossless": "false",
        "code_location": "dotad_release.identity",
        "notes": "Reviewer decision fields remain blank.",
    },
    "DERIVE_SOURCE_REVIEW": {
        "rule_name": "Derive source or licence review item",
        "rule_description": (
            "Surface missing, unknown, or ambiguous source/licence metadata "
            "without inferring an answer."
        ),
        "inputs": "source dictionary and study metadata rows",
        "outputs": "source manifest or review queue row",
        "lossless": "false",
        "code_location": "dotad_release.source_export",
        "notes": "Unknown licence is UNKNOWN; redistribution is NEEDS_REVIEW.",
    },
    "DERIVE_DICTIONARY_MAPPING": {
        "rule_name": "Derive explicit dictionary mapping",
        "rule_description": (
            "Project only explicitly declared source-to-definition columns "
            "from the Field Dictionary."
        ),
        "inputs": "Field Dictionary source row",
        "outputs": "source field mapping row",
        "lossless": "false",
        "code_location": "dotad_release.dictionary_export",
        "notes": "No missing semantic mapping is synthesized.",
    },
    "EXTRACT_COMPANION_METADATA": {
        "rule_name": "Extract companion collection metadata",
        "rule_description": (
            "Record only information present in the affinity README and Phase "
            "1 audit; do not parse companion data into the core release."
        ),
        "inputs": "affinity README and Phase 1 audit rows",
        "outputs": "companion manifest draft row",
        "lossless": "false",
        "code_location": "dotad_release.companion",
        "notes": "All inclusion decisions remain pending review.",
    },
    "AUDIT_SOURCE_LICENCE": {
        "rule_name": "Audit source citation, licence, and redistribution status",
        "rule_description": (
            "Combine confirmed source metadata with conservative licence-"
            "evidence and redistribution rules without inferring permissions "
            "from public availability or article open-access status."
        ),
        "inputs": "source manifest, literature registry metadata, or affinity README row",
        "outputs": "source/licence audit registry row",
        "lossless": "false",
        "code_location": "dotad_release.source_licence_audit",
        "notes": (
            "Unknown dataset licences remain permission-gated; article and "
            "dataset licence scopes are kept separate."
        ),
    },
    "INDEX_EXPERIMENTAL_RECORD": {
        "rule_name": "Index released experimental source record",
        "rule_description": (
            "Create a deterministic index row for one released GDPa source "
            "record without changing its values."
        ),
        "inputs": "released GDPa raw or summary source record",
        "outputs": "experimental record index row",
        "lossless": "true",
        "code_location": "dotad_release.experimental_export",
        "notes": "",
    },
    "DESCRIBE_EXPERIMENTAL_BLOCK": {
        "rule_name": "Describe experimental block and layer",
        "rule_description": (
            "Describe one GDPa block/layer while retaining lineage to every "
            "source row represented by the description."
        ),
        "inputs": "one GDPa source block and record layer",
        "outputs": "experimental source description row",
        "lossless": "false",
        "code_location": "dotad_release.experimental_export",
        "notes": "GDPa blocks are within-study blocks, not publications.",
    },
    "RECONSTRUCT_EXPERIMENTAL_SUMMARY": {
        "rule_name": "Reconstruct reported experimental summary",
        "rule_description": (
            "Compare a reported block-local summary with a condition-aware "
            "recalculation from unambiguous raw inputs."
        ),
        "inputs": "GDPa raw and summary rows within one source block",
        "outputs": "experimental summary reconstruction QC row",
        "lossless": "false",
        "code_location": "dotad_release.experimental_export",
        "notes": "No aggregation is performed across GDPa blocks.",
    },
    "SUMMARIZE_REPLICATE_STRUCTURE": {
        "rule_name": "Summarize experimental replicate structure",
        "rule_description": (
            "Count raw measurement rows within one block, antibody, "
            "condition, and endpoint replicate group."
        ),
        "inputs": "GDPa raw rows within one source block",
        "outputs": "experimental replicate structure QC row",
        "lossless": "false",
        "code_location": "dotad_release.experimental_export",
        "notes": "",
    },
    "DERIVE_FIELD_SEMANTICS_REVIEW": {
        "rule_name": "Derive field-semantics review item",
        "rule_description": (
            "Surface an unresolved dictionary or experimental semantic "
            "condition without inventing a mapping or aggregation rule."
        ),
        "inputs": "dictionary, Phase 1 crosscheck, or experimental QC row",
        "outputs": "field semantics review queue row",
        "lossless": "false",
        "code_location": "dotad_release.cli",
        "notes": "Reviewer decision remains blank.",
    },
}


TABLE_RULES = {
    "sequence_fingerprints": "NORMALIZE_SEQUENCE_FOR_HASH",
    "sequence_qc": "ANALYZE_SEQUENCE_QC",
    "antibody_aliases": "EXTRACT_ALIAS",
    "identity_mapping_candidates": "DERIVE_IDENTITY_REVIEW_CANDIDATE",
    "identity_review_queue": "DERIVE_IDENTITY_REVIEW_CANDIDATE",
    "identity_coverage_status": "DERIVE_IDENTITY_REVIEW_CANDIDATE",
    "identity_relationship_groups": "DERIVE_IDENTITY_REVIEW_CANDIDATE",
    "identity_relationship_members": "DERIVE_IDENTITY_REVIEW_CANDIDATE",
    "source_manifest": "DERIVE_SOURCE_REVIEW",
    "source_evidence_registry": "DERIVE_SOURCE_REVIEW",
    "source_review_queue": "DERIVE_SOURCE_REVIEW",
    "licence_review_queue": "DERIVE_SOURCE_REVIEW",
    "source_field_mapping": "DERIVE_DICTIONARY_MAPPING",
    "affinity_collection_manifest_draft": "EXTRACT_COMPANION_METADATA",
    "source_count_reconciliation": "AUDIT_SOURCE_LICENCE",
    "licence_evidence_registry": "AUDIT_SOURCE_LICENCE",
    "redistribution_strategy": "AUDIT_SOURCE_LICENCE",
    "literature_source_registry": "AUDIT_SOURCE_LICENCE",
    "affinity_source_licence_registry": "AUDIT_SOURCE_LICENCE",
    "experimental_record_index": "INDEX_EXPERIMENTAL_RECORD",
    "experimental_source_description": "DESCRIBE_EXPERIMENTAL_BLOCK",
    "experimental_summary_reconstruction": "RECONSTRUCT_EXPERIMENTAL_SUMMARY",
    "experimental_reconstruction_summary": "RECONSTRUCT_EXPERIMENTAL_SUMMARY",
    "experimental_reconstruction_exceptions": "RECONSTRUCT_EXPERIMENTAL_SUMMARY",
    "source_summary_exclusion_cases": "RECONSTRUCT_EXPERIMENTAL_SUMMARY",
    "summary_only_endpoints": "RECONSTRUCT_EXPERIMENTAL_SUMMARY",
    "experimental_replicate_structure": "SUMMARIZE_REPLICATE_STRUCTURE",
    "field_semantics_review_queue": "DERIVE_FIELD_SEMANTICS_REVIEW",
}

TABLE_SCRIPTS = {
    "antibody_aliases": "dotad_release/metadata_export.py",
    "antibody_metadata": "dotad_release/metadata_export.py",
    "antibody_sequences": "dotad_release/sequence_export.py",
    "assay_endpoint_dictionary": "dotad_release/dictionary_export.py",
    "controlled_vocabularies": "dotad_release/dictionary_export.py",
    "field_dictionary": "dotad_release/dictionary_export.py",
    "source_field_mapping": "dotad_release/dictionary_export.py",
    "identity_mapping_candidates": "dotad_release/identity.py",
    "identity_review_queue": "dotad_release/identity.py",
    "identity_coverage_status": "dotad_release/identity.py",
    "identity_relationship_groups": "dotad_release/identity.py",
    "identity_relationship_members": "dotad_release/identity.py",
    "sequence_fingerprints": "dotad_release/sequence_export.py",
    "sequence_qc": "dotad_release/sequence_export.py",
    "gdpa1_raw_measurements": "dotad_release/experimental_export.py",
    "gdpa1_summary_measurements": "dotad_release/experimental_export.py",
    "gdpa2_raw_measurements": "dotad_release/experimental_export.py",
    "gdpa2_summary_measurements": "dotad_release/experimental_export.py",
    "gdpa3_raw_measurements": "dotad_release/experimental_export.py",
    "gdpa3_summary_measurements": "dotad_release/experimental_export.py",
    "experimental_record_index": "dotad_release/experimental_export.py",
    "experimental_source_description": "dotad_release/experimental_export.py",
    "experimental_summary_reconstruction": "dotad_release/experimental_export.py",
    "experimental_reconstruction_summary": "dotad_release/experimental_export.py",
    "experimental_reconstruction_exceptions": "dotad_release/experimental_export.py",
    "source_summary_exclusion_cases": "dotad_release/experimental_export.py",
    "summary_only_endpoints": "dotad_release/experimental_export.py",
    "experimental_replicate_structure": "dotad_release/experimental_export.py",
    "literature_curated_records": "dotad_release/literature_export.py",
    "source_manifest": "dotad_release/source_export.py",
    "source_evidence_registry": "dotad_release/source_export.py",
    "source_review_queue": "dotad_release/source_export.py",
    "licence_review_queue": "dotad_release/source_export.py",
    "field_semantics_review_queue": "dotad_release/cli.py",
    "affinity_collection_manifest_draft": "dotad_release/companion.py",
    "source_count_reconciliation": "dotad_release/source_licence_audit.py",
    "licence_evidence_registry": "dotad_release/source_licence_audit.py",
    "redistribution_strategy": "dotad_release/source_licence_audit.py",
    "literature_source_registry": "dotad_release/source_licence_audit.py",
    "affinity_source_licence_registry": "dotad_release/source_licence_audit.py",
}


def build_lineage_outputs(
    context: BuildContext,
    results: Iterable[ExportResult],
    script_commit: str,
) -> tuple[ExportResult, ExportResult, ExportResult]:
    result_list = list(results)
    record_index = {
        _output_record_id(row): row
        for result in result_list
        for row in result.rows
    }
    lineage_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    for result in result_list:
        by_output: defaultdict[str, int] = defaultdict(int)
        for row in result.rows:
            output_record_id = _output_record_id(row)
            sources = _lineage_sources(
                row,
                record_index,
                trail=(output_record_id,),
            )
            if not sources:
                raise ValueError(
                    f"{result.table_name}:{output_record_id} cannot be traced "
                    "to an input source row"
                )
            rule_id = TABLE_RULES.get(result.table_name, "COPY_WITH_METADATA")
            for source in sources:
                lineage_rows.append(
                    _lineage_row(
                        context,
                        result.table_name,
                        output_record_id,
                        source,
                        rule_id,
                        script_commit,
                    )
                )
                by_output[output_record_id] += 1
        released = len(result.rows)
        with_lineage = sum(
            1 for row in result.rows if by_output[_output_record_id(row)] > 0
        )
        coverage_rows.append(
            {
                "output_table": result.table_name,
                "released_rows": released,
                "rows_with_lineage": with_lineage,
                "rows_without_lineage": released - with_lineage,
                "coverage_fraction": 1.0 if released == 0 else with_lineage / released,
                "lineage_required": "true",
            }
        )
    rule_rows = [
        {"transformation_rule_id": rule_id, **details}
        for rule_id, details in RULES.items()
    ]
    lineage_path = context.output_dir / "provenance" / "record_lineage.tsv"
    coverage_path = context.output_dir / "qc" / "lineage_coverage.tsv"
    rules_path = context.output_dir / "provenance" / "transformation_rules.tsv"
    write_tsv(lineage_path, lineage_rows, LINEAGE_FIELDS)
    write_tsv(coverage_path, coverage_rows, COVERAGE_FIELDS)
    write_tsv(rules_path, rule_rows, RULE_FIELDS)
    return (
        ExportResult(
            "record_lineage",
            lineage_path,
            LINEAGE_FIELDS,
            tuple(lineage_rows),
            "one row per released-row to input-row lineage edge",
        ),
        ExportResult(
            "lineage_coverage",
            coverage_path,
            COVERAGE_FIELDS,
            tuple(coverage_rows),
            "one row per lineage-required output table",
        ),
        ExportResult(
            "transformation_rules",
            rules_path,
            RULE_FIELDS,
            tuple(rule_rows),
            "one row per transformation rule used by the release",
        ),
    )


def _output_record_id(row: dict[str, Any]) -> str:
    value = row.get("record_id")
    if value is None or str(value).strip() == "":
        raise ValueError("Every formal released row must contain record_id")
    return str(value)


def _lineage_sources(
    row: dict[str, Any],
    record_index: dict[str, dict[str, Any]] | None = None,
    trail: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    encoded = row.get("lineage_sources_json")
    if encoded:
        value = json.loads(str(encoded))
        if not isinstance(value, list):
            raise ValueError("lineage_sources_json must encode a list")
        return _dedupe_sources([dict(item) for item in value])

    references = _referenced_record_ids(row)
    if references and record_index is not None:
        sources: list[dict[str, Any]] = []
        for record_id in references:
            if record_id in trail:
                raise ValueError(
                    "Lineage reference cycle: " + " -> ".join((*trail, record_id))
                )
            source_row = record_index.get(record_id)
            if source_row is None:
                raise ValueError(f"Unresolved lineage reference: {record_id}")
            sources.extend(
                _lineage_sources(
                    source_row,
                    record_index,
                    trail=(*trail, record_id),
                )
            )
        return _dedupe_sources(sources)

    required = (
        "source_workbook",
        "source_input_sha256",
        "source_sheet",
        "source_excel_row",
    )
    if all(row.get(field) not in (None, "") for field in required):
        return [
            {
                "input_file": row["source_workbook"],
                "input_file_sha256": row["source_input_sha256"],
                "input_sheet": row["source_sheet"],
                "input_excel_row": row["source_excel_row"],
                "input_column_or_columns": row.get("source_columns", "*"),
            }
        ]

    dictionary_required = (
        "source_dictionary_workbook",
        "source_input_sha256",
        "source_dictionary_sheet",
        "source_excel_row",
    )
    if any(row.get(field) in (None, "") for field in dictionary_required):
        return []
    return [
        {
            "input_file": row["source_dictionary_workbook"],
            "input_file_sha256": row["source_input_sha256"],
            "input_sheet": row["source_dictionary_sheet"],
            "input_excel_row": row["source_excel_row"],
            "input_column_or_columns": row.get("source_columns", "*"),
        }
    ]


def _referenced_record_ids(row: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    own_record_id = str(row.get("record_id", "")).strip()
    for field in (
        "source_record_id",
        "source_record_ids",
        "candidate_id",
        "field_dictionary_record_id",
        "sequence_record_id_a",
        "sequence_record_id_b",
        "summary_record_id",
    ):
        raw = row.get(field)
        if raw in (None, ""):
            continue
        values.extend(
            part.strip()
            for part in str(raw).split("|")
            if part.strip() and part.strip() != own_record_id
        )
    return tuple(dict.fromkeys(values))


def _dedupe_sources(
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    keyed: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for source in sources:
        key = (
            str(source.get("input_file", source.get("source_workbook", ""))),
            str(source.get("input_sheet", source.get("source_sheet", ""))),
            str(
                source.get(
                    "input_excel_row",
                    source.get("source_excel_row", ""),
                )
            ),
            str(
                source.get(
                    "input_column_or_columns",
                    source.get("source_columns", "*"),
                )
            ),
        )
        keyed[key] = source
    return [keyed[key] for key in sorted(keyed)]


def _lineage_row(
    context: BuildContext,
    output_table: str,
    output_record_id: str,
    source: dict[str, Any],
    rule_id: str,
    script_commit: str,
) -> dict[str, Any]:
    input_file = source.get("input_file", source.get("source_workbook", ""))
    input_sha = source.get(
        "input_file_sha256", source.get("source_input_sha256", "")
    )
    input_sheet = source.get("input_sheet", source.get("source_sheet", ""))
    input_row = source.get("input_excel_row", source.get("source_excel_row", ""))
    input_columns = source.get(
        "input_column_or_columns", source.get("source_columns", "*")
    )
    if not all((input_file, input_sha, input_sheet, input_row)):
        raise ValueError(f"Incomplete lineage source for {output_record_id}")
    lineage_id = stable_content_id(
        "LINEAGE",
        [
            output_table,
            output_record_id,
            str(input_file),
            str(input_sheet),
            str(input_row),
            rule_id,
        ],
    )
    return {
        "lineage_id": lineage_id,
        "output_table": output_table,
        "output_record_id": output_record_id,
        "input_file": Path(str(input_file)).name,
        "input_file_sha256": input_sha,
        "input_sheet": input_sheet,
        "input_excel_row": input_row,
        "input_column_or_columns": input_columns,
        "transformation_rule_id": rule_id,
        "transformation_script": _script_for_table(output_table),
        "transformation_script_commit": script_commit,
        "release_version": context.release_version,
    }


def _script_for_table(output_table: str) -> str:
    try:
        return TABLE_SCRIPTS[output_table]
    except KeyError as error:
        raise ValueError(
            f"No transformation script is registered for {output_table}"
        ) from error
