# Device & equipment extraction

`device_extraction.py` walks the bundle that `run_pipeline.py` produces and finds mentions of medical devices and durable medical equipment (DME) — both **structured codes** (HCPCS Level II, SNOMED CT procedure codes, CPT-4) and **unstructured mentions** (regex against CCDA section narratives and decoded note text). It emits two CSVs that share a `patient_id` column so you can join them.

Equipment matters disproportionately for motor neuron disease and other progressive neurologic disease: speech-generating devices, BiPAP / non-invasive ventilation, cough-assist, hospital beds, power wheelchairs, feeding tubes, and all of the OT / PT / SLP referrals that come with them. Most of that information rarely lives in discrete code fields; even when DME *is* coded, registries often miss it because their pipeline focuses on diagnoses, labs, and medications rather than HCPCS Level II. This module covers both surfaces.

The module is best characterized as an **equipment-identification ontology combining billing, procedural, terminology, and NLP concepts** — not a pure code lookup and not a pure NLP extractor. Every output row makes its provenance explicit so downstream phenotyping can decide which surface to trust.

## Coverage

The module ships a hand-curated mapping table and a regex pattern set covering the equipment classes most relevant to neurodegenerative disease:

| Category | Structured codes (HCPCS / SNOMED / CPT) | Regex patterns (free text) |
|---|---|---|
| AAC / speech-generating devices | E2500–E2512, CPT 92605–92609, CPT 97755 (assistive-tech assessment) | "AAC device", "speech-generating device", "SGD", "speech device", "communication device", "Tobii Dynavox", "Prentke-Romich / PRC", "Accent", "NovaChat", "Lingraphica", "TouchChat", "Proloquo", "TD Pilot", "Grid Pad", "Smartbox", "eye-gaze", "eye-tracking", "communication board", "letter board", "ETRAN", "voice banking", "message banking", "ModelTalker", "VocaliD" |
| Wheelchairs (manual) | K0001–K0009 (canonical set; the broader E1037–E1170 range previously listed was dropped because it mixes pediatric and custom-seating items) | "wheelchair", "manual w/c", "transport chair" |
| Wheelchairs (power) | E1230, E1231–E1234, K0813–K0890, CPT 97542 | "power wheelchair", "motorized wheelchair", "PWC", "tilt-in-space", "wheelchair tilt / recline", "sip-and-puff", "head array" |
| Pressure-relief seating cushions | E2601–E2608, E2624, E2625 | (covered by "cushion" in the wheelchair context) |
| Standers | E0637, E0638, E0642 | "stander", "standing frame", "sit-to-stand" |
| Mobility aids | E0100–E0148 | "walker", "rollator", "quad cane", "crutches" |
| Transfer aids | E0635, E0639, E0641 | "Hoyer lift", "patient lift", "mechanical lift", "ceiling lift", "transfer board / belt / sling / pole", "gait belt", "Sara Stedy", "Sabina" |
| Bath / toileting safety | E0163, E0240, E0241, E0244, E0247 | "shower chair", "tub bench", "commode", "raised toilet seat", "grab bars" |
| Beds / mattresses | E0250–E0256, E0260, E0261, E0277, E0297 (E0181 moved out — it is a positioning cushion, not a bed) | "hospital bed", "alternating-pressure mattress", "low-air-loss mattress", "pressure-reducing mattress" |
| Respiratory equipment (high relevance for motor neuron disease) | E0470, E0471, E0472, E0464, E0465, E0466, E0481, E0482, E0483, E0500, E0600, E1390–E1392 | "BiPAP", "BPAP", "bi-level", "AVAPS", "CPAP", "NIV", "NPPV", "NIPPV", "noninvasive ventilation", "sip ventilation", "home ventilator", "tracheostomy ventilator", "Trilogy", "Astral", "Vivo", "VOCSN", "Luisa", "LTV", "cough assist", "MI-E", "CoughAssist", "VitalCough", "Comfort Cough", "HFCWO", "high-frequency chest wall oscillation", "The Vest", "InCourage", "SmartVest", "AffloVest", "suction machine", "Yankauer", "oxygen concentrator", "HomeFill", "Inogen", "SimplyGo", "Eclipse", "portable oxygen", "home oxygen" |
| Feeding equipment | B9002, B4034–B4036 (supply kits), B4081–B4088 (tubes and accessories) | "PEG tube", "G-tube", "GJ-tube", "NG tube", "gastrostomy", "jejunostomy", "feeding pump", "enteral pump", "Kangaroo", "Joey pump", "Infinity pump", "EnteraLite", "Flexiflo", "enteral feeds / tube feeds / bolus feeds", "continuous feeds", "gravity feeds" |
| Orthotic / bracing | CPT 97760, 97761, 97763 | "AFO", "KAFO", "HKAFO", "ankle-foot orthosis", "cervical collar", "neck brace", "head support collar", "wrist splint", "hand splint", "resting hand splint" |
| Environmental control / assistive tech | (no dedicated codes) | "environmental control unit", "ECU", "smart-home accessibility", "voice-controlled environment", "switch access" |
| Home modifications | (no dedicated codes) | "wheelchair ramp", "stairlift", "chair lift", "accessible bathroom", "grab bar installation", "home modification" |
| Equipment referrals | (no dedicated codes — captured by regex) | "DME order / referral / prescription", "OT eval for equipment", "PT consult for wheelchair", "SLP eval for AAC", "wheelchair fitting", "seating clinic", "home safety eval" |

The dicts and pattern list at the top of the module are the right place to extend coverage; everything is plain Python with no dependencies beyond the standard library.

## A note on brand-name detection

Brand-name patterns are kept alongside generic patterns. Real EHR narrative routinely names the device by brand ("patient uses a Trilogy 100 overnight", "trial of Tobii Dynavox", "Kangaroo pump with bolus feeds"), so collapsing everything to generic terms would lose recall. Both surfaces feed the same output; downstream phenotyping decides whether to roll brand variants up to a generic equipment class (e.g. all `ventilator_brand` matches under "Home ventilator").

## A note on SNOMED CT

SNOMED CT concept IDs in this module are seed values. Before production clinical use, validate against the current SNOMED CT release for active status, preferred term, descendants, and whether you want device concepts versus procedure concepts. Hierarchies vary by implementation, and concepts are sometimes inactive or replaced. The module's source notes which SNOMED IDs are flagged for validation.

## A note on the structured-versus-NLP split

HCPCS = equipment / supplies billing codes. CPT = clinician services / evaluations. SNOMED CT = clinical terminology. Regex = NLP extraction. The four are intentionally mixed because all four surfaces carry equipment information in real EHR data, but they do not mean the same thing — a CPT 97542 ("wheelchair management / training") tells you that a clinician spent time on wheelchair-related care, while HCPCS K0848 tells you a specific power wheelchair was ordered. Downstream phenotyping should treat the four surfaces as separate evidence rather than collapsing them.

## A note on calibration

This module's HCPCS ranges have been tightened from previous iterations based on a structured critique of the original mappings. For high-sensitivity research screening, the current set is a reasonable starting point. For production claims phenotyping or registry abstraction, expect to do further codebook refinement against your local data — particularly around SNOMED concept validation and the manual-wheelchair classes.

## Outputs

Two side-by-side CSVs, both UTF-8 with BOM (Excel-friendly), all cells quoted, sorted by `patient_id` then source.

### `device_codes.csv`

One row per coded record in the bundle whose (vocabulary, code) matches a known equipment code. Columns:

| Column | Description |
|---|---|
| `patient_id` | Patient identifier from `dashboard_data.json` |
| `source_kind` | Bundle category the record came from (`procedures`, `medications`, `care_plans`, etc.) |
| `source_id` | Record-level ID for traceability |
| `code_system` | Vocabulary the code is drawn from (`HCPCS`, `SNOMED-CT`, `CPT-4`) |
| `code` | The code itself |
| `recorded_display_name` | The display name the EHR provided (often empty for HCPCS) |
| `forge_label` | The label the module assigns from its mapping table |
| `category` | Equipment category (e.g. `wheelchair_power`, `respiratory`, `aac_device`) |
| `effective_date` | Record's effective date or start date |
| `status` | Record status if present |

### `device_extractions.csv`

One row per regex match in CCDA section narratives, decoded note text, and diagnostic-report narratives. Columns:

| Column | Description |
|---|---|
| `patient_id` | Patient identifier |
| `source_kind` | `ccda_section`, `note`, or `diagnostic_report` |
| `source_id` | Document or section identifier for traceability |
| `pattern` | Internal name of the regex pattern that matched |
| `category` | Equipment category |
| `value` | The exact text the regex matched |
| `snippet` | ±60 characters of surrounding context, for spot-review |
| `char_offset` | Character position of the match in the source text |
| `description` | Human-readable description of the pattern |

The two files together let you build per-patient equipment summaries — flag a patient if either file has any row for them, count by category to track equipment uptake over time, or feed both as columns into the patient master CSV for a one-row-per-patient view.

## Running it

From the command line, sitting in a directory with `dashboard_data.json`:

```bash
python device_extraction.py
```

From Python:

```python
import device_extraction
device_extraction.main(
    bundle_path = './dashboard_data.json',
    out_root    = './',
)
```

In Colab, after the main pipeline cell:

```python
import sys, importlib

WORK = '/content/work'
DRIVE = '/content/drive/MyDrive/ALS_TDI_complete_FINAL_PIPELINE'

if WORK not in sys.path: sys.path.insert(0, WORK)
for m in [k for k in list(sys.modules) if k == 'device_extraction']:
    del sys.modules[m]
import device_extraction

device_extraction.main(
    bundle_path = f'{WORK}/dashboard_data.json',
    out_root    = WORK,
)

# Copy the two CSVs back to Drive
import shutil
for name in ('device_codes.csv', 'device_extractions.csv'):
    shutil.copy(f'{WORK}/{name}', f'{DRIVE}/{name}')
```

## What this is and isn't

- **Is:** a focused, dependency-free module that complements the ALS-specific note extraction with everything an ALS or progressive-neurologic registry needs to track around adaptive equipment, respiratory equipment, feeding equipment, and the referral pathways that bring them in.
- **Is:** designed for joinability — same `patient_id` key as `patient_master.csv`, `note_extractions.csv`, and the per-patient Phenopacket JSONs, so equipment status can be cross-referenced with diagnoses, ALSFRS-R scores, and milestone dates.
- **Is:** explicit about what it captures versus what it doesn't — every regex pattern carries a description string so reviewers can audit the extraction rationale, and every code maps to a category for downstream grouping.
- **Isn't:** a replacement for a clinical equipment-tracking workflow. The patterns are seed patterns; site-specific tuning against real narrative samples is expected.
- **Isn't:** an attempt to capture supplies (catheters, dressings, batteries) that aren't the device itself. Scope is intentionally limited to the equipment that affects functional independence.
