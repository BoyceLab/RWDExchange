# Dashboard

The pipeline ships with a single-file HTML dashboard for browsing the
output bundle. No server, no install -- just open the HTML in any modern
browser and load the JSON.

## Loading

1. Open `dashboard.html` in your browser (Chrome, Firefox, Safari,
   Edge -- anything modern).
2. Click the file picker.
3. Select `dashboard_data.json`.

The dashboard parses the JSON entirely client-side. Nothing leaves your
machine.

## Two view modes

### Browse Patients

The default. Patients listed in the sidebar (sortable, filterable by
named/unnamed, searchable). Click a patient to see their tabs:

- **Docs** -- every document linked to this patient, with format filter
  buttons (CCDA / HTML / RTF / PDF). The "Hide empty PDFs" toggle is on
  by default.
- **Visits** -- encounters with date, type, class, status.
- **Problems** -- conditions with onset date, code, clinical status.
- **Meds** -- medications with authored-on date, code system, dose.
- **Procs** -- procedures.
- **Labs/Obs** -- combined labs + vitals with value, unit, code.
- **Allergies** -- with severity and clinical status.
- **Vaccines** -- with date.
- **Careplans** -- with title and status.
- **Reports** -- diagnostic reports with conclusion.
- **Goals** -- with status.
- **Sections** -- CCDA section narratives with full text body.

Each tab has an "Export CSV" button that exports just that tab for the
current patient. CSVs include the patient's name, MRN, and DOB columns
prepended.

### Keyword Search

Toggle in the sidebar header. Type a term and the dashboard searches
across:

- Every document body (CCDA, RTF, HTML, PDF text).
- Every CCDA section narrative.
- Every clinical record's display name, code, and free-text fields.

Hits show up in the main panel grouped by tab category, each with:

- Tab type (Med, Problem, Document, Section, etc.)
- Patient name and MRN
- Date
- A snippet around the matched keyword, with the match highlighted.

The sidebar shows patients ordered by hit count. Click any hit to jump to
that patient's record.

The "Export hits" button writes a CSV containing every hit with patient
name, tab category, date, code, display name, source file, and snippet.

## Cohort overview

Click "Cohort overview" in the sidebar to see all patients as a table
with columns for each clinical category and document count.

## Sharing the dashboard

Because it's a single HTML file plus a single JSON file, sharing is just
"send these two files". The recipient opens the HTML, picks the JSON, and
gets the same view -- no install, no API keys, no deployment.

## Customizing

The dashboard is plain HTML/CSS/JS in one file (~60 KB). Common tweaks:

- **Changing tab columns**: edit the `cols` array in the corresponding
  `TABS` entry near the top of the `<script>` block.
- **Changing the color palette**: edit the `:root` CSS variables in the
  `<style>` block (`--accent`, `--accent2`, `--bg`).
- **Adding a tab**: add an entry to `TABS` and ensure the bundle has a
  matching key.
