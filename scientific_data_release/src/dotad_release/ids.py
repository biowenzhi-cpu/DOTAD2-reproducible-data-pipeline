from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence


def normalize_identifier_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().lower()
    normalized = re.sub(r"[^0-9a-z]+", "_", normalized)
    return normalized.strip("_") or "unnamed"


def stable_source_record_id(prefix: str, sheet_name: str, excel_row: int) -> str:
    if excel_row < 1:
        raise ValueError("excel_row must be a positive physical Excel row")
    token = normalize_identifier_token(sheet_name)
    return f"{prefix.upper()}:{token}:{excel_row}"


def stable_content_id(prefix: str, parts: Sequence[str]) -> str:
    framed = "\x1f".join(unicodedata.normalize("NFKC", str(part)) for part in parts)
    digest = hashlib.sha256(framed.encode("utf-8")).hexdigest()[:24]
    return f"{prefix.upper()}:{digest}"
