# Downloads

Everything you need to run the pipeline locally is hosted directly from
this site. Right-click any link and pick "Save link as..." (or just click
to download).

## Pipeline runtime

The two files needed to ingest your own data and view the result:

| File | Size | Purpose |
|------|------|---------|
| [`run_pipeline.py`](assets/run_pipeline.py) | ~78 KB | The full ETL pipeline. Single-file Python module with all seven stages (six in the JAMIA manuscript plus the code-inventory output). Edit `BASE_DIR` near the top and `HARDCODED_TEST_EXCLUSIONS` if needed, then run. |
| [`dashboard.html`](assets/dashboard.html) | ~60 KB | Browser-only viewer. Open it locally, click the file picker, point it at your `dashboard_data.json`. No server required. |
| [`omop_etl.py`](assets/omop_etl.py) | ~16 KB | OMOP CDM v5.4 ETL. Reads `dashboard_data.json` plus an Athena vocabulary download and writes nine CDM tables to a folder tagged with the vocabulary release version. See [OMOP ETL](omop.md). |
| [`phenopackets_etl.py`](assets/phenopackets_etl.py) | ~30 KB | GA4GH Phenopackets v2 ETL. Reads `dashboard_data.json` plus optional `note_extractions.csv` and an Athena vocabulary directory; emits one Phenopacket JSON per patient with HPO phenotypes, Mondo diseases, LOINC measurements, RxNorm/SNOMED medical actions, and a placeholder genetic-interpretation block. Seed mappings cover ALS, epilepsy, and autoimmune disease. See [Phenopackets ETL](phenopackets.md). |
| [`mondo_omop_bridge.py`](assets/mondo_omop_bridge.py) | ~16 KB | Mondo-OMOP bridge & rare disease cohort builder. Given a Mondo term ID, walks the Mondo disease hierarchy to find every descendant and emits a code list (SNOMED CT + ICD-10-CM) defining the cohort, plus OMOP standard concept_ids if Athena is available. Includes GARD/NORD/Orphanet rare disease subset flags. Adapted from Monarch Initiative's mondo2omop. See [Mondo-OMOP bridge](mondo-omop-bridge.md). |
| [`note_extraction.py`](assets/note_extraction.py) | ~12 KB | Regex-based recovery of ALS-specific content (ALSFRS-R, ECAS, El Escorial, family history, genetic mutations, treatment milestones) from CCDA narratives and decoded note text. Seed patterns intended for site-specific tuning. See [Note extraction](note-extraction.md). |
| [`device_extraction.py`](assets/device_extraction.py) | ~28 KB | Equipment & DME extraction. Walks the bundle for HCPCS Level II / SNOMED CT / CPT-4 device codes and runs regex against narratives for AAC devices, wheelchairs, BiPAP/NIV, cough-assist, hospital beds, PEG tubes, and OT/PT/SLP equipment referrals. Emits `device_codes.csv` and `device_extractions.csv`. See [Device & equipment extraction](devices.md). |

## Example notebook

| File | Size | Purpose |
|------|------|---------|
| [`ARC_pipeline.ipynb`](assets/ARC_pipeline.ipynb) | ~12 KB | Six-cell example notebook for end-to-end runs. Loads inputs, runs the pipeline, prints diagnostics, saves outputs. Works in any Jupyter-compatible environment. |

## Configuration templates

| File | Size | Purpose |
|------|------|---------|
| [`test_patients.txt`](assets/test_patients.txt) | ~1 KB | File-based exclusion-rule template with the four match modes commented in. See [Stage 6](stages/filtering.md). |

## Databricks data extraction

These run inside Databricks (or any Spark-SQL engine) and produce the
chunked CSV inputs the pipeline reads. See
[Data extraction (Databricks)](databricks.md) for full instructions.

| File | Size | Purpose |
|------|------|---------|
| [`databricks_export.py`](assets/databricks_export.py) | ~9 KB | Full PySpark notebook with config block, validation, and consolidation. |
| [`databricks_export.sql`](assets/databricks_export.sql) | ~3 KB | Pure-SQL alternative for SQL-only Databricks notebooks. |

## Sample data

A complete synthetic ALS patient -- 84 records covering every category --
that you can run the pipeline against without any real EHR access. See
[Sample data](samples.md) for what's in each file.

| File | Size | Purpose |
|------|------|---------|
| [`sample_ccda.xml`](assets/sample_ccda.xml) | ~19 KB | Rich CCDA continuity-of-care document, 8 populated sections |
| [`sample_fhir_bundle.json`](assets/sample_fhir_bundle.json) | ~80 KB | FHIR R4 Bundle, 57 resources covering every extracted type |
| [`patient_master.csv`](assets/patient_master.csv) | ~70 KB | Long-format master CSV produced by Stage 7b. One row per record, patient demographics on every row. See [Patient master CSV](patient-master.md). |
| [`uuid_mapping.csv`](assets/uuid_mapping.csv) | <1 KB | Document-to-patient bridge with full demographics |
| [`ccda_chunks.csv`](assets/ccda_chunks.csv) | ~25 KB | Pipeline-ready chunked CSV for the CCDA |
| [`fhir_chunks.csv`](assets/fhir_chunks.csv) | ~85 KB | Pipeline-ready chunked CSV for the FHIR bundle |

## Quick local setup

To get a fully working local copy, download these four files plus the five
sample-data files:

```text
your-work-dir/
├── run_pipeline.py            <- from "Pipeline runtime"
├── dashboard.html             <- from "Pipeline runtime"
├── test_patients.txt          <- from "Configuration templates"
├── uuid_mapping.csv           <- from "Sample data"
└── CCDA and FHIR data/
    ├── ccda_chunks.csv        <- from "Sample data"
    └── fhir_chunks.csv        <- from "Sample data"
```

Then `pip install pandas pypdf openpyxl` and `python run_pipeline.py`.
See [Quickstart](quickstart.md) for full instructions.
