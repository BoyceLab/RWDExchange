"""
Device & equipment extraction module
====================================
Companion to note_extraction.py and phenopackets_etl.py. Walks the bundle
(dashboard_data.json) for mentions of medical devices and durable medical
equipment (DME), drawing on both structured codes and free-text narratives.

Output: two CSVs sitting side-by-side, with the same patient_id key so they
can be joined.

  device_codes.csv         Every record in the bundle whose (vocabulary,
                            code) pair matches a known equipment code from
                            HCPCS Level II, SNOMED CT (procedure side), or
                            CPT-4. One row per match.

  device_extractions.csv   Every regex match in CCDA section narratives
                            and decoded note text. One row per match, with
                            a +/- 60-char snippet for spot-review.

Coverage areas (relevant to ALS specifically, useful broadly):
  - AAC and speech-generating devices (SGDs): HCPCS E2500-E2512 +
    CPT 92605-92609 + 97755 + brand names (Tobii, Dynavox, PRC,
    TouchChat, Proloquo, eye-gaze, Eyegaze)
  - Wheelchairs (manual): HCPCS E1130-E1298, K0001-K0009
  - Power wheelchairs: HCPCS K0813-K0890, E1230 + tilt-in-space mentions
  - Standers and standing frames: HCPCS E0637, E0638, E0642
  - Mobility aids: walkers, canes, crutches, rollators
  - Transfer aids: Hoyer lifts, ceiling lifts, transfer boards, gait belts
  - Bath/toileting: shower chairs, commodes, raised toilet seats, grab bars
  - Hospital beds and pressure mattresses
  - Respiratory equipment (very high relevance for ALS):
    BiPAP/NIV (E0470, E0471), home ventilators (E0464, E0465, E0466),
    cough-assist / MI-E (E0482), HFCWO Vest (E0483), suction (E0600),
    oxygen, plus brand names Trilogy/Astral/Vivo/CoughAssist/Vest
  - Feeding equipment: PEG/G-tube mentions, enteral feeding pumps (B9002,
    B4034-B4036)
  - Equipment referrals: DME orders, OT/PT/SLP consults specifically for
    equipment (wheelchair fit, AAC eval, seating)

Operates without dependencies beyond the standard library. Reads
dashboard_data.json. Writes the two CSVs alongside it. Mirrors the
file-naming, encoding, and run-log conventions of note_extraction.py
for consistency.
"""

import os
import re
import csv
import json
import datetime as _dt
from collections import Counter, defaultdict


# ============================================================================
# STRUCTURED EQUIPMENT CODES
# ============================================================================
# Format: (vocabulary_id, code) -> (display_name, category)
# Vocabulary IDs match what the rest of Registry Forge emits in the bundle.
# Keep the list focused on actionable equipment; supplies that aren't the
# device itself (catheters, dressings) are out of scope.

DEVICE_CODES = {
    # === AAC / Speech Generating Devices ===
    ('HCPCS', 'E2500'): ('SGD, digitized speech, ≤8 min recording', 'aac_device'),
    ('HCPCS', 'E2502'): ('SGD, digitized speech, >8 ≤20 min recording', 'aac_device'),
    ('HCPCS', 'E2504'): ('SGD, digitized speech, >20 ≤40 min recording', 'aac_device'),
    ('HCPCS', 'E2506'): ('SGD, digitized speech, >40 min recording', 'aac_device'),
    ('HCPCS', 'E2508'): ('SGD, synthesized speech, spelling-based', 'aac_device'),
    ('HCPCS', 'E2510'): ('SGD, synthesized speech, multi-method', 'aac_device'),
    ('HCPCS', 'E2511'): ('SGD - software upgrade', 'aac_device'),
    ('HCPCS', 'E2512'): ('SGD - accessory', 'aac_device'),
    ('HCPCS', 'E1902'): ('Communication board, non-electronic', 'aac_device'),
    ('CPT-4', '92605'): ('Eval for non-SGD AAC prescription', 'aac_eval'),
    ('CPT-4', '92606'): ('Therapeutic services for non-SGD AAC', 'aac_eval'),
    ('CPT-4', '92607'): ('Eval for SGD prescription, first hour', 'aac_eval'),
    ('CPT-4', '92608'): ('Eval for SGD prescription, additional 30 min', 'aac_eval'),
    ('CPT-4', '92609'): ('Therapeutic services for SGD use', 'aac_eval'),
    ('CPT-4', '97755'): ('Assistive technology assessment', 'aac_eval'),
    ('SNOMED-CT', '462866006'): ('Speech generating device', 'aac_device'),
    ('SNOMED-CT', '17988007'):  ('Augmentative and alternative communication', 'aac_device'),
    ('SNOMED-CT', '702779005'): ('Augmentative and alternative communication device', 'aac_device'),

    # === Wheelchairs - manual ===
    ('HCPCS', 'E1130'): ('Standard wheelchair, fixed full-length arms', 'wheelchair_manual'),
    ('HCPCS', 'E1140'): ('Wheelchair, detachable arms', 'wheelchair_manual'),
    ('HCPCS', 'E1170'): ('Wheelchair, amputee, detachable arms', 'wheelchair_manual'),
    ('HCPCS', 'E1037'): ('Transport chair, pediatric', 'wheelchair_manual'),
    ('HCPCS', 'E1038'): ('Transport chair, adult', 'wheelchair_manual'),
    ('HCPCS', 'K0001'): ('Standard manual wheelchair', 'wheelchair_manual'),
    ('HCPCS', 'K0002'): ('Standard hemi (low seat) wheelchair', 'wheelchair_manual'),
    ('HCPCS', 'K0003'): ('Lightweight wheelchair', 'wheelchair_manual'),
    ('HCPCS', 'K0004'): ('High-strength lightweight wheelchair', 'wheelchair_manual'),
    ('HCPCS', 'K0005'): ('Ultra-lightweight wheelchair', 'wheelchair_manual'),
    ('HCPCS', 'K0006'): ('Heavy-duty wheelchair', 'wheelchair_manual'),
    ('HCPCS', 'K0007'): ('Extra-heavy-duty wheelchair', 'wheelchair_manual'),
    ('HCPCS', 'K0008'): ('Custom manual wheelchair', 'wheelchair_manual'),
    ('HCPCS', 'K0009'): ('Other manual wheelchair, NOC', 'wheelchair_manual'),
    ('SNOMED-CT', '466089005'): ('Manual wheelchair', 'wheelchair_manual'),

    # === Wheelchairs - power ===
    ('HCPCS', 'E1230'): ('Motorized wheelchair (3-wheel)', 'wheelchair_power'),
    ('HCPCS', 'K0813'): ('Power wheelchair, group 1, portable', 'wheelchair_power'),
    ('HCPCS', 'K0814'): ('Power wheelchair, group 1, captain chair', 'wheelchair_power'),
    ('HCPCS', 'K0822'): ('Power wheelchair, group 2, captain chair', 'wheelchair_power'),
    ('HCPCS', 'K0848'): ('Power wheelchair, group 3 standard', 'wheelchair_power'),
    ('HCPCS', 'K0856'): ('Power wheelchair, group 3 single-power', 'wheelchair_power'),
    ('HCPCS', 'K0861'): ('Power wheelchair, group 3 multi-power', 'wheelchair_power'),
    ('HCPCS', 'K0890'): ('Power wheelchair, group 4 standard', 'wheelchair_power'),
    ('CPT-4', '97542'): ('Wheelchair management/training', 'wheelchair_eval'),
    ('SNOMED-CT', '466088002'): ('Powered wheelchair', 'wheelchair_power'),

    # === Standers ===
    ('HCPCS', 'E0637'): ('Combination sit-to-stand frame', 'stander'),
    ('HCPCS', 'E0638'): ('Standing frame', 'stander'),
    ('HCPCS', 'E0642'): ('Standing frame, mobile', 'stander'),

    # === Mobility aids ===
    ('HCPCS', 'E0100'): ('Cane', 'mobility_aid'),
    ('HCPCS', 'E0105'): ('Cane, quad or 3-prong', 'mobility_aid'),
    ('HCPCS', 'E0110'): ('Crutches, forearm', 'mobility_aid'),
    ('HCPCS', 'E0114'): ('Crutches, underarm', 'mobility_aid'),
    ('HCPCS', 'E0130'): ('Walker, rigid', 'mobility_aid'),
    ('HCPCS', 'E0135'): ('Walker, folding', 'mobility_aid'),
    ('HCPCS', 'E0143'): ('Walker, folding wheeled', 'mobility_aid'),
    ('HCPCS', 'E0144'): ('Walker, enclosed four-sided', 'mobility_aid'),
    ('HCPCS', 'E0147'): ('Heavy-duty multiple-braking walker', 'mobility_aid'),
    ('HCPCS', 'E0148'): ('Walker, heavy duty, no wheels', 'mobility_aid'),
    ('SNOMED-CT', '40847000'):  ('Walker', 'mobility_aid'),
    ('SNOMED-CT', '116167008'): ('Cane', 'mobility_aid'),
    ('SNOMED-CT', '37148003'):  ('Crutch', 'mobility_aid'),

    # === Transfer aids ===
    ('HCPCS', 'E0635'): ('Patient lift, electric', 'transfer_aid'),
    ('HCPCS', 'E0639'): ('Patient lift, mobile', 'transfer_aid'),
    ('HCPCS', 'E0641'): ('Multi-positional patient transfer system', 'transfer_aid'),

    # === Bath / toileting safety ===
    ('HCPCS', 'E0163'): ('Commode chair, mobile or stationary', 'bath_safety'),
    ('HCPCS', 'E0240'): ('Bath/shower chair', 'bath_safety'),
    ('HCPCS', 'E0241'): ('Bath tub wall rail', 'bath_safety'),
    ('HCPCS', 'E0244'): ('Raised toilet seat', 'bath_safety'),
    ('HCPCS', 'E0247'): ('Transfer bench for tub or toilet', 'bath_safety'),

    # === Beds and pressure mattresses ===
    ('HCPCS', 'E0181'): ('Pressure-reducing mattress, alternating', 'bed_mattress'),
    ('HCPCS', 'E0260'): ('Hospital bed, semi-electric', 'bed_mattress'),
    ('HCPCS', 'E0261'): ('Hospital bed, full-electric', 'bed_mattress'),
    ('HCPCS', 'E0277'): ('Powered pressure-reducing mattress', 'bed_mattress'),
    ('HCPCS', 'E0297'): ('Hospital bed, total electric', 'bed_mattress'),

    # === Respiratory equipment (high relevance for ALS) ===
    ('HCPCS', 'E0470'): ('BiPAP without backup rate', 'respiratory'),
    ('HCPCS', 'E0471'): ('BiPAP with backup rate (NIV)', 'respiratory'),
    ('HCPCS', 'E0472'): ('Respiratory assist device, vol vent', 'respiratory'),
    ('HCPCS', 'E0464'): ('Pressure support ventilator with vol-control', 'respiratory'),
    ('HCPCS', 'E0465'): ('Home ventilator, invasive', 'respiratory'),
    ('HCPCS', 'E0466'): ('Home ventilator, non-invasive', 'respiratory'),
    ('HCPCS', 'E0481'): ('Intrapulmonary percussive ventilation', 'respiratory'),
    ('HCPCS', 'E0482'): ('Cough-stimulating device (MI-E / CoughAssist)', 'respiratory'),
    ('HCPCS', 'E0483'): ('High frequency chest wall oscillation (Vest)', 'respiratory'),
    ('HCPCS', 'E0500'): ('IPPB machine', 'respiratory'),
    ('HCPCS', 'E0600'): ('Suction pump, home model', 'respiratory'),
    ('HCPCS', 'E1390'): ('Oxygen concentrator', 'respiratory'),
    ('HCPCS', 'E1391'): ('Oxygen concentrator, dual-port', 'respiratory'),
    ('HCPCS', 'E1392'): ('Portable oxygen concentrator', 'respiratory'),
    ('SNOMED-CT', '706180003'): ('Bilevel positive airway pressure ventilation system', 'respiratory'),
    ('SNOMED-CT', '243140006'): ('Continuous positive airway pressure ventilation', 'respiratory'),
    ('SNOMED-CT', '243144000'): ('Mechanical ventilator', 'respiratory'),
    ('SNOMED-CT', '466762001'): ('Suction machine', 'respiratory'),
    ('SNOMED-CT', '301401004'): ('Cough assist device', 'respiratory'),

    # === Feeding equipment ===
    ('HCPCS', 'B9002'): ('Enteral nutrition infusion pump', 'feeding'),
    ('HCPCS', 'B4034'): ('Enteral feeding supply kit, syringe-fed', 'feeding'),
    ('HCPCS', 'B4035'): ('Enteral feeding supply kit, pump-fed', 'feeding'),
    ('HCPCS', 'B4036'): ('Enteral feeding supply kit, gravity-fed', 'feeding'),

    # === OT/PT/Orthotic-prosthetic eval CPT codes ===
    ('CPT-4', '97760'): ('Orthotic management and training', 'orthotic_prosthetic'),
    ('CPT-4', '97761'): ('Prosthetic training', 'orthotic_prosthetic'),
    ('CPT-4', '97763'): ('Orthotic/prosthetic management/training', 'orthotic_prosthetic'),
}


# ============================================================================
# REGEX PATTERNS for unstructured (free-text) mentions
# ============================================================================
# Each entry: (pattern_name, category, compiled_regex, description)
# Word boundaries used aggressively. Brand names case-sensitive where the
# common word has a different meaning (e.g. "Vest" the device vs. "vest" the
# garment). Generic terms like "wheelchair" allowed any case.

DEVICE_PATTERNS = [
    # ---- AAC / SGDs ----
    ('aac_general', 'aac_device',
     re.compile(r'\b(?:augmentative\s*(?:and|&)\s*alternative\s*communication|AAC)(?:\s*(?:device|system))?\b', re.I),
     'Augmentative and Alternative Communication device or system'),
    ('sgd_general', 'aac_device',
     re.compile(r'\b(?:speech[-\s]?generating\s*device|SGD)s?\b', re.I),
     'Speech-generating device'),
    ('aac_brand_tobii', 'aac_device',
     re.compile(r'\bTobii(?:\s*Dynavox)?\b'),
     'Tobii / Tobii Dynavox AAC device brand'),
    ('aac_brand_dynavox', 'aac_device',
     re.compile(r'\bDynavox\b'),
     'Dynavox AAC device brand'),
    ('aac_brand_prc', 'aac_device',
     re.compile(r'\b(?:PRC|Prentke[-\s]Romich)\b'),
     'PRC / Prentke-Romich AAC device brand'),
    ('aac_brand_touchchat', 'aac_device',
     re.compile(r'\bTouchChat\b'),
     'TouchChat AAC software'),
    ('aac_brand_proloquo', 'aac_device',
     re.compile(r'\bProloquo\w*\b'),
     'Proloquo2Go / Proloquo4Text AAC software'),
    ('eye_gaze', 'aac_device',
     re.compile(r'\b(?:eye[-\s]gaze|eye[-\s]tracking|eye[-\s]controlled|gaze\s+interaction)\b', re.I),
     'Eye-gaze / eye-tracking input for AAC'),
    ('communication_board', 'aac_device',
     re.compile(r'\b(?:communication\s+board|low[-\s]tech\s+communication|letter\s+board|alphabet\s+board|spelling\s+board|ETRAN|E-?TRAN)\b', re.I),
     'Low-tech communication board'),

    # ---- Wheelchairs ----
    ('wheelchair_general', 'wheelchair',
     re.compile(r'\b(?:wheel\s*chair|w/c)\b', re.I),
     'Wheelchair (general)'),
    ('wheelchair_power', 'wheelchair_power',
     re.compile(r'\b(?:power\s*(?:wheel\s*chair|w/c)|motorized\s*(?:wheel\s*chair|w/c)|electric\s+wheel\s*chair|PWC)\b', re.I),
     'Power / motorized wheelchair'),
    ('wheelchair_manual', 'wheelchair_manual',
     re.compile(r'\bmanual\s*(?:wheel\s*chair|w/c)\b', re.I),
     'Manual wheelchair'),
    ('wheelchair_transport', 'wheelchair',
     re.compile(r'\btransport\s+chair\b', re.I),
     'Transport chair'),
    ('wheelchair_tilt_in_space', 'wheelchair',
     re.compile(r'\btilt[-\s]in[-\s]space\b', re.I),
     'Tilt-in-space wheelchair feature'),
    ('wheelchair_recline', 'wheelchair',
     re.compile(r'\b(?:wheel\s*chair|w/c)\s+(?:recline|tilt)\b', re.I),
     'Recline / tilt wheelchair feature'),
    ('wheelchair_sip_puff', 'wheelchair_power',
     re.compile(r'\bsip[-\s](?:and[-\s])?puff\b', re.I),
     'Sip-and-puff power wheelchair control'),
    ('wheelchair_head_array', 'wheelchair_power',
     re.compile(r'\bhead\s+array\b', re.I),
     'Head-array power wheelchair control'),

    # ---- Standers ----
    ('stander', 'stander',
     re.compile(r'\b(?:stand(?:er|ing\s+frame|ing\s+device)|sit[-\s]to[-\s]stand)\b', re.I),
     'Standing frame / stander / sit-to-stand'),

    # ---- Transfer aids ----
    ('hoyer_lift', 'transfer_aid',
     re.compile(r'\b(?:Hoyer(?:\s+lift)?|patient\s+lift|mechanical\s+lift|ceiling\s+lift|sit[-\s]to[-\s]stand\s+lift)\b', re.I),
     'Hoyer / mechanical / ceiling lift'),
    ('transfer_board', 'transfer_aid',
     re.compile(r'\btransfer\s+(?:board|belt|disc|sling|pole)\b', re.I),
     'Transfer board / belt / sling / pole'),
    ('gait_belt', 'transfer_aid',
     re.compile(r'\bgait\s+belt\b', re.I),
     'Gait belt'),

    # ---- Mobility aids (general) ----
    ('walker_rollator', 'mobility_aid',
     re.compile(r'\b(?:walker|rollator)\b', re.I),
     'Walker or rollator'),
    ('quad_cane', 'mobility_aid',
     re.compile(r'\bquad\s+cane\b', re.I),
     'Quad cane'),
    ('crutch', 'mobility_aid',
     re.compile(r'\bcrutch(?:es)?\b', re.I),
     'Crutch / crutches'),
    # Generic "cane" intentionally NOT included alone; too many false positives
    # (sugarcane, candy cane, surnames). Quad cane caught above.

    # ---- Bath / toileting safety ----
    ('shower_chair', 'bath_safety',
     re.compile(r'\b(?:shower\s+(?:chair|bench|seat)|tub\s+(?:bench|seat|transfer))\b', re.I),
     'Shower chair / bench / tub transfer bench'),
    ('commode', 'bath_safety',
     re.compile(r'\b(?:bedside\s+)?commode\b', re.I),
     'Bedside commode'),
    ('raised_toilet_seat', 'bath_safety',
     re.compile(r'\braised\s+toilet\s+seat\b', re.I),
     'Raised toilet seat'),
    ('grab_bar', 'bath_safety',
     re.compile(r'\bgrab\s+bar(?:s)?\b', re.I),
     'Grab bar'),

    # ---- Beds / mattresses ----
    ('hospital_bed', 'bed_mattress',
     re.compile(r'\bhospital\s+bed\b', re.I),
     'Hospital bed'),
    ('pressure_mattress', 'bed_mattress',
     re.compile(r'\b(?:alternating[-\s]pressure|low[-\s]air[-\s]loss|pressure[-\s]reducing|pressure[-\s]relieving)\s+mattress\b', re.I),
     'Pressure-reducing or alternating-pressure mattress'),

    # ---- Respiratory equipment (high relevance for ALS) ----
    ('bipap_niv', 'respiratory',
     re.compile(r'\b(?:BiPAP|BPAP|bi[-\s]?level)\b', re.I),
     'BiPAP / bi-level positive airway pressure'),
    ('cpap', 'respiratory',
     re.compile(r'\bCPAP\b'),  # case-sensitive; CPAP is always all caps
     'CPAP - continuous positive airway pressure'),
    ('niv_general', 'respiratory',
     re.compile(r'\b(?:non[-\s]invasive\s+ventilation|NIV|NPPV|non[-\s]invasive\s+positive\s+pressure\s+ventilation)\b', re.I),
     'Non-invasive ventilation (general)'),
    ('home_ventilator', 'respiratory',
     re.compile(r'\b(?:home\s+vent(?:ilator)?|tracheostomy\s+vent(?:ilator)?|invasive\s+vent(?:ilator)?|portable\s+vent(?:ilator)?)\b', re.I),
     'Home / tracheostomy / portable ventilator'),
    ('cough_assist', 'respiratory',
     re.compile(r'\b(?:cough\s+assist|MI[-\s]?E|mechanical\s+(?:insufflator[-\s]exsufflator|in[-\s]?exsufflation)|CoughAssist)\b', re.I),
     'Cough assist (mechanical insufflator-exsufflator)'),
    ('hfcwo_vest', 'respiratory',
     re.compile(r'\b(?:HFCWO|chest\s+oscillation|chest\s+(?:wall\s+)?vest|InCourage|SmartVest|AffloVest|The\s+Vest)\b'),
     'High-frequency chest wall oscillation (Vest, InCourage, AffloVest)'),
    ('suction_machine', 'respiratory',
     re.compile(r'\b(?:suction\s+(?:machine|pump|unit)|portable\s+suction|wall\s+suction|Yankauer)\b', re.I),
     'Suction machine / portable suction / Yankauer'),
    ('oxygen_concentrator', 'respiratory',
     re.compile(r'\b(?:oxygen\s+concentrator|portable\s+oxygen|home\s+oxygen|HomeFill|liquid\s+oxygen|LOX)\b', re.I),
     'Oxygen concentrator / home oxygen'),
    ('ventilator_brand_trilogy', 'respiratory',
     re.compile(r'\b(?:Trilogy(?:\s*Evo)?|Astral(?:\s*150)?|Eve\s*In|Vivo(?:\s*\d+)?|VOCSN|Luisa)\b'),
     'Home ventilator brand: Trilogy / Astral / Vivo / VOCSN / Luisa'),

    # ---- Feeding equipment ----
    ('peg_g_tube', 'feeding',
     re.compile(r'\b(?:PEG(?:\s+tube)?|gastrostomy(?:\s+tube)?|G[-\s]?tube|RIG|PRG|jejunostomy(?:\s+tube)?|J[-\s]?tube|GJ[-\s]?tube|nasogastric\s+tube|NG[-\s]?tube|feeding\s+tube)\b', re.I),
     'PEG / gastrostomy / jejunostomy / NG feeding tube'),
    ('feeding_pump', 'feeding',
     re.compile(r'\b(?:feeding\s+pump|enteral\s+pump|Kangaroo(?:\s+pump)?|Joey\s+pump|Infinity\s+pump|EnteraLite)\b', re.I),
     'Enteral feeding pump'),

    # ---- Equipment referrals & consults ----
    ('dme_referral', 'referral',
     re.compile(r'\b(?:DME\s+(?:referral|consult|order|eval(?:uation)?|prescription|RX)|durable\s+medical\s+equipment\s+(?:referral|order|prescription))\b', re.I),
     'DME referral / order / prescription'),
    ('ot_eval_equipment', 'referral',
     re.compile(r'\b(?:OT\s+(?:eval|consult|referral)|occupational\s+therap(?:y|ist))\b.{0,40}\b(?:equipment|wheelchair|seating|positioning|AAC|adaptive|home\s+safety)\b', re.I | re.S),
     'OT consult / eval for equipment'),
    ('pt_eval_equipment', 'referral',
     re.compile(r'\b(?:PT\s+(?:eval|consult|referral)|physical\s+therap(?:y|ist))\b.{0,40}\b(?:equipment|wheelchair|seating|gait|mobility|stander)\b', re.I | re.S),
     'PT consult / eval for equipment'),
    ('slp_aac_eval', 'referral',
     re.compile(r'\b(?:SLP|speech[-\s]language\s+pathologist|speech\s+therap(?:y|ist))\b.{0,40}\b(?:AAC|SGD|communication|speech\s+device)\b', re.I | re.S),
     'SLP consult / eval for AAC'),
    ('wheelchair_eval', 'referral',
     re.compile(r'\bwheel\s*chair\s+(?:eval(?:uation)?|fitting|assessment|seating\s+(?:eval|assessment)|clinic)\b', re.I),
     'Wheelchair fitting / seating clinic eval'),
    ('aac_eval', 'referral',
     re.compile(r'\b(?:AAC|SGD)\s+(?:eval(?:uation)?|assessment|trial|clinic)\b', re.I),
     'AAC / SGD eval or trial'),
    ('home_safety_eval', 'referral',
     re.compile(r'\bhome\s+safety\s+(?:eval(?:uation)?|assessment)\b', re.I),
     'Home safety evaluation'),
]


# ============================================================================
# DRIVER
# ============================================================================
LOG = []
def log(msg):
    line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line); LOG.append(line)


def iter_text_sources(bundle):
    """Yield (patient_id, source_kind, source_id, text) tuples from CCDA
    section narratives and decoded note documents in the bundle."""
    # CCDA section narratives
    for doc in bundle.get('documents', []) or []:
        pid = doc.get('patient_id') or ''
        sid = doc.get('document_id') or doc.get('id') or ''
        for sec in (doc.get('sections') or []):
            text = (sec.get('narrative') or '').strip()
            if text:
                yield (str(pid or ''), 'ccda_section',
                       f'{sid}#{sec.get("title") or sec.get("code") or "section"}',
                       text)
    # Decoded note bodies
    for note in bundle.get('notes', []) or []:
        pid = note.get('patient_id') or ''
        sid = note.get('note_id') or note.get('id') or ''
        text = (note.get('body') or note.get('text') or '').strip()
        if text:
            yield (str(pid or ''), 'note', str(sid or ''), text)
    # Diagnostic-report or other narrative-bearing categories that some
    # registries put narrative into. Best-effort; skipped silently if absent.
    for rep in bundle.get('diagnostic_reports', []) or []:
        pid = rep.get('patient_id') or ''
        sid = rep.get('report_id') or rep.get('id') or ''
        text = (rep.get('narrative') or rep.get('text') or '').strip()
        if text:
            yield (str(pid or ''), 'diagnostic_report', str(sid or ''), text)


def iter_coded_records(bundle):
    """Yield (patient_id, source_kind, source_id, record) tuples for every
    coded record in the bundle. source_kind is the bundle category name."""
    categories = ('medications','procedures','immunizations','problems',
                  'labs_vitals','observations','allergies','care_plans',
                  'goals','encounters','diagnostic_reports')
    for cat in categories:
        for rec in bundle.get(cat, []) or []:
            pid = rec.get('patient_id') or ''
            sid = rec.get('id') or rec.get('record_id') or ''
            yield (str(pid or ''), cat, str(sid or ''), rec)


def find_codes(bundle):
    """Walk every coded record in the bundle and emit a row for each match
    against DEVICE_CODES."""
    rows = []
    for pid, source_kind, source_id, rec in iter_coded_records(bundle):
        # Primary coding
        primary_vocab = rec.get('code_system') or ''
        primary_code  = rec.get('code') or ''
        candidates = [(primary_vocab, primary_code)]
        # Alternate codings if preserved on the record
        for c in (rec.get('all_codings') or []) or []:
            candidates.append((c.get('system') or c.get('code_system') or '',
                                c.get('code') or ''))
        seen = set()
        for vocab, code in candidates:
            if not code or (vocab, code) in seen: continue
            seen.add((vocab, code))
            hit = DEVICE_CODES.get((vocab, code))
            if not hit: continue
            display, category = hit
            rows.append({
                'patient_id': pid,
                'source_kind': source_kind,
                'source_id': source_id,
                'code_system': vocab,
                'code': code,
                'recorded_display_name': rec.get('display_name') or '',
                'forge_label': display,
                'category': category,
                'effective_date': rec.get('effective_date') or rec.get('start_date') or '',
                'status': rec.get('status') or '',
            })
    return rows


def find_extractions(bundle):
    """Walk every text source in the bundle and emit a row for each regex
    match against DEVICE_PATTERNS, with a ±60-character snippet."""
    rows = []
    for pid, source_kind, source_id, text in iter_text_sources(bundle):
        for pattern_name, category, regex, description in DEVICE_PATTERNS:
            for m in regex.finditer(text):
                start = max(0, m.start() - 60)
                end   = min(len(text), m.end() + 60)
                snippet = text[start:end].replace('\n', ' ').replace('\r', ' ')
                snippet = re.sub(r'\s+', ' ', snippet).strip()
                rows.append({
                    'patient_id': pid,
                    'source_kind': source_kind,
                    'source_id': source_id,
                    'pattern': pattern_name,
                    'category': category,
                    'value': m.group(0),
                    'snippet': snippet,
                    'char_offset': m.start(),
                    'description': description,
                })
    return rows


def write_csv(path, rows, fieldnames):
    """Write rows to CSV with UTF-8 BOM (Excel-friendly), all cells quoted,
    sorted by patient_id then source_kind then char_offset where available."""
    def sortkey(r):
        return (str(r.get('patient_id') or ''),
                str(r.get('source_kind') or ''),
                int(r.get('char_offset') or 0),
                str(r.get('pattern') or r.get('code') or ''))
    rows = sorted(rows, key=sortkey)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows: w.writerow(r)


def main(bundle_path='./dashboard_data.json',
         out_root='./'):
    log('=' * 72)
    log('Device & equipment extraction -- starting')
    log('=' * 72)
    log(f'Bundle: {bundle_path}')
    log(f'Output dir: {out_root}')

    bundle = json.load(open(bundle_path))
    log(f'Patients in bundle: {len(bundle.get("patients", []))}')
    log(f'Coded records to scan: '
        f'{sum(len(bundle.get(c, []) or []) for c in ("medications","procedures","immunizations","problems","labs_vitals","observations","care_plans","goals","encounters"))}')

    # Structured codes
    code_rows = find_codes(bundle)
    code_path = os.path.join(out_root, 'device_codes.csv')
    write_csv(code_path, code_rows,
              fieldnames=['patient_id','source_kind','source_id','code_system','code',
                          'recorded_display_name','forge_label','category',
                          'effective_date','status'])

    # Unstructured regex
    ext_rows = find_extractions(bundle)
    ext_path = os.path.join(out_root, 'device_extractions.csv')
    write_csv(ext_path, ext_rows,
              fieldnames=['patient_id','source_kind','source_id','pattern','category',
                          'value','snippet','char_offset','description'])

    # Per-category summary
    cat_struct = Counter(r['category'] for r in code_rows)
    cat_text   = Counter(r['category'] for r in ext_rows)
    log('')
    log(f'device_codes.csv:       {len(code_rows):,} rows')
    if code_rows:
        for c, n in cat_struct.most_common():
            log(f'    {c:<24}{n:>5}')
    log(f'device_extractions.csv: {len(ext_rows):,} rows')
    if ext_rows:
        for c, n in cat_text.most_common():
            log(f'    {c:<24}{n:>5}')

    return {
        'device_codes_csv':       code_path,
        'device_extractions_csv': ext_path,
        'code_rows':       len(code_rows),
        'extraction_rows': len(ext_rows),
    }


if __name__ == '__main__':
    main()
