from __future__ import annotations

import json
import math
import re
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ids import stable_content_id, stable_source_record_id
from .models import BuildContext, ExportResult
from .workbook_reader import (
    SourceRow,
    iter_source_rows,
    ordered_payload_json,
    serialize_scalar,
    write_tsv,
)


SOURCE_STUDY_ID = "GDPA_EXPERIMENTAL_SOURCE_BLOCKS"
SOURCE_STUDY_IDS = {
    "GDPa1": "SRC:GINKGO_GDPA1",
    "GDPa2": "SRC:GINKGO_GDPA2_1",
    "GDPa3": "SRC:GINKGO_GDPA3",
}
ABSOLUTE_TOLERANCE = 1e-8
RELATIVE_TOLERANCE = 1e-8
REPORTED_TWO_DECIMAL_TOLERANCE = 0.005 + 1e-10
GDPA1_TWO_DECIMAL_ENDPOINTS = {
    "normalized_titer",
    "titer",
    "purity_%lc_hc",
    "tonset_nanodsf",
    "tm1_nanodsf",
    "tm2_nanodsf",
    "tm3_nanodsf",
    "sec_%monomer",
    "smac_rt",
    "hic_rt",
    "hac_rt",
}

GDPA1_HIGH_PRECISION_ENDPOINTS = {
    "acsins_dlmax_ph7_4",
    "acsins_dlmax_ph6",
    "polyreactivity_prscore_cho",
    "polyreactivity_prscore_ova",
    "dlskd_ph7_4",
    "dlskd_ph6",
}

DLS_SUMMARY_TO_RAW_ENDPOINT = {
    "dlskd_pbsph7_4": "dlskd_ph7_4",
    "dlskd_hisargph6": "dlskd_ph6",
}

SOURCE_SUMMARY_EXCLUSION_KEYS = {
    ("GDPa1", "DOTAD-325", "acsins_dlmax_ph7_4"),
    ("GDPa1", "DOTAD-313", "acsins_dlmax_ph6"),
}

MEASUREMENT_FIELDS = (
    "record_id",
    "experimental_record_id",
    "source_study_id",
    "source_block_id",
    "record_layer",
    "DOTAD_ID",
    "antibody_name",
    "production_batch",
    "run",
    "technical_replicate",
    "biological_replicate",
    "production_host",
    "tag",
    "construct_context",
    "assay_method",
    "assay_endpoint",
    "pH",
    "temperature",
    "concentration",
    "unit",
    "statistic_type",
    "replicate_count",
    "condition_key",
    "input_file",
    "input_file_sha256",
    "source_workbook",
    "source_input_sha256",
    "source_sheet",
    "source_excel_row",
    "release_version",
    "source_payload_json",
)

INDEX_FIELDS = (
    "record_id",
    "experimental_record_id",
    "source_study_id",
    "source_block_id",
    "record_layer",
    "DOTAD_ID",
    "condition_key",
    "release_file",
    "input_file",
    "input_file_sha256",
    "source_workbook",
    "source_input_sha256",
    "source_sheet",
    "source_excel_row",
    "release_version",
)

DESCRIPTION_FIELDS = (
    "record_id",
    "experimental_record_id",
    "source_study_id",
    "source_block_id",
    "record_layer",
    "DOTAD_ID",
    "source_type",
    "raw_source_sheet",
    "summary_source_sheet",
    "source_sheet",
    "source_excel_row",
    "description",
    "input_file",
    "input_file_sha256",
    "release_version",
    "lineage_sources_json",
)

RECONSTRUCTION_FIELDS = (
    "record_id",
    "source_block_id",
    "summary_record_id",
    "DOTAD_ID",
    "condition_key",
    "endpoint",
    "reported_summary_value",
    "recomputed_summary_value",
    "absolute_difference",
    "relative_difference",
    "reported_stddev",
    "recomputed_stddev",
    "reported_replicate_count",
    "recomputed_replicate_count",
    "aggregation_function",
    "comparison_rule",
    "absolute_tolerance",
    "relative_tolerance",
    "mean_verification_status",
    "stddev_verification_status",
    "replicate_count_verification_status",
    "verification_status",
    "notes",
    "lineage_sources_json",
)

REPLICATE_FIELDS = (
    "record_id",
    "source_study_id",
    "source_block_id",
    "DOTAD_ID",
    "condition_key",
    "endpoint",
    "raw_record_count",
    "nonmissing_value_count",
    "distinct_technical_replicates",
    "production_batches",
    "production_hosts",
    "tags",
    "replicate_structure_status",
    "lineage_sources_json",
)

RECONSTRUCTION_SUMMARY_FIELDS = (
    "record_id",
    "source_block_id",
    "endpoint",
    "verification_status",
    "record_count",
    "lineage_sources_json",
)

RECONSTRUCTION_EXCEPTION_FIELDS = RECONSTRUCTION_FIELDS

SOURCE_SUMMARY_EXCLUSION_FIELDS = (
    "record_id",
    "source_block_id",
    "DOTAD_ID",
    "endpoint",
    "all_raw_values_json",
    "raw_count",
    "reported_summary_value",
    "reported_stddev",
    "reported_count",
    "matching_subset_values_json",
    "excluded_raw_values_json",
    "verification_status",
    "source_exclusion_reason",
    "author_review_status",
    "notes",
    "lineage_sources_json",
)

SUMMARY_ONLY_FIELDS = (
    "record_id",
    "source_block_id",
    "summary_record_id",
    "DOTAD_ID",
    "condition_key",
    "endpoint",
    "reported_value",
    "raw_counterpart_status",
    "source_sheet",
    "notes",
    "lineage_sources_json",
)


@dataclass(frozen=True)
class ExperimentalOutputs:
    measurement_results: tuple[ExportResult, ...]
    record_index: ExportResult
    source_description: ExportResult
    summary_reconstruction: ExportResult
    replicate_structure: ExportResult
    experimental_reconstruction_summary: ExportResult
    experimental_reconstruction_exceptions: ExportResult
    source_summary_exclusion_cases: ExportResult
    summary_only_endpoints: ExportResult
    semantics_review_items: tuple[dict[str, Any], ...]


def export_experimental(context: BuildContext) -> ExperimentalOutputs:
    workbook_path = context.core_inputs["experimental"]
    roles = context.sheet_roles["experimental"]
    measurement_results: list[ExportResult] = []
    records_by_block_layer: dict[tuple[str, str], list[dict[str, Any]]] = {}
    index_rows: list[dict[str, Any]] = []
    description_rows: list[dict[str, Any]] = []

    for block_id in ("GDPa1", "GDPa2", "GDPa3"):
        block_roles = roles["blocks"][block_id]
        source_study_id = SOURCE_STUDY_IDS[block_id]
        for layer_key, record_layer in (
            ("raw", "raw_measurement"),
            ("summary", "summary_measurement"),
        ):
            sheet_name = block_roles[layer_key]
            rows = [
                _measurement_record(
                    source_row,
                    workbook_path,
                    context,
                    block_id,
                    source_study_id,
                    record_layer,
                )
                for source_row in iter_source_rows(workbook_path, sheet_name)
            ]
            records_by_block_layer[(block_id, record_layer)] = rows
            table_name = (
                f"{block_id.lower()}_"
                f"{'raw' if layer_key == 'raw' else 'summary'}_measurements"
            )
            path = (
                context.output_dir
                / "evidence"
                / "experimental"
                / f"{table_name}.tsv"
            )
            write_tsv(path, rows, MEASUREMENT_FIELDS)
            result = ExportResult(
                table_name=table_name,
                path=path,
                fieldnames=MEASUREMENT_FIELDS,
                rows=tuple(rows),
                row_definition=(
                    f"one non-empty source row from {sheet_name}; "
                    f"{block_id} {record_layer}"
                ),
            )
            measurement_results.append(result)
            for row in rows:
                index_rows.append(_index_record(row, path, context.output_dir))
            description_rows.append(
                _description_record(
                    context,
                    workbook_path,
                    block_id,
                    block_roles,
                    source_study_id,
                    record_layer,
                    [_source_ref(row) for row in rows],
                )
            )

    (
        reconstruction_rows,
        semantics_review,
        source_summary_exclusion_rows,
    ) = _reconstruct_summaries(
        records_by_block_layer
    )
    replicate_rows = _replicate_structure(records_by_block_layer)
    reconstruction_summary_rows = _reconstruction_status_summary(
        reconstruction_rows
    )
    reconstruction_exception_rows = [
        row
        for row in reconstruction_rows
        if row["verification_status"]
        in {"not_reproducible", "missing_raw_input", "partially_verified"}
    ]
    summary_only_rows = [
        {
            "record_id": stable_content_id(
                "EXPSUMONLY",
                [row["summary_record_id"], row["endpoint"]],
            ),
            "source_block_id": row["source_block_id"],
            "summary_record_id": row["summary_record_id"],
            "DOTAD_ID": row["DOTAD_ID"],
            "condition_key": row["condition_key"],
            "endpoint": row["endpoint"],
            "reported_value": row["reported_summary_value"],
            "raw_counterpart_status": "absent",
            "source_sheet": _first_lineage_sheet(row),
            "notes": row["notes"],
            "lineage_sources_json": row["lineage_sources_json"],
        }
        for row in reconstruction_rows
        if row["verification_status"] == "summary_only_no_raw_counterpart"
    ]

    index_path = (
        context.output_dir
        / "evidence"
        / "experimental"
        / "experimental_record_index.tsv"
    )
    description_path = (
        context.output_dir
        / "evidence"
        / "experimental"
        / "experimental_source_description.tsv"
    )
    reconstruction_path = (
        context.output_dir / "qc" / "experimental_summary_reconstruction.tsv"
    )
    replicate_path = (
        context.output_dir / "qc" / "experimental_replicate_structure.tsv"
    )
    reconstruction_summary_path = (
        context.output_dir / "qc" / "experimental_reconstruction_summary.tsv"
    )
    reconstruction_exception_path = (
        context.output_dir / "qc" / "experimental_reconstruction_exceptions.tsv"
    )
    source_summary_exclusion_path = (
        context.output_dir / "qc" / "source_summary_exclusion_cases.tsv"
    )
    summary_only_path = context.output_dir / "qc" / "summary_only_endpoints.tsv"
    write_tsv(index_path, index_rows, INDEX_FIELDS)
    write_tsv(description_path, description_rows, DESCRIPTION_FIELDS)
    write_tsv(reconstruction_path, reconstruction_rows, RECONSTRUCTION_FIELDS)
    write_tsv(replicate_path, replicate_rows, REPLICATE_FIELDS)
    write_tsv(
        reconstruction_summary_path,
        reconstruction_summary_rows,
        RECONSTRUCTION_SUMMARY_FIELDS,
    )
    write_tsv(
        reconstruction_exception_path,
        reconstruction_exception_rows,
        RECONSTRUCTION_EXCEPTION_FIELDS,
    )
    write_tsv(
        source_summary_exclusion_path,
        source_summary_exclusion_rows,
        SOURCE_SUMMARY_EXCLUSION_FIELDS,
    )
    write_tsv(summary_only_path, summary_only_rows, SUMMARY_ONLY_FIELDS)

    return ExperimentalOutputs(
        measurement_results=tuple(measurement_results),
        record_index=ExportResult(
            "experimental_record_index",
            index_path,
            INDEX_FIELDS,
            tuple(index_rows),
            "one index row per released experimental source record",
        ),
        source_description=ExportResult(
            "experimental_source_description",
            description_path,
            DESCRIPTION_FIELDS,
            tuple(description_rows),
            "one source-block and record-layer description",
        ),
        summary_reconstruction=ExportResult(
            "experimental_summary_reconstruction",
            reconstruction_path,
            RECONSTRUCTION_FIELDS,
            tuple(reconstruction_rows),
            "one block-local reconstruction check per reported summary endpoint",
        ),
        replicate_structure=ExportResult(
            "experimental_replicate_structure",
            replicate_path,
            REPLICATE_FIELDS,
            tuple(replicate_rows),
            "one block, antibody, condition, and raw endpoint replicate group",
        ),
        experimental_reconstruction_summary=ExportResult(
            "experimental_reconstruction_summary",
            reconstruction_summary_path,
            RECONSTRUCTION_SUMMARY_FIELDS,
            tuple(reconstruction_summary_rows),
            "one source block, endpoint, and verification status count",
        ),
        experimental_reconstruction_exceptions=ExportResult(
            "experimental_reconstruction_exceptions",
            reconstruction_exception_path,
            RECONSTRUCTION_EXCEPTION_FIELDS,
            tuple(reconstruction_exception_rows),
            "one unresolved experimental reconstruction exception",
        ),
        source_summary_exclusion_cases=ExportResult(
            "source_summary_exclusion_cases",
            source_summary_exclusion_path,
            SOURCE_SUMMARY_EXCLUSION_FIELDS,
            tuple(source_summary_exclusion_rows),
            (
                "one source summary reproduced only after an unexplained "
                "source-side raw-value exclusion"
            ),
        ),
        summary_only_endpoints=ExportResult(
            "summary_only_endpoints",
            summary_only_path,
            SUMMARY_ONLY_FIELDS,
            tuple(summary_only_rows),
            "one reported summary endpoint without a raw-data counterpart",
        ),
        semantics_review_items=tuple(semantics_review),
    )


def _measurement_record(
    source_row: SourceRow,
    workbook_path: Path,
    context: BuildContext,
    block_id: str,
    source_study_id: str,
    record_layer: str,
) -> dict[str, Any]:
    lookup = _lookup(source_row)
    record_id = stable_source_record_id(
        "EXP", source_row.sheet_name, source_row.excel_row
    )
    conditions = {
        "production_batch": _pick(lookup, "production_batch"),
        "production_host": _pick(lookup, "production_host"),
        "tag": _pick(lookup, "tag"),
        "construct_context": _pick(
            lookup, "construct_context", "construct", "construct_type"
        ),
        "assay_method": _pick(lookup, "assay_method", "method"),
        "pH": _pick(lookup, "ph"),
        "temperature": _pick(lookup, "temperature"),
        "concentration": _pick(lookup, "concentration"),
        "unit": _pick(lookup, "unit", "units"),
    }
    condition_key = _condition_key(conditions)
    return {
        "record_id": record_id,
        "experimental_record_id": record_id,
        "source_study_id": source_study_id,
        "source_block_id": block_id,
        "record_layer": record_layer,
        "DOTAD_ID": _pick(lookup, "antibody_id", "dotad_id"),
        "antibody_name": _pick(lookup, "antibody_name"),
        "production_batch": conditions["production_batch"],
        "run": _pick(lookup, "run"),
        "technical_replicate": _pick(
            lookup, "technical_replicate", "technical_rep"
        ),
        "biological_replicate": _pick(
            lookup, "biological_replicate", "biological_rep"
        ),
        "production_host": conditions["production_host"],
        "tag": conditions["tag"],
        "construct_context": conditions["construct_context"],
        "assay_method": conditions["assay_method"],
        "assay_endpoint": _pick(
            lookup, "assay_endpoint", "endpoint", "assay"
        ),
        "pH": conditions["pH"],
        "temperature": conditions["temperature"],
        "concentration": conditions["concentration"],
        "unit": conditions["unit"],
        "statistic_type": _pick(
            lookup, "statistic_type", "statistic", "summary_statistic"
        ),
        "replicate_count": _pick(
            lookup, "replicate_count", "count", "replicates"
        ),
        "condition_key": condition_key,
        "input_file": workbook_path.name,
        "input_file_sha256": context.input_shas["experimental"],
        "source_workbook": workbook_path.name,
        "source_input_sha256": context.input_shas["experimental"],
        "source_sheet": source_row.sheet_name,
        "source_excel_row": source_row.excel_row,
        "release_version": context.release_version,
        "source_payload_json": ordered_payload_json(source_row),
    }


def _index_record(
    row: dict[str, Any], output_path: Path, output_root: Path
) -> dict[str, Any]:
    return {
        "record_id": stable_content_id(
            "EXPIDX", [str(row["experimental_record_id"])]
        ),
        "experimental_record_id": row["experimental_record_id"],
        "source_study_id": row["source_study_id"],
        "source_block_id": row["source_block_id"],
        "record_layer": row["record_layer"],
        "DOTAD_ID": row["DOTAD_ID"],
        "condition_key": row["condition_key"],
        "release_file": output_path.relative_to(output_root).as_posix(),
        "input_file": row["input_file"],
        "input_file_sha256": row["input_file_sha256"],
        "source_workbook": row["source_workbook"],
        "source_input_sha256": row["source_input_sha256"],
        "source_sheet": row["source_sheet"],
        "source_excel_row": row["source_excel_row"],
        "release_version": row["release_version"],
    }


def _description_record(
    context: BuildContext,
    workbook_path: Path,
    block_id: str,
    block_roles: dict[str, str],
    source_study_id: str,
    record_layer: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    record_id = stable_content_id(
        "EXPBLOCK", [source_study_id, block_id, record_layer]
    )
    source_sheet = (
        block_roles["raw"]
        if record_layer == "raw_measurement"
        else block_roles["summary"]
    )
    return {
        "record_id": record_id,
        "experimental_record_id": record_id,
        "source_study_id": source_study_id,
        "source_block_id": block_id,
        "record_layer": record_layer,
        "DOTAD_ID": "",
        "source_type": "within_study_data_block",
        "raw_source_sheet": block_roles["raw"],
        "summary_source_sheet": block_roles["summary"],
        "source_sheet": source_sheet,
        "source_excel_row": 1,
        "description": (
            f"{record_layer} layer of the {block_id} experimental source."
        ),
        "input_file": workbook_path.name,
        "input_file_sha256": context.input_shas["experimental"],
        "release_version": context.release_version,
        "lineage_sources_json": json.dumps(
            _dedupe_source_refs(sources),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def _reconstruct_summaries(
    records: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    output: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    exclusion_cases: list[dict[str, Any]] = []
    for block_id in ("GDPa1", "GDPa2", "GDPa3"):
        raw_rows = records[(block_id, "raw_measurement")]
        summary_rows = records[(block_id, "summary_measurement")]
        for summary_row in summary_rows:
            summary_payload = _payload_lookup(summary_row)
            metrics = _summary_metrics(summary_payload)
            for metric in metrics:
                reconstruction, review, exclusion_case = _reconstruct_metric(
                    block_id, summary_row, metric, raw_rows
                )
                output.append(reconstruction)
                if review:
                    reviews.append(review)
                if exclusion_case:
                    exclusion_cases.append(exclusion_case)
    output.sort(
        key=lambda row: (
            row["source_block_id"],
            row["summary_record_id"],
            row["endpoint"],
        )
    )
    reviews.sort(key=lambda row: row["record_id"])
    exclusion_cases.sort(key=lambda row: row["record_id"])
    return output, reviews, exclusion_cases


def _reconstruction_status_summary(
    reconstruction_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: defaultdict[
        tuple[str, str, str], list[dict[str, Any]]
    ] = defaultdict(
        list
    )
    for row in reconstruction_rows:
        grouped[
            (
                row["source_block_id"],
                row["endpoint"],
                row["verification_status"],
            )
        ].append(row)
    output: list[dict[str, Any]] = []
    for (block_id, endpoint, status), rows in sorted(grouped.items()):
        output.append(
            {
                "record_id": stable_content_id(
                    "EXPRECONSUM", [block_id, endpoint, status]
                ),
                "source_block_id": block_id,
                "endpoint": endpoint,
                "verification_status": status,
                "record_count": len(rows),
                "lineage_sources_json": json.dumps(
                    _dedupe_source_refs(
                        [
                            source
                            for row in rows
                            for source in json.loads(
                                str(row["lineage_sources_json"])
                            )
                        ]
                    ),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    return output


def _first_lineage_sheet(row: dict[str, Any]) -> str:
    sources = json.loads(str(row["lineage_sources_json"]))
    return (
        serialize_scalar(sources[0].get("input_sheet"))
        if sources
        else ""
    )


def _reconstruct_metric(
    block_id: str,
    summary_row: dict[str, Any],
    metric: dict[str, Any],
    raw_rows: list[dict[str, Any]],
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    endpoint = metric["endpoint"]
    raw_endpoint, batch_filter = _raw_endpoint_and_batch(endpoint)
    ignored_conditions = _ignored_conditions_for_endpoint(
        block_id, raw_endpoint, batch_filter
    )
    candidates = [
        row
        for row in raw_rows
        if _same_identity_and_condition(
            summary_row, row, ignored_fields=ignored_conditions
        )
        and (
            batch_filter is None
            or serialize_scalar(row.get("production_batch")) == batch_filter
        )
    ]
    ambiguous_fields = _ambiguous_unspecified_conditions(
        summary_row,
        candidates,
        ignored_fields=ignored_conditions,
    )
    values: list[float] = []
    contributing: list[dict[str, Any]] = [_source_ref(summary_row)]
    for row in candidates:
        payload = _payload_lookup(row)
        raw_value = _find_endpoint_value(payload, raw_endpoint)
        numeric = _as_float(raw_value)
        if numeric is not None:
            values.append(numeric)
            contributing.append(_source_ref(row))

    reported = _as_float(metric.get("reported_value"))
    reported_count = _as_int(metric.get("reported_count"))
    reported_stddev = _as_float(metric.get("reported_stddev"))
    raw_counterpart_exists = _raw_endpoint_exists(raw_rows, raw_endpoint)
    if not raw_counterpart_exists:
        recomputed = None
        recomputed_count = 0
        recomputed_stddev = None
    elif ambiguous_fields:
        recomputed = None
        recomputed_count = 0
        recomputed_stddev = None
    elif metric["aggregation_function"] == "median":
        recomputed = statistics.median(values) if values else None
        recomputed_count = len(values)
        recomputed_stddev = statistics.stdev(values) if len(values) > 1 else None
    else:
        recomputed = statistics.fmean(values) if values else None
        recomputed_count = len(values)
        recomputed_stddev = statistics.stdev(values) if len(values) > 1 else None
    absolute = (
        abs(reported - recomputed)
        if reported is not None and recomputed is not None
        else None
    )
    relative = (
        absolute / abs(reported)
        if absolute is not None and reported not in (None, 0)
        else None
    )
    if not raw_counterpart_exists:
        status = "summary_only_no_raw_counterpart"
        notes = (
            "The summary endpoint is reported by the source but no raw-data "
            "column with the same endpoint is available."
        )
    elif ambiguous_fields:
        status = "partially_verified"
        notes = (
            "Raw rows span ambiguous experimental conditions not specified "
            "by the summary row: "
            + ", ".join(ambiguous_fields)
            + ". No cross-condition aggregate was recomputed."
        )
    else:
        (
            status,
            notes,
            mean_status,
            stddev_status,
            count_status,
            comparison_rule,
            absolute_tolerance,
            relative_tolerance,
        ) = _verification_status(
            block_id,
            raw_endpoint,
            reported,
            recomputed,
            reported_count,
            recomputed_count,
            reported_stddev,
            recomputed_stddev,
            batch_filter,
        )
    if not raw_counterpart_exists:
        mean_status = "not_assessed_no_raw_counterpart"
        stddev_status = (
            "not_reported_not_assessed"
            if reported_stddev is None
            else "not_assessed_no_raw_counterpart"
        )
        count_status = "not_assessed_no_raw_counterpart"
        comparison_rule = "raw_counterpart_required"
        absolute_tolerance = ""
        relative_tolerance = ""
    elif ambiguous_fields:
        mean_status = "not_assessed_ambiguous_conditions"
        stddev_status = (
            "not_reported_not_assessed"
            if reported_stddev is None
            else "not_assessed_ambiguous_conditions"
        )
        count_status = "not_assessed_ambiguous_conditions"
        comparison_rule = "condition_matched_raw_values"
        absolute_tolerance = ""
        relative_tolerance = ""

    exclusion_case = None
    if status == "not_reproducible":
        exclusion_case = _source_summary_exclusion_case(
            block_id=block_id,
            summary_row=summary_row,
            endpoint=raw_endpoint,
            values=values,
            reported=reported,
            reported_stddev=reported_stddev,
            reported_count=reported_count,
            lineage_sources=contributing,
        )
        if exclusion_case is not None:
            matching_values = json.loads(
                exclusion_case["matching_subset_values_json"]
            )
            recomputed = statistics.fmean(matching_values)
            recomputed_count = len(matching_values)
            recomputed_stddev = (
                statistics.stdev(matching_values)
                if len(matching_values) > 1
                else None
            )
            absolute = (
                abs(reported - recomputed)
                if reported is not None
                else None
            )
            relative = (
                absolute / abs(reported)
                if absolute is not None and reported not in (None, 0)
                else None
            )
            status = exclusion_case["verification_status"]
            mean_status = status
            stddev_status = (
                status
                if reported_stddev is not None
                else "not_reported_not_assessed"
            )
            count_status = status
            comparison_rule = "explicit_source_summary_exclusion_audit"
            absolute_tolerance = ABSOLUTE_TOLERANCE
            relative_tolerance = RELATIVE_TOLERANCE
            notes = exclusion_case["notes"]
    condition_key = summary_row["condition_key"]
    if batch_filter is not None:
        condition_key = _append_condition(
            condition_key, "production_batch", batch_filter
        )
    record_id = stable_content_id(
        "EXPRECON",
        [summary_row["record_id"], endpoint, condition_key],
    )
    row = {
        "record_id": record_id,
        "source_block_id": block_id,
        "summary_record_id": summary_row["record_id"],
        "DOTAD_ID": summary_row["DOTAD_ID"],
        "condition_key": condition_key,
        "endpoint": endpoint,
        "reported_summary_value": reported,
        "recomputed_summary_value": recomputed,
        "absolute_difference": absolute,
        "relative_difference": relative,
        "reported_stddev": reported_stddev,
        "recomputed_stddev": recomputed_stddev,
        "reported_replicate_count": reported_count,
        "recomputed_replicate_count": recomputed_count,
        "aggregation_function": metric["aggregation_function"],
        "comparison_rule": comparison_rule,
        "absolute_tolerance": absolute_tolerance,
        "relative_tolerance": relative_tolerance,
        "mean_verification_status": mean_status,
        "stddev_verification_status": stddev_status,
        "replicate_count_verification_status": count_status,
        "verification_status": status,
        "notes": notes,
        "lineage_sources_json": json.dumps(
            _dedupe_source_refs(contributing),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    review = None
    if status in {"not_reproducible", "missing_raw_input", "partially_verified"}:
        review = {
            "record_id": stable_content_id(
                "SEMREV", ["EXPERIMENTAL_RECONSTRUCTION", record_id]
            ),
            "issue_type": "EXPERIMENTAL_SUMMARY_RECONSTRUCTION",
            "review_status": "NEEDS_REVIEW",
            "source_block_id": block_id,
            "source_record_id": summary_row["record_id"],
            "field_or_endpoint": endpoint,
            "review_reason": notes,
            "reviewer_decision": "",
            "lineage_sources_json": row["lineage_sources_json"],
        }
    return row, review, exclusion_case


def _replicate_structure(
    records: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    groups: defaultdict[
        tuple[str, str, str, str],
        dict[str, Any],
    ] = defaultdict(
        lambda: {
            "rows": [],
            "values": [],
            "technical": set(),
            "batches": set(),
            "hosts": set(),
            "tags": set(),
        }
    )
    for block_id in ("GDPa1", "GDPa2", "GDPa3"):
        for row in records[(block_id, "raw_measurement")]:
            payload = _payload_lookup(row)
            for header, value in payload.items():
                if header in _NON_ENDPOINT_HEADERS:
                    continue
                numeric = _as_float(value)
                if numeric is None:
                    continue
                endpoint = _canonical_endpoint(header)
                grouping_condition = row["condition_key"]
                if block_id == "GDPa1" and endpoint not in {
                    "titer",
                    "normalized_titer",
                }:
                    grouping_condition = _condition_key_without(
                        row, {"production_batch"}
                    )
                key = (
                    block_id,
                    serialize_scalar(row["DOTAD_ID"]),
                    serialize_scalar(grouping_condition),
                    endpoint,
                )
                group = groups[key]
                group["rows"].append(_source_ref(row))
                group["values"].append(numeric)
                _add_nonblank(group["technical"], row["technical_replicate"])
                _add_nonblank(group["batches"], row["production_batch"])
                _add_nonblank(group["hosts"], row["production_host"])
                _add_nonblank(group["tags"], row["tag"])
    output: list[dict[str, Any]] = []
    for (block_id, dotad_id, condition_key, endpoint), group in sorted(
        groups.items()
    ):
        nonmissing = len(group["values"])
        technical = len(group["technical"])
        status = (
            "REPLICATES_PRESENT"
            if technical > 1 or nonmissing > 1
            else "SINGLE_OBSERVATION"
        )
        output.append(
            {
                "record_id": stable_content_id(
                    "EXPREP",
                    [block_id, dotad_id, condition_key, endpoint],
                ),
                "source_study_id": SOURCE_STUDY_IDS[block_id],
                "source_block_id": block_id,
                "DOTAD_ID": dotad_id,
                "condition_key": condition_key,
                "endpoint": endpoint,
                "raw_record_count": len(group["rows"]),
                "nonmissing_value_count": nonmissing,
                "distinct_technical_replicates": technical,
                "production_batches": "|".join(sorted(group["batches"])),
                "production_hosts": "|".join(sorted(group["hosts"])),
                "tags": "|".join(sorted(group["tags"])),
                "replicate_structure_status": status,
                "lineage_sources_json": json.dumps(
                    _dedupe_source_refs(group["rows"]),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    return output


def _summary_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for header, value in payload.items():
        match = re.match(r"^(.*)_(avg|mean|median)$", header, re.IGNORECASE)
        if not match or value in (None, ""):
            continue
        endpoint = _canonical_endpoint(match.group(1))
        metrics.append(
            {
                "endpoint": endpoint,
                "reported_value": value,
                "reported_count": _first_payload_value(
                    payload,
                    f"{match.group(1)}_replicates",
                    f"{match.group(1)}_count",
                ),
                "reported_stddev": _first_payload_value(
                    payload,
                    f"{match.group(1)}_stddev",
                    f"{match.group(1)}_sd",
                ),
                "aggregation_function": (
                    "median" if match.group(2).lower() == "median" else "mean"
                ),
            }
        )
    return metrics


def _raw_endpoint_and_batch(endpoint: str) -> tuple[str, str | None]:
    match = re.match(r"^(normalized_titer|titer)_productionbatch([0-9]+)$", endpoint)
    if match:
        return match.group(1), match.group(2)
    return DLS_SUMMARY_TO_RAW_ENDPOINT.get(endpoint, endpoint), None


def _ignored_conditions_for_endpoint(
    block_id: str,
    raw_endpoint: str,
    batch_filter: str | None,
) -> set[str]:
    if block_id == "GDPa1":
        if batch_filter is not None:
            return {"production_batch"}
        if raw_endpoint in {"titer", "normalized_titer"}:
            return set()
        return {"production_batch"}
    return set()


def _raw_endpoint_exists(
    raw_rows: list[dict[str, Any]], endpoint: str
) -> bool:
    canonical = _canonical_endpoint(endpoint)
    return any(
        any(
            _canonical_endpoint(header) == canonical
            for header in _payload_lookup(row)
        )
        for row in raw_rows
    )


def _find_endpoint_value(payload: dict[str, Any], endpoint: str) -> Any:
    canonical = _canonical_endpoint(endpoint)
    for header, value in payload.items():
        if _canonical_endpoint(header) == canonical:
            return value
    return None


def _same_identity_and_condition(
    summary: dict[str, Any],
    raw: dict[str, Any],
    ignored_fields: set[str] | None = None,
) -> bool:
    ignored = ignored_fields or set()
    if serialize_scalar(summary["DOTAD_ID"]) != serialize_scalar(raw["DOTAD_ID"]):
        return False
    for field in _CONDITION_FIELDS:
        if field in ignored:
            continue
        expected = serialize_scalar(summary.get(field))
        if expected and expected != serialize_scalar(raw.get(field)):
            return False
    return True


def _ambiguous_unspecified_conditions(
    summary: dict[str, Any],
    candidates: list[dict[str, Any]],
    ignored_fields: set[str],
) -> tuple[str, ...]:
    ambiguous: list[str] = []
    for field in _CONDITION_FIELDS:
        if field in ignored_fields or serialize_scalar(summary.get(field)):
            continue
        observed = {
            serialize_scalar(row.get(field))
            for row in candidates
        }
        if len(observed) > 1:
            ambiguous.append(field)
    return tuple(ambiguous)


def _verification_status(
    block_id: str,
    endpoint: str,
    reported: float | None,
    recomputed: float | None,
    reported_count: int | None,
    recomputed_count: int,
    reported_stddev: float | None,
    recomputed_stddev: float | None,
    batch_filter: str | None,
) -> tuple[str, str, str, str, str, str, float, float]:
    (
        comparison_rule,
        absolute_tolerance,
        relative_tolerance,
        success_status,
    ) = _comparison_policy(block_id, endpoint)
    if recomputed is None:
        return (
            "missing_raw_input",
            "No matching non-missing raw values were found.",
            "missing_raw_input",
            (
                "not_reported_not_assessed"
                if reported_stddev is None
                else "missing_raw_input"
            ),
            "missing_raw_input",
            comparison_rule,
            absolute_tolerance,
            relative_tolerance,
        )
    if reported is None:
        return (
            "partially_verified",
            "Reported summary value is not numeric.",
            "not_assessed_non_numeric_reported_value",
            (
                "not_reported_not_assessed"
                if reported_stddev is None
                else "not_assessed"
            ),
            (
                "not_reported_not_assessed"
                if reported_count is None
                else "not_assessed"
            ),
            comparison_rule,
            absolute_tolerance,
            relative_tolerance,
        )
    mean_exact = math.isclose(reported, recomputed, rel_tol=0.0, abs_tol=1e-12)
    mean_within_rule = math.isclose(
        reported,
        recomputed,
        rel_tol=relative_tolerance,
        abs_tol=absolute_tolerance,
    )
    if reported_count is None:
        count_status = "not_reported_not_assessed"
        count_ok = True
    elif reported_count == recomputed_count:
        count_status = "verified_exact"
        count_ok = True
    else:
        count_status = "not_reproducible"
        count_ok = False
    if reported_stddev is None:
        stddev_status = "not_reported_not_assessed"
        std_ok = True
        std_exact = True
    elif recomputed_stddev is None:
        stddev_status = "missing_raw_input"
        std_ok = False
        std_exact = False
    else:
        std_exact = math.isclose(
            reported_stddev,
            recomputed_stddev,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        std_ok = math.isclose(
            reported_stddev,
            recomputed_stddev,
            rel_tol=relative_tolerance,
            abs_tol=absolute_tolerance,
        )
        if std_ok:
            stddev_status = success_status if not std_exact else "verified_exact"
        else:
            stddev_status = "not_reproducible"

    if mean_within_rule:
        mean_status = success_status if not mean_exact else "verified_exact"
    else:
        mean_status = "not_reproducible"

    filter_note = (
        f" Raw rows were filtered to production_batch={batch_filter}."
        if batch_filter is not None
        else ""
    )
    if mean_within_rule and count_ok and std_ok:
        if success_status == "verified_at_reported_precision":
            status = success_status
            notes = (
                "Reported and recomputed values agree at the source summary's "
                "two-decimal publication precision."
            )
            mean_status = success_status
            if reported_stddev is not None:
                stddev_status = success_status
        elif mean_exact and std_exact:
            status = (
                "verified_with_documented_filter"
                if batch_filter is not None
                else "verified_exact"
            )
            notes = "Reported and recomputed values agree."
        else:
            status = "verified_rounding"
            notes = "Difference is within the endpoint-specific tolerance."
        return (
            status,
            notes + filter_note,
            mean_status,
            stddev_status,
            count_status,
            comparison_rule,
            absolute_tolerance,
            relative_tolerance,
        )
    if mean_within_rule:
        return (
            "partially_verified",
            "Mean agrees but replicate count or standard deviation does not.",
            mean_status,
            stddev_status,
            count_status,
            comparison_rule,
            absolute_tolerance,
            relative_tolerance,
        )
    return (
        "not_reproducible",
        "Reported summary value was not reproduced under the endpoint-specific rule.",
        mean_status,
        stddev_status,
        count_status,
        comparison_rule,
        absolute_tolerance,
        relative_tolerance,
    )


def _comparison_policy(
    block_id: str,
    endpoint: str,
) -> tuple[str, float, float, str]:
    canonical = _canonical_endpoint(endpoint)
    if block_id == "GDPa1" and canonical in GDPA1_TWO_DECIMAL_ENDPOINTS:
        return (
            "reported_two_decimal_precision",
            REPORTED_TWO_DECIMAL_TOLERANCE,
            0.0,
            "verified_at_reported_precision",
        )
    if block_id == "GDPa1" and canonical in GDPA1_HIGH_PRECISION_ENDPOINTS:
        return (
            "high_precision_absolute_or_relative_1e-8",
            ABSOLUTE_TOLERANCE,
            RELATIVE_TOLERANCE,
            "verified_rounding",
        )
    return (
        "absolute_or_relative_1e-8",
        ABSOLUTE_TOLERANCE,
        RELATIVE_TOLERANCE,
        "verified_rounding",
    )


def _source_summary_exclusion_case(
    *,
    block_id: str,
    summary_row: dict[str, Any],
    endpoint: str,
    values: list[float],
    reported: float | None,
    reported_stddev: float | None,
    reported_count: int | None,
    lineage_sources: list[dict[str, Any]],
) -> dict[str, Any] | None:
    dotad_id = serialize_scalar(summary_row["DOTAD_ID"])
    canonical_endpoint = _canonical_endpoint(endpoint)
    key = (block_id, dotad_id, canonical_endpoint)
    if (
        key not in SOURCE_SUMMARY_EXCLUSION_KEYS
        or reported is None
        or reported_count is None
        or len(values) != reported_count + 1
    ):
        return None
    for index, excluded in enumerate(values):
        subset = values[:index] + values[index + 1 :]
        recomputed = statistics.fmean(subset)
        recomputed_stddev = (
            statistics.stdev(subset) if len(subset) > 1 else None
        )
        mean_ok = math.isclose(
            reported,
            recomputed,
            rel_tol=RELATIVE_TOLERANCE,
            abs_tol=ABSOLUTE_TOLERANCE,
        )
        stddev_ok = (
            reported_stddev is None
            or (
                recomputed_stddev is not None
                and math.isclose(
                    reported_stddev,
                    recomputed_stddev,
                    rel_tol=RELATIVE_TOLERANCE,
                    abs_tol=ABSOLUTE_TOLERANCE,
                )
            )
        )
        if not (mean_ok and stddev_ok):
            continue
        status = (
            "verified_against_source_summary_with_unexplained_raw_exclusion"
        )
        notes = (
            "The reported source summary is reproduced after omitting one raw "
            "value, but the source QC rationale is unavailable. DOTAD retains "
            "all raw values and does not classify the omitted value as an outlier."
        )
        return {
            "record_id": stable_content_id(
                "EXPSRCEXCL", [block_id, dotad_id, canonical_endpoint]
            ),
            "source_block_id": block_id,
            "DOTAD_ID": dotad_id,
            "endpoint": canonical_endpoint,
            "all_raw_values_json": json.dumps(
                values, ensure_ascii=False, separators=(",", ":")
            ),
            "raw_count": len(values),
            "reported_summary_value": reported,
            "reported_stddev": reported_stddev,
            "reported_count": reported_count,
            "matching_subset_values_json": json.dumps(
                subset, ensure_ascii=False, separators=(",", ":")
            ),
            "excluded_raw_values_json": json.dumps(
                [excluded], ensure_ascii=False, separators=(",", ":")
            ),
            "verification_status": status,
            "source_exclusion_reason": "SOURCE_QC_REASON_NOT_AVAILABLE",
            "author_review_status": "NEEDS_AUTHOR_REVIEW",
            "notes": notes,
            "lineage_sources_json": json.dumps(
                _dedupe_source_refs(lineage_sources),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
    return None


def _lookup(row: SourceRow) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for item in row.payload:
        key = _normalized_header(item.header)
        if key and key not in lookup:
            lookup[key] = item.value
    return lookup


def _payload_lookup(row: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(str(row["source_payload_json"]))
    lookup: dict[str, Any] = {}
    for item in payload:
        header = _normalized_header(item.get("header", ""))
        if header and header not in lookup:
            lookup[header] = item.get("value")
    return lookup


def _condition_key(values: dict[str, Any]) -> str:
    parts = [
        f"{key}={serialize_scalar(value)}"
        for key, value in values.items()
        if serialize_scalar(value)
    ]
    return "|".join(parts) if parts else "UNSPECIFIED_CONDITION"


def _condition_key_without(
    row: dict[str, Any], ignored_fields: set[str]
) -> str:
    return _condition_key(
        {
            field: row.get(field, "")
            for field in _CONDITION_FIELDS
            if field not in ignored_fields
        }
    )


def _append_condition(condition_key: str, key: str, value: str) -> str:
    item = f"{key}={value}"
    if condition_key == "UNSPECIFIED_CONDITION":
        return item
    return f"{condition_key}|{item}"


def _canonical_endpoint(value: str) -> str:
    normalized = _normalized_header(value)
    normalized = re.sub(r"ph([0-9]+)_0(?=_|$)", r"ph\1", normalized)
    return normalized


def _normalized_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return re.sub(r"[^0-9a-z%]+", "_", normalized).strip("_")


def _pick(lookup: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in lookup:
            return lookup[key]
    return ""


def _first_payload_value(payload: dict[str, Any], *keys: str) -> Any:
    normalized_lookup = {_normalized_header(key): value for key, value in payload.items()}
    for key in keys:
        normalized = _normalized_header(key)
        if normalized in normalized_lookup:
            return normalized_lookup[normalized]
    return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _source_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_file": row["input_file"],
        "input_file_sha256": row["input_file_sha256"],
        "input_sheet": row["source_sheet"],
        "input_excel_row": row["source_excel_row"],
        "input_column_or_columns": "*",
    }


def _dedupe_source_refs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            serialize_scalar(row.get("input_file")),
            serialize_scalar(row.get("input_sheet")),
            serialize_scalar(row.get("input_excel_row")),
            serialize_scalar(row.get("input_column_or_columns")),
        )
        keyed[key] = row
    return [keyed[key] for key in sorted(keyed)]


def _add_nonblank(target: set[str], value: Any) -> None:
    serialized = serialize_scalar(value)
    if serialized:
        target.add(serialized)


_NON_ENDPOINT_HEADERS = {
    "antibody_id",
    "antibody_name",
    "hc_subtype",
    "lc_subtype",
    "production_batch",
    "run",
    "technical_replicate",
    "biological_replicate",
    "production_host",
    "tag",
    "construct_context",
    "assay_method",
    "ph",
    "temperature",
    "concentration",
    "unit",
}

_CONDITION_FIELDS = (
    "production_batch",
    "production_host",
    "tag",
    "construct_context",
    "assay_method",
    "pH",
    "temperature",
    "concentration",
    "unit",
)
