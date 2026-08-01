from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .ids import stable_content_id
from .metadata_export import normalize_name
from .workbook_reader import serialize_scalar


CATEGORY_ORDER = (
    "same_id_different_pairs",
    "different_ids_same_pair",
    "same_normalized_name_different_pairs",
    "different_names_same_pair",
    "alias_across_ids",
    "parent_mutant_candidate",
)
MAPPING_FIELDS = (
    "record_id",
    "candidate_id",
    "category",
    "antibody_ids",
    "source_record_ids",
    "pair_fingerprints",
    "names",
    "evidence_key",
    "suggested_action",
    "proposed_antibody_id",
)
REVIEW_FIELDS = (
    "record_id",
    "review_id",
    "candidate_id",
    "conflict_type",
    "category",
    "antibody_id_a",
    "antibody_id_b",
    "name_a",
    "name_b",
    "sequence_record_id_a",
    "sequence_record_id_b",
    "paired_fingerprint_a",
    "paired_fingerprint_b",
    "current_relationship",
    "suggested_action",
    "evidence_summary",
    "source_record_ids",
    "status",
    "reviewer",
    "reviewer_decision",
    "review_date",
    "reviewer_notes",
    "review_notes",
)

COVERAGE_FIELDS = (
    "record_id",
    "antibody_id",
    "metadata_available",
    "sequence_available",
    "coverage_status",
    "sequence_record_id",
    "notes",
    "source_record_ids",
)

RELATIONSHIP_GROUP_FIELDS = (
    "record_id",
    "relationship_group_id",
    "paired_vh_vl_fingerprint",
    "member_count",
    "distinct_antibody_id_count",
    "distinct_name_count",
    "distinct_format_count",
    "distinct_target_count",
    "relationship_class",
    "review_priority",
    "reviewer_decision",
    "reviewer",
    "review_date",
    "review_notes",
    "source_record_ids",
)

RELATIONSHIP_MEMBER_FIELDS = (
    "record_id",
    "relationship_group_id",
    "paired_vh_vl_fingerprint",
    "antibody_id",
    "antibody_names",
    "formats",
    "targets",
    "development_stages",
    "metadata_available",
    "sequence_record_ids",
    "source_record_ids",
)


@dataclass(frozen=True)
class IdentityOutputs:
    mapping_candidates: tuple[dict[str, Any], ...]
    review_queue: tuple[dict[str, Any], ...]
    coverage_status: tuple[dict[str, Any], ...]
    relationship_groups: tuple[dict[str, Any], ...]
    relationship_members: tuple[dict[str, Any], ...]

    @property
    def candidate_rows(self) -> tuple[dict[str, Any], ...]:
        return self.mapping_candidates

    @property
    def review_rows(self) -> tuple[dict[str, Any], ...]:
        return self.review_queue


def build_identity_candidates(
    metadata_rows: Iterable[Mapping[str, Any]],
    sequence_rows: Iterable[Mapping[str, Any]],
    fingerprint_rows: Iterable[Mapping[str, Any]],
    alias_rows: Iterable[Mapping[str, Any]],
) -> IdentityOutputs:
    metadata = tuple(metadata_rows)
    sequences = tuple(sequence_rows)
    fingerprints = tuple(fingerprint_rows)
    aliases = tuple(alias_rows)
    candidates: list[dict[str, Any]] = []

    names_by_id, name_sources_by_id = _names_by_id(metadata, sequences)
    pairs = _paired_fingerprints(fingerprints)
    pairs_by_id = _group_values(pairs, "antibody_id", "pair_fingerprint")
    records_by_id = _group_values(pairs, "antibody_id", "source_record_id")

    for antibody_id, pair_values in pairs_by_id.items():
        if len(pair_values) > 1:
            candidates.append(
                _candidate(
                    "same_id_different_pairs",
                    antibody_ids=[antibody_id],
                    source_record_ids=records_by_id[antibody_id],
                    pair_fingerprints=pair_values,
                    names=names_by_id.get(antibody_id, ()),
                    evidence_key=antibody_id,
                )
            )

    ids_by_pair = _group_values(pairs, "pair_fingerprint", "antibody_id")
    records_by_pair = _group_values(
        pairs, "pair_fingerprint", "source_record_id"
    )
    for pair_fingerprint, antibody_ids in ids_by_pair.items():
        if len(antibody_ids) > 1:
            candidates.append(
                _candidate(
                    "different_ids_same_pair",
                    antibody_ids=antibody_ids,
                    source_record_ids=records_by_pair[pair_fingerprint],
                    pair_fingerprints=[pair_fingerprint],
                    names=_names_for_ids(names_by_id, antibody_ids),
                    evidence_key=pair_fingerprint,
                )
            )

    ids_by_name, name_sources = _ids_by_normalized_name(metadata, sequences)
    for normalized_name, antibody_ids in ids_by_name.items():
        if len(antibody_ids) < 2:
            continue
        pair_values = _values_for_ids(pairs_by_id, antibody_ids)
        if len(pair_values) > 1:
            candidates.append(
                _candidate(
                    "same_normalized_name_different_pairs",
                    antibody_ids=antibody_ids,
                    source_record_ids=name_sources[normalized_name]
                    + _values_for_ids(records_by_id, antibody_ids),
                    pair_fingerprints=pair_values,
                    names=_names_for_ids(names_by_id, antibody_ids),
                    evidence_key=normalized_name,
                )
            )

    for pair_fingerprint, antibody_ids in ids_by_pair.items():
        normalized_names = {
            normalize_name(name)
            for name in _names_for_ids(names_by_id, antibody_ids)
            if normalize_name(name)
        }
        if len(antibody_ids) == 1 and len(normalized_names) > 1:
            candidates.append(
                _candidate(
                    "different_names_same_pair",
                    antibody_ids=antibody_ids,
                    source_record_ids=records_by_pair[pair_fingerprint]
                    + _values_for_ids(name_sources_by_id, antibody_ids),
                    pair_fingerprints=[pair_fingerprint],
                    names=_names_for_ids(names_by_id, antibody_ids),
                    evidence_key=pair_fingerprint,
                )
            )

    alias_groups = _alias_groups(aliases)
    for normalized_alias, group in alias_groups.items():
        antibody_ids = group["antibody_ids"]
        if len(antibody_ids) > 1:
            candidates.append(
                _candidate(
                    "alias_across_ids",
                    antibody_ids=antibody_ids,
                    source_record_ids=group["source_record_ids"],
                    pair_fingerprints=(),
                    names=group["names"],
                    evidence_key=normalized_alias,
                )
            )

    metadata_ids = _antibody_ids(metadata)
    sequence_ids = _antibody_ids(sequences)

    for root_name, group in _parent_mutant_groups(metadata, sequences).items():
        if len(group["antibody_ids"]) > 1:
            candidates.append(
                _candidate(
                    "parent_mutant_candidate",
                    antibody_ids=group["antibody_ids"],
                    source_record_ids=group["source_record_ids"],
                    pair_fingerprints=_values_for_ids(
                        pairs_by_id, group["antibody_ids"]
                    ),
                    names=group["names"],
                    evidence_key=root_name,
                )
            )

    category_index = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    candidates.sort(
        key=lambda row: (category_index[row["category"]], row["candidate_id"])
    )
    review_rows = tuple(_review_row(row) for row in candidates)
    coverage_rows = _coverage_rows(metadata, sequences)
    relationship_groups, relationship_members = _relationship_rows(
        metadata,
        sequences,
        pairs,
        names_by_id,
        alias_rows=aliases,
    )
    return IdentityOutputs(
        mapping_candidates=tuple(candidates),
        review_queue=review_rows,
        coverage_status=coverage_rows,
        relationship_groups=relationship_groups,
        relationship_members=relationship_members,
    )


def _coverage_rows(
    metadata_rows: tuple[Mapping[str, Any], ...],
    sequence_rows: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    metadata_ids = _antibody_ids(metadata_rows)
    sequence_ids = _antibody_ids(sequence_rows)
    output: list[dict[str, Any]] = []
    for antibody_id in sorted(metadata_ids | sequence_ids):
        has_metadata = antibody_id in metadata_ids
        has_sequence = antibody_id in sequence_ids
        if has_metadata and has_sequence:
            status = "metadata_and_sequence"
            notes = "Metadata and sequence records are both available."
        elif has_sequence:
            status = "sequence_only"
            notes = (
                "Sequence record is available; metadata is not available in "
                "the current release."
            )
        elif has_metadata:
            status = "metadata_only"
            notes = (
                "Metadata record is available; sequence is not available in "
                "the current release."
            )
        else:
            status = "unresolved"
            notes = "Identity coverage could not be resolved."
        sequence_record_ids = _record_ids_for_id(sequence_rows, antibody_id)
        source_record_ids = _sorted_unique(
            _record_ids_for_id(metadata_rows, antibody_id)
            + sequence_record_ids
        )
        output.append(
            {
                "record_id": stable_content_id(
                    "IDCOV", [antibody_id, status]
                ),
                "antibody_id": antibody_id,
                "metadata_available": str(has_metadata).lower(),
                "sequence_available": str(has_sequence).lower(),
                "coverage_status": status,
                "sequence_record_id": "|".join(sequence_record_ids),
                "notes": notes,
                "source_record_ids": "|".join(source_record_ids),
            }
        )
    return tuple(output)


def _relationship_rows(
    metadata_rows: tuple[Mapping[str, Any], ...],
    sequence_rows: tuple[Mapping[str, Any], ...],
    pair_rows: tuple[dict[str, str], ...],
    names_by_id: Mapping[str, Iterable[str]],
    alias_rows: tuple[Mapping[str, Any], ...],
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
]:
    ids_by_pair = _group_values(
        pair_rows, "pair_fingerprint", "antibody_id"
    )
    sequence_records_by_id = _group_values(
        pair_rows, "antibody_id", "source_record_id"
    )
    metadata_ids = _antibody_ids(metadata_rows)
    metadata_by_id: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(
        list
    )
    for row in metadata_rows:
        antibody_id = _non_blank(row.get("antibody_id"))
        if antibody_id:
            metadata_by_id[antibody_id].append(row)
    aliases_by_id: defaultdict[str, set[str]] = defaultdict(set)
    for row in alias_rows:
        antibody_id = _non_blank(row.get("antibody_id"))
        alias = normalize_name(row.get("alias"))
        if antibody_id and alias:
            aliases_by_id[antibody_id].add(alias)

    groups: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    for pair_fingerprint, antibody_ids in ids_by_pair.items():
        if len(antibody_ids) < 2:
            continue
        group_id = stable_content_id(
            "IDREL", ["PAIRED_VH_VL", pair_fingerprint]
        )
        names = _names_for_ids(names_by_id, antibody_ids)
        formats = _metadata_values(
            metadata_by_id, antibody_ids, ("Format", "format")
        )
        targets = _metadata_values(
            metadata_by_id, antibody_ids, ("Target", "target")
        )
        stages = _metadata_values(
            metadata_by_id,
            antibody_ids,
            (
                "Highest clinical stage",
                "highest_clinical_stage",
                "development_stage",
                "clinical_stage",
            ),
        )
        relationship_class = _classify_relationship(
            antibody_ids,
            names_by_id,
            formats,
            targets,
            stages,
            metadata_ids,
            aliases_by_id,
        )
        priority = (
            "LOW"
            if relationship_class
            in {"same_name_same_pair", "exact_alias_candidate"}
            else "HIGH"
            if relationship_class
            in {
                "same_variable_domain_distinct_product",
                "different_name_same_pair_different_context",
                "unresolved",
            }
            else "MEDIUM"
        )
        source_record_ids = _sorted_unique(
            _values_for_ids(sequence_records_by_id, antibody_ids)
            + tuple(
                record_id
                for antibody_id in antibody_ids
                for record_id in _record_ids_for_id(
                    metadata_rows, antibody_id
                )
            )
        )
        groups.append(
            {
                "record_id": group_id,
                "relationship_group_id": group_id,
                "paired_vh_vl_fingerprint": pair_fingerprint,
                "member_count": len(antibody_ids),
                "distinct_antibody_id_count": len(antibody_ids),
                "distinct_name_count": len(
                    {normalize_name(name) for name in names if name}
                ),
                "distinct_format_count": len(formats),
                "distinct_target_count": len(targets),
                "relationship_class": relationship_class,
                "review_priority": priority,
                "reviewer_decision": "",
                "reviewer": "",
                "review_date": "",
                "review_notes": "",
                "source_record_ids": "|".join(source_record_ids),
            }
        )
        for antibody_id in antibody_ids:
            member_sources = _sorted_unique(
                sequence_records_by_id.get(antibody_id, ())
                + _record_ids_for_id(metadata_rows, antibody_id)
            )
            members.append(
                {
                    "record_id": stable_content_id(
                        "IDRELMEM", [group_id, antibody_id]
                    ),
                    "relationship_group_id": group_id,
                    "paired_vh_vl_fingerprint": pair_fingerprint,
                    "antibody_id": antibody_id,
                    "antibody_names": "|".join(
                        _sorted_unique(names_by_id.get(antibody_id, ()))
                    ),
                    "formats": "|".join(
                        _metadata_values(
                            metadata_by_id, (antibody_id,), ("Format", "format")
                        )
                    ),
                    "targets": "|".join(
                        _metadata_values(
                            metadata_by_id, (antibody_id,), ("Target", "target")
                        )
                    ),
                    "development_stages": "|".join(
                        _metadata_values(
                            metadata_by_id,
                            (antibody_id,),
                            (
                                "Highest clinical stage",
                                "highest_clinical_stage",
                                "development_stage",
                                "clinical_stage",
                            ),
                        )
                    ),
                    "metadata_available": str(
                        antibody_id in metadata_ids
                    ).lower(),
                    "sequence_record_ids": "|".join(
                        sequence_records_by_id.get(antibody_id, ())
                    ),
                    "source_record_ids": "|".join(member_sources),
                }
            )
    groups.sort(key=lambda row: row["relationship_group_id"])
    members.sort(
        key=lambda row: (
            row["relationship_group_id"],
            row["antibody_id"],
        )
    )
    return tuple(groups), tuple(members)


def _metadata_values(
    rows_by_id: Mapping[str, Iterable[Mapping[str, Any]]],
    antibody_ids: Iterable[str],
    fields: tuple[str, ...],
) -> tuple[str, ...]:
    return _sorted_unique(
        row.get(field)
        for antibody_id in antibody_ids
        for row in rows_by_id.get(antibody_id, ())
        for field in fields
        if _non_blank(row.get(field))
    )


def _classify_relationship(
    antibody_ids: tuple[str, ...],
    names_by_id: Mapping[str, Iterable[str]],
    formats: tuple[str, ...],
    targets: tuple[str, ...],
    stages: tuple[str, ...],
    metadata_ids: set[str],
    aliases_by_id: Mapping[str, set[str]],
) -> str:
    if any(value not in metadata_ids for value in antibody_ids) and any(
        value in metadata_ids for value in antibody_ids
    ):
        return "metadata_to_sequence_only_link"
    normalized_names = {
        normalize_name(name)
        for name in _names_for_ids(names_by_id, antibody_ids)
        if normalize_name(name)
    }
    if len(normalized_names) <= 1:
        return "same_name_same_pair"
    for antibody_id in antibody_ids:
        other_names = {
            normalize_name(name)
            for other_id in antibody_ids
            if other_id != antibody_id
            for name in names_by_id.get(other_id, ())
        }
        if aliases_by_id.get(antibody_id, set()) & other_names:
            return "exact_alias_candidate"
    roots = {
        _parent_mutant_root(name)[0]
        for name in _names_for_ids(names_by_id, antibody_ids)
        if _parent_mutant_root(name)[0]
    }
    if len(roots) == 1 and any(
        _parent_mutant_root(name)[1]
        for name in _names_for_ids(names_by_id, antibody_ids)
    ):
        return "parent_derivative_candidate"
    if len(formats) > 1:
        return "same_variable_domain_distinct_format"
    if len(targets) > 1 or len(stages) > 1:
        return "same_variable_domain_distinct_product"
    if len(formats) <= 1 and len(targets) <= 1 and len(stages) <= 1:
        return "different_name_same_pair_same_context"
    if formats or targets or stages:
        return "different_name_same_pair_different_context"
    return "unresolved"


def _candidate(
    category: str,
    antibody_ids: Iterable[str],
    source_record_ids: Iterable[str],
    pair_fingerprints: Iterable[str],
    names: Iterable[str],
    evidence_key: str,
) -> dict[str, Any]:
    ids = _sorted_unique(antibody_ids)
    records = _sorted_unique(source_record_ids)
    pairs = _sorted_unique(pair_fingerprints)
    source_names = _sorted_unique(names)
    candidate_id = stable_content_id(
        "IDCAND",
        [
            category,
            evidence_key,
            "\x1e".join(ids),
            "\x1e".join(records),
            "\x1e".join(pairs),
            "\x1e".join(source_names),
        ],
    )
    return {
        "record_id": candidate_id,
        "candidate_id": candidate_id,
        "category": category,
        "antibody_ids": "|".join(ids),
        "source_record_ids": "|".join(records),
        "pair_fingerprints": "|".join(pairs),
        "names": "|".join(source_names),
        "evidence_key": evidence_key,
        "suggested_action": "manual_review_no_automatic_merge",
        "proposed_antibody_id": "",
    }


def _review_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    ids = _split_pipe(candidate.get("antibody_ids"))
    names = _split_pipe(candidate.get("names"))
    records = tuple(
        value
        for value in _split_pipe(candidate.get("source_record_ids"))
        if value.startswith("SEQ:")
    )
    pairs = _split_pipe(candidate.get("pair_fingerprints"))
    single_id = len(ids) == 1
    shared_pair = pairs[0] if len(ids) >= 2 and len(pairs) == 1 else ""
    review_id = stable_content_id(
        "IDREVIEW",
        [candidate["candidate_id"], candidate["category"]],
    )
    return {
        "record_id": review_id,
        "review_id": review_id,
        "candidate_id": candidate["candidate_id"],
        "conflict_type": candidate["category"],
        "category": candidate["category"],
        "antibody_id_a": _at(ids, 0),
        "antibody_id_b": _at(ids, 1),
        "name_a": _at(names, 0) if single_id else "",
        "name_b": _at(names, 1) if single_id else "",
        "sequence_record_id_a": _at(records, 0) if single_id else "",
        "sequence_record_id_b": _at(records, 1) if single_id else "",
        "paired_fingerprint_a": (
            _at(pairs, 0) if single_id else shared_pair
        ),
        "paired_fingerprint_b": (
            _at(pairs, 1) if single_id else shared_pair
        ),
        "current_relationship": candidate["category"],
        "suggested_action": candidate["suggested_action"],
        "evidence_summary": (
            f"IDs={candidate['antibody_ids']}; "
            f"names={candidate['names']}; "
            f"paired_fingerprints={candidate['pair_fingerprints']}"
        ),
        "source_record_ids": candidate["source_record_ids"],
        "status": "PENDING_REVIEW",
        "reviewer": "",
        "reviewer_decision": "",
        "review_date": "",
        "reviewer_notes": "",
        "review_notes": "",
    }


def _split_pipe(value: Any) -> tuple[str, ...]:
    text = serialize_scalar(value)
    return tuple(part for part in text.split("|") if part)


def _at(values: tuple[str, ...], index: int) -> str:
    return values[index] if index < len(values) else ""


def _paired_fingerprints(
    fingerprint_rows: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, str], ...]:
    paired: list[dict[str, str]] = []
    for row in fingerprint_rows:
        pair_fingerprint = _non_blank(
            row.get("pair_sha256", row.get("pair_fingerprint"))
        )
        antibody_id = _non_blank(row.get("antibody_id"))
        if not pair_fingerprint or not antibody_id:
            continue
        paired.append(
            {
                "antibody_id": antibody_id,
                "pair_fingerprint": pair_fingerprint,
                "source_record_id": _non_blank(
                    row.get("sequence_record_id", row.get("record_id"))
                ),
            }
        )
    return tuple(paired)


def _names_by_id(
    metadata_rows: tuple[Mapping[str, Any], ...],
    sequence_rows: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    names: defaultdict[str, set[str]] = defaultdict(set)
    sources: defaultdict[str, set[str]] = defaultdict(set)
    for row in metadata_rows + sequence_rows:
        antibody_id = _non_blank(row.get("antibody_id"))
        name = _non_blank(row.get("antibody_name"))
        if not antibody_id or not name:
            continue
        names[antibody_id].add(name)
        record_id = _non_blank(row.get("record_id"))
        if record_id:
            sources[antibody_id].add(record_id)
    return (
        {key: tuple(sorted(values)) for key, values in sorted(names.items())},
        {key: tuple(sorted(values)) for key, values in sorted(sources.items())},
    )


def _ids_by_normalized_name(
    metadata_rows: tuple[Mapping[str, Any], ...],
    sequence_rows: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    ids: defaultdict[str, set[str]] = defaultdict(set)
    sources: defaultdict[str, set[str]] = defaultdict(set)
    for row in metadata_rows + sequence_rows:
        antibody_id = _non_blank(row.get("antibody_id"))
        normalized_name = normalize_name(row.get("antibody_name"))
        if not antibody_id or not normalized_name:
            continue
        ids[normalized_name].add(antibody_id)
        record_id = _non_blank(row.get("record_id"))
        if record_id:
            sources[normalized_name].add(record_id)
    return (
        {key: tuple(sorted(values)) for key, values in sorted(ids.items())},
        {key: tuple(sorted(values)) for key, values in sorted(sources.items())},
    )


def _alias_groups(
    alias_rows: tuple[Mapping[str, Any], ...],
) -> dict[str, dict[str, tuple[str, ...]]]:
    grouped: defaultdict[str, dict[str, set[str]]] = defaultdict(
        lambda: {
            "antibody_ids": set(),
            "source_record_ids": set(),
            "names": set(),
        }
    )
    for row in alias_rows:
        normalized_alias = _non_blank(row.get("normalized_alias"))
        if not normalized_alias:
            normalized_alias = normalize_name(row.get("alias"))
        antibody_id = _non_blank(row.get("antibody_id"))
        if not normalized_alias or not antibody_id:
            continue
        grouped[normalized_alias]["antibody_ids"].add(antibody_id)
        alias = _non_blank(row.get("alias"))
        if alias:
            grouped[normalized_alias]["names"].add(alias)
        source_record_id = _non_blank(
            row.get("source_record_id", row.get("record_id"))
        )
        if source_record_id:
            grouped[normalized_alias]["source_record_ids"].add(source_record_id)
    return {
        key: {
            field: tuple(sorted(values))
            for field, values in sorted(group.items())
        }
        for key, group in sorted(grouped.items())
    }


def _parent_mutant_groups(
    metadata_rows: tuple[Mapping[str, Any], ...],
    sequence_rows: tuple[Mapping[str, Any], ...],
) -> dict[str, dict[str, tuple[str, ...]]]:
    grouped: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "antibody_ids": set(),
            "source_record_ids": set(),
            "names": set(),
            "has_marker": False,
        }
    )
    for row in metadata_rows + sequence_rows:
        antibody_id = _non_blank(row.get("antibody_id"))
        name = _non_blank(row.get("antibody_name"))
        root, has_marker = _parent_mutant_root(name)
        if not antibody_id or not root:
            continue
        group = grouped[root]
        group["antibody_ids"].add(antibody_id)
        group["names"].add(name)
        group["has_marker"] = group["has_marker"] or has_marker
        record_id = _non_blank(row.get("record_id"))
        if record_id:
            group["source_record_ids"].add(record_id)
    return {
        key: {
            "antibody_ids": tuple(sorted(group["antibody_ids"])),
            "source_record_ids": tuple(sorted(group["source_record_ids"])),
            "names": tuple(sorted(group["names"])),
        }
        for key, group in sorted(grouped.items())
        if group["has_marker"] and len(group["antibody_ids"]) > 1
    }


def _parent_mutant_root(value: Any) -> tuple[str, bool]:
    normalized = normalize_name(value)
    marker = re.compile(
        r"\b(?:mutant|variant|wild[\s_-]*type|wt|parent|parental|mutein)\b"
    )
    has_marker = bool(marker.search(normalized))
    root = marker.sub(" ", normalized)
    root = re.sub(r"[^0-9a-z]+", " ", root)
    return re.sub(r"\s+", " ", root).strip(), has_marker


def _group_values(
    rows: Iterable[Mapping[str, str]],
    key_field: str,
    value_field: str,
) -> dict[str, tuple[str, ...]]:
    grouped: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        key = _non_blank(row.get(key_field))
        value = _non_blank(row.get(value_field))
        if key and value:
            grouped[key].add(value)
    return {
        key: tuple(sorted(values)) for key, values in sorted(grouped.items())
    }


def _values_for_ids(
    values_by_id: Mapping[str, Iterable[str]],
    antibody_ids: Iterable[str],
) -> tuple[str, ...]:
    values = {
        value
        for antibody_id in antibody_ids
        for value in values_by_id.get(antibody_id, ())
    }
    return tuple(sorted(values))


def _names_for_ids(
    names_by_id: Mapping[str, Iterable[str]],
    antibody_ids: Iterable[str],
) -> tuple[str, ...]:
    return _values_for_ids(names_by_id, antibody_ids)


def _antibody_ids(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        antibody_id
        for row in rows
        if (antibody_id := _non_blank(row.get("antibody_id")))
    }


def _record_ids_for_id(
    rows: Iterable[Mapping[str, Any]],
    antibody_id: str,
) -> tuple[str, ...]:
    return _sorted_unique(
        _non_blank(row.get("record_id"))
        for row in rows
        if _non_blank(row.get("antibody_id")) == antibody_id
    )


def _sorted_unique(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                serialized
                for value in values
                if (serialized := _non_blank(value))
            }
        )
    )


def _non_blank(value: Any) -> str:
    serialized = serialize_scalar(value)
    return serialized if serialized.strip() else ""
