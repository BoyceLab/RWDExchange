"""
Note extraction (regex-based)
==============================
Reads `dashboard_data.json` (output of run_pipeline.py) and walks every
unstructured-text record &mdash; CCDA section narratives in `notes` and the
`plain_text` field of decoded clinical documents &mdash; matching a library of
disease-specific regular expressions. Writes `note_extractions.csv` with a
row per match capturing the patient, source, pattern name, captured value,
and a surrounding snippet for manual review.

The patterns shipped here are seed patterns for ALS registries. They cover
content that is rarely captured as discrete coded values in production EHRs
and so have to be reconstructed from free-text notes:

    - ALSFRS-R total and sub-domain scores
    - ECAS (Edinburgh Cognitive and Behavioural ALS Screen) total + domains
    - El Escorial classification (definite / probable / possible)
    - Site of onset (bulbar / limb / spinal / respiratory)
    - Cognitive/behavioral involvement (FTD spectrum)
    - Family history of ALS / known genetic mutations
    - Forced vital capacity (FVC) percent-predicted
    - PEG, NIV, riluzole, edaravone start/stop dates

These are STARTER PATTERNS, not validated NLP. Local clinical phrasing varies
and patterns will need site-specific tuning. Validate every pattern against
your own corpus before using captured values for analysis. For production
clinical NLP use cTAKES, MedSpaCy, or similar; this module is intended for
quick exploration and as a template adopters can extend.

Usage
-----
    python note_extraction.py

or programmatically:

    import note_extraction
    note_extraction.main(
        bundle_path='./dashboard_data.json',
        out_path='./note_extractions.csv',
    )
"""

import os
import re
import csv
import json
import datetime as _dt
from collections import Counter, defaultdict


# --- USER CONFIGURATION -----------------------------------------------------
BUNDLE_PATH = './dashboard_data.json'
OUT_PATH    = './note_extractions.csv'
SNIPPET_RADIUS = 60   # characters of surrounding context to capture per match
# ----------------------------------------------------------------------------


# Compile patterns once. Each pattern entry:
#   (pattern_name, compiled_regex, value_group, description)
# value_group is the regex group whose captured text is the actual extracted
# value. Group 0 (the whole match) is used when value_group is None.
PATTERNS = []

def _add(name, pattern, value_group=1, description='', flags=re.IGNORECASE):
    PATTERNS.append((name, re.compile(pattern, flags), value_group, description))


# ----- ALSFRS-R -------------------------------------------------------------
_add('alsfrs_r_total',
     r'\b(?:ALSFRS[\s\-]*R(?:\s*total)?|ALS\s+Functional\s+Rating\s+Scale[\s\-]*(?:Revised|R))[:\s]+'
     r'(\d{1,2})(?:\s*/\s*48)?',
     value_group=1,
     description='ALSFRS-R total score, range 0-48')

_add('alsfrs_r_bulbar',
     r'\bALSFRS[\s\-]*R[^.\n]{0,80}?bulbar\s*(?:sub)?(?:score|domain)?\s*[:=]?\s*(\d{1,2})',
     value_group=1,
     description='ALSFRS-R bulbar subdomain score (range 0-12)')

_add('alsfrs_r_fine_motor',
     r'\bALSFRS[\s\-]*R[^.\n]{0,80}?(?:fine\s*motor|upper\s*extremity)\s*(?:sub)?(?:score|domain)?\s*[:=]?\s*(\d{1,2})',
     value_group=1,
     description='ALSFRS-R fine motor / upper extremity subdomain (0-12)')

_add('alsfrs_r_gross_motor',
     r'\bALSFRS[\s\-]*R[^.\n]{0,80}?(?:gross\s*motor|lower\s*extremity|legs)\s*(?:sub)?(?:score|domain)?\s*[:=]?\s*(\d{1,2})',
     value_group=1,
     description='ALSFRS-R gross motor / lower extremity subdomain (0-12)')

_add('alsfrs_r_respiratory',
     r'\bALSFRS[\s\-]*R[^.\n]{0,80}?respirator(?:y|ion)\s*(?:sub)?(?:score|domain)?\s*[:=]?\s*(\d{1,2})',
     value_group=1,
     description='ALSFRS-R respiratory subdomain score (range 0-12)')


# ----- ECAS (cognitive/behavioral) -----------------------------------------
_add('ecas_total',
     r'\b(?:ECAS|Edinburgh\s+Cognitive\s+(?:and\s+)?Behavi[ou]ral\s+ALS\s+Screen)\b[^.\n]{0,40}?'
     r'(?:total)?[:\s]+(\d{1,3})(?:\s*/\s*136)?',
     value_group=1,
     description='ECAS total (max 136)')

_add('ecas_als_specific',
     r'\bECAS[^.\n]{0,60}?ALS[\s\-]*specific\s*[:=]?\s*(\d{1,3})',
     value_group=1,
     description='ECAS ALS-specific score (executive, language, fluency)')

_add('ecas_non_als_specific',
     r'\bECAS[^.\n]{0,60}?non[\s\-]*ALS[\s\-]*specific\s*[:=]?\s*(\d{1,3})',
     value_group=1,
     description='ECAS non-ALS-specific score (memory, visuospatial)')

_add('ftd_spectrum',
     r'\b(?:ALS[\s\-]*FTD|frontotemporal\s+(?:lobar\s+)?(?:degeneration|dementia)|'
     r'behavior(?:al)?\s+variant\s+FTD|bvFTD|executive\s+dysfunction)\b',
     value_group=0,
     description='Frontotemporal-spectrum involvement mention')


# ----- El Escorial / Awaji classification ---------------------------------
_add('el_escorial',
     r'\b(definite|clinically\s+probable|probable|lab[\s\-]*supported\s+probable|'
     r'clinically\s+possible|possible|suspected)\s+ALS\b',
     value_group=1,
     description='El Escorial / Awaji-Shima diagnostic certainty level')


# ----- Site / region of onset ---------------------------------------------
_add('onset_region',
     r'\b(bulbar|limb|spinal|respiratory|axial|cervical|lumbar|upper[\s\-]*limb|lower[\s\-]*limb)'
     r'[\s\-]*onset\b',
     value_group=1,
     description='Site of disease onset')


# ----- Family history of ALS / genetic mutations --------------------------
_add('family_history_negative',
     r'\b(?:no\s+(?:known\s+)?family\s+(?:history|hx)|negative\s+family\s+(?:history|hx)|'
     r'family\s+(?:history|hx)\s*[:=]?\s*(?:negative|none|noncontributory))'
     r'(?:\s+(?:of|for)\s+(?:ALS|MND|motor\s+neuro(?:n|ne)\s+disease|neurodegenerative\s+disease))?',
     value_group=0,
     description='Negative family history')

_add('family_history_positive',
     r'(?<!\bno\s)(?<!\bnegative\s)(?<!\bdenies\s)'
     r'\b(?:family\s+(?:history|hx)\s+positive\s+for|'
     r'\+\s*(?:FH|family\s+hx|family\s+history)|'
     r'(?:father|mother|brother|sister|sibling|aunt|uncle|cousin|grandparent|grandfather|grandmother)\s+'
     r'(?:had|has|with|diagnosed\s+with|died\s+(?:of|from)))\s*'
     r'(ALS|MND|motor\s+neuro(?:n|ne)\s+disease|FTD|frontotemporal\s+dementia)',
     value_group=1,
     description='Affected relative with ALS/MND/FTD (positive family history)')

_add('genetic_mutation',
     r'\b(C9orf72|SOD1|FUS|TARDBP|TBK1|VCP|UBQLN2|PFN1|MATR3|CHCHD10|ANG|OPTN|ATXN2)\b'
     r'[^.\n]{0,40}?(?:positive|carrier|mutation|expansion|repeat|variant|pathogenic)',
     value_group=1,
     description='ALS-associated genetic mutation positive')


# ----- FVC (percent predicted) ---------------------------------------------
_add('fvc_percent_predicted',
     r'\b(?:FVC|forced\s+vital\s+capacity)\s*[:=]?\s*(\d{1,3}(?:\.\d+)?)\s*%\s*(?:predicted|pred)?',
     value_group=1,
     description='Forced vital capacity, percent predicted')


# ----- Treatment milestones with dates --------------------------------------
# Date formats: ISO YYYY-MM-DD, US M/D/YYYY, "Mar 2024", "March 15, 2024"
_DATE_RE = (r'(?:\d{4}[\-/]\d{1,2}[\-/]\d{1,2}|\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4}|'
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}|'
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})')

_add('peg_placement_date',
     r'\b(?:PEG(?:\s+tube)?|gastrostomy|percutaneous\s+endoscopic\s+gastrostomy)'
     r'[^.\n]{0,80}?(?:placed|placement|inserted|inserted)\s+on\s+(' + _DATE_RE + r')',
     value_group=1,
     description='Date of PEG / gastrostomy tube placement')

_add('niv_initiation_date',
     r'\b(?:BiPAP|NIV|non[\s\-]invasive\s+ventilation|CPAP|nocturnal\s+ventilation)'
     r'[^.\n]{0,80}?(?:initiated|started|began)\s+on\s+(' + _DATE_RE + r')',
     value_group=1,
     description='Date of non-invasive ventilation initiation')

_add('riluzole_start_date',
     r'\b(?:started|initiated|began)\s+(?:on\s+)?riluzole\s+(?:on\s+)?(' + _DATE_RE + r')',
     value_group=1,
     description='Date riluzole therapy started')

_add('edaravone_start_date',
     r'\b(?:started|initiated|began)\s+(?:on\s+)?edaravone\s+(?:on\s+)?(' + _DATE_RE + r')',
     value_group=1,
     description='Date edaravone therapy started')

_add('tracheostomy_date',
     r'\btracheostomy[^.\n]{0,80}?(?:placed|placement|performed|on)\s+(' + _DATE_RE + r')',
     value_group=1,
     description='Date of tracheostomy')


# ---------- Logging helper --------------------------------------------------
LOG = []
def log(msg):
    line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    LOG.append(line)


# ---------- Source iteration -----------------------------------------------
def iter_text_sources(bundle):
    """Yield (patient_id, source_kind, source_id, text) for every record
    that contains free-text content. Coerces None values to empty strings
    so callers can safely sort/index by these fields."""
    # CCDA section narratives
    for note in bundle.get('notes', []):
        text = note.get('narrative_text') or ''
        if not text: continue
        yield (
            note.get('patient_id') or '',
            'ccda_section',
            f"{note.get('document_id') or ''}::{note.get('section_title') or ''}",
            text,
        )
    # Decoded clinical documents (HTML, RTF, PDF text)
    for doc in bundle.get('documents', []):
        text = doc.get('plain_text') or ''
        if not text or text.startswith('[PDF -'):
            continue
        yield (
            doc.get('patient_id') or '',
            f"document:{doc.get('source_format') or 'unknown'}",
            doc.get('document_id') or doc.get('file') or '',
            text,
        )


# ---------- Snippet helper -------------------------------------------------
def make_snippet(text, start, end, radius=SNIPPET_RADIUS):
    """Return the matched span surrounded by `radius` chars on each side, with
    newlines collapsed to spaces and ellipses where the text was truncated."""
    s = max(0, start - radius)
    e = min(len(text), end + radius)
    prefix = '...' if s > 0 else ''
    suffix = '...' if e < len(text) else ''
    snippet = text[s:e].replace('\r', ' ').replace('\n', ' ')
    snippet = re.sub(r'\s+', ' ', snippet).strip()
    return f"{prefix}{snippet}{suffix}"


# ---------- Main extraction loop -------------------------------------------
def extract(bundle):
    """Apply all patterns to all text sources. Returns list of dict rows and
    a Counter summarizing per-pattern hit counts."""
    rows = []
    by_pattern = Counter()
    seen_sources = 0

    for pid, src_kind, src_id, text in iter_text_sources(bundle):
        seen_sources += 1
        for name, regex, vg, desc in PATTERNS:
            for m in regex.finditer(text):
                value = m.group(vg) if vg is not None else m.group(0)
                rows.append({
                    'patient_id': pid,
                    'source_kind': src_kind,
                    'source_id': src_id,
                    'pattern': name,
                    'value': (value or '').strip(),
                    'snippet': make_snippet(text, m.start(), m.end()),
                    'char_offset': m.start(),
                    'description': desc,
                })
                by_pattern[name] += 1

    return rows, by_pattern, seen_sources


# ---------- Writer ---------------------------------------------------------
def write_csv(rows, out_path):
    fieldnames = ['patient_id','source_kind','source_id','pattern',
                  'value','snippet','char_offset','description']
    # Coerce sort keys to safe types -- None values are real in production data
    # (FHIR-only patients without demographic linkage produce rows where
    # patient_id is None), and Python 3 refuses to compare None with str.
    rows = sorted(rows, key=lambda r: (
        str(r.get('patient_id') or ''),
        str(r.get('pattern') or ''),
        str(r.get('source_id') or ''),
        int(r.get('char_offset') or 0),
    ))
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


# ---------- Main -----------------------------------------------------------
def main(bundle_path=None, out_path=None):
    bundle_path = bundle_path or BUNDLE_PATH
    out_path = out_path or OUT_PATH

    log('=' * 72)
    log('Note extraction -- starting')
    log('=' * 72)
    log(f'Bundle: {bundle_path}')
    log(f'Out:    {out_path}')
    log(f'Patterns loaded: {len(PATTERNS)}')

    with open(bundle_path) as f:
        bundle = json.load(f)

    rows, by_pattern, n_sources = extract(bundle)
    log(f'\nScanned {n_sources:,} text sources, captured {len(rows):,} matches across '
        f'{len(by_pattern)} patterns:')
    for name, n in by_pattern.most_common():
        log(f'  {name:<28} {n:>6,}')

    write_csv(rows, out_path)
    log(f'\nWrote {out_path}')

    # Per-patient summary as a quick sanity check
    by_patient = Counter(r['patient_id'] for r in rows)
    log(f'\nPatients with at least one match: {len(by_patient)}')
    if by_patient:
        log('Top 10 patients by match count:')
        for pid, n in by_patient.most_common(10):
            log(f'  {pid}: {n}')

    return rows


if __name__ == '__main__':
    main()
