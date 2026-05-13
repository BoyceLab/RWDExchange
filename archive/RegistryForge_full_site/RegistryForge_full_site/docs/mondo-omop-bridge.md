# Mondo-OMOP bridge & rare disease cohort builder

`mondo_omop_bridge.py` is the inverse of [`phenopackets_etl.py`](phenopackets.md). The Phenopackets ETL runs **forward** — source codes in the bundle → HPO/Mondo for a Phenopacket. The bridge runs **backward** — given a Mondo term ID, walk the Mondo disease hierarchy to find every descendant, then produce a code list that defines the cohort.

Use it when you want to answer questions like:

> *"Give me everyone with anything in the spectrum of motor neuron disease."*<br>
> *"Build me a code list for an epilepsy cohort I can paste into a query against any EHR or claims warehouse."*<br>
> *"Find all GARD-designated rare neurologic diseases in our cohort."*

## Attribution

The mapping logic and the use of Mondo's KGX `same_as` cross-references is adapted directly from the [Monarch Initiative's `mondo2omop`](https://github.com/monarch-initiative/mondo2omop) repository (MIT-licensed). We reuse their approach with credit, and extend it with a per-target cohort-build entry point, an optional Athena-free fallback, and a stdlib BFS implementation for environments without NetworkX.

## What it produces

The module emits one master mapping table and two per-cohort files for every target Mondo ID:

| File | Contents |
|---|---|
| `MONDO2OMOP_<release>.tsv` | One row per (Mondo term, source vocabulary, source code). Columns: mondo_id, mondo_label, mondo_description, source_vocabulary (ICD10CM, SNOMED, MeSH), source_code, standard_concept_id (OMOP, Condition domain), standard_concept_name, standard_vocabulary, standard_concept_code, plus six rare disease subset flags. Skipped Athena columns if no `vocab_dir` provided. |
| `cohort_<MONDO_id>_codes.tsv` | The code list that defines the cohort — one row per (Mondo descendant of target, source vocabulary, code). Drop the source-code column into a Databricks query or an EHR chart-review filter. |
| `cohort_<MONDO_id>_omop.tsv` | Same cohort joined to OMOP standard concept_ids. Drop the standard_concept_id list into a CONDITION_OCCURRENCE filter on your OMOP CDM extract. Skipped if `vocab_dir` is None. |

Every output row carries the six **rare disease subset flags** that Mondo annotates:

| Flag | What it means |
|---|---|
| `rare` | Mondo's general rare disease flag |
| `gard_rare` | Listed in [GARD](https://rarediseases.info.nih.gov/) (NIH Genetic and Rare Diseases Information Center) |
| `nord_rare` | Listed by [NORD](https://rarediseases.org/) (National Organization for Rare Disorders) |
| `orphanet_rare` | Listed in [Orphanet](https://www.orpha.net/) |
| `inferred_rare` | Inferred rare by Mondo's reasoner |
| `mondo_rare` | Mondo's own rare disease subset designation |

## How the descendant walk works

Mondo's KGX edges file records `is_a` (subclass_of) relationships between disease terms. The module loads those edges into a directed graph (NetworkX if available, plain dict-of-sets BFS otherwise) and computes the descendants of the target term.

Following the upstream mondo2omop logic, the module restricts to terms that are descendants of MONDO:0700096 (human disease) and excludes descendants of MONDO:0042489 (disease susceptibility), MONDO:0021125 (disease characteristic), and MONDO:0021178 (injury) before the walk.

**Picking the right target term matters.** "ALS" specifically (MONDO:0004976) returns just ALS and ALS-FTD because PMA and PLS are *siblings* in the Mondo hierarchy, not descendants. To get the full spectrum of motor neuron disease (ALS + PMA + PLS + ALS-FTD), target their shared parent: **MONDO:0019056 motor neuron disease**.

A few useful anchors for ALS TDI's domain:

| Anchor | What you get |
|---|---|
| `MONDO:0019056` motor neuron disease | spectrum of motor neuron disease (ALS, PMA, PLS, ALS-FTD, juvenile MND, hereditary forms) |
| `MONDO:0004976` amyotrophic lateral sclerosis | Just ALS proper and its sub-types |
| `MONDO:0005027` epilepsy | All epilepsy syndromes (focal, generalized, syndromic) |
| `MONDO:0005301` multiple sclerosis | MS and its phenotypes (RRMS, SPMS, PPMS) |
| `MONDO:0007915` systemic lupus erythematosus | SLE and its subtypes |
| `MONDO:0005071` nervous system disorder | Everything neurologic (very broad) |

Browse the full hierarchy at [Monarch Initiative](https://monarchinitiative.org/) or the [Mondo OBO browser](https://obofoundry.org/ontology/mondo.html).

## Running it

The module accepts a pre-downloaded Mondo KGX directory or downloads a release itself:

```python
import mondo_omop_bridge

mondo_omop_bridge.main(
    mondo_kgx_dir    = None,                       # None = download
    mondo_version    = '2026-04-07',               # if downloading; current release
    vocab_dir        = '/path/to/Athena/Vocab',    # optional; enables OMOP join
    target_mondo_ids = ['MONDO:0019056',           # spectrum of motor neuron disease
                        'MONDO:0005027',           # epilepsy
                        'MONDO:0005301'],          # MS
    out_root         = './mondo_omop_output',
)
```

From the command line, with a few defaults pre-set:

```bash
python mondo_omop_bridge.py
# Builds cohorts for MONDO:0004976 (ALS) and MONDO:0005027 (epilepsy)
```

In Colab, with Mondo cached on Drive:

```python
import sys
sys.path.insert(0, '/content/work')
import mondo_omop_bridge

DRIVE = '/content/drive/MyDrive/ALS_TDI_complete_FINAL_PIPELINE'

mondo_omop_bridge.main(
    mondo_kgx_dir    = f'{DRIVE}/mondo_kgx',          # pre-downloaded
    vocab_dir        = f'{DRIVE}/Vocab',              # Athena bundle
    target_mondo_ids = ['MONDO:0019056','MONDO:0005027'],
    out_root         = f'{DRIVE}/mondo_omop_output',
)
```

**Mondo download sources.** The module fetches the Mondo ontology from the canonical sources in the following order, falling back as needed:

1. **GitHub releases (primary, recommended)** &mdash; [https://github.com/monarch-initiative/mondo/releases](https://github.com/monarch-initiative/mondo/releases). The Monarch Initiative publishes monthly tagged releases (e.g. `v2026-04-07`) with assets including `mondo.json`, `mondo.owl`, and `mondo.obo`. The bridge module reads `mondo.json` from the release matching `mondo_version` (or `latest` if `mondo_version=None`). This is the stable, versioned, and citable source.
2. **OBO PURL (fallback)** &mdash; [http://purl.obolibrary.org/obo/mondo.json](http://purl.obolibrary.org/obo/mondo.json). Always-current latest version; use this when you want the bleeding edge without specifying a version. The Mondo download page is [https://mondo.monarchinitiative.org/pages/download/](https://mondo.monarchinitiative.org/pages/download/).
3. **Legacy KGX TSV** &mdash; the previous KG-OBO mirror at `kg-hub.berkeleybop.io/kg-obo/mondo/` is deprecated and no longer maintained. The module retains a load path for KGX TSV (`mondo_kgx_tsv_nodes.tsv` + `mondo_kgx_tsv_edges.tsv`) so adopters with cached KGX files can still use them, but new users should use the GitHub or PURL paths above.

The `mondo.json` file is approximately 50&ndash;100 MB; first run downloads it, subsequent runs against the same Mondo version reuse the cached copy.

## Worked example — spectrum of motor neuron disease cohort

Run against the synthetic 10-term Mondo subset that ships with Registry Forge as a smoke test:

```
$ python mondo_omop_bridge.py
[12:31] NetworkX available: True
[12:31] Mondo KGX dir: ./synthetic_mondo
[12:31]   Loaded 10 nodes, 9 edges
[12:31]   Disease nodes (post-obsolete filter): 10
[12:31] Building master MONDO2OMOP table ...
[12:31]   Kept 9 Mondo human-disease nodes (post-exclusions)
[12:31]   14 (Mondo, source-code) rows written
[12:31] Building cohort for MONDO:0004976 ...
[12:31]   Target: MONDO:0004976  amyotrophic lateral sclerosis
[12:31]   2 Mondo terms in cohort (target + descendants)
[12:31]   Wrote ./out/cohort_MONDO_0004976_codes.tsv (4 rows)
```

`cohort_MONDO_0004976_codes.tsv` (snippet):

```tsv
mondo_id      mondo_label                                          source_vocabulary  source_code  rare  gard_rare  nord_rare  orphanet_rare  inferred_rare  mondo_rare
MONDO:0004976  amyotrophic lateral sclerosis                       ICD10CM           G12.21       1     1          0          1              0              0
MONDO:0004976  amyotrophic lateral sclerosis                       SNOMED            86044005     1     1          0          1              0              0
MONDO:0019469  amyotrophic lateral sclerosis-frontotemporal dementia  ICD10CM        G31.09       1     1          0          1              0              0
MONDO:0019469  amyotrophic lateral sclerosis-frontotemporal dementia  SNOMED         230260009    1     1          0          1              0              0
```

With the full Mondo release, this same query returns dozens of rows covering hereditary ALS subtypes, ALS variants by causative gene, juvenile MND, and the full ALS-FTD spectrum — exactly the cohort you want to query in CONDITION_OCCURRENCE.

## Relationship to the other modules

`phenopackets_etl.py` and `mondo_omop_bridge.py` operate on the same vocabulary mappings but in opposite directions:

| Module | Direction | Input | Output |
|---|---|---|---|
| `phenopackets_etl.py` | Source → Mondo | EHR records with SNOMED/ICD-10 codes | GA4GH Phenopacket with Mondo `diseases[]` |
| `mondo_omop_bridge.py` | Mondo → Source + OMOP | A Mondo term ID | Code list (SNOMED, ICD-10-CM) + OMOP standard concept_ids |

Both consume the same Athena vocabulary bundle when present. Both honor the same ontology release version in their output filenames. Neither requires the other to run.

The bridge's output also doubles as **a high-quality input for extending `phenopackets_etl.py`'s seed mapping tables** — the `MONDO2OMOP_<release>.tsv` master table is exactly the (source-vocab, source-code) → Mondo mapping that the Phenopackets ETL needs. A small script that reads the master TSV and emits a Python dict literal can drop straight into the `SNOMED_ICD_TO_MONDO` block of `phenopackets_etl.py`.

## Dependencies

- Python 3.9+
- `requests` (for downloading Mondo KGX). Pre-download manually if not available.
- `networkx` (optional). Without it, the module falls back to a pure-stdlib BFS over a dict-of-sets graph. The fallback is slightly slower on full Mondo but functionally equivalent.

No pandas dependency despite the upstream mondo2omop using it — the bridge uses csv module + stdlib joins. This keeps the dependency footprint small for adopters who want to drop the module into a constrained environment.
