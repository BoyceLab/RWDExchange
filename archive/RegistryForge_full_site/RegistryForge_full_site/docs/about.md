# About Registry Forge

## Who built this

Registry Forge was developed by **Danielle Boyce, MPH, DPA**, of the [ALS Therapy Development Institute (ALS TDI)](https://www.als.net/), as part of the [ARC Study](https://www.als.net/arc/) natural history registry program.

Correspondence and questions: <a href="mailto:dboyce@als.net">dboyce@als.net</a>.

## What ARC is

The **ARC Study** is ALS TDI's natural history registry for people living with ALS and motor neuron disease spectrum disorders. Participants authorize ALS TDI to retrieve their longitudinal clinical records from their healthcare providers through patient-directed SMART on FHIR, and contribute survey data, biosamples, and (where consented) whole-genome sequencing. Registry Forge is the open-source post-acquisition data engineering layer for the EHR side of ARC.

De-identified ARC data is being made available to qualified researchers through the **ARC Data Commons** as that program is built out. For current access information and request workflow, see: [https://www.als.net/arc/data-commons/](https://www.als.net/arc/data-commons/).

## How to cite Registry Forge

A DOI for this software and its companion manuscript is forthcoming and will be added here once issued. In the meantime, an informal citation would be:

> Boyce D. *Registry Forge: an open-source ETL pipeline for SMART on FHIR clinical-registry data, with OMOP, GA4GH Phenopackets, and drug-repurposing add-on modules.* ALS Therapy Development Institute, 2026. Available at: [https://boycelab.github.io/RegistryForge/](https://boycelab.github.io/RegistryForge/)

If you use the drug repurposing add-on module, please also cite the methodology paper it builds on: Reimer et al., *Identification of drug repurposing candidates for amyotrophic lateral sclerosis using electronic health records*, [Lancet Digit Health 2026](https://doi.org/10.1016/j.landig.2025.100963).

## License

Registry Forge is released under the **MIT License**. This is one of the most permissive open-source licenses available: you may use, copy, modify, and redistribute the code freely &mdash; including for commercial purposes &mdash; provided you preserve the copyright and license notice. The software is provided **"AS IS" without warranty of any kind**, and the authors are not liable for any damages or issues arising from its use.

```
MIT License

Copyright (c) 2026 Danielle Boyce / ALS Therapy Development Institute

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

### What this license means in plain language

- **You can use Registry Forge for anything** &mdash; academic research, commercial registry work, internal hospital projects, derivative software, training materials &mdash; without asking permission and without paying anything.
- **You can modify it however you need.** Fork the repository, change the code, add new modules, remove parts you don't use. We encourage adopters to extend the seed dictionaries and tune the regex patterns to their own data.
- **You can redistribute it**, including as part of a larger system, as long as you keep the copyright notice and license text with the code.
- **There is no warranty.** Registry Forge is provided as-is. If it produces a wrong output, fails to run on your data, or causes any downstream consequence, ALS TDI and the authors are not liable. Real ETL is iterative; adopters are responsible for validating outputs against their own data &mdash; see [Registry Forge gives you the bones, you bring the judgment](index.md#registry-forge-gives-you-the-bones-you-bring-the-judgment) on the home page.

## Why this is important

Patient registries for rare and neurodegenerative diseases are usually built and run by very small teams with very little data engineering capacity. Most of the technical effort needed to turn raw EHR exports into a research-usable data set &mdash; format detection, code-system normalization, vocabulary harmonization, OMOP conformance, Phenopackets serialization, quality control &mdash; is the same across registries. Registry Forge is the pipeline ALS TDI built for the ARC Study, opened up so that other small registries don't have to build it from scratch.

This is the work that lets a registry move from "we have a folder of XML files" to "we have a research-ready data set with vocabulary-versioned OMOP tables and GA4GH Phenopackets ready for Matchmaker submission" without standing up a vendor platform, without writing 2,000+ lines of ETL code in-house, and without exposing data outside the analyst's workstation.

## A standing offer of help

If you would like to use Registry Forge with your own registry, your own EHR vendor, or your own disease area &mdash; and you run into something that doesn't behave as expected, or you would like help adapting the seed dictionaries and regex patterns to your data &mdash; please reach out. We are genuinely glad to help, and we are looking to validate this pipeline against deployments beyond ARC.

Contact: <a href="mailto:dboyce@als.net">dboyce@als.net</a>

For related tools (CDAtransformer) and training (the ALS TDI Real World Evidence Resources hub, the *Guide to Real-World Data for Clinical Research* book, and the OMOP introductory course), see the [Resources &amp; related tools](resources.md) page.

## Acknowledgements

Registry Forge stands on the shoulders of substantial open community work. See the [External references and standards](external-references.md) page for the full list. Particular thanks to:

- The [OHDSI community](https://www.ohdsi.org/) for the OMOP Common Data Model and the [Athena](https://athena.ohdsi.org/) vocabulary service
- The [GA4GH](https://www.ga4gh.org/) consortium and the [Phenopackets](https://phenopacket-schema.readthedocs.io/) working group
- The [Monarch Initiative](https://monarchinitiative.org/) for [Mondo](https://mondo.monarchinitiative.org/) and the [Human Phenotype Ontology](https://hpo.jax.org/)
- The [SMART Health IT](https://smarthealthit.org/) team at Boston Children's Hospital / Harvard Medical School
- [Reimer et al. (2026)](https://doi.org/10.1016/j.landig.2025.100963) whose ALS-VHA drug repurposing methodology is adapted in the drug repurposing add-on module
- The [FDA / NCATS-NIH / Critical Path Institute CURE Drug Repurposing Collaboratory](https://cure.ncats.io/) whose intake schema the drug repurposing add-on writes to

And, above all, the ARC Study participants and their families &mdash; whose decision to share their clinical records made every line of this code worth writing.
