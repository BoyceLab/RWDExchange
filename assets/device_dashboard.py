"""
device_dashboard.py — single-file HTML dashboard for viewing the outputs
of device_extraction.py.

Reads:
  device_codes.csv         (structured DEVICE_CODES matches)
  device_extractions.csv   (regex matches against narrative text)

Produces:
  device_dashboard.html    (self-contained, no external deps; open in any browser)

Privacy controls (baked in):
  * patient_id          -> PT-NNNN pseudonyms (stable within one run)
  * dates               -> year only
  * snippet text        -> truncated to 200 chars
  * device / pattern with fewer than k unique patients is suppressed
"""

import csv
import json
import os
import re
import sys
import datetime as _dt
from collections import defaultdict, Counter

DEFAULT_K_ANONYMITY = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pseudo_id(pid, cache):
    if not pid:
        pid = '(no-id)'
    if pid not in cache:
        cache[pid] = f'PT-{len(cache)+1:04d}'
    return cache[pid]


def _year_only(date_str):
    if not date_str:
        return ''
    m = re.match(r'(\d{4})', str(date_str))
    return m.group(1) if m else ''


def _truncate(s, n=200):
    if not s:
        return ''
    s = str(s).strip()
    return s if len(s) <= n else s[:n-1] + '…'


def _esc(s):
    """HTML-escape a string."""
    if s is None:
        return ''
    return (str(s)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data(codes_csv_path, extractions_csv_path):
    """Load both CSVs. Either may be missing — returns empty list if so."""
    codes_rows = []
    if os.path.exists(codes_csv_path):
        with open(codes_csv_path, encoding='utf-8-sig') as f:
            codes_rows = list(csv.DictReader(f))
    ext_rows = []
    if os.path.exists(extractions_csv_path):
        with open(extractions_csv_path, encoding='utf-8-sig') as f:
            ext_rows = list(csv.DictReader(f))
    return codes_rows, ext_rows


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------
def compute_summary(codes_rows, ext_rows, k=DEFAULT_K_ANONYMITY):
    """Aggregate into the structures the renderer needs."""
    pseudo = {}

    # By device-category (e.g. als_care_indicator_respiratory, peg_tube, ...)
    cat_to_pts = defaultdict(set)
    cat_to_records = Counter()
    cat_to_source = defaultdict(set)  # 'coded' / 'text'

    # By device label / regex pattern
    label_to_pts = defaultdict(set)
    label_to_records = Counter()
    label_to_category = {}
    label_to_source = defaultdict(set)

    # By patient (for patient-matrix view)
    patient_to_cat_count = defaultdict(lambda: defaultdict(int))
    patient_to_total = defaultdict(int)

    # Year of records, for time distribution
    year_to_records = Counter()

    for r in codes_rows:
        pid = r.get('patient_id', '')
        pseudo_id = _pseudo_id(pid, pseudo)
        cat = (r.get('category') or 'other').strip()
        label = (r.get('forge_label') or r.get('code') or '').strip() or '(unlabeled)'
        cat_to_pts[cat].add(pseudo_id)
        cat_to_records[cat] += 1
        cat_to_source[cat].add('coded')
        label_to_pts[label].add(pseudo_id)
        label_to_records[label] += 1
        label_to_category[label] = cat
        label_to_source[label].add('coded')
        patient_to_cat_count[pseudo_id][cat] += 1
        patient_to_total[pseudo_id] += 1
        yr = _year_only(r.get('effective_date'))
        if yr:
            year_to_records[yr] += 1

    for r in ext_rows:
        pid = r.get('patient_id', '')
        pseudo_id = _pseudo_id(pid, pseudo)
        cat = (r.get('category') or 'other').strip()
        label = (r.get('pattern') or r.get('description') or '').strip() or '(unlabeled)'
        cat_to_pts[cat].add(pseudo_id)
        cat_to_records[cat] += 1
        cat_to_source[cat].add('text')
        label_to_pts[label].add(pseudo_id)
        label_to_records[label] += 1
        label_to_category[label] = cat
        label_to_source[label].add('text')
        patient_to_cat_count[pseudo_id][cat] += 1
        patient_to_total[pseudo_id] += 1

    # k-anonymity at the category level
    categories = []
    for cat, pts in sorted(cat_to_pts.items(), key=lambda kv: -len(kv[1])):
        if len(pts) < k:
            continue
        sources = sorted(cat_to_source[cat])
        categories.append({
            'category':   cat,
            'n_patients': len(pts),
            'n_records':  cat_to_records[cat],
            'source':     '+'.join(sources),
        })

    # k-anonymity at the label level
    labels = []
    for label, pts in sorted(label_to_pts.items(), key=lambda kv: -len(kv[1])):
        if len(pts) < k:
            continue
        labels.append({
            'label':      label,
            'category':   label_to_category[label],
            'source':     '+'.join(sorted(label_to_source[label])),
            'n_patients': len(pts),
            'n_records':  label_to_records[label],
        })

    # Patient matrix — only include categories that survive k-anon
    surviving_cats = [c['category'] for c in categories]
    cell_index = {}
    for pseudo_id, cats in patient_to_cat_count.items():
        for cat, n in cats.items():
            if cat in surviving_cats:
                cell_index[f'{pseudo_id}|{cat}'] = n
    patient_list = sorted(patient_to_total.keys(),
                          key=lambda p: -patient_to_total[p])

    # Year distribution
    years_sorted = sorted(year_to_records.items())

    return {
        'n_patients_total':       len(pseudo),
        'n_codes_rows':           len(codes_rows),
        'n_extractions_rows':     len(ext_rows),
        'categories':             categories,
        'labels':                 labels,
        'patients':               patient_list,
        'patient_to_total':       dict(patient_to_total),
        'patient_cell_index':     cell_index,
        'surviving_cats':         surviving_cats,
        'years':                  years_sorted,
    }


def collect_snippets(ext_rows, k=DEFAULT_K_ANONYMITY, max_examples=3):
    """For each regex pattern with >= k unique patients, take up to
    max_examples representative snippets, prioritizing patient diversity."""
    pseudo = {}
    pat_to_pts = defaultdict(set)
    pat_to_examples = defaultdict(list)
    seen_pt_per_pat = defaultdict(set)

    for r in ext_rows:
        pat = (r.get('pattern') or 'unknown').strip()
        pid = r.get('patient_id', '')
        pseudo_id = _pseudo_id(pid, pseudo)
        pat_to_pts[pat].add(pseudo_id)

    for r in ext_rows:
        pat = (r.get('pattern') or 'unknown').strip()
        if len(pat_to_pts[pat]) < k:
            continue
        pid = r.get('patient_id', '')
        pseudo_id = _pseudo_id(pid, pseudo)
        if pseudo_id in seen_pt_per_pat[pat]:
            continue
        if len(seen_pt_per_pat[pat]) >= max_examples:
            continue
        seen_pt_per_pat[pat].add(pseudo_id)
        pat_to_examples[pat].append({
            'pseudo_id':   pseudo_id,
            'source_kind': r.get('source_kind', ''),
            'value':       _truncate(r.get('value'), 80),
            'snippet':     _truncate(r.get('snippet'), 200),
            'description': r.get('description', ''),
            'category':    (r.get('category') or '').strip(),
        })

    return sorted([{
        'pattern':    pat,
        'category':   pat_to_examples[pat][0]['category'] if pat_to_examples[pat] else '',
        'description': pat_to_examples[pat][0]['description'] if pat_to_examples[pat] else '',
        'n_patients': len(pat_to_pts[pat]),
        'examples':   pat_to_examples[pat],
    } for pat in pat_to_examples], key=lambda x: -x['n_patients'])


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
CSS = """
:root {
  --bg:          #f7f6f1;
  --panel:       #ffffff;
  --text:        #1e2433;
  --muted:       #5f6478;
  --navy:        #1e3a5f;
  --purple:      #4a148c;
  --teal:        #0f6e56;
  --amber:       #854f0b;
  --indigo:      #3949ab;
  --border:      #e3e0d5;
  --header-grad: linear-gradient(135deg, #1e3a5f 0%, #4a148c 100%);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: system-ui, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
}
header {
  background: var(--header-grad);
  color: #fff;
  padding: 28px 32px 22px 32px;
}
header h1 { margin: 0 0 6px 0; font-size: 24px; }
header h1 .forge { color: #a3b5ff; }
header .meta { color: #d4cbf0; font-size: 13px; }
header .meta strong { color: #fff; }
.summary-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  padding: 20px 32px 8px 32px;
}
.stat {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px 18px;
}
.stat .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
.stat .value { font-size: 26px; font-weight: 600; color: var(--navy); margin-top: 4px; }
.stat .sub   { font-size: 12px; color: var(--muted); margin-top: 4px; }
nav.tabs {
  display: flex;
  gap: 4px;
  padding: 10px 32px 0 32px;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
}
nav.tabs button {
  background: transparent;
  border: none;
  padding: 12px 20px 14px 20px;
  font-size: 14px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  border-bottom: 3px solid transparent;
}
nav.tabs button.active {
  color: var(--navy);
  border-bottom-color: var(--purple);
}
nav.tabs button:hover { color: var(--navy); }
main {
  padding: 24px 32px 60px 32px;
  max-width: 1400px;
}
.tabpanel { display: none; }
.tabpanel.active { display: block; }
section { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 22px 26px; margin-bottom: 22px; }
section h2 { margin: 0 0 14px 0; font-size: 16px; color: var(--navy); }
section h3 { margin: 18px 0 10px 0; font-size: 14px; color: var(--purple); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--border); }
th { background: #faf8f1; font-weight: 600; color: var(--navy); font-size: 12px; text-transform: uppercase; letter-spacing: 0.02em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.cat { font-size: 11px; color: var(--muted); }
.bar-row { display: flex; align-items: center; gap: 10px; }
.bar-row .barwrap { flex: 1; height: 14px; background: #ece9dc; border-radius: 3px; overflow: hidden; }
.bar-row .bar { height: 100%; background: linear-gradient(90deg, var(--navy), var(--purple)); }
.bar-row .barlabel { font-variant-numeric: tabular-nums; font-size: 12px; color: var(--muted); width: 60px; text-align: right; }
.matrix-cell {
  display: inline-block;
  min-width: 36px;
  padding: 3px 6px;
  border-radius: 3px;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-align: center;
}
.matrix-cell.empty { background: #f5f3ea; color: #c0bca8; }
.matrix-cell.low   { background: #e3eef9; color: #1e3a5f; }
.matrix-cell.med   { background: #b8d3ee; color: #0d2545; }
.matrix-cell.high  { background: #4a148c; color: #fff; }
.snippet { background: #fafaf3; border-left: 3px solid var(--purple); padding: 8px 12px; margin: 6px 0; font-size: 13px; line-height: 1.55; }
.snippet .meta { color: var(--muted); font-size: 11px; margin-bottom: 4px; }
.snippet .text { color: var(--text); }
.snippet .value { color: var(--purple); font-weight: 600; }
.source-coded { color: var(--teal); }
.source-text  { color: var(--amber); }
.source-both  { color: var(--purple); }
input.filter { padding: 7px 10px; border: 1px solid var(--border); border-radius: 4px; width: 240px; font-size: 13px; }
"""

JS = """
function showTab(name) {
  document.querySelectorAll('.tabpanel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav.tabs button').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  document.querySelector('nav.tabs button[data-tab=' + name + ']').classList.add('active');
}
function filterTable(input, tableId) {
  const q = input.value.toLowerCase();
  const tbody = document.querySelector('#' + tableId + ' tbody');
  if (!tbody) return;
  for (const row of tbody.rows) {
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  }
}
"""


def _render_categories(summary):
    if not summary['categories']:
        return '<p style="color: var(--muted);">No device categories survived k-anonymity threshold.</p>'
    max_pts = max(c['n_patients'] for c in summary['categories'])
    rows = []
    for c in summary['categories']:
        pct = 100 * c['n_patients'] / max(max_pts, 1)
        rows.append(
            f'<tr>'
            f'<td>{_esc(c["category"])}</td>'
            f'<td class="num">{c["n_patients"]}</td>'
            f'<td><div class="bar-row"><div class="barwrap"><div class="bar" style="width:{pct:.1f}%"></div></div></div></td>'
            f'<td class="num">{c["n_records"]:,}</td>'
            f'<td><span class="source-{c["source"].replace("+","-")}">{_esc(c["source"])}</span></td>'
            f'</tr>')
    return (
        '<table><thead><tr>'
        '<th>Category</th><th>Unique patients</th><th></th>'
        '<th>Total records</th><th>Source</th>'
        '</tr></thead><tbody>'
        + ''.join(rows)
        + '</tbody></table>')


def _render_labels(summary):
    if not summary['labels']:
        return '<p style="color: var(--muted);">No device labels survived k-anonymity threshold.</p>'
    rows = []
    for L in summary['labels']:
        rows.append(
            f'<tr>'
            f'<td>{_esc(L["label"])}</td>'
            f'<td class="cat">{_esc(L["category"])}</td>'
            f'<td class="num">{L["n_patients"]}</td>'
            f'<td class="num">{L["n_records"]:,}</td>'
            f'<td><span class="source-{L["source"].replace("+","-")}">{_esc(L["source"])}</span></td>'
            f'</tr>')
    return (
        '<input class="filter" placeholder="Filter labels or categories..." '
        'oninput="filterTable(this, \'labels-tbl\')">'
        '<table id="labels-tbl" style="margin-top:10px"><thead><tr>'
        '<th>Device / indicator</th><th>Category</th>'
        '<th>Unique patients</th><th>Records</th><th>Source</th>'
        '</tr></thead><tbody>'
        + ''.join(rows)
        + '</tbody></table>')


def _render_matrix(summary):
    cats = summary['surviving_cats']
    patients = summary['patients']
    if not cats or not patients:
        return '<p style="color: var(--muted);">No data to display.</p>'
    cell = summary['patient_cell_index']

    # Cap displayed patients to top 40 by total
    pts_shown = patients[:40]
    out = ['<div style="overflow-x: auto;"><table style="font-size: 12px"><thead><tr>',
           '<th style="position: sticky; left: 0; background: #faf8f1;">Patient</th>']
    for c in cats:
        out.append(f'<th style="font-size: 10px; text-transform: none;">{_esc(c)}</th>')
    out.append('</tr></thead><tbody>')
    for p in pts_shown:
        out.append(f'<tr><td style="position: sticky; left: 0; background: var(--panel); '
                   f'font-weight: 600;">{_esc(p)}</td>')
        for c in cats:
            n = cell.get(f'{p}|{c}', 0)
            klass = 'empty' if n == 0 else ('low' if n < 5 else ('med' if n < 20 else 'high'))
            disp = '·' if n == 0 else str(n)
            out.append(f'<td><span class="matrix-cell {klass}">{disp}</span></td>')
        out.append('</tr>')
    out.append('</tbody></table></div>')
    if len(patients) > 40:
        out.append(f'<p style="color: var(--muted); margin-top: 12px;">Showing top 40 of '
                   f'{len(patients)} patients with any device record. Values are record counts '
                   f'per category (· = none).</p>')
    return ''.join(out)


def _render_snippets(snippet_groups):
    if not snippet_groups:
        return '<p style="color: var(--muted);">No regex extractions to display.</p>'
    out = []
    for g in snippet_groups:
        out.append(f'<h3>{_esc(g["pattern"])} '
                   f'<span style="color: var(--muted); font-weight: normal; font-size: 12px;">'
                   f'{g["n_patients"]} unique patients · category '
                   f'<code>{_esc(g["category"] or "—")}</code></span></h3>')
        if g["description"]:
            out.append(f'<p style="color: var(--muted); font-size: 12px;">{_esc(g["description"])}</p>')
        for ex in g["examples"]:
            out.append(
                f'<div class="snippet">'
                f'<div class="meta">{_esc(ex["pseudo_id"])} · '
                f'<code>{_esc(ex["source_kind"])}</code> · matched '
                f'<span class="value">{_esc(ex["value"])}</span></div>'
                f'<div class="text">{_esc(ex["snippet"])}</div>'
                f'</div>')
    return ''.join(out)


def render_html(summary, snippet_groups, cohort_name='ARC EHR cohort',
                k=DEFAULT_K_ANONYMITY):
    now = _dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    title = 'Device & Equipment Dashboard'
    n_cat = len(summary['categories'])
    n_label = len(summary['labels'])
    n_pt = summary['n_patients_total']

    body = f"""
<header>
  <h1>Registry <span class="forge">Forge</span> — {_esc(title)}</h1>
  <div class="meta">Cohort: <strong>{_esc(cohort_name)}</strong> &nbsp;·&nbsp;
       Generated {_esc(now)} &nbsp;·&nbsp;
       k-anonymity threshold k={k}</div>
</header>

<div class="summary-row">
  <div class="stat">
    <div class="label">Patients with devices</div>
    <div class="value">{n_pt:,}</div>
    <div class="sub">across all categories</div>
  </div>
  <div class="stat">
    <div class="label">Device categories</div>
    <div class="value">{n_cat:,}</div>
    <div class="sub">after k≥{k} filter</div>
  </div>
  <div class="stat">
    <div class="label">Distinct devices/indicators</div>
    <div class="value">{n_label:,}</div>
    <div class="sub">after k≥{k} filter</div>
  </div>
  <div class="stat">
    <div class="label">Source records</div>
    <div class="value">{summary['n_codes_rows'] + summary['n_extractions_rows']:,}</div>
    <div class="sub">{summary['n_codes_rows']:,} coded · {summary['n_extractions_rows']:,} text</div>
  </div>
</div>

<nav class="tabs">
  <button data-tab="overview" class="active" onclick="showTab('overview')">Overview by category</button>
  <button data-tab="labels"   onclick="showTab('labels')">All devices / indicators</button>
  <button data-tab="matrix"   onclick="showTab('matrix')">Patient × category matrix</button>
  <button data-tab="snippets" onclick="showTab('snippets')">Source-record snippets</button>
  <button data-tab="about"    onclick="showTab('about')">About</button>
</nav>

<main>
  <div id="panel-overview" class="tabpanel active">
    <section>
      <h2>Device & care-pathway categories</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        Each row aggregates the patients and records associated with one device category.
        "Source" indicates whether matches came from structured codes
        (<span class="source-coded">coded</span>),
        free-text regex (<span class="source-text">text</span>), or both
        (<span class="source-both">coded+text</span>).
      </p>
      {_render_categories(summary)}
    </section>
  </div>

  <div id="panel-labels" class="tabpanel">
    <section>
      <h2>All devices / indicators</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        Per-label aggregate. Use the filter box to narrow down by name or category.
      </p>
      {_render_labels(summary)}
    </section>
  </div>

  <div id="panel-matrix" class="tabpanel">
    <section>
      <h2>Patient × device-category matrix</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        Patient identifiers are PT-NNNN pseudonyms. Cell shows the number of
        records for that patient in that category; · = none. Categories with
        fewer than k={k} unique patients are suppressed entirely.
      </p>
      {_render_matrix(summary)}
    </section>
  </div>

  <div id="panel-snippets" class="tabpanel">
    <section>
      <h2>Source-record snippets</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        Up to 3 representative text excerpts per regex pattern, prioritizing
        unique-patient diversity. Snippet text truncated to 200 chars. For
        chart-review preparation and source verification; not for analysis.
      </p>
      {_render_snippets(snippet_groups)}
    </section>
  </div>

  <div id="panel-about" class="tabpanel">
    <section>
      <h2>About this dashboard</h2>
      <p>This is a privacy-safe summary of the device-extraction outputs from
      <code>device_extraction.py</code>. It combines two underlying CSVs:</p>
      <ul>
        <li><code>device_codes.csv</code> — structured matches against the
          DEVICE_CODES lookup table (HCPCS / CPT-4 / SNOMED-CT codes for
          devices and ALS-care-indicator procedures).</li>
        <li><code>device_extractions.csv</code> — regex matches against
          <code>notes[].narrative_text</code> and <code>documents[].plain_text</code>
          using DEVICE_PATTERNS (PEG, BiPAP, tracheostomy, cough assist,
          power wheelchair, AAC/SGD brand names, etc.).</li>
      </ul>
      <p><strong>Privacy controls applied throughout:</strong></p>
      <ul>
        <li>Patient identifiers are replaced with PT-NNNN pseudonyms, stable within this
          run.</li>
        <li>Calendar dates are reduced to year only.</li>
        <li>Snippet text is truncated to 200 characters.</li>
        <li>Devices, patterns, and categories with fewer than k={k} unique
          exposed patients are suppressed entirely (k-anonymity at the
          device level).</li>
        <li>Resource UUIDs are never emitted.</li>
      </ul>
      <p><strong>Important caveat about ARC data:</strong> SMART on FHIR pulls
      from the clinical EHR rarely capture DME-supplier records, so direct
      device-procurement codes (E0470 BiPAP, K0813 power wheelchair, B4034
      gastrostomy supplies) are typically absent from structured procedure
      records. The structured side of this dashboard surfaces mostly
      <em>ALS-care-indicator</em> procedures &mdash; spirometry, sleep studies,
      EMG/NCS, speech screening, botulinum toxin, PT/OT re-evaluations, and
      mobility/self-care status reporting &mdash; that signal where the
      patient is on the typical ALS-care pathway. Direct device presence is
      typically captured by the regex side from clinical notes.</p>
    </section>
  </div>
</main>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — {_esc(cohort_name)}</title>
<style>{CSS}</style>
</head>
<body>
{body}
<script>{JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main(codes_csv_path='./device_codes.csv',
         extractions_csv_path='./device_extractions.csv',
         out_path='./device_dashboard.html',
         cohort_name='ARC EHR cohort',
         k=DEFAULT_K_ANONYMITY):
    """End-to-end. Reads the two CSVs, aggregates, writes a single HTML."""
    ts = _dt.datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] device_dashboard — building from:')
    print(f'    codes:       {codes_csv_path}')
    print(f'    extractions: {extractions_csv_path}')

    codes_rows, ext_rows = load_data(codes_csv_path, extractions_csv_path)
    print(f'    Loaded {len(codes_rows):,} code rows and {len(ext_rows):,} extraction rows')

    summary = compute_summary(codes_rows, ext_rows, k=k)
    snippets = collect_snippets(ext_rows, k=k, max_examples=3)

    html = render_html(summary, snippets, cohort_name=cohort_name, k=k)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    ts = _dt.datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] Wrote {out_path} ({os.path.getsize(out_path):,} bytes)')
    print(f'    Patients with devices:     {summary["n_patients_total"]:,}')
    print(f'    Device categories shown:   {len(summary["categories"])}')
    print(f'    Devices / indicators:      {len(summary["labels"])}')
    print(f'    Snippet groups:            {len(snippets)}')
    return out_path


if __name__ == '__main__':
    main()
