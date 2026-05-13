# Code inventory

Stage 7 of the pipeline writes `code_inventory.csv` next to `dashboard_data.json`. It lists every unique `(vocabulary, code)` pair seen anywhere in the bundle, along with how often it was referenced, how many distinct patients had it, the most-common display name observed, and which bundle categories it appeared in.

## Columns

| Column | Meaning |
| --- | --- |
| `vocabulary` | Source vocabulary as named by the pipeline (`SNOMED-CT`, `ICD-10-CM`, `RxNorm`, `LOINC`, `CVX`, `CPT-4`, `FHIR encounter class`, ...) |
| `code` | Source code value |
| `display_name` | Most-common display string seen for this code across all references |
| `n_references` | Total number of records (across all categories) referencing this code |
| `n_unique_patients` | Number of distinct patients who have at least one reference to this code |
| `source_categories` | Semicolon-separated list of bundle tabs this code appears in (e.g. `problems;diagnostic_reports`) |

## How codes are collected

For each record in the coded categories &mdash; `problems`, `medications`, `procedures`, `allergies`, `immunizations`, `labs_vitals`, `diagnostic_reports`, `document_references`, `encounters` &mdash; the pipeline walks each entry's `all_codings` array. A record with both a SNOMED concept and an ICD-10-CM translation contributes two rows to the inventory (one per vocabulary). When `all_codings` is missing, the pipeline falls back to the record's top-level `code` and `code_system` fields.

The split `labs` and `vitals` views are skipped because their rows are duplicated in the unified `labs_vitals` tab; including all three would triple-count the same observations.

## Reading the inventory

The CSV is sorted by vocabulary, then by descending reference count, then by code. A typical first look:

```python
import pandas as pd
inv = pd.read_csv('code_inventory.csv')
print(inv['vocabulary'].value_counts())               # codes per vocab
print(inv.sort_values('n_references', ascending=False).head(20))  # most-used codes
print(inv[inv['n_unique_patients'] >= 10].shape[0])   # codes hit by many patients
```

The inventory is the input to the [OMOP ETL](omop.md) and is also useful on its own &mdash; for QA passes, to spot codes whose displays are inconsistent across records, or to size a vocabulary download from Athena.
