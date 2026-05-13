"""
SMART on FHIR Registry ETL Pipeline
====================================
End-to-end pipeline for clinical registry data extracted from a SMART-on-FHIR
data warehouse. Six stages turn chunked CCDA + FHIR exports into a single
queryable bundle.

Pipeline stages
---------------
Stage 1  Decoding & reassembly       - reassemble warehouse chunks; double-base64 decode CCDA
Stage 2  Heterogeneous parsing       - magic-byte format detection: CCDA / RTF / PDF / HTML
Stage 3  FHIR resource extraction    - 13 resource types with code-system-aware coding walk
Stage 4  Joining & assembly          - merge CCDA + FHIR; dedupe by name+DOB; field aliasing
Stage 5  Display-name enrichment     - LOINC/SNOMED lookup backfill
Stage 6  Test-patient filter         - exclusion file with 4 match modes

Key features (vs prior versions)
--------------------------------
- extract_all_codings() walks every <translation> child of <code>, fixing the bug
  where outer NDC was kept but inner RxNorm was discarded. Same logic for FHIR coding[].
- Pre-pass over ALL FHIR bundles builds a global Medication index and a global
  DocumentReference->patient_id index. Critical for ARC's per-resource-type-per-patient
  bundle layout where MedicationRequest references Medications in a separate file.
- uuid_mapping.csv (or document_patient_mapping.csv) bridges document UUIDs to
  patient_ids for ALL document formats (CCDA, HTML, RTF, PDF), not just CCDAs that
  contain a recordTarget.
- Patient demographics filled from the mapping file when FHIR Patient resources
  are absent. Patients deduplicated by (first_name, last_name, dob).
- Per-record field aliases added to support both our schema and the original Box
  dashboard's schema (start_date, allergen, vaccine, narrative_text, num_documents, etc.).
- Documents tagged with both source_format (e.g. 'ccda_xml') and source_type
  ('rtf_note' for the original dashboard).

Author: ALS Therapy Development Institute
"""

# ============================================================================
# PART A - IMPORTS & CONFIG
# ============================================================================
import os
import io
import re
import json
import base64
import sys
import unicodedata
from datetime import datetime
from collections import defaultdict
import xml.etree.ElementTree as ET
import logging

# Silence pypdf chatty warnings about non-standard but recoverable PDF internals
logging.getLogger('pypdf').setLevel(logging.ERROR)
logging.getLogger('pypdf._reader').setLevel(logging.ERROR)
logging.getLogger('pypdf.filters').setLevel(logging.ERROR)
logging.getLogger('pypdf.generic').setLevel(logging.ERROR)

import pandas as pd

try:
    from pypdf import PdfReader
    HAVE_PYPDF = True
except ImportError:
    HAVE_PYPDF = False

try:
    from google.colab import drive  # noqa: F401
    IN_COLAB = True
except ImportError:
    IN_COLAB = False


# --- USER CONFIGURATION ------------------------------------------------------
# BASE_DIR is the working directory. Inputs are read from
# `<BASE_DIR>/CCDA and FHIR data/`; outputs (dashboard_data.json, csv_exports/,
# etc.) are written to `<BASE_DIR>/`. Defaults to the current directory.
BASE_DIR = './'

# Optional secondary input location, e.g. a cloud-storage mount. The pipeline
# checks here for the mapping CSV and exclusion file if they aren't in
# BASE_DIR. Set to None if not used.
DRIVE_DIR = None

CCDA_CSV = os.path.join(BASE_DIR, 'CCDA and FHIR data/ccda_chunks.csv')
FHIR_CSV = os.path.join(BASE_DIR, 'CCDA and FHIR data/fhir_chunks.csv')

# Mapping CSV candidates (tried in order; None entries skipped)
MAPPING_CANDIDATES = [p for p in [
    os.path.join(BASE_DIR, 'uuid_mapping.csv'),
    os.path.join(DRIVE_DIR, 'uuid_mapping.csv') if DRIVE_DIR else None,
    os.path.join(BASE_DIR, 'document_patient_mapping.csv'),
    os.path.join(DRIVE_DIR, 'document_patient_mapping.csv') if DRIVE_DIR else None,
] if p]

# Test-patient exclusion file candidates
EXCLUSION_CANDIDATES = [p for p in [
    os.path.join(BASE_DIR, 'test_patients.txt'),
    os.path.join(DRIVE_DIR, 'test_patients.txt') if DRIVE_DIR else None,
] if p]

# ----------------------------------------------------------------------------
# HARDCODED TEST PATIENT EXCLUSIONS - edit this list ONCE and forget about it.
# These rules are merged with anything in test_patients.txt and ALWAYS apply,
# even when the file is missing or empty. Each entry is "type: value":
#   name:           exact full-name match (case-insensitive)
#   name_contains:  first or last name contains substring (case-insensitive)
#   mrn_contains:   MRN contains substring (case-insensitive)
#   id:             exact patient_id (UUID)
# ----------------------------------------------------------------------------
HARDCODED_TEST_EXCLUSIONS = [
    # Replace these with your real test patient names. Examples:
    # 'name: Zztest Patient',
    # 'name: Test One',
    # 'name_contains: zztest',
    # 'mrn_contains: 99999',
]

CCDA_DIR = os.path.join(BASE_DIR, 'ccda_assembled/')
FHIR_DIR = os.path.join(BASE_DIR, 'fhir_assembled/')

OUT_JSON   = os.path.join(BASE_DIR, 'dashboard_data.json')
OUT_XLSX   = os.path.join(BASE_DIR, 'dashboard_data.xlsx')
OUT_BACKUP = os.path.join(BASE_DIR, 'dashboard_data.prefilter.json')
OUT_LOG    = os.path.join(BASE_DIR, 'pipeline_run.log')
OUT_CSV_DIR = os.path.join(BASE_DIR, 'csv_exports/')

# HL7 namespace for CCDA XPath
NS = {'hl7': 'urn:hl7-org:v3', 'xsi': 'http://www.w3.org/2001/XMLSchema-instance'}

# CCDA section templateIds - HL7 CDA R2 standard
TPL = {
    'problems_section':   '2.16.840.1.113883.10.20.22.2.5',
    'medications_section':'2.16.840.1.113883.10.20.22.2.1',
    'results_section':    '2.16.840.1.113883.10.20.22.2.3',
    'vitals_section':     '2.16.840.1.113883.10.20.22.2.4',
    'encounters_section': '2.16.840.1.113883.10.20.22.2.22',
    'allergies_section':  '2.16.840.1.113883.10.20.22.2.6',
    'immunizations_sec':  '2.16.840.1.113883.10.20.22.2.2',
    'procedures_section': '2.16.840.1.113883.10.20.22.2.7',
}

# Code-system prioritization. Order = preference for picking the canonical row code.
CODE_SYSTEM_PREF = {
    'medication':   ['rxnorm', '2.16.840.1.113883.6.88'],
    'problem':      ['snomed', '2.16.840.1.113883.6.96', 'icd-10', '2.16.840.1.113883.6.90'],
    'lab':          ['loinc',  '2.16.840.1.113883.6.1'],
    'vital':        ['loinc',  '2.16.840.1.113883.6.1'],
    'procedure':    ['cpt',    '2.16.840.1.113883.6.12', 'snomed', '2.16.840.1.113883.6.96'],
    'allergy':      ['rxnorm', '2.16.840.1.113883.6.88', 'snomed', '2.16.840.1.113883.6.96'],
    'immunization': ['cvx',    '2.16.840.1.113883.12.292'],
    'encounter':    ['cpt',    '2.16.840.1.113883.6.12', 'snomed', '2.16.840.1.113883.6.96'],
}

OID_TO_NAME = {
    '2.16.840.1.113883.6.88':  'RxNorm',
    '2.16.840.1.113883.6.96':  'SNOMED-CT',
    '2.16.840.1.113883.6.90':  'ICD-10-CM',
    '2.16.840.1.113883.6.103': 'ICD-9-CM',
    '2.16.840.1.113883.6.1':   'LOINC',
    '2.16.840.1.113883.6.12':  'CPT-4',
    '2.16.840.1.113883.12.292':'CVX',
    '2.16.840.1.113883.6.69':  'NDC',
}

LOG_LINES = []
def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    LOG_LINES.append(line)


# ============================================================================
# PART B - DISPLAY NAME ENRICHMENT LOOKUPS
# ============================================================================
LOINC_DISPLAY = {
    # Vital signs
    '8480-6':  'Systolic blood pressure',
    '8462-4':  'Diastolic blood pressure',
    '85354-9': 'Blood pressure panel',
    '8867-4':  'Heart rate',
    '9279-1':  'Respiratory rate',
    '8310-5':  'Body temperature',
    '8302-2':  'Body height',
    '29463-7': 'Body weight',
    '39156-5': 'Body mass index (BMI)',
    '2710-2':  'Oxygen saturation',
    '59408-5': 'Oxygen saturation (pulse oximetry)',
    # Basic + comprehensive metabolic
    '2345-7':  'Glucose, serum',
    '3094-0':  'Urea nitrogen, serum',
    '2160-0':  'Creatinine, serum',
    '2823-3':  'Potassium, serum',
    '2951-2':  'Sodium, serum',
    '2075-0':  'Chloride, serum',
    '2028-9':  'Carbon dioxide, total, serum',
    '17861-6': 'Calcium, serum',
    '1742-6':  'Alanine aminotransferase (ALT)',
    '1920-8':  'Aspartate aminotransferase (AST)',
    '6768-6':  'Alkaline phosphatase',
    '1975-2':  'Total bilirubin',
    '2885-2':  'Total protein',
    '1751-7':  'Albumin',
    # CBC
    '6690-2':  'White blood cell count',
    '789-8':   'Red blood cell count',
    '718-7':   'Hemoglobin',
    '4544-3':  'Hematocrit',
    '787-2':   'Mean corpuscular volume',
    '777-3':   'Platelet count',
    '770-8':   'Neutrophils %',
    '736-9':   'Lymphocytes %',
    # Lipid panel
    '2093-3':  'Total cholesterol',
    '2571-8':  'Triglycerides',
    '2085-9':  'HDL cholesterol',
    '13457-7': 'LDL cholesterol calculated',
    # Diabetes / glycemic
    '4548-4':  'Hemoglobin A1c',
    # Thyroid
    '3016-3':  'Thyrotropin (TSH)',
    '3051-0':  'Free thyroxine (T4 free)',
    '3053-6':  'Free triiodothyronine (T3 free)',
    # Coagulation
    '5902-2':  'Prothrombin time (PT)',
    '6301-6':  'INR',
    '14979-9': 'aPTT',
    # Cardiac
    '2154-3':  'Creatine kinase MB',
    '6598-7':  'Troponin T',
    '10839-9': 'Troponin I',
    '33762-6': 'NT-proBNP',
    # Inflammation
    '1988-5':  'C-reactive protein',
    '4537-7':  'Erythrocyte sedimentation rate',
    # Urinalysis
    '5803-2':  'pH of urine',
    '5811-5':  'Specific gravity of urine',
    '5804-0':  'Protein, urine',
    '5792-7':  'Glucose, urine',
    '5794-3':  'Ketones, urine',
    # Pulmonary (relevant for ALS)
    '19868-9': 'FVC - forced vital capacity',
    '19911-7': 'FEV1',
    '19926-5': 'FEV1 / FVC ratio',
    '20150-9': 'Forced expiratory flow 25-75%',
    # ALS-specific
    '67131-4': 'ALS Functional Rating Scale-Revised',
    # Other commonly seen
    '2532-0':  'Lactate dehydrogenase (LDH)',
    '2324-2':  'Gamma glutamyl transferase',
    '2502-3':  'Iron, serum',
    '2498-4':  'Iron binding capacity',
    '2276-4':  'Ferritin',
    '1798-8':  'Amylase',
    '2143-6':  'Cortisol',
    '14959-1': 'Microalbumin/creatinine ratio',
    '34714-6': 'INR (point of care)',
    '2069-3':  'Chloride, urine',
    '2955-3':  'Sodium, urine',
    '2161-8':  'Creatinine, urine',
    '6299-2':  'Urea nitrogen, urine',
}

SNOMED_DISPLAY = {
    '86044005':   'Amyotrophic lateral sclerosis',
    '38341003':   'Hypertensive disorder',
    '44054006':   'Diabetes mellitus type 2',
    '195967001':  'Asthma',
    '13645005':   'Chronic obstructive pulmonary disease',
    '414916001':  'Obesity',
    '53741008':   'Coronary arteriosclerosis',
    '414545008':  'Ischemic heart disease',
    '49436004':   'Atrial fibrillation',
    '195080001':  'Atrial fibrillation',
    '40930008':   'Hypothyroidism',
    '370388006':  'Anxiety',
    '35489007':   'Depressive disorder',
    '230690007':  'Cerebrovascular accident',
    '74732009':   'Mental disorder',
    '64859006':   'Osteoporosis',
    '64531003':   'Migraine',
    '195662009':  'Acute viral pharyngitis',
}


def enrich_one(code, display):
    """Return a backfilled display name when missing or generic."""
    if display and display.strip() and display.strip().lower() not in {'no display', 'unknown', ''}:
        return display
    if not code:
        return display
    if code in LOINC_DISPLAY:
        return LOINC_DISPLAY[code]
    if code in SNOMED_DISPLAY:
        return SNOMED_DISPLAY[code]
    return display


# ============================================================================
# PART C - NAME CLEANING
# ============================================================================
_STRIPPABLE = ['"', "'", '`', '\u201c', '\u201d', '\u2018', '\u2019',
               '\ufeff', '\u200b', '\u200c', '\u200d']


def clean_name(s):
    """Normalize a name string: strip quotes, BOMs, zero-width chars, list-repr leftovers."""
    if s is None:
        return ''
    if not isinstance(s, str):
        s = str(s)
    s = unicodedata.normalize('NFC', s)
    # Strip Python-bytes repr leftovers like b'Smith' or b"Smith"
    if s.startswith(("b'", 'b"')) and s.endswith(("'", '"')):
        s = s[2:-1]
    # Strip Python list-repr leftovers like ['Smith'] or ["Smith", "Jr"]
    if s.startswith('[') and s.endswith(']'):
        inner = s[1:-1].strip()
        if (inner.startswith("'") and inner.endswith("'")) or (inner.startswith('"') and inner.endswith('"')):
            s = inner[1:-1]
        elif ',' in inner:
            first = inner.split(',', 1)[0].strip()
            if (first.startswith("'") and first.endswith("'")) or (first.startswith('"') and first.endswith('"')):
                s = first[1:-1]
    for ch in _STRIPPABLE:
        s = s.strip(ch)
    s = s.strip()
    s = re.sub(r'\s+', ' ', s)
    if s and (s.isupper() or s.islower()) and re.match(r"^[A-Za-z][A-Za-z\s'\-\.]*$", s):
        s = s.title()
    return s


def normalize_mrn(m):
    """Normalize MRN for cross-format matching: strip non-alphanumeric, uppercase."""
    if not m:
        return ''
    return ''.join(c for c in str(m) if c.isalnum()).upper()


# ============================================================================
# PART D - CODE WALKING (the central reviewer-flagged fix)
# ============================================================================
def _system_token(s):
    if not s:
        return ''
    return str(s).lower()


def extract_all_codings(elem):
    """
    Walk a CCDA <code> element AND every nested <translation>.
    Returns list of dicts {code, system_oid, system_name, display}.

    Core fix: outer <code> attrs alone are not enough. When the EHR puts NDC
    outermost and RxNorm in <translation>, only the translation walk recovers
    the right code.
    """
    out = []
    if elem is None:
        return out

    def harvest(node):
        c = node.get('code')
        if c:
            out.append({
                'code': c,
                'system_oid': node.get('codeSystem'),
                'system_name': node.get('codeSystemName') or OID_TO_NAME.get(node.get('codeSystem'), ''),
                'display': node.get('displayName') or '',
            })

    harvest(elem)
    for t in elem.findall('hl7:translation', NS):
        harvest(t)
    return out


def prioritize_code(codings, domain):
    """Pick the preferred coding for a domain, preserving all codings.
    Returns (code, system_name, display, all_codings_list).

    For medication, when multiple RxNorm codings are present we apply a
    lightweight no-Athena heuristic that prefers codings whose display
    name shows the SCD/SBD fingerprint (a dose strength like '50 MG' or
    '500 MG/ML' AND a dose form word). This avoids picking the Ingredient
    (IN) or Brand Name (BN) when a Semantic Clinical Drug (SCD) is also
    present, which is the common failure mode of a naive first-match
    picker. The downstream OMOP and Phenopackets ETLs, when given an
    Athena vocabulary, do the principled CONCEPT.concept_class_id lookup
    on top of this and re-pick if the heuristic was wrong.
    """
    if not codings:
        return None, None, None, []
    prefs = CODE_SYSTEM_PREF.get(domain, [])
    # First narrow to the highest-priority matching system
    matching = []
    for pref in prefs:
        for c in codings:
            haystack = _system_token(c.get('system_name')) + ' ' + _system_token(c.get('system_oid'))
            if pref in haystack:
                matching.append(c)
        if matching:
            break

    if matching:
        if domain == 'medication' and len(matching) > 1:
            chosen = _prefer_scd_like(matching)
        else:
            chosen = matching[0]
        return chosen['code'], chosen['system_name'], chosen['display'], codings

    first = codings[0]
    return first['code'], first['system_name'], first['display'], codings


_SCD_STRENGTH_RE = re.compile(r'\b\d+(?:\.\d+)?\s*(?:MG|MCG|G|ML|MEQ|UNT|U|%|MG/ML|MG/HR|MG/G)\b', re.IGNORECASE)
_SCD_DOSE_FORM_WORDS = ('tablet','capsule','solution','suspension','injection','injectable',
                       'cream','ointment','patch','spray','syrup','elixir','suppository',
                       'lozenge','powder','inhaler','aerosol','drop','gel','foam','wafer',
                       'oral','topical','intravenous','subcutaneous','inhalation','transdermal')

def _prefer_scd_like(codings):
    """Heuristic: prefer a coding whose display string carries BOTH a
    dose-strength pattern and a dose-form word -- the fingerprint of an
    RxNorm Semantic Clinical Drug or Semantic Branded Drug. Falls back
    to the first coding if no candidate matches both criteria.

    Cheap and Athena-independent. The downstream OMOP/Phenopackets ETLs
    will refine this with the concept_class_id lookup when Athena is loaded.
    """
    def score(c):
        d = (c.get('display') or '').lower()
        has_strength = bool(_SCD_STRENGTH_RE.search(d))
        has_form = any(w in d for w in _SCD_DOSE_FORM_WORDS)
        return (2 if (has_strength and has_form) else
                1 if (has_strength or has_form) else
                0)
    best = max(codings, key=score)
    return best if score(best) > 0 else codings[0]


def fhir_prioritize_coding(codeable_concept, domain):
    """Same as prioritize_code but for a FHIR CodeableConcept dict."""
    if not isinstance(codeable_concept, dict):
        return None, None, None, []

    codings_raw = codeable_concept.get('coding', []) or []
    norm = []
    for c in codings_raw:
        if not isinstance(c, dict):
            continue  # defensive: occasionally a string slips in
        sys_url = c.get('system', '') or ''
        sys_name = sys_url
        sl = sys_url.lower()
        if   'rxnorm' in sl: sys_name = 'RxNorm'
        elif 'snomed' in sl: sys_name = 'SNOMED-CT'
        elif 'loinc'  in sl: sys_name = 'LOINC'
        elif 'icd-10' in sl or 'icd10' in sl: sys_name = 'ICD-10-CM'
        elif 'icd-9'  in sl: sys_name = 'ICD-9-CM'
        elif 'cpt'    in sl: sys_name = 'CPT-4'
        elif 'cvx'    in sl: sys_name = 'CVX'
        elif 'ndc'    in sl: sys_name = 'NDC'
        norm.append({
            'code': c.get('code'),
            'system_oid': sys_url,
            'system_name': sys_name,
            'display': c.get('display', '') or '',
        })

    code, system, display, _ = prioritize_code(norm, domain)
    if not display:
        display = codeable_concept.get('text', '') or ''
    return code, system, display, norm


# ============================================================================
# PART E - FORMAT DETECTION + DECODING + STRIPPERS
# ============================================================================
def detect_format(data):
    """Return one of: 'pdf', 'rtf', 'ccda_xml', 'html_fragment', 'unknown'."""
    if isinstance(data, bytes):
        head_bytes = data[:512]
        try:
            head = head_bytes.decode('utf-8', errors='ignore')
        except Exception:
            head = ''
    else:
        head = data[:512] if data else ''
        head_bytes = head.encode('utf-8', errors='ignore')

    if head_bytes.startswith(b'%PDF'):
        return 'pdf'
    if head.lstrip().startswith('{\\rtf'):
        return 'rtf'
    head_stripped = head.lstrip()
    if head_stripped.startswith('<?xml') or '<ClinicalDocument' in head:
        return 'ccda_xml'
    if re.search(r'<(html|body|div|p|span|br|table)\b', head, re.I):
        return 'html_fragment'
    return 'unknown'


def looks_decoded(data_bytes):
    """Return True if data already looks like decoded content (not base64)."""
    if not data_bytes:
        return False
    head = data_bytes[:64]
    if head.startswith(b'%PDF') or head.startswith(b'{\\rtf') or head.lstrip().startswith(b'<'):
        return True
    # Plain text with high non-base64 punctuation count
    sample = data_bytes[:512]
    try:
        s = sample.decode('utf-8', errors='ignore')
    except Exception:
        return False
    non_b64 = sum(1 for c in s if not (c.isalnum() or c in '+/= \t\r\n'))
    return non_b64 > 20  # heuristic: any meaningful non-b64 punctuation


def robust_decode(text):
    """Decode possibly double-base64 chunked content. Returns (raw_bytes, was_double).

    CRITICAL: if input is bytes that already look decoded (PDF magic, RTF, XML/HTML start),
    return immediately. The previous code decoded bytes as UTF-8 with errors='ignore' as a
    defensive re-pass, which silently dropped non-UTF-8 bytes from PDF streams and corrupted
    them beyond pypdf's ability to extract text. Skip the round-trip for binary content."""
    if isinstance(text, (bytes, bytearray)) and looks_decoded(text):
        return bytes(text), False
    s = text.strip() if isinstance(text, str) else text.decode('utf-8', errors='ignore').strip()

    # Strip Python repr wrapping like b'...' or b"..."
    if s.startswith(("b'", 'b"')) and s.endswith(("'", '"')):
        s = s[2:-1]

    # If input already looks decoded, return as bytes
    if looks_decoded(s.encode('utf-8', errors='ignore')):
        return s.encode('utf-8', errors='ignore'), False

    cleaned = re.sub(r'[^A-Za-z0-9+/=]', '', s)
    if len(cleaned) % 4:
        cleaned += '=' * (4 - len(cleaned) % 4)

    try:
        first = base64.b64decode(cleaned, validate=False)
    except Exception:
        return s.encode('utf-8', errors='ignore'), False

    if looks_decoded(first):
        return first, False

    # Try a second pass
    try:
        as_text = first.decode('utf-8', errors='ignore').strip()
        cleaned2 = re.sub(r'[^A-Za-z0-9+/=]', '', as_text)
        if len(cleaned2) >= 32 and len(cleaned2) % 4 == 0:
            second = base64.b64decode(cleaned2, validate=False)
            if looks_decoded(second):
                return second, True
    except Exception:
        pass

    return first, False


# ----- RTF stripper (Epic epicV10801 notes) -----
_RTF_CONTROL = re.compile(r"\\[a-zA-Z]+(-?\d+)?[ ]?")
_RTF_HEX     = re.compile(r"\\'([0-9a-fA-F]{2})")
_RTF_UNICODE = re.compile(r"\\u(-?\d+)\??")
_RTF_SKIP_GROUPS = {
    'fonttbl', 'colortbl', 'stylesheet', 'listtable', 'listoverridetable',
    'rsidtbl', 'info', 'xmlnstbl', 'pict', 'themedata', 'colorschememapping', 'datastore',
}


def strip_rtf(rtf_text):
    if not rtf_text:
        return ''
    text = rtf_text
    # Preserve escaped braces/backslashes through processing
    text = text.replace('\\{', '\x00').replace('\\}', '\x01').replace('\\\\', '\x02')

    # Remove non-content destination groups (font tables, etc.)
    def remove_groups(s, group_names):
        for g in group_names:
            pattern = '{\\' + g
            while True:
                idx = s.find(pattern)
                if idx < 0:
                    break
                depth = 0
                end = idx
                for i in range(idx, len(s)):
                    if s[i] == '{':
                        depth += 1
                    elif s[i] == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                else:
                    end = len(s)
                s = s[:idx] + s[end:]
        return s

    text = remove_groups(text, _RTF_SKIP_GROUPS)

    # Decode hex escapes \'XX
    def hex_repl(m):
        try:
            return bytes.fromhex(m.group(1)).decode('cp1252', errors='ignore')
        except Exception:
            return ''
    text = _RTF_HEX.sub(hex_repl, text)

    # Decode unicode escapes \uNNNN?
    def unicode_repl(m):
        try:
            cp = int(m.group(1))
            if cp < 0:
                cp += 65536
            return chr(cp)
        except Exception:
            return ''
    text = _RTF_UNICODE.sub(unicode_repl, text)

    # Strip remaining control words, drop braces, restore escaped specials
    text = _RTF_CONTROL.sub(' ', text)
    text = text.replace('{', '').replace('}', '')
    text = text.replace('\x00', '{').replace('\x01', '}').replace('\x02', '\\')

    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n[ \t]*\n+', '\n\n', text)
    return text.strip()


# ----- HTML fragment stripper -----
import html as html_module
_HTML_SCRIPT = re.compile(r'<(script|style)\b[^>]*>.*?</\1>', re.I | re.S)
_HTML_TAG    = re.compile(r'<[^>]+>')


def strip_html(html_text):
    if not html_text:
        return ''
    s = _HTML_SCRIPT.sub(' ', html_text)
    s = _HTML_TAG.sub(' ', s)
    s = html_module.unescape(s)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\n[ \t]*\n+', '\n\n', s)
    return s.strip()


# ----- PDF text extraction -----
def extract_pdf_text(pdf_bytes):
    if not HAVE_PYPDF:
        return '[PDF - pypdf not installed]'
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or '')
            except Exception:
                pages.append('')
        return ('\n\n'.join(p for p in pages if p)).strip() or '[PDF - no extractable text]'
    except Exception as e:
        return f'[PDF - extraction failed: {type(e).__name__}]'


# ----- Excel-safe sanitizer -----
_ILLEGAL_XLSX = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
def excel_safe(s):
    if s is None:
        return ''
    return _ILLEGAL_XLSX.sub('', str(s))


# ============================================================================
# PART F - STAGE 1: REASSEMBLY
# ============================================================================
def reassemble_chunks(csv_path, out_dir, is_base64):
    """Reassemble Databricks chunked CSV exports into individual files."""
    if not os.path.exists(csv_path):
        log(f"  SKIP reassembly: {csv_path} not found")
        return 0
    # Skip if output dir is already populated
    if os.path.exists(out_dir) and len(os.listdir(out_dir)) > 100:
        log(f"  Reassembly SKIPPED for {os.path.basename(csv_path)} (output dir already populated)")
        return len(os.listdir(out_dir))

    log(f"  Reassembling chunks from {os.path.basename(csv_path)}")
    os.makedirs(out_dir, exist_ok=True)

    df = pd.read_csv(csv_path).sort_values(['id', 'chunk_index'])
    written = 0

    for fid, chunks in df.groupby('id'):
        data = ''.join(chunks['chunk_data'].dropna().astype(str))
        ext = '.xml' if is_base64 else '.json'
        out_path = os.path.join(out_dir, f"{fid}{ext}")

        if is_base64:
            raw, _ = robust_decode(data)
            mode = 'wb'
            payload = raw
        else:
            mode = 'w'
            payload = data

        try:
            with open(out_path, mode, encoding=None if mode == 'wb' else 'utf-8') as f:
                f.write(payload)
            written += 1
        except Exception as e:
            log(f"  reassembly error for {fid}: {e}")

    log(f"  Wrote {written} files to {out_dir}")
    return written


# ============================================================================
# PART G - STAGE 2: CCDA PARSING
# ============================================================================
def _ccda_section(root, section_oid):
    for sec in root.findall('.//hl7:structuredBody/hl7:component/hl7:section', NS):
        for tid in sec.findall('hl7:templateId', NS):
            if tid.get('root') == section_oid:
                return sec
    return None


def _ccda_section_narrative(root):
    """Return (concatenated_text, [{section_title, narrative_text, character_count}, ...])."""
    out = []
    sections = []
    for sec in root.findall('.//hl7:structuredBody/hl7:component/hl7:section', NS):
        title_el = sec.find('hl7:title', NS)
        text_el  = sec.find('hl7:text', NS)
        title = (title_el.text or '').strip() if title_el is not None and title_el.text else 'Section'
        if text_el is not None:
            body = ' '.join((text_el.itertext())).strip()
            body = re.sub(r'\s+', ' ', body)
            if body:
                out.append(f"--- {title} ---\n{body}")
                sections.append({
                    'section_title': title,
                    'narrative_text': body,
                    'character_count': len(body),
                })
    return '\n\n'.join(out), sections


def _ccda_record_target_name(root):
    """Pull patient name STRICTLY from <recordTarget>/<patientRole>/<patient>/<name>."""
    rt = root.find('.//hl7:recordTarget/hl7:patientRole/hl7:patient/hl7:name', NS)
    if rt is None:
        return '', ''
    given = rt.find('hl7:given', NS)
    family = rt.find('hl7:family', NS)
    given_text = clean_name(given.text) if (given is not None and given.text) else ''
    family_text = clean_name(family.text) if (family is not None and family.text) else ''
    return given_text, family_text


def _ccda_record_target_demographics(root):
    """Pull MRN, DOB, gender, race, ethnicity from <recordTarget>.
    Race comes from <raceCode> (and optionally sdtc:raceCode for additional
    races). Ethnicity from <ethnicGroupCode>. Both use CDC R&E codes from
    OID 2.16.840.1.113883.6.238."""
    pr = root.find('.//hl7:recordTarget/hl7:patientRole', NS)
    mrn = ''
    dob = ''
    gender = ''
    race = ''
    ethnicity = ''
    if pr is not None:
        for id_el in pr.findall('hl7:id', NS):
            ext = id_el.get('extension')
            if ext:
                mrn = clean_name(ext)
                break
        pat = pr.find('hl7:patient', NS)
        if pat is not None:
            bd = pat.find('hl7:birthTime', NS)
            if bd is not None and bd.get('value'):
                dob = bd.get('value')[:8]
                if len(dob) == 8:
                    dob = f"{dob[:4]}-{dob[4:6]}-{dob[6:]}"
            g = pat.find('hl7:administrativeGenderCode', NS)
            if g is not None:
                gender = g.get('displayName') or g.get('code') or ''
            # Race: <raceCode> + any <sdtc:raceCode> (multi-race patients)
            race_labels = []
            for r in pat.findall('hl7:raceCode', NS):
                lbl = (r.get('displayName') or
                       CDC_RACE_DISPLAY.get(r.get('code', ''), '') or
                       r.get('code', ''))
                if lbl and lbl not in race_labels:
                    race_labels.append(lbl)
            # sdtc namespace for additional race codes
            for r in pat.findall('{urn:hl7-org:sdtc}raceCode'):
                lbl = (r.get('displayName') or
                       CDC_RACE_DISPLAY.get(r.get('code', ''), '') or
                       r.get('code', ''))
                if lbl and lbl not in race_labels:
                    race_labels.append(lbl)
            race = ' / '.join(race_labels) if race_labels else ''
            # Ethnicity
            eg = pat.find('hl7:ethnicGroupCode', NS)
            if eg is not None:
                ethnicity = (eg.get('displayName') or
                             CDC_ETHNICITY_DISPLAY.get(eg.get('code', ''), '') or
                             eg.get('code', '') or '')
    return mrn, dob, gender, race, ethnicity


def _ccda_effective_time(entry):
    et = entry.find('hl7:effectiveTime', NS)
    if et is None:
        return ''
    if et.get('value'):
        return et.get('value')[:8]
    low = et.find('hl7:low', NS)
    if low is not None and low.get('value'):
        return low.get('value')[:8]
    return ''


def _ccda_value_for_obs(obs):
    val = obs.find('hl7:value', NS)
    if val is None:
        return None, None
    xtype = val.get('{http://www.w3.org/2001/XMLSchema-instance}type', '')
    if xtype == 'PQ':
        return val.get('value'), val.get('unit')
    if xtype == 'CD':
        return val.get('displayName') or val.get('code'), None
    if xtype == 'ST':
        return (val.text or '').strip(), None
    return val.get('value') or (val.text or '').strip(), val.get('unit')


def parse_ccda_document(decoded_bytes, doc_uuid, fname, log_errors=False):
    """Parse a CCDA XML document. Returns (header, structured_records, narrative_text).
    structured_records includes 'notes' (per-section narratives) ready for the dashboard."""
    try:
        text = decoded_bytes.decode('utf-8', errors='ignore') if isinstance(decoded_bytes, bytes) else decoded_bytes
        root = ET.fromstring(text)
    except Exception as e:
        if log_errors:
            log(f"  CCDA parse error in {fname}: {e}")
        return None, {}, ''

    header = {}
    given, family = _ccda_record_target_name(root)
    mrn, dob, gender, race, ethnicity = _ccda_record_target_demographics(root)
    header['first_name'] = given
    header['last_name']  = family
    header['mrn'] = mrn
    header['dob'] = dob
    header['gender'] = gender
    header['race'] = race
    header['ethnicity'] = ethnicity

    structured = defaultdict(list)

    # ----- Medications -----
    sec = _ccda_section(root, TPL['medications_section'])
    if sec is not None:
        for sa in sec.findall('.//hl7:substanceAdministration', NS):
            mat = sa.find('.//hl7:consumable/hl7:manufacturedProduct/hl7:manufacturedMaterial', NS)
            if mat is None:
                continue
            code_el = mat.find('hl7:code', NS)
            codings = extract_all_codings(code_el)
            code, system, display, all_codings = prioritize_code(codings, 'medication')
            fallback = ''
            if code_el is not None:
                ot = code_el.find('hl7:originalText', NS)
                if ot is not None:
                    fallback = ' '.join(ot.itertext()).strip()
            if not fallback:
                nm = mat.find('hl7:name', NS)
                if nm is not None:
                    fallback = ' '.join(nm.itertext()).strip()
            display = display or fallback or 'Unknown medication'
            structured['medications'].append({
                'code': code, 'code_system': system, 'display_name': display,
                'all_codings': all_codings,
                'effective_date': _ccda_effective_time(sa),
                'source': 'ccda', 'source_file': fname,
            })

    # ----- Problems -----
    sec = _ccda_section(root, TPL['problems_section'])
    if sec is not None:
        for obs in sec.findall('.//hl7:observation', NS):
            val = obs.find('hl7:value', NS)
            if val is None:
                continue
            codings = extract_all_codings(val)
            code, system, display, all_codings = prioritize_code(codings, 'problem')
            if not code:
                continue
            structured['problems'].append({
                'code': code, 'code_system': system,
                'display_name': display or 'Unknown problem',
                'all_codings': all_codings,
                'effective_date': _ccda_effective_time(obs),
                'source': 'ccda', 'source_file': fname,
            })

    # ----- Labs -----
    sec = _ccda_section(root, TPL['results_section'])
    if sec is not None:
        for obs in sec.findall('.//hl7:observation', NS):
            code_el = obs.find('hl7:code', NS)
            codings = extract_all_codings(code_el)
            code, system, display, all_codings = prioritize_code(codings, 'lab')
            if not code:
                continue
            value, unit = _ccda_value_for_obs(obs)
            structured['labs'].append({
                'code': code, 'code_system': system, 'display_name': display or '',
                'all_codings': all_codings, 'value': value, 'unit': unit,
                'effective_date': _ccda_effective_time(obs),
                'source': 'ccda', 'source_file': fname, 'category': 'laboratory',
            })

    # ----- Vitals -----
    sec = _ccda_section(root, TPL['vitals_section'])
    if sec is not None:
        for obs in sec.findall('.//hl7:observation', NS):
            code_el = obs.find('hl7:code', NS)
            codings = extract_all_codings(code_el)
            code, system, display, all_codings = prioritize_code(codings, 'vital')
            if not code:
                continue
            value, unit = _ccda_value_for_obs(obs)
            structured['vitals'].append({
                'code': code, 'code_system': system, 'display_name': display or '',
                'all_codings': all_codings, 'value': value, 'unit': unit,
                'effective_date': _ccda_effective_time(obs),
                'source': 'ccda', 'source_file': fname, 'category': 'vital-signs',
            })

    # ----- Encounters -----
    sec = _ccda_section(root, TPL['encounters_section'])
    if sec is not None:
        for enc in sec.findall('.//hl7:encounter', NS):
            code_el = enc.find('hl7:code', NS)
            codings = extract_all_codings(code_el)
            code, system, display, all_codings = prioritize_code(codings, 'encounter')
            structured['encounters'].append({
                'code': code, 'code_system': system, 'display_name': display or '',
                'all_codings': all_codings,
                'effective_date': _ccda_effective_time(enc),
                'source': 'ccda', 'source_file': fname,
            })

    # ----- Allergies -----
    sec = _ccda_section(root, TPL['allergies_section'])
    if sec is not None:
        for obs in sec.findall('.//hl7:observation', NS):
            ag = obs.find('.//hl7:participant/hl7:participantRole/hl7:playingEntity/hl7:code', NS)
            codings = extract_all_codings(ag) if ag is not None else []
            code, system, display, all_codings = prioritize_code(codings, 'allergy')
            if not display:
                vc = obs.find('hl7:value', NS)
                if vc is not None:
                    display = vc.get('displayName') or display
            if not code and not display:
                continue
            structured['allergies'].append({
                'code': code, 'code_system': system,
                'display_name': display or 'Unknown allergen',
                'all_codings': all_codings,
                'effective_date': _ccda_effective_time(obs),
                'source': 'ccda', 'source_file': fname,
            })

    # ----- Immunizations -----
    sec = _ccda_section(root, TPL['immunizations_sec'])
    if sec is not None:
        for sa in sec.findall('.//hl7:substanceAdministration', NS):
            mat = sa.find('.//hl7:consumable/hl7:manufacturedProduct/hl7:manufacturedMaterial', NS)
            if mat is None:
                continue
            code_el = mat.find('hl7:code', NS)
            codings = extract_all_codings(code_el)
            code, system, display, all_codings = prioritize_code(codings, 'immunization')
            structured['immunizations'].append({
                'code': code, 'code_system': system, 'display_name': display or '',
                'all_codings': all_codings,
                'effective_date': _ccda_effective_time(sa),
                'source': 'ccda', 'source_file': fname,
            })

    # ----- Procedures -----
    sec = _ccda_section(root, TPL['procedures_section'])
    if sec is not None:
        for proc in sec.findall('.//hl7:procedure', NS):
            code_el = proc.find('hl7:code', NS)
            codings = extract_all_codings(code_el)
            code, system, display, all_codings = prioritize_code(codings, 'procedure')
            if not code:
                continue
            structured['procedures'].append({
                'code': code, 'code_system': system, 'display_name': display or '',
                'all_codings': all_codings,
                'effective_date': _ccda_effective_time(proc),
                'source': 'ccda', 'source_file': fname,
            })

    narrative, sections = _ccda_section_narrative(root)
    if sections:
        # Add source_file so the user can trace narrative back to CCDA
        for s in sections:
            s['source_file'] = fname
            s['source'] = 'ccda'
        structured['notes'] = sections

    return header, structured, narrative


# ============================================================================
# PART H - STAGE 3: FHIR PARSING (13 resource types)
# ============================================================================
def _fhir_patient_id(resource, doc_id, doc_map):
    for key in ('subject', 'patient', 'beneficiary'):
        ref_obj = resource.get(key)
        if isinstance(ref_obj, dict):
            ref = ref_obj.get('reference', '') or ''
            if '/' in ref:
                return ref.split('/')[-1]
    return doc_map.get(doc_id)


def _fhir_best_name(name_array):
    if not name_array:
        return '', ''
    use_priority = {'official': 0, 'usual': 1, None: 2, '': 2}
    sorted_names = sorted(name_array, key=lambda n: use_priority.get(n.get('use') if isinstance(n, dict) else None, 3))
    for n in sorted_names:
        if not isinstance(n, dict):
            continue
        given_list = n.get('given') or []
        family = n.get('family') or ''
        given = given_list[0] if given_list else ''
        if given or family:
            return clean_name(given), clean_name(family)
    return '', ''


# CDC R&E race code -> display name (OID 2.16.840.1.113883.6.238)
CDC_RACE_DISPLAY = {
    '2106-3': 'White',
    '2054-5': 'Black or African American',
    '1002-5': 'American Indian or Alaska Native',
    '2028-9': 'Asian',
    '2076-8': 'Native Hawaiian or Other Pacific Islander',
    '2131-1': 'Other Race',
}
CDC_ETHNICITY_DISPLAY = {
    '2135-2': 'Hispanic or Latino',
    '2186-5': 'Not Hispanic or Latino',
}

US_CORE_RACE_URL      = 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-race'
US_CORE_ETHNICITY_URL = 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity'


def _fhir_us_core_race_ethnicity(patient_res):
    """Walk Patient.extension[] looking for US Core race and ethnicity.
    US Core nests an ombCategory valueCoding plus an optional text field.
    Returns (race_label, ethnicity_label) — each is the text field if
    present, otherwise the ombCategory display, otherwise CDC code lookup."""
    race_label = ''
    ethnicity_label = ''
    for ext in patient_res.get('extension', []) or []:
        if not isinstance(ext, dict): continue
        url = ext.get('url', '')
        if url not in (US_CORE_RACE_URL, US_CORE_ETHNICITY_URL):
            continue
        # Walk nested extensions: ombCategory (Coding) + text (string)
        nested = ext.get('extension', []) or []
        label_from_text = ''
        label_from_omb = ''
        for n in nested:
            if not isinstance(n, dict): continue
            nurl = n.get('url', '')
            if nurl == 'text' and n.get('valueString'):
                label_from_text = n.get('valueString', '').strip()
            elif nurl == 'ombCategory':
                vc = n.get('valueCoding') or {}
                disp = vc.get('display', '').strip()
                code = vc.get('code', '').strip()
                if not disp and code:
                    disp = (CDC_RACE_DISPLAY.get(code) or
                            CDC_ETHNICITY_DISPLAY.get(code) or '')
                if disp and not label_from_omb:
                    label_from_omb = disp
        label = label_from_text or label_from_omb
        if url == US_CORE_RACE_URL and label:
            race_label = label
        elif url == US_CORE_ETHNICITY_URL and label:
            ethnicity_label = label
    return race_label, ethnicity_label


def parse_fhir_bundle(bundle, doc_id, doc_map, global_med_index=None):
    """Walk a FHIR Bundle. Returns (patient_dict_or_None, structured_records).
    global_med_index: cross-bundle Medication index (id/url -> code dict)."""
    structured = defaultdict(list)
    patient_record = None

    medication_index = dict(global_med_index) if global_med_index else {}
    # Add per-bundle Medications too (in case a bundle does have them)
    for entry in bundle.get('entry', []) or []:
        res = entry.get('resource') or {}
        if res.get('resourceType') == 'Medication':
            mc = res.get('code') or {}
            if not mc:
                continue
            mid = res.get('id', '')
            if mid:
                medication_index[mid] = mc
                medication_index['#' + mid] = mc
                medication_index['Medication/' + mid] = mc
            full = entry.get('fullUrl', '') or ''
            if full:
                medication_index[full] = mc

    for entry in bundle.get('entry', []) or []:
        res = entry.get('resource') or {}
        rtype = res.get('resourceType')
        if not rtype:
            continue

        # ----- Patient (the only place we accept patient demographics from FHIR) -----
        if rtype == 'Patient':
            pid = res.get('id')
            given, family = _fhir_best_name(res.get('name'))
            mrn = ''
            for ident in res.get('identifier', []) or []:
                if not isinstance(ident, dict):
                    continue
                ty = ident.get('type')
                tt = (ty.get('text', '') if isinstance(ty, dict) else '') or ''
                tc = ''
                if isinstance(ty, dict):
                    coding = ty.get('coding') or []
                    if coding and isinstance(coding[0], dict):
                        tc = coding[0].get('code', '') or ''
                if 'MR' in (tt + tc).upper() or 'MRN' in (tt + tc).upper():
                    mrn = clean_name(ident.get('value', ''))
                    break
            if not mrn and res.get('identifier'):
                first_id = res['identifier'][0] if res.get('identifier') else None
                if isinstance(first_id, dict):
                    mrn = clean_name(first_id.get('value', ''))
            # US Core race / ethnicity extensions
            race_label, ethnicity_label = _fhir_us_core_race_ethnicity(res)
            patient_record = {
                'patient_id': pid, 'first_name': given, 'last_name': family,
                'mrn': mrn,
                'dob': res.get('birthDate', '') or '',
                'gender': res.get('gender', '') or '',
                'race': race_label,
                'ethnicity': ethnicity_label,
            }
            continue

        # Practitioner / Organization / etc - explicitly NOT pulled into patient set
        if rtype in ('Practitioner', 'PractitionerRole', 'Organization',
                     'Location', 'RelatedPerson', 'Device', 'Medication'):
            continue

        pid = _fhir_patient_id(res, doc_id, doc_map)
        if not pid:
            continue

        if rtype == 'Condition':
            code, sys_, disp, all_c = fhir_prioritize_coding(res.get('code', {}), 'problem')
            if code:
                cs_obj = res.get('clinicalStatus') or {}
                cs_coding = cs_obj.get('coding') or [] if isinstance(cs_obj, dict) else []
                cs_code = cs_coding[0].get('code', '') if cs_coding and isinstance(cs_coding[0], dict) else ''
                structured['problems'].append({
                    'patient_id': pid, 'code': code, 'code_system': sys_,
                    'display_name': disp, 'all_codings': all_c,
                    'effective_date': res.get('onsetDateTime') or res.get('recordedDate') or '',
                    'clinical_status': cs_code, 'source': 'fhir',
                })

        elif rtype == 'Observation':
            code, sys_, disp, all_c = fhir_prioritize_coding(res.get('code', {}), 'lab')
            if not code:
                continue
            cats = []
            for cat in res.get('category', []) or []:
                if not isinstance(cat, dict):
                    continue
                for c in (cat.get('coding') or []):
                    if isinstance(c, dict):
                        cats.append(c.get('code', ''))
            cat_str = ','.join(cats).lower()
            bucket = 'vitals' if 'vital' in cat_str else 'labs'
            v = res.get('valueQuantity') or {}
            structured[bucket].append({
                'patient_id': pid,
                'code': code, 'code_system': sys_, 'display_name': disp, 'all_codings': all_c,
                'value': v.get('value') if isinstance(v, dict) else None,
                'unit': v.get('unit') if isinstance(v, dict) else None,
                'effective_date': res.get('effectiveDateTime') or '',
                'category': cat_str or bucket, 'source': 'fhir',
            })

        elif rtype in ('MedicationRequest', 'MedicationStatement', 'MedicationAdministration'):
            mc = res.get('medicationCodeableConcept', {}) or {}
            med_ref_obj = res.get('medicationReference') or {}
            ref = med_ref_obj.get('reference', '') if isinstance(med_ref_obj, dict) else ''
            ref_display = med_ref_obj.get('display', '') if isinstance(med_ref_obj, dict) else ''
            if not mc and ref:
                contained = {c.get('id'): c for c in (res.get('contained') or []) if isinstance(c, dict) and c.get('id')}
                mid = ref.split('#')[-1] if '#' in ref else ref.split('/')[-1]
                mref = contained.get(mid, {})
                mc = mref.get('code', {}) if isinstance(mref, dict) else {}
                if not mc:
                    mc = (medication_index.get(ref) or
                          medication_index.get(mid) or
                          medication_index.get(ref.lstrip('#')) or {})
            code, sys_, disp, all_c = fhir_prioritize_coding(mc, 'medication')
            if not disp:
                disp = ref_display or ref
            structured['medications'].append({
                'patient_id': pid,
                'code': code, 'code_system': sys_, 'display_name': disp or '', 'all_codings': all_c,
                'effective_date': res.get('authoredOn') or res.get('effectiveDateTime') or '',
                'status': res.get('status', ''), 'source': 'fhir',
                'fhir_resource_type': rtype,
            })

        elif rtype == 'Procedure':
            code, sys_, disp, all_c = fhir_prioritize_coding(res.get('code', {}), 'procedure')
            perf_period = res.get('performedPeriod') or {}
            perf_start = perf_period.get('start') if isinstance(perf_period, dict) else ''
            structured['procedures'].append({
                'patient_id': pid,
                'code': code, 'code_system': sys_, 'display_name': disp, 'all_codings': all_c,
                'effective_date': res.get('performedDateTime') or perf_start or '',
                'status': res.get('status', ''), 'source': 'fhir',
            })

        elif rtype == 'Encounter':
            cls = res.get('class', {}) or {}
            class_code = cls.get('code') if isinstance(cls, dict) else ''
            class_display = (cls.get('display') if isinstance(cls, dict) else '') or class_code
            type_disp = ''
            for t in res.get('type', []) or []:
                if isinstance(t, dict):
                    _, _, td, _ = fhir_prioritize_coding(t, 'encounter')
                    if td:
                        type_disp = td
                        break
            period = res.get('period') or {}
            period_start = period.get('start') if isinstance(period, dict) else ''
            period_end   = period.get('end') if isinstance(period, dict) else ''
            structured['encounters'].append({
                'patient_id': pid,
                'code': class_code, 'code_system': 'FHIR encounter class',
                'display_name': class_display, 'type': type_disp,
                'effective_date': period_start or '',
                'end_date': period_end or '',
                'status': res.get('status', ''), 'source': 'fhir',
            })

        elif rtype == 'AllergyIntolerance':
            code, sys_, disp, all_c = fhir_prioritize_coding(res.get('code', {}), 'allergy')
            reactions = res.get('reaction') or []
            severity = ''
            if reactions and isinstance(reactions[0], dict):
                severity = reactions[0].get('severity', '') or ''
            cs = res.get('clinicalStatus')
            cs_code = ''
            if isinstance(cs, dict):
                cs_coding = cs.get('coding') or []
                if cs_coding and isinstance(cs_coding[0], dict):
                    cs_code = cs_coding[0].get('code', '') or ''
            structured['allergies'].append({
                'patient_id': pid,
                'code': code, 'code_system': sys_, 'display_name': disp, 'all_codings': all_c,
                'effective_date': res.get('recordedDate', ''),
                'severity': severity, 'clinical_status': cs_code,
                'source': 'fhir',
            })

        elif rtype == 'Immunization':
            code, sys_, disp, all_c = fhir_prioritize_coding(res.get('vaccineCode', {}), 'immunization')
            structured['immunizations'].append({
                'patient_id': pid,
                'code': code, 'code_system': sys_, 'display_name': disp, 'all_codings': all_c,
                'effective_date': res.get('occurrenceDateTime', ''),
                'status': res.get('status', ''), 'source': 'fhir',
            })

        elif rtype == 'CarePlan':
            period = res.get('period') or {}
            structured['careplans'].append({
                'patient_id': pid,
                'title': res.get('title', '') or '',
                'description': res.get('description', '') or '',
                'status': res.get('status', ''),
                'effective_date': period.get('start') if isinstance(period, dict) else '',
                'source': 'fhir',
            })

        elif rtype == 'DiagnosticReport':
            code, sys_, disp, all_c = fhir_prioritize_coding(res.get('code', {}), 'lab')
            structured['diagnostic_reports'].append({
                'patient_id': pid,
                'code': code, 'code_system': sys_, 'display_name': disp, 'all_codings': all_c,
                'status': res.get('status', ''),
                'effective_date': res.get('effectiveDateTime', ''),
                'conclusion': res.get('conclusion', '') or '',
                'source': 'fhir',
            })

        elif rtype == 'Goal':
            descr_obj = res.get('description') or {}
            descr = descr_obj.get('text', '') if isinstance(descr_obj, dict) else ''
            structured['goals'].append({
                'patient_id': pid,
                'description': descr,
                'status': res.get('lifecycleStatus', ''),
                'effective_date': res.get('startDate', '') or '',
                'source': 'fhir',
            })

        elif rtype == 'DocumentReference':
            type_obj = res.get('type') or {}
            type_text = type_obj.get('text', '') if isinstance(type_obj, dict) else ''
            structured['document_references'].append({
                'patient_id': pid,
                'type_display': type_text,
                'status': res.get('status', ''),
                'effective_date': res.get('date', ''),
                'source': 'fhir',
            })

    return patient_record, structured


# ============================================================================
# PART I - STAGE 4: PATIENT DEDUPLICATION + FIELD ALIASING
# ============================================================================
def _dedupe_patients_by_name_dob(patients, records, documents):
    """Merge patients with identical (lower(first_name), lower(last_name), dob).
    Reassigns records and documents to the surviving canonical patient_id.
    All three fields must be non-empty for a merge."""
    by_key = defaultdict(list)
    for pid, p in patients.items():
        fn = (p.get('first_name') or '').strip().lower()
        ln = (p.get('last_name') or '').strip().lower()
        dob = (p.get('dob') or '').strip()
        if fn and ln and dob:
            by_key[(fn, ln, dob)].append(pid)

    # patient_id -> canonical_patient_id (only for merged patients)
    remap = {}
    merged_count = 0
    for key, pids in by_key.items():
        if len(pids) <= 1:
            continue
        # Pick the canonical: longest one (most data filled in usually)
        canonical = max(pids, key=lambda pid: sum(1 for v in patients[pid].values() if v))
        for pid in pids:
            if pid != canonical:
                remap[pid] = canonical
                merged_count += 1
                # Merge any non-empty fields the canonical doesn't have
                for k, v in patients[pid].items():
                    if v and not patients[canonical].get(k):
                        patients[canonical][k] = v

    if not remap:
        return 0, 0, 0

    # Drop merged patients
    for pid in remap:
        patients.pop(pid, None)

    # Reassign records
    rec_count = 0
    for cat, rows in records.items():
        for r in rows:
            if r.get('patient_id') in remap:
                r['patient_id'] = remap[r['patient_id']]
                rec_count += 1

    # Reassign documents
    doc_count = 0
    for d in documents:
        if d.get('patient_id') in remap:
            d['patient_id'] = remap[d['patient_id']]
            doc_count += 1

    return merged_count, rec_count, doc_count


def _alias_record_fields(records):
    """Add original-Box-dashboard schema field aliases to records in-place.
    Original dashboard expects 'start_date', 'visit_type', 'allergen', 'authored_on',
    'onset_datetime', etc. We add these as aliases of our canonical names."""
    for tab, rows in records.items():
        for r in rows:
            if not isinstance(r, dict):
                continue
            d = r.get('effective_date') or ''
            disp = r.get('display_name') or ''
            code = r.get('code') or ''
            sysn = r.get('code_system') or ''
            if d:
                r.setdefault('effective_datetime', d)
                if tab in ('allergies', 'immunizations'):
                    r.setdefault('recorded_date', d)
                if tab == 'encounters':
                    r.setdefault('start_date', d)
                if tab == 'medications':
                    r.setdefault('authored_on', d)
                    r.setdefault('start_date', d)
                if tab == 'problems':
                    r.setdefault('onset_datetime', d)
                if tab == 'procedures':
                    r.setdefault('performed_datetime', d)
                if tab == 'immunizations':
                    r.setdefault('occurrence_datetime', d)
            if tab == 'allergies':
                if disp: r.setdefault('allergen', disp)
                if code: r.setdefault('allergen_code', code)
                if sysn: r.setdefault('allergen_system', sysn)
            if tab == 'immunizations':
                if disp: r.setdefault('vaccine', disp)
                if code: r.setdefault('vaccine_code', code)
                if sysn: r.setdefault('vaccine_system', sysn)
            if tab == 'encounters':
                t = r.get('type') or ''
                if t: r.setdefault('visit_type', t)
                if disp: r.setdefault('visit_type_display', disp)
            if tab == 'problems':
                cs = r.get('clinical_status') or ''
                if cs and not r.get('status'):
                    r['status'] = cs
            if tab == 'medications':
                ft = r.get('fhir_resource_type') or ''
                if ft: r.setdefault('medication_subtype', ft)


# ============================================================================
# PART J - STAGE 4 (cont.): BUNDLE ASSEMBLY
# ============================================================================
def assemble_bundle(patients, all_records, all_documents):
    """Build the final dashboard JSON bundle, with field aliases for both schemas."""
    _alias_record_fields(all_records)
    labs_vitals = list(all_records.get('labs', [])) + list(all_records.get('vitals', []))

    # Compute num_documents per patient (original dashboard reads patient.num_documents)
    doc_counts = defaultdict(int)
    for d in all_documents:
        pid = d.get('patient_id')
        if pid:
            doc_counts[pid] += 1
    for pid, p in patients.items():
        p['num_documents'] = doc_counts.get(pid, 0)

    return {
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'pipeline': 'SMART on FHIR ETL',
            'total_patients': len(patients),
        },
        'patients':           list(patients.values()),
        'documents':          all_documents,
        'medications':        all_records.get('medications', []),
        'problems':           all_records.get('problems', []),
        'labs':               all_records.get('labs', []),
        'vitals':             all_records.get('vitals', []),
        'labs_vitals':        labs_vitals,                          # original dashboard alias
        'notes':              all_records.get('notes', []),         # CCDA section narratives
        'encounters':         all_records.get('encounters', []),
        'procedures':         all_records.get('procedures', []),
        'allergies':          all_records.get('allergies', []),
        'immunizations':      all_records.get('immunizations', []),
        'careplans':          all_records.get('careplans', []),
        'diagnostic_reports': all_records.get('diagnostic_reports', []),
        'goals':              all_records.get('goals', []),
        'document_references':all_records.get('document_references', []),
    }


# ============================================================================
# PART K - STAGE 5: ENRICHMENT
# ============================================================================
def enrich_bundle(bundle):
    """Backfill display_name from LOINC_DISPLAY / SNOMED_DISPLAY where missing."""
    enriched = 0
    for tab in ('labs', 'vitals', 'labs_vitals', 'problems', 'medications', 'allergies',
                'procedures', 'immunizations', 'diagnostic_reports', 'encounters'):
        for row in bundle.get(tab, []):
            new_disp = enrich_one(row.get('code'), row.get('display_name'))
            if new_disp != row.get('display_name'):
                row['display_name'] = new_disp
                row['display_enriched'] = True
                enriched += 1
    log(f"  Enriched {enriched} display names")
    return enriched


# ============================================================================
# PART L - STAGE 6: TEST PATIENT FILTER
# ============================================================================
def parse_exclusions(path):
    """Read exclusion rules from a plain-text file. Each line is one rule:
       type: value
    where type is: id, name, name_contains, mrn_contains.
    Lines without 'type:' default to name_contains."""
    rules = []
    if not path or not os.path.exists(path):
        log(f"  No exclusion file found")
        return rules
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                kind, _, val = line.partition(':')
                kind = kind.strip().lower()
                val = val.strip().strip('"').strip("'")
                if kind in {'id', 'name', 'name_contains', 'mrn_contains'} and val:
                    rules.append((kind, val))
                    continue
            rules.append(('name_contains', line))
    log(f"  Loaded {len(rules)} exclusion rules from {os.path.basename(path)}")
    return rules


def filter_test_patients(bundle, rules):
    """Apply exclusion rules. Returns (filtered_bundle, n_dropped_patients, n_dropped_records)."""
    if not rules:
        return bundle, 0, 0

    drop_ids = set()
    for p in bundle.get('patients', []):
        pid = p.get('patient_id', '')
        fn = (p.get('first_name') or '').lower()
        ln = (p.get('last_name') or '').lower()
        full = f"{fn} {ln}".strip()
        mrn = (p.get('mrn') or '').lower()

        for kind, val in rules:
            v = val.lower()
            if kind == 'id' and pid == val:
                drop_ids.add(pid); break
            if kind == 'name' and full == v:
                drop_ids.add(pid); break
            if kind == 'name_contains' and (v in fn or v in ln or v in full):
                drop_ids.add(pid); break
            if kind == 'mrn_contains' and v in mrn:
                drop_ids.add(pid); break

    if not drop_ids:
        return bundle, 0, 0

    new_bundle = {}
    dropped_records = 0
    for k, v in bundle.items():
        if k == 'metadata':
            new_bundle[k] = v; continue
        if k == 'patients':
            new_bundle[k] = [p for p in v if p.get('patient_id') not in drop_ids]
            continue
        if isinstance(v, list):
            kept = []
            for r in v:
                if r.get('patient_id') in drop_ids:
                    dropped_records += 1
                else:
                    kept.append(r)
            new_bundle[k] = kept
        else:
            new_bundle[k] = v
    return new_bundle, len(drop_ids), dropped_records


# ============================================================================
# PART M - OUTPUT
# ============================================================================
def build_patient_master_csv(bundle, out_dir):
    """
    Write a long-format "master" CSV with patient demographics joined onto
    every record. One row per record across all categories, sorted by
    patient name then date. Three files written:

        patient_master.csv       all records, both CCDA- and FHIR-sourced
        patient_master_fhir.csv  FHIR-sourced records only
        patient_master_ccda.csv  CCDA-sourced records only

    Schema (uniform across categories; NULL where the field doesn't apply):

        last_name, first_name, dob, patient_id, mrn, gender, marital_status,
        category, source, effective_date, end_date,
        code, code_system, display_name, all_codings_json,
        value, unit, status, text, source_file, raw_record_json

    Designed for cross-checking the dashboard against a spreadsheet:
    filter by patient_id to see one patient's full record, by category to
    see one category across the cohort, by source to compare CCDA vs FHIR.
    The all_codings_json and raw_record_json columns hold the full original
    code list and original record so a reviewer can verify every dashboard
    cell against the underlying source.
    """
    import csv as _csv
    import json as _json

    EXCEL_CELL_LIMIT = 32000  # Excel's hard limit is 32,767; leave headroom

    def _truncate(s):
        if s is None: return ''
        s = str(s)
        if len(s) > EXCEL_CELL_LIMIT:
            return s[:EXCEL_CELL_LIMIT - 20] + '...[truncated]'
        return s

    def _date(record):
        for k in ('effective_date', 'start_date', 'authored_on',
                  'occurrence_date', 'recorded_date', 'date'):
            v = record.get(k)
            if v: return str(v)[:10]
        return ''

    def _end_date(record):
        for k in ('end_date', 'resolved_date', 'stop_date'):
            v = record.get(k)
            if v: return str(v)[:10]
        return ''

    def _value_unit(record):
        v = record.get('value')
        u = record.get('unit') or ''
        return ('' if v is None else str(v)), str(u)

    def _text(record, category):
        # Pick the best free-text column for this category
        if category == 'notes':
            return record.get('narrative_text') or ''
        if category == 'medications':
            return (record.get('dosage_text') or record.get('display_name') or '')
        if category == 'diagnostic_reports':
            return (record.get('conclusion') or record.get('result') or
                    record.get('display_name') or '')
        if category == 'procedures':
            note = ''
            if record.get('note'):
                if isinstance(record['note'], list):
                    note = ' | '.join(str(n.get('text','') if isinstance(n, dict) else n)
                                      for n in record['note'])
                else:
                    note = str(record['note'])
            return note or record.get('display_name', '')
        if category == 'careplans':
            return (record.get('description') or record.get('title') or '')
        if category == 'goals':
            return record.get('description') or ''
        if category == 'allergies':
            r = record.get('reaction', '')
            if isinstance(r, list):
                r = ' | '.join(str(x) for x in r)
            return r or record.get('display_name', '')
        if category == 'documents':
            return record.get('plain_text', '')
        return record.get('display_name') or ''

    def _source_file(record):
        return (record.get('source_file') or record.get('document_id') or
                record.get('file') or record.get('fhir_resource_type', ''))

    def _all_codings(record):
        ac = record.get('all_codings')
        if not ac: return ''
        try:
            return _json.dumps(ac, separators=(',', ':'))
        except Exception:
            return ''

    # --- Build patient demographics lookup --------------------------------
    pts = bundle.get('patients', [])
    pt_by_id = {}
    for p in pts:
        pid = p.get('patient_id', '')
        if not pid: continue
        pt_by_id[pid] = {
            'last_name': p.get('last_name', '') or '',
            'first_name': p.get('first_name', '') or '',
            'dob': p.get('dob', '') or '',
            'patient_id': pid,
            'mrn': p.get('mrn', '') or '',
            'gender': p.get('gender', '') or '',
            'marital_status': p.get('marital_status', '') or '',
        }

    # Categories to include with their canonical name
    categories = [
        'documents', 'encounters', 'problems', 'medications', 'procedures',
        'labs_vitals', 'allergies', 'immunizations', 'careplans',
        'diagnostic_reports', 'goals', 'notes', 'document_references',
    ]

    # Patient header row (one per patient) so even a patient with no other
    # records shows up in the master CSV
    header_rows = []
    for pid, demo in pt_by_id.items():
        header_rows.append({
            **demo,
            'category': 'patient',
            'source': '',
            'effective_date': demo['dob'],
            'end_date': '',
            'code': '',
            'code_system': '',
            'display_name': f"{demo['first_name']} {demo['last_name']}".strip(),
            'all_codings_json': '',
            'value': '',
            'unit': '',
            'status': '',
            'text': '',
            'source_file': '',
            'raw_record_json': _json.dumps(
                next((p for p in pts if p.get('patient_id') == pid), {}),
                separators=(',', ':'),
                default=str,
            ),
        })

    # All other categories
    record_rows = []
    for cat in categories:
        for record in bundle.get(cat, []):
            pid = record.get('patient_id', '')
            demo = pt_by_id.get(pid, {
                'last_name': '(unknown)',
                'first_name': '(unknown)',
                'dob': '',
                'patient_id': pid,
                'mrn': '',
                'gender': '',
                'marital_status': '',
            })
            value, unit = _value_unit(record)
            try:
                raw = _json.dumps(record, separators=(',', ':'), default=str)
            except Exception:
                raw = ''
            record_rows.append({
                **demo,
                'category': cat,
                'source': record.get('source', ''),
                'effective_date': _date(record),
                'end_date': _end_date(record),
                'code': str(record.get('code') or ''),
                'code_system': str(record.get('code_system') or ''),
                'display_name': _truncate(record.get('display_name') or
                                          record.get('section_title') or ''),
                'all_codings_json': _truncate(_all_codings(record)),
                'value': value,
                'unit': unit,
                'status': str(record.get('status') or
                              record.get('clinical_status') or ''),
                'text': _truncate(_text(record, cat)),
                'source_file': str(_source_file(record) or ''),
                'raw_record_json': _truncate(raw),
            })

    all_rows = header_rows + record_rows
    # Sort: by last_name, first_name, then category order (with patient
    # header first), then date
    cat_order = {'patient': 0}
    for i, c in enumerate(categories, start=1):
        cat_order[c] = i
    all_rows.sort(key=lambda r: (
        (r.get('last_name') or '').lower(),
        (r.get('first_name') or '').lower(),
        r.get('patient_id') or '',
        cat_order.get(r.get('category'), 999),
        r.get('effective_date') or '',
    ))

    fieldnames = [
        'last_name', 'first_name', 'dob',
        'patient_id', 'mrn', 'gender', 'marital_status',
        'category', 'source', 'effective_date', 'end_date',
        'code', 'code_system', 'display_name', 'all_codings_json',
        'value', 'unit', 'status', 'text', 'source_file',
        'raw_record_json',
    ]

    def _write(rows, path):
        # utf-8-sig so Excel handles non-ASCII characters cleanly
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = _csv.DictWriter(f, fieldnames=fieldnames,
                                quoting=_csv.QUOTE_ALL)
            w.writeheader()
            for r in rows:
                # Ensure every field is present; missing -> ''
                w.writerow({k: r.get(k, '') for k in fieldnames})

    main_path = os.path.join(out_dir, 'patient_master.csv')
    fhir_path = os.path.join(out_dir, 'patient_master_fhir.csv')
    ccda_path = os.path.join(out_dir, 'patient_master_ccda.csv')

    _write(all_rows, main_path)

    # FHIR-only: include patient header + FHIR-sourced records
    fhir_rows = [r for r in all_rows
                 if r['category'] == 'patient' or r['source'] == 'fhir']
    _write(fhir_rows, fhir_path)

    # CCDA-only: include patient header + CCDA-sourced records
    ccda_rows = [r for r in all_rows
                 if r['category'] == 'patient' or r['source'] == 'ccda']
    _write(ccda_rows, ccda_path)

    return {
        'all': len(all_rows),
        'fhir': len(fhir_rows),
        'ccda': len(ccda_rows),
        'patients': len(header_rows),
    }


def build_code_inventory(bundle, out_path):
    """
    Walk every coded record in the bundle and produce an inventory CSV
    listing each unique (vocabulary, code) pair with reference count, unique
    patient count, display name, and the source categories it appears in.

    Output columns:
        vocabulary           e.g. SNOMED-CT, RxNorm, LOINC, ICD-10-CM, CVX, CPT
        code                 the source code value
        display_name         most-common display string seen for that code
        n_references         total number of records referencing this code
        n_unique_patients    number of distinct patients with this code
        source_categories    semicolon-separated list of bundle tabs (e.g.
                             "problems;diagnostic_reports")
    """
    from collections import Counter, defaultdict
    inventory = defaultdict(lambda: {
        'display_counter': Counter(),
        'patients': set(),
        'categories': set(),
        'n_references': 0,
    })

    # categories that carry codes. Skip 'labs' and 'vitals' because the
    # pipeline also emits the union of both as 'labs_vitals'; counting all
    # three would triple-count the same observation rows.
    coded_categories = (
        'problems', 'medications', 'procedures', 'allergies', 'immunizations',
        'labs_vitals', 'diagnostic_reports',
        'document_references', 'encounters',
    )

    for cat in coded_categories:
        rows = bundle.get(cat, [])
        for row in rows:
            pid = row.get('patient_id')
            codings = row.get('all_codings') or []
            # fallback to top-level code/code_system pair if all_codings missing
            if not codings and row.get('code'):
                codings = [{
                    'code': row.get('code'),
                    'system_name': row.get('code_system') or '',
                    'display': row.get('display_name') or '',
                }]
            for c in codings:
                code = (c.get('code') or '').strip()
                vocab = (c.get('system_name') or '').strip()
                disp = (c.get('display') or '').strip()
                if not code or not vocab:
                    continue
                key = (vocab, code)
                rec = inventory[key]
                rec['n_references'] += 1
                if pid:
                    rec['patients'].add(pid)
                rec['categories'].add(cat)
                if disp:
                    rec['display_counter'][disp] += 1

    # Write CSV (sorted by vocabulary, then descending references)
    rows_out = []
    for (vocab, code), rec in inventory.items():
        most_common_display = (rec['display_counter'].most_common(1)[0][0]
                               if rec['display_counter'] else '')
        rows_out.append({
            'vocabulary': vocab,
            'code': code,
            'display_name': most_common_display,
            'n_references': rec['n_references'],
            'n_unique_patients': len(rec['patients']),
            'source_categories': ';'.join(sorted(rec['categories'])),
        })
    rows_out.sort(key=lambda r: (r['vocabulary'], -r['n_references'], r['code']))

    import csv as _csv
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = _csv.DictWriter(f, fieldnames=['vocabulary','code','display_name',
                                           'n_references','n_unique_patients',
                                           'source_categories'])
        w.writeheader()
        for row in rows_out:
            w.writerow(row)

    # Per-vocabulary summary in the log
    by_vocab = Counter()
    for r in rows_out:
        by_vocab[r['vocabulary']] += 1
    for vocab, n in by_vocab.most_common():
        log(f"    {vocab}: {n} unique codes")

    return len(rows_out)


def write_outputs(bundle, json_path, xlsx_path, csv_dir):
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(bundle, f, indent=2, default=str)
    log(f"  JSON written: {json_path}  ({os.path.getsize(json_path)/1e6:.1f} MB)")

    try:
        with pd.ExcelWriter(xlsx_path, engine='openpyxl') as xw:
            for k, v in bundle.items():
                if k == 'metadata' or not isinstance(v, list) or not v:
                    continue
                df = pd.DataFrame(v)
                for col in df.select_dtypes(include='object').columns:
                    df[col] = df[col].map(lambda x: excel_safe(x) if isinstance(x, str) else x)
                sheet = re.sub(r'[/\\?*\[\]]', '_', k)[:31]
                df.to_excel(xw, sheet_name=sheet, index=False)
        log(f"  Excel written: {xlsx_path}")
    except Exception as e:
        log(f"  Excel write skipped ({type(e).__name__}: {e})")

    os.makedirs(csv_dir, exist_ok=True)
    for k, v in bundle.items():
        if k == 'metadata' or not isinstance(v, list) or not v:
            continue
        path = os.path.join(csv_dir, f"{k}.csv")
        try:
            pd.DataFrame(v).to_csv(path, index=False)
        except Exception as e:
            log(f"  CSV write failed for {k}: {e}")
    log(f"  Per-tab CSVs written to: {csv_dir}")


# ============================================================================
# PART N - ORCHESTRATOR
# ============================================================================
def main():
    log("=" * 72)
    log("EHR Pipeline -- starting")
    log("=" * 72)

    # ---- Mount Drive (Colab only, if configured) ----
    if IN_COLAB and DRIVE_DIR:
        log("Mounting Google Drive ...")
        try:
            from google.colab import drive
            drive.mount('/content/drive', force_remount=True)
        except Exception as e:
            log(f"Drive mount failed: {e}")

    # ---- Load doc->patient mapping ----
    doc_map = {}
    mapping_demographics = {}  # patient_id -> {first_name, last_name, mrn, dob, gender}
    found_mapping = next((p for p in MAPPING_CANDIDATES if p and os.path.exists(p)), None)
    if found_mapping:
        try:
            dm = pd.read_csv(found_mapping, dtype=str).fillna('')
            log(f"Mapping CSV: {os.path.basename(found_mapping)} ({len(dm)} rows)")
            uuid_col = next((c for c in ('document_uuid', 'id', 'doc_uuid', 'fhirdocument_id') if c in dm.columns), None)
            if uuid_col and 'patient_id' in dm.columns:
                pairs = list(zip(dm[uuid_col].astype(str), dm['patient_id'].astype(str)))
                doc_map = {k: v for k, v in pairs if k and v}
                log(f"  doc->patient entries: {len(doc_map)}")
                demo_cols = [c for c in ('first_name', 'last_name', 'mrn', 'dob', 'gender') if c in dm.columns]
                for _, row in dm.iterrows():
                    pid = (row.get('patient_id') or '').strip()
                    if not pid:
                        continue
                    if pid not in mapping_demographics:
                        mapping_demographics[pid] = {}
                    for k in demo_cols:
                        v = (row.get(k) or '').strip()
                        if v and not mapping_demographics[pid].get(k):
                            if k in ('first_name', 'last_name', 'mrn'):
                                mapping_demographics[pid][k] = clean_name(v)
                            else:
                                mapping_demographics[pid][k] = v
                log(f"  patient demographics from mapping: {len(mapping_demographics)} patients (cols: {demo_cols})")
            else:
                log(f"  Could not find uuid + patient_id columns; columns: {list(dm.columns)}")
        except Exception as e:
            log(f"  Mapping load error: {e}")
    else:
        log("  No mapping CSV found")

    # ---- STAGE 1: Reassembly ----
    log("\nSTAGE 1 -- Decoding & reassembly")
    reassemble_chunks(CCDA_CSV, CCDA_DIR, is_base64=True)
    reassemble_chunks(FHIR_CSV, FHIR_DIR, is_base64=False)

    # ---- STAGES 2+3: Parse FHIR + CCDA ----
    log("\nSTAGE 2+3 -- Parsing CCDA and FHIR")
    patients = {}
    documents = []
    records = defaultdict(list)
    fmt_counts = defaultdict(int)

    # Pre-pass: build global cross-bundle Medication + DocumentReference indices
    global_med_index = {}
    global_docref_pid = {}
    if os.path.exists(FHIR_DIR):
        log("  Pre-pass: building global Medication + DocumentReference indices")
        for fname in sorted(os.listdir(FHIR_DIR)):
            if not fname.endswith('.json'):
                continue
            try:
                with open(os.path.join(FHIR_DIR, fname), 'r', encoding='utf-8') as f:
                    bundle = json.load(f)
            except Exception:
                continue
            for entry in bundle.get('entry', []) or []:
                res = entry.get('resource') or {}
                rt = res.get('resourceType', '')
                full_url = entry.get('fullUrl', '') or ''
                if rt == 'Medication':
                    mc = res.get('code') or {}
                    if not mc:
                        continue
                    mid = res.get('id', '')
                    if mid:
                        global_med_index[mid] = mc
                        global_med_index['#' + mid] = mc
                        global_med_index['Medication/' + mid] = mc
                    if full_url:
                        global_med_index[full_url] = mc
                elif rt == 'DocumentReference':
                    subj = res.get('subject') or {}
                    subj_ref = subj.get('reference') if isinstance(subj, dict) else ''
                    if subj_ref and '/' in subj_ref:
                        pid = subj_ref.split('/')[-1]
                        did = res.get('id', '')
                        if did:
                            global_docref_pid[did] = pid
                            global_docref_pid['DocumentReference/' + did] = pid
                        if full_url:
                            global_docref_pid[full_url] = pid
                        for content in res.get('content', []) or []:
                            if not isinstance(content, dict):
                                continue
                            att = content.get('attachment') or {}
                            url = att.get('url', '') if isinstance(att, dict) else ''
                            if url:
                                global_docref_pid[url] = pid
                                tail = url.rsplit('/', 1)[-1]
                                if tail:
                                    global_docref_pid[tail] = pid
        log(f"    Medications indexed: {len(global_med_index)}; DocumentReferences indexed: {len(global_docref_pid)}")
        # Merge global doc->pid into doc_map (mapping CSV wins on conflict)
        for k, v in global_docref_pid.items():
            if k not in doc_map:
                doc_map[k] = v

    # FHIR first (Patient resources are authoritative for naming)
    if os.path.exists(FHIR_DIR):
        for fname in sorted(os.listdir(FHIR_DIR)):
            if not fname.endswith('.json'):
                continue
            doc_id = fname[:-5]
            try:
                with open(os.path.join(FHIR_DIR, fname), 'r', encoding='utf-8') as f:
                    bundle = json.load(f)
            except Exception as e:
                log(f"  FHIR read error {fname}: {e}")
                continue
            try:
                patient_rec, structured = parse_fhir_bundle(bundle, doc_id, doc_map, global_med_index=global_med_index)
            except Exception as e:
                log(f"  FHIR parse error {fname}: {type(e).__name__}: {e}")
                continue

            if patient_rec and patient_rec.get('patient_id'):
                pid = patient_rec['patient_id']
                if pid in patients:
                    for k, v in patient_rec.items():
                        if v and not patients[pid].get(k):
                            patients[pid][k] = v
                else:
                    patients[pid] = patient_rec

            for cat, rows in structured.items():
                for r in rows:
                    pid = r.get('patient_id')
                    if pid and pid not in patients:
                        patients[pid] = {'patient_id': pid, 'first_name': '',
                                         'last_name': '', 'mrn': '', 'dob': '', 'gender': ''}
                    records[cat].append(r)

    # Build MRN -> pid index for CCDA fallback
    mrn_to_pid = {}
    for ppid, pp in patients.items():
        m = normalize_mrn(pp.get('mrn'))
        if m:
            mrn_to_pid[m] = ppid

    # CCDA second
    if os.path.exists(CCDA_DIR):
        for fname in sorted(os.listdir(CCDA_DIR)):
            doc_uuid = fname.rsplit('.', 1)[0]
            path = os.path.join(CCDA_DIR, fname)
            try:
                with open(path, 'rb') as f:
                    raw = f.read()
            except Exception as e:
                log(f"  CCDA read error {fname}: {e}")
                continue

            # Re-decode in case reassembly didn't go through robust_decode
            payload, _ = robust_decode(raw)
            if not isinstance(payload, bytes):
                payload = payload.encode('utf-8', errors='ignore') if isinstance(payload, str) else raw

            fmt = detect_format(payload)
            fmt_counts[fmt] += 1

            pid = doc_map.get(doc_uuid)

            doc_text = ''
            doc_record = {
                'document_uuid': doc_uuid,
                'source_file':   fname,
                'source_format': fmt,
                'source_type':   {'rtf': 'rtf_note'}.get(fmt, fmt),
                'patient_id':    pid,
            }

            try:
                if fmt == 'pdf':
                    doc_text = extract_pdf_text(payload)
                elif fmt == 'rtf':
                    doc_text = strip_rtf(payload.decode('utf-8', errors='ignore'))
                elif fmt == 'html_fragment':
                    doc_text = strip_html(payload.decode('utf-8', errors='ignore'))
                elif fmt == 'ccda_xml':
                    header, structured, narrative = parse_ccda_document(payload, doc_uuid, fname)
                    doc_text = narrative
                    # Resolve PID: doc_map first, then MRN fallback
                    if not pid and header:
                        target_mrn = normalize_mrn(header.get('mrn'))
                        if target_mrn and target_mrn in mrn_to_pid:
                            pid = mrn_to_pid[target_mrn]
                            doc_record['patient_id'] = pid
                    if pid:
                        if pid not in patients:
                            patients[pid] = {'patient_id': pid, 'first_name': '', 'last_name': '',
                                             'mrn': '', 'dob': '', 'gender': ''}
                        for k in ('first_name', 'last_name', 'mrn', 'dob', 'gender'):
                            if header and header.get(k) and not patients[pid].get(k):
                                patients[pid][k] = header[k]
                        for cat, rows in structured.items():
                            for r in rows:
                                r['patient_id'] = pid
                                records[cat].append(r)
                else:
                    doc_text = payload.decode('utf-8', errors='ignore')
            except Exception as e:
                log(f"  CCDA parse error {fname}: {type(e).__name__}: {e}")

            doc_record['plain_text'] = (doc_text or '')[:30000]
            documents.append(doc_record)

    log(f"  Format counts: {dict(fmt_counts)}")
    log(f"  Patients discovered (pre-mapping fill): {len(patients)}")

    # ---- Fill demographics from mapping ----
    if mapping_demographics:
        added, filled = 0, 0
        for pid, demo in mapping_demographics.items():
            if pid not in patients:
                patients[pid] = {'patient_id': pid, 'first_name': '', 'last_name': '',
                                 'mrn': '', 'dob': '', 'gender': ''}
                added += 1
            for k, v in demo.items():
                if v and not patients[pid].get(k):
                    patients[pid][k] = v
                    filled += 1
        log(f"  Mapping fill-in: added {added} CCDA-only patients, filled {filled} demographic fields")
    log(f"  Patients (post-mapping fill): {len(patients)}")

    for k, v in records.items():
        log(f"    {k}: {len(v)} records")

    # ---- Patient deduplication by name+DOB ----
    n_merged, n_recs, n_docs = _dedupe_patients_by_name_dob(patients, records, documents)
    if n_merged:
        log(f"  Patient dedup: merged {n_merged} duplicates ({n_recs} records, {n_docs} docs reassigned)")
    log(f"  Patients after dedup: {len(patients)}")

    # ---- STAGE 4: assemble ----
    log("\nSTAGE 4 -- Assembling bundle")
    bundle = assemble_bundle(patients, records, documents)

    with open(OUT_BACKUP, 'w', encoding='utf-8') as f:
        json.dump(bundle, f, indent=2, default=str)
    log(f"  Pre-filter backup: {OUT_BACKUP}")

    # ---- STAGE 5: enrich ----
    log("\nSTAGE 5 -- Enriching display names")
    enrich_bundle(bundle)

    # ---- STAGE 6: filter test patients ----
    log("\nSTAGE 6 -- Filtering test patients")
    found_excl = next((p for p in EXCLUSION_CANDIDATES if p and os.path.exists(p)), None)
    rules = parse_exclusions(found_excl)
    # Merge hardcoded rules - these always apply
    hardcoded = []
    for line in HARDCODED_TEST_EXCLUSIONS:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            kind, _, val = line.partition(':')
            kind = kind.strip().lower()
            val = val.strip().strip('"').strip("'")
            if kind in {'id', 'name', 'name_contains', 'mrn_contains'} and val:
                hardcoded.append((kind, val))
                continue
        hardcoded.append(('name_contains', line))
    if hardcoded:
        log(f"  Loaded {len(hardcoded)} hardcoded exclusion rules from run_pipeline.py")
    rules = list(rules) + hardcoded
    log(f"  Total active exclusion rules: {len(rules)}")
    bundle, n_pat, n_rec = filter_test_patients(bundle, rules)
    log(f"  Removed {n_pat} test patients ({n_rec} associated records)")

    # ---- Outputs ----
    log("\nWriting outputs")
    write_outputs(bundle, OUT_JSON, OUT_XLSX, OUT_CSV_DIR)

    # ---- STAGE 7: code inventory ----
    log("\nSTAGE 7 -- Building code inventory")
    inv_path = os.path.join(BASE_DIR, 'code_inventory.csv')
    n_codes = build_code_inventory(bundle, inv_path)
    log(f"  {n_codes} unique (vocabulary, code) pairs written to {inv_path}")

    # ---- STAGE 7b: patient master CSV (per-patient long-format export) ----
    log("\nSTAGE 7b -- Building patient master CSV")
    counts = build_patient_master_csv(bundle, BASE_DIR)
    log(f"  patient_master.csv         {counts['all']:>7,} rows ({counts['patients']} patients)")
    log(f"  patient_master_fhir.csv    {counts['fhir']:>7,} rows")
    log(f"  patient_master_ccda.csv    {counts['ccda']:>7,} rows")

    # ---- STAGE 7c: regex note extraction (optional; skipped if module absent) ----
    log("\nSTAGE 7c -- Running regex note extraction")
    try:
        # note_extraction.py must sit next to run_pipeline.py for this import.
        # If it's not available we just log and continue -- the rest of the
        # pipeline outputs are already written.
        import sys as _sys, importlib as _importlib
        _here = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else BASE_DIR
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        # Force re-import so a freshly-uploaded note_extraction.py is picked up
        if 'note_extraction' in _sys.modules:
            _importlib.reload(_sys.modules['note_extraction'])
        else:
            import note_extraction  # noqa: F401
        import note_extraction as _ne
        bundle_path = os.path.join(BASE_DIR, 'dashboard_data.json')
        out_path    = os.path.join(BASE_DIR, 'note_extractions.csv')
        rows = _ne.main(bundle_path=bundle_path, out_path=out_path)
        log(f"  note_extractions.csv       {len(rows):>7,} matches written to {out_path}")
    except ImportError:
        log("  (skipped -- note_extraction.py not found alongside run_pipeline.py)")
    except Exception as _e:
        log(f"  (note extraction failed: {type(_e).__name__}: {_e})")

    try:
        with open(OUT_LOG, 'w', encoding='utf-8') as f:
            f.write('\n'.join(LOG_LINES))
    except Exception:
        pass

    log("\nPipeline complete.")
    log(f"  Final patients: {len(bundle.get('patients', []))}")
    return bundle


if __name__ == '__main__':
    main()
