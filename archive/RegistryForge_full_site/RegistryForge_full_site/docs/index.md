<div class="rf-funding" markdown>
**Funding acknowledgement.** This work was supported by the Centers for Disease Control and Prevention grant **# R01-TS000341**.
</div>

<div class="rf-hero" markdown>

<span class="rf-hero-eyebrow">Open-source · Local-first · No servers required</span>

<h1 class="rf-hero-title">Registry Forge</h1>

<p class="rf-hero-tagline">From raw EHR data to registry analytics &mdash; the whole pipeline, in one place.</p>

<p class="rf-hero-body">
Take raw EHR exports &mdash; <strong>C-CDA XML, FHIR R4 Bundles, RTF clinical notes, PDFs, and HTML fragments</strong> &mdash; and produce a research-ready data set in one pass: a structured patient bundle, OMOP CDM v5.4 tables, GA4GH Phenopackets, a browser dashboard, a shareable cohort EDA report, a drug repurposing report with CURE ID export, and privacy-safe device-and-equipment and environmental-exposure dashboards. Single-file Python, runs locally, ships with a five-tier QC framework and an inventory of every vocabulary and code it touched.
</p>

<div class="rf-badges" markdown>
<span class="rf-badge-strong rf-badge">C-CDA XML</span>
<span class="rf-badge-strong rf-badge">FHIR R4 JSON</span>
<span class="rf-badge-strong rf-badge">RTF notes</span>
<span class="rf-badge-strong rf-badge">PDF</span>
<span class="rf-badge-strong rf-badge">HTML</span>
</div>

<div class="rf-cta-row" markdown>
[:material-rocket-launch: Quickstart](quickstart.md){ .rf-cta .rf-cta-primary }
[:material-account-search: Cohort dashboard](demo.md){ .rf-cta .rf-cta-secondary }
[:material-chart-multiple: Cohort EDA](cohort-eda.md#live-demo){ .rf-cta .rf-cta-secondary }
[:material-text-search: Note extraction](assets/note_dashboard_demo.html){ .rf-cta .rf-cta-secondary target="_blank" }
[:material-pill: Drug repurposing report](drug-repurposing.md#live-demo){ .rf-cta .rf-cta-secondary }
[:material-medical-bag: Device dashboard](assets/device_dashboard_demo.html){ .rf-cta .rf-cta-secondary target="_blank" }
[:material-leaf: Exposure dashboard](assets/exposure_dashboard_demo.html){ .rf-cta .rf-cta-secondary target="_blank" }
[:material-download: Downloads](downloads.md){ .rf-cta .rf-cta-secondary }
</div>

<p style="text-align:center; margin: 12px auto 0; font-size: 13px; color: var(--md-default-fg-color--light); font-style: italic;">
Six live demos run entirely in your browser: the interactive cohort dashboard (load your own bundle JSON), the privacy-safe cohort EDA report, the clinical-note extraction dashboard, the drug repurposing report, and the device-and-equipment and environmental-exposure dashboards &mdash; all pre-loaded against synthetic ALS cohorts.
</p>

</div>

<p class="rf-section-eyebrow">Live demos</p>
<h2 class="rf-section-h">Six privacy-safe demos &mdash; open the HTML and you're done</h2>

<p style="max-width: 880px; margin: 0 auto 28px; color: var(--md-default-fg-color--light); font-size: 15px; line-height: 1.6;">
Every Registry Forge demo is a <strong>single self-contained HTML file</strong>. No server, no JavaScript build, no external scripts. PT-NNNN pseudonyms, year-only dates, and k-anonymity baked into the static dashboards and reports. Open one in any browser; share it with collaborators as an artifact attached to an email.
</p>

<div class="grid cards" markdown>

-   :material-account-search:{ .lg } &nbsp; **[Cohort dashboard](dashboard.md)**

    The interactive patient-and-cohort dashboard produced by the core
    pipeline. Loads your own bundle JSON in the browser and shows
    demographics, problems, medications, labs, encounters, and an
    integrated note viewer for every patient.

    [:material-arrow-right-circle: Open demo](demo.md){ .md-button }

-   :material-chart-multiple:{ .lg } &nbsp; **[Cohort EDA report](cohort-eda.md)**

    Privacy-safe exploratory analysis report. ALS-specific measurements
    (ALSFRS-R, FVC% predicted, El Escorial), demographics, comorbidities,
    and code-vocabulary distribution. Designed for sharing outside the
    clinical firewall.

    [:material-arrow-right-circle: Open demo](cohort-eda.md#live-demo){ .md-button }

-   :material-text-search:{ .lg } &nbsp; **[Clinical-note extraction dashboard](note-dashboard.md)**

    ALS-specific clinical content recovered from unstructured narrative:
    ALSFRS-R total and four subdomain scores, FVC % predicted, El
    Escorial / Awaji-Shima certainty, onset region, family history,
    genetic mutations, FTD-spectrum mentions, and dated treatment
    milestones (PEG, tracheostomy, NIV, riluzole, edaravone).

    [:material-arrow-right-circle: Open demo](assets/note_dashboard_demo.html){ .md-button target="_blank" }

-   :material-pill:{ .lg } &nbsp; **[Drug repurposing report](drug-repurposing.md)**

    Reimer-methodology pharmacoepidemiology hypothesis generation.
    Produces an HTML report with the ALS clinical-interest medication
    panel, an ATC-class-grouped medication summary, a forest plot, and a
    CURE ID intake CSV ready for off-label-use reporting.

    [:material-arrow-right-circle: Open demo](drug-repurposing.md#live-demo){ .md-button }

-   :material-medical-bag:{ .lg } &nbsp; **[Device & equipment dashboard](device-dashboard.md)**

    Devices, durable-medical-equipment indicators, and ALS-care-pathway
    procedures (spirometry, sleep studies, EMG/NCS, PT/OT evaluations,
    speech screening, mobility status). Combines structured codes and
    note-regex matches with source-snippet drill-down.

    [:material-arrow-right-circle: Open demo](assets/device_dashboard_demo.html){ .md-button target="_blank" }

-   :material-leaf:{ .lg } &nbsp; **[Environmental / occupational exposure dashboard](exposure-dashboard.md)**

    ALS-relevant environmental, occupational, and toxic exposures grounded
    in [ECTO](https://github.com/EnvironmentOntology/environmental-exposure-ontology),
    the GA4GH-Phenopackets-compatible exposure ontology. Smoking, military
    service, pesticides, heavy metals, solvents, head trauma, cyanotoxins,
    asbestos, EMF, air pollution, mold &mdash; with verified ECTO term IDs
    where available and an explicit curation worklist for the rest.

    [:material-arrow-right-circle: Open demo](assets/exposure_dashboard_demo.html){ .md-button target="_blank" }

</div>

<p class="rf-section-eyebrow">Everything you get</p>
<h2 class="rf-section-h">A complete EHR-to-research pipeline, in sixteen modules</h2>

<p style="max-width: 880px; margin: 0 auto 28px; color: var(--md-default-fg-color--light); font-size: 15px; line-height: 1.6;">
Registry Forge is organized as a small <strong>core pipeline</strong> (<code>run_pipeline.py</code>, Stages 1&ndash;7, plus the browser dashboard) and a set of independent <strong>add-on modules</strong> that consume the bundle the core produces. Run only the core and you have a researcher-usable bundle and a dashboard. Add OMOP for OHDSI federation, Phenopackets for rare disease research, the cohort EDA for PHI-safe sharing, drug repurposing for pharmacoepidemiology hypothesis generation &mdash; whatever your use case calls for. Every module is plain Python with editable seed dictionaries; the modules are starting points, not finished products.
</p>

<div class="grid cards" markdown>

-   :material-database-import:{ .lg } &nbsp; **Multi-format ingest**

    Reads raw C-CDA XML, FHIR R4 JSON Bundles, RTF clinical notes, HTML
    fragments, base64-chunked CSV exports, and PDFs &mdash; routed to format-
    specific parsers by magic-byte detection. No conversion required upstream.

-   :material-account-supervisor:{ .lg } &nbsp; **Cross-format patient linkage**

    Resolves FHIR `medicationReference`, `DocumentReference.subject`, and
    UUID-to-identifier bridges so the same patient across CCDA + FHIR + notes
    appears as one record. Survives messy production identifier drift.

-   :material-translate:{ .lg } &nbsp; **Coding troubleshooting & enrichment**

    Built-in display-name lookup for common LOINC and SNOMED codes fills the
    blanks the EHR left empty. Multiple codings preserved per record so
    downstream consumers can pick the one that fits.

-   :material-format-list-checks:{ .lg } &nbsp; **Code inventory report**

    Stage 7 walks every record and emits a CSV listing every (vocabulary, code)
    pair encountered, with reference and unique-patient counts and the bundle
    categories the code appears in. Your seed-mapping worklist.

-   :material-file-table-outline:{ .lg } &nbsp; **Patient master CSV**

    A long-format master CSV that joins every patient's demographics onto every
    coded record, with FHIR-only and CCDA-only splits, raw record JSON
    preserved per row, and Excel-safe encoding for collaborator review.

-   :material-database-export:{ .lg } &nbsp; **[OMOP CDM v5.4](https://ohdsi.github.io/CommonDataModel/cdm54.html) ETL**

    Maps every source code to its standard concept via [Athena](https://athena.ohdsi.org/)
    `CONCEPT_RELATIONSHIP` *Maps to* and routes records by domain into nine CDM tables.
    Output folder tagged with the vocabulary release version for reproducibility.

-   :material-dna:{ .lg } &nbsp; **[GA4GH](https://www.ga4gh.org/) [Phenopackets v2](https://phenopacket-schema.readthedocs.io/) ETL**

    Structured-code-driven Phenopackets &mdash; ICD-10 / SNOMED &rarr; [HPO](https://hpo.jax.org/)
    and [Mondo](https://mondo.monarchinitiative.org/) via seed tables for spectrum of motor
    neuron disease, epilepsy, and autoimmune disease. Plug in a curated genetics CSV to
    populate full HGNC/HGVS/ACMG GenomicInterpretations.

-   :material-account-group-outline:{ .lg } &nbsp; **Mondo-OMOP cohort builder**

    Given a [Mondo](https://mondo.monarchinitiative.org/) term ID, walks the disease hierarchy to find every
    descendant and emits a code list (SNOMED + ICD-10) defining the cohort,
    plus OMOP standard concept_ids. Built-in rare disease subset flags
    (GARD, NORD, Orphanet). Adapted from Monarch Initiative's mondo2omop.

-   :material-text-search:{ .lg } &nbsp; **Note extraction (regex)**

    Recovers ALS-specific content from free-text narratives that rarely makes
    it into discrete fields: ALSFRS-R total + 4 subdomains, ECAS scores, El
    Escorial, family history, gene mentions, treatment milestones. Demonstration
    layer; production-graded by site-specific clinical review.

-   :material-wheelchair:{ .lg } &nbsp; **Device & equipment extraction**

    Walks the bundle for HCPCS Level II + SNOMED + CPT codes (speech-generating
    devices, wheelchairs, BiPAP, cough-assist, feeding tubes, hospital beds, orthotics)
    and runs regex against narratives for both generic equipment terms and brand-name
    detection (Tobii Dynavox, Trilogy, Hoyer lift, The Vest, Kangaroo pump,
    AVAPS / NIPPV, OT/PT/SLP eval). Emits two joinable CSVs.

-   :material-chart-box-outline:{ .lg } &nbsp; **Browser dashboard**

    A single static HTML file that loads the bundle in any browser &mdash; no
    server, no install. Per-patient views, cohort overview, format filters,
    and a global keyword search across every document body and clinical record.

-   :material-chart-multiple:{ .lg } &nbsp; **Cohort EDA report**

    A single self-contained HTML page summarizing demographics, code coverage,
    observation period, vocabulary distribution, and ALS-specific signal &mdash;
    safe to share with colleagues. Pseudonymized IDs, banded ages, k-anonymity
    suppression, no per-patient diagnostic codes, no free text. [Live demo &rarr;](cohort-eda.md)

-   :material-pill:{ .lg } &nbsp; **Drug repurposing analysis**

    Adapts the methodology of [Reimer et al., *Lancet Digit Health* 2026](https://doi.org/10.1016/j.landig.2025.100963)
    to your bundle: identifies the motor-neuron-disease cohort, applies the
    paper&rsquo;s exposure criteria A and B, groups medications by ATC class, and
    exports a clean cohort table for downstream Cox / propensity analysis plus
    a CURE ID intake CSV ready for FDA / NCATS-NIH submission. Highly customizable
    via the `ATC_SEED` and exposure-window constants. [Read more &rarr;](drug-repurposing.md)

-   :material-shield-check:{ .lg } &nbsp; **Five-tier QC framework**

    A starting set of checks &mdash; schema validation, mapping coverage tracking,
    cross-output consistency, clinician spot-review, and synthetic-cohort
    regression testing &mdash; with recommended cadence. Built in, not bolted on.
    Adopters review their data through the established frameworks: the
    [Kahn et al. 2016](https://doi.org/10.13063/2327-9214.1244) taxonomy and
    the [Book of OHDSI Chapter 15](https://ohdsi.github.io/TheBookOfOhdsi/DataQuality.html)
    + [OHDSI DQD](https://github.com/OHDSI/DataQualityDashboard) for OMOP.

-   :material-package-variant:{ .lg } &nbsp; **Reproducibility built in**

    OMOP and Phenopackets output folders carry the vocabulary release version
    in the folder name; `metaData.resources` records every ontology version
    used. Any output file is traceable to the exact mappings that produced it.

-   :material-test-tube:{ .lg } &nbsp; **Synthetic demo cohort**

    Jane Marie Demo &mdash; a clinically-realistic synthetic ALS patient with 84
    records spanning every bundle category. Ships with the pipeline; runs end-
    to-end in seconds; the regression-test bedrock for every change.

</div>

<div class="rf-tested" markdown>
<span class="rf-tested-label">Tested across multiple production source-data variants</span>
<div class="rf-tested-row" markdown>
<span class="rf-tested-chip">Production C-CDA exports</span>
<span class="rf-tested-chip">FHIR R4 Bundle</span>
<span class="rf-tested-chip">Real ARC production data</span>
<span class="rf-tested-chip">Production RTF notes</span>
<span class="rf-tested-chip">Generic PDF</span>
<span class="rf-tested-chip">Databricks chunked CSV</span>
</div>
</div>

## Pipeline at a glance

```
Databricks export (chunked CSVs)  +  FHIR Bundle pulls  +  C-CDA XML
                              |
                              v
+----------------------------------------------------------+
|  Stage 1   Decoding & reassembly   (base64, chunk concat) |
|  Stage 2   Format detection         (magic-byte routing)  |
|  Stage 3   FHIR resource extraction (13 resource types)   |
|  Stage 4   Joining & assembly       (cross-format linkage)|
|  Stage 5   Display-name enrichment  (LOINC / SNOMED)      |
|  Stage 6   Test-patient exclusion   (rule-based filter)   |
|  Stage 7   Code inventory + master CSV + note extraction  |
+----------------------------------------------------------+
                              |
            +-----------------+-----------------+
            v                 v                 v
   dashboard_data.json   omop_etl.py    phenopackets_etl.py
   + dashboard.html      9 OMOP CDM     GA4GH Phenopackets v2
   + patient_master.csv  v5.4 tables    + cohort + summary
```

## Get started

- [Overview](overview.md) &mdash; a single-page summary
- [Installation](installation.md) &mdash; Python environment setup
- [Quickstart](quickstart.md) &mdash; run end-to-end against the included synthetic data
- [Live dashboard demo](demo.md) &mdash; the patient dashboard running on the synthetic cohort
- [Cohort EDA demo](cohort-eda.md) &mdash; the no-PHI cohort report you can share with colleagues
- [Data extraction (Databricks)](databricks.md) &mdash; generate the chunked CSV inputs from your warehouse

## Built on the same foundation as established consumer registry platforms

Registry Forge uses the same patient-directed SMART on FHIR + OAuth 2.0 acquisition pattern that has become standard across consumer health-record applications, registry platforms, and rare disease frameworks built on REDCap or similar tools. It is the open-source ETL layer for organizations that want the same data flow without standing up a vendor platform.

## A note on validation

Registry Forge has been built and tested against the ALS Therapy Development Institute's ARC Study data. We have not yet tested it against data from other organizations, EHR vendors, or registry deployments. If you would like to use Registry Forge with your own data and help us understand how it performs in other settings, please [contact us](https://www.als.net/) &mdash; we would welcome the collaboration.

## Registry Forge gives you the bones. You bring the judgment.

Registry Forge automates the engineering work &mdash; schema parsing, code system normalization, vocabulary harmonization, FHIR / C-CDA chunk reassembly, the OMOP and Phenopackets ETLs, the device and note extraction layers &mdash; that would otherwise take a small team many thousands of hours to write from scratch. **What it does not do is replace your own clinical, methodological, and editorial judgment about your registry.**

Real ETL is iterative:

- You will look at the OMOP `CONDITION_OCCURRENCE` table and decide some source codes shouldn't have mapped to those standard concepts &mdash; or that they need a different mapping for your study.
- You will look at the cohort EDA and decide to drop a handful of patients who shouldn't be in the analysis, or to revisit a participant whose record looks anomalous.
- You will look at the Phenopackets output and add disease seed mappings before submitting to a Matchmaker network.
- You will look at the device extraction CSVs and decide which brand-name matches to collapse to a generic class, and which to keep distinct.
- You will tune the note-extraction regex against your own narrative style.

That iteration is **your** work, and it's what makes the registry trustworthy. Registry Forge is a starting point that gets you to that iteration much faster &mdash; not a substitute for it.

For the canonical reference on doing this work well, read the OHDSI community's [Book of OHDSI, Chapter 6 &mdash; Extract Transform Load](https://ohdsi.github.io/TheBookOfOhdsi/ExtractTransformLoad.html). It covers how to plan an ETL, how to think about source-to-target mapping, when to add custom logic, and how to validate the result. Registry Forge follows the spirit of those practices; the depth comes from you.

## About, citation, and contact

Registry Forge was developed by **Danielle Boyce, MPH, DPA**, of the [ALS Therapy Development Institute](https://www.als.net/), as part of the [ARC Study](https://www.als.net/arc/) natural history registry program. De-identified ARC data is being made available through the [ARC Data Commons](https://www.als.net/arc/data-commons/).

A DOI for this software and its companion manuscript is forthcoming. Registry Forge is released under the [MIT License](about.md#license) &mdash; permissive, no warranty, free to use and modify. If you would like help adapting it to your own registry or EHR vendor, please reach out: <a href="mailto:dboyce@als.net">dboyce@als.net</a>.

See the [About &amp; license](about.md) page for full details, acknowledgements, and how to cite. For related tools and training &mdash; CDAtransformer (single-file C-CDA inspection), the ALS TDI Real World Evidence Resources hub, *Guide to Real-World Data for Clinical Research*, and the OMOP introductory course &mdash; see the [Resources &amp; related tools](resources.md) page.
