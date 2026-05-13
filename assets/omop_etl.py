"""
OMOP CDM v5.4 ETL
==================
Reads `dashboard_data.json` produced by run_pipeline.py and a folder of
OMOP vocabulary CSVs downloaded from Athena (https://athena.ohdsi.org),
and writes OMOP CDM tables to an output directory tagged with the
vocabulary release version.

What this does
--------------
For every coded record in the bundle:

    1. Look up the source code in the Athena CONCEPT.csv to get a
       source_concept_id (the concept identifier for the source vocabulary).
    2. Follow CONCEPT_RELATIONSHIP "Maps to" to find the standard
       concept_id (the OMOP concept everything pivots around).
    3. Use the standard concept's domain_id to decide which OMOP table
       the record belongs in (Condition -> CONDITION_OCCURRENCE,
       Drug -> DRUG_EXPOSURE, etc.).
    4. Write rows to the appropriate OMOP table, populating both the
       *_concept_id (standard) and *_source_concept_id (source) plus
       *_source_value (raw code text) so the lineage is preserved.

The output folder is named `omop_output_<vocab_version>/` so different
Athena releases don't overwrite each other. CDM_SOURCE.csv records
the exact vocabulary versions that were used for the run.

Usage
-----
Set the three paths below and run:

    python omop_etl.py

The defaults assume:

    ./dashboard_data.json    pipeline output (input)
    ./vocab/                 Athena vocab download (input)
    ./omop_output_<ver>/     where CDM tables go (output)

Athena vocabulary download
--------------------------
Visit https://athena.ohdsi.org, select at minimum these vocabularies
(other vocabs can be added but these cover the source codes we emit):

    SNOMED, ICD10CM, RxNorm, RxNorm Extension, LOINC, CVX, CPT4

Submit the bundle, wait for the email, download the zip, extract into
`./vocab/`. CPT4 requires a separate license-acceptance step that
Athena describes when you select it.
"""

import os
import csv
import json
import datetime as _dt
from collections import Counter, defaultdict


# --- USER CONFIGURATION -----------------------------------------------------
# Path to the bundle produced by run_pipeline.py
BUNDLE_PATH = './dashboard_data.json'

# Path to the unzipped Athena vocabulary download.
# Expected files inside this folder:
#   CONCEPT.csv, CONCEPT_RELATIONSHIP.csv, VOCABULARY.csv
#   DOMAIN.csv (optional), CONCEPT_CLASS.csv (optional)
VOCAB_DIR = './vocab/'

# Output base. The actual folder appended with '_<vocab_version>' is created
# at runtime so you can keep multiple Athena releases side by side.
OMOP_OUTPUT_BASE = './omop_output'
# ----------------------------------------------------------------------------


# Map FHIR / pipeline `system_name` values -> Athena `vocabulary_id`
SYSTEM_NAME_TO_VOCAB_ID = {
    'SNOMED-CT': 'SNOMED',
    'SNOMED CT': 'SNOMED',
    'SNOMED': 'SNOMED',
    'ICD-10-CM': 'ICD10CM',
    'ICD10CM': 'ICD10CM',
    'RxNorm': 'RxNorm',
    'LOINC': 'LOINC',
    'CVX': 'CVX',
    'CPT-4': 'CPT4',
    'CPT4': 'CPT4',
    'CPT': 'CPT4',
}

# RxNorm term-type preference for selecting the "best" coding when a
# MedicationRequest carries multiple RxNorm codings (e.g. Ingredient +
# Semantic Clinical Drug + Brand Name). Lower index = preferred.
# SCD/SBD encode ingredient + strength + dose form, which is what OHDSI
# DRUG_STRENGTH and downstream analytics key off. IN is ingredient-only
# (too generic). BN loses strength. Following OHDSI conventions.
RXNORM_TTY_PREF = [
    'Clinical Drug',           # SCD - ingredient + strength + dose form
    'Branded Drug',            # SBD - brand + ingredient + strength + form
    'Quant Clinical Drug',     # SCDC - quantified clinical drug
    'Quant Branded Drug',      # SBDC - quantified branded drug
    'Clinical Pack',           # GPCK
    'Branded Pack',            # BPCK
    'Clinical Drug Form',      # SCDF - ingredient + dose form
    'Branded Drug Form',       # SBDF - brand + dose form
    'Clinical Drug Comp',      # SCDG - clinical drug group
    'Branded Drug Comp',       # SBDG - branded drug group
    'Ingredient',              # IN - ingredient only (too generic for analytics)
    'Precise Ingredient',      # PIN
    'Brand Name',              # BN - brand only, no strength
    'Dose Form',               # DF
    'Dose Form Group',         # DFG
]
# Athena exposes TTYs via `concept_class_id` in CONCEPT.csv. The values
# above are Athena's display labels; Athena RxNorm rows store the TTY as
# e.g. "Clinical Drug" for SCD, not the bare "SCD" abbreviation.

# Hard-coded OMOP standard concept_ids that don't need vocab lookup
GENDER_CONCEPT = {'female': 8532, 'f': 8532, 'male': 8507, 'm': 8507}

# FHIR encounter class -> OMOP visit_concept_id (standard)
VISIT_CLASS_CONCEPT = {
    'AMB': 9202,    # Outpatient Visit
    'IMP': 9201,    # Inpatient Visit
    'EMER': 9203,   # Emergency Room Visit
    'HH':   581476, # Home Visit
    'OBSENC': 581478,  # Observation Room
}

OMOP_TYPE_CONCEPT_EHR = 32817  # "EHR" - used for *_type_concept_id columns


# ---------- Logging helper --------------------------------------------------
LOG = []
def log(msg):
    line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    LOG.append(line)


# ---------- Vocabulary loading ---------------------------------------------
def _read_vocab_csv(path, columns_needed=None, vocab_filter=None,
                    domain_filter=None):
    """Read an Athena CSV (tab-delimited, no quoting). Optional column / vocab
    / domain filters reduce memory before returning."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Athena vocabulary file not found: {path}\n"
            f"Download from https://athena.ohdsi.org and unzip into {VOCAB_DIR}"
        )
    rows = []
    # Tab-delimited, no quoting per Athena spec
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE)
        for row in reader:
            if vocab_filter and row.get('vocabulary_id') not in vocab_filter:
                continue
            if domain_filter and row.get('domain_id') not in domain_filter:
                continue
            if columns_needed:
                row = {k: row.get(k, '') for k in columns_needed}
            rows.append(row)
    return rows


def load_vocabularies(vocab_dir, source_vocabs):
    """Load the subset of Athena tables we need to do the mapping.

    Returns a dict with:
      'concept'         dict (vocab_id, concept_code) -> concept row
      'maps_to'         dict source_concept_id -> standard concept_id
      'concept_by_id'   dict concept_id -> concept row (for the
                                target standard concepts plus any source rows)
      'vocab_versions'  dict vocab_id -> version string
      'release_tag'     short string suitable for output-folder suffix
    """
    log(f"Loading vocabulary files from {vocab_dir}")

    # --- VOCABULARY.csv -- get release versions
    vocab_path = os.path.join(vocab_dir, 'VOCABULARY.csv')
    vocab_rows = _read_vocab_csv(vocab_path)
    vocab_versions = {r['vocabulary_id']: r.get('vocabulary_version', '')
                      for r in vocab_rows}
    log(f"  VOCABULARY.csv: {len(vocab_rows)} vocabularies registered")
    for vid in sorted(source_vocabs):
        log(f"    {vid:<10} version: {vocab_versions.get(vid, '<NOT IN DOWNLOAD>')}")

    # Choose a release tag: use SNOMED's date if present, otherwise the first
    # vocab's version, slugified
    candidate = (vocab_versions.get('SNOMED') or
                 vocab_versions.get('RxNorm') or
                 next(iter(vocab_versions.values()), 'unknown'))
    release_tag = (
        candidate.replace(' ', '_')
                 .replace('/', '-')
                 .replace(':', '-')
                 .replace(',', '')
                 [:60]
        or 'unknown'
    )

    # --- CONCEPT.csv -- filter to source vocabs + standard concepts in
    # target domains, in one pass.
    concept_path = os.path.join(vocab_dir, 'CONCEPT.csv')
    log("  CONCEPT.csv: streaming + filtering ...")
    target_domains = {'Condition', 'Drug', 'Procedure', 'Measurement',
                      'Observation', 'Visit'}
    # We need any concept whose vocabulary_id is in source_vocabs (these
    # are the candidates for source_concept_id lookup), plus any concept
    # that's standard and in a target domain (target of "Maps to"). Loading
    # standard concepts means we can look them up by id without a second pass.
    concepts_by_key = {}     # (vocab_id, concept_code) -> row
    concepts_by_id = {}      # concept_id -> row
    n_seen = 0
    with open(concept_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE)
        for row in reader:
            n_seen += 1
            vid = row.get('vocabulary_id', '')
            std = row.get('standard_concept', '')
            domain = row.get('domain_id', '')
            keep_as_source = vid in source_vocabs
            keep_as_target = (std == 'S' and domain in target_domains)
            if not (keep_as_source or keep_as_target):
                continue
            cid = row.get('concept_id', '')
            if keep_as_source:
                concepts_by_key[(vid, row.get('concept_code', ''))] = row
            if cid:
                concepts_by_id[cid] = row
    log(f"    scanned {n_seen:,} concepts")
    log(f"    kept {len(concepts_by_key):,} source-vocab rows + standards "
        f"in target domains (total {len(concepts_by_id):,} concepts indexed)")

    # --- CONCEPT_RELATIONSHIP.csv -- filter to "Maps to" only
    rel_path = os.path.join(vocab_dir, 'CONCEPT_RELATIONSHIP.csv')
    log("  CONCEPT_RELATIONSHIP.csv: streaming + filtering 'Maps to' ...")
    maps_to = {}  # source_concept_id -> standard concept_id
    n_seen = 0
    with open(rel_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE)
        for row in reader:
            n_seen += 1
            if row.get('relationship_id') != 'Maps to':
                continue
            invalid = row.get('invalid_reason', '')
            if invalid and invalid not in ('', 'NULL'):
                continue
            cid1 = row.get('concept_id_1', '')
            cid2 = row.get('concept_id_2', '')
            # Keep only rows where source side is one of our source vocab concepts
            if cid1 in concepts_by_id:
                maps_to[cid1] = cid2
    log(f"    scanned {n_seen:,} relationships, kept {len(maps_to):,} 'Maps to'")

    return {
        'concept': concepts_by_key,
        'maps_to': maps_to,
        'concept_by_id': concepts_by_id,
        'vocab_versions': vocab_versions,
        'release_tag': release_tag,
    }


# ---------- Mapping helpers ------------------------------------------------
def map_source_code(vocab, code, vocabs):
    """Given a (vocab system name, code), return:
        (source_concept_id, standard_concept_id, target_domain)
    Any field is '0' if not found."""
    vid = SYSTEM_NAME_TO_VOCAB_ID.get(vocab)
    if not vid or not code:
        return ('0', '0', '')
    src = vocabs['concept'].get((vid, code))
    if not src:
        return ('0', '0', '')
    src_id = src['concept_id']
    std_id = vocabs['maps_to'].get(src_id, '0')
    domain = ''
    if std_id and std_id != '0':
        std_row = vocabs['concept_by_id'].get(std_id)
        if std_row:
            domain = std_row.get('domain_id', '')
    return (src_id, std_id, domain)


# ---------- Date / datetime utilities --------------------------------------
def _parse_date(s):
    """Return (date_str_yyyy_mm_dd, datetime_str_iso) or ('', '')."""
    if not s:
        return ('', '')
    s = str(s).strip()
    # Try a few common formats
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            dt = _dt.datetime.strptime(s.replace('Z', '+0000'), fmt)
            return (dt.strftime('%Y-%m-%d'), dt.strftime('%Y-%m-%d %H:%M:%S'))
        except Exception:
            pass
    # Bare date string — accept first 10 chars if they look like YYYY-MM-DD
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        return (s[:10], s[:10])
    return ('', '')


# ---------- OMOP table builders --------------------------------------------
def build_person(bundle):
    """OMOP PERSON: one row per patient."""
    rows = []
    for i, p in enumerate(bundle.get('patients', []), start=1):
        gender = (p.get('gender') or '').lower().strip()
        gender_concept = GENDER_CONCEPT.get(gender, 0)
        dob = p.get('dob') or ''
        try:
            dt = _dt.datetime.strptime(dob, '%Y-%m-%d')
            year, month, day = dt.year, dt.month, dt.day
            birth_dt = dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            year = month = day = ''
            birth_dt = ''
        rows.append({
            'person_id': i,
            'gender_concept_id': gender_concept,
            'year_of_birth': year,
            'month_of_birth': month,
            'day_of_birth': day,
            'birth_datetime': birth_dt,
            'race_concept_id': 0,
            'ethnicity_concept_id': 0,
            'location_id': '',
            'provider_id': '',
            'care_site_id': '',
            'person_source_value': p.get('patient_id', ''),
            'gender_source_value': gender,
            'gender_source_concept_id': 0,
            'race_source_value': '',
            'race_source_concept_id': 0,
            'ethnicity_source_value': '',
            'ethnicity_source_concept_id': 0,
        })
    return rows


def _person_id_lookup(persons):
    """{patient_id: person_id}"""
    return {p['person_source_value']: p['person_id'] for p in persons}


def build_visit_occurrence(bundle, persons):
    """OMOP VISIT_OCCURRENCE."""
    pid_map = _person_id_lookup(persons)
    rows = []
    for i, e in enumerate(bundle.get('encounters', []), start=1):
        person_id = pid_map.get(e.get('patient_id', ''))
        if not person_id:
            continue
        sd, sdt = _parse_date(e.get('effective_date') or e.get('start_date', ''))
        ed, edt = _parse_date(e.get('end_date', ''))
        cls_code = (e.get('code') or '').strip()
        visit_concept = VISIT_CLASS_CONCEPT.get(cls_code, 0)
        rows.append({
            'visit_occurrence_id': i,
            'person_id': person_id,
            'visit_concept_id': visit_concept,
            'visit_start_date': sd,
            'visit_start_datetime': sdt,
            'visit_end_date': ed or sd,
            'visit_end_datetime': edt or sdt,
            'visit_type_concept_id': OMOP_TYPE_CONCEPT_EHR,
            'provider_id': '',
            'care_site_id': '',
            'visit_source_value': cls_code,
            'visit_source_concept_id': 0,
            'admitted_from_concept_id': 0,
            'admitted_from_source_value': '',
            'discharged_to_concept_id': 0,
            'discharged_to_source_value': '',
            'preceding_visit_occurrence_id': '',
        })
    return rows


def _visits_index(visits):
    """Build (person_id, date) -> visit_occurrence_id index for closest-visit
    matching. Returns sorted list per person."""
    idx = defaultdict(list)
    for v in visits:
        if v['visit_start_date']:
            idx[v['person_id']].append((v['visit_start_date'], v['visit_occurrence_id']))
    for k in idx:
        idx[k].sort()
    return idx


def _find_visit(person_id, date_str, idx):
    """Return visit_occurrence_id whose start_date <= date_str, latest match."""
    if not date_str: return ''
    candidates = idx.get(person_id, [])
    chosen = ''
    for vd, vid in candidates:
        if vd <= date_str:
            chosen = vid
        else:
            break
    return chosen


def _coded_records(rows):
    """Yield (row, system_name, code, display) for every coding on every row."""
    for row in rows:
        codings = row.get('all_codings') or []
        if not codings and row.get('code'):
            codings = [{
                'code': row.get('code'),
                'system_name': row.get('code_system') or '',
                'display': row.get('display_name') or '',
            }]
        for c in codings:
            yield (row, (c.get('system_name') or '').strip(),
                        (c.get('code') or '').strip(),
                        (c.get('display') or '').strip())


def select_primary_rxnorm(codings, vocabs):
    """Pick the best RxNorm coding from a CodeableConcept.coding[] array
    by RxNorm term-type (TTY) preference.

    The first stage of the pipeline picks "first matching RxNorm coding"
    which is order-dependent and can leave us with an Ingredient (IN) or
    Brand Name (BN) when the same MedicationRequest also carried a
    Semantic Clinical Drug (SCD). This selector re-ranks by TTY when
    Athena is loaded so SCD/SBD win over IN/BN. Falls back to first
    RxNorm coding when Athena is not available (preserving previous
    behavior). Falls back to first coding overall when no RxNorm coding
    is present.

    Returns a single coding dict (or None if `codings` is empty).
    """
    if not codings:
        return None
    rxnorm = [c for c in codings
              if 'rxnorm' in ((c.get('system_name') or '').lower())]
    if not rxnorm:
        return codings[0]
    if not vocabs or not vocabs.get('concept'):
        # No Athena loaded -- fall back to the previous behavior
        return rxnorm[0]
    concept_idx = vocabs['concept']
    def rank(c):
        code = (c.get('code') or '').strip()
        crow = concept_idx.get(('RxNorm', code))
        if not crow:
            return len(RXNORM_TTY_PREF) + 1  # unknown
        cls = (crow.get('concept_class_id') or '').strip()
        try:
            return RXNORM_TTY_PREF.index(cls)
        except ValueError:
            return len(RXNORM_TTY_PREF)
    # Stable min — preserves source order among ties
    return min(rxnorm, key=rank)


def build_clinical_tables(bundle, persons, visits, vocabs):
    """Build CONDITION_OCCURRENCE / DRUG_EXPOSURE / PROCEDURE_OCCURRENCE /
    MEASUREMENT / OBSERVATION using domain-of-standard-concept dispatch."""
    pid_map = _person_id_lookup(persons)
    vidx = _visits_index(visits)

    cond_rows, drug_rows, proc_rows, meas_rows, obs_rows = [], [], [], [], []
    cid = did = prid = mid = oid = 0
    unmapped = Counter()
    domain_routing = Counter()

    def route(category, src_field):
        nonlocal cid, did, prid, mid, oid
        for row in bundle.get(category, []):
            person_id = pid_map.get(row.get('patient_id', ''))
            if not person_id: continue
            sd, sdt = _parse_date(row.get('effective_date') or row.get('start_date')
                                  or row.get('authored_on') or '')
            visit_id = _find_visit(person_id, sd, vidx)
            # Pick the primary source coding. For medications, use TTY-aware
            # selection (SCD/SBD > IN/BN) when Athena is loaded; otherwise
            # first-match-wins (the historical behavior). All remaining
            # codings are still captured in code_inventory.csv via _coded_records.
            codings = row.get('all_codings') or []
            if not codings and row.get('code'):
                codings = [{
                    'code': row.get('code'),
                    'system_name': row.get('code_system') or '',
                    'display': row.get('display_name') or '',
                }]
            if not codings:
                continue
            if category == 'medications':
                primary = select_primary_rxnorm(codings, vocabs)
            else:
                primary = codings[0]
            vocab = (primary.get('system_name') or '').strip()
            code = (primary.get('code') or '').strip()
            display = (primary.get('display') or '').strip()
            src_id, std_id, domain = map_source_code(vocab, code, vocabs)
            if std_id == '0':
                unmapped[(vocab, code)] += 1
            domain_routing[(category, domain or 'Unmapped')] += 1

            # Domain-driven routing
            if domain == 'Condition':
                cid += 1
                cond_rows.append({
                    'condition_occurrence_id': cid,
                    'person_id': person_id,
                    'condition_concept_id': std_id or 0,
                    'condition_start_date': sd,
                    'condition_start_datetime': sdt,
                    'condition_end_date': '',
                    'condition_end_datetime': '',
                    'condition_type_concept_id': OMOP_TYPE_CONCEPT_EHR,
                    'condition_status_concept_id': 0,
                    'stop_reason': '',
                    'provider_id': '',
                    'visit_occurrence_id': visit_id,
                    'visit_detail_id': '',
                    'condition_source_value': code,
                    'condition_source_concept_id': src_id or 0,
                    'condition_status_source_value': row.get('status', ''),
                })
            elif domain == 'Drug':
                did += 1
                drug_rows.append({
                    'drug_exposure_id': did,
                    'person_id': person_id,
                    'drug_concept_id': std_id or 0,
                    'drug_exposure_start_date': sd,
                    'drug_exposure_start_datetime': sdt,
                    'drug_exposure_end_date': '',
                    'drug_exposure_end_datetime': '',
                    'verbatim_end_date': '',
                    'drug_type_concept_id': OMOP_TYPE_CONCEPT_EHR,
                    'stop_reason': '',
                    'refills': '',
                    'quantity': '',
                    'days_supply': '',
                    'sig': row.get('dosage_text') or row.get('display_name', ''),
                    'route_concept_id': 0,
                    'lot_number': '',
                    'provider_id': '',
                    'visit_occurrence_id': visit_id,
                    'visit_detail_id': '',
                    'drug_source_value': code,
                    'drug_source_concept_id': src_id or 0,
                    'route_source_value': '',
                    'dose_unit_source_value': '',
                })
            elif domain == 'Procedure':
                prid += 1
                proc_rows.append({
                    'procedure_occurrence_id': prid,
                    'person_id': person_id,
                    'procedure_concept_id': std_id or 0,
                    'procedure_date': sd,
                    'procedure_datetime': sdt,
                    'procedure_end_date': '',
                    'procedure_end_datetime': '',
                    'procedure_type_concept_id': OMOP_TYPE_CONCEPT_EHR,
                    'modifier_concept_id': 0,
                    'quantity': '',
                    'provider_id': '',
                    'visit_occurrence_id': visit_id,
                    'visit_detail_id': '',
                    'procedure_source_value': code,
                    'procedure_source_concept_id': src_id or 0,
                    'modifier_source_value': '',
                })
            elif domain == 'Measurement':
                mid += 1
                value = row.get('value')
                try: value = float(value)
                except (TypeError, ValueError): value = ''
                meas_rows.append({
                    'measurement_id': mid,
                    'person_id': person_id,
                    'measurement_concept_id': std_id or 0,
                    'measurement_date': sd,
                    'measurement_datetime': sdt,
                    'measurement_time': '',
                    'measurement_type_concept_id': OMOP_TYPE_CONCEPT_EHR,
                    'operator_concept_id': 0,
                    'value_as_number': value,
                    'value_as_concept_id': 0,
                    'unit_concept_id': 0,
                    'range_low': '',
                    'range_high': '',
                    'provider_id': '',
                    'visit_occurrence_id': visit_id,
                    'visit_detail_id': '',
                    'measurement_source_value': code,
                    'measurement_source_concept_id': src_id or 0,
                    'unit_source_value': row.get('unit', ''),
                    'unit_source_concept_id': 0,
                    'value_source_value': str(row.get('value', '')),
                })
            elif domain == 'Observation' or (domain == '' and category == 'allergies'):
                oid += 1
                obs_rows.append({
                    'observation_id': oid,
                    'person_id': person_id,
                    'observation_concept_id': std_id or 0,
                    'observation_date': sd,
                    'observation_datetime': sdt,
                    'observation_type_concept_id': OMOP_TYPE_CONCEPT_EHR,
                    'value_as_number': '',
                    'value_as_string': display,
                    'value_as_concept_id': 0,
                    'qualifier_concept_id': 0,
                    'unit_concept_id': 0,
                    'provider_id': '',
                    'visit_occurrence_id': visit_id,
                    'visit_detail_id': '',
                    'observation_source_value': code,
                    'observation_source_concept_id': src_id or 0,
                    'unit_source_value': '',
                    'qualifier_source_value': '',
                    'value_source_value': '',
                    'observation_event_id': '',
                    'obs_event_field_concept_id': 0,
                })
            # else: domain=Visit/empty/other -> not emitted as a clinical row;
            # encounter codes are already in VISIT_OCCURRENCE.

    # Source category -> resource type
    route('problems',           'Condition')
    route('medications',        'Drug')
    route('procedures',         'Procedure')
    route('labs_vitals',        'Measurement')
    route('allergies',          'Observation')
    route('immunizations',      'Drug')      # CVX usually maps to Drug domain
    route('diagnostic_reports', 'Measurement')

    return {
        'condition_occurrence': cond_rows,
        'drug_exposure': drug_rows,
        'procedure_occurrence': proc_rows,
        'measurement': meas_rows,
        'observation': obs_rows,
    }, unmapped, domain_routing


def build_observation_period(persons, all_dates):
    """Derive OBSERVATION_PERIOD per person from earliest/latest event dates."""
    by_person = defaultdict(list)
    for pid, d in all_dates:
        if d:
            by_person[pid].append(d)
    rows = []
    for i, person in enumerate(persons, start=1):
        dates = sorted(by_person.get(person['person_id'], []))
        if not dates: continue
        rows.append({
            'observation_period_id': i,
            'person_id': person['person_id'],
            'observation_period_start_date': dates[0],
            'observation_period_end_date': dates[-1],
            'period_type_concept_id': OMOP_TYPE_CONCEPT_EHR,
        })
    return rows


def build_cdm_source(vocabs, omop_output_dir):
    today = _dt.date.today().strftime('%Y-%m-%d')
    return [{
        'cdm_source_name': 'SMART on FHIR registry export',
        'cdm_source_abbreviation': 'SOF-REG',
        'cdm_holder': '',
        'source_description': ('OMOP CDM v5.4 ETL output from the SMART on FHIR '
                               'pipeline (run_pipeline.py)'),
        'source_documentation_reference': '',
        'cdm_etl_reference': 'omop_etl.py',
        'source_release_date': today,
        'cdm_release_date': today,
        'cdm_version': '5.4',
        'cdm_version_concept_id': 756265,  # CDM v5.4
        'vocabulary_version': '; '.join(
            f"{vid}:{ver}" for vid, ver in sorted(vocabs['vocab_versions'].items())
            if vid in {'SNOMED','ICD10CM','RxNorm','LOINC','CVX','CPT4'}
        ),
    }]


# ---------- Writer ---------------------------------------------------------
def _write_csv(rows, path, fieldnames=None):
    if not rows:
        # Still write an empty file with a header for downstream tools
        if fieldnames is None:
            fieldnames = []
    if rows and not fieldnames:
        fieldnames = list(rows[0].keys())
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------- Main -----------------------------------------------------------
def main(bundle_path=None, vocab_dir=None, omop_output_base=None):
    bundle_path = bundle_path or BUNDLE_PATH
    vocab_dir = vocab_dir or VOCAB_DIR
    omop_output_base = omop_output_base or OMOP_OUTPUT_BASE

    log("=" * 72)
    log("OMOP CDM v5.4 ETL -- starting")
    log("=" * 72)
    log(f"Bundle:    {bundle_path}")
    log(f"Vocab dir: {vocab_dir}")

    with open(bundle_path) as f:
        bundle = json.load(f)
    log(f"  Loaded bundle: {len(bundle.get('patients', []))} patients")

    # Which Athena vocabularies do we actually need?
    source_vocabs = set()
    for cat in ('problems','medications','procedures','allergies','immunizations',
                'labs_vitals','diagnostic_reports','encounters'):
        for row in bundle.get(cat, []):
            for c in row.get('all_codings') or []:
                vid = SYSTEM_NAME_TO_VOCAB_ID.get((c.get('system_name') or '').strip())
                if vid: source_vocabs.add(vid)
    log(f"  Source vocabularies in bundle: {sorted(source_vocabs)}")

    # Always include the standard target vocabs so we can resolve "Maps to"
    # destinations even when the source side is something like ICD10CM that
    # maps OUT to SNOMED.
    target_vocabs = {'SNOMED', 'RxNorm'}
    vocabs = load_vocabularies(vocab_dir, source_vocabs | target_vocabs)

    out_dir = f"{omop_output_base}_{vocabs['release_tag']}"
    os.makedirs(out_dir, exist_ok=True)
    log(f"\nOutput folder: {out_dir}")

    log("\nBuilding PERSON ...")
    persons = build_person(bundle)
    log(f"  {len(persons)} rows")

    log("\nBuilding VISIT_OCCURRENCE ...")
    visits = build_visit_occurrence(bundle, persons)
    log(f"  {len(visits)} rows")

    log("\nBuilding clinical tables (routing by domain of standard concept) ...")
    tables, unmapped, domain_routing = build_clinical_tables(
        bundle, persons, visits, vocabs)
    for name, rows in tables.items():
        log(f"  {name:<25} {len(rows):>6}")

    log("\nDomain routing summary (source category -> domain of std concept):")
    for (cat, dom), n in sorted(domain_routing.items()):
        log(f"  {cat:<25} -> {dom:<15} {n:>4}")

    if unmapped:
        log(f"\nUnmapped codes ({len(unmapped)} unique):")
        for (vocab, code), n in unmapped.most_common(20):
            log(f"  [{n}x] {vocab:<10} {code}")

    log("\nBuilding OBSERVATION_PERIOD ...")
    all_dates = []
    for person in persons:
        pid = person['person_id']
    for tbl_name, date_field in (
        ('condition_occurrence', 'condition_start_date'),
        ('drug_exposure', 'drug_exposure_start_date'),
        ('procedure_occurrence', 'procedure_date'),
        ('measurement', 'measurement_date'),
        ('observation', 'observation_date'),
    ):
        for r in tables[tbl_name]:
            all_dates.append((r['person_id'], r.get(date_field, '')))
    for v in visits:
        all_dates.append((v['person_id'], v.get('visit_start_date', '')))
    obs_period = build_observation_period(persons, all_dates)
    log(f"  {len(obs_period)} rows")

    log("\nBuilding CDM_SOURCE ...")
    cdm_source = build_cdm_source(vocabs, out_dir)
    log(f"  vocabulary_version: {cdm_source[0]['vocabulary_version']}")

    # Write everything
    log("\nWriting OMOP tables ...")
    _write_csv(persons, os.path.join(out_dir, 'PERSON.csv'))
    _write_csv(visits, os.path.join(out_dir, 'VISIT_OCCURRENCE.csv'))
    _write_csv(tables['condition_occurrence'], os.path.join(out_dir, 'CONDITION_OCCURRENCE.csv'))
    _write_csv(tables['drug_exposure'], os.path.join(out_dir, 'DRUG_EXPOSURE.csv'))
    _write_csv(tables['procedure_occurrence'], os.path.join(out_dir, 'PROCEDURE_OCCURRENCE.csv'))
    _write_csv(tables['measurement'], os.path.join(out_dir, 'MEASUREMENT.csv'))
    _write_csv(tables['observation'], os.path.join(out_dir, 'OBSERVATION.csv'))
    _write_csv(obs_period, os.path.join(out_dir, 'OBSERVATION_PERIOD.csv'))
    _write_csv(cdm_source, os.path.join(out_dir, 'CDM_SOURCE.csv'))

    # Save log
    with open(os.path.join(out_dir, 'omop_etl.log'), 'w') as f:
        f.write('\n'.join(LOG))

    log(f"\nOMOP ETL complete. Tables written to {out_dir}")


if __name__ == '__main__':
    main()
