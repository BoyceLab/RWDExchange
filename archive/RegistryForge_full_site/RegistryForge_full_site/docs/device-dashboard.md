# Device & equipment dashboard

`device_dashboard.py` is the visualization layer for the device-extraction
outputs. It reads the two CSVs produced by `device_extraction.py` and
emits a single, self-contained HTML report (no external dependencies, no
network calls at view time, no server required) that you can open in any
browser.

**[:material-monitor-eye: Open the live demo](assets/device_dashboard_demo.html){ .md-button .md-button--primary target="_blank" }** &nbsp;
**[:material-download: Module source (device_dashboard.py)](assets/device_dashboard.py){ .md-button target="_blank" }**

## Inputs

| File                       | Produced by              | Description                                          |
| -------------------------- | ------------------------ | ---------------------------------------------------- |
| `device_codes.csv`         | `device_extraction.py`   | Structured matches against DEVICE_CODES (HCPCS / CPT-4 / SNOMED-CT) including ALS-care-indicator procedures. |
| `device_extractions.csv`   | `device_extraction.py`   | Regex matches against `notes[].narrative_text` and `documents[].plain_text` for PEG, BiPAP, tracheostomy, cough assist, power wheelchair, AAC/SGD brand names, etc. |

## Output

A single HTML file — typical name `device_dashboard.html` — with five
tabs:

1. **Overview by category** — bar chart of patient counts per device
   category (e.g. `als_care_indicator_respiratory`, `peg`, `niv`,
   `wheelchair_power`, `aac_device`) with record counts and a marker
   for whether matches came from structured codes, free-text regex, or
   both.

2. **All devices / indicators** — filterable per-label table.
   Type in the filter box to narrow by name or category.

3. **Patient × category matrix** — heatmap-style grid showing which
   PT-NNNN-pseudonymized patients have records in which categories.
   Cell shading scales with record count: light blue (1–4), medium
   (5–19), deep purple (20+).

4. **Source-record snippets** — up to three representative text
   excerpts per regex pattern, sampled across unique patients. For
   chart-review preparation and source verification — not for analysis.

5. **About** — privacy controls and the SMART-on-FHIR-doesn't-capture-DME
   caveat (see below).

## Privacy controls (baked in, cannot be disabled)

- Patient identifiers replaced with **PT-NNNN pseudonyms**, stable
  within one run.
- Calendar dates reduced to **year only**.
- Snippet text truncated to **200 characters**.
- Devices, patterns, and categories with fewer than **k=2** unique
  exposed patients are suppressed entirely (k-anonymity at the device
  level).
- Resource UUIDs are never emitted.

## Usage

In a notebook:

```python
import device_dashboard

device_dashboard.main(
    codes_csv_path       = './device_codes.csv',
    extractions_csv_path = './device_extractions.csv',
    out_path             = './device_dashboard.html',
    cohort_name          = 'Your cohort name',
    k                    = 2,           # k-anonymity threshold
)
```

From the command line:

```bash
python -c "import device_dashboard; device_dashboard.main()"
```

(defaults read from the working directory).

## Important caveat about ARC data

SMART on FHIR pulls from the clinical EHR rarely capture DME-supplier
records, so direct device-procurement codes (E0470 BiPAP, K0813 power
wheelchair, B4034 gastrostomy supplies) are typically absent from
structured procedure records. The structured side of this dashboard
surfaces mostly **ALS-care-indicator** procedures — spirometry, sleep
studies, EMG/NCS, speech screening, botulinum toxin, PT/OT
re-evaluations, and mobility/self-care status reporting — that signal
where the patient is on the typical ALS-care pathway. Direct device
presence is typically captured by the regex side from clinical notes.

This is documented in the dashboard's About tab so users don't
over-interpret the absence of structured DME codes as absence of devices.

## Visual design

The dashboard uses a navy → purple gradient header that distinguishes it
from the [exposure dashboard](exposure-dashboard.md) (teal → navy →
purple gradient). Both follow the same privacy-safe template so you can
view either of them in the same context without confusion.
