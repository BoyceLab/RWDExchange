# External references and standards

Registry Forge builds on a stack of community-maintained standards, vocabularies, and ontologies. This page lists the canonical authoritative source for each, with a short note on what Registry Forge uses it for. Adopters should treat these URLs as the primary references when validating their own ETL output.

## Common Data Models and observational research

| Standard | Canonical source | Used by Registry Forge for |
|---|---|---|
| **OHDSI community** | [https://www.ohdsi.org/](https://www.ohdsi.org/) | Methodology, network studies, software ecosystem |
| **OMOP CDM v5.4 specification** | [https://ohdsi.github.io/CommonDataModel/cdm54.html](https://ohdsi.github.io/CommonDataModel/cdm54.html) | Target schema for `omop_etl.py` (Add-on module) |
| **OHDSI Athena vocabulary service** | [https://athena.ohdsi.org/](https://athena.ohdsi.org/) | Source for vocabulary downloads (`CONCEPT.csv`, `CONCEPT_RELATIONSHIP.csv`) used in OMOP source-to-standard mapping |
| **Book of OHDSI** | [https://ohdsi.github.io/TheBookOfOhdsi/](https://ohdsi.github.io/TheBookOfOhdsi/) | Canonical community reference |
| **Book of OHDSI Chapter 6 &mdash; Extract Transform Load** | [https://ohdsi.github.io/TheBookOfOhdsi/ExtractTransformLoad.html](https://ohdsi.github.io/TheBookOfOhdsi/ExtractTransformLoad.html) | Recommended reading for any adopter planning an ETL |
| **Book of OHDSI Chapter 15 &mdash; Data Quality** | [https://ohdsi.github.io/TheBookOfOhdsi/DataQuality.html](https://ohdsi.github.io/TheBookOfOhdsi/DataQuality.html) | Recommended reading on the [QC framework page](qc.md) |
| **OHDSI Data Quality Dashboard (DQD)** | [https://github.com/OHDSI/DataQualityDashboard](https://github.com/OHDSI/DataQualityDashboard) | Reference implementation of the Kahn framework on OMOP-shaped data |

## Genomic / rare disease standards

| Standard | Canonical source | Used by Registry Forge for |
|---|---|---|
| **GA4GH Global Alliance for Genomics and Health** | [https://www.ga4gh.org/](https://www.ga4gh.org/) | Standards body for Phenopackets and related schemas |
| **GA4GH Phenopackets v2 schema** | [https://phenopacket-schema.readthedocs.io/](https://phenopacket-schema.readthedocs.io/) and [https://github.com/phenopackets/phenopacket-schema](https://github.com/phenopackets/phenopacket-schema) | Target schema for `phenopackets_etl.py` (Add-on module) |
| **Phenopackets project home** | [https://phenopackets.org/](https://phenopackets.org/) | Project overview, examples, tooling |
| **Human Phenotype Ontology (HPO)** | [https://hpo.jax.org/](https://hpo.jax.org/) | Phenotype terms in Phenopackets MedicalActions |
| **Mondo Disease Ontology** | [https://mondo.monarchinitiative.org/](https://mondo.monarchinitiative.org/) | Disease terms in Phenopackets; rare disease cohort builder |
| **Mondo downloads** | [https://github.com/monarch-initiative/mondo/releases](https://github.com/monarch-initiative/mondo/releases) | Tagged monthly releases (`mondo.json`, `mondo.owl`, `mondo.obo`) |
| **Mondo OBO PURL (latest)** | [http://purl.obolibrary.org/obo/mondo.json](http://purl.obolibrary.org/obo/mondo.json) | Always-current Mondo JSON; used as fallback when GitHub releases are unreachable |
| **Monarch Initiative** | [https://monarchinitiative.org/](https://monarchinitiative.org/) | Maintainer of Mondo and host of related rare disease infrastructure |

## SMART on FHIR and clinical interoperability

| Standard | Canonical source | Used by Registry Forge for |
|---|---|---|
| **HL7 FHIR R4** | [https://www.hl7.org/fhir/](https://www.hl7.org/fhir/) | Source data format for FHIR Bundle ingestion |
| **SMART on FHIR** | [https://smarthealthit.org/](https://smarthealthit.org/) | Patient-directed data acquisition pattern that ARC and adopters use |
| **HL7 C-CDA R2.1** | [https://www.hl7.org/implement/standards/product_brief.cfm?product_id=492](https://www.hl7.org/implement/standards/product_brief.cfm?product_id=492) | C-CDA XML parsing in `run_pipeline.py` |
| **CMS Blue Button 2.0** | [https://bluebutton.cms.gov/](https://bluebutton.cms.gov/) | Medicare claims via SMART on FHIR (relevant for some adopters) |
| **ONC 21st Century Cures Act Final Rule** | [https://www.healthit.gov/topic/oncs-cures-act-final-rule](https://www.healthit.gov/topic/oncs-cures-act-final-rule) | Regulatory backdrop for patient-directed data sharing |

## Vocabularies and code systems

| Vocabulary | Canonical source | Used by Registry Forge for |
|---|---|---|
| **SNOMED CT** | [https://www.snomed.org/](https://www.snomed.org/) | Problem-list standardization |
| **LOINC** | [https://loinc.org/](https://loinc.org/) | Lab and observation codes |
| **RxNorm** | [https://www.nlm.nih.gov/research/umls/rxnorm/](https://www.nlm.nih.gov/research/umls/rxnorm/) | Medication standardization |
| **ICD-10-CM** | [https://www.cdc.gov/nchs/icd/icd10cm.htm](https://www.cdc.gov/nchs/icd/icd10cm.htm) | Diagnosis coding |
| **HCPCS Level II** | [https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system](https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system) | Durable medical equipment (used by `device_extraction.py`) |
| **CPT-4** | [https://www.ama-assn.org/practice-management/cpt](https://www.ama-assn.org/practice-management/cpt) | Procedure codes |
| **ATC classification** | [https://www.whocc.no/atc_ddd_index/](https://www.whocc.no/atc_ddd_index/) | Drug class grouping (used by `drug_repurposing.py`) |
| **NDC** | [https://www.fda.gov/drugs/drug-approvals-and-databases/national-drug-code-directory](https://www.fda.gov/drugs/drug-approvals-and-databases/national-drug-code-directory) | Medication packaging codes |
| **CVX** | [https://www2a.cdc.gov/vaccines/iis/iisstandards/vaccines.asp?rpt=cvx](https://www2a.cdc.gov/vaccines/iis/iisstandards/vaccines.asp?rpt=cvx) | Vaccine codes |
| **CDC Race and Ethnicity** | [https://www.cdc.gov/nchs/data/statnt/statnt20.pdf](https://www.cdc.gov/nchs/data/statnt/statnt20.pdf) | Demographic standardization (US Core) |
| **UCUM units of measure** | [https://ucum.org/](https://ucum.org/) | Quantitative observation units |

## Data quality and methodology

| Reference | Source | Relevance |
|---|---|---|
| **Kahn et al. 2016 data quality framework** | Kahn MG, Callahan TJ, Barnard J, et al. *A harmonized data quality assessment terminology and framework for the secondary use of electronic health record data.* EGEMS (Wash DC) 2016;4(1):1244. PMID: 27713905. DOI: [10.13063/2327-9214.1244](https://doi.org/10.13063/2327-9214.1244) | Recommended framework adopters should use to review data quality. Defines the conformance / completeness / plausibility taxonomy that is the standard vocabulary for EHR data quality. |
| **Book of OHDSI Chapter 15 &mdash; Data Quality** | [https://ohdsi.github.io/TheBookOfOhdsi/DataQuality.html](https://ohdsi.github.io/TheBookOfOhdsi/DataQuality.html) | Practical implementation guidance and OMOP-specific data quality checks; canonical companion to Kahn |
| **OHDSI Data Quality Dashboard tool** | [https://github.com/OHDSI/DataQualityDashboard](https://github.com/OHDSI/DataQualityDashboard) | Reference implementation of Kahn / Book of OHDSI Ch 15 checks on OMOP-shaped data |
| **Phenopacket Schema paper** | Jacobsen JOB, Baudis M, Baynam GS, et al. *The GA4GH Phenopacket schema defines a computable representation of clinical data.* Nat Biotechnol. 2022;40(6):817-820. DOI: [10.1038/s41587-022-01357-4](https://doi.org/10.1038/s41587-022-01357-4) | Schema reference |
| **SMART on FHIR paper** | Mandel JC, Kreda DA, Mandl KD, Kohane IS, Ramoni RB. *SMART on FHIR: a standards-based, interoperable apps platform for electronic health records.* J Am Med Inform Assoc. 2016;23(5):899-908. DOI: [10.1093/jamia/ocv189](https://doi.org/10.1093/jamia/ocv189) | Foundational reference for the data acquisition pattern Registry Forge consumes |

## Drug repurposing references

| Reference | Source | Relevance |
|---|---|---|
| **Reimer et al. 2026 ALS drug repurposing** | Reimer RJ, Soper B, Wilson JL, et al. *Identification of drug repurposing candidates for amyotrophic lateral sclerosis using electronic health records: a retrospective cohort study.* Lancet Digit Health 2026. DOI: [10.1016/j.landig.2025.100963](https://doi.org/10.1016/j.landig.2025.100963) | Methodology adapted by [`drug_repurposing.py`](drug-repurposing.md) |
| **CURE Drug Repurposing Collaboratory** | [https://cure.ncats.io/](https://cure.ncats.io/) | FDA / NCATS-NIH / Critical Path Institute partnership; target of `cure_id_intake.csv` export |
| **CURE ID platform** | [https://cure.ncats.io/explore](https://cure.ncats.io/explore) | Treatment Registry where intake CRFs are submitted |

## Tooling cross-references

The following tools are cited in Registry Forge documentation as the appropriate next-step for work outside this pipeline's scope:

- **OHDSI Data Quality Dashboard** &mdash; for production-grade DQ checks on OMOP-shaped data: [https://github.com/OHDSI/DataQualityDashboard](https://github.com/OHDSI/DataQualityDashboard)
- **OHDSI ATLAS** &mdash; for cohort definition and federated study design over OMOP: [http://www.ohdsi.org/web/atlas/](http://www.ohdsi.org/web/atlas/)
- **lifelines** (Python) &mdash; for survival analysis on `drug_repurposing_cohort.csv`: [https://lifelines.readthedocs.io/](https://lifelines.readthedocs.io/)
- **statsmodels** (Python) &mdash; for propensity score modeling: [https://www.statsmodels.org/](https://www.statsmodels.org/)
- **cTAKES** &mdash; production-grade clinical NLP (recommended next step beyond the regex-based `note_extraction.py`): [https://ctakes.apache.org/](https://ctakes.apache.org/)
- **MedSpaCy** &mdash; Python clinical NLP alternative: [https://github.com/medspacy/medspacy](https://github.com/medspacy/medspacy)
- **Matchmaker Exchange** &mdash; rare disease matchmaking network that consumes Phenopackets: [https://www.matchmakerexchange.org/](https://www.matchmakerexchange.org/)
- **GA4GH Beacon Network** &mdash; federated variant query consuming Phenopacket variant findings: [https://beacon-network.org/](https://beacon-network.org/)
