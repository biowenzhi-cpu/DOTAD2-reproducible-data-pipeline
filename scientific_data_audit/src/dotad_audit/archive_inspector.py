from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


def member_security_status(info: zipfile.ZipInfo) -> tuple[str, bool, str]:
    normalized = info.filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    mode = (info.external_attr >> 16) & 0xFFFF
    is_symlink = stat.S_ISLNK(mode)
    reasons: list[str] = []
    if path.is_absolute() or normalized.startswith(("/", "\\")):
        reasons.append("absolute_path")
    if len(normalized) > 1 and normalized[1] == ":":
        reasons.append("drive_qualified_path")
    if ".." in path.parts:
        reasons.append("path_traversal")
    if is_symlink:
        reasons.append("symlink")
    return ("unsafe" if reasons else "safe", is_symlink, ",".join(reasons))


def inspect_zip(
    archive_path: Path,
    *,
    display_path: str | None = None,
    recurse_nested: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    archive_label = display_path or str(archive_path)
    with zipfile.ZipFile(archive_path) as handle:
        for info in handle.infolist():
            status, is_symlink, note = member_security_status(info)
            rows.append(
                {
                    "archive_path": archive_label,
                    "member_path": info.filename,
                    "compressed_size": info.compress_size,
                    "uncompressed_size": info.file_size,
                    "compression_type": info.compress_type,
                    "is_directory": info.is_dir(),
                    "is_symlink": is_symlink,
                    "security_status": status,
                    "extraction_status": "not_attempted",
                    "notes": note,
                }
            )
    return rows


def safe_extract_zip(
    archive_path: Path,
    destination: Path,
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = rows if rows is not None else inspect_zip(archive_path)
    by_member = {row["member_path"]: row for row in manifest}
    with zipfile.ZipFile(archive_path) as handle:
        for info in handle.infolist():
            row = by_member[info.filename]
            if row["security_status"] != "safe":
                row["extraction_status"] = "blocked"
                continue
            target = (destination / PurePosixPath(info.filename)).resolve()
            if destination != target and destination not in target.parents:
                row["security_status"] = "unsafe"
                row["extraction_status"] = "blocked"
                row["notes"] = _append(row["notes"], "resolved_path_outside_destination")
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                row["extraction_status"] = "directory_created"
                continue
            if target.exists() and target.is_file() and target.stat().st_size == info.file_size:
                try:
                    target.chmod(stat.S_IREAD)
                except OSError as exc:
                    row["notes"] = _append(row["notes"], f"readonly_attribute_failed:{exc}")
                row["extraction_status"] = "existing_read_only_reused"
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            try:
                target.chmod(stat.S_IREAD)
            except OSError as exc:
                row["notes"] = _append(row["notes"], f"readonly_attribute_failed:{exc}")
            row["extraction_status"] = "extracted_read_only"
    return manifest


def _append(existing: str, value: str) -> str:
    return "; ".join(item for item in (existing, value) if item)
