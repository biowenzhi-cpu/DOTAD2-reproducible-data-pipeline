from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


VALID_MAPPING_STATUSES = {
    "exact_file_and_script",
    "exact_file_no_script",
    "probable_file",
    "multiple_candidates",
    "no_candidate",
}
VALID_VERIFICATION_STATUSES = {
    "verified_exact",
    "verified_rounding",
    "verified_with_documented_filter",
    "partially_verified",
    "not_reproducible",
    "not_attempted_due_to_missing_input",
    "contradictory",
}


def map_claims(
    claims: list[dict[str, Any]],
    file_inventory: list[dict[str, Any]],
    table_inventory: list[dict[str, Any]],
    script_inventory: list[dict[str, Any]],
    facts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for claim in claims:
        claim_id = str(claim["claim_id"])
        fact = facts.get(claim_id)
        candidates = candidate_sources(claim, file_inventory, table_inventory)
        scripts = candidate_scripts(claim, script_inventory)
        if fact:
            mapping_status = fact.get(
                "mapping_status",
                "exact_file_and_script" if fact.get("candidate_script") else "exact_file_no_script",
            )
            verification_status = fact.get("verification_status", "partially_verified")
            recomputed = fact.get("recomputed_value", "")
            absolute, relative = differences(claim.get("claimed_value"), recomputed)
            row = {
                "claim_id": claim_id,
                "claim_text": claim["claim_text"],
                "claimed_value": claim.get("claimed_value", ""),
                "candidate_source_file": fact.get("candidate_source_file", ""),
                "candidate_sheet_or_table": fact.get("candidate_sheet_or_table", ""),
                "candidate_columns": fact.get("candidate_columns", ""),
                "candidate_filter_logic": fact.get("candidate_filter_logic", ""),
                "candidate_script": fact.get("candidate_script", ""),
                "reproduction_command": fact.get("reproduction_command", ""),
                "recomputed_value": recomputed,
                "difference_absolute": absolute,
                "difference_relative": relative,
                "mapping_status": mapping_status,
                "verification_status": verification_status,
                "confidence": fact.get("confidence", "medium"),
                "notes": fact.get("notes", ""),
            }
        else:
            mapping_status = (
                "no_candidate"
                if not candidates
                else "probable_file"
                if len(candidates) == 1
                else "multiple_candidates"
            )
            verification_status = (
                "not_attempted_due_to_missing_input"
                if not candidates
                else "not_reproducible"
            )
            row = {
                "claim_id": claim_id,
                "claim_text": claim["claim_text"],
                "claimed_value": claim.get("claimed_value", ""),
                "candidate_source_file": " | ".join(candidate["relative_path"] for candidate in candidates[:8]),
                "candidate_sheet_or_table": "",
                "candidate_columns": "",
                "candidate_filter_logic": "",
                "candidate_script": " | ".join(script["relative_path"] for script in scripts[:8]),
                "reproduction_command": "",
                "recomputed_value": "",
                "difference_absolute": "",
                "difference_relative": "",
                "mapping_status": mapping_status,
                "verification_status": verification_status,
                "confidence": "low" if candidates else "high",
                "notes": (
                    "Numeric evidence or an executable derivation was not found in the supplied archive. "
                    "Filename/column similarity is not sufficient for verification."
                ),
            }
        validate_mapping_row(row)
        output.append(row)
    return output


def validate_mapping_row(row: dict[str, Any]) -> None:
    if row["mapping_status"] not in VALID_MAPPING_STATUSES:
        raise ValueError(f"Invalid mapping_status: {row['mapping_status']}")
    if row["verification_status"] not in VALID_VERIFICATION_STATUSES:
        raise ValueError(f"Invalid verification_status: {row['verification_status']}")
    if str(row["verification_status"]).startswith("verified") and not row.get("reproduction_command"):
        raise ValueError("Verified claims require a reproduction command.")


def candidate_sources(
    claim: dict[str, Any],
    files: list[dict[str, Any]],
    tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    haystack = f"{claim.get('metric_name', '')} {claim.get('claim_category', '')}".lower()
    keywords: set[str] = set()
    if any(token in haystack for token in ("sequence", "entry", "resource_size")):
        keywords.update(("sequence", "dataset", "metadata", "benchmark"))
    if any(token in haystack for token in ("annotation", "record")):
        keywords.update(("experimental", "literature", "dataset", "benchmark"))
    if any(token in haystack for token in ("coverage", "missing", "composition", "subset")):
        keywords.update(("statistics", "metadata", "experimental"))
    if any(token in haystack for token in ("correlation", "pca", "concordance", "pair")):
        keywords.update(("statistics", "figure", "harmon", "experimental"))
    if "benchmark" in haystack:
        keywords.add("benchmark")
    scored: list[tuple[int, dict[str, Any]]] = []
    for file_row in files:
        path = file_row["relative_path"].lower()
        score = sum(keyword in path for keyword in keywords)
        if score:
            scored.append((score, file_row))
    scored.sort(key=lambda item: (-item[0], item[1]["relative_path"]))
    return [item[1] for item in scored[:12]]


def candidate_scripts(claim: dict[str, Any], scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    category = str(claim.get("claim_category", "")).lower()
    metric = str(claim.get("metric_name", "")).lower()
    tokens = {category, *metric.replace("/", " ").replace("-", " ").split()}
    scored: list[tuple[int, dict[str, Any]]] = []
    for script in scripts:
        text = f"{script.get('relative_path', '')} {script.get('purpose_guess', '')}".lower()
        score = sum(token and token in text for token in tokens)
        if score:
            scored.append((score, script))
    scored.sort(key=lambda item: (-item[0], item[1]["relative_path"]))
    return [item[1] for item in scored]


def differences(claimed: Any, recomputed: Any) -> tuple[float | str, float | str]:
    try:
        claimed_number = float(claimed)
        recomputed_number = float(recomputed)
    except (TypeError, ValueError):
        return "", ""
    absolute = recomputed_number - claimed_number
    relative = absolute / claimed_number if claimed_number else math.nan
    return round(absolute, 9), round(relative, 9) if not math.isnan(relative) else ""


def unresolved_claims(mapping_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in mapping_rows
        if row["verification_status"]
        in {"not_reproducible", "not_attempted_due_to_missing_input"}
    ]


def discrepancies(mapping_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in mapping_rows
        if row["verification_status"] in {"contradictory", "partially_verified"}
        or (
            row.get("difference_absolute") not in ("", None, 0, 0.0)
            and str(row["verification_status"]).startswith("verified")
        )
    ]
