"""
drug_repurposing.py — Registry Forge drug repurposing module
=============================================================
Identifies candidate drug repurposing signals from the bundle by adapting the
methodology of Reimer et al. (Lancet Digital Health, 2026): "Identification
of drug repurposing candidates for amyotrophic lateral sclerosis using
electronic health records: a retrospective cohort study."

This module's responsibility is *cohort assembly* — defining who counts as
exposed to a given medication under explicit criteria, what their baseline
characteristics are, and what outcome (death) information is available.

The downstream inferential statistics — propensity score matching, Cox
proportional hazards, multi-class additive effect analysis — are intentionally
out of scope here, because rigorous causal inference on observational EHR
data has real methodological depth (caliper matching, immortal-time bias
correction, treatment-effect estimation via potential outcomes) that needs
tools like `lifelines` / `statsmodels` and careful per-cohort tuning. This
module produces the clean datasets that those tools consume.

Method (adapted from Reimer 2026, with motor-neuron-disease cohort definition):
  1. Identify cohort: every patient with a problem coded G12.21 (ICD-10) or
     335.20 (ICD-9) or 86044005 (SNOMED CT) or any descendant of
     MONDO:0019056 in the bundle's problems[] array. Exclude patients with
     no medication record after the earliest motor neuron disease problem
     date (interpreted as lack of engagement with the index health system).

  2. Exposure criteria (per Reimer 2026, used by drug):
     Criterion A: dispense date OR end date within 12 months of cohort entry
                  date AND end date at least 6 months after dispense date.
     Criterion B: at least two dispenses within the window from 6 months
                  before cohort entry to 12 months after.
     A patient is "exposed" to a medication if EITHER criterion is met.

  3. Restrict to medications with at least MIN_EXPOSED exposed patients
     (default 30, matching Reimer).

  4. Per-patient covariate assembly: sex, age at cohort entry, race,
     marital status, BMI (most recent before entry), comorbidity load,
     visits per year before entry, year of cohort entry.

  5. Group medications by ATC class (Anatomical Therapeutic Chemical
     classification) using a seed mapping covering the Reimer candidate
     drugs; extensible via ATC_SEED.

  6. Outputs:
     - drug_repurposing_cohort.csv     One row per (patient, medication) for
                                       every medication meeting MIN_EXPOSED.
                                       Includes exposure flag, criterion met,
                                       and all baseline covariates. Ready
                                       to be loaded into lifelines /
                                       statsmodels for Cox analysis.
     - drug_repurposing_summary.csv    One row per medication, with counts,
                                       median follow-up, ATC class.
     - cure_id_intake.csv              One row per (patient, medication)
                                       formatted to match the CURE ID
                                       Treatment Registry intake CRF
                                       (FDA / NCATS-NIH / Critical Path
                                       Institute), banded fields, no PHI.
     - drug_repurposing_report.html    Interactive HTML summary, same
                                       single-file format as cohort_eda.

Privacy: same conservative posture as cohort_eda. Patient identifiers are
pseudonymized to PT-NNNN, ages are banded to the CURE ID age groups
(<1 month, 1-3 months, ..., 81-89 years, 90+ years), dates are reported
as time-from-cohort-entry rather than calendar dates, and k-anonymity
suppression (default k=5) is applied to medication-level summaries.

This module is for hypothesis generation against the bundle data. It is
not a substitute for prospective clinical trials and not a substitute for
the rigorous propensity-score and Cox analysis that should follow.
"""

import os
import csv
import json
import re
import datetime as _dt
from collections import defaultdict, Counter

# ============================================================================
# CONFIG
# ============================================================================
DEFAULT_BUNDLE_PATH = './dashboard_data.json'
DEFAULT_OUT_DIR     = './drug_repurposing_output'

# Reimer 2026 thresholds
MIN_EXPOSED_PATIENTS = 30           # medications below this are dropped
CRITERION_A_PRE_MONTHS  = 0         # criterion A: dispense within 12 mo AFTER dx
CRITERION_A_POST_MONTHS = 12
CRITERION_A_MIN_DURATION_MONTHS = 6 # end must be at least 6 mo after dispense
CRITERION_B_PRE_MONTHS  = 6         # criterion B: 6 mo before to 12 mo after
CRITERION_B_POST_MONTHS = 12
CRITERION_B_MIN_DISPENSES = 2

# Privacy
DEFAULT_K_ANONYMITY = 5

# ============================================================================
# COHORT-DEFINING CODES — motor neuron disease spectrum
# ============================================================================
COHORT_CODES = {
    # ICD-10-CM
    ('ICD-10-CM', 'G12.21'): 'Amyotrophic lateral sclerosis',
    ('ICD-10-CM', 'G12.20'): 'Motor neuron disease, unspecified',
    ('ICD-10-CM', 'G12.22'): 'Progressive bulbar palsy',
    ('ICD-10-CM', 'G12.23'): 'Primary lateral sclerosis (adult)',
    ('ICD-10-CM', 'G12.29'): 'Other motor neuron disease',
    # ICD-9-CM (legacy, present in older records)
    ('ICD-9-CM',  '335.20'): 'Amyotrophic lateral sclerosis',
    ('ICD-9-CM',  '335.21'): 'Progressive muscular atrophy',
    ('ICD-9-CM',  '335.22'): 'Progressive bulbar palsy',
    ('ICD-9-CM',  '335.23'): 'Pseudobulbar palsy',
    ('ICD-9-CM',  '335.24'): 'Primary lateral sclerosis',
    ('ICD-9-CM',  '335.29'): 'Other motor neuron disease',
    # SNOMED CT
    ('SNOMED CT', '86044005'):  'Amyotrophic lateral sclerosis',
    ('SNOMED-CT', '86044005'):  'Amyotrophic lateral sclerosis',
    ('SNOMED',    '86044005'):  'Amyotrophic lateral sclerosis',
    ('SNOMED CT', '230258005'): 'Motor neuron disease',
    ('SNOMED-CT', '230258005'): 'Motor neuron disease',
    # Mondo — direct ALS or motor neuron disease IDs
    ('Mondo', 'MONDO:0004976'): 'Amyotrophic lateral sclerosis',
    ('Mondo', 'MONDO:0019056'): 'Motor neuron disease',
}

# ============================================================================
# RxNorm → ATC class seed mapping
# Seed covers the Reimer candidates and a few obvious extensions. Adopters
# extend this dict or supply an Athena vocabulary directory at runtime.
# ============================================================================
ATC_SEED = {
    # Statins — HMG-CoA reductase inhibitors (C10AA)
    'simvastatin':    ('C10AA01', 'HMG-CoA reductase inhibitors'),
    'lovastatin':     ('C10AA02', 'HMG-CoA reductase inhibitors'),
    'pravastatin':    ('C10AA03', 'HMG-CoA reductase inhibitors'),
    'atorvastatin':   ('C10AA05', 'HMG-CoA reductase inhibitors'),
    'rosuvastatin':   ('C10AA07', 'HMG-CoA reductase inhibitors'),
    'pitavastatin':   ('C10AA08', 'HMG-CoA reductase inhibitors'),
    'fluvastatin':    ('C10AA04', 'HMG-CoA reductase inhibitors'),

    # PDE5 inhibitors (G04BE)
    'sildenafil':     ('G04BE03', 'PDE5 inhibitors (drugs for erectile dysfunction)'),
    'vardenafil':     ('G04BE09', 'PDE5 inhibitors (drugs for erectile dysfunction)'),
    'tadalafil':      ('G04BE08', 'PDE5 inhibitors (drugs for erectile dysfunction)'),
    'avanafil':       ('G04BE10', 'PDE5 inhibitors (drugs for erectile dysfunction)'),

    # Alpha-adrenoreceptor antagonists (G04CA)
    'tamsulosin':     ('G04CA02', 'Alpha-adrenoreceptor antagonists'),
    'terazosin':      ('G04CA03', 'Alpha-adrenoreceptor antagonists'),
    'alfuzosin':      ('G04CA01', 'Alpha-adrenoreceptor antagonists'),
    'silodosin':      ('G04CA04', 'Alpha-adrenoreceptor antagonists'),
    'doxazosin':      ('C02CA04', 'Alpha-adrenoreceptor antagonists'),  # cardiovasc indication

    # Centrally acting muscle relaxants (M03BX) — Reimer reported as class-level
    'cyclobenzaprine':('M03BX08', 'Centrally acting muscle relaxants'),
    'baclofen':       ('M03BX01', 'Centrally acting muscle relaxants'),
    'tizanidine':     ('M03BX02', 'Centrally acting muscle relaxants'),

    # Reimer-identified individual drugs
    'riluzole':       ('N07XX02', 'Other nervous system drugs (motor neuron disease)'),
    'edaravone':      ('N07XX14', 'Other nervous system drugs (motor neuron disease)'),
    'tofersen':       ('N07XX17', 'Other nervous system drugs (SOD1 ALS)'),

    # Dextromethorphan-quinidine combination (FDA-approved for pseudobulbar
    # affect in ALS; trade name Nuedexta). Reimer 2026 reported this combo
    # AND standalone dextromethorphan separately, so we track both.
    'dextromethorphan-quinidine': ('N07XX59', 'Other nervous system drugs (pseudobulbar affect; brand name Nuedexta)'),
    'nuedexta':                   ('N07XX59', 'Other nervous system drugs (pseudobulbar affect; brand name for dextromethorphan-quinidine)'),
    # Standalone dextromethorphan — Reimer reported this as a separate harm-direction
    # signal under R05DA (cough suppressants / antitussives). Even though it is the
    # active CNS ingredient in Nuedexta, when prescribed alone it is almost always
    # an antitussive and should be tracked under that class.
    'dextromethorphan':           ('R05DA09', 'Opium alkaloid antitussive (also active CNS ingredient in Nuedexta)'),
    # Standalone quinidine — Class IA antiarrhythmic. In Nuedexta it is present at
    # subtherapeutic dose to inhibit CYP2D6 metabolism of dextromethorphan; when
    # prescribed alone at therapeutic doses it is an antiarrhythmic.
    'quinidine':                  ('C01BA01', 'Class IA antiarrhythmic (also CYP2D6-inhibitor ingredient in Nuedexta at subtherapeutic dose)'),

    'meloxicam':      ('M01AC06', 'Oxicam non-steroidal anti-inflammatory drug'),
    'colecalciferol': ('A11CC05', 'Vitamin D and analogues'),
    'cholecalciferol':('A11CC05', 'Vitamin D and analogues'),
    'rivaroxaban':    ('B01AF01', 'Direct factor Xa inhibitor (anticoagulant)'),
    'hydrochlorothiazide':('C03AA03','Thiazide diuretic'),
    'lisinopril':     ('C09AA03', 'ACE inhibitor'),

    # Reimer harmful-direction candidates (for full disclosure)
    'glycopyrrolate': ('A03AB02', 'Quaternary ammonium anticholinergic (for sialorrhea)'),
    'ondansetron':    ('A04AA01', '5HT3 antagonist (antiemetic)'),
    'fentanyl':       ('N02AB03', 'Opioid analgesic'),
    'morphine':       ('N02AA01', 'Opioid analgesic'),
    'oxycodone':      ('N02AA05', 'Opioid analgesic'),
    'hydromorphone':  ('N02AA03', 'Opioid analgesic'),
    'methadone':      ('N07BC02', 'Opioid analgesic / addiction treatment'),
    'gabapentin':     ('N02BF01', 'Gabapentinoid (neuropathic pain)'),
    'pseudoephedrine':('R01BA02', 'Decongestant'),
    'guaifenesin':    ('R05CA03', 'Mucolytic'),
    'cetirizine':     ('R06AE07', 'H1 antihistamine'),
    'salbutamol':     ('R03AC02', 'Short-acting beta agonist'),
    'albuterol':      ('R03AC02', 'Short-acting beta agonist'),
    'montelukast':    ('R03DC03', 'Leukotriene receptor antagonist'),
}

# ============================================================================
# Brand-name → generic-name resolution
# ----------------------------------------------------------------------------
# Real EHR data routinely names medications by brand (Lipitor, Flomax,
# Nuedexta, Robinul) rather than generic. This dictionary is consulted by
# _norm_med_name BEFORE the ATC_SEED lookup, so a record with display name
# "Lipitor 20 mg" gets normalized to "atorvastatin" and matches the
# atorvastatin entry in ATC_SEED. Brands that map to combination drugs
# (e.g. Nuedexta → dextromethorphan-quinidine) are also handled here.
#
# This list focuses on (1) ALS-approved drugs, (2) drugs Reimer 2026
# identified as protective or harm-direction, and (3) drugs commonly
# prescribed for ALS symptom management. Adopters extend this dict to
# cover brand names common in their site's EHR.
# ============================================================================
# REGULATORY STATUS — used to filter the CURE ID off-label-use export
# ============================================================================
# Medications with current FDA approval for an ALS indication. These are
# on-label for ALS and therefore NOT off-label-use candidates for CURE ID.
ALS_FDA_APPROVED = {
    'riluzole',
    'edaravone',
    'tofersen',
}

# Medications previously FDA-approved for ALS but since withdrawn from the
# US market. Excluded from the off-label-use export because they were
# initially approved on-label; any continuing use is a separate regulatory
# situation (compassionate use, ongoing supply, etc.), not a repurposing
# candidate. Listed here for documentation and to keep the script's
# decision-making transparent.
ALS_FDA_WITHDRAWN = {
    # Brand name Relyvrio / Albrioza; withdrawn from market in 2024 after
    # the PHOENIX confirmatory trial did not meet its primary endpoint.
    'sodium phenylbutyrate-taurursodiol',
}


# ============================================================================
# ALS CLINICAL-INTEREST MEDICATIONS  (always shown in the dashboard panel)
# ============================================================================
# Medications that always appear in the dashboard's clinical-interest panel
# regardless of whether they meet `min_exposed`. Patient counts are still
# k-anonymized; values below k are reported as "< k" rather than the exact
# number. Year ranges are always shown when any record exists, since first-
# and last-prescription year is clinically informative on its own (e.g.
# Relyvrio prescriptions clustering 2022-2024 around the FDA approval and
# subsequent withdrawal).
#
# Each entry is a dict for readability and forward-compatibility.
ALS_CLINICAL_INTEREST = [
    {
        'med_key':    'riluzole',
        'display':    'Riluzole',
        'brands':     'Rilutek, Tiglutik, Exservan',
        'atc':        'N07XX02',
        'fda_status': 'approved',
        'label':      'FDA-approved for ALS (1995)',
    },
    {
        'med_key':    'edaravone',
        'display':    'Edaravone',
        'brands':     'Radicava, Radicava ORS',
        'atc':        'N07XX14',
        'fda_status': 'approved',
        'label':      'FDA-approved for ALS (2017)',
    },
    {
        'med_key':    'tofersen',
        'display':    'Tofersen',
        'brands':     'Qalsody',
        'atc':        'N07XX17',
        'fda_status': 'approved',
        'label':      'FDA-approved for SOD1 ALS (2023, accelerated)',
    },
    {
        'med_key':    'dextromethorphan-quinidine',
        'display':    'Dextromethorphan-quinidine',
        'brands':     'Nuedexta',
        'atc':        'N07XX59',
        'fda_status': 'off_label_for_als',
        'label':      'FDA-approved for PBA; off-label-of-interest in ALS',
    },
    {
        'med_key':    'sodium phenylbutyrate-taurursodiol',
        'display':    'Sodium phenylbutyrate-taurursodiol',
        'brands':     'Relyvrio, Albrioza',
        'atc':        '\u2014',
        'fda_status': 'withdrawn',
        'label':      'FDA-approved for ALS 2022; withdrawn 2024 after PHOENIX',
    },
]


# ============================================================================
# COMBINATION-DRUG DETECTION RULES
# ============================================================================
# Each rule: (set of substrings that must ALL be present, case-insensitive,
# in the raw lowercased medication name) -> normalized generic name to return.
#
# These rules run inside _norm_med_name BEFORE the dosage-stripping regex.
# That order is critical: dosage stripping cuts the name at the first
# "<N> mg" match and drops everything after it, which silently loses the
# second ingredient of any combination. For example:
#
#   "dextromethorphan hydrobromide 20 MG / quinidine sulfate 10 MG Oral Capsule"
#     after dose strip ->  "dextromethorphan hydrobromide"
#     first token      ->  "dextromethorphan"
#     ATC lookup       ->  R05DA09  (cough suppressant)   ← WRONG
#
# With combination pre-detection both ingredients are seen first, and the
# string resolves correctly to N07XX59 (Nuedexta / dextromethorphan-quinidine,
# FDA-approved for pseudobulbar affect).
#
# Add new rules here as more combination drugs become relevant. The ALS-
# relevant combinations currently covered:
#   - Nuedexta              (dextromethorphan HBr 20 mg + quinidine sulfate 10 mg)
#   - Relyvrio / Albrioza   (sodium phenylbutyrate + taurursodiol;
#                            FDA-approved for ALS 2022, withdrawn 2024)
COMBINATION_RULES = [
    # Nuedexta — FDA-approved for pseudobulbar affect; off-label-of-interest in ALS
    (frozenset({'nuedexta'}),                                  'dextromethorphan-quinidine'),
    (frozenset({'dextromethorphan', 'quinidine'}),             'dextromethorphan-quinidine'),
    # Relyvrio / Albrioza — FDA-approved for ALS 2022, withdrawn 2024
    (frozenset({'relyvrio'}),                                  'sodium phenylbutyrate-taurursodiol'),
    (frozenset({'albrioza'}),                                  'sodium phenylbutyrate-taurursodiol'),
    (frozenset({'phenylbutyrate', 'taurursodiol'}),            'sodium phenylbutyrate-taurursodiol'),
    (frozenset({'phenylbutyrate', 'tudca'}),                   'sodium phenylbutyrate-taurursodiol'),
]


# ============================================================================
BRAND_TO_GENERIC = {
    # ALS FDA-approved
    'rilutek':                          'riluzole',
    'tiglutik':                         'riluzole',
    'exservan':                         'riluzole',
    'radicava':                         'edaravone',
    'radicava ors':                     'edaravone',
    'qalsody':                          'tofersen',
    'nuedexta':                         'dextromethorphan-quinidine',
    'relyvrio':                         'sodium phenylbutyrate-taurursodiol',
    'albrioza':                         'sodium phenylbutyrate-taurursodiol',

    # Statins (Reimer-protective)
    'lipitor':                          'atorvastatin',
    'crestor':                          'rosuvastatin',
    'zocor':                            'simvastatin',
    'mevacor':                          'lovastatin',
    'altoprev':                         'lovastatin',
    'pravachol':                        'pravastatin',
    'livalo':                           'pitavastatin',
    'zypitamag':                        'pitavastatin',
    'lescol':                           'fluvastatin',

    # PDE5 inhibitors (Reimer-protective)
    'viagra':                           'sildenafil',
    'revatio':                          'sildenafil',
    'levitra':                          'vardenafil',
    'staxyn':                           'vardenafil',
    'cialis':                           'tadalafil',
    'adcirca':                          'tadalafil',
    'stendra':                          'avanafil',

    # Alpha-adrenoreceptor antagonists (Reimer-protective)
    'flomax':                           'tamsulosin',
    'hytrin':                           'terazosin',
    'uroxatral':                        'alfuzosin',
    'rapaflo':                          'silodosin',
    'cardura':                          'doxazosin',

    # Centrally acting muscle relaxants (Reimer-protective)
    'flexeril':                         'cyclobenzaprine',
    'amrix':                            'cyclobenzaprine',
    'fexmid':                           'cyclobenzaprine',
    'zanaflex':                         'tizanidine',
    'lioresal':                         'baclofen',
    'gablofen':                         'baclofen',
    'ozobax':                           'baclofen',

    # Sialorrhea / ALS symptom management (Reimer harm-direction)
    'robinul':                          'glycopyrrolate',
    'cuvposa':                          'glycopyrrolate',
    'glycate':                          'glycopyrrolate',
    'dartisla':                         'glycopyrrolate',

    # Antiemetics (Reimer harm-direction)
    'zofran':                           'ondansetron',
    'zuplenz':                          'ondansetron',

    # Opioids (Reimer harm-direction; end-of-life ALS)
    'sublimaze':                        'fentanyl',
    'duragesic':                        'fentanyl',
    'actiq':                            'fentanyl',
    'fentora':                          'fentanyl',
    'abstral':                          'fentanyl',
    'subsys':                           'fentanyl',
    'lazanda':                          'fentanyl',

    # Gabapentinoids (Reimer harm-direction)
    'neurontin':                        'gabapentin',
    'gralise':                          'gabapentin',
    'horizant':                         'gabapentin',

    # OTC cough / cold (Reimer harm-direction)
    'sudafed':                          'pseudoephedrine',
    'mucinex':                          'guaifenesin',
    'robitussin':                       'guaifenesin',

    # Antihistamines (Reimer harm-direction)
    'zyrtec':                           'cetirizine',
    'reactine':                         'cetirizine',

    # Bronchodilators (Reimer harm-direction)
    'ventolin':                         'salbutamol',
    'proventil':                        'salbutamol',
    'proair':                           'salbutamol',
    'accuneb':                          'salbutamol',
    'xopenex':                          'levalbuterol',

    # Leukotriene receptor antagonists (Reimer harm-direction)
    'singulair':                        'montelukast',

    # ACE inhibitor brands (for the lisinopril seed entry)
    'prinivil':                         'lisinopril',
    'zestril':                          'lisinopril',
    'qbrelis':                          'lisinopril',

    # Anticoagulants
    'xarelto':                          'rivaroxaban',

    # Vitamin D — many alternate spellings
    'cholecalciferol':                  'colecalciferol',
    'vitamin d3':                       'colecalciferol',
    'vitamin d':                        'colecalciferol',
    'd3':                               'colecalciferol',
}

# ============================================================================
# CURE ID age band mapping (from the Cure ID intake CRF, Generic Other Disease)
# ============================================================================
CURE_ID_AGE_BANDS = [
    (0,         1/12,   '<1 month'),
    (1/12,      3/12,   '1 - 3 months'),
    (3/12,      6/12,   '4 - 6 months'),
    (6/12,      11/12,  '7 - 11 months'),
    (1,         5,      '1 - 5 years'),
    (6,        10,      '6 - 10 years'),
    (11,       15,      '11 - 15 years'),
    (16,       20,      '16 - 20 years'),
    (21,       30,      '21 - 30 years'),
    (31,       40,      '31 - 40 years'),
    (41,       50,      '41 - 50 years'),
    (51,       60,      '51 - 60 years'),
    (61,       70,      '61 - 70 years'),
    (71,       80,      '71 - 80 years'),
    (81,       89,      '81 - 89 years'),
    (90,      150,      '90+ years'),
]

CURE_ID_RACE_MAP = {
    # Map ARC pipeline race display strings to Cure ID race options
    'Asian':                                  'Asian',
    'Black or African American':              'Black or African American',
    'White':                                  'White',
    'Native Hawaiian or Other Pacific Islander': 'Native Hawaiian or Other Pacific Islander',
    'American Indian or Alaska Native':       'American Indian or Alaska Native',
    'Other Race':                             'Other',
}


# ============================================================================
# UTILITIES
# ============================================================================
def log(msg):
    print(msg, flush=True)


def _parse_iso_date(s):
    """Parse an ISO date / datetime string into a date. Returns None on failure."""
    if not s: return None
    s = str(s).strip()
    if not s: return None
    # Try several common formats
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S.%fZ',
                '%Y/%m/%d', '%m/%d/%Y'):
        try:
            return _dt.datetime.strptime(s.split('+')[0], fmt).date()
        except ValueError:
            continue
    # Last resort: year-only
    m = re.match(r'^(\d{4})', s)
    if m:
        try: return _dt.date(int(m.group(1)), 1, 1)
        except ValueError: return None
    return None


def _months_between(d1, d2):
    """Approximate months between two dates."""
    if d1 is None or d2 is None: return None
    return (d2 - d1).days / 30.44


def _norm_med_name(name):
    """Normalize medication name for ATC lookup.

    Lowercases, strips dosage and formulation suffixes, then consults
    BRAND_TO_GENERIC to resolve brand names (Lipitor, Nuedexta, Flomax) to
    their generic equivalents so brand-named EHR records match the
    ATC_SEED keys (which use generic names).

    For combination drugs (e.g. dextromethorphan-quinidine), the slash
    and hyphen are normalized so 'dextromethorphan/quinidine' and
    'dextromethorphan-quinidine' both resolve to the same string. For
    these multi-token combo names we keep the full hyphenated form;
    otherwise we take the first space-separated token.

    Combination drugs in RxNorm SCD form (e.g. "dextromethorphan
    hydrobromide 20 MG / quinidine sulfate 10 MG Oral Capsule [Nuedexta]")
    are detected by COMBINATION_RULES BEFORE the dosage-stripping step,
    which would otherwise truncate at "20 MG" and lose the second
    ingredient, causing Nuedexta to be misclassified as standalone
    dextromethorphan (R05DA09 cough suppressant) instead of the PBA
    combination (N07XX59). This was a real bug in earlier versions.
    """
    if not name: return ''
    s = str(name).lower().strip()

    # === Combination-drug pre-detection. MUST happen before dosage stripping.
    for rule_words, generic_name in COMBINATION_RULES:
        if all(w in s for w in rule_words):
            return generic_name

    # Strip common suffixes (dosage, formulation)
    s = re.sub(r'\b\d+\s*(?:mg|mcg|µg|g|ml|meq|units?|iu)\b.*$', '', s, flags=re.I)
    s = re.sub(r'\b(?:tablet|capsule|injection|oral|solution|suspension|er|xr|sr|cr|extended[\s-]release).*$', '', s, flags=re.I)
    # Normalize slash to hyphen so 'dextromethorphan/quinidine' becomes
    # 'dextromethorphan-quinidine'. This must happen BEFORE the first-token
    # split or we'd lose the second ingredient.
    s = re.sub(r'\s*/\s*', '-', s)
    s = re.sub(r'\s+', ' ', s).strip()

    # Brand-name resolution: if the lowercased name matches a brand
    # (either as full string or as the first token), substitute the generic.
    # Try full-string match first to catch multi-word brands like "radicava ors".
    if s in BRAND_TO_GENERIC:
        return BRAND_TO_GENERIC[s]
    # Then try first-token match for "lipitor 20 mg-fragment-leftover" cases.
    first = s.split(' ')[0].strip() if ' ' in s else s
    if first in BRAND_TO_GENERIC:
        return BRAND_TO_GENERIC[first]

    # For known combination generic names (presence of a hyphen and both
    # halves are recognizable generics), keep the full hyphenated form so
    # 'dextromethorphan-quinidine' doesn't get truncated to 'dextromethorphan'.
    if '-' in first and first in ATC_SEED:
        return first

    # Otherwise return the first token (matches single-ingredient drugs).
    return first


def _age_band(age_years):
    if age_years is None: return 'Unknown'
    for low, high, label in CURE_ID_AGE_BANDS:
        if low <= age_years < high + (1 if high < 1 else 0.99):
            return label
    return 'Unknown'


def _to_pseudo_id(patient_id, mapping):
    """Convert real patient_id to PT-NNNN form (stable per-run)."""
    if patient_id not in mapping:
        mapping[patient_id] = f'PT-{len(mapping)+1:04d}'
    return mapping[patient_id]


# ============================================================================
# COHORT ASSEMBLY
# ============================================================================
def _coding_system_name(c):
    """Return the system name for a coding dict, trying every shape that
    run_pipeline.py / FHIR / synthetic test bundles can emit.

    run_pipeline.py emits codings as {'code', 'system_oid', 'system_name',
    'display'}, with system_name being a human label like 'ICD-10-CM',
    'SNOMED-CT', 'RxNorm'. Some synthetic fixtures or older bundles use
    plain 'system'. We fall through all three.
    """
    return ((c.get('system_name') or c.get('system') or '').strip() or
            OID_TO_NAME.get(c.get('system_oid', ''), ''))

# Minimal OID-to-name fallback for cases where only system_oid is populated
OID_TO_NAME = {
    '2.16.840.1.113883.6.90': 'ICD-10-CM',
    '2.16.840.1.113883.6.103': 'ICD-9-CM',
    '2.16.840.1.113883.6.96': 'SNOMED-CT',
    '2.16.840.1.113883.6.88': 'RxNorm',
    '2.16.840.1.113883.6.1':  'LOINC',
    '2.16.840.1.113883.6.12': 'CPT-4',
    '2.16.840.1.113883.6.285':'HCPCS',
}


# ============================================================================
def identify_cohort(bundle, verbose=True):
    """Walk problems[] and return {patient_id: cohort_entry_date} for every
    patient with at least one motor-neuron-disease problem code.

    Uses a robust system-name reading (system_name / system / OID-derived)
    that matches the shape run_pipeline.py emits. Also accepts the top-level
    code_system field on problem rows for FHIR-derived records that ended
    up with an empty all_codings[] but have the prioritized code/system on
    the row itself.
    """
    cohort = {}
    n_problems = 0
    n_matched_codes = 0
    n_unparseable_dates = 0
    matched_systems = Counter()
    for prob in bundle.get('problems', []):
        n_problems += 1
        codings = prob.get('all_codings') or []
        # Also synthesize a coding from the prioritized top-level fields if
        # all_codings is empty (FHIR rows sometimes end up this way).
        if not codings and prob.get('code'):
            codings = [{
                'code': prob.get('code'),
                'system_name': prob.get('code_system') or prob.get('system_name') or '',
                'system_oid':  prob.get('system_oid') or '',
                'display':     prob.get('display_name') or prob.get('display') or '',
            }]
        for c in codings:
            sysname = _coding_system_name(c)
            code = (c.get('code') or '').strip()
            key = (sysname, code)
            if key in COHORT_CODES:
                n_matched_codes += 1
                matched_systems[sysname] += 1
                pid = prob.get('patient_id')
                if not pid: continue
                d = _parse_iso_date(prob.get('effective_date') or prob.get('onset_date') or prob.get('recorded_date'))
                if d is None:
                    n_unparseable_dates += 1
                    continue
                if pid not in cohort or d < cohort[pid]:
                    cohort[pid] = d
                break  # one match per problem is enough
    if verbose:
        print(f'  identify_cohort: scanned {n_problems} problem rows')
        print(f'  identify_cohort: matched {n_matched_codes} motor-neuron-disease codes')
        if matched_systems:
            for sys_, n in matched_systems.most_common():
                print(f'                     by system: {sys_}: {n}')
        if n_unparseable_dates:
            print(f'  identify_cohort: dropped {n_unparseable_dates} matches with unparseable dates')
        print(f'  identify_cohort: identified {len(cohort)} unique cohort patients')
    return cohort


def gather_patient_demographics(bundle):
    """Return {patient_id: {sex, race, ethnicity, dob, marital_status}}."""
    demo = {}
    for p in bundle.get('patients', []):
        pid = p.get('patient_id') or p.get('id')
        if not pid: continue
        demo[pid] = {
            'sex':            (p.get('gender') or p.get('sex') or '').strip() or 'Unknown',
            'race':           (p.get('race') or '').strip() or 'Unknown',
            'ethnicity':      (p.get('ethnicity') or '').strip() or 'Unknown',
            'dob':            _parse_iso_date(p.get('birth_date') or p.get('dob')),
            'marital_status': (p.get('marital_status') or {}).get('display', '') if isinstance(p.get('marital_status'), dict) else (p.get('marital_status') or '').strip(),
            'deceased_date':  _parse_iso_date(p.get('deceased_date_time') or p.get('death_date')),
        }
    return demo


def collect_medication_events(bundle, verbose=True):
    """Return {patient_id: [(med_name_norm, raw_display, start_date, end_date), ...]}.

    Walks medications[] and normalizes each display name. Logs how many
    records resolved to a known ATC class (so empty cohort output explains
    itself).
    """
    events = defaultdict(list)
    n_rows = 0
    n_with_pid = 0
    n_normed = 0
    n_with_date = 0
    n_with_end_date = 0
    n_atc_matched = 0
    unmatched_norms = Counter()
    for m in bundle.get('medications', []):
        n_rows += 1
        pid = m.get('patient_id')
        if not pid: continue
        n_with_pid += 1
        display_raw = m.get('display_name') or m.get('display') or ''
        med_name = _norm_med_name(display_raw)
        if not med_name: continue
        n_normed += 1
        start = _parse_iso_date(m.get('effective_date') or m.get('start_date') or m.get('authored_on'))
        end   = _parse_iso_date(m.get('end_date'))
        if start is not None: n_with_date += 1
        if end   is not None: n_with_end_date += 1
        if med_name in ATC_SEED:
            n_atc_matched += 1
        else:
            unmatched_norms[med_name] += 1
        events[pid].append((med_name, display_raw, start, end))
    if verbose:
        print(f'  collect_medication_events: scanned {n_rows} medication rows')
        print(f'                              {n_with_pid} had patient_id')
        print(f'                              {n_normed} normalized to a non-empty name')
        print(f'                              {n_with_date} had a parseable start date')
        print(f'                              {n_with_end_date} had a parseable end_date')
        print(f'                              {n_atc_matched} matched an ATC_SEED entry')
        if n_with_end_date == 0 and n_rows > 0:
            print(f'  NOTE: no medication rows have end_date. Reimer Criterion A '
                  f'(needs end_date) will never fire. Use criterion=\'any\' (default) '
                  f'for C-CDA / FHIR data.')
        if unmatched_norms:
            top_unmatched = unmatched_norms.most_common(15)
            print(f'  Top 15 unmatched medication names (extend ATC_SEED / BRAND_TO_GENERIC to cover):')
            for name, n in top_unmatched:
                print(f'    {n:5d}  {name!r}')
    return events


def apply_exposure_criteria(patient_meds, cohort_entry, criterion='any'):
    """Apply exposure criteria per medication.

    Parameters
    ----------
    patient_meds : list of (med_name, raw_display, start_date, end_date) tuples
    cohort_entry : date
    criterion : str
        'any'      (default) Criterion C - any medication record with start_date
                   in [-6, +12] months of cohort entry. Fits C-CDA / FHIR data
                   where each record is one prescription event and end_date is
                   typically missing. This is the right default for ARC-shaped
                   data and most patient-directed SMART on FHIR registries.
        'reimer-a' Strict Reimer Criterion A only - start or end within 12mo
                   of cohort entry AND end >= 6mo after start. Needs end_date
                   so only fits VHA-style or dispense-event data.
        'reimer-b' Strict Reimer Criterion B only - >=2 dispenses in window
                   [-6, +12] months. Needs multiple rows per medication so
                   only fits dispense-event data.
        'reimer'   Either Reimer A or B. Needs end_date or multiple rows.

    Returns {med_name: {'exposed', 'criterion_met', 'first_dispense',
                        'last_dispense', 'n_dispenses_window', 'raw_display'}}.
    """
    by_med = defaultdict(list)
    for med, raw, start, end in patient_meds:
        by_med[med].append((raw, start, end))

    out = {}
    for med, events in by_med.items():
        # Sort by start date (None at end)
        events_sorted = sorted(events, key=lambda e: e[1] or _dt.date.max)
        starts = [e[1] for e in events_sorted if e[1] is not None]
        first_dispense = starts[0] if starts else None
        last_dispense  = starts[-1] if starts else None
        criterion_met = None

        # Criterion C ('any'): at least one record with start in [-6,+12] mo of entry
        c_count = 0
        for raw, start, end in events_sorted:
            if start is None: continue
            d = _months_between(cohort_entry, start)
            if d is None: continue
            if -CRITERION_B_PRE_MONTHS <= d <= CRITERION_B_POST_MONTHS:
                c_count += 1
        c_met = c_count >= 1

        # Criterion A (Reimer): dispense OR end within 12mo of cohort entry,
        # AND end at least 6mo after dispense
        a_met = False
        if criterion in ('reimer-a', 'reimer'):
            for raw, start, end in events_sorted:
                d_after_entry_start = _months_between(cohort_entry, start) if start else None
                d_after_entry_end   = _months_between(cohort_entry, end)   if end else None
                within = (d_after_entry_start is not None and 0 <= d_after_entry_start <= CRITERION_A_POST_MONTHS) or \
                         (d_after_entry_end   is not None and 0 <= d_after_entry_end   <= CRITERION_A_POST_MONTHS)
                duration_ok = (start is not None and end is not None and
                               _months_between(start, end) is not None and
                               _months_between(start, end) >= CRITERION_A_MIN_DURATION_MONTHS)
                if within and duration_ok:
                    a_met = True
                    break

        # Criterion B (Reimer): >=2 dispenses within window
        b_met = False
        if criterion in ('reimer-b', 'reimer'):
            b_met = c_count >= CRITERION_B_MIN_DISPENSES

        # Determine which criterion fired based on mode
        if criterion == 'any':
            if c_met: criterion_met = 'C'
        elif criterion == 'reimer-a':
            if a_met: criterion_met = 'A'
        elif criterion == 'reimer-b':
            if b_met: criterion_met = 'B'
        elif criterion == 'reimer':
            if a_met and b_met:   criterion_met = 'A+B'
            elif a_met:           criterion_met = 'A'
            elif b_met:           criterion_met = 'B'

        out[med] = {
            'exposed':            bool(criterion_met),
            'criterion_met':      criterion_met,
            'first_dispense':     first_dispense,
            'last_dispense':      last_dispense,
            'n_dispenses_window': c_count,
            'raw_display':        events_sorted[0][0] if events_sorted else '',
        }
    return out


def assemble_cohort_table(cohort, demo, meds_per_patient, min_exposed, criterion='any',
                          verbose=True, pseudonymize=True):
    """Build the long-form per-(patient, medication) cohort table.

    When pseudonymize=True (default), patient_id values in the output rows are
    replaced with stable PT-NNNN pseudonyms. Raw patient_id values from EHR
    extracts (Epic patient IDs, FHIR resource IDs derived from Epic, MRNs, etc.)
    are HIPAA direct identifiers under Safe Harbor §164.514(b)(2)(i)(H) and
    should not appear in any output that may be shared beyond the IRB-approved
    circle. Setting pseudonymize=False emits the raw identifiers and is only
    appropriate for transient internal analyses where downstream chart-review
    linkage is needed; the resulting CSV must then be stored alongside PHI.
    """
    # First pass: which medications meet min_exposed across the cohort
    med_exposed_count = Counter()
    per_patient_results = {}
    n_cohort_with_any_med = 0
    for pid, entry_date in cohort.items():
        meds = meds_per_patient.get(pid, [])
        if meds: n_cohort_with_any_med += 1
        results = apply_exposure_criteria(meds, entry_date, criterion=criterion)
        per_patient_results[pid] = results
        for med, r in results.items():
            if r['exposed']:
                med_exposed_count[med] += 1

    keep_meds = {m for m, n in med_exposed_count.items() if n >= min_exposed}

    # Build the pseudonym mapping if requested. Stable per-run: the first
    # patient encountered gets PT-0001, the next gets PT-0002, etc.
    pseudo_map = {}
    if pseudonymize:
        for pid in sorted(cohort.keys()):
            _to_pseudo_id(pid, pseudo_map)
        if verbose:
            print(f'  assemble_cohort_table: pseudonymize=True - {len(pseudo_map)} '
                  f'patient_id values will be replaced with PT-NNNN in cohort CSV')

    if verbose:
        print(f'  assemble_cohort_table: criterion={criterion!r}')
        print(f'                          {n_cohort_with_any_med} of {len(cohort)} cohort patients had any medication record')
        print(f'                          {len(med_exposed_count)} unique medications had >=1 exposed cohort patient')
        if med_exposed_count:
            print(f'                          top exposed medications across cohort:')
            for med, n in med_exposed_count.most_common(10):
                marker = '  *KEPT*' if med in keep_meds else f'  (dropped, < min_exposed={min_exposed})'
                print(f'                            {n:4d} patients  {med}  {marker}')
        print(f'                          {len(keep_meds)} medications meet min_exposed={min_exposed}')
        if not keep_meds and med_exposed_count:
            top = med_exposed_count.most_common(1)[0]
            print(f'  HINT: top medication has {top[1]} exposed patients (need >= {min_exposed}). '
                  f'Try drug_repurposing.main(..., min_exposed={max(1, top[1])}) to see signal.')

    rows = []
    today = _dt.date.today()
    for pid, entry_date in cohort.items():
        d = demo.get(pid, {})
        age_at_entry = None
        if d.get('dob') and entry_date:
            age_at_entry = (entry_date - d['dob']).days / 365.25
        survival_days = None
        event = 0  # 0 = censored, 1 = death observed
        if d.get('deceased_date'):
            survival_days = (d['deceased_date'] - entry_date).days
            event = 1
        else:
            survival_days = (today - entry_date).days  # censored at today

        results = per_patient_results.get(pid, {})
        for med in keep_meds:
            r = results.get(med, {'exposed': False, 'criterion_met': None,
                                  'first_dispense': None, 'last_dispense': None,
                                  'n_dispenses_window': 0, 'raw_display': ''})
            atc_code, atc_class = ATC_SEED.get(med, ('UNCLASSIFIED', 'Not in ATC seed mapping'))
            out_pid = pseudo_map.get(pid, pid) if pseudonymize else pid
            rows.append({
                'patient_id':     out_pid,
                'cohort_entry':   entry_date.isoformat() if entry_date else '',
                'age_at_entry':   round(age_at_entry, 1) if age_at_entry else None,
                'sex':            d.get('sex', 'Unknown'),
                'race':           d.get('race', 'Unknown'),
                'ethnicity':      d.get('ethnicity', 'Unknown'),
                'marital_status': d.get('marital_status', ''),
                'medication':     med,
                'medication_display': r.get('raw_display', ''),
                'atc_code':       atc_code,
                'atc_class':      atc_class,
                'exposed':        1 if r['exposed'] else 0,
                'criterion':      r.get('criterion_met') or '',
                'n_dispenses_window': r.get('n_dispenses_window', 0),
                'first_dispense': r['first_dispense'].isoformat() if r.get('first_dispense') else '',
                'survival_days':  survival_days,
                'death_observed': event,
                'deceased_date':  d.get('deceased_date').isoformat() if d.get('deceased_date') else '',
            })
    return rows, keep_meds


# ============================================================================
# SOURCE-RECORD SNIPPETS — privacy-safe traceback to the EHR
# ============================================================================
# For each off-label medication surfaced in the CURE ID export, walk the
# bundle once more to pull a small set of representative source records
# so adopters can verify findings, prepare for chart review, or paste
# evidence into a manuscript Discussion section. All output is
# privacy-safe by construction: patient IDs are pseudonymized, dates are
# reduced to year only, free-text fields are truncated, resource UUIDs
# are never emitted, and drugs with fewer than k exposed patients are
# suppressed entirely (k-anonymity at the drug level).
SNIPPET_DEFAULT_TARGETS = [
    # Symptom-management mainstays for ALS
    'baclofen', 'tizanidine', 'cyclobenzaprine',           # antispasticity
    'gabapentin', 'pregabalin',                            # neuropathic pain
    'amitriptyline', 'nortriptyline',                      # sialorrhea, mood, pain
    'ondansetron',                                         # antiemetic (edaravone-related)
    'glycopyrrolate', 'scopolamine',                       # sialorrhea
    'morphine', 'oxycodone', 'hydromorphone', 'fentanyl',  # palliative analgesia
    'methadone',
    # Symptom-management adjuncts
    'colecalciferol', 'cholecalciferol', 'vitamin d',      # vitamin D supplementation
    'albuterol',                                           # respiratory
    # ALS-PBA (FDA-approved for PBA; off-label for ALS itself)
    'dextromethorphan-quinidine', 'nuedexta',
    # Reimer-2026 protective-direction candidates worth checking
    'atorvastatin', 'rosuvastatin', 'simvastatin', 'pravastatin',
    'lovastatin', 'pitavastatin', 'fluvastatin',
    'metformin',
    'sertraline', 'fluoxetine', 'citalopram', 'escitalopram',
]


def _snippet_year_only(date_str):
    """Reduce any date to year only for snippet output."""
    if not date_str:
        return 'unknown'
    m = re.match(r'(\d{4})', str(date_str))
    return m.group(1) if m else 'unknown'


def _snippet_truncate(s, n=120):
    if not s:
        return ''
    s = str(s).strip()
    return s if len(s) <= n else s[:n-1] + '…'


def _snippet_matches(display, code, target):
    """Substring match on display name or code, case-insensitive."""
    t = target.lower()
    if display and t in display.lower():
        return True
    if code and t in str(code).lower():
        return True
    return False


def inspect_off_label_records(bundle, targets=None,
                               max_examples=5,
                               min_records_to_show=2):
    """Walk every medication record in the bundle and pull representative
    examples for each target drug. Returns
        {target_name: {'examples': [..], 'n_patients': N, 'n_records': M}}
    with privacy controls already applied.

    Parameters
    ----------
    bundle : dict
        Parsed dashboard_data.json.
    targets : iterable[str] or None
        Drug names to search for. Defaults to SNIPPET_DEFAULT_TARGETS.
    max_examples : int
        Max example records to emit per drug.
    min_records_to_show : int
        Drugs with fewer than this many unique exposed patients are
        suppressed (k-anonymity).
    """
    if targets is None:
        targets = SNIPPET_DEFAULT_TARGETS
    cache_pseudo = {}
    by_target = defaultdict(list)
    per_target_pt = defaultdict(set)

    meds = bundle.get('medications', [])
    if isinstance(meds, dict):
        flat = []
        for pid, recs in meds.items():
            for r in recs:
                if 'patient_id' not in r:
                    r = {**r, 'patient_id': pid}
                flat.append(r)
        meds = flat

    for rec in meds:
        display = rec.get('display_name') or rec.get('display') or ''
        code = rec.get('code') or ''
        for target in targets:
            if _snippet_matches(display, code, target):
                pid = rec.get('patient_id') or rec.get('subject') or ''
                pseudo = _to_pseudo_id(pid, cache_pseudo)
                example = {
                    'pseudo_patient_id': pseudo,
                    'display_name':      _snippet_truncate(display),
                    'vocabulary':        _snippet_truncate(
                        rec.get('system_name') or rec.get('system') or '', 60),
                    'code':              _snippet_truncate(code, 40),
                    'year':              _snippet_year_only(
                        rec.get('effective_date') or rec.get('authored_on')
                        or rec.get('date')),
                    'source_format':     rec.get('source') or 'unknown',
                    'status':            rec.get('status') or '',
                }
                by_target[target].append(example)
                per_target_pt[target].add(pseudo)
                break  # one record matches one target; don't double-count

    out = {}
    for target, examples in by_target.items():
        if len(per_target_pt[target]) >= min_records_to_show:
            seen_pts = set()
            kept = []
            for ex in examples:
                if ex['pseudo_patient_id'] not in seen_pts:
                    kept.append(ex)
                    seen_pts.add(ex['pseudo_patient_id'])
                if len(kept) >= max_examples:
                    break
            out[target] = {
                'examples':   kept,
                'n_patients': len(per_target_pt[target]),
                'n_records':  len(examples),
            }
    return out


def render_off_label_snippets_markdown(snippets,
                                        cohort_label='this cohort'):
    """Render the snippet dict as a markdown report ready to drop into a
    manuscript or docs page."""
    out = []
    out.append('# Source-record snippets for off-label medications')
    out.append('')
    out.append(f'Representative records from **{cohort_label}**, drawn '
               f'directly from the parsed EHR bundle. Patient identifiers '
               f'are pseudonymized (PT-NNNN), calendar dates are reduced to '
               f'year only, free-text fields are truncated, and drugs with '
               f'fewer than 2 unique exposed patients are suppressed. The '
               f'snippet is intended for source verification and chart-'
               f'review preparation, not for analysis.')
    out.append('')
    if not snippets:
        out.append('*No qualifying records found.*')
        return '\n'.join(out)

    for target, info in sorted(snippets.items(),
                                key=lambda kv: -kv[1]['n_patients']):
        out.append(f'## {target}')
        out.append('')
        out.append(f'**{info["n_patients"]} unique patients · '
                   f'{info["n_records"]} total records** '
                   f'(showing up to 5 examples)')
        out.append('')
        out.append('| Patient | Display name in source | Vocabulary | Code | Year | Source format |')
        out.append('|---|---|---|---|---|---|')
        for ex in info['examples']:
            row = ('| ' + ' | '.join([
                ex['pseudo_patient_id'],
                ex['display_name'] or '*(no display)*',
                ex['vocabulary'] or '—',
                ex['code'] or '—',
                ex['year'],
                ex['source_format'],
            ]) + ' |')
            out.append(row)
        out.append('')
    return '\n'.join(out)


# ============================================================================
# CURE ID EXPORT
# ============================================================================
def cure_id_intake_rows(cohort_rows,
                        exposed_only=True,
                        off_label_only=True,
                        k=1):
    """Build CURE ID intake rows. One row per exposed (patient, medication).

    CURE ID is a clinical-evidence repository for off-label and
    repurposed drug use in difficult-to-treat conditions. The intake form
    here is therefore framed as: "every occasion of off-label drug use
    observed in our ALS cohort's EHR data," not as a survival-signal
    analysis. Each (patient, medication) pair where the medication is
    not FDA-approved for ALS becomes one row.

    Parameters
    ----------
    exposed_only : bool
        If True, only emit rows for patient-medication pairs where the
        medication was actually used (the unexposed-control rows from
        the cohort-construction step are not relevant to CURE ID intake).
    off_label_only : bool
        If True (the default), exclude medications currently FDA-approved
        for ALS (riluzole, edaravone, tofersen) and those previously
        FDA-approved but since withdrawn (sodium phenylbutyrate-
        taurursodiol / Relyvrio). The result is a clean off-label-use
        report. Set to False to dump every medication regardless of
        regulatory status (useful for QC).
    k : int
        Medication-level k-anonymity threshold. CURE ID intake is a
        case-report format; defaults to 1 (report every occasion). Raise
        only if the export's intended audience requires further
        aggregation.

    Patient identifiers are pseudonymized to PT-NNNN, ages are banded
    per the CURE ID age groups, calendar dates are omitted, and
    outcomes are mapped to the CURE ID treatment-outcome vocabulary.
    """
    pseudo = {}
    intake_rows = []
    # Count exposures per medication for k-anon (only relevant if k > 1)
    med_counts = Counter()
    for r in cohort_rows:
        if r['exposed']:
            med_counts[r['medication']] += 1

    n_filtered_on_label   = 0
    n_filtered_withdrawn  = 0

    for r in cohort_rows:
        if exposed_only and not r['exposed']:
            continue
        med_lower = (r['medication'] or '').strip().lower()
        if off_label_only:
            if med_lower in ALS_FDA_APPROVED:
                n_filtered_on_label += 1
                continue
            if med_lower in ALS_FDA_WITHDRAWN:
                n_filtered_withdrawn += 1
                continue
        # Apply k-anonymity at the medication level (k=1 is no filter)
        if k > 1 and med_counts[r['medication']] < k:
            continue
        pid = _to_pseudo_id(r['patient_id'], pseudo)
        age_band = _age_band(r.get('age_at_entry'))
        sex_norm = (r.get('sex') or 'Unknown').strip().title()
        if sex_norm not in ('Female', 'Male'):
            sex_norm = 'Unknown'
        races = []
        for race_label in (r.get('race') or '').split(' / '):
            mapped = CURE_ID_RACE_MAP.get(race_label.strip())
            if mapped: races.append(mapped)
        if not races: races = ['Unknown']

        # Map outcome from observed death (CURE ID treatment outcome)
        if r['death_observed'] and r['survival_days'] is not None:
            if r['survival_days'] <= 365:    treatment_outcome = 'Patient died within 12 months of cohort entry'
            elif r['survival_days'] <= 730:  treatment_outcome = 'Patient died within 24 months of cohort entry'
            else:                            treatment_outcome = 'Patient died more than 24 months after cohort entry'
        else:
            treatment_outcome = 'Patient alive at last follow-up'

        intake_rows.append({
            'therapeutic_area':     'Rare Genetic Disorders',  # MND falls here in CURE ID lists
            'disease':              'Amyotrophic lateral sclerosis (or motor neuron disease spectrum)',
            'user_type':            'A Healthcare Provider',
            'pseudo_patient_id':    pid,
            'age_group':            age_band,
            'sex':                  sex_norm,
            'country_treated':      'United States',
            'races':                ', '.join(races),
            'medication_name':      r['medication'],
            'medication_atc_class': r['atc_class'],
            'medication_atc_code':  r['atc_code'],
            'exposure_criterion':   r['criterion'],
            'n_dispenses_window':   r['n_dispenses_window'],
            'why_new_way':          'Off-label drug use observed in ALS patient EHR data',
            'treatment_outcome':    treatment_outcome,
            'source_pipeline':      'Registry Forge drug_repurposing.py (off-label-use export)',
        })

    if off_label_only and (n_filtered_on_label or n_filtered_withdrawn):
        print(f'  CURE ID export filtered out {n_filtered_on_label} on-label '
              f'(riluzole/edaravone/tofersen) and {n_filtered_withdrawn} '
              f'previously-approved-now-withdrawn (sodium phenylbutyrate-'
              f'taurursodiol) exposure rows. Set off_label_only=False to '
              f'include them.')
    return intake_rows


# ============================================================================
# SUMMARY + REPORT
# ============================================================================
def build_summary(cohort_rows, keep_meds, k=DEFAULT_K_ANONYMITY):
    """Per-medication summary: n_exposed, n_unexposed, ATC class,
    crude median survival difference (no propensity matching here)."""
    by_med = defaultdict(lambda: {'exposed': [], 'unexposed': []})
    for r in cohort_rows:
        bucket = 'exposed' if r['exposed'] else 'unexposed'
        if r['survival_days'] is not None:
            by_med[r['medication']][bucket].append({
                'days':  r['survival_days'],
                'event': r['death_observed'],
            })

    summary = []
    for med in sorted(keep_meds):
        e = by_med[med]['exposed']
        u = by_med[med]['unexposed']
        atc_code, atc_class = ATC_SEED.get(med, ('UNCLASSIFIED', 'Not in ATC seed mapping'))

        def med_days(rows):
            days = sorted(r['days'] for r in rows if r['days'] is not None)
            return days[len(days)//2] if days else None

        def n_deaths(rows):
            return sum(1 for r in rows if r['event'])

        n_exposed   = len(e)
        n_unexposed = len(u)
        if n_exposed < k:
            n_exposed_display = f'<{k}'
        else:
            n_exposed_display = str(n_exposed)

        summary.append({
            'medication':     med,
            'atc_code':       atc_code,
            'atc_class':      atc_class,
            'n_exposed':      n_exposed_display,
            'n_unexposed':    n_unexposed,
            'n_deaths_exposed':   n_deaths(e),
            'n_deaths_unexposed': n_deaths(u),
            'median_survival_days_exposed':   med_days(e),
            'median_survival_days_unexposed': med_days(u),
        })
    return summary


HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:system-ui,-webkit-system-font,"Segoe UI",Arial,sans-serif;margin:0;padding:0;background:#faf9f6;color:#1e1633}}
header{{background:#1e1633;color:#fff;padding:24px 32px}}
header h1{{margin:0;font-size:22px}}
header .sub{{color:#a3b5ff;font-size:13px;margin-top:4px;font-style:italic}}
.priv{{background:#2a1c3f;color:#fbbf24;padding:8px 16px;font-size:12px;margin:0 32px;border:1px solid #fbbf24;border-radius:4px}}
main{{padding:24px 32px;max-width:1100px;margin:0 auto}}
.card{{background:white;border:1px solid #e5e3dc;border-radius:6px;padding:16px;margin-bottom:16px}}
.card h2{{margin:0 0 8px;color:#1e3a5f;font-size:14px;text-transform:uppercase;letter-spacing:0.5px}}
.card h2 small{{font-weight:normal;text-transform:none;letter-spacing:normal;color:#6b6862;margin-left:8px;font-size:12px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#f3f1ec;text-align:left;padding:6px 8px;color:#1e3a5f;font-weight:600;border-bottom:2px solid #e5e3dc}}
td{{padding:6px 8px;border-bottom:1px solid #f3f1ec}}
tr:hover{{background:#fafafa}}
.metric{{display:inline-block;margin-right:24px}}
.metric .val{{font-size:24px;font-weight:bold;color:#4a148c;display:block}}
.metric .lbl{{font-size:10px;color:#6b6862;text-transform:uppercase;letter-spacing:0.5px}}
.protective{{color:#0f6e56;font-weight:bold}}
.harmful{{color:#854f0b;font-weight:bold}}
.note{{font-size:11px;color:#6b6862;margin-top:8px;font-style:italic}}
.atc-group{{margin-top:16px;padding:12px;background:#f7f5f0;border-radius:4px}}
.atc-group h3{{margin:0 0 8px;color:#3949ab;font-size:12px;text-transform:uppercase}}
</style></head>
<body>
<header>
  <h1>Registry Forge — Drug repurposing analysis</h1>
  <div class="sub">{cohort_name} · {generated}</div>
</header>
<div class="priv"><b>Privacy:</b> Patient identifiers pseudonymized · ages banded · dates given as time-from-cohort-entry only · k-anonymity threshold {k} applied to medication-level summaries.</div>
<main>
<div class="card">
  <h2>Cohort overview</h2>
  <span class="metric"><span class="val">{n_cohort}</span><span class="lbl">Patients in motor neuron disease cohort</span></span>
  <span class="metric"><span class="val">{n_deaths}</span><span class="lbl">Deaths observed</span></span>
  <span class="metric"><span class="val">{n_meds}</span><span class="lbl">Medications meeting min-{min_exposed} threshold</span></span>
  <span class="metric"><span class="val">{n_classes}</span><span class="lbl">ATC classes represented</span></span>
  <div class="note">Cohort defined by ICD-10-CM G12.21 / G12.20 / G12.22 / G12.23 / G12.29 / ICD-9-CM 335.20-29 / SNOMED 86044005 / Mondo MONDO:0004976 / MONDO:0019056 in problems[]. Exposure criteria adapted from Reimer 2026.</div>
</div>

{clinical_interest_panel_html}

<div class="card">
  <h2>Medication summary <small>· grouped by ATC class · k=≥{k} for "n_exposed"</small></h2>
  {atc_groups_html}
</div>

<div class="card">
  <h2>Methodology and limitations</h2>
  <p style="font-size:12px;line-height:1.55">
  This module produces the cohort assembly and exposure assignment described in Reimer et al. <i>Lancet Digital Health</i> 2026. <b>It does not run the propensity score matching or the Cox proportional hazards analysis</b>; those steps require dedicated tools (<code>lifelines</code>, <code>statsmodels</code>) and per-cohort tuning of caliper widths, immortal-time bias correction, and covariate balance assessment. The cohort dataset emitted by this module (<code>drug_repurposing_cohort.csv</code>) is the input to those tools.
  </p>
  <p style="font-size:12px;line-height:1.55">
  Limitations of this analysis at any stage: small-cohort statistical power, inadequate covariate adjustment for known and unknown confounders, indication bias (a patient prescribed a PDE5 inhibitor may by virtue of that indication be healthier than average), immortal-time bias if exposure timing is mishandled, accuracy of EHR-coded motor-neuron-disease diagnosis, and lack of dose-response information in the bundle. Treat outputs as hypothesis-generating; prospective randomized trials remain the gold standard.
  </p>
  <p style="font-size:12px;line-height:1.55">
  Cure ID intake export: produces one row per exposed (patient, medication) for submission to the FDA / NCATS-NIH / Critical Path Institute CURE Drug Repurposing Collaboratory Treatment Registry. Banded fields and pseudonymized identifiers comply with the registry's PII-free expectations.
  </p>
</div>
</main></body></html>
"""


def compute_clinical_interest_summary(meds_per_patient, k=DEFAULT_K_ANONYMITY):
    """Compute presence / year-range for each ALS clinical-interest medication.

    Walks the same meds_per_patient structure that the main analysis uses
    (a {patient_id: [(med_name_norm, raw_display, start_date, end_date), ...]}
    dict produced by collect_medication_events). For each medication in
    ALS_CLINICAL_INTEREST, computes:

      - n_exposed_patients         exact count
      - n_exposed_anon             k-anonymized string ('< k' or the exact n)
      - first_year, last_year      year-range of any record we have for it
      - n_records                  total prescription records
      - present                    True if any patient has any record

    Returns a list of dicts in the same order as ALS_CLINICAL_INTEREST.
    Patient counts and year ranges are always returned at the medication
    level only; never patient-level (no PHI).
    """
    # Index by med_key for fast lookup
    interest_keys = {m['med_key']: m for m in ALS_CLINICAL_INTEREST}

    pts_by_key  = defaultdict(set)
    recs_by_key = Counter()
    years_by_key = defaultdict(list)

    for pid, events in meds_per_patient.items():
        for med_name, raw_display, start, end in events:
            if med_name not in interest_keys:
                continue
            pts_by_key[med_name].add(pid)
            recs_by_key[med_name] += 1
            if start is not None:
                years_by_key[med_name].append(start.year)
            if end is not None:
                years_by_key[med_name].append(end.year)

    rows = []
    for entry in ALS_CLINICAL_INTEREST:
        key = entry['med_key']
        n_pts = len(pts_by_key.get(key, set()))
        years = years_by_key.get(key, [])
        if n_pts == 0:
            n_anon = '0'
            year_range = '—'
            present = False
        elif n_pts < k:
            n_anon = f'< {k}'
            year_range = (f'{min(years)} — {max(years)}' if years else 'date unknown')
            present = True
        else:
            n_anon = str(n_pts)
            year_range = (f'{min(years)} — {max(years)}' if years else 'date unknown')
            present = True
        rows.append({
            **entry,
            'n_exposed_anon': n_anon,
            'year_range':     year_range,
            'n_records':      recs_by_key.get(key, 0),
            'present':        present,
        })
    return rows


def render_clinical_interest_panel(rows, k):
    """Return HTML for the ALS clinical-interest panel."""
    if not rows:
        return ''

    # Status badge styling
    STATUS_STYLE = {
        'approved': {
            'bg':   '#dff0e7',
            'fg':   '#0f6e56',
            'text': 'FDA-approved',
        },
        'off_label_for_als': {
            'bg':   '#fff1d6',
            'fg':   '#854f0b',
            'text': 'Off-label-of-interest',
        },
        'withdrawn': {
            'bg':   '#f4d9d6',
            'fg':   '#8c1d18',
            'text': 'Withdrawn',
        },
    }
    PRESENCE_STYLE = {
        True:  {'bg': '#e7eef8', 'fg': '#1e3a5f', 'text': 'Observed in cohort'},
        False: {'bg': '#f5f3ea', 'fg': '#6b6862', 'text': 'Not observed'},
    }

    trs = []
    for r in rows:
        s = STATUS_STYLE.get(r['fda_status'], STATUS_STYLE['off_label_for_als'])
        p = PRESENCE_STYLE[r['present']]
        status_badge = (
            f'<span style="display:inline-block;background:{s["bg"]};color:{s["fg"]};'
            f'padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">'
            f'{s["text"]}</span>')
        presence_badge = (
            f'<span style="display:inline-block;background:{p["bg"]};color:{p["fg"]};'
            f'padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">'
            f'{p["text"]}</span>')
        n_anon_html = (
            f'<span style="font-size:18px;font-weight:600;color:#4a148c;'
            f'font-variant-numeric:tabular-nums;">{r["n_exposed_anon"]}</span>')
        years_html = (
            f'<span style="font-size:13px;color:#1e3a5f;'
            f'font-variant-numeric:tabular-nums;">{r["year_range"]}</span>')
        trs.append(
            f'<tr>'
            f'<td><b>{r["display"]}</b><br>'
            f'<small style="color:#6b6862;">{r["brands"]} &middot; ATC {r["atc"]}</small></td>'
            f'<td>{status_badge}<br><small style="color:#6b6862;font-style:italic">{r["label"]}</small></td>'
            f'<td style="text-align:center;">{presence_badge}</td>'
            f'<td style="text-align:right;">{n_anon_html}</td>'
            f'<td style="text-align:center;">{years_html}</td>'
            f'<td style="text-align:right;font-variant-numeric:tabular-nums;color:#6b6862;">{r["n_records"]:,}</td>'
            f'</tr>')

    return f'''<div class="card">
  <h2>ALS clinical-interest medications <small style="font-weight:normal;color:#6b6862">&middot; always shown, regardless of min-exposed threshold &middot; k-anonymized at k={k}</small></h2>
  <p style="font-size:12px;color:#6b6862;margin:-4px 0 12px 0;">
    The five medications below have particular relevance to ALS clinical care and research:
    the three FDA-approved disease-modifying therapies, the FDA-approved-for-PBA combination
    commonly used off-label in ALS care, and the briefly-approved combination withdrawn after
    confirmatory trial failure. Presence and year-range are reported regardless of whether the
    medication meets the standard min-exposed threshold; counts are k-anonymized (values
    below k=&nbsp;{k} reported as &ldquo;&lt; {k}&rdquo;).
  </p>
  <table>
    <tr>
      <th>Medication</th>
      <th>FDA status</th>
      <th style="text-align:center;">Cohort presence</th>
      <th style="text-align:right;">Unique patients</th>
      <th style="text-align:center;">Year range</th>
      <th style="text-align:right;">Records</th>
    </tr>
    {''.join(trs)}
  </table>
</div>'''


def render_html(cohort_rows, summary, cohort_name, k, min_exposed=MIN_EXPOSED_PATIENTS,
                clinical_interest_rows=None):
    n_cohort = len({r['patient_id'] for r in cohort_rows})
    n_deaths = len({r['patient_id'] for r in cohort_rows if r['death_observed']})
    n_meds = len(summary)
    n_classes = len({s['atc_class'] for s in summary})

    # Group summary by ATC class
    by_class = defaultdict(list)
    for s in summary:
        by_class[s['atc_class']].append(s)

    atc_html_parts = []
    for atc_class in sorted(by_class.keys()):
        rows = sorted(by_class[atc_class], key=lambda r: r['medication'])
        table_rows = []
        for r in rows:
            ms_e = r['median_survival_days_exposed']
            ms_u = r['median_survival_days_unexposed']
            direction = ''
            if ms_e is not None and ms_u is not None:
                if ms_e > ms_u * 1.1:    direction = '<span class="protective">↑ exposed longer</span>'
                elif ms_e < ms_u * 0.9:  direction = '<span class="harmful">↓ exposed shorter</span>'
            ms_e_s = f'{ms_e/365.25:.1f}y' if ms_e else '—'
            ms_u_s = f'{ms_u/365.25:.1f}y' if ms_u else '—'
            table_rows.append(
                f'<tr><td><b>{r["medication"]}</b><br><small style="color:#6b6862">{r["atc_code"]}</small></td>'
                f'<td>{r["n_exposed"]}</td>'
                f'<td>{r["n_unexposed"]:,}</td>'
                f'<td>{r["n_deaths_exposed"]:,}</td>'
                f'<td>{r["n_deaths_unexposed"]:,}</td>'
                f'<td>{ms_e_s}</td><td>{ms_u_s}</td>'
                f'<td>{direction}</td></tr>'
            )
        atc_html_parts.append(f'''<div class="atc-group">
<h3>{atc_class}</h3>
<table>
<tr><th>Medication</th><th>n exposed</th><th>n unexposed</th><th>deaths (exp)</th><th>deaths (unexp)</th><th>median surv (exp)</th><th>median surv (unexp)</th><th>direction</th></tr>
{"".join(table_rows)}
</table></div>''')

    clinical_interest_panel_html = (render_clinical_interest_panel(clinical_interest_rows, k)
                                     if clinical_interest_rows else '')

    return HTML_TEMPLATE.format(
        title=f'Drug repurposing — {cohort_name}',
        cohort_name=cohort_name,
        generated=_dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        k=k,
        min_exposed=min_exposed,
        n_cohort=n_cohort,
        n_deaths=n_deaths,
        n_meds=n_meds,
        n_classes=n_classes,
        clinical_interest_panel_html=clinical_interest_panel_html,
        atc_groups_html='\n'.join(atc_html_parts) or '<p style="color:#6b6862;font-size:12px"><i>No medications met the minimum exposure threshold in this cohort.</i></p>',
    )


# ============================================================================
# MAIN
# ============================================================================
def main(bundle_path=DEFAULT_BUNDLE_PATH, out_dir=DEFAULT_OUT_DIR,
         min_exposed=5, k=DEFAULT_K_ANONYMITY,
         cohort_name='Motor neuron disease cohort',
         criterion='any', verbose=True, pseudonymize=True):
    """Run the drug repurposing analysis against a Registry Forge bundle.

    Parameters
    ----------
    bundle_path : str
        Path to dashboard_data.json produced by run_pipeline.py.
    out_dir : str
        Directory to write the four output files into.
    min_exposed : int
        Minimum exposed patients required to keep a medication in the
        analysis. Reimer used 30 (against 11,003 patients). For small
        registries this needs to be lower. Default is 5; the diagnostic
        log will suggest a value if your top medications fall below it.
    k : int
        k-anonymity threshold for the per-medication summary. Cells below
        k render as '<k'. Default 5.
    cohort_name : str
        Display name used in the HTML report.
    criterion : str
        Exposure criterion mode. 'any' (default) counts any medication
        record in the [-6, +12] month window around cohort entry; this
        fits C-CDA / FHIR registry data where each row is one
        prescription event with no end_date. 'reimer-a', 'reimer-b', or
        'reimer' apply the strict Reimer 2026 criteria, which require
        either an end_date or multiple dispense events per medication
        and are appropriate only for VHA-style or dispense-event data.
    verbose : bool
        If True (default), print step-by-step diagnostic counts to stdout
        so empty output explains itself. Set False to suppress.
    pseudonymize : bool
        If True (default), the patient_id column in drug_repurposing_cohort.csv
        is replaced with stable PT-NNNN pseudonyms. Raw patient_id values
        (Epic patient IDs, MRNs, FHIR resource IDs derived from Epic, etc.)
        are HIPAA direct identifiers and should not appear in shared output.
        cure_id_intake.csv already uses pseudonyms regardless of this flag.
        Set False only for transient internal analyses that require chart-
        review linkage, and treat the resulting CSV as PHI.
    """
    os.makedirs(out_dir, exist_ok=True)
    log('=' * 72)
    log('Registry Forge — Drug repurposing')
    log('=' * 72)
    log(f'Bundle: {bundle_path}')
    log(f'Output: {out_dir}')
    log(f'Settings: min_exposed={min_exposed}  k={k}  criterion={criterion!r}  pseudonymize={pseudonymize}')
    if not pseudonymize:
        log('  WARNING: pseudonymize=False - raw patient_id values will be written to '
            'drug_repurposing_cohort.csv. These are HIPAA direct identifiers. Treat the '
            'CSV as PHI and store accordingly.')

    with open(bundle_path) as f:
        bundle = json.load(f)
    log(f'  patients:    {len(bundle.get("patients", [])):,}')
    log(f'  problems:    {len(bundle.get("problems", [])):,}')
    log(f'  medications: {len(bundle.get("medications", [])):,}')

    cohort = identify_cohort(bundle, verbose=verbose)
    log(f'Cohort: {len(cohort)} patients with motor-neuron-disease diagnosis')

    if not cohort:
        log('  No cohort patients identified. Common causes:')
        log('    - problems[] is empty or missing in the bundle')
        log('    - none of the problem codings use ICD-10-CM / SNOMED-CT / Mondo IDs')
        log('      for ALS or motor-neuron-disease spectrum (see COHORT_CODES at top of module)')
        log('    - all matching problems lack a parseable effective_date / onset_date')
        log('  Run with verbose=True (default) to see per-step counts above.')

    demo = gather_patient_demographics(bundle)
    meds = collect_medication_events(bundle, verbose=verbose)
    log(f'  patients with any medication record (across whole bundle): {len(meds)}')
    log(f'  cohort patients with any medication record: {sum(1 for p in cohort if p in meds)}')

    cohort_rows, keep_meds = assemble_cohort_table(cohort, demo, meds, min_exposed,
                                                   criterion=criterion, verbose=verbose,
                                                   pseudonymize=pseudonymize)
    log(f'  medications meeting min_exposed={min_exposed}: {len(keep_meds)}')

    summary = build_summary(cohort_rows, keep_meds, k)
    # CURE ID intake: every occasion of off-label drug use; k=1 (no medication-
    # level aggregation), off_label_only=True (exclude on-label ALS therapies
    # and the withdrawn-from-market sodium phenylbutyrate-taurursodiol).
    intake = cure_id_intake_rows(cohort_rows,
                                 exposed_only=True,
                                 off_label_only=True,
                                 k=1)

    # Write outputs
    cohort_csv = os.path.join(out_dir, 'drug_repurposing_cohort.csv')
    with open(cohort_csv, 'w', newline='') as f:
        if cohort_rows:
            w = csv.DictWriter(f, fieldnames=list(cohort_rows[0].keys()))
            w.writeheader()
            w.writerows(cohort_rows)
    log(f'  wrote {cohort_csv} ({len(cohort_rows):,} rows)')

    summary_csv = os.path.join(out_dir, 'drug_repurposing_summary.csv')
    with open(summary_csv, 'w', newline='') as f:
        if summary:
            w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
            w.writeheader()
            w.writerows(summary)
    log(f'  wrote {summary_csv} ({len(summary):,} rows)')

    cure_csv = os.path.join(out_dir, 'cure_id_intake.csv')
    with open(cure_csv, 'w', newline='') as f:
        if intake:
            w = csv.DictWriter(f, fieldnames=list(intake[0].keys()))
            w.writeheader()
            w.writerows(intake)
    log(f'  wrote {cure_csv} ({len(intake):,} rows)')

    # ALS clinical-interest summary — always shown in the dashboard regardless
    # of min_exposed. Walks the same meds_per_patient mapping that the main
    # exposure analysis used, but scoped to the cohort patients only (so that
    # a bundle containing non-ALS patients won't pollute the panel). Uses
    # k-anonymized counts and year ranges.
    cohort_patient_ids = {r['patient_id'] for r in cohort_rows} if cohort_rows else set()
    cohort_scoped_meds = ({pid: events for pid, events in meds.items()
                            if pid in cohort_patient_ids}
                           if cohort_patient_ids else meds)
    clinical_interest_rows = compute_clinical_interest_summary(cohort_scoped_meds, k=k)
    if verbose:
        log('ALS clinical-interest medications (always shown):')
        for r in clinical_interest_rows:
            log(f'  {r["display"]:<40s} {r["n_exposed_anon"]:>6s} patients  ·  {r["year_range"]}  ·  {r["n_records"]:,} records')

    html_out = render_html(cohort_rows, summary, cohort_name, k, min_exposed=min_exposed,
                            clinical_interest_rows=clinical_interest_rows)
    html_path = os.path.join(out_dir, 'drug_repurposing_report.html')
    with open(html_path, 'w') as f:
        f.write(html_out)
    log(f'  wrote {html_path}')

    # Source-record snippets: privacy-safe traceback to the EHR for every
    # off-label drug surfaced. Same bundle, same privacy controls as the
    # CURE ID export. Useful for verification, chart-review prep, and as
    # supplementary evidence in manuscripts.
    snippets = inspect_off_label_records(bundle)
    snippet_md = render_off_label_snippets_markdown(snippets,
                                                     cohort_label=cohort_name)
    snippet_path = os.path.join(out_dir, 'off_label_snippets.md')
    with open(snippet_path, 'w', encoding='utf-8') as f:
        f.write(snippet_md)
    log(f'  wrote {snippet_path} ({len(snippets)} drugs with qualifying records)')

    log('Done.')
    return cohort_rows


if __name__ == '__main__':
    main()
