from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .source_licence_audit import (
    ALLOWED_BLOCKING_STATUSES,
    ALLOWED_STRATEGIES,
)
from .workbook_reader import sha256_file


ALLOWED_SEQUENCE_CLASSES = {
    "paired_vh_vl",
    "heavy_only",
    "light_only",
    "full_length_only",
    "cdr_only",
    "mixed_or_other",
    "no_sequence",
}

ALLOWED_SOURCE_TYPES = {
    "peer_reviewed_publication",
    "preprint",
    "public_repository_dataset",
    "preprint_and_public_repository",
    "unpublished_contributor_dataset",
    "database_resource",
    "pending_author_confirmation",
    "pending_source_evidence_reconciliation",
    "public_repository_dataset_and_peer_reviewed_publication",
    "other",
}

ALLOWED_IDENTITY_COVERAGE_STATUSES = {
    "metadata_and_sequence",
    "sequence_only",
    "metadata_only",
    "unresolved",
}

ALLOWED_IDENTITY_RELATIONSHIP_CLASSES = {
    "same_name_same_pair",
    "exact_alias_candidate",
    "metadata_to_sequence_only_link",
    "same_variable_domain_distinct_format",
    "same_variable_domain_distinct_product",
    "parent_derivative_candidate",
    "different_name_same_pair_same_context",
    "different_name_same_pair_different_context",
    "unresolved",
}

PERSONAL_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE),
    re.compile(r"/(?:home|Users)/[^/\s]+", re.IGNORECASE),
)

PROHIBITED_HEADLINE_TOKENS = (
    "324,000",
    "315,968",
    "2.89 million",
    "2.921 million",
    "4.9 million",
    "54.9 million",
)

LINEAGE_REQUIRED_FIELDS = (
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
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


@dataclass(frozen=True)
class ValidationReport:
    release_root: Path
    passed: bool
    checks: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_markdown(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            "# DOTAD 2.0 core release validation",
            "",
            f"Overall status: **{status}**",
            "",
            "## Checks",
            "",
            "| Check | Status |",
            "|---|---|",
        ]
        lines.extend(f"| {name} | {value} |" for name, value in self.checks)
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {value}" for value in self.errors or ("None.",))
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {value}" for value in self.warnings or ("None.",))
        return "\n".join(lines) + "\n"


def validate_release(release_root: Path) -> ValidationReport:
    root = Path(release_root)
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[tuple[str, str]] = []

    manifest_path = root / "release_manifest.tsv"
    if not manifest_path.is_file():
        errors.append("release_manifest.tsv is missing")
        return _report(root, checks, errors, warnings)

    manifest = _read_tsv(manifest_path)
    checks.append(("manifest_rows", str(len(manifest))))
    lineage_rows = _read_tsv(root / "provenance" / "record_lineage.tsv")
    _validate_lineage_rows(root, lineage_rows, errors)
    lineage_keys = {
        (row.get("output_table", ""), row.get("output_record_id", ""))
        for row in lineage_rows
    }

    for entry in manifest:
        relative = entry.get("file_path", "")
        table = entry.get("table_name", "")
        path = root / relative
        if not path.is_file():
            errors.append(f"Manifest file is missing: {relative}")
            continue
        expected_sha = entry.get("sha256", "")
        actual_sha = sha256_file(path)
        if expected_sha != actual_sha:
            errors.append(f"Checksum mismatch for {relative}")
        rows = _read_tsv(path)
        expected_count = _parse_int(entry.get("row_count", ""))
        if expected_count is None or expected_count != len(rows):
            errors.append(
                f"Row-count mismatch for {table}: manifest={expected_count}, "
                f"actual={len(rows)}"
            )
        if _is_true(entry.get("lineage_required", "")):
            _validate_formal_ids_and_lineage(table, rows, lineage_keys, errors)

    _validate_lineage_coverage(root, errors)
    _validate_sequence_classes(root, errors)
    _validate_source_licences(root, errors)
    _validate_source_licence_audit(root, errors)
    _validate_phase2b_vocabularies(root, errors)
    _validate_no_automatic_identity_merge(root, errors)
    _validate_companion_exclusion(root, errors)
    _validate_text_safety(root, errors)
    checks.extend(
        [
            ("lineage_rows", str(len(lineage_rows))),
            ("errors", str(len(errors))),
            ("warnings", str(len(warnings))),
        ]
    )
    return _report(root, checks, errors, warnings)


def _validate_lineage_rows(
    root: Path,
    lineage_rows: list[dict[str, str]],
    errors: list[str],
) -> None:
    if not lineage_rows:
        errors.append("record_lineage.tsv is missing or empty")
        return

    missing_fields = {
        field: sum(not str(row.get(field, "")).strip() for row in lineage_rows)
        for field in LINEAGE_REQUIRED_FIELDS
    }
    for field, count in missing_fields.items():
        if count:
            errors.append(
                f"record_lineage.tsv has {count} rows with blank {field}"
            )

    lineage_ids = [
        str(row.get("lineage_id", "")).strip()
        for row in lineage_rows
        if str(row.get("lineage_id", "")).strip()
    ]
    if len(lineage_ids) != len(set(lineage_ids)):
        errors.append("record_lineage.tsv contains duplicate lineage_id values")

    invalid_hashes = sum(
        bool(value) and SHA256_PATTERN.fullmatch(value) is None
        for value in (
            str(row.get("input_file_sha256", "")).strip()
            for row in lineage_rows
        )
    )
    if invalid_hashes:
        errors.append(
            f"record_lineage.tsv has {invalid_hashes} invalid "
            "input_file_sha256 values"
        )

    invalid_rows = 0
    for row in lineage_rows:
        value = str(row.get("input_excel_row", "")).strip()
        if not value:
            continue
        try:
            valid = int(value) > 0
        except ValueError:
            valid = False
        if not valid:
            invalid_rows += 1
    if invalid_rows:
        errors.append(
            f"record_lineage.tsv has {invalid_rows} invalid input_excel_row values"
        )

    rule_rows = _read_tsv(root / "provenance" / "transformation_rules.tsv")
    rule_ids = {
        str(row.get("transformation_rule_id", "")).strip()
        for row in rule_rows
        if str(row.get("transformation_rule_id", "")).strip()
    }
    if not rule_ids:
        errors.append("transformation_rules.tsv is missing or empty")
    unknown_rules = sorted(
        {
            str(row.get("transformation_rule_id", "")).strip()
            for row in lineage_rows
            if str(row.get("transformation_rule_id", "")).strip()
            and str(row.get("transformation_rule_id", "")).strip()
            not in rule_ids
        }
    )
    if unknown_rules:
        errors.append(
            "record_lineage.tsv uses unknown transformation_rule_id values: "
            + ", ".join(unknown_rules)
        )

    source_root = Path(__file__).resolve().parents[1]
    invalid_scripts = sorted(
        {
            script
            for script in (
                str(row.get("transformation_script", "")).strip()
                for row in lineage_rows
            )
            if script
            and (
                Path(script).is_absolute()
                or ".." in Path(script).parts
                or not (source_root / script).is_file()
            )
        }
    )
    if invalid_scripts:
        errors.append(
            "record_lineage.tsv has invalid transformation_script values: "
            + ", ".join(invalid_scripts)
        )

    invalid_commits = sum(
        bool(value)
        and value != "UNKNOWN"
        and COMMIT_PATTERN.fullmatch(value) is None
        for value in (
            str(row.get("transformation_script_commit", "")).strip()
            for row in lineage_rows
        )
    )
    if invalid_commits:
        errors.append(
            f"record_lineage.tsv has {invalid_commits} invalid "
            "transformation_script_commit values"
        )


def _validate_formal_ids_and_lineage(
    table: str,
    rows: list[dict[str, str]],
    lineage_keys: set[tuple[str, str]],
    errors: list[str],
) -> None:
    ids = [row.get("record_id", "") for row in rows]
    if any(not value for value in ids):
        errors.append(f"{table} contains a blank record_id")
    if len(ids) != len(set(ids)):
        errors.append(f"{table} contains duplicate record_id values")
    missing = [
        record_id
        for record_id in ids
        if record_id and (table, record_id) not in lineage_keys
    ]
    if missing:
        errors.append(
            f"{table} has {len(missing)} released rows without lineage"
        )


def _validate_lineage_coverage(root: Path, errors: list[str]) -> None:
    path = root / "qc" / "lineage_coverage.tsv"
    rows = _read_tsv(path)
    if not rows:
        errors.append("lineage_coverage.tsv is missing or empty")
        return
    for row in rows:
        if _is_true(row.get("lineage_required", "")):
            try:
                coverage = float(row.get("coverage_fraction", ""))
            except ValueError:
                coverage = -1.0
            if coverage != 1.0 or row.get("rows_without_lineage") not in ("0", "0.0"):
                errors.append(
                    f"Lineage coverage is not 1.0 for "
                    f"{row.get('output_table', '')}"
                )


def _validate_sequence_classes(root: Path, errors: list[str]) -> None:
    path = root / "sequences" / "antibody_sequences.tsv"
    if not path.is_file():
        return
    invalid = sorted(
        {
            row.get("sequence_class", "")
            for row in _read_tsv(path)
            if row.get("sequence_class", "") not in ALLOWED_SEQUENCE_CLASSES
        }
    )
    if invalid:
        errors.append(f"Invalid sequence_class values: {', '.join(invalid)}")


def _validate_source_licences(root: Path, errors: list[str]) -> None:
    path = root / "provenance" / "source_manifest.tsv"
    if not path.is_file():
        return
    for row in _read_tsv(path):
        if not row.get("licence"):
            errors.append(
                f"Blank licence in source_manifest for {row.get('record_id', '')}"
            )
        if not row.get("redistribution_status"):
            errors.append(
                "Blank redistribution_status in source_manifest for "
                f"{row.get('record_id', '')}"
            )


def _validate_source_licence_audit(
    root: Path,
    errors: list[str],
) -> None:
    licence_path = root / "provenance" / "licence_evidence_registry.tsv"
    strategy_path = root / "provenance" / "redistribution_strategy.tsv"
    if not licence_path.is_file() and not strategy_path.is_file():
        return

    licences = {
        row.get("source_id", ""): row
        for row in _read_tsv(licence_path)
        if row.get("source_id", "")
    }
    for row in _read_tsv(strategy_path):
        source_id = row.get("source_id", "")
        strategy = row.get("strategy", "")
        blocking_status = row.get("blocking_status", "")
        if strategy not in ALLOWED_STRATEGIES:
            errors.append(
                f"Invalid redistribution strategy for {source_id}: {strategy}"
            )
        if blocking_status not in ALLOWED_BLOCKING_STATUSES:
            errors.append(
                f"Invalid redistribution blocking_status for {source_id}: "
                f"{blocking_status}"
            )
        licence = licences.get(source_id, {})
        licence_name = licence.get("licence_name_as_reported", "")
        evidence_status = licence.get("evidence_status", "")
        licence_unknown = (
            not licence
            or not licence_name
            or licence_name.upper().startswith("UNKNOWN")
            or evidence_status == "DATASET_LEVEL_LICENCE_NOT_FOUND"
        )
        if strategy == "FULL_REDEPOSIT_ALLOWED" and (
            licence_unknown or _is_true(row.get("permission_required", ""))
        ):
            errors.append(
                "FULL_REDEPOSIT_ALLOWED is not permitted for an unknown, "
                f"unverified, or permission-gated source: {source_id}"
            )


def _validate_phase2b_vocabularies(
    root: Path, errors: list[str]
) -> None:
    checks = (
        (
            root / "provenance" / "source_manifest.tsv",
            "source_type",
            ALLOWED_SOURCE_TYPES,
        ),
        (
            root / "metadata" / "identity_coverage_status.tsv",
            "coverage_status",
            ALLOWED_IDENTITY_COVERAGE_STATUSES,
        ),
        (
            root / "review" / "identity_relationship_groups.tsv",
            "relationship_class",
            ALLOWED_IDENTITY_RELATIONSHIP_CLASSES,
        ),
    )
    for path, field, allowed in checks:
        if not path.is_file():
            continue
        invalid = sorted(
            {
                row.get(field, "")
                for row in _read_tsv(path)
                if row.get(field, "") not in allowed
            }
        )
        if invalid:
            errors.append(
                f"Invalid {field} values: {', '.join(invalid)}"
            )


def _validate_no_automatic_identity_merge(
    root: Path, errors: list[str]
) -> None:
    candidates = (
        root / "metadata" / "identity_mapping_candidates.tsv"
    )
    if candidates.is_file() and any(
        row.get("proposed_antibody_id", "").strip()
        for row in _read_tsv(candidates)
    ):
        errors.append(
            "Identity mapping candidates contain an automatic identifier "
            "merge proposal."
        )
    groups = root / "review" / "identity_relationship_groups.tsv"
    if groups.is_file() and any(
        "merge" in row.get("reviewer_decision", "").lower()
        for row in _read_tsv(groups)
    ):
        errors.append(
            "Identity relationship groups contain an automatic merge decision."
        )


def _validate_companion_exclusion(root: Path, errors: list[str]) -> None:
    path = root / "qc" / "core_table_counts.tsv"
    if not path.is_file():
        errors.append("core_table_counts.tsv is missing")
        return
    for row in _read_tsv(path):
        table = row.get("table_name", "").lower()
        if "affinity" in table or "companion" in table:
            errors.append("Affinity companion appears in core table counts")


def _validate_text_safety(root: Path, errors: list[str]) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".tsv", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(text) for pattern in PERSONAL_PATH_PATTERNS):
            errors.append(
                f"Release text contains an absolute personal path: "
                f"{path.relative_to(root).as_posix()}"
            )
    for name in ("README_DRAFT.md", "CHANGELOG_DRAFT.md"):
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for token in PROHIBITED_HEADLINE_TOKENS:
            if token.lower() in text:
                errors.append(
                    f"Prohibited legacy headline token in {name}: {token}"
                )
        if "total data points" in text or "grand total data points" in text:
            errors.append(f"Undefined aggregate data-points claim in {name}")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not Path(path).is_file():
        return []
    csv.field_size_limit(2**31 - 1)
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_true(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _report(
    root: Path,
    checks: list[tuple[str, str]],
    errors: list[str],
    warnings: list[str],
) -> ValidationReport:
    return ValidationReport(
        release_root=root,
        passed=not errors,
        checks=tuple(checks),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
