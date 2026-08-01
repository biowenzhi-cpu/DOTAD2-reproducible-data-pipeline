from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dotad_release.config import EXPECTED_CORE_INPUTS, ReleaseConfig
from dotad_release.workbook_reader import sha256_file


def _write_config(config_dir: Path, project_root: Path) -> None:
    config_dir.mkdir(parents=True)
    core = {}
    for key, relative in EXPECTED_CORE_INPUTS.items():
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture-{key}".encode())
        core[key] = {"path": relative, "sha256": sha256_file(path)}
    (config_dir / "core_inputs.yml").write_text(
        yaml.safe_dump({"core_inputs": core}, sort_keys=False),
        encoding="utf-8",
    )
    (config_dir / "sheet_roles.yml").write_text("{}\n", encoding="utf-8")
    (config_dir / "field_roles.yml").write_text("{}\n", encoding="utf-8")


def test_config_requires_exact_four_paths_and_matching_hashes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config_dir = tmp_path / "config"
    _write_config(config_dir, project)
    config = ReleaseConfig.load(config_dir, project, "v2.0.0")
    assert set(config.core_inputs) == set(EXPECTED_CORE_INPUTS)
    assert config.input_shas["metadata_sequences"] == sha256_file(
        config.core_inputs["metadata_sequences"]
    )
    config.core_inputs["metadata_sequences"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        ReleaseConfig.load(config_dir, project, "v2.0.0")
