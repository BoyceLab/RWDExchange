# Environmental / occupational exposure dashboard

`exposure_dashboard.py` is the visualization layer for
[`exposure_extraction.py`](exposure-extraction.md). It reads the two
CSVs produced by exposure extraction and emits a single, self-contained
HTML report (no external dependencies) showing environmental,
occupational, and toxic exposures across the cohort with full ECTO
ontology grounding.

**[:material-monitor-eye: Open the live demo](assets/exposure_dashboard_demo.html){ .md-button .md-button--primary target="_blank" }** &nbsp;
**[:material-download: Module source (exposure_dashboard.py)](assets/exposure_dashboard.py){ .md-button target="_blank" }**

## Inputs

| File                       | Description                                              |
| -------------------------- | -------------------------------------------------------- |
| `exposure_codes.csv`       | Structured ICD-10-CM occupational and exposure codes     |
| `exposure_extractions.csv` | Regex matches against `notes[].narrative_text` and `documents[].plain_text` for ALS-relevant environmental exposures |

Both are produced by [`exposure_extraction.py`](exposure-extraction.md).

## Output

A single HTML file — typical name `exposure_dashboard.html` — with six
tabs:

### 1. Overview by category

Table with one row per exposure category. Columns: category name, ECTO
term (clickable link to OLS browser for verified terms; "curation
pending" badge otherwise), unique patient count, bar chart of patient
prevalence, total record count, and source kind (structured-coded,
text-regex, or both).

### 2. All exposures

Filterable per-label table. Type in the filter box to narrow down by
specific exposure name (e.g. "paraquat", "Camp Lejeune", "TCE",
"BMAA").

### 3. Patient × category matrix

Heatmap-style grid showing PT-NNNN-pseudonymized patients by exposure
category. Cell shading scales with record count: light teal (1–2),
medium (3–9), deep teal (10+).

### 4. Source-record snippets

Up to three representative text excerpts per regex pattern, sampled
across unique patients. Each snippet shows: pseudonym, source kind
(CCDA section / RTF document / HTML document), matched value, and a
truncated text excerpt. For source verification and chart-review
preparation.

### 5. ECTO mapping

Full reference table showing every category mapped to ECTO. Three
curation states:

- :material-check-circle: **verified** — ID confirmed against the
  published SSSOM mapping file; rendered as a clickable link to the
  EBI Ontology Lookup Service.
- :material-progress-clock: **pending** — no verified term yet;
  dashboard displays "curation pending" rather than a guessed ID.
- :material-cancel: **catch-all / out of scope** — buckets that don't
  correspond to real ECTO concepts, or that ECTO doesn't cover.

Sorted with verified entries first so the curation worklist is
immediately visible.

### 6. About

Methodology, privacy controls, ALS risk-factor literature context,
and caveats about EHR data completeness.

## Privacy controls (baked in, cannot be disabled)

Same posture as the [device dashboard](device-dashboard.md):

- Patient identifiers replaced with **PT-NNNN pseudonyms**, stable
  within one run.
- Calendar dates reduced to **year only**.
- Snippet text truncated to **200 characters**.
- Categories and patterns with fewer than **k=2** unique patients
  are suppressed entirely.
- Resource UUIDs are never emitted.

## Usage

```python
import exposure_dashboard

exposure_dashboard.main(
    codes_csv_path       = './exposure_codes.csv',
    extractions_csv_path = './exposure_extractions.csv',
    out_path             = './exposure_dashboard.html',
    cohort_name          = 'Your cohort name',
    k                    = 2,           # k-anonymity threshold
)
```

## ALS risk-factor literature context

The exposure categories surfaced by this dashboard are drawn from the
established and proposed ALS environmental risk-factor literature:

- **Smoking** is the most consistent epidemiological signal in ALS
  case-control studies.
- **Military service** is associated with elevated ALS risk; ALS is a
  presumptive service-connected disability for some veteran cohorts.
- **Pesticides** (especially organophosphates and paraquat) and **heavy
  metals** (particularly lead) have been studied as candidate risk
  factors across multiple cohorts.
- **Industrial solvents** (especially trichloroethylene / TCE,
  implicated in the Camp Lejeune water contamination 1953–1987) are
  active research areas.
- **Repetitive head trauma** (football, military blast injury) is
  associated with motor neuron disease via the CTE pathway.
- **Cyanotoxins** (the BMAA hypothesis, Cox & Sacks 2002) and
  **electromagnetic fields** (occupational electrical workers) remain
  active candidate hypotheses.

This dashboard is designed to surface candidate signals in
routinely-collected EHR data; confirmatory epidemiological analysis
requires independent exposure assessment and case-control or cohort
designs.

## Visual design

The dashboard uses a teal → navy → purple gradient header that
visually distinguishes it from the [device
dashboard](device-dashboard.md) (which uses navy → purple). Same
privacy-safe template; same five-or-six-tab layout.

## Related

- **[Exposure extraction](exposure-extraction.md)** — module that
  produces the CSVs this dashboard reads.
- **[Device dashboard](device-dashboard.md)** — companion privacy-safe
  dashboard for clinical equipment / ALS-care-indicator data.
- **[Cohort EDA](cohort-eda.md)** — broader cohort exploration
  dashboard for demographics, comorbidities, and ALS-specific
  measurements.
- **[GA4GH Phenopackets](phenopackets.md)** — downstream consumer of
  ECTO-coded exposure annotations.
