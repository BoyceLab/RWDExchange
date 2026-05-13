# Configuration

All configuration lives in constants near the top of `run_pipeline.py`. To
change behavior, edit the file -- there's no separate config file.

## Paths

```python
BASE_DIR  = '/path/to/working/directory/'
CCDA_CSV  = os.path.join(BASE_DIR, 'CCDA and FHIR data/ccda_chunks.csv')
FHIR_CSV  = os.path.join(BASE_DIR, 'CCDA and FHIR data/fhir_chunks.csv')
CCDA_DIR  = os.path.join(BASE_DIR, 'ccda_assembled/')
FHIR_DIR  = os.path.join(BASE_DIR, 'fhir_assembled/')
OUT_JSON  = os.path.join(BASE_DIR, 'dashboard_data.json')
OUT_XLSX  = os.path.join(BASE_DIR, 'dashboard_data.xlsx')
OUT_CSV_DIR = os.path.join(BASE_DIR, 'csv_exports/')
```

`BASE_DIR` is the only path you typically need to change. Everything else
is derived from it.

## Mapping CSV candidates

The pipeline checks several locations for `uuid_mapping.csv` and uses the
first one that exists:

```python
MAPPING_CANDIDATES = [
    os.path.join(BASE_DIR, 'uuid_mapping.csv'),
    # add more paths if your mapping lives elsewhere
]
```

The CSV must have at minimum `document_uuid` and `patient_id` columns
(in any order). Optional columns: `first_name`, `last_name`, `mrn`,
`dob`, `gender`. The pipeline auto-detects the document UUID column
under any of these names: `document_uuid`, `id`, `doc_uuid`,
`fhirdocument_id`.

## Test-patient exclusions

Two sources, both always applied:

### Hardcoded list

Near the top of `run_pipeline.py`:

```python
HARDCODED_TEST_EXCLUSIONS = [
    'name: Test Patient',
    'name_contains: training',
    'mrn_contains: 99999',
]
```

### File-based

The pipeline checks for `test_patients.txt` in `BASE_DIR`. If present,
its rules are merged with the hardcoded list. See
[Stage 6](stages/filtering.md) for the rule syntax.

## Code-system preferences

Which coding to pick when a CodeableConcept has multiple parallel codings:

```python
CODE_SYSTEM_PREF = {
    'medication':   ['rxnorm', '2.16.840.1.113883.6.88'],
    'problem':      ['snomed', '2.16.840.1.113883.6.96',
                     'icd-10', '2.16.840.1.113883.6.90'],
    'lab':          ['loinc',  '2.16.840.1.113883.6.1'],
    # ...
}
```

To prefer ICD-10 over SNOMED for problems, swap the order. The pipeline
matches by substring against both the system name (e.g. `"SNOMED-CT"`)
and the OID, so either works.

## Display-enrichment tables

`LOINC_DISPLAY` and `SNOMED_DISPLAY` are plain Python dicts. Add or
remove entries directly:

```python
LOINC_DISPLAY = {
    '8480-6':  'Systolic blood pressure',
    # add your codes here
}
```

## Section template OIDs

CCDA section template IDs:

```python
TPL = {
    'problems_section':   '2.16.840.1.113883.10.20.22.2.5',
    'medications_section':'2.16.840.1.113883.10.20.22.2.1',
    # ...
}
```

These are HL7 CDA R2 standard OIDs and rarely need changing. If your
warehouse uses non-standard template IDs, override them here.

## Output disabling

To skip Excel output (useful for very large bundles where openpyxl is
slow):

```python
# In write_outputs(), comment out or guard the xlsx block.
```

To skip CSV exports, do the same for the per-tab CSV loop. JSON output
is the canonical format and shouldn't be skipped.
