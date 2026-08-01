from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".ods"}
DELIMITED_EXTENSIONS = {".csv", ".tsv", ".txt"}
SEQUENCE_EXTENSIONS = {".fasta", ".fa", ".faa", ".fna", ".fastq"}
SCRIPT_EXTENSIONS = {".py", ".r", ".rmd", ".ipynb", ".js", ".ts", ".sql", ".sh", ".ps1"}
DOCUMENTATION_EXTENSIONS = {".md", ".rst", ".txt", ".docx", ".pdf"}
FIGURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".pdf"}
DATABASE_EXTENSIONS = {".sqlite", ".sqlite3", ".db", ".parquet"}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_category_rules(path: Path) -> dict[str, list[re.Pattern[str]]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        category: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        for category, patterns in raw.get("rules", {}).items()
    }


def category_guess(relative_path: str, rules: dict[str, list[re.Pattern[str]]]) -> str:
    normalized = relative_path.replace("\\", "/")
    for category, patterns in rules.items():
        if any(pattern.search(normalized) for pattern in patterns):
            return category
    return "unknown"


def is_probably_binary(path: Path, sample_size: int = 8192) -> bool:
    extension = path.suffix.lower()
    if extension in SPREADSHEET_EXTENSIONS | ARCHIVE_EXTENSIONS | FIGURE_EXTENSIONS | DATABASE_EXTENSIONS:
        return True
    try:
        sample = path.read_bytes()[:sample_size]
    except OSError:
        return True
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    text_bytes = bytes(range(32, 127)) + b"\b\f\n\r\t"
    non_text = sample.translate(None, text_bytes)
    return len(non_text) / len(sample) > 0.30


def inventory_tree(
    root: Path,
    category_rules: dict[str, list[re.Pattern[str]]],
    *,
    exclude_roots: Iterable[Path] = (),
) -> list[dict[str, Any]]:
    root = root.resolve()
    excludes = [path.resolve() for path in exclude_roots]
    file_paths = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix().lower(),
    )
    file_paths = [
        path
        for path in file_paths
        if not any(
            path.resolve() == item or item in path.resolve().parents
            for item in excludes
        )
    ]
    return inventory_paths(root, file_paths, category_rules)


def inventory_selected_files(
    root: Path,
    selected_paths: Iterable[Path],
    category_rules: dict[str, list[re.Pattern[str]]],
) -> list[dict[str, Any]]:
    """Inventory an explicit, authoritative file set under ``root``.

    The selected paths are resolved and validated before any content is read.
    This prevents unrelated website assets or historical releases from silently
    entering a scoped data audit.
    """

    root = root.resolve()
    resolved: list[Path] = []
    for candidate in selected_paths:
        path = candidate if candidate.is_absolute() else root / candidate
        path = path.resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"Selected file is outside project root: {path}")
        if not path.exists():
            raise FileNotFoundError(f"Selected file does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"Selected path is not a file: {path}")
        resolved.append(path)
    unique_paths = sorted(set(resolved), key=lambda path: path.as_posix().lower())
    return inventory_paths(root, unique_paths, category_rules)


def inventory_paths(
    root: Path,
    file_paths: Iterable[Path],
    category_rules: dict[str, list[re.Pattern[str]]],
) -> list[dict[str, Any]]:
    root = root.resolve()
    rows: list[dict[str, Any]] = []
    file_id = 0
    for path in file_paths:
        path = path.resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"Inventory path is outside project root: {path}")
        file_id += 1
        relative = path.relative_to(root).as_posix()
        extension = path.suffix.lower()
        notes: list[str] = []
        try:
            stat = path.stat()
            checksum = sha256_file(path)
            read_status = "readable"
            size = stat.st_size
            modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        except OSError as exc:
            checksum = ""
            read_status = "unreadable"
            size = 0
            modified = ""
            notes.append(str(exc))
        mime_guess = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rows.append(
            {
                "file_id": f"F{file_id:06d}",
                "relative_path": relative,
                "parent_directory": path.parent.relative_to(root).as_posix(),
                "filename": path.name,
                "extension": extension,
                "size_bytes": size,
                "modified_time": modified,
                "sha256": checksum,
                "mime_or_type_guess": mime_guess,
                "category_guess": category_guess(relative, category_rules),
                "is_hidden": any(part.startswith(".") for part in path.relative_to(root).parts),
                "is_binary": is_probably_binary(path) if read_status == "readable" else "",
                "is_archive": extension in ARCHIVE_EXTENSIONS,
                "is_spreadsheet": extension in SPREADSHEET_EXTENSIONS,
                "is_delimited_text": extension in DELIMITED_EXTENSIONS,
                "is_sequence_file": extension in SEQUENCE_EXTENSIONS,
                "is_script": extension in SCRIPT_EXTENSIONS,
                "is_documentation": extension in DOCUMENTATION_EXTENSIONS,
                "is_figure": extension in FIGURE_EXTENSIONS,
                "is_database": extension in DATABASE_EXTENSIONS,
                "read_status": read_status,
                "notes": "; ".join(notes),
            }
        )
    return rows


def duplicate_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    version_pattern = re.compile(
        r"(?i)(?:[_\-. ](?:v(?:ersion)?\s*)?\d+(?:\.\d+)*|[_\-. ](?:final|latest|old|copy|\(\d+\)))"
    )
    by_version_stem: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["sha256"]:
            by_hash[row["sha256"]].append(row)
        by_name[row["filename"].lower()].append(row)
        stem = version_pattern.sub("", Path(row["filename"]).stem).strip(" _-.").lower()
        by_version_stem[f"{stem}{Path(row['filename']).suffix.lower()}"].append(row)

    group_number = 0
    seen: set[tuple[str, tuple[str, ...]]] = set()

    def add_group(kind: str, key: str, members: list[dict[str, Any]], notes: str = "") -> None:
        nonlocal group_number
        paths = tuple(sorted(member["relative_path"] for member in members))
        marker = (kind, paths)
        if len(members) < 2 or marker in seen:
            return
        seen.add(marker)
        group_number += 1
        hashes = sorted({member["sha256"] for member in members if member["sha256"]})
        output.append(
            {
                "group_id": f"DG{group_number:05d}",
                "relationship_type": kind,
                "group_key": key,
                "file_count": len(members),
                "relative_paths_json": _json(paths),
                "sha256_values_json": _json(hashes),
                "same_content": len(hashes) == 1,
                "notes": notes,
            }
        )

    for checksum, members in sorted(by_hash.items()):
        add_group("exact_duplicate", checksum, members)
    for filename, members in sorted(by_name.items()):
        if len({member["sha256"] for member in members}) > 1:
            add_group("same_name_different_content", filename, members)
    for stem, members in sorted(by_version_stem.items()):
        names = {member["filename"].lower() for member in members}
        if len(names) > 1:
            add_group(
                "suspected_version_family",
                stem,
                members,
                "Filename-based relationship only; no files were removed or preferred automatically.",
            )
    return output


def extension_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter(row["extension"] or "[no extension]" for row in rows)
    sizes: Counter[str] = Counter()
    for row in rows:
        sizes[row["extension"] or "[no extension]"] += int(row["size_bytes"] or 0)
    return [
        {"extension": ext, "file_count": counts[ext], "total_size_bytes": sizes[ext]}
        for ext in sorted(counts)
    ]


def largest_files(rows: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: int(row["size_bytes"] or 0), reverse=True)[:limit]


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
