from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCRIPT_EXTENSIONS = {
    ".py": "Python",
    ".r": "R",
    ".rmd": "R Markdown",
    ".ipynb": "Jupyter",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".sql": "SQL",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".yml": "YAML workflow/config",
    ".yaml": "YAML workflow/config",
}
DATA_REFERENCE_PATTERN = re.compile(
    r"""(?P<path>[A-Za-z0-9_./\\()\-]+\.(?:csv|tsv|txt|xlsx|xlsm|json|parquet|sqlite|db|zip|png|pdf|svg))""",
    re.IGNORECASE,
)
QUOTED_DATA_REFERENCE_PATTERN = re.compile(
    r"""(?P<quote>['"`])(?P<path>[A-Za-z0-9_./\\() \-]+\.(?:csv|tsv|txt|xlsx|xlsm|json|parquet|sqlite|db|zip|png|pdf|svg))(?P=quote)""",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")
WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/][^'\"\r\n]+")
UNIX_PATH_PATTERN = re.compile(r"(?<!https:)(?<!http:)/(?:Users|home|mnt|data|tmp)/[^'\"\r\n]+")


DEFAULT_EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "vendor",
    "audit_inputs",
    "audit_outputs",
}


def inventory_scripts(
    project_root: Path,
    include_roots: list[Path] | None = None,
) -> list[dict[str, Any]]:
    project_root = project_root.resolve()
    roots = _resolve_script_roots(project_root, include_roots)
    rows: list[dict[str, Any]] = []
    count = 0
    paths = {
        path.resolve()
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SCRIPT_EXTENSIONS
        and not any(part.lower() in DEFAULT_EXCLUDED_DIRECTORY_NAMES for part in path.parts)
    }
    for path in sorted(paths, key=lambda item: item.as_posix().lower()):
        if not path.is_file() or path.suffix.lower() not in SCRIPT_EXTENSIONS:
            continue
        count += 1
        relative = path.relative_to(project_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            read_status = "readable"
            notes = ""
        except OSError as exc:
            text = ""
            read_status = "unreadable"
            notes = str(exc)
        references = extract_data_references(text)
        inputs, outputs = classify_references(text, references)
        packages = detect_packages(text, path.suffix.lower())
        hardcoded = sorted(
            {
                value
                for value in WINDOWS_PATH_PATTERN.findall(text) + UNIX_PATH_PATTERN.findall(text)
                if not value.lower().startswith(("p://", "s://")) and "://" not in value
            }
        )
        urls = sorted(set(URL_PATTERN.findall(text)))
        seed = bool(re.search(r"(?i)(set\.seed|random\.seed|np\.random\.seed|seed\s*=)", text))
        write_source = bool(
            re.search(
                r"(?i)(to_csv|to_excel|write\.csv|write\.table|json\.dump|write_text|write_bytes|saveRDS)"
                r".{0,240}(assets/data|data/|source|raw)",
                text,
                re.DOTALL,
            )
        )
        execution_risk = "high" if write_source or any(token in text for token in ("subprocess.", "os.system(", "DROP TABLE", "DELETE FROM")) else "low"
        rows.append(
            {
                "script_id": f"SC{count:05d}",
                "relative_path": relative,
                "language": SCRIPT_EXTENSIONS[path.suffix.lower()],
                "purpose_guess": purpose_guess(relative, text),
                "input_files_detected": json.dumps(inputs, ensure_ascii=False),
                "output_files_detected": json.dumps(outputs, ensure_ascii=False),
                "packages_detected": json.dumps(packages, ensure_ascii=False),
                "hardcoded_paths": json.dumps(hardcoded, ensure_ascii=False),
                "external_urls": json.dumps(urls, ensure_ascii=False),
                "random_seed_detected": seed,
                "writes_to_source_directory": write_source,
                "execution_risk": execution_risk,
                "read_status": read_status,
                "notes": notes or "Static analysis only; script was not executed.",
            }
        )
    return rows


def extract_data_references(text: str) -> list[str]:
    references: set[str] = set()
    quoted_spans: list[tuple[int, int]] = []
    for match in QUOTED_DATA_REFERENCE_PATTERN.finditer(text):
        value = match.group("path")
        quoted_spans.append(match.span())
        if _keep_data_reference(value):
            references.add(value)
    for match in DATA_REFERENCE_PATTERN.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in quoted_spans):
            continue
        value = match.group("path")
        if _is_literal_file_reference(text, match.start(), match.end(), value):
            if _keep_data_reference(value):
                references.add(value)
    return sorted(references)


def _keep_data_reference(value: str) -> bool:
    lower = value.lower()
    return (
        lower not in {"csv.zip", ".csv.zip", "zipfile.zip"}
        and not lower.startswith("zipfile.")
        and "://" not in value
    )


def _is_literal_file_reference(
    text: str,
    start: int,
    end: int,
    value: str,
) -> bool:
    if "/" in value or "\\" in value:
        return "://" not in value
    preceding = text[start - 1] if start else ""
    following = text[end] if end < len(text) else ""
    return preceding in {"'", '"'} and following == preceding


def _resolve_script_roots(
    project_root: Path,
    include_roots: list[Path] | None,
) -> list[Path]:
    if not include_roots:
        candidates = [project_root / "scripts", project_root / "R"]
        return [path for path in candidates if path.is_dir()]
    roots: list[Path] = []
    for candidate in include_roots:
        path = candidate if candidate.is_absolute() else project_root / candidate
        path = path.resolve()
        if path != project_root and project_root not in path.parents:
            raise ValueError(f"Script root is outside project root: {path}")
        if not path.is_dir():
            raise FileNotFoundError(f"Script root does not exist: {path}")
        roots.append(path)
    return roots


def classify_references(text: str, references: list[str]) -> tuple[list[str], list[str]]:
    inputs: list[str] = []
    outputs: list[str] = []
    lines = text.splitlines()
    for reference in references:
        matching_lines = [line.lower() for line in lines if reference.lower() in line.lower()]
        is_output = any(
            re.search(r"(?i)\boutput\s*:", line)
            or re.search(r"(?i)\boutput(?:_file|_path|_json)?\b\s*=", line)
            or re.search(r"(?i)\bout(?:put)?_[a-z0-9_]*\s*=", line)
            or re.search(
                r"(?i)(?:path\.join|file\.path)\(\s*(?:figure|fig|panel|temp|output|result)[a-z0-9_]*_dir\b",
                line,
            )
            or any(
                token in line
                for token in (
                    "to_csv",
                    "to_excel",
                    "write.csv",
                    "write.table",
                    "writefile",
                    "write_text",
                    "write_bytes",
                    "json.dump",
                    "savefig",
                    "ggsave",
                    "screenshot",
                    "page.pdf",
                    "tofile",
                    "--output",
                )
            )
            for line in matching_lines
        )
        is_input = any(
            re.search(r"(?i)\binput\s*:", line)
            or any(token in line for token in ("read_csv", "read_excel", "read_text", "json.load", "--input"))
            for line in matching_lines
        )
        if is_output:
            outputs.append(reference)
        if is_input or not is_output:
            inputs.append(reference)
    return sorted(set(inputs)), sorted(set(outputs))


def detect_packages(text: str, suffix: str) -> list[str]:
    packages: set[str] = set()
    if suffix == ".py":
        packages.update(re.findall(r"(?m)^\s*(?:from|import)\s+([A-Za-z0-9_.-]+)", text))
    elif suffix in {".r", ".rmd"}:
        packages.update(re.findall(r"(?i)(?:library|require)\s*\(\s*['\"]?([^'\")]+)", text))
    elif suffix in {".js", ".ts"}:
        packages.update(re.findall(r"(?:from\s+|require\()['\"]([^'\"]+)", text))
    return sorted(packages)


def purpose_guess(relative_path: str, text: str) -> str:
    path_lower = relative_path.lower()
    lower = f"{relative_path} {text[:2000]}".lower()
    if path_lower.startswith("assets/js/"):
        return "Vendored website runtime/library"
    if "numbering" in path_lower:
        return "Build sequence numbering website export"
    if "hydrophobic" in path_lower:
        return "Build hydrophobicity website export"
    if "liabilit" in path_lower:
        return "Build sequence liability website export"
    if "scorecard" in path_lower:
        return "Build heuristic scorecard website export"
    if "statistics" in path_lower:
        return "Build website statistics aggregate"
    if "figure" in lower or "fig" in lower:
        return "Figure generation or capture"
    return "Unclassified script/config"


def dependency_edges(scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    edge_id = 0
    for script in scripts:
        inputs = _loads(script["input_files_detected"])
        outputs = _loads(script["output_files_detected"])
        for input_path in inputs:
            edge_id += 1
            rows.append(
                {
                    "edge_id": f"E{edge_id:05d}",
                    "from_node": input_path,
                    "to_node": script["relative_path"],
                    "relationship": "input_to_script",
                    "status": "partially_complete",
                    "evidence": "Static quoted-path detection",
                    "notes": "",
                }
            )
        for output_path in outputs:
            edge_id += 1
            rows.append(
                {
                    "edge_id": f"E{edge_id:05d}",
                    "from_node": script["relative_path"],
                    "to_node": output_path,
                    "relationship": "script_to_output",
                    "status": "partially_complete",
                    "evidence": "Static quoted-path detection",
                    "notes": "",
                }
            )
    return rows


def reproducibility_rows(
    scripts: list[dict[str, Any]],
    file_inventory: list[dict[str, Any]],
    project_root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = {row["relative_path"].replace("\\", "/").lower() for row in file_inventory}
    status_rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for script in scripts:
        inputs = _loads(script["input_files_detected"])
        absent = []
        for value in inputs:
            normalized = value.replace("\\", "/").lstrip("./").lower()
            exists_in_project = _dependency_exists(
                project_root,
                script["relative_path"],
                value,
            )
            if (
                normalized not in paths
                and not any(path.endswith("/" + normalized) for path in paths)
                and not exists_in_project
            ):
                absent.append(value)
                missing.append(
                    {
                        "dependency_type": "input_file",
                        "required_by": script["relative_path"],
                        "missing_item": value,
                        "severity": "blocking" if "build" in script["relative_path"].lower() else "warning",
                        "notes": (
                            "Quoted input path was not matched to an authoritative "
                            "asset or an existing path in the current project tree."
                        ),
                    }
                )
        if script["read_status"] != "readable":
            status = "ambiguous"
        elif absent:
            status = "missing_input"
        elif not inputs and not _loads(script["output_files_detected"]):
            status = "ambiguous"
        else:
            status = "partially_complete"
        status_rows.append(
            {
                "component": script["relative_path"],
                "chain_stage": purpose_guess(script["relative_path"], ""),
                "status": status,
                "inputs_detected": json.dumps(inputs, ensure_ascii=False),
                "outputs_detected": script["output_files_detected"],
                "missing_inputs_json": json.dumps(absent, ensure_ascii=False),
                "safe_to_execute_in_audit": script["execution_risk"] == "low",
                "notes": "Static assessment; archived scripts were not executed.",
            }
        )
    return status_rows, missing


def _dependency_exists(
    project_root: Path | None,
    script_relative_path: str,
    dependency: str,
) -> bool:
    if project_root is None or not dependency or re.match(r"^[a-z]+://", dependency, re.I):
        return False
    if any(character in dependency for character in "*?[]{}"):
        return False
    raw = Path(dependency)
    candidates = [raw] if raw.is_absolute() else [
        project_root / raw,
        project_root / Path(script_relative_path).parent / raw,
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists():
            return True
    return False


def dependency_graph_markdown(
    scripts: list[dict[str, Any]],
    status_rows: list[dict[str, Any]],
    project_version_note: str,
) -> str:
    lines = [
        "# Preliminary Rebuild Dependency Graph",
        "",
        project_version_note,
        "",
        "```mermaid",
        'flowchart LR',
        '  raw["Raw/source files"] --> parsed["Parsed/intermediate files"]',
        '  parsed --> harmonized["Harmonized files"]',
        '  harmonized --> release["Static release files"]',
        '  release --> benchmark["Benchmark files"]',
        '  harmonized --> figures["Figure inputs"]',
        '  figures --> manuscript["Manuscript figures and numeric claims"]',
        "```",
        "",
        "## Archived Implementations",
        "",
    ]
    if not scripts:
        lines.append("- No processing scripts were found.")
    for script in scripts:
        status = next(
            (row["status"] for row in status_rows if row["component"] == script["relative_path"]),
            "ambiguous",
        )
        lines.append(
            f"- `{script['relative_path']}` — {script['purpose_guess']} — **{status}**"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- The graph above is a target lifecycle, not evidence that every edge is implemented.",
            "- The supplied archive contains website-export builders but no complete source-to-harmonized-to-release pipeline.",
            "- Manual steps and missing scripts are reported in `reproducibility_status.tsv` and `missing_dependencies.tsv`.",
        ]
    )
    return "\n".join(lines)


def _loads(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []
