# Registry Forge — backlog of items to address later

Running list of issues spotted during demo and dashboard review.
Each entry: `[date noted]` short description, then context. Strike
through entries when resolved.

## Demo data realism

### Note dashboard synthetic snippet realism — RESOLVED 2026-05-13
Original synthetic data wrapped every captured value in the same
generic `"...patient findings include {val} per neurologic exam on
visit..."` boilerplate. Several values do not appear in that clinical
context (FTD is not a neurologic-exam finding; PEG dates and trach
dates are procedure-record entries, not exam findings). Some snippets
also lacked the captured value entirely as a substring of the
surrounding text. **Fixed:** rewrote synthetic-data generator to use
per-pattern templates that match how each value actually appears in
clinical narrative — spirometry context for FVC%, social-history
context for family history, gene-panel-result context for genetic
mutations, etc.

### Cohort EDA demo data — review for realism
The synthetic ALSFRS-R, FVC%, and El Escorial values in the cohort
EDA demo may have the same generic-context issue. Confirm next time
the demo is regenerated.

### Drug repurposing demo data — review
Likewise — the medication-name strings in the synthetic drug
repurposing demo should reflect real RxNorm SCD form variation
(salt names, dosage strings, brand/generic mix). Currently they may
look too clean.

## Pattern accuracy (note extraction)

### onset_region — capture date alongside region
Onset region (bulbar / cervical / lumbar) is most useful clinically
when paired with the *date* of symptom onset. Current pattern captures
the region word; add a paired pattern that captures
"symptoms started/onset/began [DATE]" for the same event.

### ALSFRS-R — capture subdomain scores separately
Pattern currently captures the total. Add subdomain capture
(bulbar / fine motor / gross motor / respiratory; each 0–12) so
analysts can recover the four-component breakdown without re-parsing
the snippet.

### FVC% predicted — capture seated vs supine
Both are clinically important; supine FVC drop ≥ 25% is a NIV-initiation
criterion. Current pattern doesn't distinguish.

### Family history negative — distinguish "denies" from "no documented"
"Denies family history" (patient assertion) is different from "family
history not obtained" (clinician didn't ask) and from "no documented
family history" (intermediate). Worth surfacing the difference in
captured value.

## Pattern accuracy (exposure extraction)

### tobacco_chewing snuff fix — DONE 2026-05-13
Bare `\bsnuff\b` was matching Epic / Cerner social-history template
boilerplate where snuff appears as a field option for every encounter
regardless of patient endorsement. Now requires positive endorsement
context (`uses snuff`, `chronic snuff use`, `history of snuff`).
Re-run drops the 8,450 count substantially while keeping real
endorsements.

### NFL = neurofilament light fix — DONE 2026-05-13
Bare `NFL` was matching the serum biomarker (Z-score routinely
reported for ALS patients) and falsely flagging head-trauma category.
Now requires explicit football vocabulary (`played NFL`, `NFL player`,
`NFL career`, etc.).

### PERC = pulmonary embolism rule-out fix — DONE 2026-05-13
Bare `PERC` was matching the PE rule-out criteria. Now requires
explicit exposure context.

### Negation context for exposure mentions
A "denies tobacco use" or "no pesticide exposure" still increments
the exposure count today. Reasonable for surfacing topic discussion,
but for *exposure-prevalence* analysis we'd want to filter negated
mentions. Consider adding a `negation_detected` column to
exposure_extractions.csv with a simple ConText-style rule.

## Dashboard improvements

### Note dashboard — add date axis where applicable
For dated milestones (PEG, tracheostomy, NIV, riluzole start), would
be useful to show a timeline / Gantt-style view by patient.

### Device dashboard — surface DME-supplier gap explicitly
The "no DME procurement codes captured" finding is documented in the
About tab. Consider a top-of-dashboard callout so readers notice it
without opening the About panel.

### Exposure dashboard — ECTO curation status filter
Add a filter / toggle in the All Exposures tab to show only patterns
with verified ECTO IDs vs all patterns. Helps a publication-prep user
see what's defensible vs pending.

## ECTO curation worklist (from exposure_extraction.py)

Patterns awaiting verified ECTO term IDs:
- benzene
- cadmium_exposure
- camp_lejeune
- contact_sport_football
- contact_sport_other
- cyanotoxin
- dust_general
- emf_electrical
- farming_occupation
- formaldehyde
- heavy_metal_general
- manganese_exposure
- military_blast
- military_gulf_war
- military_service_general
- military_vietnam
- mold_exposure
- pesticide_DDT
- pesticide_general
- pesticide_glyphosate
- pesticide_organophosphate
- pesticide_paraquat
- solvent_general
- tbi_history
- tce_pce
- welding

Verified so far (6):
- smoking → ECTO:9000250 (exposure to nicotine)
- lead_exposure → ECTO:9000945 (exposure to lead)
- mercury_exposure → ECTO:0001571 (exposure to mercury)
- arsenic_exposure → ECTO:9000032 (exposure to arsenic)
- asbestos → ECTO:9000033 (exposure to asbestos)
- air_pollution → ECTO:0000977 (exposure to ultrafine respirable
  suspended particulate matter via inhalation)

## Manuscript figures still needed

Figures 1 and 8 embedded; figures 2-7 still placeholders:
- Figure 2 — Browser-viewable cohort dashboard screenshot
- Figure 3 — Code inventory + OMOP outputs panel
- Figure 4 — Regex-based note extraction output (CSV excerpt)
- Figure 5 — GA4GH Phenopackets v2 JSON output excerpt
- Figure 6 — Synthetic demonstration vs. production deployment
- Figure 7 — Cohort EDA report (PHI-free)
