# RWDExchange

**Live site → [boyceLab.github.io/RWDExchange](https://boyceLab.github.io/RWDExchange/)**

RWDExchange is a browser-based tool for evaluating the exchangeability potential of real-world data (RWD) — including electronic health records and patient registries — for use as external comparators in clinical trials.

No installation, no server, no login. All assessment data is stored locally in your browser.

---

## Features

- **Variable Assessment** — Evaluate each candidate variable with six structured exchangeability questions generating an automatic 0–6 feasibility score. Optionally document data quality metrics (N, % missing, date range) and planned confounders/analytical approach.
- **Pocock's Criteria** — Assess all seven Pocock (1976) historical control criteria with ratings and notes per criterion.
- **FDA Guidance** — Evaluate alignment with all eight considerations from the FDA's 2023 guidance on externally controlled trials.
- **Gray et al. Framework** — Complete the eight methodological domains from Gray et al. (*Drug Saf.* 2020), covering research question fit, population representativeness, outcome measurement, confounder availability, analytical approach, and reproducibility.
- **Composite Verdict** — A weighted score synthesized across all four frameworks, displayed as a live progress ring (Strong Potential / Conditional / Limited / Preliminary).
- **Progress Tracking** — Per-variable completion pills (POC · FDA · GRY) show at a glance what still needs evaluation.
- **Download Report** — Export variables, Pocock, FDA, and Gray assessments as separate CSV files or a full combined report.
- **JSON Import / Export** — Back up and restore your full session, or hand off to a colleague.
- **Shareable URL** — Encode your entire session into a URL hash for instant sharing — no server required.
- **Print View** — Clean print stylesheet for protocol appendices or regulatory packages.
- **OMOP Data Scan** *(in development)* — Automated data characterization using OMOP concept codes.

---

## Deployment

This is a fully static single-file site. No build step required.

### GitHub Pages (recommended)

1. Copy `index.html` and `.nojekyll` to the root of this repository on the `main` branch
2. Go to **Settings → Pages → Source** → select `main` branch, `/ (root)`
3. Site will be live at `https://boyceLab.github.io/RWDExchange/` within ~2 minutes

### Local use

Just open `index.html` in any modern browser. No server needed.

---

## Data & Privacy

All data is stored exclusively in your browser's `localStorage`. Nothing is transmitted to any server. Use **Export JSON** to back up your session; clearing browser data will erase it.

---

## References

- Gray CM, Grimson F, Layton D, Pocock S, Kim J. A Framework for Methodological Choice and Evidence Assessment for Studies Using External Comparators from Real-World Data. *Drug Saf.* 2020 Jul;43(7):623-633. doi: [10.1007/s40264-020-00944-1](https://doi.org/10.1007/s40264-020-00944-1)
- Pocock SJ. The combination of randomized and historical controls in clinical trials. *J Chronic Dis.* 1976 Mar;29(3):175-88. doi: [10.1016/0021-9681(76)90044-8](https://doi.org/10.1016/0021-9681(76)90044-8)
- US Food and Drug Administration. "Considerations for the Design and Conduct of Externally Controlled Trials for Drug and Biological Products." February 2023. [View guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/considerations-design-and-conduct-externally-controlled-trials-drug-and-biological-products)

---

## License

MIT — see [LICENSE](LICENSE)

## Contact

Developed by Danielle Boyce, MPH, DPA · [danielle@boycedatascience.com](mailto:danielle@boycedatascience.com)
