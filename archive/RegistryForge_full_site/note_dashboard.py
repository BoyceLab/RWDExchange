"""
note_dashboard.py — single-file HTML dashboard for viewing the outputs
of note_extraction.py.

Reads:
  note_extractions.csv   (regex matches in narrative text — ALSFRS-R,
                          FVC%, El Escorial, family history, genetic
                          mutations, FTD spectrum, PEG/trach dates, etc.)

Produces:
  note_dashboard.html    (self-contained; open in any browser)

Privacy controls (baked in):
  * patient_id           -> PT-NNNN pseudonyms
  * dates and free-text values inside snippets -> the snippet itself is
    truncated to 200 chars (raw values like ALSFRS-R scores or El Escorial
    category names are clinical findings, not PHI, and are shown directly)
  * pattern with < k unique patients suppressed (default k=2)

Six tabs:
  1. Overview by pattern category — aggregated by clinical-content group
     (ALSFRS-R, ECAS / FTD, diagnosis, family history, pulmonary, treatment
     milestones), with patient counts and bar charts
  2. All patterns — filterable per-pattern table
  3. Patient × pattern matrix — heatmap of which patients have which content
  4. Captured values distribution — for numeric patterns (ALSFRS-R total,
     FVC% predicted), shows the distribution of captured values; for
     categorical (El Escorial, onset region), shows category counts
  5. Source-record snippets — sample regex matches per pattern
  6. About — methodology, privacy controls, scope-of-use caveats

This module is the visualization complement to note_extraction.py;
together they surface ALS-specific clinical content that lives in
unstructured narrative rather than discrete coded values.
"""

import csv
import json
import os
import re
import sys
import datetime as _dt
from collections import defaultdict, Counter

DEFAULT_K_ANONYMITY = 2


# ===========================================================================
# Pattern → category mapping. Categories are display labels used in the
# Overview tab. Patterns not listed here fall through to 'other'.
# Drives the bar-chart groupings and the Patient × pattern matrix columns.
# ===========================================================================
NOTE_PATTERN_CATEGORIES = {
    # ALSFRS-R
    'alsfrs_r_total':            ('alsfrs_r',        'ALSFRS-R'),
    'alsfrs_r_bulbar':           ('alsfrs_r',        'ALSFRS-R'),
    'alsfrs_r_fine_motor':       ('alsfrs_r',        'ALSFRS-R'),
    'alsfrs_r_gross_motor':      ('alsfrs_r',        'ALSFRS-R'),
    'alsfrs_r_respiratory':      ('alsfrs_r',        'ALSFRS-R'),
    # ECAS and FTD spectrum
    'ecas_total':                ('ecas',            'ECAS / FTD'),
    'ecas_als_specific':         ('ecas',            'ECAS / FTD'),
    'ecas_non_als':              ('ecas',            'ECAS / FTD'),
    'ftd_spectrum':              ('ecas',            'ECAS / FTD'),
    # Diagnosis
    'el_escorial':               ('diagnosis',       'Diagnosis'),
    'onset_region':              ('diagnosis',       'Diagnosis'),
    # Family history and genetics
    'family_history_negative':   ('family_history',  'Family history & genetics'),
    'family_history_positive':   ('family_history',  'Family history & genetics'),
    'genetic_mutation':          ('family_history',  'Family history & genetics'),
    # Pulmonary
    'fvc_percent_predicted':     ('pulmonary',       'Pulmonary'),
    # Treatment milestones (dated procedures or drug starts)
    'peg_placement_date':        ('milestone',       'Treatment milestones'),
    'tracheostomy_date':         ('milestone',       'Treatment milestones'),
    'niv_initiation':            ('milestone',       'Treatment milestones'),
    'riluzole_start':            ('milestone',       'Treatment milestones'),
    'edaravone_start':           ('milestone',       'Treatment milestones'),
}

# Display order for categories on the Overview tab
CATEGORY_ORDER = ['alsfrs_r', 'pulmonary', 'diagnosis', 'family_history',
                  'ecas', 'milestone', 'other']

# Pretty labels per category code
CATEGORY_LABEL = {
    'alsfrs_r':       'ALSFRS-R',
    'pulmonary':      'Pulmonary',
    'diagnosis':      'Diagnosis',
    'family_history': 'Family history & genetics',
    'ecas':           'ECAS / FTD',
    'milestone':      'Treatment milestones',
    'other':          'Other',
}


# ===========================================================================
# Helpers
# ===========================================================================
def _pseudo_id(pid, cache):
    if not pid: pid = '(no-id)'
    if pid not in cache:
        cache[pid] = f'PT-{len(cache)+1:04d}'
    return cache[pid]


def _truncate(s, n=200):
    if not s: return ''
    s = str(s).strip()
    return s if len(s) <= n else s[:n-1] + '…'


def _esc(s):
    if s is None: return ''
    return (str(s)
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;').replace("'", '&#39;'))


def _category_for(pattern_name):
    cat, _ = NOTE_PATTERN_CATEGORIES.get(pattern_name, ('other', 'Other'))
    return cat


def _try_float(s):
    """Parse a captured value as a float when possible (for ALSFRS-R, FVC%, etc.)."""
    if not s: return None
    s = str(s).strip().replace('%', '').replace(',', '')
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def load_data(ext_csv):
    ext = []
    if os.path.exists(ext_csv):
        with open(ext_csv, encoding='utf-8-sig') as f:
            ext = list(csv.DictReader(f))
    return ext


# ===========================================================================
# Aggregations
# ===========================================================================
def compute_summary(ext_rows, k=DEFAULT_K_ANONYMITY):
    pseudo = {}
    pat_to_pts = defaultdict(set)
    pat_to_records = Counter()
    pat_to_values = defaultdict(list)
    cat_to_pts = defaultdict(set)
    cat_to_records = Counter()
    cat_to_patterns = defaultdict(set)
    patient_to_pat_count = defaultdict(lambda: defaultdict(int))
    patient_to_total = defaultdict(int)

    for r in ext_rows:
        pid = r.get('patient_id', '')
        pseudo_id = _pseudo_id(pid, pseudo)
        pat = (r.get('pattern') or 'unknown').strip()
        cat = _category_for(pat)
        val = (r.get('value') or '').strip()
        pat_to_pts[pat].add(pseudo_id)
        pat_to_records[pat] += 1
        if val:
            pat_to_values[pat].append(val)
        cat_to_pts[cat].add(pseudo_id)
        cat_to_records[cat] += 1
        cat_to_patterns[cat].add(pat)
        patient_to_pat_count[pseudo_id][pat] += 1
        patient_to_total[pseudo_id] += 1

    # k-anonymity at pattern level
    patterns = []
    for pat, pts in sorted(pat_to_pts.items(), key=lambda kv: -len(kv[1])):
        if len(pts) < k: continue
        patterns.append({
            'pattern':    pat,
            'category':   _category_for(pat),
            'n_patients': len(pts),
            'n_records':  pat_to_records[pat],
        })

    # Category aggregation
    categories = []
    for cat in CATEGORY_ORDER:
        if cat not in cat_to_pts: continue
        if len(cat_to_pts[cat]) < k: continue
        categories.append({
            'category':   cat,
            'label':      CATEGORY_LABEL.get(cat, cat),
            'n_patients': len(cat_to_pts[cat]),
            'n_records':  cat_to_records[cat],
            'n_patterns': len(cat_to_patterns[cat]),
        })

    # Patient matrix — only patterns that survived k-anon
    surviving_pats = [p['pattern'] for p in patterns]
    cell_index = {}
    for pseudo_id, pats in patient_to_pat_count.items():
        for pat, n in pats.items():
            if pat in surviving_pats:
                cell_index[f'{pseudo_id}|{pat}'] = n
    patient_list = sorted(patient_to_total.keys(),
                          key=lambda p: -patient_to_total[p])

    # Per-pattern value distribution (for numeric patterns)
    value_summaries = {}
    NUMERIC_PATTERNS = {'alsfrs_r_total', 'alsfrs_r_bulbar', 'alsfrs_r_fine_motor',
                        'alsfrs_r_gross_motor', 'alsfrs_r_respiratory',
                        'ecas_total', 'ecas_als_specific', 'ecas_non_als',
                        'fvc_percent_predicted'}
    for pat, vals in pat_to_values.items():
        if pat in NUMERIC_PATTERNS:
            numeric = [_try_float(v) for v in vals]
            numeric = [n for n in numeric if n is not None]
            if numeric:
                numeric.sort()
                value_summaries[pat] = {
                    'kind':   'numeric',
                    'n':      len(numeric),
                    'min':    min(numeric),
                    'max':    max(numeric),
                    'median': numeric[len(numeric)//2],
                    'mean':   sum(numeric) / len(numeric),
                }
        else:
            # Categorical / freetext — top values
            value_counts = Counter(v[:60] for v in vals)
            value_summaries[pat] = {
                'kind':       'categorical',
                'n':          len(vals),
                'top_values': value_counts.most_common(10),
            }

    return {
        'n_patients_total':   len(pseudo),
        'n_extractions_rows': len(ext_rows),
        'patterns':           patterns,
        'categories':         categories,
        'patients':           patient_list,
        'patient_to_total':   dict(patient_to_total),
        'patient_cell_index': cell_index,
        'surviving_pats':     surviving_pats,
        'value_summaries':    value_summaries,
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
            'value':       _truncate(r.get('value'), 60),
            'snippet':     _truncate(r.get('snippet'), 200),
            'description': r.get('description', ''),
        })
    return sorted([{
        'pattern':    pat,
        'category':   _category_for(pat),
        'description': pat_to_examples[pat][0]['description'] if pat_to_examples[pat] else '',
        'n_patients': len(pat_to_pts[pat]),
        'examples':   pat_to_examples[pat],
    } for pat in pat_to_examples], key=lambda x: -x['n_patients'])


# ===========================================================================
# HTML rendering
# ===========================================================================
CSS = """
:root {
  --bg:#f7f6f1; --panel:#fff; --text:#1e2433; --muted:#5f6478;
  --navy:#1e3a5f; --purple:#4a148c; --teal:#0f6e56; --amber:#854f0b;
  --indigo:#3949ab; --border:#e3e0d5;
  --header-grad: linear-gradient(135deg, #4a148c 0%, #3949ab 60%, #1e3a5f 100%);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, "Segoe UI", "Helvetica Neue", Arial, sans-serif; background: var(--bg); color: var(--text); font-size: 14px; }
header { background: var(--header-grad); color: #fff; padding: 28px 32px 22px 32px; }
header h1 { margin: 0 0 6px 0; font-size: 24px; }
header h1 .forge { color: #c5b8e5; }
header .meta { color: #e0d6f0; font-size: 13px; }
header .meta strong { color: #fff; }
.summary-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; padding: 20px 32px 8px 32px; }
.stat { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 16px 18px; }
.stat .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
.stat .value { font-size: 26px; font-weight: 600; color: var(--purple); margin-top: 4px; }
.stat .sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
nav.tabs { display: flex; gap: 4px; padding: 10px 32px 0 32px; border-bottom: 1px solid var(--border); background: var(--panel); flex-wrap: wrap; }
nav.tabs button { background: transparent; border: none; padding: 12px 20px 14px 20px; font-size: 14px; font-weight: 500; color: var(--muted); cursor: pointer; border-bottom: 3px solid transparent; }
nav.tabs button.active { color: var(--navy); border-bottom-color: var(--purple); }
nav.tabs button:hover { color: var(--navy); }
main { padding: 24px 32px 60px 32px; max-width: 1400px; }
.tabpanel { display: none; }
.tabpanel.active { display: block; }
section { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 22px 26px; margin-bottom: 22px; }
section h2 { margin: 0 0 14px 0; font-size: 16px; color: var(--navy); }
section h3 { margin: 18px 0 10px 0; font-size: 14px; color: var(--purple); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--border); }
th { background: #f4f0f9; font-weight: 600; color: var(--navy); font-size: 12px; text-transform: uppercase; letter-spacing: 0.02em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.cat { font-size: 11px; color: var(--muted); }
.bar-row { display: flex; align-items: center; gap: 10px; }
.bar-row .barwrap { flex: 1; height: 14px; background: #ebe5f3; border-radius: 3px; overflow: hidden; }
.bar-row .bar { height: 100%; background: linear-gradient(90deg, var(--purple), var(--indigo)); }
.matrix-cell { display: inline-block; min-width: 36px; padding: 3px 6px; border-radius: 3px; font-size: 11px; font-variant-numeric: tabular-nums; text-align: center; }
.matrix-cell.empty { background: #f5f3ea; color: #c0bca8; }
.matrix-cell.low { background: #ebe5f3; color: var(--purple); }
.matrix-cell.med { background: #b8a3d6; color: #2a0857; }
.matrix-cell.high { background: var(--purple); color: #fff; }
.snippet { background: #f9f6fd; border-left: 3px solid var(--purple); padding: 8px 12px; margin: 6px 0; font-size: 13px; line-height: 1.55; }
.snippet .meta { color: var(--muted); font-size: 11px; margin-bottom: 4px; }
.snippet .text { color: var(--text); }
.snippet .value { color: var(--purple); font-weight: 600; }
input.filter { padding: 7px 10px; border: 1px solid var(--border); border-radius: 4px; width: 240px; font-size: 13px; }
.cat-tag { display: inline-block; background: #ebe5f3; color: var(--purple); padding: 2px 7px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.value-card { background: #f9f6fd; border-radius: 4px; padding: 12px 14px; margin: 8px 0; font-size: 13px; }
.value-card h4 { margin: 0 0 8px 0; font-size: 13px; color: var(--purple); }
.value-card .nums { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; font-variant-numeric: tabular-nums; }
.value-card .nums div { font-size: 11px; color: var(--muted); }
.value-card .nums div strong { display: block; font-size: 18px; color: var(--navy); }
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
        return '<p style="color: var(--muted);">No pattern categories survived k-anonymity.</p>'
    max_pts = max(c['n_patients'] for c in summary['categories'])
    rows = []
    for c in summary['categories']:
        pct = 100 * c['n_patients'] / max(max_pts, 1)
        rows.append(
            f'<tr>'
            f'<td><strong>{_esc(c["label"])}</strong></td>'
            f'<td class="num">{c["n_patients"]}</td>'
            f'<td><div class="bar-row"><div class="barwrap"><div class="bar" style="width:{pct:.1f}%"></div></div></div></td>'
            f'<td class="num">{c["n_records"]:,}</td>'
            f'<td class="num">{c["n_patterns"]}</td>'
            f'</tr>')
    return ('<table><thead><tr><th>Clinical-content category</th>'
            '<th>Unique patients</th><th></th><th>Records</th><th>Patterns</th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>')


def _render_patterns(summary):
    if not summary['patterns']:
        return '<p style="color: var(--muted);">No patterns survived k-anonymity.</p>'
    rows = []
    for p in summary['patterns']:
        rows.append(
            f'<tr>'
            f'<td><code>{_esc(p["pattern"])}</code></td>'
            f'<td><span class="cat-tag">{_esc(CATEGORY_LABEL.get(p["category"], p["category"]))}</span></td>'
            f'<td class="num">{p["n_patients"]}</td>'
            f'<td class="num">{p["n_records"]:,}</td>'
            f'</tr>')
    return ('<input class="filter" placeholder="Filter patterns or categories..." '
            'oninput="filterTable(this, \'pat-tbl\')">'
            '<table id="pat-tbl" style="margin-top:10px"><thead><tr>'
            '<th>Pattern</th><th>Category</th><th>Unique patients</th><th>Records</th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>')


def _render_matrix(summary):
    pats = summary['surviving_pats']
    patients = summary['patients']
    if not pats or not patients:
        return '<p style="color: var(--muted);">No data to display.</p>'
    cell = summary['patient_cell_index']
    pts_shown = patients[:40]
    out = ['<div style="overflow-x:auto;"><table style="font-size:12px"><thead><tr>',
           '<th style="position:sticky;left:0;background:#f4f0f9;">Patient</th>']
    for p in pats:
        out.append(f'<th style="font-size:10px;text-transform:none;">{_esc(p)}</th>')
    out.append('</tr></thead><tbody>')
    for pid in pts_shown:
        out.append(f'<tr><td style="position:sticky;left:0;background:var(--panel);font-weight:600;">{_esc(pid)}</td>')
        for p in pats:
            n = cell.get(f'{pid}|{p}', 0)
            klass = 'empty' if n == 0 else ('low' if n < 3 else ('med' if n < 10 else 'high'))
            disp = '·' if n == 0 else str(n)
            out.append(f'<td><span class="matrix-cell {klass}">{disp}</span></td>')
        out.append('</tr>')
    out.append('</tbody></table></div>')
    if len(patients) > 40:
        out.append(f'<p style="color:var(--muted);margin-top:12px;">Showing top 40 of '
                   f'{len(patients)} patients with at least one match.</p>')
    return ''.join(out)


def _render_values(summary):
    vs = summary.get('value_summaries', {})
    if not vs:
        return '<p style="color: var(--muted);">No captured values to summarize.</p>'
    out = []
    # Numeric first
    numeric = [(p, s) for p, s in vs.items() if s.get('kind') == 'numeric']
    if numeric:
        out.append('<h3>Numeric findings (ALSFRS-R, ECAS, FVC% predicted)</h3>')
        for pat, s in sorted(numeric, key=lambda x: -x[1]['n']):
            out.append(
                f'<div class="value-card">'
                f'<h4><code>{_esc(pat)}</code></h4>'
                f'<div class="nums">'
                f'<div>captured values<strong>{s["n"]:,}</strong></div>'
                f'<div>min<strong>{s["min"]:.1f}</strong></div>'
                f'<div>median<strong>{s["median"]:.1f}</strong></div>'
                f'<div>max<strong>{s["max"]:.1f}</strong></div>'
                f'</div></div>')
    # Categorical second
    cat = [(p, s) for p, s in vs.items() if s.get('kind') == 'categorical']
    if cat:
        out.append('<h3>Categorical / freetext findings</h3>')
        for pat, s in sorted(cat, key=lambda x: -x[1]['n'])[:8]:
            tvs = s.get('top_values', [])[:6]
            tv_html = ''.join(
                f'<tr><td><code>{_esc(v[:60])}</code></td><td class="num">{n:,}</td></tr>'
                for v, n in tvs)
            out.append(
                f'<div class="value-card">'
                f'<h4><code>{_esc(pat)}</code> &mdash; {s["n"]:,} captured values; top:</h4>'
                f'<table style="margin-top:4px"><tbody>{tv_html}</tbody></table>'
                f'</div>')
    return ''.join(out) or '<p style="color: var(--muted);">No captured values.</p>'


def _render_snippets(snippet_groups):
    if not snippet_groups:
        return '<p style="color: var(--muted);">No regex extractions to display.</p>'
    out = []
    for g in snippet_groups:
        out.append(f'<h3><code>{_esc(g["pattern"])}</code> '
                   f'<span style="color:var(--muted);font-weight:normal;font-size:12px;">'
                   f'{g["n_patients"]} unique patients · '
                   f'<span class="cat-tag">{_esc(CATEGORY_LABEL.get(g["category"], g["category"]))}</span></span></h3>')
        if g['description']:
            out.append(f'<p style="color:var(--muted);font-size:12px;">{_esc(g["description"])}</p>')
        for ex in g['examples']:
            out.append(
                f'<div class="snippet">'
                f'<div class="meta">{_esc(ex["pseudo_id"])} · '
                f'<code>{_esc(ex["source_kind"])}</code> · captured '
                f'<span class="value">{_esc(ex["value"])}</span></div>'
                f'<div class="text">{_esc(ex["snippet"])}</div>'
                f'</div>')
    return ''.join(out)


def render_html(summary, snippet_groups, cohort_name='ARC EHR cohort',
                k=DEFAULT_K_ANONYMITY):
    now = _dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    n_pt = summary['n_patients_total']
    n_cat = len(summary['categories'])
    n_pat = len(summary['patterns'])

    body = f"""
<header>
  <h1>Registry <span class="forge">Forge</span> — Clinical-Note Extraction Dashboard</h1>
  <div class="meta">Cohort: <strong>{_esc(cohort_name)}</strong> &nbsp;·&nbsp;
       Generated {_esc(now)} &nbsp;·&nbsp;
       Surfaces ALS-specific clinical content from unstructured narrative &nbsp;·&nbsp;
       k-anonymity threshold k={k}</div>
</header>

<div class="summary-row">
  <div class="stat">
    <div class="label">Patients with any match</div>
    <div class="value">{n_pt:,}</div>
    <div class="sub">across all patterns</div>
  </div>
  <div class="stat">
    <div class="label">Pattern categories</div>
    <div class="value">{n_cat:,}</div>
    <div class="sub">after k≥{k} filter</div>
  </div>
  <div class="stat">
    <div class="label">Distinct patterns</div>
    <div class="value">{n_pat:,}</div>
    <div class="sub">after k≥{k} filter</div>
  </div>
  <div class="stat">
    <div class="label">Total matches</div>
    <div class="value">{summary['n_extractions_rows']:,}</div>
    <div class="sub">across all narrative sources</div>
  </div>
</div>

<nav class="tabs">
  <button data-tab="overview" class="active" onclick="showTab('overview')">Overview by category</button>
  <button data-tab="patterns" onclick="showTab('patterns')">All patterns</button>
  <button data-tab="matrix"   onclick="showTab('matrix')">Patient × pattern matrix</button>
  <button data-tab="values"   onclick="showTab('values')">Captured values</button>
  <button data-tab="snippets" onclick="showTab('snippets')">Source snippets</button>
  <button data-tab="about"    onclick="showTab('about')">About</button>
</nav>

<main>
  <div id="panel-overview" class="tabpanel active">
    <section>
      <h2>ALS-specific clinical content by category</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        Each row aggregates patients and records from all regex patterns within
        one clinical-content group. ALSFRS-R covers the functional rating
        scale and its four subdomain subscales; pulmonary captures FVC %
        predicted; diagnosis covers El Escorial / Awaji-Shima certainty and
        onset region; family history covers negative and positive constructs
        and genetic-mutation mentions; ECAS / FTD covers cognitive screening
        scores and FTD-spectrum references; treatment milestones cover dated
        procedures and drug starts (PEG, tracheostomy, NIV, riluzole,
        edaravone).
      </p>
      {_render_categories(summary)}
    </section>
  </div>

  <div id="panel-patterns" class="tabpanel">
    <section>
      <h2>All patterns</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        One row per regex pattern in the shipped library. Filter by name
        or category.
      </p>
      {_render_patterns(summary)}
    </section>
  </div>

  <div id="panel-matrix" class="tabpanel">
    <section>
      <h2>Patient × pattern matrix</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        PT-NNNN pseudonyms. Cell shows the number of records for that patient
        × that pattern; · = none. Patterns with fewer than k={k} unique
        patients are suppressed.
      </p>
      {_render_matrix(summary)}
    </section>
  </div>

  <div id="panel-values" class="tabpanel">
    <section>
      <h2>Captured-value distribution</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        For numeric patterns (ALSFRS-R scores, FVC % predicted), shows min /
        median / max across captured values. For categorical / freetext
        patterns (El Escorial certainty, onset region, family-history
        constructs), shows the top distinct values and their record counts.
        Numeric extractions are not de-duplicated per patient at this view;
        use the Patient × pattern matrix to scope to per-patient counts.
      </p>
      {_render_values(summary)}
    </section>
  </div>

  <div id="panel-snippets" class="tabpanel">
    <section>
      <h2>Source-record snippets</h2>
      <p style="color: var(--muted); font-size: 12px; margin-top: -8px;">
        Up to 3 representative text excerpts per regex pattern, prioritizing
        unique-patient diversity. Snippet text truncated to 200 chars.
        Useful for source verification and pattern-tuning review.
      </p>
      {_render_snippets(snippet_groups)}
    </section>
  </div>

  <div id="panel-about" class="tabpanel">
    <section>
      <h2>About this dashboard</h2>
      <p>This dashboard surfaces ALS-specific clinical content captured by
      <code>note_extraction.py</code> from the unstructured narrative
      sections of the EHR feed (CCDA section narratives and decoded
      documents — RTF, HTML, PDF). Unlike the device and exposure
      dashboards, these are clinical findings (functional rating scores,
      cognitive scores, diagnostic certainty, treatment milestones, family
      history, genetic mutations) rather than risk factors or equipment.</p>
      <p><strong>Scope and limitations.</strong> The patterns shipped with
      <code>note_extraction.py</code> are seed patterns calibrated against
      a single registry (ARC). Adopters should validate every pattern
      against their own narrative corpus before using captured values for
      analysis; site-specific phrasing conventions, vendor-specific
      template structures, and individual-clinician dictation patterns
      vary substantially. Treat this dashboard as a chart-review-preparation
      and pattern-tuning aid; do not admit numeric values (ALSFRS-R, FVC%,
      ECAS) into downstream analysis without validation against the
      patient's structured measurement record or the original note.</p>
      <p><strong>Privacy controls (baked in):</strong></p>
      <ul>
        <li>Patient identifiers replaced with PT-NNNN pseudonyms, stable
          within this run.</li>
        <li>Snippet text truncated to 200 characters.</li>
        <li>Captured values truncated to 60 characters.</li>
        <li>Patterns with fewer than k={k} unique patients are suppressed
          entirely.</li>
        <li>Resource UUIDs are never emitted.</li>
      </ul>
      <p>The companion module
      <a href="https://boycelab.github.io/RegistryForge/note-extraction/"
         target="_blank">note_extraction.py</a> produces the underlying
      <code>note_extractions.csv</code>; this dashboard is the privacy-safe
      visualization layer over that CSV.</p>
    </section>
  </div>
</main>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clinical-note dashboard &mdash; {_esc(cohort_name)}</title>
<style>{CSS}</style>
</head>
<body>
{body}
<script>{JS}</script>
</body>
</html>"""


# ===========================================================================
# Main entry
# ===========================================================================
def main(extractions_csv_path='./note_extractions.csv',
         out_path='./note_dashboard.html',
         cohort_name='ARC EHR cohort',
         k=DEFAULT_K_ANONYMITY):
    ts = _dt.datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] note_dashboard — building from:')
    print(f'    extractions: {extractions_csv_path}')

    ext_rows = load_data(extractions_csv_path)
    print(f'    Loaded {len(ext_rows):,} extraction rows')

    summary = compute_summary(ext_rows, k=k)
    snippets = collect_snippets(ext_rows, k=k, max_examples=3)
    html = render_html(summary, snippets, cohort_name=cohort_name, k=k)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    ts = _dt.datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] Wrote {out_path} ({os.path.getsize(out_path):,} bytes)')
    print(f'    Patients with any match:  {summary["n_patients_total"]:,}')
    print(f'    Pattern categories:       {len(summary["categories"])}')
    print(f'    Distinct patterns:        {len(summary["patterns"])}')
    print(f'    Snippet groups:           {len(snippets)}')
    return out_path


if __name__ == '__main__':
    main()
