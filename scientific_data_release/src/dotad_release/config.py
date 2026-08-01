from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .workbook_reader import sha256_file


EXPECTED_CORE_INPUTS = {
    "metadata_sequences": "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx",
    "data_dictionary": "assets/data/dotad_data_dictionary_v2.0.xlsx",
    "experimental": "assets/data/dotad_experimental_developability_v2.0.xlsx",
    "literature": "assets/data/dotad_literature_developability_collection_v2.0.xlsx",
}


@dataclass(frozen=True)
class ReleaseConfig:
    config_dir: Path
    project_root: Path
    release_version: str
    core_inputs: dict[str, Path]
    input_shas: dict[str, str]
    sheet_roles: dict[str, Any]
    field_roles: dict[str, Any]

    @classmethod
    def load(
        cls, config_dir: Path, project_root: Path, release_version: str
    ) -> "ReleaseConfig":
        config_dir = Path(config_dir).resolve()
        project_root = Path(project_root).resolve()
        core_config = _read_yaml(config_dir / "core_inputs.yml")
        declared = core_config.get("core_inputs", {})
        if set(declared) != set(EXPECTED_CORE_INPUTS):
            raise ValueError(
                "core_inputs.yml must declare exactly the four authoritative inputs"
            )
        paths: dict[str, str] = {}
        expected_shas: dict[str, str] = {}
        for key, value in declared.items():
            if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
                raise ValueError(
                    f"Core input {key} must declare exactly path and sha256"
                )
            paths[key] = str(value["path"])
            expected_shas[key] = str(value["sha256"]).lower()
        if paths != EXPECTED_CORE_INPUTS:
            raise ValueError(
                "core_inputs.yml paths must match the four authoritative inputs"
            )
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in expected_shas.values()
        ):
            raise ValueError("Every authoritative input SHA-256 must be 64 hex digits")
        resolved = {
            key: (project_root / relative_path).resolve()
            for key, relative_path in paths.items()
        }
        missing = [str(path) for path in resolved.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError("Missing core inputs: " + ", ".join(missing))
        actual_shas = {key: sha256_file(path) for key, path in resolved.items()}
        mismatches = [
            f"{key}: expected {expected_shas[key]}, got {actual_shas[key]}"
            for key in resolved
            if actual_shas[key] != expected_shas[key]
        ]
        if mismatches:
            raise ValueError(
                "Authoritative input hash mismatch: " + "; ".join(mismatches)
            )
        return cls(
            config_dir=config_dir,
            project_root=project_root,
            release_version=release_version,
            core_inputs=resolved,
            input_shas=actual_shas,
            sheet_roles=_read_yaml(config_dir / "sheet_roles.yml"),
            field_roles=_read_yaml(config_dir / "field_roles.yml"),
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected mapping in {path}")
    return value
