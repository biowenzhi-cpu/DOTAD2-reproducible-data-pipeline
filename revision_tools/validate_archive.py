"""Read-only standard-library checks of the final local research archive."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
from collections import Counter, defaultdict
from zipfile import ZipFile

csv.field_size_limit(100_000_000)
RELEASE_ZIP = 'release/DOTAD2_scientific_data_release_revision_candidate.zip'
AFFINITY_ZIP = 'companion/affinity/dotad_affinity_benchmark_v2.0.zip'
AFFINITY_SHA = '6a662263ea4f855c5e14f9504312a1d91f3091864ee7f95893f190b507c3cbcd'
FIGURE_SHA = '1aa68513160f73b2620e4bbe5549cf9c9a71972ca3a1663e94e55179058ae09c'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def table(data, delimiter='\t'):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''), delimiter=delimiter))


def unzip(data, strip_root=True):
    with ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        assert len(names) == len(set(names)), 'Duplicate archive paths'
        assert len(names) == len(set(n.casefold() for n in names)), 'Case-insensitive duplicate paths'
        for info in z.infolist():
            # Windows normalizes backslashes in filename; inspect the original member name.
            n = info.orig_filename
            assert not n.startswith(('/', '\\')) and '\\' not in n and ':' not in n
            assert '..' not in PurePosixPath(n).parts
            assert (info.external_attr >> 16) & 0o170000 != 0o120000, 'Symlink member'
        assert z.testzip() is None
        files = {n: z.read(n) for n in names if not n.endswith('/')}
    if strip_root:
        assert len({n.split('/')[0] for n in files}) == 1
        files = {n.split('/', 1)[1]: b for n, b in files.items()}
    return files


def verify_manifests(outer, release):
    outer_rows = table(outer['documentation/checksum_manifest.tsv'])
    assert {r['relative_path'] for r in outer_rows} == set(outer) - {'documentation/checksum_manifest.tsv'}
    for r in outer_rows:
        b = outer[r['relative_path']]
        assert len(b) == int(r['size_bytes']) and sha(b) == r['sha256'], r['relative_path']
    manifest = table(release['release_manifest.tsv'])
    assert {r['file_path'] for r in manifest} == set(release) - {'release_manifest.tsv', 'checksums_sha256.txt'}
    for r in manifest:
        b = release[r['file_path']]
        assert sha(b) == r['sha256'], r['file_path']
        if r['file_path'].endswith('.tsv'):
            data = table(b)
            assert len(data) == int(r['row_count']), r['file_path']
            pk = r['primary_key']
            if data and pk in data[0]:
                assert all(x[pk] for x in data)
                assert len({x[pk] for x in data}) == len(data), r['file_path']
    lines = release['checksums_sha256.txt'].decode().splitlines()
    checked = set()
    for line in lines:
        digest, name = line.split('  ', 1)
        assert name not in checked and sha(release[name]) == digest, name
        checked.add(name)
    assert checked == set(release) - {'checksums_sha256.txt'}
    assert outer['documentation/release_manifest.tsv'] == release['release_manifest.tsv']
    return len(outer_rows), len(manifest)


def validate(path, baseline=None):
    data = path.read_bytes()
    outer = unzip(data)
    release = unzip(outer[RELEASE_ZIP])
    assert 'README.md' in outer, 'Missing archive-entry README'
    assert 'documentation/LICENSES_AND_ATTRIBUTION.md' in outer, 'Missing layered rights statement'
    outer_n, release_n = verify_manifests(outer, release)
    registry = table(release['companion/affinity/affinity_source_registry.tsv'])
    dataset_manifest = table(release['companion/affinity/affinity_dataset_manifest.tsv'])
    assert len(registry) == len(dataset_manifest) == 85
    assert all(r['source_repository'] == 'https://github.com/Graylab/FLAb' for r in registry)
    assert all(r['upstream_collection_licence'] == 'CC-BY-4.0' for r in registry)
    assert all(r['author_intake_review'] == 'AUTHOR_REPORTED_MANUAL_REVIEW_BEFORE_INCLUSION' for r in registry)
    unspecified = [r for r in registry if r['dataset_licence'] == 'NOT_SEPARATELY_SPECIFIED_BY_UPSTREAM']
    assert len(unspecified) == 6
    assert all(r['redistribution_status'] == 'COLLECTION_DECLARATION_RECORDED_STUDY_TERMS_UNSPECIFIED' for r in unspecified)
    assert all(r['source_url'] for r in registry)
    byname = {r['dataset_name']: r for r in registry}
    assert len(byname) == 85
    for r in dataset_manifest:
        s = byname[r['dataset_name']]
        assert r['licence'] == s['dataset_licence'] and r['redistribution_status'] == s['redistribution_status']
        assert (r['physical_file_count'],r['directory_entry_count'],r['total_zip_member_count']) == ('89','2','91')
    assert sha(release[AFFINITY_ZIP]) == AFFINITY_SHA
    with ZipFile(io.BytesIO(release[AFFINITY_ZIP])) as z:
        assert len(z.infolist()) == 91 and sum(not i.is_dir() for i in z.infolist()) == 89
        for name in byname:
            assert 'binding/'+name in z.namelist() or 'binding/'+name+'.zip' in z.namelist(), name
    comparison = table(release['companion/affinity/upstream_file_verification.tsv'])
    assert len(comparison) == 88 and all(r['byte_identical'] == 'True' for r in comparison)
    evidence = json.loads(release['companion/affinity/upstream_evidence/flab_distribution_evidence.json'])
    assert len(evidence['files']) == 6 and all(r['csv_fields_identical'] and r['zip_member_crc_valid'] for r in evidence['files'])
    lit = table(release['literature/literature_curated_records.tsv'])
    exp = table(release['experimental/experimental_record_index.tsv'])
    lineage = table(release['provenance/record_lineage.tsv'])
    assert len(lit) == 10021 and all(r['doi'] and r['source_url'] for r in lit)
    assert len(exp) == 2566 and all(r['source_url'] for r in exp)
    assert len(lineage) == 118641 and all(r['input_file_sha256'] and r['input_sheet'] and r['input_excel_row'] for r in lineage)
    for rid in ['LIT:jain_et_al:75','LIT:jain_et_al:85']:
        row = next(r for r in lit if r['record_id'] == rid)
        fields = {r['header']:r['value'] for r in json.loads(row['source_payload_json'])}
        assert fields['HIC'] == 25 and 'not an exact retention time' in fields['HIC_value_qualifier']
    fig = 'analysis_derivatives/figure4/'
    assert sha(outer[fig+'Figure4_author_approved.pdf']) == FIGURE_SHA
    counts = Counter(r['Assay'] for r in table(outer[fig+'panelA_plotted_pairs.tsv']))
    assert counts == {'AC-SINS pH 7.4':133,'HIC':131,'SMAC':133,'Fab Tm vs IgG Tm1':132}
    assert len(table(outer[fig+'panelC_nearest.tsv'])) == len(table(outer[fig+'panelC_random.tsv'])) == 1220
    labels = table(outer['analysis_derivatives/benchmark_source_stratified/labels.tsv'])
    coverage = table(outer['analysis_derivatives/benchmark_source_stratified/coverage.tsv'])
    assert len(labels) == 2028 and len(coverage) == 9
    groups = defaultdict(set)
    for r in labels: groups[r['sequence_group']].add(r['split'])
    assert all(len(v) == 1 for v in groups.values())
    for r in coverage:
        task = [x for x in labels if (x['source'],x['endpoint']) == (r['source'],r['endpoint'])]
        assert len(task) == int(r['labels'])
        assert sum(bool(x['VH'] and x['VL']) for x in task) == int(r['paired_vhvl'])
        for split in ['train','valid','test']: assert sum(x['split'] == split for x in task) == int(r[split])
    private = re.compile(rb'(?:[A-Za-z]:[/\\](?:Users|downloads|temp)[/\\]|https?://[^\s"<>]+[?&](?:token|access_token|signature)=[A-Za-z0-9])',re.I)
    findings = []
    member_count = 0
    def scan(b, name):
        nonlocal member_count
        member_count += 1
        if b.startswith(b'PK\x03\x04'):
            for n,v in unzip(b,False).items(): scan(v,name+'!'+n)
        elif PurePosixPath(name).suffix.lower() in {'.csv','.tsv','.md','.json','.txt','.py','.r','.yml','.yaml','.xml','.rels'}:
            if private.search(b): findings.append(name)
        assert PurePosixPath(name).suffix.lower() not in {'.docx','.doc','.ris'}, name
    scan(data,path.name)
    assert not findings, findings
    preserved = {}
    if baseline:
        before = unzip(baseline.read_bytes())
        oldrelease = unzip(before[RELEASE_ZIP])
        audit = table(outer['documentation/archive_file_changes.tsv'])
        expected = {r['relative_path'] for r in audit}
        delta = {n for n in set(before)|set(outer) if before.get(n)!=outer.get(n)}
        excluded = {'documentation/checksum_manifest.tsv','documentation/archive_file_changes.tsv'}
        assert delta-excluded == expected, (delta-excluded)^expected
        release_delta = {n for n in set(oldrelease)|set(release) if oldrelease.get(n)!=release.get(n)}
        ra = table(release['revision/final_preparation_file_changes.tsv'])
        rexc = {'release_manifest.tsv','checksums_sha256.txt','revision/final_preparation_file_changes.tsv'}
        assert release_delta-rexc == {r['relative_path'] for r in ra}
        assert all(before[n] == outer[n] for n in before if n.startswith('analysis_derivatives/'))
        protected_prefixes = ('metadata/','sequences/','experimental/','literature/','dictionaries/','provenance/')
        for n,b in oldrelease.items():
            if n.startswith(protected_prefixes): assert release[n] == b, n
        for a,b in zip(sorted(registry,key=lambda r:r['record_id']),sorted(table(oldrelease['companion/affinity/affinity_source_registry.tsv']),key=lambda r:r['record_id'])):
            for key in ['record_id','source_record_id','dataset_name','doi','source_url','source_publication']:
                assert a[key] == b[key], (a['record_id'],key)
        preserved = {'analysis_files_unchanged':sum(n.startswith('analysis_derivatives/') for n in before),
                     'release_payload_files_unchanged':sum(n.startswith(protected_prefixes) for n in oldrelease),
                     'release_changed_files':len(release_delta),'outer_changed_files':len(delta)}
    return {'status':'PASS_LOCAL_ARCHIVE_TECHNICAL_CHECKS','archive':path.name,'sha256':sha(data),'size_bytes':len(data),
            'outer_manifest_files':outer_n,'release_manifest_files':release_n,'recursive_members_checked':member_count,
            'literature_rows':len(lit),'literature_doi_rows':sum(bool(r['doi']) for r in lit),
            'experimental_rows':len(exp),'experimental_doi_rows':sum(bool(r['source_doi']) for r in exp),
            'affinity_datasets':85,'affinity_doi_datasets':sum(bool(r['doi']) for r in registry),
            'affinity_study_terms_not_separately_specified':6,'affinity_physical_files':89,'affinity_members':91,
            'panelA_counts':dict(counts),'panelC_pairs_per_group':1220,'benchmark_labels':2028,'benchmark_tasks':9,
            'cross_split_sequence_groups':0,'private_information_findings':findings,'uploaded':False,'published':False,
            'full_source_reconstruction_rerun':False,'scope':'Integrity and documented source evidence, not independent legal clearance or editorial acceptance.',**preserved}


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('archive',type=Path)
    p.add_argument('--baseline',type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    result=validate(a.archive,a.baseline)
    rendered=json.dumps(result,indent=2)+'\n'
    if a.output:a.output.write_text(rendered,encoding='utf-8')
    print(rendered)
