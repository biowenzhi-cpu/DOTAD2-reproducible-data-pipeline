# Revised research archive validation

The research archive associated with https://doi.org/10.5281/zenodo.21742780 contains a revised source-preserving release and manuscript-associated analysis derivatives. Website records, historical manuscript-analysis matrices and source-stratified benchmark tasks have different scopes; their counts are not interchangeable.

## Using the validator

Download the author-approved revised ZIP separately and run:

```sh
python revision_tools/validate_archive.py path/to/archive.zip --output validation.json
```

Use ordinary Python, not optimization mode (`-O`): the validator uses assertions. Python 3.11-3.13 and only the standard library are required. This validator is version-specific; it checks declared counts and hashes of approved artifacts, not arbitrary future releases. A copy is also included in the archive itself.

Checks include ZIP integrity and path safety, exact manifest coverage, SHA-256, tabular keys, source identifier coverage, retained affinity payload identity, revised Figure 4 identity, source-specific benchmark counts and sequence-group split separation. Optional `--baseline path/to/previous.zip` verifies the documented file-change ledger and preservation of protected scientific payloads against the preceding revision candidate.

The September revision removes the inadequately documented Garbinski subset, preserves an explicit identifier migration, adds source evidence at the available resolution, and retains the two source-qualified HIC corrections. The final packaging step does not recompute figures, benchmark labels or quantitative matrices. Original construction-input hashes in `authoritative_input_hashes.md` remain historical build pins; they have not been silently replaced with revision-workbook hashes.

## Benchmark download status

As verified on 9 October 2026, the default benchmark download at https://i.uestc.edu.cn/DOTAD2.0/Download.html provides the revised source-stratified package: nine source/endpoint tasks and 2,028 continuous labels. Its `labels.tsv` and `coverage.tsv` match the corresponding products in the research archive. The former pooled v1.1 package is retained as a historical product for reproducibility and audit, rather than as the recommended default.

The benchmark README in the originally deposited ZIP retained the obsolete candidate-stage statement "Not deployed and not a silent replacement of v1.1." This sentence does not describe the current website deployment. A documentation-only correction replaces the deployment statement and the candidate heading; benchmark labels, task coverage and sequence-group splits remain unchanged. The code repository contains validation code and documentation; the data package is distributed through the website and Zenodo.

## Scope and limits

This is package validation, not a complete rerun of the revision overlay from primary literature or an independent human extraction audit. The original construction pipeline remains documented in `build.md`; earlier manuscript-analysis scripts can require frozen workbooks or prior-stage products not distributed in this code repository. Do not interpret successful public synthetic tests as verification of every primary-source value.

Metadata and sequence curation-route attribution does not reconstruct missing historical field-specific accessions. Source URLs identify inputs at the evidential resolution documented by each layer. Historical analysis matrices remain historical derivatives, not additional primary records.

## Affinity sources and rights

The companion follows the upstream FLAb distribution. Its evidence registry cites the FLAb2 record https://doi.org/10.5281/zenodo.21580361 and distinguishes that record's collection-level CC BY 4.0 declaration from per-study declarations. Six studies have no separately specified study-level terms in the checked upstream documentation; this is explicitly recorded rather than described as individual author permission. Existing noncommercial, share-alike and other source-specific conditions are not overwritten. Consult the archive's `LICENSES.tsv` and `LICENSES_AND_ATTRIBUTION.md`.

MIT applies only to this repository's code, not to the research archive or third-party data. The validator is not a legal clearance certificate.
