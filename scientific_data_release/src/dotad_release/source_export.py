from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .experimental_export import SOURCE_STUDY_IDS
from .ids import stable_content_id, stable_source_record_id
from .models import BuildContext, ExportResult
from .workbook_reader import SourceRow, iter_source_rows, serialize_scalar, write_tsv


SOURCE_FIELDS = (
    "record_id",
    "source_id",
    "source_type",
    "publication_status",
    "source_study_label",
    "source_block_id",
    "pmid",
    "doi",
    "url",
    "source_dataset_name",
    "source_version",
    "provider",
    "associated_publication",
    "citation_status",
    "source_attribution_status",
    "licence",
    "redistribution_status",
    "evidence_record_count",
    "secondary_distribution_source",
    "notes",
    "lineage_sources_json",
)

SOURCE_EVIDENCE_FIELDS = (
    "record_id",
    "source_id",
    "source_study_label",
    "source_type",
    "publication_status",
    "citation_status",
    "source_attribution_status",
    "pmid",
    "doi",
    "url",
    "provider",
    "associated_publication",
    "licence",
    "redistribution_status",
    "evidence_basis",
    "lineage_sources_json",
)

SOURCE_REVIEW_FIELDS = (
    "record_id",
    "source_id",
    "source_study_label",
    "issue_type",
    "review_status",
    "review_reason",
    "candidate_count",
    "reviewer_decision",
    "lineage_sources_json",
)

LICENCE_REVIEW_FIELDS = (
    "record_id",
    "source_id",
    "source_study_label",
    "licence",
    "redistribution_status",
    "review_status",
    "review_reason",
    "reviewer_decision",
    "lineage_sources_json",
)


@dataclass(frozen=True)
class SourceOutputs:
    source_manifest: ExportResult
    source_evidence_registry: ExportResult
    source_review_queue: ExportResult
    licence_review_queue: ExportResult


def build_source_outputs(
    context: BuildContext,
    literature_result: ExportResult,
) -> SourceOutputs:
    workbook_path = context.core_inputs["data_dictionary"]
    roles = context.sheet_roles["data_dictionary"]
    literature_source_rows = tuple(
        iter_source_rows(workbook_path, roles["literature_sources"])
    )
    indexed = _index_dictionary_rows(literature_source_rows)
    evidence_by_sheet = _evidence_by_sheet(literature_result)

    source_rows: list[dict[str, Any]] = []
    source_reviews: list[dict[str, Any]] = []
    licence_reviews: list[dict[str, Any]] = []
    for source_sheet in _ordered_source_sheets(literature_result):
        matches = indexed.get(source_sheet, [])
        source, reviews = _literature_source_record(
            context,
            source_sheet,
            matches,
            evidence_by_sheet.get(source_sheet, []),
        )
        source_rows.append(source)
        source_reviews.extend(reviews)
        licence_reviews.append(_licence_review(source))

    for experimental_source in _experimental_sources(context):
        source_rows.append(experimental_source)
        licence_reviews.append(_licence_review(experimental_source))

    source_rows.sort(key=lambda row: row["source_id"])
    evidence_rows = [_source_evidence_record(row) for row in source_rows]
    source_reviews.sort(key=lambda row: row["record_id"])
    licence_reviews.sort(key=lambda row: row["record_id"])
    source_path = context.output_dir / "provenance" / "source_manifest.tsv"
    evidence_path = (
        context.output_dir / "provenance" / "source_evidence_registry.tsv"
    )
    source_review_path = (
        context.output_dir / "review" / "source_review_queue.tsv"
    )
    licence_review_path = (
        context.output_dir / "review" / "licence_review_queue.tsv"
    )
    write_tsv(source_path, source_rows, SOURCE_FIELDS)
    write_tsv(evidence_path, evidence_rows, SOURCE_EVIDENCE_FIELDS)
    write_tsv(source_review_path, source_reviews, SOURCE_REVIEW_FIELDS)
    write_tsv(licence_review_path, licence_reviews, LICENCE_REVIEW_FIELDS)
    return SourceOutputs(
        source_manifest=ExportResult(
            "source_manifest",
            source_path,
            SOURCE_FIELDS,
            tuple(source_rows),
            (
                "one source-level evidence entity, including independent "
                "GDPa1, GDPa2, and GDPa3 sources"
            ),
        ),
        source_evidence_registry=ExportResult(
            "source_evidence_registry",
            evidence_path,
            SOURCE_EVIDENCE_FIELDS,
            tuple(evidence_rows),
            "one evidence-status registry row per source manifest entity",
        ),
        source_review_queue=ExportResult(
            "source_review_queue",
            source_review_path,
            SOURCE_REVIEW_FIELDS,
            tuple(source_reviews),
            "one deterministic review row per unresolved citation/source condition",
        ),
        licence_review_queue=ExportResult(
            "licence_review_queue",
            licence_review_path,
            LICENCE_REVIEW_FIELDS,
            tuple(licence_reviews),
            "one conservative licence/redistribution review row per source",
        ),
    )


def _literature_source_record(
    context: BuildContext,
    source_sheet: str,
    matches: list[SourceRow],
    evidence_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_id = stable_content_id("SRC", ["LITERATURE", source_sheet])
    reviews: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    if len(matches) == 1:
        source_row = matches[0]
        lookup = _row_lookup(source_row)
        refs.append(_dictionary_ref(context, source_row))
        pmid = _blank_to_empty(lookup.get("pmid"))
        doi = _blank_to_empty(lookup.get("doi"))
        url = _blank_to_empty(lookup.get("source_url"))
        citation_status = "COMPLETE" if any((pmid, doi, url)) else "MISSING"
        dataset_name = _blank_to_empty(
            lookup.get("short_name") or lookup.get("study_key")
        )
        notes = _blank_to_empty(lookup.get("notes"))
    elif len(matches) > 1:
        pmid = doi = url = dataset_name = notes = ""
        citation_status = "AMBIGUOUS"
    else:
        pmid = doi = url = dataset_name = notes = ""
        citation_status = "MISSING"
    refs.extend(_evidence_refs(evidence_rows))
    source = {
        "record_id": source_id,
        "source_id": source_id,
        "source_type": "peer_reviewed_publication",
        "publication_status": "peer_reviewed",
        "source_study_label": source_sheet,
        "source_block_id": "",
        "pmid": pmid,
        "doi": doi,
        "url": url,
        "source_dataset_name": dataset_name,
        "source_version": "",
        "provider": "",
        "associated_publication": "",
        "citation_status": citation_status,
        "source_attribution_status": "SOURCE_LINKED_RECORD",
        "licence": "UNKNOWN",
        "redistribution_status": "NEEDS_REVIEW",
        "evidence_record_count": len(evidence_rows),
        "secondary_distribution_source": "",
        "notes": notes,
        "lineage_sources_json": _sources_json(refs),
    }
    source = _apply_confirmed_literature_override(source, source_sheet)
    if _confirmed_literature_key(source_sheet) is not None:
        reviews = []
    elif len(matches) > 1:
        reviews.append(
            _source_review(
                source,
                "AMBIGUOUS_SOURCE_METADATA",
                "Multiple exact Literature Sources rows match this source sheet.",
                len(matches),
            )
        )
    elif len(matches) == 0:
        reviews.append(
            _source_review(
                source,
                "MISSING_SOURCE_METADATA",
                "No exact Literature Sources source_sheet match exists.",
                0,
            )
        )
    elif citation_status == "MISSING":
        reviews.append(
            _source_review(
                source,
                "MISSING_CITATION_METADATA",
                "No PMID, DOI, or source URL is reported in the exact dictionary row.",
            )
        )
    return source, reviews


def _experimental_sources(context: BuildContext) -> list[dict[str, Any]]:
    workbook_path = context.core_inputs["data_dictionary"]
    roles = context.sheet_roles["data_dictionary"]
    source_rows: list[SourceRow] = []
    for role in ("readme", "workbook_sheet_dictionary"):
        sheet_name = roles.get(role)
        if not sheet_name:
            continue
        for row in iter_source_rows(workbook_path, sheet_name):
            text = " ".join(serialize_scalar(item.value) for item in row.payload)
            if any(block.lower() in text.lower() for block in ("GDPa1", "GDPa2", "GDPa3")):
                source_rows.append(row)
    refs = [_dictionary_ref(context, row) for row in source_rows]
    metadata = {
        "GDPa1": {
            "source_type": (
                "public_repository_dataset_and_peer_reviewed_publication"
            ),
            "publication_status": "peer_reviewed",
            "pmid": "41328470",
            "doi": "10.1080/19420862.2025.2593055",
            "url": "https://huggingface.co/datasets/ginkgo-datapoints/GDPa1",
            "source_dataset_name": "GDPa1 PROPHET-Ab Benchmark",
            "associated_publication": (
                "A high-throughput platform for biophysical antibody "
                "developability assessment to enable AI/ML model training"
            ),
            "licence": "cc-by-with-restrictions",
            "redistribution_status": "PERMISSION_REQUIRED",
            "notes": (
                "The official README reports both 242 and 246 antibodies. "
                "The frozen DOTAD workbook contains 246 summary rows; the "
                "discrepancy is preserved in source_count_reconciliation.tsv."
            ),
        },
        "GDPa2": {
            "source_type": "public_repository_dataset",
            "publication_status": (
                "NO_ASSOCIATED_PEER_REVIEWED_PUBLICATION_IDENTIFIED"
            ),
            "pmid": "",
            "doi": "",
            "url": (
                "https://datapoints.ginkgo.bio/functional-genomics/"
                "gdpx2-processed-data"
            ),
            "source_dataset_name": (
                "GDPa2.1 Antibody Developability Profiling of 18 VHH "
                "Constructs"
            ),
            "associated_publication": "",
            "licence": "UNKNOWN",
            "redistribution_status": "PERMISSION_REQUIRED",
            "notes": (
                "Repository dataset covering 18 VHH constructs, including "
                "VHH-His and VHH-Fc formats. No associated peer-reviewed "
                "publication was identified; the Arsiwala DOI is not assigned."
            ),
        },
        "GDPa3": {
            "source_type": (
                "public_repository_dataset_and_peer_reviewed_publication"
            ),
            "publication_status": "peer_reviewed",
            "pmid": "41724677",
            "doi": "10.1080/19420862.2026.2634216",
            "url": "https://pubmed.ncbi.nlm.nih.gov/41724677/",
            "source_dataset_name": "GDPa3 Antibody Developability Dataset",
            "associated_publication": (
                "Ginkgo Datapoints Antibody Developability Competition "
                "outcomes: limited model performance and a call for data "
                "standardization"
            ),
            "licence": "UNKNOWN",
            "redistribution_status": "PERMISSION_REQUIRED",
            "notes": (
                "Dataset scope: 80 held-out IgG antibodies. Article open "
                "access does not establish repository-dataset redistribution "
                "permission."
            ),
        },
    }
    output: list[dict[str, Any]] = []
    for block_id in ("GDPa1", "GDPa2", "GDPa3"):
        values = metadata[block_id]
        source_id = SOURCE_STUDY_IDS[block_id]
        output.append(
            {
                "record_id": source_id,
                "source_id": source_id,
                "source_type": values["source_type"],
                "publication_status": values["publication_status"],
                "source_study_label": block_id,
                "source_block_id": block_id,
                "pmid": values["pmid"],
                "doi": values["doi"],
                "url": values["url"],
                "source_dataset_name": values["source_dataset_name"],
                "source_version": "",
                "provider": "Ginkgo Datapoints / Ginkgo Bioworks",
                "associated_publication": values["associated_publication"],
                "citation_status": (
                    "COMPLETE"
                    if values["associated_publication"]
                    else "NOT_APPLICABLE_NO_ASSOCIATED_PUBLICATION"
                ),
                "source_attribution_status": "CONFIRMED_SOURCE_ATTRIBUTION",
                "licence": values["licence"],
                "redistribution_status": values["redistribution_status"],
                "evidence_record_count": "",
                "secondary_distribution_source": "",
                "notes": values["notes"],
                "lineage_sources_json": _sources_json(refs),
            }
        )
    return output


def _confirmed_literature_key(source_sheet: str) -> str | None:
    normalized = _normalized_header(source_sheet)
    if normalized.startswith("garbinski_et_al"):
        return "garbinski"
    if normalized.startswith("shanehsazzadeh_et_al"):
        return "shanehsazzadeh"
    return None


def _apply_confirmed_literature_override(
    source: dict[str, Any], source_sheet: str
) -> dict[str, Any]:
    key = _confirmed_literature_key(source_sheet)
    if key == "garbinski":
        source.update(
            {
                "source_type": "unpublished_contributor_dataset",
                "publication_status": "unpublished",
                "source_study_label": (
                    "Garbinski et al. unpublished data, 2023"
                ),
                "pmid": "",
                "doi": "",
                "url": "",
                "citation_status": "NOT_APPLICABLE_UNPUBLISHED",
                "licence": "UNKNOWN_PENDING_DATASET_LEVEL_CONFIRMATION",
                "redistribution_status": "NEEDS_REVIEW",
                "secondary_distribution_source": "FLAb / FLAb2",
                "notes": (
                    "Unpublished contributor dataset; FLAb / FLAb2 is a "
                    "secondary distribution source, not a publication."
                ),
            }
        )
    elif key == "shanehsazzadeh":
        source.update(
            {
                "source_type": "preprint_and_public_repository",
                "publication_status": "preprint",
                "doi": "10.1101/2023.01.08.523187",
                "citation_status": "COMPLETE",
                "licence": "Clear BSD",
                "redistribution_status": (
                    "REVIEW_ATTRIBUTION_AND_REPOSITORY_TERMS"
                ),
                "notes": (
                    "Preprint with a public repository dataset; downstream "
                    "reuse must review attribution and repository terms."
                ),
            }
        )
    return source


def _source_evidence_record(source: dict[str, Any]) -> dict[str, Any]:
    evidence_basis = (
        "Phase 2D confirmed source attribution and source-specific evidence"
        if source["source_block_id"]
        or _confirmed_literature_key(source["source_study_label"])
        else "Authoritative data dictionary and source-linked records"
    )
    return {
        "record_id": stable_content_id(
            "SRCEVID", [source["source_id"]]
        ),
        "source_id": source["source_id"],
        "source_study_label": source["source_study_label"],
        "source_type": source["source_type"],
        "publication_status": source["publication_status"],
        "citation_status": source["citation_status"],
        "source_attribution_status": source["source_attribution_status"],
        "pmid": source["pmid"],
        "doi": source["doi"],
        "url": source["url"],
        "provider": source["provider"],
        "associated_publication": source["associated_publication"],
        "licence": source["licence"],
        "redistribution_status": source["redistribution_status"],
        "evidence_basis": evidence_basis,
        "lineage_sources_json": source["lineage_sources_json"],
    }


def _source_review(
    source: dict[str, Any],
    issue_type: str,
    reason: str,
    candidate_count: int | str = "",
) -> dict[str, Any]:
    return {
        "record_id": stable_content_id(
            "SRCREV", [source["source_id"], issue_type]
        ),
        "source_id": source["source_id"],
        "source_study_label": source["source_study_label"],
        "issue_type": issue_type,
        "review_status": "NEEDS_REVIEW",
        "review_reason": reason,
        "candidate_count": candidate_count,
        "reviewer_decision": "",
        "lineage_sources_json": source["lineage_sources_json"],
    }


def _licence_review(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": stable_content_id(
            "LICREV", [source["source_id"], source["licence"]]
        ),
        "source_id": source["source_id"],
        "source_study_label": source["source_study_label"],
        "licence": source["licence"],
        "redistribution_status": source["redistribution_status"],
        "review_status": "NEEDS_REVIEW",
        "review_reason": (
            "Review the source-specific licence, attribution, and "
            "redistribution terms before release."
        ),
        "reviewer_decision": "",
        "lineage_sources_json": source["lineage_sources_json"],
    }


def _index_dictionary_rows(
    rows: tuple[SourceRow, ...],
) -> dict[str, list[SourceRow]]:
    index: dict[str, list[SourceRow]] = {}
    for row in rows:
        value = _row_lookup(row).get("source_sheet")
        if _is_blank(value):
            continue
        index.setdefault(str(value), []).append(row)
    return index


def _ordered_source_sheets(result: ExportResult) -> tuple[str, ...]:
    ordered: list[str] = []
    for row in result.rows:
        value = row.get("source_sheet")
        if _is_blank(value):
            continue
        source_sheet = str(value)
        if source_sheet not in ordered:
            ordered.append(source_sheet)
    return tuple(ordered)


def _evidence_by_sheet(result: ExportResult) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in result.rows:
        sheet = serialize_scalar(row.get("source_sheet"))
        if sheet:
            grouped.setdefault(sheet, []).append(row)
    return grouped


def _evidence_refs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in rows:
        if all(
            row.get(field) not in (None, "")
            for field in (
                "source_workbook",
                "source_input_sha256",
                "source_sheet",
                "source_excel_row",
            )
        ):
            refs.append(
                {
                    "input_file": row["source_workbook"],
                    "input_file_sha256": row["source_input_sha256"],
                    "input_sheet": row["source_sheet"],
                    "input_excel_row": row["source_excel_row"],
                    "input_column_or_columns": "*",
                }
            )
    return refs


def _dictionary_ref(context: BuildContext, row: SourceRow) -> dict[str, Any]:
    return {
        "input_file": context.core_inputs["data_dictionary"].name,
        "input_file_sha256": context.input_shas["data_dictionary"],
        "input_sheet": row.sheet_name,
        "input_excel_row": row.excel_row,
        "input_column_or_columns": "*",
    }


def _sources_json(refs: list[dict[str, Any]]) -> str:
    keyed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ref in refs:
        key = (
            serialize_scalar(ref.get("input_file")),
            serialize_scalar(ref.get("input_sheet")),
            serialize_scalar(ref.get("input_excel_row")),
        )
        keyed[key] = ref
    return json.dumps(
        [keyed[key] for key in sorted(keyed)],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _row_lookup(row: SourceRow) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for item in row.payload:
        key = _normalized_header(item.header)
        if key and key not in lookup:
            lookup[key] = item.value
    return lookup


def _normalized_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return re.sub(r"[^0-9a-z]+", "_", normalized).strip("_")


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _blank_to_empty(value: Any) -> Any:
    return "" if _is_blank(value) else value
