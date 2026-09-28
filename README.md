# DOTAD 2.0 reproducible data release pipeline

DOTAD 2.0 is primarily accessed through the DOTAD web platform at https://i.uestc.edu.cn/DOTAD2.0/, which provides antibody-centered browsing, interactive developability analysis, sequence tools, statistics, visualization, and data downloads.

This repository contains the reproducible build and validation code. It is not the primary DOTAD data portal.

## Purpose

The repository provides source parsers, normalization, lineage, validation, release-generation, and deterministic packaging code.

## Data access

Real DOTAD data are not included. Use the DOTAD website for data access; the immutable article dataset is preserved at https://doi.org/10.5281/zenodo.21742780.

## Installation

Use Python 3.11, 3.12, or 3.13 and run `python -m pip install -r requirements-lock.txt`.

## Environment

Dependencies are pinned in `requirements-lock.txt`; CI tests all supported Python versions.

## Build

Run source-specific commands documented in `docs/build.md`. Authoritative files are supplied locally and are never downloaded by CI.

## Validation

Run `python -m pytest`. Tests requiring authoritative data use the `requires_authoritative_data` marker and are skipped by default.

For the September 2026 revised research archive, use `python revision_tools/validate_archive.py path/to/archive.zip`. See [revision scope](docs/revision_archive.md) for the distinction between original source reconstruction and checks of packaged revision products. No real data are added to this repository.

## Synthetic example

Run `python examples/synthetic/build.py --output build-one` twice and compare the generated SHA-256 values.

## Deterministic output

TSV serialization, member order, timestamps, and ZIP metadata are normalized for reproducible builds.

## Input integrity

Authoritative input hashes are documented in `docs/authoritative_input_hashes.md`; the workbooks are not part of this repository.

## Testing

CI runs pytest, compilation checks, the synthetic build twice, deterministic comparison, placeholder scanning, and private-path scanning.

## Code licence

MIT applies to code only. See `LICENSE`.

## Data rights

MIT does not apply to DOTAD data. DOTAD-generated fields and source-derived content have distinct rights; source values remain subject to source-specific licences and `LICENSES.tsv` in the data release.

## Citation

Use `CITATION.cff` for this software and cite the DOTAD 2.0 dataset at https://doi.org/10.5281/zenodo.21742780 plus relevant original sources when using data.

## Contributing

See `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `SECURITY.md`.

## Known limitations

Public CI uses synthetic fixtures and does not independently re-extract all literature values or redistribute authoritative inputs.
