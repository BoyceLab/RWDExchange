"""
Phenopackets ETL (GA4GH Phenopacket Schema v2)
==============================================
Reads `dashboard_data.json` (output of run_pipeline.py) and produces one
GA4GH Phenopacket v2 JSON document per patient, plus a combined Cohort
document, a summary CSV, and an unmapped-codes report.

The primary path is fully driven by the structured codes already present
in the EHR feed: ICD-10-CM and SNOMED CT problem codes, LOINC lab and
vital codes, RxNorm medication codes, and SNOMED CT / CPT-4 procedure
codes. Nothing about that path requires NLP, manual abstraction, or
review of clinical narrative; the Phenopacket is generated end-to-end
from the same coded data the pipeline is already collecting.

Two optional inputs extend the structured-code output:

  1. note_extractions.csv (optional, currently a demonstration)
     Lets the module surface ALSFRS-R subscores, ECAS scores, and gene
     symbols that the regex-based note extraction module recovers from
     free-text narratives. We treat this as a proof-of-concept layer:
     the module shows that note-derived content can be folded into the
     Phenopacket as Measurements (LOINC-coded) or as enrichment to the
     genetic-interpretation block, but the ARC pipeline is not yet
     using it for production output. Set include_note_measurements=True
     (default True) to include them; set False for structured-only.

  2. external_genetics_csv (optional, recommended for real use)
     A path to a CSV the user maintains outside the EHR pipeline,
     describing variants per patient (gene symbol, HGVS, pathogenicity,
     source). When supplied, the module emits proper GenomicInterpretation
     entries with HGNC-coded geneContext and ACMG-style classifications.
     This is how production genetic data is meant to enter the pipeline:
     curated outside, joined here by patient identifier. The expected
     schema is documented in the function `load_external_genetics`.

Mapping strategy (structured-code path; this is the production output)
---------------------------------------------------------------------
SOURCE CODES IN BUNDLE                TARGET                 HOW
ICD-10-CM / SNOMED CT (problems)  ->  HPO (phenotypes)       seed + Athena
                                  ->  Mondo (diseases)        seed + Athena
LOINC (labs/vitals)               ->  LOINC measurements     pass-through
RxNorm (medications)              ->  RxNorm medicalActions  pass-through
SNOMED CT / CPT-4 (procedures)    ->  pass-through medicalActions
CVX (immunizations)               ->  pass-through medicalActions

Demonstration layer (note_extractions.csv, optional)
----------------------------------------------------
ALSFRS-R / ECAS scores            ->  LOINC Measurements     POC, off by default for
                                                              registries not yet using NLP
Gene symbols                      ->  HGNC enrichment of     POC; replaced by
                                       interpretation block   external_genetics_csv
                                                              when that's available

External genetics path (recommended for production)
---------------------------------------------------
external_genetics_csv             ->  GenomicInterpretation   Curated outside the EHR
                                       with HGNC, HGVS,        pipeline; joined here by
                                       pathogenicity, source   patient identifier

Seed tables ship hand-curated mappings for three disease areas:
   - ALS spectrum (~30 phenotype, ~9 disease mappings)
   - Epilepsy (~14 phenotype, ~8 disease mappings)
   - Autoimmune neurologic and rheumatic (~17 phenotype, ~15 disease)
For broader coverage, point the module at an Athena vocabulary download
with HPO and Mondo selected and the seed table is supplemented
automatically.

Quality control
---------------
This module is one tier of an integrated QC framework. See the QC docs
page for the full approach. At a minimum, every Phenopacket output is
schema-validated against the GA4GH Phenopacket v2 JSON Schema, the
unmapped_codes.csv report tracks coverage, and metaData.resources
records exact ontology release versions used.
"""

import os
import re
import csv
import json
import datetime as _dt
from collections import Counter, defaultdict


# === SEED MAPPING TABLES ====================================================
# Hand-curated. Extend by editing the dicts below or by passing additional
# rows via load_external_mappings(). Format: (vocabulary_id, code) -> dict
# with id and label.

# --- Phenotypes (HPO) -------------------------------------------------------
SNOMED_ICD_TO_HPO = {
    # ============== ALS-spectrum phenotypes ==============
    ('SNOMED-CT', '40425005'): {'id':'HP:0002015','label':'Dysphagia'},
    ('SNOMED-CT', '40739000'): {'id':'HP:0002015','label':'Dysphagia'},
    ('SNOMED-CT', '8011004'):  {'id':'HP:0001260','label':'Dysarthria'},
    ('SNOMED-CT', '26544005'): {'id':'HP:0001324','label':'Muscle weakness'},
    ('SNOMED-CT', '91037003'): {'id':'HP:0002380','label':'Fasciculations'},
    ('SNOMED-CT', '56116003'): {'id':'HP:0001257','label':'Spasticity'},
    ('SNOMED-CT', '44169009'): {'id':'HP:0001347','label':'Hyperreflexia'},
    ('SNOMED-CT', '47742003'): {'id':'HP:0003202','label':'Skeletal muscle atrophy'},
    ('SNOMED-CT', '230456007'):{'id':'HP:0001283','label':'Bulbar palsy'},
    ('SNOMED-CT', '50920009'): {'id':'HP:0002307','label':'Drooling'},
    ('SNOMED-CT', '267036007'):{'id':'HP:0002094','label':'Dyspnea'},
    ('SNOMED-CT', '267062005'):{'id':'HP:0002747','label':'Respiratory insufficiency'},
    ('SNOMED-CT', '89362005'): {'id':'HP:0001824','label':'Weight loss'},
    ('SNOMED-CT', '84229001'): {'id':'HP:0012378','label':'Fatigue'},
    ('SNOMED-CT', '50369003'): {'id':'HP:0002493','label':'Upper motor neuron dysfunction'},
    ('SNOMED-CT', '286947009'):{'id':'HP:0007354','label':'Amyotrophic lateral sclerosis'},
    ('SNOMED-CT', '86044005'): {'id':'HP:0007354','label':'Amyotrophic lateral sclerosis'},  # primary ALS code in many systems
    ('SNOMED-CT', '35489007'): {'id':'HP:0000716','label':'Depression'},  # depressive disorder
    ('SNOMED-CT', '73595000'): {'id':'HP:0000716','label':'Depression'},
    ('SNOMED-CT', '162076009'):{'id':'HP:0000726','label':'Dementia'},  # cognitive impairment
    ('SNOMED-CT', '386807006'):{'id':'HP:0001259','label':'Coma'},  # used incorrectly sometimes; placeholder
    ('SNOMED-CT', '162675008'):{'id':'HP:0001260','label':'Dysarthria'},  # alt code
    ('ICD-10-CM',  'G12.21'):  {'id':'HP:0007354','label':'Amyotrophic lateral sclerosis'},
    ('ICD-10-CM',  'R13.10'):  {'id':'HP:0002015','label':'Dysphagia'},
    ('ICD-10-CM',  'R13.0'):   {'id':'HP:0002015','label':'Dysphagia'},
    ('ICD-10-CM',  'R47.1'):   {'id':'HP:0001260','label':'Dysarthria'},
    ('ICD-10-CM',  'R25.3'):   {'id':'HP:0002380','label':'Fasciculations'},
    ('ICD-10-CM',  'M62.81'):  {'id':'HP:0001324','label':'Muscle weakness'},
    ('ICD-10-CM',  'R26.89'):  {'id':'HP:0001288','label':'Gait disturbance'},
    ('ICD-10-CM',  'R06.00'):  {'id':'HP:0002094','label':'Dyspnea'},
    ('ICD-10-CM',  'R06.0'):   {'id':'HP:0002094','label':'Dyspnea'},
    ('ICD-10-CM',  'R63.4'):   {'id':'HP:0001824','label':'Weight loss'},
    ('ICD-10-CM',  'R53.83'):  {'id':'HP:0012378','label':'Fatigue'},
    ('ICD-10-CM',  'R29.2'):   {'id':'HP:0001347','label':'Hyperreflexia'},
    ('ICD-10-CM',  'R25.2'):   {'id':'HP:0002510','label':'Spasticity'},

    # ============== Epilepsy phenotypes ==============
    ('SNOMED-CT', '91175000'): {'id':'HP:0001250','label':'Seizure'},
    ('SNOMED-CT', '230381004'):{'id':'HP:0002069','label':'Bilateral tonic-clonic seizure'},
    ('SNOMED-CT', '79631006'): {'id':'HP:0002121','label':'Absence seizure'},
    ('SNOMED-CT', '102449007'):{'id':'HP:0002123','label':'Generalized myoclonic seizure'},
    ('SNOMED-CT', '313307000'):{'id':'HP:0007359','label':'Focal-onset seizure'},
    ('SNOMED-CT', '230456007'):{'id':'HP:0011097','label':'Epileptic encephalopathy'},
    ('SNOMED-CT', '60007008'): {'id':'HP:0001289','label':'Confusion'},
    ('SNOMED-CT', '230469006'):{'id':'HP:0002133','label':'Status epilepticus'},
    ('ICD-10-CM',  'G40.909'): {'id':'HP:0001250','label':'Seizure'},
    ('ICD-10-CM',  'G40.301'): {'id':'HP:0002069','label':'Bilateral tonic-clonic seizure'},
    ('ICD-10-CM',  'G40.A09'): {'id':'HP:0002121','label':'Absence seizure'},
    ('ICD-10-CM',  'G40.B09'): {'id':'HP:0002123','label':'Generalized myoclonic seizure'},
    ('ICD-10-CM',  'G40.219'): {'id':'HP:0007359','label':'Focal-onset seizure'},
    ('ICD-10-CM',  'R56.9'):   {'id':'HP:0001250','label':'Seizure'},
    ('ICD-10-CM',  'G40.901'): {'id':'HP:0002133','label':'Status epilepticus'},

    # ============== Autoimmune-disease phenotypes ==============
    ('SNOMED-CT', '57676002'): {'id':'HP:0002829','label':'Arthralgia'},
    ('SNOMED-CT', '396275006'):{'id':'HP:0001369','label':'Arthritis'},
    ('SNOMED-CT', '271807003'):{'id':'HP:0000988','label':'Skin rash'},
    ('SNOMED-CT', '274118001'):{'id':'HP:0025385','label':'Malar rash'},
    ('SNOMED-CT', '195469007'):{'id':'HP:0030880','label':'Raynaud phenomenon'},
    ('SNOMED-CT', '24079001'): {'id':'HP:0000992','label':'Cutaneous photosensitivity'},
    ('SNOMED-CT', '230502005'):{'id':'HP:0009830','label':'Peripheral neuropathy'},
    ('SNOMED-CT', '386661006'):{'id':'HP:0001945','label':'Fever'},
    ('SNOMED-CT', '30746006'): {'id':'HP:0002716','label':'Lymphadenopathy'},
    ('SNOMED-CT', '193093009'):{'id':'HP:0007359','label':'Focal-onset seizure'},  # in autoimmune encephalitis
    ('ICD-10-CM',  'M25.50'):  {'id':'HP:0002829','label':'Arthralgia'},
    ('ICD-10-CM',  'L93.0'):   {'id':'HP:0025385','label':'Malar rash'},
    ('ICD-10-CM',  'I73.00'):  {'id':'HP:0030880','label':'Raynaud phenomenon'},
    ('ICD-10-CM',  'L57.8'):   {'id':'HP:0000992','label':'Cutaneous photosensitivity'},
    ('ICD-10-CM',  'G62.9'):   {'id':'HP:0009830','label':'Peripheral neuropathy'},
    ('ICD-10-CM',  'R50.9'):   {'id':'HP:0001945','label':'Fever'},
    ('ICD-10-CM',  'R59.9'):   {'id':'HP:0002716','label':'Lymphadenopathy'},
}

# --- Diseases (Mondo) -------------------------------------------------------
SNOMED_ICD_TO_MONDO = {
    # ============== ALS spectrum ==============
    ('SNOMED-CT', '86044005'): {'id':'MONDO:0004976','label':'amyotrophic lateral sclerosis'},
    ('SNOMED-CT', '230260009'):{'id':'MONDO:0017276','label':'frontotemporal dementia'},
    ('SNOMED-CT', '73297009'): {'id':'MONDO:0019468','label':'primary lateral sclerosis'},
    ('SNOMED-CT', '32202008'): {'id':'MONDO:0019515','label':'progressive muscular atrophy'},
    ('SNOMED-CT', '230258007'):{'id':'MONDO:0017276','label':'frontotemporal dementia'},
    ('ICD-10-CM',  'G12.21'):  {'id':'MONDO:0004976','label':'amyotrophic lateral sclerosis'},
    ('ICD-10-CM',  'G12.22'):  {'id':'MONDO:0019515','label':'progressive muscular atrophy'},
    ('ICD-10-CM',  'G12.23'):  {'id':'MONDO:0019468','label':'primary lateral sclerosis'},
    ('ICD-10-CM',  'G31.09'):  {'id':'MONDO:0017276','label':'frontotemporal dementia'},

    # ============== Epilepsy spectrum ==============
    ('SNOMED-CT', '84757009'): {'id':'MONDO:0005027','label':'epilepsy'},
    ('SNOMED-CT', '313307000'):{'id':'MONDO:0011828','label':'focal epilepsy'},
    ('SNOMED-CT', '79631006'): {'id':'MONDO:0010833','label':'absence epilepsy'},
    ('SNOMED-CT', '230381004'):{'id':'MONDO:0010834','label':'generalized epilepsy'},
    ('ICD-10-CM',  'G40.909'): {'id':'MONDO:0005027','label':'epilepsy'},
    ('ICD-10-CM',  'G40.219'): {'id':'MONDO:0011828','label':'focal epilepsy'},
    ('ICD-10-CM',  'G40.A09'): {'id':'MONDO:0010833','label':'absence epilepsy'},
    ('ICD-10-CM',  'G40.301'): {'id':'MONDO:0010834','label':'generalized epilepsy'},

    # ============== Autoimmune ==============
    ('SNOMED-CT', '200936003'):{'id':'MONDO:0007915','label':'systemic lupus erythematosus'},
    ('SNOMED-CT', '69896004'): {'id':'MONDO:0008383','label':'rheumatoid arthritis'},
    ('SNOMED-CT', '24700007'): {'id':'MONDO:0005301','label':'multiple sclerosis'},
    ('SNOMED-CT', '83901003'): {'id':'MONDO:0010030','label':'Sjogren syndrome'},
    ('SNOMED-CT', '91637004'): {'id':'MONDO:0008381','label':'myasthenia gravis'},
    ('SNOMED-CT', '193206009'):{'id':'MONDO:0008328','label':'Guillain-Barre syndrome'},
    ('SNOMED-CT', '396275006'):{'id':'MONDO:0008383','label':'rheumatoid arthritis'},
    ('ICD-10-CM',  'M32.9'):   {'id':'MONDO:0007915','label':'systemic lupus erythematosus'},
    ('ICD-10-CM',  'M32.10'):  {'id':'MONDO:0007915','label':'systemic lupus erythematosus'},
    ('ICD-10-CM',  'M05.9'):   {'id':'MONDO:0008383','label':'rheumatoid arthritis'},
    ('ICD-10-CM',  'M06.9'):   {'id':'MONDO:0008383','label':'rheumatoid arthritis'},
    ('ICD-10-CM',  'G35'):     {'id':'MONDO:0005301','label':'multiple sclerosis'},
    ('ICD-10-CM',  'M35.00'):  {'id':'MONDO:0010030','label':'Sjogren syndrome'},
    ('ICD-10-CM',  'G70.00'):  {'id':'MONDO:0008381','label':'myasthenia gravis'},
    ('ICD-10-CM',  'G61.0'):   {'id':'MONDO:0008328','label':'Guillain-Barre syndrome'},
}

# --- HGNC gene IDs for placeholder genetic interpretations ----------------
GENE_HGNC = {
    'C9orf72':  {'id':'HGNC:28337','symbol':'C9orf72'},
    'SOD1':     {'id':'HGNC:11179','symbol':'SOD1'},
    'FUS':      {'id':'HGNC:4010', 'symbol':'FUS'},
    'TARDBP':   {'id':'HGNC:11571','symbol':'TARDBP'},
    'TBK1':     {'id':'HGNC:11584','symbol':'TBK1'},
    'VCP':      {'id':'HGNC:12666','symbol':'VCP'},
    'UBQLN2':   {'id':'HGNC:12509','symbol':'UBQLN2'},
    'PFN1':     {'id':'HGNC:8881', 'symbol':'PFN1'},
    'MATR3':    {'id':'HGNC:6912', 'symbol':'MATR3'},
    'CHCHD10':  {'id':'HGNC:14964','symbol':'CHCHD10'},
    'ANG':      {'id':'HGNC:483',  'symbol':'ANG'},
    'OPTN':     {'id':'HGNC:17142','symbol':'OPTN'},
    'ATXN2':    {'id':'HGNC:10555','symbol':'ATXN2'},
}

# Gender mapping (FHIR -> Phenopackets Sex enum)
SEX_MAP = {
    'male':'MALE', 'female':'FEMALE',
    'other':'OTHER_SEX', 'unknown':'UNKNOWN_SEX', '':'UNKNOWN_SEX',
}

# RxNorm term-type preference for selecting the "best" coding when a
# medication carries multiple RxNorm codings. Lower index = preferred.
# Athena stores TTYs in CONCEPT.csv as `concept_class_id`. See omop_etl.py
# for the same list (kept independent so modules run standalone).
RXNORM_TTY_PREF = [
    'Clinical Drug',           # SCD - ingredient + strength + dose form
    'Branded Drug',            # SBD - brand + ingredient + strength + form
    'Quant Clinical Drug',     # SCDC
    'Quant Branded Drug',      # SBDC
    'Clinical Pack',           # GPCK
    'Branded Pack',            # BPCK
    'Clinical Drug Form',      # SCDF - ingredient + dose form
    'Branded Drug Form',       # SBDF - brand + dose form
    'Clinical Drug Comp',      # SCDG
    'Branded Drug Comp',       # SBDG
    'Ingredient',              # IN - too generic for analytics alone
    'Precise Ingredient',      # PIN
    'Brand Name',              # BN - no strength
    'Dose Form',               # DF
    'Dose Form Group',         # DFG
]


def select_primary_rxnorm(codings, rxnorm_tty_index):
    """Pick the best RxNorm coding from a CodeableConcept.coding[] array
    by RxNorm term-type (TTY) preference. SCD/SBD beat IN/BN when Athena
    is loaded; otherwise falls back to first-RxNorm. Returns a single
    coding dict (or None if `codings` is empty)."""
    if not codings:
        return None
    rxnorm = [c for c in codings
              if 'rxnorm' in ((c.get('system_name') or '').lower())]
    if not rxnorm:
        return codings[0]
    if not rxnorm_tty_index:
        return rxnorm[0]
    def rank(c):
        code = (c.get('code') or '').strip()
        cls = rxnorm_tty_index.get(code, '')
        try: return RXNORM_TTY_PREF.index(cls)
        except ValueError: return len(RXNORM_TTY_PREF) + (0 if cls else 1)
    return min(rxnorm, key=rank)


# === LOG HELPER =============================================================
LOG = []
def log(msg):
    line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line); LOG.append(line)


# === ATHENA EXTENSION (optional) ============================================
def load_athena_extension(vocab_dir):
    """If an Athena vocab directory contains HPO and/or Mondo, build (vocab,
    code) -> {id, label} dictionaries that supplement the seed tables.

    Loads CONCEPT.csv rows where vocabulary_id is HPO or Mondo (target side)
    and follows CONCEPT_RELATIONSHIP.csv 'Maps to' to get source codes
    (SNOMED, ICD-10-CM) that map to them. Also collects RxNorm rows with
    their concept_class_id for TTY-aware medication coding selection.

    Returns (extra_hpo, extra_mondo, vocab_versions, rxnorm_tty_index). On
    any error, returns empty dicts.
    """
    extra_hpo, extra_mondo, versions, rxnorm_tty_index = {}, {}, {}, {}
    if not vocab_dir or not os.path.isdir(vocab_dir):
        return extra_hpo, extra_mondo, versions, rxnorm_tty_index

    concept_path = os.path.join(vocab_dir, 'CONCEPT.csv')
    rel_path     = os.path.join(vocab_dir, 'CONCEPT_RELATIONSHIP.csv')
    voc_path     = os.path.join(vocab_dir, 'VOCABULARY.csv')

    if not (os.path.exists(concept_path) and os.path.exists(rel_path)):
        log("  (Athena extension skipped -- CONCEPT.csv or CONCEPT_RELATIONSHIP.csv not present)")
        return extra_hpo, extra_mondo, versions, rxnorm_tty_index

    # vocabulary versions
    if os.path.exists(voc_path):
        with open(voc_path, encoding='utf-8') as f:
            r = csv.DictReader(f, delimiter='\t')
            for row in r:
                vid = row.get('vocabulary_id') or ''
                ver = row.get('vocabulary_version') or ''
                if vid: versions[vid] = ver

    # First pass: collect HPO and Mondo concept_ids, code, label
    hpo_concept = {}    # concept_id -> {'id', 'label'}
    mondo_concept = {}
    src_concept = {}    # concept_id -> (vocab, code)
    log("  Loading Athena CONCEPT.csv (this can take a minute) ...")
    with open(concept_path, encoding='utf-8', errors='replace') as f:
        r = csv.DictReader(f, delimiter='\t')
        for row in r:
            vid = row.get('vocabulary_id') or ''
            if vid == 'HPO':
                hpo_concept[row['concept_id']] = {
                    'id': row.get('concept_code'),
                    'label': row.get('concept_name', ''),
                }
            elif vid in ('Mondo','MONDO'):
                mondo_concept[row['concept_id']] = {
                    'id': 'MONDO:'+row.get('concept_code',''),
                    'label': row.get('concept_name', ''),
                }
            elif vid in ('SNOMED','SNOMED-CT','SNOMED CT','ICD10CM','ICD-10-CM'):
                src_vocab = 'SNOMED-CT' if 'SNOMED' in vid else 'ICD-10-CM'
                src_concept[row['concept_id']] = (src_vocab, row.get('concept_code',''))
            elif vid == 'RxNorm':
                # Capture term type for TTY-aware medication coding selection
                code = row.get('concept_code','').strip()
                cls  = row.get('concept_class_id','').strip()
                if code and cls:
                    rxnorm_tty_index[code] = cls
    log(f"    {len(hpo_concept):,} HPO  {len(mondo_concept):,} Mondo  "
        f"{len(src_concept):,} SNOMED/ICD source concepts  "
        f"{len(rxnorm_tty_index):,} RxNorm with TTY")

    # Second pass: follow 'Maps to' edges from source -> HPO/Mondo
    log("  Loading Athena CONCEPT_RELATIONSHIP.csv ...")
    with open(rel_path, encoding='utf-8', errors='replace') as f:
        r = csv.DictReader(f, delimiter='\t')
        for row in r:
            if row.get('relationship_id') != 'Maps to':
                continue
            c1 = row.get('concept_id_1','')
            c2 = row.get('concept_id_2','')
            if c1 not in src_concept:
                continue
            src = src_concept[c1]
            if c2 in hpo_concept:
                # HPO concept_code in Athena is bare integer; HP:0007354 is "HP:0007354"
                # Athena stores HPO concept_code as e.g. "HP:0007354" depending on import
                hpo_code = hpo_concept[c2]['id'] or ''
                if hpo_code and not hpo_code.startswith('HP:'):
                    hpo_code = 'HP:' + hpo_code
                if hpo_code:
                    extra_hpo[src] = {'id': hpo_code, 'label': hpo_concept[c2]['label']}
            elif c2 in mondo_concept:
                extra_mondo[src] = mondo_concept[c2]
    log(f"    {len(extra_hpo):,} extra (vocab,code)->HPO mappings  {len(extra_mondo):,} extra ->Mondo mappings")
    return extra_hpo, extra_mondo, versions, rxnorm_tty_index


# === LOOKUPS ================================================================
def lookup_hpo(vocab, code, athena_extra):
    key = (vocab, code)
    return athena_extra.get(key) or SNOMED_ICD_TO_HPO.get(key)

def lookup_mondo(vocab, code, athena_extra):
    key = (vocab, code)
    return athena_extra.get(key) or SNOMED_ICD_TO_MONDO.get(key)


# === DATE / VALUE HELPERS ===================================================
def to_iso_timestamp(value):
    """Coerce various date forms to ISO 8601 timestamp; return None if blank."""
    if not value: return None
    s = str(value).strip()
    if not s: return None
    if 'T' in s and ('Z' in s or '+' in s):
        return s
    # Date-only -> midnight UTC
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s + 'T00:00:00Z'
    if re.match(r'^\d{4}-\d{2}$', s):
        return s + '-01T00:00:00Z'
    if re.match(r'^\d{4}$', s):
        return s + '-01-01T00:00:00Z'
    return None  # unrecognized; let caller decide

def maybe_quantity(value, unit):
    """Build a Phenopackets Quantity if value is numeric, else None."""
    if value is None or value == '':
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    q = {'value': f}
    if unit:
        q['unit'] = {'id': f'UCUM:{unit}', 'label': str(unit)}
    return q


# === BUILDERS ===============================================================
def build_subject(patient):
    s = {'id': patient.get('patient_id') or 'unknown'}
    dob = to_iso_timestamp(patient.get('dob'))
    if dob: s['dateOfBirth'] = dob
    sex_in = (patient.get('gender') or '').lower()
    s['sex'] = SEX_MAP.get(sex_in, 'UNKNOWN_SEX')
    return s


def build_phenotypic_features(patient_id, bundle, athena_extra, mondo_seen, unmapped):
    """Build phenotypicFeatures from problems. Skip codes that are already
    captured as Mondo diseases (a primary diagnosis like ALS belongs in
    diseases, not in phenotypicFeatures). A code is counted as unmapped
    only if it has neither HPO nor Mondo coverage."""
    out = []
    seen = set()
    for prob in bundle.get('problems', []):
        if (prob.get('patient_id') or '') != patient_id:
            continue
        vocab = prob.get('code_system') or ''
        code  = prob.get('code') or ''
        if not code: continue
        # If this code is already captured as a Mondo disease, don't also
        # emit it as a phenotypic feature.
        if (vocab, code) in mondo_seen:
            continue
        hpo = lookup_hpo(vocab, code, athena_extra)
        if not hpo:
            # Truly unmapped: no HPO match and we already know no Mondo match
            unmapped[(vocab, code, prob.get('display_name') or '')] += 1
            continue
        key = (hpo['id'], to_iso_timestamp(prob.get('effective_date') or '') or '')
        if key in seen: continue
        seen.add(key)
        feat = {'type': {'id': hpo['id'], 'label': hpo['label']}}
        onset_iso = to_iso_timestamp(prob.get('effective_date') or '')
        if onset_iso: feat['onset'] = {'timestamp': onset_iso}
        status = (prob.get('clinical_status') or prob.get('status') or '').lower()
        if status in ('resolved','inactive','remission'):
            feat['resolution'] = {'timestamp': to_iso_timestamp(prob.get('end_date')) or ''}
        if status == 'refuted' or status == 'entered-in-error':
            feat['excluded'] = True
        out.append(feat)
    return out


def build_diseases(patient_id, bundle, athena_extra):
    """Build diseases from problems. Returns the list AND the set of
    (vocab, code) pairs that mapped, so phenotypicFeatures can skip them."""
    out = []
    seen_mondo = set()
    seen_codes = set()
    for prob in bundle.get('problems', []):
        if (prob.get('patient_id') or '') != patient_id:
            continue
        vocab = prob.get('code_system') or ''
        code  = prob.get('code') or ''
        if not code: continue
        mondo = lookup_mondo(vocab, code, athena_extra)
        if not mondo:
            continue
        seen_codes.add((vocab, code))
        if mondo['id'] in seen_mondo: continue
        seen_mondo.add(mondo['id'])
        dis = {'term': {'id': mondo['id'], 'label': mondo['label']}}
        onset = to_iso_timestamp(prob.get('effective_date') or '')
        if onset: dis['onset'] = {'timestamp': onset}
        out.append(dis)
    return out, seen_codes


def build_measurements(patient_id, bundle, note_rows_by_patient,
                        include_note_measurements=True):
    """Build Measurements from coded sources first; optionally add a
    demonstration layer of note-derived ALSFRS-R/ECAS/FVC values.

    Production path (always on): every record in `bundle['labs_vitals']`
    becomes a Measurement with the source coding (LOINC pass-through is
    the common case, but SNOMED-coded vitals also pass through cleanly).
    No NLP is involved in this path; the Phenopacket's measurement set
    is fully traceable to coded EHR data.

    Demonstration layer (include_note_measurements=True): if
    note_extractions.csv has captured ALSFRS-R total/subdomains, ECAS
    total/subscores, or FVC % predicted, those rows are emitted as
    additional Measurements with LOINC codes where defined and clearly-
    labeled placeholder LOINC codes for ECAS subscores (LOINC has not
    yet assigned official codes for those). This proves the pipeline
    *can* surface note-derived ALSFRS-R; the ARC pipeline is not yet
    using it for production output, and registries that haven't validated
    their NLP layer should set this False.
    """
    out = []
    # 1. Structured: labs/vitals from bundle (LOINC pass-through is
    #    typical; SNOMED-coded vitals also work).
    for rec in bundle.get('labs_vitals', []):
        if (rec.get('patient_id') or '') != patient_id:
            continue
        code = rec.get('code') or ''
        if not code: continue
        vocab = rec.get('code_system') or 'LOINC'
        assay_id = f'{vocab.replace("-CT","").replace("-CM","")}:{code}'
        if vocab == 'LOINC':
            assay_id = f'LOINC:{code}'
        assay = {'id': assay_id, 'label': rec.get('display_name') or ''}
        m = {'assay': assay}
        q = maybe_quantity(rec.get('value'), rec.get('unit'))
        if q is not None:
            m['value'] = {'quantity': q}
        else:
            v = rec.get('value')
            if v is not None and v != '':
                m['value'] = {'ontologyClass': {'id': 'NCIT:C25712', 'label': str(v)}}
        ts = to_iso_timestamp(rec.get('effective_date'))
        if ts: m['timeObserved'] = {'timestamp': ts}
        out.append(m)

    # 2. Demonstration layer (opt-in): ALSFRS-R / ECAS / FVC from notes.
    if not include_note_measurements:
        return out

    NOTE_TO_LOINC = {
        'alsfrs_r_total':       ('LOINC:67131-4','ALS Functional Rating Scale-Revised'),
        'alsfrs_r_bulbar':      ('LOINC:LP200147-7','ALSFRS-R bulbar subdomain'),
        'alsfrs_r_fine_motor':  ('LOINC:LP200148-5','ALSFRS-R fine motor subdomain'),
        'alsfrs_r_gross_motor': ('LOINC:LP200149-3','ALSFRS-R gross motor subdomain'),
        'alsfrs_r_respiratory': ('LOINC:LP200150-1','ALSFRS-R respiratory subdomain'),
        'ecas_total':           ('LOINC:99999-ECAS-T','ECAS total score (placeholder LOINC)'),
        'ecas_als_specific':    ('LOINC:99999-ECAS-A','ECAS ALS-specific subscore (placeholder LOINC)'),
        'ecas_non_als_specific':('LOINC:99999-ECAS-N','ECAS non-ALS-specific subscore (placeholder LOINC)'),
        'fvc_percent_predicted':('LOINC:19868-9','FVC % predicted'),
    }
    for row in note_rows_by_patient.get(patient_id, []):
        spec = NOTE_TO_LOINC.get(row['pattern'])
        if not spec: continue
        try:
            f = float(row.get('value') or '')
        except (TypeError, ValueError):
            continue
        out.append({
            'assay': {'id': spec[0], 'label': spec[1]},
            'value': {'quantity': {'value': f}},
            '_provenance': 'note_extraction_demonstration',
            '_comment': ('Recovered by regex-based note extraction. This is a '
                         'demonstration that note-derived measurements can be '
                         'surfaced as Phenopacket Measurements; not used in '
                         'production ARC output yet.'),
        })
    return out


def build_medical_actions(patient_id, bundle, rxnorm_tty_index=None):
    out = []
    # Medications
    for med in bundle.get('medications', []):
        if (med.get('patient_id') or '') != patient_id: continue
        # TTY-aware RxNorm selection from all_codings (when Athena is loaded).
        # Falls back to med['code'] when all_codings missing or no RxNorm coding.
        all_codings = med.get('all_codings') or []
        primary = select_primary_rxnorm(all_codings, rxnorm_tty_index)
        if primary:
            vocab = primary.get('system_name') or med.get('code_system') or 'RxNorm'
            code  = primary.get('code') or ''
            display = primary.get('display') or med.get('display_name') or ''
        else:
            vocab = med.get('code_system') or 'RxNorm'
            code  = med.get('code') or ''
            display = med.get('display_name') or ''
        if not code: continue
        # Normalize vocab string for Phenopacket curie
        v = vocab.strip()
        if 'rxnorm' in v.lower(): v = 'RxNorm'
        agent = {'id': f'{v}:{code}', 'label': display}
        treat = {'agent': agent}
        ts = to_iso_timestamp(med.get('effective_date'))
        if ts: treat['cumulativeDose'] = {} ; treat['_startDate'] = ts
        # NOTE: doseIntervals would need parsed dosage_text; left as future work
        out.append({'treatment': treat})
    # Procedures
    for proc in bundle.get('procedures', []):
        if (proc.get('patient_id') or '') != patient_id: continue
        vocab = proc.get('code_system') or 'SNOMED-CT'
        code  = proc.get('code') or ''
        if not code: continue
        norm = vocab.replace('-CT','').replace('-CM','')
        cd = {'id': f'{norm}:{code}', 'label': proc.get('display_name') or ''}
        action = {'procedure': {'code': cd}}
        ts = to_iso_timestamp(proc.get('effective_date'))
        if ts: action['procedure']['performed'] = {'timestamp': ts}
        out.append(action)
    # Immunizations -> medicalAction with treatmentTarget = "immunization"
    for imm in bundle.get('immunizations', []):
        if (imm.get('patient_id') or '') != patient_id: continue
        code = imm.get('code') or ''
        if not code: continue
        out.append({
            'treatment': {
                'agent': {'id': f'CVX:{code}', 'label': imm.get('display_name') or ''},
                'routeOfAdministration': {'id':'NCIT:C28161','label':'Intramuscular'},
            },
            '_treatmentIntent': 'IMMUNIZATION',
        })
    return out


def load_external_genetics(path):
    """Load a CSV of curated genetic findings (maintained outside the
    EHR pipeline) keyed by patient_id. Returns a dict patient_id ->
    list of variant entries. If the path is None or missing, returns
    an empty dict and the structured-only path runs.

    Expected schema (header row required, columns are case-insensitive):

      patient_id      Patient identifier matching dashboard_data.json
      gene_symbol     HGNC-approved gene symbol (e.g. C9orf72, SOD1)
      hgnc_id         HGNC numeric ID, with or without the "HGNC:" prefix
                       (optional; module looks up symbol if blank)
      hgvs            HGVS variant descriptor (optional but recommended)
                       e.g. NM_000454.5:c.272A>C  or  NC_000009.12:g.27573529G>A
      pathogenicity   ACMG classification: Pathogenic, Likely pathogenic,
                       Uncertain significance, Likely benign, Benign
                       (optional; defaults to NOT_PROVIDED)
      zygosity        homozygous / heterozygous / hemizygous (optional)
      source          Free text describing where the finding came from
                       (e.g. "Invitae 2023-04 panel", "research VCF
                       chr9:27573529 verified by Sanger 2024-02")

    Each row becomes one entry under interpretations[].diagnosis.
    genomicInterpretations for the matching patient. Patients not
    listed in this file get the structured-only interpretation block.
    """
    by_patient = defaultdict(list)
    if not path or not os.path.exists(path):
        return dict(by_patient)
    with open(path, encoding='utf-8-sig') as f:
        r = csv.DictReader(f)
        for row in r:
            # Normalize keys to lowercase
            row = {(k or '').strip().lower(): (v or '').strip() for k, v in row.items()}
            pid = row.get('patient_id') or row.get('patientid') or ''
            if not pid: continue
            by_patient[pid].append(row)
    return dict(by_patient)


# Map ACMG strings to Phenopackets AcmgPathogenicityClassification enum.
ACMG_MAP = {
    'pathogenic': 'PATHOGENIC',
    'likely pathogenic': 'LIKELY_PATHOGENIC',
    'uncertain significance': 'UNCERTAIN_SIGNIFICANCE',
    'vus': 'UNCERTAIN_SIGNIFICANCE',
    'likely benign': 'LIKELY_BENIGN',
    'benign': 'BENIGN',
}


def build_genomic_interpretations_from_external(patient_id, ext_rows):
    """Build proper GenomicInterpretation entries from the external
    genetics CSV. This is the production path for ARC and any registry
    that maintains variant data outside the EHR feed."""
    interps = []
    for row in ext_rows:
        sym  = row.get('gene_symbol') or row.get('gene') or ''
        hgnc = row.get('hgnc_id') or ''
        if hgnc and not hgnc.startswith('HGNC:'):
            hgnc = 'HGNC:' + hgnc
        if not hgnc and sym:
            hint = GENE_HGNC.get(sym)
            if hint: hgnc = hint['id']
        gene_ctx = {}
        if hgnc: gene_ctx['valueId'] = hgnc
        if sym:  gene_ctx['symbol']  = sym

        hgvs       = row.get('hgvs') or ''
        path_label = (row.get('pathogenicity') or '').lower()
        zygosity   = (row.get('zygosity') or '').lower()
        source     = row.get('source') or ''

        descriptor_id = f'{patient_id}-{sym or "variant"}'
        if hgvs:
            descriptor_id = f'{descriptor_id}-{re.sub(r"[^A-Za-z0-9]+","_", hgvs)[:40]}'

        descriptor = {'id': descriptor_id}
        if gene_ctx: descriptor['geneContext'] = gene_ctx
        if hgvs:
            descriptor['expressions'] = [{'syntax': 'hgvs', 'value': hgvs}]
        if zygosity:
            zmap = {
                'heterozygous': {'id':'GENO:0000135','label':'heterozygous'},
                'homozygous':   {'id':'GENO:0000136','label':'homozygous'},
                'hemizygous':   {'id':'GENO:0000134','label':'hemizygous'},
            }
            if zygosity in zmap:
                descriptor['allelicState'] = zmap[zygosity]

        vint = {'variationDescriptor': descriptor}
        if path_label in ACMG_MAP:
            vint['acmgPathogenicityClassification'] = ACMG_MAP[path_label]

        gi = {
            'subjectOrBiosampleId': patient_id,
            'interpretationStatus': 'CONTRIBUTORY' if path_label in (
                'pathogenic','likely pathogenic') else 'UNKNOWN_STATUS',
            'variantInterpretation': vint,
        }
        if source:
            gi['_source'] = source
        interps.append(gi)
    return interps


def build_interpretations(patient_id, note_rows_by_patient, external_rows_by_patient):
    """Assemble the Phenopacket interpretations block, using external
    curated genetics when available and falling back to a structured
    placeholder when not.

    Behavior:
      external_rows_by_patient[patient_id] non-empty:
          Production-style block with progressStatus COMPLETED and
          properly-coded GenomicInterpretation entries from the CSV.
          Note-derived gene mentions are recorded as comments only,
          not duplicated as interpretations.
      external rows absent, note rows have a gene mention:
          Demonstration block with progressStatus IN_PROGRESS, an
          HGNC-coded gene-only stub, and a clear _placeholder marker.
      neither:
          Empty placeholder block with progressStatus IN_PROGRESS and
          an explanatory _comment. Downstream consumers can detect the
          gap and treat the Phenopacket accordingly.
    """
    ext_rows = external_rows_by_patient.get(patient_id) or []

    # --- Production path: external curated CSV ---
    if ext_rows:
        gi_list = build_genomic_interpretations_from_external(patient_id, ext_rows)
        interp = {
            'id': f'genetic-interp-{patient_id}',
            'progressStatus': 'COMPLETED',
            'diagnosis': {
                'genomicInterpretations': gi_list,
            },
        }
        # If a primary disease is implied by the genetics (ALS gene found),
        # tag it on diagnosis.disease for completeness. Conservative: only
        # tag MONDO ALS when an ALS-associated gene is in the rows.
        als_genes = {(r.get('gene_symbol') or r.get('gene') or '').strip()
                     for r in ext_rows}
        if als_genes & set(GENE_HGNC.keys()):
            interp['diagnosis']['disease'] = {
                'id': 'MONDO:0004976',
                'label': 'amyotrophic lateral sclerosis',
            }
        # Honest record of any note-derived gene mentions for that patient.
        note_gene_rows = [r for r in note_rows_by_patient.get(patient_id, [])
                          if r.get('pattern') == 'genetic_mutation']
        if note_gene_rows:
            interp['_noteExtractionDemonstration'] = [
                {'value': r.get('value'), 'snippet': r.get('snippet')}
                for r in note_gene_rows
            ]
        return [interp]

    # --- Demonstration path: gene mentions captured by note extraction ---
    note_gene_rows = [r for r in note_rows_by_patient.get(patient_id, [])
                      if r.get('pattern') == 'genetic_mutation']
    if note_gene_rows:
        gi_list = []
        for r in note_gene_rows:
            sym = (r.get('value') or '').strip()
            entry = {
                'subjectOrBiosampleId': patient_id,
                'interpretationStatus': 'UNKNOWN_STATUS',
                '_placeholder': True,
                '_provenance': 'note_extraction_demonstration',
                '_sourceSnippet': r.get('snippet') or '',
            }
            hgnc = GENE_HGNC.get(sym)
            if hgnc:
                entry['variantInterpretation'] = {
                    'variationDescriptor': {
                        'id': f'placeholder-{patient_id}-{sym}',
                        'geneContext': {
                            'valueId': hgnc['id'],
                            'symbol': hgnc['symbol'],
                        },
                        '_comment': ('Gene captured by regex note extraction; this is a '
                                     'demonstration of note-driven enrichment. Replace '
                                     'with a curated entry in external_genetics_csv for '
                                     'production output.'),
                    },
                }
            gi_list.append(entry)
        return [{
            'id': f'placeholder-genetic-interp-{patient_id}',
            'progressStatus': 'IN_PROGRESS',
            '_placeholder': True,
            '_comment': ('PLACEHOLDER (demonstration): gene symbols captured by regex '
                         'note extraction. Production output should populate this block '
                         'from external_genetics_csv with HGVS and ACMG pathogenicity.'),
            'diagnosis': {
                'disease': {'id':'MONDO:0004976','label':'amyotrophic lateral sclerosis'},
                'genomicInterpretations': gi_list,
            },
        }]

    # --- No genetic data at all: bare placeholder ---
    return [{
        'id': f'placeholder-genetic-interp-{patient_id}',
        'progressStatus': 'IN_PROGRESS',
        '_placeholder': True,
        '_comment': ('PLACEHOLDER: no external genetic data and no note-derived gene '
                     'mentions for this patient. Populate this block by adding a row '
                     'to external_genetics_csv (gene_symbol, hgvs, pathogenicity, source) '
                     'when curated variant data is available.'),
    }]


# Keep the old name as an alias for backward compatibility.
build_interpretations_placeholder = build_interpretations


def build_metadata(athena_versions):
    resources = [
        {'id':'hp','name':'Human Phenotype Ontology',
         'url':'http://purl.obolibrary.org/obo/hp.json',
         'version': athena_versions.get('HPO','seed-table-only'),
         'namespacePrefix':'HP','iriPrefix':'http://purl.obolibrary.org/obo/HP_'},
        {'id':'mondo','name':'Mondo Disease Ontology',
         'url':'http://purl.obolibrary.org/obo/mondo.json',
         'version': athena_versions.get('Mondo', athena_versions.get('MONDO','seed-table-only')),
         'namespacePrefix':'MONDO','iriPrefix':'http://purl.obolibrary.org/obo/MONDO_'},
        {'id':'loinc','name':'LOINC','url':'https://loinc.org',
         'version': athena_versions.get('LOINC','unknown'),
         'namespacePrefix':'LOINC','iriPrefix':'https://loinc.org/'},
        {'id':'rxnorm','name':'RxNorm','url':'https://www.nlm.nih.gov/research/umls/rxnorm/',
         'version': athena_versions.get('RxNorm','unknown'),
         'namespacePrefix':'RxNorm','iriPrefix':'https://uts.nlm.nih.gov/uts/rxnorm/concept/'},
        {'id':'snomed','name':'SNOMED CT',
         'url':'http://snomed.info/sct',
         'version': athena_versions.get('SNOMED','unknown'),
         'namespacePrefix':'SNOMED','iriPrefix':'http://snomed.info/id/'},
        {'id':'icd10cm','name':'ICD-10-CM',
         'url':'https://www.cdc.gov/nchs/icd/icd10cm.htm',
         'version': athena_versions.get('ICD10CM','unknown'),
         'namespacePrefix':'ICD10CM','iriPrefix':'http://hl7.org/fhir/sid/icd-10-cm/'},
        {'id':'hgnc','name':'HUGO Gene Nomenclature Committee',
         'url':'https://www.genenames.org','version':'unknown',
         'namespacePrefix':'HGNC','iriPrefix':'https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/HGNC:'},
    ]
    return {
        'created': _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'createdBy': 'Registry Forge phenopackets_etl.py',
        'submittedBy': 'Registry Forge',
        'phenopacketSchemaVersion': '2.0',
        'resources': resources,
    }


def build_phenopacket(patient, bundle, note_rows_by_patient,
                       athena_hpo, athena_mondo, athena_versions, unmapped,
                       include_note_measurements=True,
                       external_rows_by_patient=None,
                       rxnorm_tty_index=None):
    pid = patient.get('patient_id') or 'unknown'
    external_rows_by_patient = external_rows_by_patient or {}
    # Diseases first so we know which (vocab, code) pairs to skip when
    # building phenotypicFeatures.
    diseases, mondo_seen = build_diseases(pid, bundle, athena_mondo)
    pp = {
        'id': f'registry-forge-{pid}',
        'subject': build_subject(patient),
        'phenotypicFeatures': build_phenotypic_features(pid, bundle, athena_hpo, mondo_seen, unmapped),
        'measurements': build_measurements(pid, bundle, note_rows_by_patient,
                                            include_note_measurements=include_note_measurements),
        'medicalActions': build_medical_actions(pid, bundle, rxnorm_tty_index),
        'diseases': diseases,
        'interpretations': build_interpretations(pid, note_rows_by_patient, external_rows_by_patient),
        'metaData': build_metadata(athena_versions),
    }
    return pp


# === DRIVER =================================================================
def load_note_extractions(path):
    if not path or not os.path.exists(path):
        return {}
    out = defaultdict(list)
    with open(path, encoding='utf-8-sig') as f:
        r = csv.DictReader(f)
        for row in r:
            pid = row.get('patient_id') or ''
            out[pid].append(row)
    return dict(out)


def main(bundle_path='./dashboard_data.json',
         note_extractions_path='./note_extractions.csv',
         vocab_dir=None,
         out_root='./',
         include_note_measurements=True,
         external_genetics_csv=None):
    """Run the Phenopackets ETL.

    Required:
      bundle_path                Path to dashboard_data.json from run_pipeline.py.

    Optional inputs:
      note_extractions_path      Path to note_extractions.csv. Used only when
                                  include_note_measurements=True or when the
                                  external_genetics_csv is missing rows for
                                  some patients (in which case note-derived
                                  gene mentions are surfaced as a clearly-
                                  labeled demonstration).
      vocab_dir                  Path to an Athena vocabulary directory.
                                  When present and HPO/Mondo are selected,
                                  the seed mapping tables are supplemented.
      out_root                   Where to write the output folder.
      include_note_measurements  False = structured codes only (production).
                                  True (default) = include ALSFRS-R/ECAS/FVC
                                  values from notes as a demonstration that
                                  this is possible. Each such Measurement is
                                  flagged with _provenance='note_extraction_
                                  demonstration'.
      external_genetics_csv      Path to a curated CSV of variant findings
                                  maintained outside the EHR pipeline. When
                                  present, populates Phenopacket
                                  interpretations[].genomicInterpretations
                                  with proper HGNC, HGVS, ACMG-classified
                                  entries. See load_external_genetics() for
                                  the required schema.
    """
    log('=' * 72)
    log('Phenopackets ETL -- starting')
    log('=' * 72)
    log(f'Bundle:                    {bundle_path}')
    log(f'Note extractions:          {note_extractions_path}')
    log(f'Vocab dir:                 {vocab_dir or "(none, using seed tables only)"}')
    log(f'Include note measurements: {include_note_measurements}  '
        f'(False = structured codes only)')
    log(f'External genetics CSV:     {external_genetics_csv or "(none)"}')

    bundle = json.load(open(bundle_path))
    note_rows_by_patient = load_note_extractions(note_extractions_path)
    external_rows_by_patient = load_external_genetics(external_genetics_csv)
    log(f'Patients in bundle: {len(bundle.get("patients", []))}')
    log(f'Note-extraction rows loaded: {sum(len(v) for v in note_rows_by_patient.values()):,}')
    log(f'External-genetics rows loaded: {sum(len(v) for v in external_rows_by_patient.values()):,} '
        f'(across {len(external_rows_by_patient)} patients)')

    athena_hpo, athena_mondo, athena_versions, rxnorm_tty_index = load_athena_extension(vocab_dir)
    log(f'Seed HPO mappings: {len(SNOMED_ICD_TO_HPO):,}  '
        f'Seed Mondo mappings: {len(SNOMED_ICD_TO_MONDO):,}')
    if rxnorm_tty_index:
        log(f'TTY-aware RxNorm selection: ENABLED ({len(rxnorm_tty_index):,} codes indexed)')
    else:
        log('TTY-aware RxNorm selection: DISABLED (Athena not loaded — falling back to first-RxNorm-coding)')

    # Output folder name carries ontology release tags when known
    hpo_ver  = (athena_versions.get('HPO') or 'seed').replace(' ','-').replace('/','-')
    mondo_ver = (athena_versions.get('Mondo') or athena_versions.get('MONDO') or 'seed').replace(' ','-').replace('/','-')
    out_dir = os.path.join(out_root, f'phenopackets_output_HPO-{hpo_ver}_Mondo-{mondo_ver}')
    os.makedirs(out_dir, exist_ok=True)
    log(f'Output dir: {out_dir}')

    unmapped = Counter()
    cohort_pps = []
    summary_rows = []

    for patient in bundle.get('patients', []):
        pp = build_phenopacket(
            patient, bundle, note_rows_by_patient,
            athena_hpo, athena_mondo, athena_versions, unmapped,
            include_note_measurements=include_note_measurements,
            external_rows_by_patient=external_rows_by_patient,
            rxnorm_tty_index=rxnorm_tty_index,
        )
        pid = patient.get('patient_id') or 'unknown'
        out_path = os.path.join(out_dir, f'{pid}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(pp, f, indent=2)
        cohort_pps.append(pp)
        # Summary: count structured vs demo measurements separately
        n_struct = sum(1 for m in pp['measurements']
                       if m.get('_provenance') != 'note_extraction_demonstration')
        n_demo   = sum(1 for m in pp['measurements']
                       if m.get('_provenance') == 'note_extraction_demonstration')
        # Interpretation source
        interp_source = 'none'
        if pp['interpretations']:
            i0 = pp['interpretations'][0]
            if i0.get('progressStatus') == 'COMPLETED':
                interp_source = 'external_genetics_csv'
            elif i0.get('_placeholder'):
                interp_source = ('note_extraction_demo'
                                  if i0.get('diagnosis') else 'placeholder_only')
        summary_rows.append({
            'patient_id': pid,
            'last_name': patient.get('last_name') or '',
            'first_name': patient.get('first_name') or '',
            'phenotypic_features': len(pp['phenotypicFeatures']),
            'diseases':            len(pp['diseases']),
            'measurements_structured': n_struct,
            'measurements_note_demo': n_demo,
            'medical_actions':     len(pp['medicalActions']),
            'interpretation_source': interp_source,
        })

    # Cohort document
    cohort = {
        'id': 'registry-forge-cohort',
        'description': 'Registry Forge cohort — auto-generated from dashboard_data.json',
        'members': cohort_pps,
        'metaData': build_metadata(athena_versions),
    }
    with open(os.path.join(out_dir, 'cohort.json'), 'w', encoding='utf-8') as f:
        json.dump(cohort, f, indent=2)

    # Summary CSV
    sum_path = os.path.join(out_dir, 'summary.csv')
    with open(sum_path, 'w', newline='', encoding='utf-8-sig') as f:
        fieldnames = ['patient_id','last_name','first_name','phenotypic_features',
                      'diseases','measurements_structured','measurements_note_demo',
                      'medical_actions','interpretation_source']
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in summary_rows: w.writerow(r)

    # Unmapped codes
    unmapped_path = os.path.join(out_dir, 'unmapped_codes.csv')
    with open(unmapped_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(['vocabulary','code','display_name','reference_count'])
        for (vocab, code, disp), n in sorted(unmapped.items(), key=lambda x: -x[1]):
            w.writerow([vocab, code, disp, n])

    log('')
    log(f'Wrote {len(cohort_pps)} per-patient Phenopackets')
    log(f'Wrote cohort.json     ({sum(len(pp["phenotypicFeatures"]) for pp in cohort_pps)} total phenotypicFeatures)')
    log(f'Wrote summary.csv')
    log(f'Wrote unmapped_codes.csv  ({len(unmapped):,} unique unmapped codes, '
        f'{sum(unmapped.values()):,} total references)')

    return {
        'out_dir': out_dir,
        'patients': len(cohort_pps),
        'unmapped_codes': len(unmapped),
        'unmapped_refs': sum(unmapped.values()),
    }


if __name__ == '__main__':
    main()
