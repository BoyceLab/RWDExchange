"""
exposure_dashboard.py — single-file HTML dashboard for viewing the
outputs of exposure_extraction.py.

Reads:
  exposure_codes.csv         (ICD-10-CM occupational/environmental codes)
  exposure_extractions.csv   (regex matches against narrative text)

Produces:
  exposure_dashboard.html    (self-contained; open in any browser)

Privacy controls (baked in):
  * patient_id          -> PT-NNNN pseudonyms
  * dates               -> year only
  * snippet text        -> truncated to 200 chars
  * category / pattern with < k unique patients suppressed (default k=2)

Tabs:
  1. Overview by category — bar chart of exposure categories
  2. All exposures / indicators — filterable table
  3. Patient × category matrix — heatmap
  4. Source-record snippets — sample regex matches
  5. ECTO mapping — which categories map to which ECTO terms
  6. About — methodology, privacy controls, ALS-literature context
"""

import csv
import json
import os
import re
import sys
import datetime as _dt
from collections import defaultdict, Counter

# Import the ECTO category map from exposure_extraction. Falls back to an
# empty dict if exposure_extraction isn't importable yet so the dashboard
# can still render.
try:
    from exposure_extraction import ECTO_CATEGORY_MAP
except ImportError:
    ECTO_CATEGORY_MAP = {}


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


def _year_only(s):
    if not s: return ''
    m = re.match(r'(\d{4})', str(s))
    return m.group(1) if m else ''


def _truncate(s, n=200):
    if not s: return ''
    s = str(s).strip()
    return s if len(s) <= n else s[:n-1] + '…'


def _esc(s):
    if s is None: return ''
    return (str(s)
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;').replace("'", '&#39;'))


def load_data(codes_csv, ext_csv):
    codes, ext = [], []
    if os.path.exists(codes_csv):
        with open(codes_csv, encoding='utf-8-sig') as f:
            codes = list(csv.DictReader(f))
    if os.path.exists(ext_csv):
        with open(ext_csv, encoding='utf-8-sig') as f:
            ext = list(csv.DictReader(f))
    return codes, ext


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------
def compute_summary(codes_rows, ext_rows, k=DEFAULT_K_ANONYMITY):
    pseudo = {}
    cat_to_pts = defaultdict(set)
    cat_to_records = Counter()
    cat_to_source = defaultdict(set)
    label_to_pts = defaultdict(set)
    label_to_records = Counter()
    label_to_category = {}
    label_to_source = defaultdict(set)
    patient_to_cat_count = defaultdict(lambda: defaultdict(int))
    patient_to_total = defaultdict(int)

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

    categories = []
    for cat, pts in sorted(cat_to_pts.items(), key=lambda kv: -len(kv[1])):
        if len(pts) < k: continue
        categories.append({
            'category':   cat,
            'n_patients': len(pts),
            'n_records':  cat_to_records[cat],
            'source':     '+'.join(sorted(cat_to_source[cat])),
            'ecto':       ECTO_CATEGORY_MAP.get(cat, {}),
        })

    labels = []
    for L, pts in sorted(label_to_pts.items(), key=lambda kv: -len(kv[1])):
        if len(pts) < k: continue
        labels.append({
            'label':      L,
            'category':   label_to_category[L],
            'source':     '+'.join(sorted(label_to_source[L])),
            'n_patients': len(pts),
            'n_records':  label_to_records[L],
        })

    surviving = [c['category'] for c in categories]
    cell_index = {}
    for pseudo_id, cats in patient_to_cat_count.items():
        for cat, n in cats.items():
            if cat in surviving:
                cell_index[f'{pseudo_id}|{cat}'] = n
    patient_list = sorted(patient_to_total.keys(),
                           key=lambda p: -patient_to_total[p])

    return {
        'n_patients_total':   len(pseudo),
        'n_codes_rows':       len(codes_rows),
        'n_extractions_rows': len(ext_rows),
        'categories':         categories,
        'labels':             labels,
        'patients':           patient_list,
        'patient_to_total':   dict(patient_to_total),
        'patient_cell_index': cell_index,
        'surviving_cats':     surviving,
    }


def collect_snippets(ext_rows, k=DEFAULT_K_ANONYMITY, max_examples=3):
    pseudo = {}
    pat_to_pts = defaultdict(set)
    for r in ext_rows:
        pat = (r.get('pattern') or 'unknown').strip()
        pseudo_id = _pseudo_id(r.get('patient_id', ''), pseudo)
        pat_to_pts[pat].add(pseudo_id)

    pat_to_examples = defaultdict(list)
    seen_per_pat = defaultdict(set)
    for r in ext_rows:
        pat = (r.get('pattern') or 'unknown').strip()
        if len(pat_to_pts[pat]) < k: continue
        pseudo_id = _pseudo_id(r.get('patient_id', ''), pseudo)
        if pseudo_id in seen_per_pat[pat]: continue
        if len(seen_per_pat[pat]) >= max_examples: continue
        seen_per_pat[pat].add(pseudo_id)
        pat_to_examples[pat].append({
            'pseudo_id':   pseudo_id,
            'source_kind': r.get('source_kind', ''),
            'value':       _truncate(r.get('value'), 80),
            'snippet':     _truncate(r.get('snippet'), 200),
            'description': r.get('description', ''),
            'category':    (r.get('category') or '').strip(),
            'ecto_seed':   r.get('ecto_seed', ''),
        })

    return sorted([{
        'pattern':    pat,
        'category':   pat_to_examples[pat][0]['category'] if pat_to_examples[pat] else '',
        'description': pat_to_examples[pat][0]['description'] if pat_to_examples[pat] else '',
        'ecto_seed':   pat_to_examples[pat][0]['ecto_seed'] if pat_to_examples[pat] else '',
        'n_patients': len(pat_to_pts[pat]),
        'examples':   pat_to_examples[pat],
    } for pat in pat_to_examples], key=lambda x: -x['n_patients'])


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
CSS = """
:root {
  --bg:#f7f6f1; --panel:#fff; --text:#1e2433; --muted:#5f6478;
  --navy:#1e3a5f; --purple:#4a148c; --teal:#0f6e56; --amber:#854f0b;
  --indigo:#3949ab; --border:#e3e0d5;
  --header-grad: linear-gradient(135deg, #0f6e56 0%, #1e3a5f 50%, #4a148c 100%);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, "Segoe UI", "Helvetica Neue", Arial, sans-serif; background: var(--bg); color: var(--text); font-size: 14px; }
header { background: var(--header-grad); color: #fff; padding: 28px 32px 22px 32px; }
header h1 { margin: 0 0 6px 0; font-size: 24px; }
header h1 .forge { color: #a3e5d0; }
header .meta { color: #d4f0e6; font-size: 13px; }
header .meta strong { color: #fff; }
.summary-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; padding: 20px 32px 8px 32px; }
.stat { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 16px 18px; }
.stat .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
.stat .value { font-size: 26px; font-weight: 600; color: var(--teal); margin-top: 4px; }
.stat .sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
nav.tabs { display: flex; gap: 4px; padding: 10px 32px 0 32px; border-bottom: 1px solid var(--border); background: var(--panel); flex-wrap: wrap; }
nav.tabs button { background: transparent; border: none; padding: 12px 20px 14px 20px; font-size: 14px; font-weight: 500; color: var(--muted); cursor: pointer; border-bottom: 3px solid transparent; }
nav.tabs button.active { color: var(--navy); border-bottom-color: var(--teal); }
nav.tabs button:hover { color: var(--navy); }
main { padding: 24px 32px 60px 32px; max-width: 1400px; }
.tabpanel { display: none; }
.tabpanel.active { display: block; }
section { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 22px 26px; margin-bottom: 22px; }
section h2 { margin: 0 0 14px 0; font-size: 16px; color: var(--navy); }
section h3 { margin: 18px 0 10px 0; font-size: 14px; color: var(--teal); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--border); }
th { background: #f5f9f7; font-weight: 600; color: var(--navy); font-size: 12px; text-transform: uppercase; letter-spacing: 0.02em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.cat { font-size: 11px; color: var(--muted); }
.bar-row { display: flex; align-items: center; gap: 10px; }
.bar-row .barwrap { flex: 1; height: 14px; background: #e0ebe5; border-radius: 3px; overflow: hidden; }
.bar-row .bar { height: 100%; background: linear-gradient(90deg, var(--teal), var(--navy)); }
.matrix-cell { display: inline-block; min-width: 36px; padding: 3px 6px; border-radius: 3px; font-size: 11px; font-variant-numeric: tabular-nums; text-align: center; }
.matrix-cell.empty { background: #f5f3ea; color: #c0bca8; }
.matrix-cell.low { background: #d9eee5; color: #0f6e56; }
.matrix-cell.med { background: #8cc8ad; color: #073d2d; }
.matrix-cell.high { background: #0f6e56; color: #fff; }
.snippet { background: #f4faf7; border-left: 3px solid var(--teal); padding: 8px 12px; margin: 6px 0; font-size: 13px; line-height: 1.55; }
.snippet .meta { color: var(--muted); font-size: 11px; margin-bottom: 4px; }
.snippet .text { color: var(--text); }
.snippet .value { color: var(--teal); font-weight: 600; }
.source-coded { color: var(--teal); }
.source-text { color: var(--amber); }
.source-coded-text { color: var(--purple); }
input.filter { padding: 7px 10px; border: 1px solid var(--border); border-radius: 4px; width: 240px; font-size: 13px; }
.ecto-tag { display: inline-block; background: #efeaf6; color: var(--purple); padding: 2px 7px; border-radius: 3px; font-family: ui-monospace, monospace; font-size: 11px; font-weight: 600; text-decoration: none; }
a.ecto-tag:hover { background: var(--purple); color: #fff; }
.ecto-pending { display: inline-block; background: #f3f0e7; color: var(--muted); padding: 2px 7px; border-radius: 3px; font-size: 11px; font-style: italic; }
.status-verified { display: inline-block; background: #d9eee5; color: var(--teal); padding: 2px 7px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.status-pending { display: inline-block; background: #f9f5e8; color: var(--amber); padding: 2px 7px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.ecto-note { color: var(--muted); font-size: 12px; font-style: italic; margin-top: 4px; }
.callout { background: #f9f5e8; border-left: 4px solid var(--amber); padding: 12px 16px; margin: 12px 0; font-size: 13px; }
.callout strong { color: var(--amber); }
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
        return '<p style="color: var(--muted);">No exposure categories survived k-anonymity threshold.</p>'
    max_pts = max(c['n_patients'] for c in summary['categories'])
    rows = []
    for c in summary['categories']:
        pct = 100 * c['n_patients'] / max(max_pts, 1)
        ecto = c.get('ecto', {})
        ecto_html = _render_ecto_tag(ecto.get('ecto_id'))
        rows.append(
            f'<tr>'
            f'<td>{_esc(c["category"])}</td>'
            f'<td>{ecto_html}</td>'
            f'<td class="num">{c["n_patients"]}</td>'
            f'<td><div class="bar-row"><div class="barwrap"><div class="bar" style="width:{pct:.1f}%"></div></div></div></td>'
            f'<td class="num">{c["n_records"]:,}</td>'
            f'<td><span class="source-{c["source"].replace("+","-")}">{_esc(c["source"])}</span></td>'
            f'</tr>')
    return (
        '<table><thead><tr>'
        '<th>Exposure category</th><th>ECTO</th><th>Patients</th><th></th>'
        '<th>Records</th><th>Source</th>'
        '</tr></thead><tbody>'
        + ''.join(rows) + '</tbody></table>')


def _render_labels(summary):
    if not summary['labels']:
        return '<p style="color: var(--muted);">No labels survived k-anonymity threshold.</p>'
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
        '<input class="filter" placeholder="Filter exposures..." '
        'oninput="filterTable(this, \'lbl-tbl\')">'
        '<table id="lbl-tbl" style="margin-top:10px"><thead><tr>'
        '<th>Exposure / indicator</th><th>Category</th><th>Patients</th>'
        '<th>Records</th><th>Source</th>'
        '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>')


def _render_matrix(summary):
    cats = summary['surviving_cats']
    patients = summary['patients']
    if not cats or not patients:
        return '<p style="color: var(--muted);">No data to display.</p>'
    cell = summary['patient_cell_index']
    pts_shown = patients[:40]
    out = ['<div style="overflow-x:auto;"><table style="font-size:12px"><thead><tr>',
           '<th style="position:sticky;left:0;background:#f5f9f7;">Patient</th>']
    for c in cats:
        out.append(f'<th style="font-size:10px;text-transform:none;">{_esc(c)}</th>')
    out.append('</tr></thead><tbody>')
    for p in pts_shown:
        out.append(f'<tr><td style="position:sticky;left:0;background:var(--panel);font-weight:600;">{_esc(p)}</td>')
        for c in cats:
            n = cell.get(f'{p}|{c}', 0)
            klass = 'empty' if n == 0 else ('low' if n < 3 else ('med' if n < 10 else 'high'))
            disp = '·' if n == 0 else str(n)
            out.append(f'<td><span class="matrix-cell {klass}">{disp}</span></td>')
        out.append('</tr>')
    out.append('</tbody></table></div>')
    if len(patients) > 40:
        out.append(f'<p style="color:var(--muted);margin-top:12px;">Showing top 40 of '
                   f'{len(patients)} patients with any exposure record.</p>')
    return ''.join(out)


def _render_snippets(snippet_groups):
    if not snippet_groups:
        return '<p style="color: var(--muted);">No regex extractions to display.</p>'
    out = []
    for g in snippet_groups:
        ecto_html = f' · {_render_ecto_tag(g["ecto_seed"])}' if g.get('ecto_seed') else ''
        out.append(f'<h3>{_esc(g["pattern"])} '
                   f'<span style="color:var(--muted);font-weight:normal;font-size:12px;">'
                   f'{g["n_patients"]} unique patients · category '
                   f'<code>{_esc(g["category"] or "—")}</code>{ecto_html}</span></h3>')
        if g['description']:
            out.append(f'<p style="color:var(--muted);font-size:12px;">{_esc(g["description"])}</p>')
        for ex in g['examples']:
            out.append(
                f'<div class="snippet">'
                f'<div class="meta">{_esc(ex["pseudo_id"])} · '
                f'<code>{_esc(ex["source_kind"])}</code> · matched '
                f'<span class="value">{_esc(ex["value"])}</span></div>'
                f'<div class="text">{_esc(ex["snippet"])}</div>'
                f'</div>')
    return ''.join(out)


def _render_ecto_table(summary):
    rows = []
    seen_cats = {c['category'] for c in summary['categories']}
    # Sort: verified first, then pending, then catch-all / out-of-scope
    sort_key = {'verified': 0, 'pending': 1, 'catch_all': 2, 'out_of_scope': 3}
    items = sorted(ECTO_CATEGORY_MAP.items(),
                   key=lambda kv: (sort_key.get(kv[1].get('curation_status', 'pending'), 4),
                                    kv[0]))
    for cat, info in items:
        in_data = '✓' if cat in seen_cats else '·'
        status = info.get('curation_status', 'pending')
        ecto_id = info.get('ecto_id')
        if status == 'verified' and ecto_id:
            ecto_cell = (f'<a href="https://www.ebi.ac.uk/ols/ontologies/ecto/terms?iri='
                         f'http://purl.obolibrary.org/obo/{ecto_id.replace(":", "_")}" '
                         f'target="_blank" class="ecto-tag">{_esc(ecto_id)}</a>')
            label_cell = _esc(info.get('ecto_label') or '')
            status_html = '<span class="status-verified">verified</span>'
        elif status == 'pending':
            ecto_cell = '<span class="ecto-pending">curation pending</span>'
            label_cell = '<span style="color: var(--muted);">—</span>'
            status_html = '<span class="status-pending">pending</span>'
        elif status == 'catch_all':
            ecto_cell = '<span class="ecto-pending">n/a — catch-all bucket</span>'
            label_cell = '<span style="color: var(--muted);">—</span>'
            status_html = '<span class="status-pending">catch-all</span>'
        else:  # out_of_scope
            ecto_cell = '<span class="ecto-pending">out of ECTO scope</span>'
            label_cell = '<span style="color: var(--muted);">—</span>'
            status_html = '<span class="status-pending">out of scope</span>'
        rows.append(
            f'<tr>'
            f'<td><strong>{_esc(cat)}</strong></td>'
            f'<td>{ecto_cell}</td>'
            f'<td>{label_cell}</td>'
            f'<td>{status_html}</td>'
            f'<td style="text-align:center;color:{"var(--teal)" if in_data == "✓" else "var(--muted)"};">{in_data}</td>'
            f'<td><span class="ecto-note">{_esc(info.get("note") or "")}</span></td>'
            f'</tr>')
    return (
        '<p style="color: var(--muted); font-size: 13px; margin-top: 0;">'
        'Each exposure category in this dashboard is grounded in a representative '
        'term from <strong>ECTO</strong>, the Environmental Conditions, Treatments, '
        'and Exposures Ontology (an OBO Foundry resource maintained at '
        '<a href="https://github.com/EnvironmentOntology/environmental-exposure-ontology" '
        'target="_blank">github.com/EnvironmentOntology/environmental-exposure-ontology</a>). '
        'ECTO interoperates with GA4GH Phenopackets and is the recommended '
        'terminology for exposure annotation. The "in data" column marks which '
        'categories actually have observations in this cohort. '
        'Click any verified term ID to open it in the EBI Ontology Lookup Service.</p>'
        '<table><thead><tr>'
        '<th>Category</th><th>ECTO term</th><th>ECTO label</th>'
        '<th>Curation</th><th>In cohort?</th><th>Notes</th>'
        '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
        '<div class="callout"><strong>Curation status legend.</strong> '
        '<span class="status-verified">verified</span> = the ECTO term ID has '
        'been confirmed against the published SSSOM mapping file. '
        '<span class="status-pending">pending</span> = no verified term ID yet — '
        'adopters should search the '
        '<a href="https://www.ebi.ac.uk/ols/ontologies/ecto" target="_blank">'
        'OLS ECTO browser</a> for the closest term and add it here, rather than '
        'displaying a guessed identifier. Specific per-substance ECTO IDs '
        '(e.g. ECTO:9000945 for lead, ECTO:9000033 for asbestos) appear on '
        'individual patterns in the Source-record snippets tab where verified.'
        '</div>')


def _render_ecto_tag(ecto_id):
    """Render an ECTO ID inline — verified IDs become links, missing/empty
    becomes 'curation pending' so we never display a guessed ID."""
    if not ecto_id or ecto_id == 'None':
        return '<span class="ecto-pending">curation pending</span>'
    safe_id = _esc(ecto_id)
    href = f'https://www.ebi.ac.uk/ols/ontologies/ecto/terms?iri=http://purl.obolibrary.org/obo/{ecto_id.replace(":", "_")}'
    return f'<a href="{href}" target="_blank" class="ecto-tag">{safe_id}</a>'


def render_html(summary, snippet_groups, cohort_name='ARC EHR cohort',
                k=DEFAULT_K_ANONYMITY):
    now = _dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    n_cat = len(summary['categories'])
    n_label = len(summary['labels'])
    n_pt = summary['n_patients_total']

    body = f"""
<header>
  <h1>Registry <span class="forge">Forge</span> — Environmental / Occupational Exposure Dashboard</h1>
  <div class="meta">Cohort: <strong>{_esc(cohort_name)}</strong> &nbsp;·&nbsp;
       Generated {_esc(now)} &nbsp;·&nbsp;
       Mapped to <strong>ECTO</strong> (GA4GH Phenopackets-compatible) &nbsp;·&nbsp;
       k-anonymity threshold k={k}</div>
</header>

<div class="summary-row">
  <div class="stat">
    <div class="label">Patients with exposures</div>
    <div class="value">{n_pt:,}</div>
    <div class="sub">across all categories</div>
  </div>
  <div class="stat">
    <div class="label">Exposure categories</div>
    <div class="value">{n_cat:,}</div>
    <div class="sub">after k≥{k} filter</div>
  </div>
  <div class="stat">
    <div class="label">Distinct exposures</div>
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
  <button data-tab="labels"   onclick="showTab('labels')">All exposures</button>
  <button data-tab="matrix"   onclick="showTab('matrix')">Patient × category matrix</button>
  <button data-tab="snippets" onclick="showTab('snippets')">Source-record snippets</button>
  <button data-tab="ecto"     onclick="showTab('ecto')">ECTO mapping</button>
  <button data-tab="about"    onclick="showTab('about')">About</button>
</nav>

<main>
  <div id="panel-overview" class="tabpanel active">
    <section>
      <h2>Exposure categories observed in this cohort</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        Each row aggregates patients and records associated with one exposure
        category. ECTO column shows the representative ontology term. "Source"
        indicates whether matches came from structured codes
        (<span class="source-coded">coded</span>), free-text regex
        (<span class="source-text">text</span>), or both.
      </p>
      {_render_categories(summary)}
    </section>
  </div>

  <div id="panel-labels" class="tabpanel">
    <section>
      <h2>All exposures / indicators</h2>
      {_render_labels(summary)}
    </section>
  </div>

  <div id="panel-matrix" class="tabpanel">
    <section>
      <h2>Patient × exposure-category matrix</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        PT-NNNN pseudonyms. Cell shows the number of records for that patient
        in that category; · = none. Categories with fewer than k={k} unique
        patients are suppressed.
      </p>
      {_render_matrix(summary)}
    </section>
  </div>

  <div id="panel-snippets" class="tabpanel">
    <section>
      <h2>Source-record snippets</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        Up to 3 representative text excerpts per regex pattern, prioritizing
        unique-patient diversity. Snippet text truncated to 200 chars.
        For verification and chart-review preparation; not for analysis.
      </p>
      {_render_snippets(snippet_groups)}
    </section>
  </div>

  <div id="panel-ecto" class="tabpanel">
    <section>
      <h2>ECTO ontology mapping</h2>
      {_render_ecto_table(summary)}
    </section>
  </div>

  <div id="panel-about" class="tabpanel">
    <section>
      <h2>About this dashboard</h2>
      <p>This dashboard summarizes environmental, occupational, and toxic
      exposures captured in the EHR data of an ALS cohort, demonstrating
      Registry Forge's capability to surface candidate ALS risk factors
      from existing clinical data. It combines two underlying CSVs:</p>
      <ul>
        <li><code>exposure_codes.csv</code> — structured matches against
          ICD-10-CM codes (Z57.x occupational exposure, Z77.x environmental
          contact, F17.x nicotine dependence, Z87.891 personal history of
          smoking, Z91.82 personal history of military deployment, Z87.820
          personal history of TBI, and others).</li>
        <li><code>exposure_extractions.csv</code> — regex matches against
          <code>notes[].narrative_text</code> and
          <code>documents[].plain_text</code> covering smoking, military
          service (including Gulf War / OIF / OEF / Vietnam / Camp
          Lejeune), pesticides (with named-agent patterns for paraquat,
          glyphosate, organophosphates, DDT), heavy metals (lead, mercury,
          manganese / welding fumes, arsenic, cadmium), industrial
          solvents (TCE, PCE, benzene, formaldehyde), asbestos,
          repetitive head trauma (TBI history, football, military blast),
          electromagnetic fields, cyanotoxins / BMAA, mold, air
          pollution, and occupational dust.</li>
      </ul>
      <p><strong>Ontology grounding.</strong> Each exposure category is
      mapped to a representative term in <strong>ECTO</strong>, the
      Environmental Conditions, Treatments, and Exposures Ontology (an
      OBO Foundry resource, ~2,700 exposure terms). ECTO is the recommended
      terminology for exposure annotation in GA4GH Phenopackets and
      interoperates with Mondo for environmentally-influenced diseases.
      The GA4GH Human Exposome Data Standards Study Group is actively
      extending Phenopackets with schemas for exposure data; this module
      is forward-compatible with that effort. See the ECTO Mapping tab
      for the term table including curation status — verified IDs were
      confirmed against the published ECTO SSSOM mapping file; pending
      ones need curation against the current ECTO release before
      publication-grade use.</p>
      <p><strong>Privacy controls (baked in):</strong></p>
      <ul>
        <li>Patient identifiers replaced with PT-NNNN pseudonyms, stable within
          this run.</li>
        <li>Calendar dates reduced to year only.</li>
        <li>Snippet text truncated to 200 characters.</li>
        <li>Categories and patterns with fewer than k={k} unique patients
          are suppressed.</li>
        <li>Resource UUIDs are never emitted.</li>
      </ul>
      <p><strong>ALS risk factor literature context.</strong> The exposure
      categories represented here are drawn from the established and
      proposed ALS environmental risk factor literature: smoking is the
      most consistent epidemiological signal; military service is
      associated with elevated ALS risk and ALS is a presumptive
      service-connected disability for some veteran cohorts; pesticide
      exposure (especially organophosphates and paraquat), heavy metals
      (particularly lead), industrial solvents (TCE), repetitive head
      trauma (football, military blast injury), cyanotoxins (BMAA
      hypothesis), and electromagnetic fields have all been studied as
      candidate risk factors. This dashboard is designed to surface
      candidate signals in routinely-collected EHR data; confirmatory
      epidemiological analysis requires independent exposure assessment
      and case-control or cohort designs.</p>
      <p><strong>Important caveat.</strong> Most environmental and
      occupational exposure information lives in social-history narrative
      sections of clinical notes rather than as structured ICD-10 codes.
      Z57.x and Z77.x codes are systematically under-coded in clinical
      practice; the bulk of signal in this dashboard comes from the
      regex side scanning narratives. Adopters should not over-interpret
      the absence of structured codes as absence of exposure, and
      detection sensitivity will vary substantially across EHR vendors
      and clinical-documentation conventions.</p>
    </section>
  </div>
</main>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Exposure Dashboard — {_esc(cohort_name)}</title>
<style>{CSS}</style>
</head>
<body>
{body}
<script>{JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def main(codes_csv_path='./exposure_codes.csv',
         extractions_csv_path='./exposure_extractions.csv',
         out_path='./exposure_dashboard.html',
         cohort_name='ARC EHR cohort',
         k=DEFAULT_K_ANONYMITY):
    ts = _dt.datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] exposure_dashboard — building from:')
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
    print(f'    Patients with exposures:  {summary["n_patients_total"]:,}')
    print(f'    Categories shown:         {len(summary["categories"])}')
    print(f'    Distinct exposures:       {len(summary["labels"])}')
    print(f'    Snippet groups:           {len(snippets)}')
    return out_path


if __name__ == '__main__':
    main()
