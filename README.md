# RWDExchange

**Live site → [boyceLab.github.io/RWDExchange](https://boyceLab.github.io/RWDExchange/)**  
**Demo → [boyceLab.github.io/RWDExchange/demo.html](https://boyceLab.github.io/RWDExchange/demo.html)**

RWDExchange is a browser-based tool for evaluating the exchangeability potential of real-world data (RWD) — including electronic health records and patient registries — for use as external comparators in clinical trials.

No installation, no server, no login. All assessment data is stored locally in your browser.

---

## Features

- **Variable Assessment** — Structured six-question exchangeability scoring (0–6); optional data quality metrics and confounder documentation
- **Pocock's Criteria** — Seven criteria for acceptable historical controls (Pocock, 1976)
- **FDA Guidance** — Eight considerations from the FDA 2023 externally controlled trials guidance
- **Gray et al. Framework** — Eight methodological domains (Gray et al., *Drug Saf.* 2020)
- **Composite Verdict** — Weighted synthesis across all four frameworks with live progress ring
- **Progress Tracking** — Per-variable POC · FDA · GRY completion pills
- **Export** — CSV (per-framework or combined) and JSON backup/restore
- **Shareable URL** — Full session encoded in URL hash for easy sharing
- **Print View** — Clean print stylesheet for regulatory appendices
- **Demo Mode** — `demo.html` pre-loaded with synthetic ALS external comparator data

---

## Deployment

### GitHub Pages (recommended)

1. Copy `index.html`, `demo.html`, and `.nojekyll` to the root of this repository on the `main` branch
2. Go to **Settings → Pages → Source** → select `main` branch, `/ (root)`
3. Site will be live at `https://boyceLab.github.io/RWDExchange/` within ~2 minutes

### Local use

Open `index.html` in any modern browser. No server needed.

---

## Files

| File | Purpose |
|---|---|
| `index.html` | Main application — clean workspace, your own data |
| `demo.html` | Pre-loaded with synthetic ALS trial data for exploration |
| `.nojekyll` | Prevents GitHub Pages from running Jekyll on these files |

---

## Data & Privacy

All data is stored exclusively in your browser's `localStorage`. Nothing is transmitted to any server. `index.html` and `demo.html` use separate storage namespaces so demo data never interferes with your real assessments.

---

## References

- Gray CM, Grimson F, Layton D, Pocock S, Kim J. A Framework for Methodological Choice and Evidence Assessment for Studies Using External Comparators from Real-World Data. *Drug Saf.* 2020;43(7):623-633. doi: [10.1007/s40264-020-00944-1](https://doi.org/10.1007/s40264-020-00944-1)
- Pocock SJ. The combination of randomized and historical controls in clinical trials. *J Chronic Dis.* 1976;29(3):175-88. doi: [10.1016/0021-9681(76)90044-8](https://doi.org/10.1016/0021-9681(76)90044-8)
- US Food and Drug Administration. "Considerations for the Design and Conduct of Externally Controlled Trials for Drug and Biological Products." February 2023.

---

Developed by Danielle Boyce, MPH, DPA · [danielle@boycedatascience.com](mailto:danielle@boycedatascience.com) · MIT License
