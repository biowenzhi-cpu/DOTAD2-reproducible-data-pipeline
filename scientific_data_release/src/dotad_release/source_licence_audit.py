from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .ids import stable_content_id
from .models import BuildContext, ExportResult
from .source_export import SourceOutputs
from .workbook_reader import SourceRow, iter_source_rows, serialize_scalar, write_tsv


ACCESS_DATE = "2026-07-30"
GDPA1_LICENSE_TEXT = (
    "This Dataset is licensed under CC BY 4.0 for commercial use not "
    "involving the sale, transfer, or licensing of the Dataset (or any data "
    "or information contained therein) itself."
)

COUNT_FIELDS = (
    "record_id",
    "source_record_id",
    "source_id",
    "metric",
    "source_location",
    "reported_value",
    "frozen_dotad_value",
    "difference",
    "difference_reason",
    "authoritative_for_release",
    "notes",
)

LICENCE_FIELDS = (
    "record_id",
    "source_record_id",
    "source_id",
    "dataset_or_publication",
    "licence_name_as_reported",
    "licence_text_verbatim",
    "licence_url",
    "access_date",
    "applies_to",
    "commercial_use",
    "redistribution",
    "derivative_distribution",
    "attribution_required",
    "registration_or_gate",
    "evidence_status",
    "review_notes",
)

REDISTRIBUTION_FIELDS = (
    "record_id",
    "source_record_id",
    "source_id",
    "release_component",
    "contains_original_values",
    "contains_original_sequences",
    "contains_copied_text",
    "contains_dotad_annotations",
    "strategy",
    "permission_required",
    "attribution_text",
    "blocking_status",
    "notes",
)

LITERATURE_FIELDS = (
    "record_id",
    "source_record_id",
    "literature_source_id",
    "sheet_name",
    "source_title",
    "authors",
    "year",
    "journal_or_repository",
    "doi",
    "pmid",
    "url",
    "publication_type",
    "open_access_status",
    "article_licence",
    "supplementary_data_licence",
    "dataset_repository",
    "redistribution_status",
    "source_row_count",
    "dotad_record_count",
    "citation_verified",
    "licence_verified",
    "review_notes",
)

AFFINITY_FIELDS = (
    "record_id",
    "source_record_id",
    "dataset_name",
    "source_publication",
    "source_repository",
    "doi",
    "pmid",
    "source_url",
    "dataset_licence",
    "redistribution_status",
    "contains_sequences",
    "contains_measurements",
    "contains_predicted_values",
    "recommended_release_strategy",
    "notes",
)

ALLOWED_STRATEGIES = {
    "FULL_REDEPOSIT_ALLOWED",
    "DERIVED_DATA_ONLY",
    "METADATA_AND_LINEAGE_ONLY",
    "LINK_TO_ORIGINAL_SOURCE",
    "WRITTEN_PERMISSION_REQUIRED",
    "EXCLUDE_FROM_PUBLIC_RELEASE",
    "UNRESOLVED",
}

ALLOWED_BLOCKING_STATUSES = {
    "CLEAR",
    "NEEDS_PERMISSION",
    "NEEDS_AUTHOR_CONFIRMATION",
    "LINK_ONLY",
    "EXCLUDE",
    "LEGAL_REVIEW_RECOMMENDED",
}


@dataclass(frozen=True)
class SourceLicenceAuditOutputs:
    source_count_reconciliation: ExportResult
    licence_evidence_registry: ExportResult
    redistribution_strategy: ExportResult
    literature_source_registry: ExportResult
    affinity_source_licence_registry: ExportResult


def build_source_licence_audit(
    context: BuildContext,
    source_outputs: SourceOutputs,
    literature_result: ExportResult,
    affinity_manifest: ExportResult,
) -> SourceLicenceAuditOutputs:
    source_rows = {
        serialize_scalar(row.get("source_id")): row
        for row in source_outputs.source_manifest.rows
    }
    count_rows = _source_count_rows(context, source_rows)
    licence_rows = _licence_rows(source_rows)
    strategy_rows = _redistribution_rows(source_rows)
    literature_rows = _literature_registry_rows(
        context,
        source_rows,
        literature_result,
    )
    affinity_rows = _affinity_registry_rows(affinity_manifest)

    outputs = (
        _write_result(
            context,
            "source_count_reconciliation",
            "provenance/source_count_reconciliation.tsv",
            COUNT_FIELDS,
            count_rows,
            "one reported-versus-frozen source count comparison",
        ),
        _write_result(
            context,
            "licence_evidence_registry",
            "provenance/licence_evidence_registry.tsv",
            LICENCE_FIELDS,
            licence_rows,
            "one conservative dataset-level licence evidence record per source",
        ),
        _write_result(
            context,
            "redistribution_strategy",
            "provenance/redistribution_strategy.tsv",
            REDISTRIBUTION_FIELDS,
            strategy_rows,
            "one proposed redistribution strategy per source component",
        ),
        _write_result(
            context,
            "literature_source_registry",
            "provenance/literature_source_registry.tsv",
            LITERATURE_FIELDS,
            literature_rows,
            "one source-audit row per literature workbook study sheet",
        ),
        _write_result(
            context,
            "affinity_source_licence_registry",
            "companion/affinity_source_licence_registry.tsv",
            AFFINITY_FIELDS,
            affinity_rows,
            "one licence-audit row per affinity README dataset",
        ),
    )
    return SourceLicenceAuditOutputs(*outputs)


def _source_count_rows(
    context: BuildContext,
    source_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    source_id = "SRC:GINKGO_GDPA1"
    frozen_count = _gdpa1_summary_count(context)
    source_record_id = source_rows[source_id]["record_id"]
    specifications = (
        (
            "README opening antibody count",
            (
                "https://huggingface.co/datasets/ginkgo-datapoints/GDPa1/"
                "blob/main/README.md"
            ),
            242,
            (
                "The README opening description reports 242 antibodies, "
                "whereas another README section and the publication report 246."
            ),
            False,
            "Preserved as a documented source-level count inconsistency.",
        ),
        (
            "Peer-reviewed publication antibody count",
            "https://pubmed.ncbi.nlm.nih.gov/41328470/",
            246,
            "The associated peer-reviewed publication reports 246 antibodies.",
            False,
            "Publication count corroborates the frozen workbook count.",
        ),
        (
            "Frozen workbook summary row count",
            (
                f"{context.core_inputs['experimental'].name}!"
                f"{_gdpa1_summary_sheet(context)}"
            ),
            frozen_count,
            "Count of non-empty source rows in the frozen GDPa1 summary sheet.",
            True,
            "Authoritative count for this DOTAD release.",
        ),
    )
    rows = []
    for metric, location, reported, reason, authoritative, notes in specifications:
        rows.append(
            {
                "record_id": stable_content_id(
                    "SRCCOUNT", [source_id, metric, location]
                ),
                "source_record_id": source_record_id,
                "source_id": source_id,
                "metric": metric,
                "source_location": location,
                "reported_value": reported,
                "frozen_dotad_value": frozen_count,
                "difference": reported - frozen_count,
                "difference_reason": reason,
                "authoritative_for_release": authoritative,
                "notes": notes,
            }
        )
    return rows


def _licence_rows(
    source_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_id in sorted(source_rows):
        source = source_rows[source_id]
        dataset_name = serialize_scalar(
            source.get("source_dataset_name")
            or source.get("source_study_label")
        )
        if source_id == "SRC:GINKGO_GDPA1":
            values = {
                "licence_name_as_reported": "cc-by-with-restrictions",
                "licence_text_verbatim": GDPA1_LICENSE_TEXT,
                "licence_url": (
                    "https://huggingface.co/datasets/ginkgo-datapoints/GDPa1/"
                    "blob/main/LICENSE.md"
                ),
                "applies_to": "GDPa1 repository dataset",
                "commercial_use": (
                    "Allowed only under the dataset-specific restriction "
                    "stated in LICENSE.md"
                ),
                "redistribution": "PERMISSION_REQUIRED",
                "derivative_distribution": "REQUIRES_TERMS_REVIEW",
                "attribution_required": True,
                "registration_or_gate": (
                    "Repository access is gated and requests name, company, "
                    "work email, and intended use"
                ),
                "evidence_status": "DATASET_LEVEL_LICENCE_FOUND_WITH_RESTRICTIONS",
                "review_notes": (
                    "Do not normalize this licence to unrestricted CC BY 4.0. "
                    "Written permission is requested for a public frozen "
                    "Scientific Data companion deposit."
                ),
            }
        elif source_id in {"SRC:GINKGO_GDPA2_1", "SRC:GINKGO_GDPA3"}:
            values = _unknown_dataset_licence(
                (
                    "No explicit dataset-level licence was identified in the "
                    "reviewed official source evidence. Article open-access "
                    "terms, when present, do not propagate to the dataset."
                )
            )
        else:
            reported = serialize_scalar(source.get("licence"))
            if reported and not reported.upper().startswith("UNKNOWN"):
                values = {
                    "licence_name_as_reported": reported,
                    "licence_text_verbatim": "",
                    "licence_url": "",
                    "applies_to": "source-specific materials as previously recorded",
                    "commercial_use": "REQUIRES_SCOPE_CONFIRMATION",
                    "redistribution": "PERMISSION_REQUIRED",
                    "derivative_distribution": "REQUIRES_TERMS_REVIEW",
                    "attribution_required": True,
                    "registration_or_gate": "UNKNOWN",
                    "evidence_status": "PREVIOUSLY_RECORDED_LICENCE_REQUIRES_SCOPE_REVIEW",
                    "review_notes": (
                        "Article, supplementary-data, and repository-dataset "
                        "licence scopes remain separate and require confirmation."
                    ),
                }
            else:
                values = _unknown_dataset_licence(
                    "No dataset-level redistribution licence is recorded."
                )
        rows.append(
            {
                "record_id": stable_content_id(
                    "LICEVID",
                    [source_id, dataset_name, values["licence_name_as_reported"]],
                ),
                "source_record_id": source["record_id"],
                "source_id": source_id,
                "dataset_or_publication": dataset_name,
                "access_date": ACCESS_DATE,
                **values,
            }
        )
    return rows


def _unknown_dataset_licence(notes: str) -> dict[str, Any]:
    return {
        "licence_name_as_reported": "UNKNOWN",
        "licence_text_verbatim": "",
        "licence_url": "",
        "applies_to": "repository or supplementary dataset",
        "commercial_use": "UNKNOWN",
        "redistribution": "PERMISSION_REQUIRED",
        "derivative_distribution": "UNKNOWN",
        "attribution_required": "UNKNOWN",
        "registration_or_gate": "UNKNOWN",
        "evidence_status": "DATASET_LEVEL_LICENCE_NOT_FOUND",
        "review_notes": notes,
    }


def _redistribution_rows(
    source_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_id in sorted(source_rows):
        source = source_rows[source_id]
        is_experimental = bool(source.get("source_block_id"))
        strategy = (
            "WRITTEN_PERMISSION_REQUIRED"
            if is_experimental
            else "METADATA_AND_LINEAGE_ONLY"
        )
        blocking_status = (
            "NEEDS_PERMISSION" if is_experimental else "LINK_ONLY"
        )
        rows.append(
            {
                "record_id": stable_content_id(
                    "REDIST", [source_id, strategy]
                ),
                "source_record_id": source["record_id"],
                "source_id": source_id,
                "release_component": (
                    "experimental_developability_values"
                    if is_experimental
                    else "literature_curated_records"
                ),
                "contains_original_values": True,
                "contains_original_sequences": "UNKNOWN",
                "contains_copied_text": False,
                "contains_dotad_annotations": True,
                "strategy": strategy,
                "permission_required": True,
                "attribution_text": _attribution_text(source),
                "blocking_status": blocking_status,
                "notes": (
                    "Public availability is not treated as permission to "
                    "republish original values. Until permission is confirmed, "
                    "release only metadata, lineage, and source links."
                ),
            }
        )
    return rows


def _literature_registry_rows(
    context: BuildContext,
    source_rows: dict[str, dict[str, Any]],
    literature_result: ExportResult,
) -> list[dict[str, Any]]:
    dictionary_path = context.core_inputs["data_dictionary"]
    dictionary_sheet = context.sheet_roles["data_dictionary"]["literature_sources"]
    dictionary_rows = list(iter_source_rows(dictionary_path, dictionary_sheet))
    by_sheet = {
        serialize_scalar(_row_lookup(row).get("source_sheet")): (row, _row_lookup(row))
        for row in dictionary_rows
        if serialize_scalar(_row_lookup(row).get("source_sheet"))
    }
    counts = Counter(
        serialize_scalar(row.get("source_sheet"))
        for row in literature_result.rows
        if serialize_scalar(row.get("source_sheet"))
    )
    rows: list[dict[str, Any]] = []
    for sheet_name in sorted(counts, key=str.casefold):
        dictionary_row, lookup = by_sheet.get(sheet_name, (None, {}))
        source_id = stable_content_id("SRC", ["LITERATURE", sheet_name])
        source = source_rows[source_id]
        pmid = _first_nonblank(
            lookup.get("pmid"), source.get("pmid")
        )
        doi = _first_nonblank(lookup.get("doi"), source.get("doi"))
        url = _first_nonblank(
            lookup.get("source_url"), source.get("url")
        )
        source_title = _first_nonblank(
            lookup.get("paper_name"),
            lookup.get("source_title"),
            lookup.get("short_name"),
            lookup.get("study_key"),
            sheet_name,
        )
        publication_type = _publication_type(sheet_name, source)
        reported_source_count = _as_int(lookup.get("row_count"))
        dotad_count = counts[sheet_name]
        dataset_repository = (
            url
            if url and "pubmed.ncbi.nlm.nih.gov" not in url.lower()
            else ""
        )
        rows.append(
            {
                "record_id": stable_content_id(
                    "LITSRCREG", [source_id, sheet_name]
                ),
                "source_record_id": source["record_id"],
                "literature_source_id": source_id,
                "sheet_name": sheet_name,
                "source_title": source_title,
                "authors": _first_nonblank(lookup.get("authors")),
                "year": _first_nonblank(lookup.get("year")),
                "journal_or_repository": _first_nonblank(
                    lookup.get("journal"),
                    lookup.get("journal_or_repository"),
                ),
                "doi": doi,
                "pmid": pmid,
                "url": url,
                "publication_type": publication_type,
                "open_access_status": "UNKNOWN",
                "article_licence": "UNKNOWN",
                "supplementary_data_licence": "UNKNOWN",
                "dataset_repository": dataset_repository,
                "redistribution_status": "PERMISSION_REQUIRED",
                "source_row_count": (
                    reported_source_count
                    if reported_source_count is not None
                    else dotad_count
                ),
                "dotad_record_count": dotad_count,
                "citation_verified": bool(pmid or doi or url),
                "licence_verified": False,
                "review_notes": (
                    "Article, supplementary-data, and repository-dataset "
                    "licences were not inferred from one another. "
                    + (
                        ""
                        if reported_source_count in (None, dotad_count)
                        else (
                            f"Dictionary source_row_count={reported_source_count}; "
                            f"DOTAD exported records={dotad_count}. "
                        )
                    )
                ).strip(),
            }
        )
    return rows


def _affinity_registry_rows(
    affinity_manifest: ExportResult,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in affinity_manifest.rows:
        if source.get("source_kind") != "README_DATASET":
            continue
        dataset_name = serialize_scalar(source.get("dataset_file"))
        publication, url = _markdown_link(
            serialize_scalar(source.get("publication_as_declared"))
        )
        doi = _doi_from_url_or_text(url, publication)
        source_repository = (
            url if url and not doi else ""
        )
        assay_text = " ".join(
            (
                serialize_scalar(source.get("assay_units_as_declared")),
                serialize_scalar(source.get("description_as_declared")),
            )
        )
        contains_predicted = "predict" in assay_text.lower()
        rows.append(
            {
                "record_id": stable_content_id(
                    "AFFLIC", [source["record_id"], dataset_name]
                ),
                "source_record_id": source["record_id"],
                "dataset_name": dataset_name,
                "source_publication": publication,
                "source_repository": source_repository,
                "doi": doi,
                "pmid": "",
                "source_url": url,
                "dataset_licence": "UNKNOWN",
                "redistribution_status": "PERMISSION_REQUIRED",
                "contains_sequences": True,
                "contains_measurements": not contains_predicted,
                "contains_predicted_values": contains_predicted,
                "recommended_release_strategy": "LINK_TO_ORIGINAL_SOURCE",
                "notes": (
                    "Affinity data remain a companion collection outside core "
                    "counts. Public availability does not establish permission "
                    "for a new full-data deposit."
                ),
            }
        )
    return sorted(rows, key=lambda row: str(row["dataset_name"]).casefold())


def _write_result(
    context: BuildContext,
    table_name: str,
    relative_path: str,
    fields: tuple[str, ...],
    rows: list[dict[str, Any]],
    definition: str,
) -> ExportResult:
    path = context.output_dir / relative_path
    write_tsv(path, rows, fields)
    return ExportResult(
        table_name,
        path,
        fields,
        tuple(rows),
        definition,
    )


def _gdpa1_summary_sheet(context: BuildContext) -> str:
    return context.sheet_roles["experimental"]["blocks"]["GDPa1"]["summary"]


def _gdpa1_summary_count(context: BuildContext) -> int:
    return sum(
        1
        for _ in iter_source_rows(
            context.core_inputs["experimental"],
            _gdpa1_summary_sheet(context),
        )
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


def _publication_type(
    sheet_name: str,
    source: dict[str, Any],
) -> str:
    normalized = _normalized_header(sheet_name)
    if normalized.startswith("garbinski_et_al"):
        return "unpublished_dataset"
    if normalized.startswith("shanehsazzadeh_et_al"):
        return "preprint"
    source_type = serialize_scalar(source.get("source_type"))
    if "database_resource" in source_type:
        return "database_resource"
    if "repository" in source_type and "publication" in source_type:
        return "mixed_source"
    if "repository" in source_type:
        return "repository_dataset"
    if source.get("pmid") or source.get("doi"):
        return "peer_reviewed_article"
    return "unknown"


def _attribution_text(source: dict[str, Any]) -> str:
    provider = serialize_scalar(source.get("provider"))
    dataset = serialize_scalar(source.get("source_dataset_name"))
    publication = serialize_scalar(source.get("associated_publication"))
    parts = [part for part in (provider, dataset, publication) if part]
    return "; ".join(parts) or serialize_scalar(source.get("source_study_label"))


def _first_nonblank(*values: Any) -> str:
    for value in values:
        text = serialize_scalar(value)
        if text:
            return text
    return ""


def _as_int(value: Any) -> int | None:
    text = serialize_scalar(value).replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _markdown_link(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"\s*\[([^\]]+)\]\(([^)]+)\)\s*", value)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    url_match = re.search(r"https?://\S+", value)
    return value.strip(), url_match.group(0).rstrip(").,") if url_match else ""


def _doi_from_url_or_text(url: str, text: str) -> str:
    candidate = f"{url} {text}"
    match = re.search(r"10\.\d{4,9}/[^\s)\],;]+", candidate, flags=re.I)
    return match.group(0).rstrip(".,") if match else ""
