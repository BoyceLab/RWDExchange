"""
cohort_eda.py — Registry Forge cohort EDA report generator
============================================================
Reads `dashboard_data.json` and emits a single self-contained, interactive
HTML report summarizing cohort demographics, code coverage, and temporal
patterns. Output contains NO PHI:

  - Patient identifiers are replaced with deterministic synthetic IDs
    (PT-0001, PT-0002, …) assigned by sorted original identifier order
    so the mapping is stable across runs.
  - Dates of birth are converted to 10-year age bands; ages > 89 are
    collapsed to "90+" (HIPAA Safe Harbor §164.514(b)(2)(i)(C)).
  - Observation period is reported as a duration (years), not as
    absolute calendar dates.
  - Activity-over-time charts use year-level aggregation.
  - Any cross-tab cell whose count is less than `k` (default 5) is
    suppressed and rendered as "<5".
  - Free text from notes is never included in the output.
  - The per-patient table includes only synthetic ID, sex, age band,
    race, ethnicity, observation period (binned), and per-category
    record counts (binned). Codes and diagnoses are NOT shown
    per-patient to prevent re-identification of rare disease patients.

Usage
-----
from cohort_eda import generate_report

generate_report(
    bundle_path='./dashboard_data.json',
    out_path='./cohort_eda_report.html',
    k=5,                            # k-anonymity threshold
    cohort_name='ALS TDI ARC Study' # appears in the report header
)

The report opens in any browser, requires only an internet connection
for Chart.js to load (or use embed_chartjs=True for fully offline).
"""

import os
import json
import csv
import re
import hashlib
import datetime as _dt
from collections import defaultdict, Counter


# ============================================================================
# DEFAULTS & CONSTANTS
# ============================================================================
DEFAULT_K_ANONYMITY = 5

AGE_BANDS = ['<20', '20-29', '30-39', '40-49', '50-59',
             '60-69', '70-79', '80-89', '90+']

CATEGORIES = ['documents', 'encounters', 'problems', 'medications',
              'labs_vitals', 'observations', 'procedures', 'immunizations',
              'allergies', 'care_plans', 'goals', 'diagnostic_reports', 'notes']

# ALS-specific markers we surface as a dedicated panel
ALS_DISEASE_CODES = {
    ('ICD-10-CM', 'G12.21'): 'Amyotrophic lateral sclerosis',
    ('SNOMED-CT', '86044005'): 'Motor neuron disease',
    ('ICD-10-CM', 'G12.22'): 'Progressive bulbar palsy',
    ('ICD-10-CM', 'G12.23'): 'Primary lateral sclerosis',
    ('ICD-10-CM', 'G31.09'): 'Other frontotemporal dementia',
}
ALS_MEDICATION_PATTERNS = [
    (re.compile(r'\briluzole\b', re.I),  'Riluzole'),
    (re.compile(r'\bedaravone\b', re.I), 'Edaravone'),
    (re.compile(r'\btofersen\b', re.I),  'Tofersen (SOD1)'),
    (re.compile(r'\bsodium phenylbutyrate\b|\btaurursodiol\b|\brelyvrio\b', re.I), 'Phenylbutyrate-taurursodiol'),
]
ALS_OBSERVATION_LOINCS = {
    '67131-4': 'ALSFRS-R total',
    '67133-0': 'ALSFRS-R bulbar',
    '67134-8': 'ALSFRS-R fine motor',
    '67135-5': 'ALSFRS-R gross motor',
    '67136-3': 'ALSFRS-R respiratory',
    '19868-9': 'Forced vital capacity (FVC)',
}

# Date fields we'll look at on each record, in priority order
DATE_FIELDS = ['effective_date', 'start_date', 'authored_on',
               'onset_date_time', 'period_start', 'recorded_date',
               'date', 'occurrence_date_time']

# Friendly-name mapping for vocabulary identifiers. Both OIDs and URLs.
# When a record's vocabulary isn't already in human form (e.g. it's a raw OID),
# we map to a friendly label here. Anything starting with the Epic prefix
# 1.2.840.114350 collapses to 'Epic local' to keep the bar chart legible.
OID_FRIENDLY_NAMES = {
    '2.16.840.1.113883.6.96':   'SNOMED-CT',
    '2.16.840.1.113883.6.88':   'RxNorm',
    '2.16.840.1.113883.6.1':    'LOINC',
    '2.16.840.1.113883.6.90':   'ICD-10-CM',
    '2.16.840.1.113883.6.103':  'ICD-9-CM',
    '2.16.840.1.113883.6.12':   'CPT-4',
    '2.16.840.1.113883.6.13':   'CDT',
    '2.16.840.1.113883.6.4':    'ICD-10-PCS',
    '2.16.840.1.113883.6.14':   'HCPCS',
    '2.16.840.1.113883.6.285':  'HCPCS Level II',
    '2.16.840.1.113883.12.292': 'CVX',
    '2.16.840.1.113883.6.69':   'NDC',
    '2.16.840.1.113883.6.238':  'CDC Race & Ethnicity',
    '2.16.840.1.113883.5.83':   'HL7 ObservationInterpretation',
    '2.16.840.1.113883.6.59':   'CVX',
    '2.16.840.1.113883.5.4':    'HL7 ActCode',
    '2.16.840.1.113883.5.25':   'HL7 Confidentiality',
    '2.16.840.1.113883.5.111':  'HL7 RoleCode',
    '2.16.840.1.113883.4.642.4.224': 'HL7 AdministrativeGender',
    '2.16.840.1.113883.6.68':   'NDDF (First DataBank)',
    '2.16.840.1.113883.6.314':  'Medispan',
}
URL_FRIENDLY_NAMES = {
    'http://snomed.info/sct':                                   'SNOMED-CT',
    'http://www.nlm.nih.gov/research/umls/rxnorm':              'RxNorm',
    'http://loinc.org':                                         'LOINC',
    'http://hl7.org/fhir/sid/icd-10-cm':                        'ICD-10-CM',
    'http://hl7.org/fhir/sid/icd-10':                           'ICD-10-CM',
    'http://hl7.org/fhir/sid/icd-9-cm':                         'ICD-9-CM',
    'http://www.ama-assn.org/go/cpt':                           'CPT-4',
    'http://hl7.org/fhir/sid/cvx':                              'CVX',
    'http://hl7.org/fhir/sid/ndc':                              'NDC',
    'urn:oid:2.16.840.1.113883.6.238':                          'CDC Race & Ethnicity',
    'http://terminology.hl7.org/CodeSystem/v3-Race':            'CDC Race & Ethnicity',
    'http://terminology.hl7.org/CodeSystem/v3-Ethnicity':       'CDC Race & Ethnicity',
}
EPIC_OID_PREFIX = '1.2.840.114350'
CERNER_OID_PREFIX = '2.16.840.1.113883.3.247'

# Case-insensitive string aliases for vocabularies. Matches the lower-cased
# raw vocab to a canonical name. Catches the variants real EHR exports use.
STRING_ALIASES = {
    # SNOMED variants
    'snomed': 'SNOMED-CT', 'snomedct': 'SNOMED-CT', 'snomed ct': 'SNOMED-CT',
    'snomed-ct': 'SNOMED-CT', 'snomed_ct': 'SNOMED-CT', 'sct': 'SNOMED-CT',
    # ICD-10
    'icd10': 'ICD-10-CM', 'icd-10': 'ICD-10-CM', 'icd 10': 'ICD-10-CM',
    'icd10cm': 'ICD-10-CM', 'icd-10-cm': 'ICD-10-CM', 'icd-10cm': 'ICD-10-CM',
    'icd-10cm (diagnosis codes)': 'ICD-10-CM', 'icd-10-cm (diagnosis codes)': 'ICD-10-CM',
    # ICD-9
    'icd9': 'ICD-9-CM', 'icd-9': 'ICD-9-CM', 'icd 9': 'ICD-9-CM',
    'icd9cm': 'ICD-9-CM', 'icd-9-cm': 'ICD-9-CM', 'icd-9cm': 'ICD-9-CM',
    'icd-9cm (diagnosis codes)': 'ICD-9-CM', 'icd-9-cm (diagnosis codes)': 'ICD-9-CM',
    # RxNorm
    'rxnorm': 'RxNorm',
    # LOINC
    'loinc': 'LOINC',
    # CPT
    'cpt': 'CPT-4', 'cpt4': 'CPT-4', 'cpt-4': 'CPT-4', 'cpt (4)': 'CPT-4',
    # HCPCS
    'hcpcs': 'HCPCS', 'hcpcs level ii': 'HCPCS Level II',
    # CVX
    'cvx': 'CVX',
    # NDC
    'ndc': 'NDC',
    # IMO ProblemIT variants
    'problem-it': 'IMO ProblemIT', 'problemit': 'IMO ProblemIT',
    'imo': 'IMO ProblemIT', 'imo problemit': 'IMO ProblemIT',
    'intelligent medical objects problemit': 'IMO ProblemIT',
    'imo problem-it': 'IMO ProblemIT',
    # Medispan / Medi-Span — multiple naming conventions in Epic exports
    'medispan': 'Medispan',
    'medispan drug descriptor id': 'Medispan',
    'medi-span generic product identifier': 'Medispan',
    'medi-span': 'Medispan', 'mediscan': 'Medispan',
    # NDDF (First DataBank)
    'nddf': 'NDDF', 'nddf (first databank)': 'NDDF',
    'first databank': 'NDDF',
    # FHIR metadata code systems (not clinical content but show up as records)
    'data-absent-reason': 'FHIR DataAbsentReason',
    'observation-category': 'FHIR ObservationCategory',
    'condition-category': 'FHIR ConditionCategory',
    # Epic encounter and other FHIR-flavored encounter classes
    'fhir encounter class': 'FHIR encounter class',
}

# Substring patterns — anything containing these prefixes collapses to the
# canonical label. Catches Epic.* string-style namespaces and similar.
SUBSTRING_TO_LABEL = [
    ('epic.',                  'Epic local'),
    ('observation-flowsheet',  'Epic local'),  # Epic flowsheet IDs
    ('flowsheet-id',           'Epic local'),
    ('cerner.',                'Cerner local'),
]


def normalize_vocab(raw):
    """Map a raw vocabulary identifier (OID, URL, or string) to a friendly
    label. Heavily annotated by what real Epic exports actually contain.
    Returns the original string if no rule matches."""
    if not raw:
        return 'Unknown'
    s = str(raw).strip()
    lower = s.lower()

    # Strip urn:oid: prefix
    if lower.startswith('urn:oid:'):
        s = s[len('urn:oid:'):]
        lower = s.lower()

    # Direct OID match
    if s in OID_FRIENDLY_NAMES:
        return OID_FRIENDLY_NAMES[s]

    # URL match (full or via urn:oid:-stripped form)
    if s in URL_FRIENDLY_NAMES:
        return URL_FRIENDLY_NAMES[s]

    # OID-prefix matches — Epic and Cerner local code tables
    if s.startswith(EPIC_OID_PREFIX):    return 'Epic local'
    if s.startswith(CERNER_OID_PREFIX):  return 'Cerner local'

    # Case-insensitive direct string alias
    if lower in STRING_ALIASES:
        return STRING_ALIASES[lower]

    # Substring matches (Epic.*, observation-flowsheet-*, etc.)
    for needle, label in SUBSTRING_TO_LABEL:
        if needle in lower:
            return label

    # Bare integer with dots — looks like an unknown OID
    if re.match(r'^\d+(\.\d+)+$', s):
        return f'Other local (OID {s[:20]}…)'

    # Bare integer — likely an HL7 v2 table or local code system number
    if re.match(r'^\d+$', s):
        return f'Local table {s}'

    # URL we don't recognize — show last path segment
    if s.startswith('http://') or s.startswith('https://'):
        tail = s.rstrip('/').split('/')[-1]
        return tail or 'Unknown URL'

    # Otherwise return as-is (already a friendly label, probably)
    return s


# ============================================================================
# PSEUDONYMIZATION
# ============================================================================
def build_id_map(patient_ids):
    """Stable sorted-order pseudonymization. Same patient_id always gets
    the same PT-NNNN suffix as long as the same cohort is present."""
    sorted_ids = sorted(set(p for p in patient_ids if p))
    return {pid: f'PT-{i+1:04d}' for i, pid in enumerate(sorted_ids)}


# ============================================================================
# DEMOGRAPHIC NORMALIZATION
# ============================================================================
SEX_NORM = {
    'm': 'Male', 'male': 'Male', 'M': 'Male',
    'f': 'Female', 'female': 'Female', 'F': 'Female',
    'o': 'Other', 'other': 'Other',
    'u': 'Unknown', 'unknown': 'Unknown',
}

def normalize_sex(s):
    if not s: return 'Unknown'
    return SEX_NORM.get(str(s).strip().lower(), 'Other')


def parse_date(s):
    if not s: return None
    s = str(s)[:10]
    try:
        return _dt.date.fromisoformat(s)
    except ValueError:
        pass
    # Be lenient with /-separated or YYYYMMDD
    m = re.match(r'^(\d{4})[-/]?(\d{1,2})[-/]?(\d{1,2})$', s)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r'^(\d{4})$', s)
    if m:
        try:
            return _dt.date(int(m.group(1)), 7, 1)  # mid-year approximation
        except ValueError:
            return None
    return None


def compute_age(dob_str, ref_date):
    dob = parse_date(dob_str)
    if not dob: return None
    age = ref_date.year - dob.year - ((ref_date.month, ref_date.day) < (dob.month, dob.day))
    return max(0, age)


def age_band(age):
    if age is None: return 'Unknown'
    if age < 20:    return '<20'
    if age >= 90:   return '90+'
    decade = (age // 10) * 10
    return f'{decade}-{decade+9}'


def obs_period_band(years):
    if years is None: return 'Unknown'
    if years < 1:   return '<1 year'
    if years < 2:   return '1-2 years'
    if years < 5:   return '2-5 years'
    if years < 10:  return '5-10 years'
    if years < 20:  return '10-20 years'
    return '20+ years'


def record_count_band(n):
    if n == 0:      return '0'
    if n < 10:      return '1-9'
    if n < 50:      return '10-49'
    if n < 100:     return '50-99'
    if n < 500:     return '100-499'
    if n < 1000:    return '500-999'
    return '1000+'


# ============================================================================
# K-ANONYMITY
# ============================================================================
def k_safe(n, k):
    """Return n if n >= k else None (None renders as '<k' in HTML)."""
    return n if (n is None or n >= k) else None


def filter_counter_k(counter, k):
    """Apply k-anonymity to a Counter; small categories collapse to 'Other (<k)'."""
    safe = {}
    suppressed = 0
    for key, val in counter.items():
        if val >= k:
            safe[key] = val
        else:
            suppressed += val
    if suppressed:
        safe[f'Other (<{k} each)'] = suppressed
    return safe


# ============================================================================
# CORE AGGREGATION
# ============================================================================
def compute_eda(bundle, k=DEFAULT_K_ANONYMITY):
    patients = bundle.get('patients') or []
    if not patients:
        raise ValueError('Bundle has no patients[] array')

    id_map = build_id_map([p.get('patient_id') for p in patients])
    ref_date = _dt.date.today()

    # ---- Per-patient profile
    profile = {}
    for p in patients:
        pid = p.get('patient_id')
        if not pid: continue
        synth = id_map[pid]
        # Look for DOB under several common field names
        dob_str = (p.get('dob') or p.get('date_of_birth') or
                   p.get('birth_date') or p.get('birthDate') or '')
        age = compute_age(dob_str, ref_date)
        profile[synth] = {
            'id': synth,
            'sex': normalize_sex(p.get('gender') or p.get('sex')),
            'age': age,
            'age_band': age_band(age),
            'race': (p.get('race') or 'Unknown').strip()[:50] or 'Unknown',
            'ethnicity': (p.get('ethnicity') or 'Unknown').strip()[:50] or 'Unknown',
            'records': Counter(),
            'event_dates': [],
            'formats': Counter(),
            'als_evidence': set(),
        }

    # ---- Walk every category and aggregate
    by_year = defaultdict(lambda: defaultdict(int))
    code_freq = {}     # (vocab, code) -> {'n_records', 'patients': set, 'display', 'category'}
    cat_totals = Counter()
    cat_unique_codes = defaultdict(set)
    format_counts = Counter()

    for cat in CATEGORIES:
        for row in (bundle.get(cat) or []):
            pid = row.get('patient_id')
            if not pid or pid not in id_map: continue
            synth = id_map[pid]
            cat_totals[cat] += 1
            profile[synth]['records'][cat] += 1

            # Source format (FHIR / CCDA / PDF / RTF / HTML)
            src = (row.get('source_kind') or row.get('source_format') or '').lower()
            if 'fhir' in src:   fmt = 'FHIR'
            elif 'ccda' in src or 'cda' in src: fmt = 'CCDA'
            elif 'pdf' in src:  fmt = 'PDF'
            elif 'rtf' in src:  fmt = 'RTF'
            elif 'html' in src: fmt = 'HTML'
            else:               fmt = 'Unknown'
            format_counts[fmt] += 1
            profile[synth]['formats'][fmt] += 1

            # Walk all codings on this record
            codings = row.get('all_codings') or []
            if not codings and row.get('code'):
                codings = [{
                    'code': row.get('code'),
                    'system_name': row.get('code_system') or '',
                    'display': row.get('display_name') or '',
                }]
            for c in codings:
                raw_vocab = (c.get('system_name') or c.get('system_oid') or c.get('system') or '').strip()
                vocab_friendly = normalize_vocab(raw_vocab)
                code  = (c.get('code') or '').strip()
                if not code: continue
                # Use RAW vocab in key so codes from different Epic tables don't
                # collide (HGB in Epic table 451 vs Epic table 286 are different
                # concepts even though they display the same friendly name).
                key = (raw_vocab, code)
                if key not in code_freq:
                    code_freq[key] = {
                        'vocab': vocab_friendly,       # what we display
                        'raw_vocab': raw_vocab,         # full identifier (table OID) for tooltip
                        'code': code,
                        'display': (c.get('display') or '').strip()[:80],
                        'n_records': 0, 'patients': set(), 'category': cat,
                    }
                code_freq[key]['n_records'] += 1
                code_freq[key]['patients'].add(synth)
                cat_unique_codes[cat].add(key)

                # ALS signal detection (use normalized vocab names)
                if (vocab_friendly, code) in ALS_DISEASE_CODES:
                    profile[synth]['als_evidence'].add(ALS_DISEASE_CODES[(vocab_friendly, code)])
                if code in ALS_OBSERVATION_LOINCS and vocab_friendly == 'LOINC':
                    profile[synth]['als_evidence'].add(ALS_OBSERVATION_LOINCS[code])
                disp_lower = (c.get('display') or '').lower()
                for pat, label in ALS_MEDICATION_PATTERNS:
                    if pat.search(disp_lower):
                        profile[synth]['als_evidence'].add(label)
                        break

            # Date for trends + observation period
            d = None
            for f in DATE_FIELDS:
                v = row.get(f)
                if v:
                    d = parse_date(v)
                    if d: break
            if d:
                profile[synth]['event_dates'].append(d)
                by_year[d.year][cat] += 1

    # ---- Per-patient derived fields
    obs_years_list = []
    for synth, data in profile.items():
        dates = data['event_dates']
        if len(dates) >= 2:
            yrs = (max(dates) - min(dates)).days / 365.25
            data['obs_years'] = round(yrs, 2)
            obs_years_list.append(yrs)
        else:
            data['obs_years'] = None
        data['obs_band'] = obs_period_band(data['obs_years'])
        data['total_records'] = sum(data['records'].values())
        data['record_band'] = record_count_band(data['total_records'])
        # Discard raw dates for privacy
        del data['event_dates']

    # ---- Cohort-level metrics
    n_patients = len(profile)

    sex_counts = filter_counter_k(Counter(p['sex'] for p in profile.values()), k)
    age_counts = Counter(p['age_band'] for p in profile.values())
    age_counts_ordered = {band: age_counts.get(band, 0) for band in AGE_BANDS + ['Unknown']}
    age_counts_ordered = {b: c for b, c in age_counts_ordered.items() if c >= k or c == 0}

    race_counts = filter_counter_k(Counter(p['race'] for p in profile.values()), k)
    eth_counts  = filter_counter_k(Counter(p['ethnicity'] for p in profile.values()), k)

    obs_band_counts = Counter(p['obs_band'] for p in profile.values())
    obs_band_counts_ordered = {
        b: obs_band_counts.get(b, 0)
        for b in ['<1 year','1-2 years','2-5 years','5-10 years','10-20 years','20+ years','Unknown']
    }

    # ---- Per-patient table (no codes shown)
    patients_table = []
    for synth in sorted(profile.keys()):
        p = profile[synth]
        patients_table.append({
            'id': p['id'],
            'sex': p['sex'],
            'age_band': p['age_band'],
            'race': p['race'] if race_counts.get(p['race']) else 'Other',
            'ethnicity': p['ethnicity'] if eth_counts.get(p['ethnicity']) else 'Other',
            'obs_band': p['obs_band'],
            'record_band': p['record_band'],
            'total_records': p['total_records'],
            'als_markers': len(p['als_evidence']),
        })

    # ---- Data volume per category
    data_volume = {
        'records_per_category': dict(cat_totals),
        'unique_codes_per_category': {cat: len(s) for cat, s in cat_unique_codes.items()},
        'records_per_patient': sorted(p['total_records'] for p in profile.values()),
    }

    # ---- Trends: records per year per category (year-level aggregation)
    trends_years = sorted(by_year.keys())
    trends = {
        'years': trends_years,
        'by_category': {
            cat: [by_year[y].get(cat, 0) for y in trends_years]
            for cat in CATEGORIES if any(by_year[y].get(cat, 0) for y in trends_years)
        }
    }

    # ---- Vocabulary breakdown — group by friendly name (Epic local collapses
    # all its OID-specific tables into one bucket, etc.)
    vocab_counts = Counter()
    for _, info in code_freq.items():
        vocab_counts[info['vocab'] or 'Unknown'] += info['n_records']
    vocab_counts = filter_counter_k(vocab_counts, k)

    # ---- Top codes — show n_records (large numbers, not patient-identifying)
    # and patient counts with k-suppression. Use the friendly vocab name as
    # the display, but include the raw OID/URL on each row so analysts can
    # tell different Epic tables apart in the table view.
    top_codes_by_vocab = defaultdict(list)
    for key, info in code_freq.items():
        n_pts = len(info['patients'])
        top_codes_by_vocab[info['vocab'] or 'Unknown'].append({
            'code': info['code'],
            'display': info['display'] or '—',
            'category': info['category'],
            'raw_vocab': info['raw_vocab'][:60] if info['raw_vocab'] != info['vocab'] else '',
            'n_records': info['n_records'],
            'n_patients_safe': k_safe(n_pts, k),
            'n_patients_raw': n_pts,  # kept for sorting only
        })
    # Keep top 25 per vocab, sorted by records
    top_codes = {}
    for vocab, items in top_codes_by_vocab.items():
        items.sort(key=lambda r: (-r['n_records'], -r['n_patients_raw']))
        top_codes[vocab] = items[:25]
        for r in top_codes[vocab]:
            del r['n_patients_raw']

    # ---- ALS-specific signal
    als_marker_counts = Counter()
    n_with_any_als = 0
    for p in profile.values():
        if p['als_evidence']:
            n_with_any_als += 1
        for m in p['als_evidence']:
            als_marker_counts[m] += 1
    als_marker_counts_safe = {m: k_safe(n, k) for m, n in als_marker_counts.items()}

    als_signal = {
        'n_with_any_marker_safe': k_safe(n_with_any_als, k),
        'n_with_any_marker_pct':  round(100.0 * n_with_any_als / n_patients, 1) if n_patients else 0.0,
        'markers': als_marker_counts_safe,
    }

    # ---- Format breakdown
    format_breakdown = filter_counter_k(format_counts, k)

    return {
        'meta': {
            'generated': str(_dt.date.today()),
            'n_patients': n_patients,
            'k_anonymity': k,
            'categories_present': sorted(c for c in CATEGORIES if cat_totals[c] > 0),
        },
        'cohort': {
            'sex': sex_counts,
            'age_bands': age_counts_ordered,
            'race': race_counts,
            'ethnicity': eth_counts,
        },
        'data_volume': data_volume,
        'coverage': {
            'obs_years_list': sorted(round(y, 2) for y in obs_years_list),
            'obs_bands': obs_band_counts_ordered,
            'median_obs_years': round(sorted(obs_years_list)[len(obs_years_list)//2], 2) if obs_years_list else None,
        },
        'trends': trends,
        'vocabularies': vocab_counts,
        'top_codes': top_codes,
        'als_signal': als_signal,
        'format_breakdown': format_breakdown,
        'patients_table': patients_table,
    }


# ============================================================================
# HTML RENDERING
# ============================================================================
HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
:root {{
  --navy: #1e3a5f;
  --purple: #4a148c;
  --indigo: #3949ab;
  --teal: #0f6e56;
  --amber: #854f0b;
  --bg: #faf9f6;
  --card-bg: #ffffff;
  --border: #e5e3dc;
  --text: #2a2620;
  --muted: #6b6862;
  --header-grad: linear-gradient(135deg, #1e1633 0%, #2d1f4a 50%, #3949ab 100%);
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
}}
header {{
  background: var(--header-grad);
  color: #fff;
  padding: 32px 40px 28px 40px;
  border-bottom: 4px solid var(--purple);
}}
header .eyebrow {{
  display: inline-block;
  background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 999px;
  padding: 4px 14px;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 12px;
  color: #e8e0ff;
}}
header h1 {{
  margin: 0 0 6px 0;
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.01em;
}}
header h1 .forge {{ color: #a3b5ff; }}
header .subtitle {{
  margin: 8px 0 0 0;
  color: #d4cbf0;
  font-size: 14px;
}}
header .meta-strip {{
  margin-top: 18px;
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 12px;
  color: #b8aedb;
}}
header .meta-strip b {{ color: #e8e0ff; font-weight: 600; }}
header .privacy-note {{
  margin-top: 14px;
  padding: 10px 14px;
  background: rgba(255,255,255,0.06);
  border-left: 3px solid #fbbf24;
  border-radius: 4px;
  font-size: 12px;
  color: #ece4ff;
}}

nav.tabs {{
  background: #fff;
  border-bottom: 1px solid var(--border);
  padding: 0 40px;
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  gap: 4px;
  overflow-x: auto;
}}
nav.tabs button {{
  background: none;
  border: none;
  padding: 14px 18px;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
  cursor: pointer;
  border-bottom: 3px solid transparent;
  transition: all 150ms;
  white-space: nowrap;
}}
nav.tabs button:hover {{ color: var(--text); background: rgba(0,0,0,0.02); }}
nav.tabs button.active {{
  color: var(--purple);
  border-bottom-color: var(--purple);
}}

main {{ padding: 28px 40px 60px 40px; }}
section.panel {{ display: none; }}
section.panel.active {{ display: block; }}

h2.section-h {{
  font-size: 18px;
  font-weight: 700;
  color: var(--navy);
  margin: 0 0 8px 0;
}}
p.section-desc {{
  color: var(--muted);
  margin: 0 0 24px 0;
  font-size: 13px;
  max-width: 800px;
}}

.cards {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 28px;
}}
.card {{
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 20px;
}}
.card .label {{
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 8px;
}}
.card .value {{
  font-size: 26px;
  font-weight: 700;
  color: var(--purple);
  line-height: 1.1;
}}
.card .sub {{
  font-size: 12px;
  color: var(--muted);
  margin-top: 4px;
}}

.chart-row {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 24px;
}}
.chart-row.full {{ grid-template-columns: 1fr; }}
.chart-box {{
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 20px;
}}
.chart-box h3 {{
  margin: 0 0 4px 0;
  font-size: 14px;
  color: var(--navy);
  font-weight: 600;
}}
.chart-box .chart-desc {{
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 14px;
}}
.chart-canvas-wrap {{ position: relative; height: 280px; }}

table.dt {{
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}}
table.dt th {{
  background: #f3f1ec;
  text-align: left;
  padding: 10px 12px;
  font-weight: 600;
  color: var(--navy);
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}}
table.dt td {{
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}}
table.dt tr:last-child td {{ border-bottom: none; }}
table.dt tr:hover td {{ background: #faf8f3; }}
table.dt code {{
  background: #f3f1ec;
  padding: 2px 5px;
  border-radius: 3px;
  font-size: 12px;
  color: var(--purple);
  font-family: "SF Mono", Menlo, Consolas, monospace;
}}
.suppress {{ color: #999; font-style: italic; }}
.als-tag {{
  display: inline-block;
  background: #ede9f8;
  color: var(--purple);
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  margin-right: 4px;
  margin-bottom: 3px;
}}

.search-bar {{
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  align-items: center;
}}
.search-bar input {{
  flex: 1;
  padding: 9px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  background: #fff;
}}
.search-bar select {{
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  background: #fff;
}}

footer {{
  padding: 24px 40px;
  text-align: center;
  font-size: 11px;
  color: var(--muted);
  border-top: 1px solid var(--border);
  background: #fff;
}}
footer a {{ color: var(--purple); text-decoration: none; }}
</style>
</head>
<body>

<header>
  <div class="eyebrow">Registry Forge · cohort EDA report</div>
  <h1>{title} — <span class="forge">cohort exploration</span></h1>
  <div class="subtitle">Aggregate views of the codes and coverage exported through Registry Forge. No PHI is included; identifiers are pseudonymized and dates are aggregated.</div>
  <div class="meta-strip">
    <span><b>Generated</b> {generated}</span>
    <span><b>Patients</b> {n_patients}</span>
    <span><b>Categories</b> {n_categories}</span>
    <span><b>k-anonymity threshold</b> {k}</span>
  </div>
  <div class="privacy-note">
    <b>Privacy protections applied:</b> patient identifiers replaced with synthetic IDs (PT-NNNN); dates of birth converted to 10-year age bands (90+ collapsed per HIPAA Safe Harbor); observation period shown as duration only; cross-tab cells with N&lt;{k} suppressed as "&lt;{k}"; no free-text content included; per-patient codes/diagnoses not shown.
  </div>
</header>

<nav class="tabs">
  <button class="active" data-tab="cohort">Cohort</button>
  <button data-tab="volume">Data volume</button>
  <button data-tab="coverage">Coverage</button>
  <button data-tab="trends">Trends</button>
  <button data-tab="vocab">Vocabularies</button>
  <button data-tab="als">ALS signal</button>
  <button data-tab="codes">Top codes</button>
  <button data-tab="patients">Patients</button>
</nav>

<main>

<section class="panel active" id="cohort">
  <h2 class="section-h">Cohort overview</h2>
  <p class="section-desc">Demographics of the {n_patients} patient(s) in the bundle. Distributions are shown only where group size is at least {k}; smaller groups are aggregated as "Other".</p>
  <div class="cards" id="cohort-cards"></div>
  <div class="chart-row">
    <div class="chart-box">
      <h3>Sex distribution</h3>
      <div class="chart-desc">Proportion of cohort by reported sex.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-sex"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Age band (at report generation date)</h3>
      <div class="chart-desc">10-year age bands; ages over 89 collapsed per HIPAA Safe Harbor.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-age"></canvas></div>
    </div>
  </div>
  <div class="chart-row">
    <div class="chart-box">
      <h3>Race</h3>
      <div class="chart-desc">Self-reported race; categories below k-anonymity threshold collapsed.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-race"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Ethnicity</h3>
      <div class="chart-desc">Self-reported ethnicity; categories below k-anonymity threshold collapsed.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-eth"></canvas></div>
    </div>
  </div>
</section>

<section class="panel" id="volume">
  <h2 class="section-h">Data volume</h2>
  <p class="section-desc">What's in the bundle, by clinical category.</p>
  <div class="chart-row">
    <div class="chart-box">
      <h3>Total records per category</h3>
      <div class="chart-desc">Number of records the pipeline extracted, by bundle category.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-cat-records"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Unique codes per category</h3>
      <div class="chart-desc">Distinct (vocabulary, code) pairs appearing in each category.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-cat-codes"></canvas></div>
    </div>
  </div>
  <div class="chart-row">
    <div class="chart-box">
      <h3>Records per patient (distribution)</h3>
      <div class="chart-desc">Histogram of total records (any category) per patient.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-rpp"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Source format breakdown</h3>
      <div class="chart-desc">Where in the original SMART on FHIR pull each record came from.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-format"></canvas></div>
    </div>
  </div>
</section>

<section class="panel" id="coverage">
  <h2 class="section-h">Observation period</h2>
  <p class="section-desc">Per-patient duration from earliest to latest dated record. Reported as years; absolute dates are never shown.</p>
  <div class="cards" id="coverage-cards"></div>
  <div class="chart-row">
    <div class="chart-box">
      <h3>Observation period bands</h3>
      <div class="chart-desc">Patients grouped by length of observed history.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-obs-bands"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Distribution (years)</h3>
      <div class="chart-desc">Continuous distribution of observation period across the cohort.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-obs-dist"></canvas></div>
    </div>
  </div>
</section>

<section class="panel" id="trends">
  <h2 class="section-h">Activity over time</h2>
  <p class="section-desc">Records per year, stacked by clinical category. Year-level aggregation only — exact dates are not preserved.</p>
  <div class="chart-row full">
    <div class="chart-box">
      <h3>Records by year and category</h3>
      <div class="chart-desc">Stacked bars show the volume of each clinical category recorded per calendar year.</div>
      <div class="chart-canvas-wrap" style="height: 360px;"><canvas id="chart-trends"></canvas></div>
    </div>
  </div>
</section>

<section class="panel" id="vocab">
  <h2 class="section-h">Vocabulary distribution</h2>
  <p class="section-desc">Which coding systems carry the volume across all categories.</p>
  <div class="chart-row full">
    <div class="chart-box">
      <h3>Records by vocabulary</h3>
      <div class="chart-desc">Coding system used (each record can contribute to multiple via translations).</div>
      <div class="chart-canvas-wrap" style="height: 320px;"><canvas id="chart-vocab"></canvas></div>
    </div>
  </div>
</section>

<section class="panel" id="als">
  <h2 class="section-h">ALS-specific markers</h2>
  <p class="section-desc">Patients with at least one ALS-spectrum diagnosis code, ALSFRS-R / FVC observation, or ALS-directed medication. Counts below k-anonymity threshold are suppressed.</p>
  <div class="cards" id="als-cards"></div>
  <div class="chart-row full">
    <div class="chart-box">
      <h3>Patients per marker type</h3>
      <div class="chart-desc">Each patient may have multiple marker types; counts sum to more than the patient total.</div>
      <div class="chart-canvas-wrap"><canvas id="chart-als"></canvas></div>
    </div>
  </div>
</section>

<section class="panel" id="codes">
  <h2 class="section-h">Top codes by vocabulary</h2>
  <p class="section-desc">Top 25 codes per vocabulary, ranked by total references. Patient counts below k-anonymity threshold are suppressed.</p>
  <div class="search-bar">
    <input type="text" id="code-search" placeholder="Search code, vocabulary, or display name…">
    <select id="code-vocab-filter">
      <option value="">All vocabularies</option>
    </select>
  </div>
  <table class="dt" id="codes-table">
    <thead>
      <tr>
        <th>Vocabulary</th><th>Code</th><th>Display name</th>
        <th>Category</th><th style="text-align:right">Records</th>
        <th style="text-align:right">Patients</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</section>

<section class="panel" id="patients">
  <h2 class="section-h">Per-patient summary</h2>
  <p class="section-desc">One row per patient using pseudonymized identifiers. Diagnostic codes are not shown per patient to prevent re-identification of rare disease cases; record counts are also banded into ranges.</p>
  <div class="search-bar">
    <input type="text" id="pt-search" placeholder="Search by PT ID or band…">
    <select id="pt-sex-filter">
      <option value="">All sexes</option>
    </select>
  </div>
  <table class="dt" id="patients-table">
    <thead>
      <tr>
        <th>Synthetic ID</th><th>Sex</th><th>Age band</th>
        <th>Race</th><th>Ethnicity</th>
        <th>Observation</th><th>Records</th>
        <th>ALS markers</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</section>

</main>

<footer>
  Generated by <a href="https://github.com/BoyceLab/RegistryForge">Registry Forge</a> · cohort_eda.py module
  · Synthetic identifiers, banded ages, suppressed small cells per HIPAA Safe Harbor guidance
</footer>

<script>
const DATA = {data_json};

// --- Tab switching
document.querySelectorAll('nav.tabs button').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('nav.tabs button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('section.panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
  }});
}});

// --- Chart.js defaults
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif';
Chart.defaults.color = '#2a2620';
Chart.defaults.borderColor = '#e5e3dc';

const PALETTE = ['#4a148c','#3949ab','#0f6e56','#854f0b','#1e3a5f',
                  '#7b1fa2','#5e35b1','#039be5','#00897b','#43a047',
                  '#fb8c00','#e53935','#8e24aa','#6d4c41','#546e7a'];

function makeBar(canvasId, labels, values, color, horizontal=false) {{
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels,
      datasets: [{{ data: values, backgroundColor: color || PALETTE[0], borderRadius: 4, borderSkipped: false }}]
    }},
    options: {{
      indexAxis: horizontal ? 'y' : 'x',
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ grid: {{ display: !horizontal, color: '#f0eee7' }}, ticks: {{ font: {{ size: 11 }} }} }},
        y: {{ grid: {{ display: horizontal, color: '#f0eee7' }}, ticks: {{ font: {{ size: 11 }} }}, beginAtZero: true }}
      }}
    }}
  }});
}}

function makeDonut(canvasId, labels, values) {{
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  new Chart(ctx, {{
    type: 'doughnut',
    data: {{
      labels,
      datasets: [{{ data: values, backgroundColor: PALETTE, borderWidth: 2, borderColor: '#fff' }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }}, padding: 12 }} }} }},
      cutout: '60%'
    }}
  }});
}}

function makeStackedBar(canvasId, labels, datasets) {{
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  new Chart(ctx, {{
    type: 'bar',
    data: {{ labels, datasets: datasets.map((d, i) => ({{ ...d, backgroundColor: PALETTE[i % PALETTE.length], borderWidth: 0 }})) }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }}, padding: 8, boxWidth: 12 }} }} }},
      scales: {{
        x: {{ stacked: true, grid: {{ display: false }} }},
        y: {{ stacked: true, beginAtZero: true, grid: {{ color: '#f0eee7' }} }}
      }}
    }}
  }});
}}

function fmtN(n) {{ return n === null ? '<' + DATA.meta.k_anonymity : n.toLocaleString(); }}
function suppressed(v) {{ return v === null ? '<span class="suppress">&lt;' + DATA.meta.k_anonymity + '</span>' : v.toLocaleString(); }}

// === COHORT TAB ===
function renderCohortCards() {{
  const target = document.getElementById('cohort-cards');
  const sex = DATA.cohort.sex || {{}};
  const total = DATA.meta.n_patients;
  const female = sex['Female'] || 0;
  const female_pct = total ? Math.round(100 * female / total) : 0;
  const medianObs = DATA.coverage.median_obs_years;
  target.innerHTML = `
    <div class="card"><div class="label">Total patients</div><div class="value">${{total.toLocaleString()}}</div></div>
    <div class="card"><div class="label">Female</div><div class="value">${{female_pct}}%</div><div class="sub">${{suppressed(sex['Female'])}} of ${{total}}</div></div>
    <div class="card"><div class="label">Categories present</div><div class="value">${{DATA.meta.categories_present.length}}</div><div class="sub">${{DATA.meta.categories_present.join(', ')}}</div></div>
    <div class="card"><div class="label">Median observation</div><div class="value">${{medianObs !== null ? medianObs + 'y' : '—'}}</div></div>
  `;
}}
renderCohortCards();
makeDonut('chart-sex', Object.keys(DATA.cohort.sex), Object.values(DATA.cohort.sex));
makeBar('chart-age', Object.keys(DATA.cohort.age_bands), Object.values(DATA.cohort.age_bands), '#3949ab');
makeBar('chart-race', Object.keys(DATA.cohort.race), Object.values(DATA.cohort.race), '#0f6e56', true);
makeBar('chart-eth', Object.keys(DATA.cohort.ethnicity), Object.values(DATA.cohort.ethnicity), '#854f0b', true);

// === DATA VOLUME TAB ===
makeBar('chart-cat-records', Object.keys(DATA.data_volume.records_per_category),
        Object.values(DATA.data_volume.records_per_category), '#4a148c');
makeBar('chart-cat-codes', Object.keys(DATA.data_volume.unique_codes_per_category),
        Object.values(DATA.data_volume.unique_codes_per_category), '#3949ab');
// Records-per-patient histogram (bin into deciles)
(function() {{
  const vals = DATA.data_volume.records_per_patient;
  if (!vals.length) return;
  const max = Math.max(...vals);
  const bins = 10;
  const step = Math.ceil(max / bins) || 1;
  const labels = [], counts = [];
  for (let i = 0; i < bins; i++) {{
    labels.push(`${{i*step}}-${{(i+1)*step-1}}`);
    counts.push(vals.filter(v => v >= i*step && v < (i+1)*step).length);
  }}
  makeBar('chart-rpp', labels, counts, '#0f6e56');
}})();
makeDonut('chart-format', Object.keys(DATA.format_breakdown), Object.values(DATA.format_breakdown));

// === COVERAGE TAB ===
function renderCoverageCards() {{
  const obs = DATA.coverage.obs_years_list;
  const med = DATA.coverage.median_obs_years;
  const target = document.getElementById('coverage-cards');
  target.innerHTML = `
    <div class="card"><div class="label">Patients with ≥2 dated records</div><div class="value">${{obs.length}}</div></div>
    <div class="card"><div class="label">Median observation</div><div class="value">${{med !== null ? med + 'y' : '—'}}</div></div>
    <div class="card"><div class="label">Max observation</div><div class="value">${{obs.length ? obs[obs.length-1] + 'y' : '—'}}</div></div>
    <div class="card"><div class="label">Min observation</div><div class="value">${{obs.length ? obs[0] + 'y' : '—'}}</div></div>
  `;
}}
renderCoverageCards();
makeBar('chart-obs-bands', Object.keys(DATA.coverage.obs_bands), Object.values(DATA.coverage.obs_bands), '#4a148c');
// Distribution histogram of obs_years
(function() {{
  const vals = DATA.coverage.obs_years_list;
  if (!vals.length) {{ return; }}
  const max = Math.max(...vals, 1);
  const bins = 10;
  const step = max / bins;
  const labels = [], counts = [];
  for (let i = 0; i < bins; i++) {{
    labels.push(`${{(i*step).toFixed(1)}}-${{((i+1)*step).toFixed(1)}}y`);
    counts.push(vals.filter(v => v >= i*step && v < (i+1)*step).length);
  }}
  makeBar('chart-obs-dist', labels, counts, '#3949ab');
}})();

// === TRENDS TAB ===
(function() {{
  const t = DATA.trends;
  if (!t.years.length) return;
  const datasets = Object.entries(t.by_category).map(([cat, vals]) => ({{
    label: cat, data: vals
  }}));
  makeStackedBar('chart-trends', t.years, datasets);
}})();

// === VOCAB TAB ===
makeBar('chart-vocab', Object.keys(DATA.vocabularies), Object.values(DATA.vocabularies), '#4a148c', true);

// === ALS TAB ===
function renderAlsCards() {{
  const target = document.getElementById('als-cards');
  const ev = DATA.als_signal;
  target.innerHTML = `
    <div class="card"><div class="label">Patients with any ALS marker</div><div class="value">${{suppressed(ev.n_with_any_marker_safe)}}</div><div class="sub">${{ev.n_with_any_marker_pct}}% of cohort</div></div>
    <div class="card"><div class="label">Distinct marker types</div><div class="value">${{Object.keys(ev.markers).length}}</div></div>
  `;
}}
renderAlsCards();
(function() {{
  const m = DATA.als_signal.markers;
  if (!Object.keys(m).length) return;
  // Suppress nulls in chart by replacing with 0 but labeling as <k
  const labels = [], vals = [];
  for (const [k, v] of Object.entries(m)) {{
    labels.push(v === null ? `${{k}} (<${{DATA.meta.k_anonymity}})` : k);
    vals.push(v === null ? 0 : v);
  }}
  makeBar('chart-als', labels, vals, '#4a148c', true);
}})();

// === TOP CODES TAB ===
(function() {{
  const tbody = document.querySelector('#codes-table tbody');
  const filter = document.getElementById('code-vocab-filter');
  const search = document.getElementById('code-search');
  const allRows = [];
  for (const [vocab, codes] of Object.entries(DATA.top_codes)) {{
    const opt = document.createElement('option');
    opt.value = vocab; opt.textContent = vocab;
    filter.appendChild(opt);
    for (const c of codes) {{
      allRows.push({{vocab, ...c}});
    }}
  }}
  function render() {{
    const f = filter.value, s = search.value.toLowerCase();
    tbody.innerHTML = '';
    for (const r of allRows) {{
      if (f && r.vocab !== f) continue;
      const hay = (r.vocab + ' ' + r.code + ' ' + r.display).toLowerCase();
      if (s && !hay.includes(s)) continue;
      const tr = document.createElement('tr');
      const rawHint = r.raw_vocab ? `<div style="font-size:10px;color:#999;font-family:monospace;margin-top:2px">${{r.raw_vocab}}</div>` : '';
      tr.innerHTML = `
        <td><b>${{r.vocab}}</b>${{rawHint}}</td>
        <td><code>${{r.code}}</code></td>
        <td>${{r.display}}</td>
        <td>${{r.category}}</td>
        <td style="text-align:right">${{r.n_records.toLocaleString()}}</td>
        <td style="text-align:right">${{suppressed(r.n_patients_safe)}}</td>
      `;
      tbody.appendChild(tr);
    }}
  }}
  filter.addEventListener('change', render);
  search.addEventListener('input', render);
  render();
}})();

// === PATIENTS TAB ===
(function() {{
  const tbody = document.querySelector('#patients-table tbody');
  const sexFilter = document.getElementById('pt-sex-filter');
  const search = document.getElementById('pt-search');
  const rows = DATA.patients_table;
  const sexes = new Set(rows.map(r => r.sex));
  for (const sx of sexes) {{
    const o = document.createElement('option'); o.value = sx; o.textContent = sx;
    sexFilter.appendChild(o);
  }}
  function render() {{
    const f = sexFilter.value, s = search.value.toLowerCase();
    tbody.innerHTML = '';
    for (const r of rows) {{
      if (f && r.sex !== f) continue;
      const hay = (r.id + ' ' + r.age_band + ' ' + r.obs_band + ' ' + r.record_band).toLowerCase();
      if (s && !hay.includes(s)) continue;
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><code>${{r.id}}</code></td>
        <td>${{r.sex}}</td>
        <td>${{r.age_band}}</td>
        <td>${{r.race}}</td>
        <td>${{r.ethnicity}}</td>
        <td>${{r.obs_band}}</td>
        <td>${{r.record_band}}</td>
        <td>${{r.als_markers > 0 ? '<span class="als-tag">' + r.als_markers + ' markers</span>' : '—'}}</td>
      `;
      tbody.appendChild(tr);
    }}
  }}
  sexFilter.addEventListener('change', render);
  search.addEventListener('input', render);
  render();
}})();
</script>

</body>
</html>
'''


def render_report(eda_data, cohort_name='Registry Forge cohort'):
    """Render the EDA aggregations into a single self-contained HTML string."""
    return HTML_TEMPLATE.format(
        title=cohort_name,
        generated=eda_data['meta']['generated'],
        n_patients=eda_data['meta']['n_patients'],
        n_categories=len(eda_data['meta']['categories_present']),
        k=eda_data['meta']['k_anonymity'],
        data_json=json.dumps(eda_data, default=list),
    )


def generate_report(bundle_path='./dashboard_data.json',
                    out_path='./cohort_eda_report.html',
                    k=DEFAULT_K_ANONYMITY,
                    cohort_name='Registry Forge cohort'):
    """End-to-end entry point. Reads the bundle, computes aggregations,
    writes a single self-contained HTML file."""
    print(f'[{_dt.datetime.now().strftime("%H:%M:%S")}] Loading bundle: {bundle_path}')
    with open(bundle_path, encoding='utf-8') as f:
        bundle = json.load(f)
    print(f'[{_dt.datetime.now().strftime("%H:%M:%S")}] Computing aggregations (k={k}) ...')
    eda = compute_eda(bundle, k=k)
    print(f'[{_dt.datetime.now().strftime("%H:%M:%S")}] Rendering HTML ...')
    html_str = render_report(eda, cohort_name=cohort_name)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html_str)
    sz = os.path.getsize(out_path)
    print(f'[{_dt.datetime.now().strftime("%H:%M:%S")}] Wrote {out_path} ({sz:,} bytes)')
    print(f'  N patients: {eda["meta"]["n_patients"]:,}')
    print(f'  Categories present: {", ".join(eda["meta"]["categories_present"])}')
    print(f'  Median observation: {eda["coverage"]["median_obs_years"]}y' if eda["coverage"]["median_obs_years"] is not None else '  Median observation: N/A')
    return out_path


if __name__ == '__main__':
    generate_report()
