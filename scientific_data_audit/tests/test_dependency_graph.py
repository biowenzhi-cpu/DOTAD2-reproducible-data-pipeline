from __future__ import annotations

import json
from pathlib import Path

from dotad_audit.dependency_graph import _dependency_exists, inventory_scripts


def test_dependency_exists_checks_project_root_and_script_directory(
    tmp_path: Path,
) -> None:
    root_input = tmp_path / "assets" / "data" / "input.csv"
    root_input.parent.mkdir(parents=True)
    root_input.write_text("x\n1\n", encoding="utf-8")
    script_input = tmp_path / "scripts" / "fixtures" / "local.csv"
    script_input.parent.mkdir(parents=True)
    script_input.write_text("x\n1\n", encoding="utf-8")

    assert _dependency_exists(
        tmp_path,
        "scripts/build.py",
        "assets/data/input.csv",
    )
    assert _dependency_exists(
        tmp_path,
        "scripts/build.py",
        "fixtures/local.csv",
    )
    assert not _dependency_exists(
        tmp_path,
        "scripts/build.py",
        "missing.csv",
    )


def test_script_inventory_ignores_callable_file_like_expressions(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "capture.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "\n".join(
            [
                'const FIGURES_DIR = path.join(ROOT, "figures");',
                "const OUTPUT = {",
                '  pdf: path.join(FIGURES_DIR, "Figure (final).pdf"),',
                "};",
                "const PANEL_OUTPUT = {",
                '  A: path.join(FIGURES_DIR, "Fig5A_query_antibody.png"),',
                "};",
                "await page.pdf({ path: OUTPUT.pdf });",
                "console.log(OUTPUT.pdf);",
            ]
        ),
        encoding="utf-8",
    )

    rows = inventory_scripts(tmp_path, [tmp_path / "scripts"])
    detected = set(json.loads(rows[0]["input_files_detected"]))
    detected.update(json.loads(rows[0]["output_files_detected"]))
    inputs = set(json.loads(rows[0]["input_files_detected"]))
    outputs = set(json.loads(rows[0]["output_files_detected"]))

    assert "Figure (final).pdf" in detected
    assert "Fig5A_query_antibody.png" in detected
    assert "Figure (final).pdf" in outputs
    assert "Fig5A_query_antibody.png" in outputs
    assert "Figure (final).pdf" not in inputs
    assert "Fig5A_query_antibody.png" not in inputs
    assert "page.pdf" not in detected
    assert "console.log(OUTPUT.pdf" not in detected
    assert "OUTPUT.pdf" not in detected
