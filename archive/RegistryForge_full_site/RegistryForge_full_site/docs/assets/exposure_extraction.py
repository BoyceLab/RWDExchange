"""
exposure_extraction.py — Environmental, occupational, and toxic exposure
extraction from EHR bundles, with mappings to ECTO (Environmental
Conditions, Treatments, and Exposures Ontology) categories.

Reads a dashboard_data.json bundle and walks:
  - structured coded records (ICD-10-CM Z57.x occupational exposure,
    Z77.x environmental/substance contact, F17.x nicotine dependence,
    Z87.891 personal history of smoking, etc.)
  - free-text narrative (notes[].narrative_text and
    documents[].plain_text) against regex patterns for ALS-relevant
    exposure categories (military service, smoking, pesticides, heavy
    metals, solvents, head trauma, asbestos, EMF, cyanotoxins, etc.)

Writes two CSVs:
  - exposure_codes.csv       structured matches
  - exposure_extractions.csv regex matches in narrative text

Both are intended to be consumed by exposure_dashboard.py for visual
review and by downstream Phenopackets generation. ECTO category
mappings are kept in ECTO_CATEGORY_MAP; some ECTO term IDs are
included where the mapping is widely-used and stable, but adopters
should verify against the current ECTO release (purl.obolibrary.org/
obo/ecto.owl) and extend as needed for their use case.

Privacy: extraction is to raw CSVs; pseudonymization is applied by
exposure_dashboard.py at the rendering layer, matching the same
posture device_dashboard.py uses.
"""

import csv
import json
import os
import re
import sys
import datetime as _dt
from collections import Counter, defaultdict


# ===========================================================================
# 1. STRUCTURED EXPOSURE CODES  (ICD-10-CM)
# ===========================================================================
# Each entry: (vocab, code) -> (friendly_label, category)
# Categories are aligned with ECTO_CATEGORY_MAP below so coded + text
# matches roll up the same way in the dashboard.
EXPOSURE_CODES = {
    # === Smoking / tobacco ===
    ('ICD-10-CM', 'Z87.891'): ('Personal history of nicotine dependence',     'smoking'),
    ('ICD-10-CM', 'F17.200'): ('Nicotine dependence, unspecified',            'smoking'),
    ('ICD-10-CM', 'F17.201'): ('Nicotine dependence, unspecified, in remission','smoking'),
    ('ICD-10-CM', 'F17.210'): ('Nicotine dependence, cigarettes',             'smoking'),
    ('ICD-10-CM', 'F17.211'): ('Nicotine dependence, cigarettes, in remission','smoking'),
    ('ICD-10-CM', 'F17.220'): ('Nicotine dependence, chewing tobacco',        'smoking'),
    ('ICD-10-CM', 'F17.290'): ('Nicotine dependence, other tobacco product',  'smoking'),
    ('ICD-10-CM', 'Z57.31'):  ('Occupational exposure to environmental tobacco smoke', 'smoking'),
    ('ICD-10-CM', 'Z77.22'):  ('Contact with and (suspected) exposure to environmental tobacco smoke (acute) (chronic)', 'smoking'),

    # === Occupational exposure to risk factors (Z57.x family) ===
    ('ICD-10-CM', 'Z57.0'):  ('Occupational exposure to noise',               'occupational_noise'),
    ('ICD-10-CM', 'Z57.1'):  ('Occupational exposure to radiation',           'radiation'),
    ('ICD-10-CM', 'Z57.2'):  ('Occupational exposure to dust',                'occupational_dust'),
    ('ICD-10-CM', 'Z57.3'):  ('Occupational exposure to other air contaminants', 'occupational_dust'),
    ('ICD-10-CM', 'Z57.39'): ('Occupational exposure to other air contaminants','occupational_dust'),
    ('ICD-10-CM', 'Z57.4'):  ('Occupational exposure to toxic agents in agriculture', 'pesticides_agriculture'),
    ('ICD-10-CM', 'Z57.5'):  ('Occupational exposure to toxic agents in other industries', 'solvents_industrial'),
    ('ICD-10-CM', 'Z57.6'):  ('Occupational exposure to extreme temperature', 'occupational_other'),
    ('ICD-10-CM', 'Z57.7'):  ('Occupational exposure to vibration',           'occupational_other'),
    ('ICD-10-CM', 'Z57.8'):  ('Occupational exposure to other risk factors',  'occupational_other'),
    ('ICD-10-CM', 'Z57.9'):  ('Occupational exposure to unspecified risk factor','occupational_other'),

    # === Contact with hazardous substances (Z77.0xx — heavy metals & chemicals) ===
    ('ICD-10-CM', 'Z77.011'): ('Contact with and exposure to lead',           'heavy_metals'),
    ('ICD-10-CM', 'Z77.012'): ('Contact with and exposure to uranium',        'radiation'),
    ('ICD-10-CM', 'Z77.018'): ('Contact with and exposure to other hazardous metals', 'heavy_metals'),
    ('ICD-10-CM', 'Z77.020'): ('Contact with and exposure to arsenic',        'heavy_metals'),
    ('ICD-10-CM', 'Z77.021'): ('Contact with and exposure to aromatic amines','solvents_industrial'),
    ('ICD-10-CM', 'Z77.028'): ('Contact with and exposure to other hazardous aromatic compounds', 'solvents_industrial'),
    ('ICD-10-CM', 'Z77.090'): ('Contact with and exposure to asbestos',       'asbestos'),
    ('ICD-10-CM', 'Z77.098'): ('Contact with and exposure to other hazardous chemicals', 'solvents_industrial'),

    # === Environmental pollution (Z77.1xx) ===
    ('ICD-10-CM', 'Z77.110'): ('Contact with and exposure to air pollution',  'air_pollution'),
    ('ICD-10-CM', 'Z77.111'): ('Contact with and exposure to water pollution','water_pollution'),
    ('ICD-10-CM', 'Z77.112'): ('Contact with and exposure to soil pollution', 'soil_pollution'),
    ('ICD-10-CM', 'Z77.118'): ('Contact with and exposure to other environmental pollution', 'air_pollution'),

    # === Physical environment hazards (Z77.12x) ===
    ('ICD-10-CM', 'Z77.121'): ('Contact with and exposure to noise',          'occupational_noise'),
    ('ICD-10-CM', 'Z77.122'): ('Contact with and exposure to mold',           'biological_mold'),
    ('ICD-10-CM', 'Z77.123'): ('Contact with and exposure to harmful algae and algae toxins', 'cyanotoxins'),
    ('ICD-10-CM', 'Z77.128'): ('Contact with and exposure to other hazards in the physical environment', 'environmental_other'),

    # === Other hazards (Z77.2x, Z77.9) ===
    ('ICD-10-CM', 'Z77.21'):  ('Contact with potentially hazardous body fluids', 'biological_other'),
    ('ICD-10-CM', 'Z77.29'):  ('Contact with other hazardous substances',     'environmental_other'),
    ('ICD-10-CM', 'Z77.9'):   ('Other contact with and exposure to hazards to health', 'environmental_other'),

    # === Military / veteran-related history ===
    # ICD-10-CM doesn't have a single "veteran" code; closest structured signals
    # are Z91.82 personal history of military deployment, and family-history Z82 codes.
    ('ICD-10-CM', 'Z91.82'):  ('Personal history of military deployment',     'military_service'),
    ('ICD-10-CM', 'Z56.82'):  ('Military deployment status',                  'military_service'),
    ('ICD-10-CM', 'Z65.8'):   ('Other specified problems related to psychosocial circumstances', 'other_psychosocial'),

    # === Head/neck trauma history ===
    ('ICD-10-CM', 'Z87.820'): ('Personal history of traumatic brain injury',  'head_trauma'),
    ('ICD-10-CM', 'S06.0X0A'): ('Concussion without loss of consciousness, initial encounter', 'head_trauma'),
    ('ICD-10-CM', 'S06.9X0A'): ('Unspecified intracranial injury without LOC, initial encounter', 'head_trauma'),
}


# ===========================================================================
# 2. REGEX PATTERNS for unstructured narrative text
# ===========================================================================
# Each entry: (pattern_name, category, compiled_regex, description, ecto_seed)
# `ecto_seed` is a known-or-best-guess ECTO term ID (see ECTO_CATEGORY_MAP
# below). Verified terms are noted explicitly; others are starting points
# adopters should validate against the current ECTO release.
EXPOSURE_PATTERNS = [
    # ----- Smoking / tobacco -----
    ('smoking_status_current',  'smoking',
     re.compile(r'\b(?:current(?:ly)?\s+smok(?:er|ing|es)|active\s+smok(?:er|ing)|smokes\s+(?:daily|currently|cigarettes))\b', re.IGNORECASE),
     'Current tobacco smoker',
     'ECTO:9000250'),
    ('smoking_status_former',   'smoking',
     re.compile(r'\b(?:former\s+smok(?:er|ing)|ex[-\s]?smok(?:er|ing)|quit\s+smoking|stopped\s+smoking|previously\s+smoked|history\s+of\s+(?:cigarette\s+)?smoking)\b', re.IGNORECASE),
     'Former tobacco smoker',
     'ECTO:9000250'),
    ('pack_years',              'smoking',
     re.compile(r'\b\d+\s*(?:\.\d+)?\s*pack[-\s]?years?\b', re.IGNORECASE),
     'Pack-years quantification',
     'ECTO:9000250'),
    ('tobacco_chewing',         'smoking',
     re.compile(
         # Phrases that don't have the template-bloat problem
         r'\b(?:chew(?:ing|s|ed)\s+tobacco'
         r'|smokeless\s+tobacco'
         r'|dip(?:ping|s|ped)?\s+tobacco'
         # "snuff" alone matches Epic/Cerner social-history template fields
         # that list "snuff" as a checkbox in every encounter note, vastly
         # overstating actual use. Require positive endorsement context.
         r'|(?:uses?|using|used|history\s+of|active|current(?:ly)?|chronic)\s+snuff'
         r'|snuff\s+(?:use|user|users|addiction|habit|chewer))'
         r'\b', re.IGNORECASE),
     'Smokeless tobacco use',
     'ECTO:9000250'),

    # ----- Military service -----
    ('military_service_general', 'military_service',
     re.compile(r'\b(?:military\s+(?:service|veteran|deployment)|veteran\s+of|served\s+in\s+(?:the\s+)?(?:army|navy|marines|air\s+force|military)|active\s+duty)\b', re.IGNORECASE),
     'Military service mention',
     None),
    ('military_gulf_war',        'military_service',
     re.compile(r'\b(?:Gulf\s+War|Operation\s+Desert\s+(?:Storm|Shield)|OEF|OIF|OND|Iraq\s+War|Afghanistan\s+(?:War|veteran)|deployment\s+to\s+(?:Iraq|Afghanistan|Kuwait))\b', re.IGNORECASE),
     'Gulf War / OIF / OEF / OND deployment',
     None),
    ('military_vietnam',         'military_service',
     re.compile(r'\b(?:Vietnam\s+(?:War|veteran|era)|Agent\s+Orange|Operation\s+Ranch\s+Hand)\b', re.IGNORECASE),
     'Vietnam War / Agent Orange exposure',
     None),
    ('camp_lejeune',             'military_service',
     re.compile(r'\b(?:Camp\s+Lejeune|Marine\s+Corps\s+Base\s+Camp\s+Lejeune)\b', re.IGNORECASE),
     'Camp Lejeune water contamination (TCE/PCE 1953-1987)',
     None),

    # ----- Pesticides / herbicides / agricultural chemicals -----
    ('pesticide_general',       'pesticides_agriculture',
     re.compile(r'\b(?:pesticide|insecticide|herbicide|fungicide|rodenticide)s?\b', re.IGNORECASE),
     'Pesticide / herbicide / insecticide exposure',
     None),
    ('pesticide_organophosphate', 'pesticides_agriculture',
     re.compile(r'\b(?:organophosphate|malathion|chlorpyrifos|parathion|diazinon)\b', re.IGNORECASE),
     'Organophosphate pesticide exposure',
     None),
    ('pesticide_paraquat',       'pesticides_agriculture',
     re.compile(r'\b(?:paraquat|Gramoxone)\b', re.IGNORECASE),
     'Paraquat herbicide exposure',
     None),
    ('pesticide_glyphosate',     'pesticides_agriculture',
     re.compile(r'\b(?:glyphosate|Roundup)\b', re.IGNORECASE),
     'Glyphosate exposure',
     None),
    ('pesticide_DDT',            'pesticides_agriculture',
     re.compile(r'\bDDT\b'),
     'DDT exposure',
     None),
    ('farming_occupation',       'pesticides_agriculture',
     re.compile(r'\b(?:farmer|farming|farmhand|agricultural\s+work(?:er)?|grain\s+farmer|crop\s+duster|orchard\s+work)\b', re.IGNORECASE),
     'Agricultural occupation',
     None),

    # ----- Heavy metals -----
    ('lead_exposure',            'heavy_metals',
     re.compile(r'\b(?:lead\s+(?:exposure|poisoning|toxicity)|elevated\s+(?:blood\s+)?lead|chronic\s+lead)\b', re.IGNORECASE),
     'Lead exposure / toxicity',
     'ECTO:9000945'),
    ('mercury_exposure',         'heavy_metals',
     re.compile(r'\b(?:mercury\s+(?:exposure|poisoning|toxicity)|amalgam(?:s)?\s+(?:exposure|toxic)|fish\s+mercury)\b', re.IGNORECASE),
     'Mercury exposure',
     'ECTO:0001571'),
    ('manganese_exposure',       'heavy_metals',
     re.compile(r'\b(?:manganese\s+(?:exposure|poisoning|toxicity)|welding\s+fumes?|MMT\s+exposure)\b', re.IGNORECASE),
     'Manganese exposure (incl. welding fumes)',
     None),
    ('arsenic_exposure',         'heavy_metals',
     re.compile(r'\b(?:arsenic\s+(?:exposure|poisoning|toxicity)|arsenic\s+in\s+(?:well\s+)?water)\b', re.IGNORECASE),
     'Arsenic exposure',
     'ECTO:9000032'),
    ('cadmium_exposure',         'heavy_metals',
     re.compile(r'\b(?:cadmium\s+(?:exposure|poisoning|toxicity))\b', re.IGNORECASE),
     'Cadmium exposure',
     None),
    ('heavy_metal_general',      'heavy_metals',
     re.compile(r'\b(?:heavy\s+metal(?:s)?\s+(?:exposure|toxicity|poisoning))\b', re.IGNORECASE),
     'Heavy-metal exposure (unspecified)',
     None),

    # ----- Solvents / industrial chemicals -----
    ('tce_pce',                 'solvents_industrial',
     re.compile(
         # Full names always match
         r'\b(?:trichloroethylene|perchloroethylene|tetrachloroethylene'
         # TCE only with explicit exposure / contamination / occupational
         # context to avoid colliding with clinical-trial abbreviations
         r'|TCE\s+(?:exposure|exposed|contamination|water|solvent|chemical|spill)'
         r'|(?:exposure|exposed|contamination|contaminated)\s+(?:to|with|from)\s+TCE'
         r'|occupational\s+TCE'
         r'|\(TCE\)'                            # appositive: "trichloroethylene (TCE)"
         # PCE / PERC must include "exposure" — bare "PERC" is the
         # Pulmonary Embolism Rule-out Criteria in clinical notes.
         r'|PCE\s+exposure'
         r'|exposure\s+to\s+P[CE]RC?'
         r')\b'),
     'TCE / PCE chlorinated solvent exposure',
     None),
    ('benzene',                 'solvents_industrial',
     re.compile(r'\bbenzene\s+(?:exposure|toxicity)\b|\boccupational\s+benzene\b', re.IGNORECASE),
     'Benzene exposure',
     None),
    ('formaldehyde',            'solvents_industrial',
     re.compile(r'\bformaldehyde\s+(?:exposure|toxicity)\b', re.IGNORECASE),
     'Formaldehyde exposure',
     None),
    ('solvent_general',         'solvents_industrial',
     re.compile(r'\b(?:organic\s+solvent(?:s)?|degreaser(?:s)?|paint\s+stripper|industrial\s+chemical)s?\s+(?:exposure)?\b', re.IGNORECASE),
     'Industrial / organic solvent exposure (unspecified)',
     None),
    ('welding',                 'solvents_industrial',
     re.compile(r'\b(?:welder|welding|MIG\s+welding|TIG\s+welding|arc\s+welder)\b', re.IGNORECASE),
     'Welding occupation / fume exposure',
     None),

    # ----- Asbestos / fibers -----
    ('asbestos',                'asbestos',
     re.compile(r'\b(?:asbestos\s+(?:exposure|fiber|fibers)|mesothelioma\s+(?:exposure|risk)|asbestos[-\s]containing\s+material)\b', re.IGNORECASE),
     'Asbestos exposure',
     'ECTO:9000033'),

    # ----- Head trauma / repetitive physical activity -----
    ('tbi_history',             'head_trauma',
     re.compile(r'\b(?:traumatic\s+brain\s+injury|TBI|history\s+of\s+(?:multiple\s+)?concussion(?:s)?|head\s+trauma|repeated\s+concussion|CTE\s+risk|chronic\s+traumatic\s+encephalopathy)\b', re.IGNORECASE),
     'TBI / concussion history',
     None),
    ('contact_sport_football',  'head_trauma',
     re.compile(
         r'\b(?:'
         r'played\s+football'
         r'|football\s+player'
         r'|college\s+football'
         r'|high\s+school\s+football'
         r'|football\s+(?:career|years|history|injury)'
         # NFL only when it appears with unambiguous football context;
         # bare "NFL" in an ALS cohort is overwhelmingly neurofilament light
         # (a serum biomarker for motor neuron disease activity).
         r'|(?:played|playing|in\s+the)\s+NFL\b'
         r'|NFL\s+(?:player|career|team|combine|draft|veteran)'
         r')\b', re.IGNORECASE),
     'Football history',
     None),
    ('contact_sport_other',     'head_trauma',
     re.compile(r'\b(?:professional\s+(?:soccer|hockey|rugby|boxing|MMA)\s+player|boxer|boxing\s+history|rugby\s+player|hockey\s+player|martial\s+arts\s+history)\b', re.IGNORECASE),
     'Other contact sport history',
     None),
    ('military_blast',          'head_trauma',
     re.compile(r'\b(?:blast\s+(?:injury|exposure)|IED\s+(?:exposure|blast)|concussive\s+blast)\b', re.IGNORECASE),
     'Blast-related head injury',
     None),

    # ----- Electromagnetic fields / electrical work -----
    ('emf_electrical',          'emf_radiation',
     re.compile(r'\b(?:electrical\s+(?:worker|lineman|engineer)|electrician(?:s)?|electric(?:al)?\s+utility\s+worker|EMF\s+exposure|electromagnetic\s+field)\b', re.IGNORECASE),
     'Electromagnetic field / electrical occupational exposure',
     None),

    # ----- Cyanotoxins / harmful algae / BMAA -----
    ('cyanotoxin',              'cyanotoxins',
     re.compile(r'\b(?:BMAA|beta[-\s]?methylamino[-\s]?L?[-\s]?alanine|cyanobacteria(?:l)?|blue[-\s]?green\s+algae|harmful\s+algal\s+bloom|cyanotoxin)\b', re.IGNORECASE),
     'Cyanotoxin / BMAA exposure',
     None),

    # ----- Mold / biological hazards -----
    ('mold_exposure',           'biological_mold',
     re.compile(r'\b(?:mold\s+(?:exposure|toxicity)|black\s+mold|toxic\s+mold|stachybotrys|mycotoxin)\b', re.IGNORECASE),
     'Mold / mycotoxin exposure',
     None),

    # ----- Air pollution -----
    ('air_pollution',           'air_pollution',
     re.compile(r'\b(?:air\s+pollution\s+exposure|PM2\.?5|PM10|particulate\s+matter\s+exposure|diesel\s+exhaust|traffic[-\s]related\s+air\s+pollution)\b', re.IGNORECASE),
     'Air pollution / particulate exposure',
     'ECTO:0000977'),

    # ----- Occupational dust -----
    ('dust_general',            'occupational_dust',
     re.compile(r'\b(?:silica\s+dust|coal\s+dust|wood\s+dust|grain\s+dust|cotton\s+dust|construction\s+dust|silicosis|coal\s+workers\s+pneumoconiosis)\b', re.IGNORECASE),
     'Occupational dust exposure',
     None),
]


# ===========================================================================
# 3. ECTO CATEGORY MAPPING
# ===========================================================================
# Each category is mapped to its closest representative ECTO term. Where the
# ID has been verified against the published ECTO release (the SSSOM mapping
# file at github.com/EnvironmentOntology/environmental-exposure-ontology/
# blob/master/mappings/ecto.sssom.tsv), `curation_status` is 'verified'.
# Where it has not, `curation_status` is 'pending' and `ecto_id` is None —
# we deliberately do NOT display a guessed ID, because guessed IDs would
# mislead downstream consumers. Adopters extending this module to a new
# study should:
#
#   1. Open the OLS browser at https://www.ebi.ac.uk/ols/ontologies/ecto
#      and search for the closest term to each category.
#   2. Replace `ecto_id`: None / `curation_status`: 'pending' with the
#      real term and set `curation_status` to 'verified'.
#   3. Add more-specific child terms when the agent or route is known
#      (e.g. ECTO:9000945 "exposure to lead" is more specific than the
#      heavy_metals parent).
#
# The patterns in EXPOSURE_PATTERNS carry their own `ecto_seed` field
# which gives a more specific term per substance where one is known.
ECTO_CATEGORY_MAP = {
    'smoking': {
        'ecto_id':         'ECTO:9000250',
        'ecto_label':      'exposure to nicotine',
        'curation_status': 'verified',
        'note':            'For more specific encoding, consider exposure to cigarette smoke, tobacco smoke, or second-hand smoke separately.',
    },
    'military_service': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'ECTO does not have a "military service" parent term; the operational guidance is to encode specific sub-exposures separately (Agent Orange — herbicide; Camp Lejeune — TCE/PCE in drinking water; blast injury — see head_trauma).',
    },
    'pesticides_agriculture': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify the parent term at OLS (search "exposure to pesticide"). Specific child terms exist for individual agents — paraquat, glyphosate, organophosphate, DDT, etc. — prefer the most specific term when the agent is known.',
    },
    'heavy_metals': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'No verified parent term yet. Verified child terms: ECTO:9000945 lead, ECTO:0001571 mercury, ECTO:9000032 arsenic. The individual EXPOSURE_PATTERNS carry these specific IDs in ecto_seed.',
    },
    'solvents_industrial': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify parent term at OLS (search "exposure to organic solvent"). Specific child terms exist for benzene, formaldehyde, TCE/PCE, etc.',
    },
    'asbestos': {
        'ecto_id':         'ECTO:9000033',
        'ecto_label':      'exposure to asbestos',
        'curation_status': 'verified',
        'note':            '',
    },
    'head_trauma': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Head trauma is a physical / mechanical exposure rather than a chemical exposure; ECTO coverage for blast/impact events is limited. May need OBI or another ontology for more precise encoding.',
    },
    'emf_radiation': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify at OLS — distinguish ionizing radiation (X-ray, gamma) from non-ionizing (EMF, RF, UV). Different ECTO terms.',
    },
    'cyanotoxins': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify at OLS. ECTO has chemical-level terms for specific cyanotoxins (microcystin, BMAA, anatoxin-a) that may be more appropriate than a generic cyanotoxin parent.',
    },
    'biological_mold': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify at OLS — fungal toxin / mycotoxin exposure terms.',
    },
    'air_pollution': {
        'ecto_id':         'ECTO:0000977',
        'ecto_label':      'exposure to ultrafine respirable suspended particulate matter via inhalation',
        'curation_status': 'verified',
        'note':            'This is the verified term for PM2.5/PM10 inhalation. For diesel exhaust, NO2, or other named pollutants, prefer the specific child term.',
    },
    'occupational_dust': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify at OLS — silica, coal, wood, and grain dust each have specific terms.',
    },
    'occupational_noise': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify at OLS.',
    },
    'radiation': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Distinguish ionizing (medical X-ray, gamma, alpha) vs non-ionizing (UV, RF, EMF) — these are different ECTO terms.',
    },
    'water_pollution': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify at OLS. Usually co-coded with a specific contaminant (lead in water, TCE in water, etc.).',
    },
    'soil_pollution': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'pending',
        'note':            'Verify at OLS.',
    },
    'environmental_other': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'catch_all',
        'note':            'Catch-all bucket; not a real ECTO term. Records here need re-categorization.',
    },
    'occupational_other': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'catch_all',
        'note':            'Catch-all bucket; not a real ECTO term.',
    },
    'biological_other': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'catch_all',
        'note':            'Catch-all bucket; not a real ECTO term.',
    },
    'other_psychosocial': {
        'ecto_id':         None,
        'ecto_label':      None,
        'curation_status': 'out_of_scope',
        'note':            'Psychosocial exposures are out of scope for ECTO; consider other ontologies for these (e.g. OBI, MSH).',
    },
}


# ===========================================================================
# 4. PIPELINE
# ===========================================================================
LOG = []
def log(msg):
    line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line); LOG.append(line)


# Vocabulary normalization — minimal version sufficient for ICD-10-CM
_VOCAB_NORM = {
    '2.16.840.1.113883.6.90':                       'ICD-10-CM',
    'urn:oid:2.16.840.1.113883.6.90':               'ICD-10-CM',
    'http://hl7.org/fhir/sid/icd-10-cm':            'ICD-10-CM',
    'icd10':       'ICD-10-CM',
    'icd-10':      'ICD-10-CM',
    'icd10cm':     'ICD-10-CM',
    'icd-10-cm':   'ICD-10-CM',
    'icd-10cm':    'ICD-10-CM',
}

def _normalize_vocab(s):
    if not s:
        return ''
    return _VOCAB_NORM.get(str(s).strip().lower(), str(s).strip())


def _extract_vocab(rec):
    for k in ('code_system_name', 'system_name', 'vocabulary',
              'code_system', 'code_system_uri', 'system'):
        v = rec.get(k)
        if v: return v
    return ''


def iter_coded_records(bundle):
    """Yield (patient_id, source_kind, source_id, record) for every coded
    record. Covers all the categories the ARC dashboard bundle uses."""
    categories = (
        'medications', 'procedures', 'immunizations',
        'problems', 'conditions',
        'labs_vitals', 'labs', 'vitals', 'observations',
        'allergies', 'careplans', 'care_plans', 'goals',
        'encounters', 'diagnostic_reports',
    )
    seen = set()
    for cat in categories:
        if cat in seen: continue
        seen.add(cat)
        for rec in bundle.get(cat) or []:
            yield (str(rec.get('patient_id') or ''),
                   cat,
                   str(rec.get('id') or rec.get('record_id') or ''),
                   rec)


def iter_text_sources(bundle):
    """Yield (patient_id, source_kind, source_id, text) for every
    narrative-bearing record. Field names match what note_extraction.py
    and device_extraction.py use."""
    for note in (bundle.get('notes') or []):
        text = (note.get('narrative_text') or '').strip()
        if not text: continue
        yield (str(note.get('patient_id') or ''),
               'ccda_section',
               f"{note.get('document_id') or ''}::{note.get('section_title') or ''}",
               text)
    for doc in (bundle.get('documents') or []):
        text = (doc.get('plain_text') or '').strip()
        if not text or text.startswith('[PDF -'): continue
        yield (str(doc.get('patient_id') or ''),
               f"document:{doc.get('source_format') or 'unknown'}",
               str(doc.get('document_id') or doc.get('file') or ''),
               text)


def find_codes(bundle, verbose=True):
    rows = []
    n_scanned = 0
    n_matched = 0
    cat_counter = Counter()
    for pid, source_kind, sid, rec in iter_coded_records(bundle):
        n_scanned += 1
        primary_vocab = _normalize_vocab(_extract_vocab(rec))
        primary_code  = (rec.get('code') or '').strip()
        candidates = [(primary_vocab, primary_code)]
        for c in (rec.get('all_codings') or rec.get('codings') or []):
            candidates.append((_normalize_vocab(c.get('system_name')
                                                or c.get('system')
                                                or c.get('code_system') or ''),
                                (c.get('code') or '').strip()))
        seen_local = set()
        for vocab, code in candidates:
            if not code or (vocab, code) in seen_local: continue
            seen_local.add((vocab, code))
            hit = EXPOSURE_CODES.get((vocab, code))
            if not hit: continue
            label, category = hit
            n_matched += 1
            cat_counter[category] += 1
            rows.append({
                'patient_id':            pid,
                'source_kind':           source_kind,
                'source_id':             sid,
                'code_system':           vocab,
                'code':                  code,
                'recorded_display_name': rec.get('display_name') or '',
                'forge_label':           label,
                'category':              category,
                'effective_date':        rec.get('effective_date') or rec.get('start_date') or '',
            })
    if verbose:
        log(f'Coded record scan: {n_scanned:,} records, {n_matched:,} matches, '
            f'{len(cat_counter)} categories represented')
        for cat, n in cat_counter.most_common(10):
            log(f'    {cat:<28s} {n:>6,}')
    return rows


def find_extractions(bundle, verbose=True):
    rows = []
    per_source = Counter()
    char_per_source = Counter()
    pat_hits = Counter()
    for pid, source_kind, sid, text in iter_text_sources(bundle):
        per_source[source_kind] += 1
        char_per_source[source_kind] += len(text or '')
        for pat_name, category, regex, description, ecto_seed in EXPOSURE_PATTERNS:
            for m in regex.finditer(text):
                pat_hits[pat_name] += 1
                start = max(0, m.start() - 80)
                end   = min(len(text), m.end() + 80)
                snippet = text[start:end].replace('\n', ' ').replace('\r', ' ')
                snippet = re.sub(r'\s+', ' ', snippet).strip()
                rows.append({
                    'patient_id':  pid,
                    'source_kind': source_kind,
                    'source_id':   sid,
                    'pattern':     pat_name,
                    'category':    category,
                    'value':       m.group(0),
                    'snippet':     snippet,
                    'char_offset': m.start(),
                    'description': description,
                    'ecto_seed':   ecto_seed,
                })
    if verbose:
        log(f'Text scan: {sum(per_source.values()):,} text blocks, '
            f'{sum(char_per_source.values()):,} chars, '
            f'{sum(pat_hits.values()):,} pattern matches')
        for pat, n in pat_hits.most_common(15):
            log(f'    {pat:<32s} {n:>6,}')
    return rows


def main(bundle_path='./dashboard_data.json',
         out_root='./'):
    log('=' * 72)
    log('Exposure (env / toxic / occupational) extraction -- starting')
    log('=' * 72)
    log(f'Bundle: {bundle_path}')
    log(f'Output dir: {out_root}')

    with open(bundle_path) as f:
        bundle = json.load(f)
    log(f'Patients in bundle: {len(bundle.get("patients", []))}')

    code_rows = find_codes(bundle)
    code_path = os.path.join(out_root, 'exposure_codes.csv')
    with open(code_path, 'w', newline='', encoding='utf-8-sig') as f:
        if code_rows:
            w = csv.DictWriter(f, fieldnames=list(code_rows[0].keys()),
                                quoting=csv.QUOTE_ALL)
            w.writeheader(); w.writerows(code_rows)

    ext_rows = find_extractions(bundle)
    ext_path = os.path.join(out_root, 'exposure_extractions.csv')
    with open(ext_path, 'w', newline='', encoding='utf-8-sig') as f:
        if ext_rows:
            w = csv.DictWriter(f, fieldnames=list(ext_rows[0].keys()),
                                quoting=csv.QUOTE_ALL)
            w.writeheader(); w.writerows(ext_rows)

    log('')
    log(f'exposure_codes.csv:       {len(code_rows):,} rows')
    log(f'exposure_extractions.csv: {len(ext_rows):,} rows')

    return {
        'exposure_codes_csv':       code_path,
        'exposure_extractions_csv': ext_path,
        'code_rows':                len(code_rows),
        'extraction_rows':          len(ext_rows),
    }


if __name__ == '__main__':
    main()
