# Environmental / occupational / toxic exposure extraction

`exposure_extraction.py` walks the dashboard bundle and surfaces
environmental, occupational, and toxic exposures relevant to ALS
risk-factor literature. It combines two extraction layers — structured
ICD-10-CM codes and regex patterns over clinical narrative — and tags
each finding with a representative ECTO (Environmental Conditions,
Treatments, and Exposures Ontology) term where one has been verified.

**[:material-download: Module source (exposure_extraction.py)](assets/exposure_extraction.py){ .md-button target="_blank" }**

## What it captures

The patterns and codes are drawn from the published ALS environmental
risk-factor literature. Each category is grounded in ECTO; the
specificity of the ontology grounding varies (see
[ECTO grounding](#ecto-grounding) below).

### Structured codes (ICD-10-CM)

| Family            | Codes                                                                | Captures                                                  |
| ----------------- | -------------------------------------------------------------------- | --------------------------------------------------------- |
| Nicotine          | `F17.x`, `Z87.891`                                                   | Current and former nicotine dependence, history of smoking |
| Occupational      | `Z57.x` (0–9)                                                        | Noise, radiation, dust, agricultural toxins, industrial chemicals, temperature, vibration |
| Substance contact | `Z77.0xx`                                                            | Lead, uranium, arsenic, aromatic compounds, asbestos, other hazardous chemicals |
| Pollution         | `Z77.110–.118`                                                       | Air, water, soil pollution                                |
| Physical hazards  | `Z77.121–.128`                                                       | Noise, mold, harmful algae / cyanotoxins                  |
| Body fluids       | `Z77.21`, `Z77.22`, `Z77.29`, `Z77.9`                                | Hazardous body fluids, environmental tobacco smoke, other |
| Military          | `Z91.82`, `Z56.82`                                                   | Personal history of military deployment, military deployment status |
| Trauma            | `Z87.820`, `S06.0X0A`, `S06.9X0A`                                    | Personal history of TBI, concussion, unspecified intracranial injury |

### Regex patterns (clinical narrative)

The pattern set covers 14 categories across the major ALS risk-factor
domains:

| Category               | Example patterns                                                                                          |
| ---------------------- | --------------------------------------------------------------------------------------------------------- |
| Smoking                | current / former smoker, pack-years, chewing tobacco                                                       |
| Military service       | general service, Gulf War / OIF / OEF / OND, Vietnam + Agent Orange, **Camp Lejeune**                     |
| Pesticides             | general pesticide, organophosphate, **paraquat**, glyphosate / Roundup, DDT, agricultural occupations    |
| Heavy metals           | lead, mercury, manganese / welding fumes, arsenic, cadmium                                                |
| Solvents               | TCE / PCE (context-required), benzene, formaldehyde, general organic solvents, welding                    |
| Asbestos               | asbestos exposure, asbestos-containing material, mesothelioma risk                                        |
| Head trauma            | TBI history, multiple concussions, **football career**, contact sports, military blast injury             |
| EMF / electrical       | electrical worker, lineman, electrician, EMF / electromagnetic field exposure                             |
| Cyanotoxins            | **BMAA**, cyanobacteria, blue-green algae, harmful algal blooms                                           |
| Mold                   | mold exposure, black / toxic mold, stachybotrys, mycotoxin                                                |
| Air pollution          | PM2.5 / PM10, particulate matter, diesel exhaust, traffic-related air pollution                           |
| Occupational dust      | silica, coal, wood, grain, cotton dust; silicosis; coal workers pneumoconiosis                            |

Patterns that historically caused false positives are documented in
comments inside the module:

- **`NFL` alone** is **not** a contact-sport match — in an ALS cohort,
  NFL overwhelmingly means **neurofilament light** (a serum biomarker
  for motor-neuron-disease activity). NFL only matches when paired
  with explicit football vocabulary (`played NFL`, `NFL player`,
  `NFL career`, `in the NFL`, etc.).
- **Bare `PERC`** is **not** a perchloroethylene match — in clinical
  notes, PERC is the **Pulmonary Embolism Rule-out Criteria**. PCE / PERC
  matches require explicit exposure context.
- **Bare `TCE`** requires exposure / contamination / occupational
  context for the same reason — `TCE clinical trial` does not match.

## ECTO grounding

Each exposure category is mapped to a representative term in
[ECTO](https://github.com/EnvironmentOntology/environmental-exposure-ontology).
ECTO is an OBO Foundry ontology with ~2,700 exposure terms; it is the
recommended terminology for exposure annotation in [GA4GH
Phenopackets](https://www.ga4gh.org/product/phenopackets/) and the
[GA4GH Human Exposome Data Standards Study
Group](https://www.ga4gh.org/product/human-exposome-data-standards/),
which is extending Phenopackets with formal schemas for exposure data.

The mapping table inside the module distinguishes three statuses:

- **`verified`** — the ECTO term ID has been confirmed against the
  published [SSSOM mapping
  file](https://github.com/EnvironmentOntology/environmental-exposure-ontology/blob/master/mappings/ecto.sssom.tsv).
  These appear in the dashboard as clickable links to the OLS browser.
- **`pending`** — no verified term ID yet. The dashboard displays
  "curation pending" rather than a guessed identifier. Adopters should
  search the [OLS ECTO browser](https://www.ebi.ac.uk/ols/ontologies/ecto)
  for the closest term and replace the `None` placeholder.
- **`catch_all` / `out_of_scope`** — buckets that don't correspond to
  real ECTO concepts, or that ECTO doesn't cover (e.g. psychosocial
  exposures).

Currently verified IDs:

| Category / agent                | ECTO term       | Label                                                              |
| ------------------------------- | --------------- | ------------------------------------------------------------------ |
| smoking (all sub-patterns)      | `ECTO:9000250`  | exposure to nicotine                                               |
| lead                            | `ECTO:9000945`  | exposure to lead                                                   |
| mercury                         | `ECTO:0001571`  | exposure to mercury                                                |
| arsenic                         | `ECTO:9000032`  | exposure to arsenic                                                |
| asbestos                        | `ECTO:9000033`  | exposure to asbestos                                               |
| air pollution (PM inhalation)   | `ECTO:0000977`  | exposure to ultrafine respirable suspended particulate matter via inhalation |

The remaining patterns and categories are flagged as `pending` and
appear in the dashboard's ECTO Mapping tab as a curation worklist.

## Outputs

| File                       | Description                                              |
| -------------------------- | -------------------------------------------------------- |
| `exposure_codes.csv`       | Structured ICD-10-CM matches                             |
| `exposure_extractions.csv` | Regex matches against narrative text                     |

Both feed into [`exposure_dashboard.py`](exposure-dashboard.md) for
visual review, and into the downstream Phenopackets generator for
formal encoding.

## Usage

```python
import exposure_extraction

result = exposure_extraction.main(
    bundle_path = './dashboard_data.json',
    out_root    = './',
)

print(result)
# {'exposure_codes_csv': './exposure_codes.csv',
#  'exposure_extractions_csv': './exposure_extractions.csv',
#  'code_rows': N,
#  'extraction_rows': M}
```

## Important caveat

Most environmental and occupational exposure information lives in
**social-history narrative sections** of clinical notes rather than as
structured ICD-10 codes. The `Z57.x` and `Z77.x` families are
systematically under-coded in clinical practice; the bulk of signal in
any ALS-cohort run of this module typically comes from the regex side
scanning narratives. Adopters should not over-interpret the absence of
structured codes as absence of exposure, and detection sensitivity will
vary substantially across EHR vendors and clinical-documentation
conventions.

## Related

- **[Exposure dashboard](exposure-dashboard.md)** — visualization layer
  for the outputs of this module.
- **[Note extraction](note-extraction.md)** — companion module that
  surfaces structured ALS findings (ALSFRS-R scores, FVC% predicted,
  El Escorial category, etc.) from the same narrative sources.
- **[GA4GH Phenopackets](phenopackets.md)** — downstream consumer of
  these exposure annotations.
