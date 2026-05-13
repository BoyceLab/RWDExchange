# Drug repurposing analysis

`drug_repurposing.py` is the add-on module that adapts the methodology of [Reimer et al., *Lancet Digital Health* 2026](https://doi.org/10.1016/j.landig.2025.100963) &mdash; an EHR-based study identifying drug repurposing candidates for ALS in the US Veterans Health Administration database &mdash; to the bundle that `run_pipeline.py` produces. It also exports candidate cases in the schema of the [FDA / NCATS-NIH / Critical Path Institute CURE Drug Repurposing Collaboratory](https://cure.ncats.io/) Treatment Registry intake CRF.

## Live demo

Below is the actual drug repurposing report, pre-loaded against a synthetic 220-patient ALS cohort that exercises every section &mdash; cohort overview, 21 medications grouped under 15 ATC classes, crude exposed-vs-unexposed median survival comparison, and protective / harmful directional markers. All values are synthetic; no real ARC data.

<iframe
  src="../assets/drug_repurposing_demo.html"
  style="width: 100%; height: 950px; border: 1px solid var(--md-default-fg-color--lightest); border-radius: 4px; display: block; margin: 1.5rem 0;"
  title="Drug repurposing demo report"
></iframe>

## What it does, what it does not do

This module is responsible for **cohort assembly and exposure assignment**. It identifies who counts as exposed to a given medication under explicit criteria, computes baseline characteristics, attaches survival information, groups medications by ATC class, and exports both an analytic data set and a CURE ID intake file.

It deliberately does **not** run propensity-score matching or Cox proportional hazards regression. Rigorous causal inference on observational EHR data needs `lifelines` / `statsmodels` and per-cohort tuning of caliper widths, immortal-time-bias correction, covariate-balance checks, and multiple-testing correction. The cohort data set this module emits (`drug_repurposing_cohort.csv`) is the input to those tools &mdash; not a substitute for them.

## Cohort definition

The module walks the bundle `problems[]` for any record coded under the motor-neuron-disease spectrum:

| Vocabulary | Code | Meaning |
|---|---|---|
| ICD-10-CM | G12.21 | Amyotrophic lateral sclerosis |
| ICD-10-CM | G12.20 | Motor neuron disease, unspecified |
| ICD-10-CM | G12.22 | Progressive bulbar palsy |
| ICD-10-CM | G12.23 | Primary lateral sclerosis (adult) |
| ICD-10-CM | G12.29 | Other motor neuron disease |
| ICD-9-CM | 335.20&ndash;29 | Motor neuron disease (legacy) |
| SNOMED CT | 86044005 | Amyotrophic lateral sclerosis |
| SNOMED CT | 230258005 | Motor neuron disease |
| Mondo | MONDO:0004976 | Amyotrophic lateral sclerosis |
| Mondo | MONDO:0019056 | Motor neuron disease |

Cohort entry date is the earliest such diagnosis. Patients with no medication record after cohort entry are excluded as a proxy for lack of engagement with the index health system, following Reimer 2026.

## Exposure criteria

The module supports four exposure-criterion modes via the `criterion` parameter.

**`criterion='any'` (default) &mdash; Criterion C, fits C-CDA / FHIR registry data.** A patient is **exposed** to a medication if they have at least one medication record with `effective_date` in the window from 6 months before to 12 months after cohort entry. This is the right default for Registry Forge bundles produced by `run_pipeline.py`, where each medication row represents one prescription event (C-CDA SubstanceAdministration or FHIR MedicationRequest / MedicationStatement) and there is typically no separate end-date.

**`criterion='reimer'`, `'reimer-a'`, `'reimer-b'` &mdash; the strict Reimer 2026 criteria, designed for VHA-style dispense-event data.** Reimer's data had one row per pharmacy fill with dispense_date and supply_days, allowing the reconstruction of treatment intervals. Criterion A requires the prescription start *or* end to fall within 12 months of cohort entry *and* the end date to be at least 6 months after the start. Criterion B requires at least two dispenses in the [-6, +12] month window. Neither will fire on C-CDA / FHIR data with one record per medication and no end_date.

```python
import drug_repurposing
drug_repurposing.main(
    bundle_path = 'dashboard_data.json',
    out_dir     = 'drug_repurposing_output',
    min_exposed = 5,           # see "Tuning min_exposed for small cohorts" below
    k           = 5,
    criterion   = 'any',       # default; 'reimer' for dispense-event data
    cohort_name = 'ARC Study EHR cohort',
)
```

## Tuning `min_exposed` for small cohorts

Reimer used `min_exposed = 30` against 11,003 ALS patients. For a 96-patient cohort that threshold is far too high &mdash; almost no medication will have 30+ exposed patients, and the output will be empty. The default in `drug_repurposing.py` is `min_exposed = 5`, which is a sensible starting value for cohorts of a few dozen to a few hundred patients.

If your run produces empty output, the diagnostic log prints a hint telling you what the top medication's exposed-patient count is, so you can lower `min_exposed` accordingly:

```
top exposed medications across cohort:
     12 patients  riluzole    (dropped, < min_exposed=30)
      8 patients  atorvastatin    (dropped, < min_exposed=30)
HINT: top medication has 12 exposed patients (need >= 30).
      Try drug_repurposing.main(..., min_exposed=12) to see signal.
```

## Diagnostic output

Empty outputs are not silent. The module logs counts at every step:

- How many problem rows were scanned, how many matched a motor-neuron-disease code, and which coding system they came from.
- How many medication rows had a patient_id, how many normalized to a non-empty name, how many had a parseable date, how many had an `end_date` (with an explicit note if none did), and how many resolved to a known `ATC_SEED` entry.
- The top 15 unmatched medication names &mdash; the right list to consult when extending `ATC_SEED` or `BRAND_TO_GENERIC` for your data.
- How many cohort patients had any medication record, how many medications had &ge;1 exposed patient under the selected criterion, and which medications met `min_exposed`.



## ATC class seed mapping

Medications are grouped by Anatomical Therapeutic Chemical (ATC) classification via the `ATC_SEED` dictionary at the top of the module. The seed covers every drug Reimer reported plus obvious extensions:

| ATC code | Class | Drugs in seed |
|---|---|---|
| C10AA | HMG-CoA reductase inhibitors | simvastatin, lovastatin, pravastatin, atorvastatin, rosuvastatin, pitavastatin, fluvastatin |
| G04BE | PDE5 inhibitors | sildenafil, vardenafil, tadalafil, avanafil |
| G04CA | &alpha;-adrenoreceptor antagonists | tamsulosin, terazosin, alfuzosin, silodosin |
| M03BX | Centrally acting muscle relaxants | cyclobenzaprine, baclofen, tizanidine |
| N07XX | Other nervous system drugs &mdash; ALS | riluzole, edaravone, tofersen |
| N07XX59 | Other nervous system drugs &mdash; pseudobulbar affect | **dextromethorphan-quinidine** (FDA-approved combination, brand name **Nuedexta**) |
| R05DA09 | Opium alkaloid antitussive | **dextromethorphan** (when prescribed alone, almost always an antitussive; also the active CNS ingredient in Nuedexta) |
| C01BA01 | Class IA antiarrhythmic | **quinidine** (when prescribed alone at therapeutic doses; in Nuedexta it is the subtherapeutic CYP2D6-inhibitor ingredient) |
| M01AC | Oxicam NSAIDs | meloxicam |
| A11CC | Vitamin D | colecalciferol |
| B01AF | Direct factor Xa inhibitors | rivaroxaban |
| C03AA | Thiazide diuretics | hydrochlorothiazide |
| C09AA | ACE inhibitors | lisinopril |
| A03AB | Anticholinergics (for sialorrhea) | glycopyrrolate |
| A04AA | 5HT3 antagonists | ondansetron |
| N02AB | Opioid analgesics | fentanyl |
| N02BF | Gabapentinoids | gabapentin |
| R01BA | Decongestants | pseudoephedrine |
| R05CA | Mucolytics | guaifenesin |
| R06AE | H1 antihistamines | cetirizine |
| R03AC | Short-acting β-agonists | salbutamol / albuterol |
| R03DC | Leukotriene receptor antagonists | montelukast |

### Nuedexta and its ingredients are tracked three ways

Following Reimer 2026, the module tracks the FDA-approved dextromethorphan-quinidine combination *and* its two active ingredients **separately**, because they appear in EHR data in different clinical contexts:

1. **Combination (Nuedexta) &rarr; N07XX59.** Prescribed for pseudobulbar affect in ALS. Resolves from `Nuedexta`, `dextromethorphan-quinidine`, `dextromethorphan/quinidine`, or any dosage-suffixed variant.
2. **Dextromethorphan alone &rarr; R05DA09.** Almost always an antitussive when prescribed alone. Reimer reported standalone dextromethorphan as a separate harm-direction signal under this class.
3. **Quinidine alone &rarr; C01BA01.** A Class IA antiarrhythmic when prescribed alone at therapeutic doses; the subtherapeutic CYP2D6-inhibitor role in Nuedexta is distinct.

Adopters with different therapeutic-area focus extend `ATC_SEED` directly in source.

## Brand-name resolution

Real EHR data routinely names medications by brand (Lipitor, Flomax, Nuedexta, Robinul, Radicava) rather than generic. The module ships a second dictionary, `BRAND_TO_GENERIC`, that `_norm_med_name` consults *before* the `ATC_SEED` lookup. A record with display name `"Lipitor 20 mg tablet"` is therefore normalized to `"atorvastatin"` and matches the atorvastatin entry; `"Nuedexta 20 mg-10 mg tablet"` is normalized to `"dextromethorphan-quinidine"` and matches the combination entry.

Brand coverage in the shipped seed:

| Category | Brand &rarr; generic mappings |
|---|---|
| ALS FDA-approved | Rilutek / Tiglutik / Exservan &rarr; riluzole · Radicava / Radicava ORS &rarr; edaravone · Qalsody &rarr; tofersen · **Nuedexta &rarr; dextromethorphan-quinidine** · Relyvrio / Albrioza &rarr; sodium phenylbutyrate-taurursodiol |
| Statins | Lipitor &rarr; atorvastatin · Crestor &rarr; rosuvastatin · Zocor &rarr; simvastatin · Mevacor / Altoprev &rarr; lovastatin · Pravachol &rarr; pravastatin · Livalo / Zypitamag &rarr; pitavastatin · Lescol &rarr; fluvastatin |
| PDE5 inhibitors | Viagra / Revatio &rarr; sildenafil · Levitra / Staxyn &rarr; vardenafil · Cialis / Adcirca &rarr; tadalafil · Stendra &rarr; avanafil |
| &alpha;-blockers | Flomax &rarr; tamsulosin · Hytrin &rarr; terazosin · Uroxatral &rarr; alfuzosin · Rapaflo &rarr; silodosin · Cardura &rarr; doxazosin |
| Muscle relaxants | Flexeril / Amrix / Fexmid &rarr; cyclobenzaprine · Zanaflex &rarr; tizanidine · Lioresal / Gablofen / Ozobax &rarr; baclofen |
| Sialorrhea | Robinul / Cuvposa / Glycate / Dartisla &rarr; glycopyrrolate |
| Antiemetics | Zofran / Zuplenz &rarr; ondansetron |
| Opioids | Sublimaze / Duragesic / Actiq / Fentora / Abstral / Subsys / Lazanda &rarr; fentanyl |
| Gabapentinoids | Neurontin / Gralise / Horizant &rarr; gabapentin |
| Cough / cold | Sudafed &rarr; pseudoephedrine · Mucinex / Robitussin &rarr; guaifenesin |
| Antihistamines | Zyrtec / Reactine &rarr; cetirizine |
| Bronchodilators | Ventolin / Proventil / ProAir / AccuNeb &rarr; salbutamol · Xopenex &rarr; levalbuterol |
| Leukotrienes | Singulair &rarr; montelukast |
| ACE inhibitors | Prinivil / Zestril / Qbrelis &rarr; lisinopril |
| Anticoagulants | Xarelto &rarr; rivaroxaban |
| Vitamin D | Vitamin D / Vitamin D3 / D3 / cholecalciferol &rarr; colecalciferol |

Adopters extend `BRAND_TO_GENERIC` for brand names common in their own site's EHR; the dictionary lookup is performed before the `ATC_SEED` lookup so adding a new brand requires no other changes.

## Outputs

Four files are produced in `out_dir`:

### 1. `drug_repurposing_cohort.csv`

Long-form per-(patient, medication) data set, one row per cohort patient per kept medication.

| Column | Description |
|---|---|
| `patient_id` | Bundle patient identifier |
| `cohort_entry` | Earliest motor-neuron-disease diagnosis date (ISO) |
| `age_at_entry` | Years (float) |
| `sex`, `race`, `ethnicity`, `marital_status` | Baseline covariates |
| `medication` | Normalized medication name |
| `atc_code`, `atc_class` | From `ATC_SEED` lookup |
| `exposed` | 1 if either criterion A or B was met, else 0 |
| `criterion` | "A", "B", "A+B", or empty |
| `n_dispenses_window` | Number of dispenses in &minus;6 to +12 month window |
| `first_dispense` | First dispense date (ISO) |
| `survival_days` | Days from cohort entry to death (or to today if censored) |
| `death_observed` | 1 if death recorded, 0 if censored |
| `deceased_date` | Death date if recorded |

This is the input to Cox proportional hazards regression and propensity-score-matched analysis. A typical follow-on notebook does:

```python
import pandas as pd
from lifelines import CoxPHFitter

df = pd.read_csv('drug_repurposing_cohort.csv')

for med in df['medication'].unique():
    sub = df[df['medication'] == med].copy()
    sub['exposed'] = sub['exposed'].astype(int)
    cph = CoxPHFitter()
    cph.fit(sub[['survival_days','death_observed','exposed',
                 'age_at_entry','sex','race']].dropna(),
            duration_col='survival_days',
            event_col='death_observed',
            formula='exposed + age_at_entry + C(sex) + C(race)')
    hr = cph.hazard_ratios_['exposed']
    p  = cph.summary.loc['exposed','p']
    print(f'{med:20s} HR={hr:.2f}  p={p:.4f}')
```

For propensity matching, use `psmpy` or implement caliper matching directly per Reimer's Methods (caliper width 0.2 &times; SD of the logit of the propensity score, up to three matched controls per treated unit).

### 2. `drug_repurposing_summary.csv`

Per-medication summary, one row per medication.

| Column | Description |
|---|---|
| `medication` | Normalized medication name |
| `atc_code`, `atc_class` | From `ATC_SEED` |
| `n_exposed` | Count with k-anonymity threshold applied (renders as "&lt;5" if below) |
| `n_unexposed` | Count of patients in cohort not exposed |
| `n_deaths_exposed`, `n_deaths_unexposed` | Deaths in each arm |
| `median_survival_days_exposed`, `median_survival_days_unexposed` | Crude median survival in each arm |

### 3. `cure_id_intake.csv`

One row per exposed (patient, medication) formatted to match the FDA / NCATS-NIH / Critical Path Institute [CURE ID Treatment Registry](https://cure.ncats.io/) intake CRF. Banded and pseudonymized for PII-free submission:

| Column | Description |
|---|---|
| `therapeutic_area` | "Rare Genetic Disorders" |
| `disease` | "Amyotrophic lateral sclerosis (or motor neuron disease spectrum)" |
| `user_type` | "A Healthcare Provider" |
| `pseudo_patient_id` | "PT-NNNN" (stable per run, not persisted) |
| `age_group` | CURE ID age band ("51 - 60 years", "61 - 70 years", etc.) |
| `sex` | "Female" / "Male" / "Unknown" |
| `country_treated` | "United States" |
| `races` | Comma-separated CURE ID race options |
| `medication_name`, `medication_atc_class`, `medication_atc_code` | From cohort table |
| `exposure_criterion` | "A", "B", or "A+B" |
| `n_dispenses_window` | Number of dispenses in window |
| `why_new_way` | "Repurposing candidate identified from EHR survival signal" |
| `treatment_outcome` | Banded by survival days from cohort entry |
| `source_pipeline` | "Registry Forge drug_repurposing.py (Reimer 2026 methodology)" |

This file is the artifact a registry submits to the CURE ID platform. The PII-free posture (pseudonymized identifiers, banded ages, no absolute dates, k-anonymity at the medication level) matches the registry's documented expectations.

### 4. `drug_repurposing_report.html`

Single-file analytical report grouped by ATC class. Cohort overview cards, per-class medication tables with exposed and unexposed counts, crude median survival comparison, and methodological limitations.

## Customization

The module exposes several knobs for site-specific tuning:

- **`MIN_EXPOSED_PATIENTS`** &mdash; default 30 (Reimer); lower for smaller cohorts.
- **`DEFAULT_K_ANONYMITY`** &mdash; default 5; raise for smaller or more sensitive cohorts.
- **`CRITERION_A_*`, `CRITERION_B_*`** &mdash; exposure-window constants (12 months after, 6 months minimum duration; 6 months before, 12 months after, 2 dispenses minimum). Site-specific exposure definitions can adjust these directly.
- **`COHORT_CODES`** &mdash; dictionary of `(system, code) → display` for cohort identification. Adapt to a different disease by replacing with the relevant ICD-10-CM / SNOMED / Mondo codes.
- **`ATC_SEED`** &mdash; medication-to-ATC-class mapping. Extend with additional drugs relevant to your therapeutic area.
- **`CURE_ID_AGE_BANDS`** &mdash; age-band definitions, currently aligned to the CURE ID Generic Other Disease CRF.
- **`CURE_ID_RACE_MAP`** &mdash; bundle-to-CURE-ID race-option mapping.

All of these live as plain Python dictionaries at the top of the module file.

## Limitations to keep in mind

- **Indication bias** &mdash; a patient prescribed a PDE5 inhibitor may by virtue of that indication be healthier than average; the patient prescribed an opioid may by virtue of that indication be in advanced disease. Propensity matching helps but does not eliminate this.
- **Immortal-time bias** &mdash; the exposure criteria already account for this by requiring duration or repeat dispensing, but downstream Cox analysis must continue the care.
- **EHR diagnosis accuracy** &mdash; Reimer addressed this by running a sensitivity analysis restricted to riluzole-treated patients (a drug with a very ALS-specific indication). The same sensitivity analysis is appropriate here.
- **Dose-response unavailable** &mdash; dosage information is not currently surfaced from the bundle; adopters wanting a dose-response analysis should extend the medication parser.
- **Multiple-testing burden** &mdash; screening hundreds of medications inflates the false-positive rate. Bonferroni correction at the medication count is the conservative default; Reimer reported both uncorrected and Bonferroni-corrected results.
- **Single-cohort generalization** &mdash; outputs are for hypothesis generation, not for confirmatory inference. Prospective randomized trials remain the gold standard for therapeutic efficacy.

## Reference

Reimer RJ, Soper B, Wilson JL, Goncalves AR, Cadena J, Suarez P, Gryshuk AL, Osborne TF, Grimes KV, Ray P. Identification of drug repurposing candidates for amyotrophic lateral sclerosis using electronic health records: a retrospective cohort study. *Lancet Digit Health* 2026. [https://doi.org/10.1016/j.landig.2025.100963](https://doi.org/10.1016/j.landig.2025.100963)
