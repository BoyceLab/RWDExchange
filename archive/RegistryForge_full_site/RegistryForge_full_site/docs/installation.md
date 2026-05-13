# Installation

The pipeline runs on any standard Python environment. There are no
infrastructure requirements beyond Python and a few libraries.

!!! tip "Get the code"
    Every file referenced on this page is downloadable from the
    [Downloads](downloads.md) page.

## Requirements

- Python 3.10 or newer
- 4 GB RAM (more for very large cohorts)
- Local disk space roughly 3x the size of the chunked CSV inputs

## Dependencies

```bash
pip install pandas pypdf openpyxl
```

| Package | Purpose |
|---------|---------|
| `pandas` | CSV reading, Excel output |
| `pypdf` | PDF text extraction |
| `openpyxl` | Excel writer engine |

That's the entire dependency list. The pipeline uses only standard library
modules (`xml.etree.ElementTree`, `json`, `base64`, `re`, `unicodedata`,
`logging`) for everything else.

## Files you need

Copy these into your working directory:

| File | Source |
|------|--------|
| `run_pipeline.py` | from the project release |
| `dashboard.html` | from the project release |
| `test_patients.txt` | from the project release (or write your own) |

And generate (or receive from your warehouse team):

| File | How |
|------|-----|
| `CCDA and FHIR data/ccda_chunks.csv` | see [Databricks export](databricks.md) |
| `CCDA and FHIR data/fhir_chunks.csv` | see [Databricks export](databricks.md) |
| `uuid_mapping.csv` | see [Databricks export](databricks.md) |

## Running

The pipeline is a single Python module. Three equivalent ways to run it:

=== "Direct script"

    ```bash
    cd /path/to/working/directory
    python run_pipeline.py
    ```

=== "From a notebook"

    ```python
    import sys
    sys.path.insert(0, '/path/to/working/directory')
    import run_pipeline
    bundle = run_pipeline.main()
    ```

=== "From a wrapper"

    ```python
    from run_pipeline import main
    bundle = main()
    print(f"Patients: {len(bundle['patients'])}")
    ```

The first form writes outputs to disk and exits. The second and third
return the bundle dict in memory for further programmatic use.

## Configuration

The default working directory and input paths are defined as constants near
the top of `run_pipeline.py`. See [Configuration](configuration.md) for the
full list and how to override them.
