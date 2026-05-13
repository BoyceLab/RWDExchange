"""
Mondo-OMOP bridge / rare disease cohort builder
================================================
Companion utility to phenopackets_etl.py. Where phenopackets_etl.py runs
*forward* (source codes in the bundle -> HPO/Mondo for a Phenopacket),
this module runs *backward*: given a Mondo term ID, walk the Mondo
disease hierarchy to find every descendant, then produce two artifacts:

  1. A code list that defines the cohort - every ICD-10-CM and SNOMED CT
     code that maps to a Mondo descendant of the target term. Drop into a
     Databricks query, an Epic chart-review filter, or a CCDA exclusion
     rule to find the cohort in your source EHR.
  2. The same cohort joined to Athena CONCEPT.csv + CONCEPT_RELATIONSHIP.csv
     to produce OMOP standard concept_ids (Condition domain). Use as
     CONDITION_OCCURRENCE.condition_concept_id filter values to find the
     cohort in your OMOP CDM extract. Skipped if no vocab_dir is provided.

Also emits a master MONDO2OMOP.tsv mapping table (one row per Mondo term
with cross-references and OMOP standard concept_id where available).

Rare-disease subset flags are preserved on every output row:
  rare, gard_rare, nord_rare, orphanet_rare, inferred_rare, mondo_rare

Use cases
---------
- "Give me everyone with anything in the ALS spectrum"
  -> build_cohort('MONDO:0004976')
  -> emits a code list covering ALS, ALS-FTD, FUS-related ALS, SOD1-ALS,
     primary lateral sclerosis, progressive muscular atrophy, etc.

- "Give me all rare neurologic diseases with GARD designation"
  -> build_subset_cohort(subset='gard_rare', parent_term='MONDO:0005071')
     (MONDO:0005071 = nervous system disorder)

- "Build a code list for an epilepsy cohort that I can paste into Epic"
  -> build_cohort('MONDO:0005027')
  -> emits ICD-10-CM and SNOMED CT codes that catch every Mondo descendant

Attribution
-----------
The mapping logic and use of Mondo's KGX same_as cross-references is
adapted from the Monarch Initiative's mondo2omop repository (MIT-licensed):
https://github.com/monarch-initiative/mondo2omop
Original authors: Monarch Initiative. We reuse their approach with credit.

Dependencies: networkx (standard in scientific-Python distributions),
plus pandas and requests for KGX download. If networkx is unavailable,
the module falls back to a pure-stdlib BFS implementation.
"""

import os
import sys
import csv
import json
import re
import tarfile
import datetime as _dt
from collections import defaultdict, deque

try:
    import requests
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False

try:
    import networkx as nx
    HAVE_NETWORKX = True
except ImportError:
    HAVE_NETWORKX = False


# ============================================================================
# CONFIG
# ============================================================================
# Primary source: Mondo's official GitHub release. This URL always redirects
# to the latest tagged release and is the canonical distribution channel
# for Mondo. Falls through to kg-hub KGX mirror if this fails.
MONDO_GITHUB_LATEST = 'https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.json'
MONDO_GITHUB_TAGGED = 'https://github.com/monarch-initiative/mondo/releases/download/v{ver}/mondo.json'
MONDO_PURL          = 'http://purl.obolibrary.org/obo/mondo.json'

# Secondary source: kg-hub KGX TSV (legacy; may not always be mirrored)
DEFAULT_MONDO_VERSION = '2024-09-10'
MONDO_KGX_BASE_URL = 'https://kg-hub.berkeleybop.io/kg-obo/mondo/'
MONDO_KGX_URL = MONDO_KGX_BASE_URL + '{ver}/mondo_kgx_tsv.tar.gz'

KNOWN_MONDO_RELEASES = [
    # Fallback list, newest first. Used only if all auto-discovery fails.
    '2024-09-10', '2024-08-06', '2024-07-02', '2024-06-04', '2024-05-08',
    '2024-04-02', '2024-03-04', '2024-02-06', '2024-01-03',
    '2023-12-05', '2023-11-07', '2023-10-04', '2023-09-12', '2023-08-02',
    '2023-07-04', '2023-06-06', '2023-05-09', '2023-04-04',
]

# Mondo top-level filter anchors (per the upstream mondo2omop logic)
HUMAN_DISEASE_ANCHOR        = 'MONDO:0700096'  # keep descendants of this
EXCLUDE_SUSCEPTIBILITY      = 'MONDO:0042489'
EXCLUDE_CHARACTERISTIC      = 'MONDO:0021125'
EXCLUDE_INJURY              = 'MONDO:0021178'

# same_as cross-reference prefixes we care about
SAMEAS_PREFIXES = {
    'http://identifiers.org/snomedct/':            'SNOMED',
    'http://identifiers.org/mesh/':                'MeSH',
    'http://purl.bioontology.org/ontology/ICD10CM/':'ICD10CM',
}

# Rare-disease subset names recognized in Mondo's `subsets` column
RARE_SUBSETS = ('rare', 'gard_rare', 'nord_rare', 'orphanet_rare',
                'inferred_rare', 'mondo_rare')


# ============================================================================
# LOGGING
# ============================================================================
LOG = []
def log(msg):
    line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line); LOG.append(line)


# ============================================================================
# MONDO DOWNLOAD / LOAD — GitHub JSON primary, kg-hub KGX fallback
# ============================================================================
def _url_exists(url, timeout=10):
    """HEAD-request a URL to check whether it returns 200. Quietly returns
    False on any error so the caller can move on to the next candidate."""
    if not HAVE_REQUESTS: return False
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def _first_tuesday_dates(months_back=24):
    """Mondo releases on the first Tuesday of each month. Generate
    candidate YYYY-MM-DD strings going back `months_back` months."""
    out = []
    today = _dt.date.today()
    year, month = today.year, today.month
    for _ in range(months_back):
        first = _dt.date(year, month, 1)
        offset = (1 - first.weekday()) % 7   # Tuesday = 1
        out.append((first + _dt.timedelta(days=offset)).isoformat())
        if month == 1:
            month = 12; year -= 1
        else:
            month -= 1
    return out


def download_mondo_json(target_dir):
    """Download mondo.json from Mondo's official GitHub release. This is
    the canonical Mondo distribution channel and the primary path. Tries
    in order:
      1. GitHub releases/latest (redirects to newest tagged release)
      2. PURL.obolibrary.org (mirror)
      3. GitHub releases for known recent tags
    Returns (json_path, version_str)."""
    if not HAVE_REQUESTS:
        raise RuntimeError("requests not available; pre-download mondo.json manually")
    os.makedirs(target_dir, exist_ok=True)
    json_path = os.path.join(target_dir, 'mondo.json')

    attempts = [
        ('GitHub releases/latest',  MONDO_GITHUB_LATEST),
        ('OBO Foundry PURL',        MONDO_PURL),
    ]
    # Add probed candidates from first-Tuesday-of-month pattern
    for ver in _first_tuesday_dates(months_back=12):
        attempts.append((f'GitHub tag v{ver}', MONDO_GITHUB_TAGGED.format(ver=ver)))

    last_err = None
    for label, url in attempts:
        try:
            log(f'  Trying {label}: {url[:90]}')
            r = requests.get(url, timeout=180, allow_redirects=True)
            if r.status_code != 200:
                last_err = f'{label} returned HTTP {r.status_code}'
                continue
            with open(json_path, 'wb') as f:
                f.write(r.content)
            # Extract version from the JSON's own metadata
            version = _extract_mondo_version(json_path) or 'unknown'
            log(f'  Downloaded Mondo {version} ({len(r.content)//1024//1024} MB) via {label}')
            return json_path, version
        except Exception as e:
            last_err = f'{label} failed: {e}'
            continue

    raise RuntimeError(
        'Could not download Mondo from any source. Last error: ' + str(last_err) +
        '\nManual workaround:\n'
        '  1. Visit https://github.com/monarch-initiative/mondo/releases/latest\n'
        '  2. Download mondo.json (~100 MB)\n'
        '  3. Save it to ' + json_path + '\n'
        '  4. Re-run with mondo_kgx_dir set to ' + target_dir
    )


def _extract_mondo_version(json_path):
    """Read just enough of the JSON to find the release version embedded in
    graphs[0].meta.version (an OBO Foundry release URL)."""
    try:
        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)
        for graph in data.get('graphs', []):
            ver_url = (graph.get('meta') or {}).get('version', '') or ''
            m = re.search(r'releases/(\d{4}-\d{2}-\d{2})/', ver_url)
            if m: return m.group(1)
            m = re.search(r'(\d{4}-\d{2}-\d{2})', ver_url)
            if m: return m.group(1)
    except Exception:
        pass
    return None


# Map xref prefixes (CURIE form from Mondo JSON) to the URL prefixes the
# existing parse_same_as logic recognizes. Lets us keep one code path.
_XREF_PREFIX_TO_URL = {
    'ICD10CM':  'http://purl.bioontology.org/ontology/ICD10CM/',
    'SNOMEDCT': 'http://identifiers.org/snomedct/',
    'MESH':     'http://identifiers.org/mesh/',
    'MeSH':     'http://identifiers.org/mesh/',
}


def load_mondo_json(json_path):
    """Read Mondo's JSON-LD release and convert to (nodes, edges) lists in
    the same format the KGX loader produces. Each node is a dict with keys
    id, category, name, description, same_as, subsets. Each edge is a
    (subject, predicate, object) tuple."""
    log(f'Loading Mondo JSON: {json_path}')
    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)

    nodes = []
    edges = []
    for graph in data.get('graphs', []):
        for n in graph.get('nodes', []):
            if n.get('type') != 'CLASS': continue
            iri = n.get('id', '')
            if 'MONDO_' not in iri: continue
            mondo_id = 'MONDO:' + iri.rsplit('MONDO_', 1)[-1]
            label = n.get('lbl', '') or ''
            meta = n.get('meta') or {}
            desc = ((meta.get('definition') or {}).get('val') or '').strip()
            # Convert CURIE xrefs (e.g. ICD10CM:G12.21) into the URL form
            # the existing parse_same_as understands.
            xref_urls = []
            for x in meta.get('xrefs') or []:
                val = (x.get('val') if isinstance(x, dict) else x) or ''
                if ':' in val:
                    prefix, code = val.split(':', 1)
                    base = _XREF_PREFIX_TO_URL.get(prefix)
                    if base:
                        xref_urls.append(base + code)
            # subsets: full URLs like http://purl.obolibrary.org/obo/mondo#rare
            #         strip the fragment to just 'rare', etc.
            subsets = []
            for s in meta.get('subsets') or []:
                if '#' in s: subsets.append(s.rsplit('#', 1)[-1])
                else:        subsets.append(s.rsplit('/', 1)[-1])
            nodes.append({
                'id': mondo_id,
                'category': 'biolink:Disease',
                'name': label,
                'description': desc,
                'same_as': '|'.join(xref_urls),
                'subsets': '|'.join(subsets),
            })

        for e in graph.get('edges', []):
            pred = (e.get('pred') or '').strip()
            if pred not in ('is_a', 'rdfs:subClassOf',
                            'http://www.w3.org/2000/01/rdf-schema#subClassOf'):
                continue
            sub = e.get('sub') or ''
            obj = e.get('obj') or ''
            if 'MONDO_' not in sub or 'MONDO_' not in obj:
                continue
            sub_id = 'MONDO:' + sub.rsplit('MONDO_', 1)[-1]
            obj_id = 'MONDO:' + obj.rsplit('MONDO_', 1)[-1]
            edges.append((sub_id, 'biolink:subclass_of', obj_id))
    log(f'  Loaded {len(nodes):,} disease nodes, {len(edges):,} subclass edges')
    return nodes, edges


def download_mondo_kgx(mondo_version=None, target_dir='./data/mondo'):
    """Legacy KGX TSV download path (kg-hub mirror). Used only if the
    primary GitHub JSON path fails. Returns (target_dir, version_used)."""
    if not HAVE_REQUESTS:
        raise RuntimeError("requests not available; pre-download Mondo KGX manually")
    os.makedirs(target_dir, exist_ok=True)

    candidates = []
    if mondo_version: candidates.append(mondo_version)
    for v in _first_tuesday_dates(months_back=24):
        if v not in candidates: candidates.append(v)
    for v in KNOWN_MONDO_RELEASES:
        if v not in candidates: candidates.append(v)

    chosen_version = None
    for v in candidates[:30]:
        url = MONDO_KGX_URL.format(ver=v)
        if _url_exists(url):
            chosen_version = v
            break
    if not chosen_version:
        return None, None  # kg-hub has no Mondo mirrored

    tar_path = os.path.join(target_dir, 'mondo_kgx_tsv.tar.gz')
    url = MONDO_KGX_URL.format(ver=chosen_version)
    log(f'  Downloading Mondo KGX {chosen_version} from {url}')
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    with open(tar_path, 'wb') as f:
        f.write(r.content)
    with tarfile.open(tar_path) as t:
        t.extractall(path=target_dir)
    return target_dir, chosen_version


def load_mondo(mondo_dir_or_file):
    """Load Mondo from either a KGX TSV directory or a JSON-LD file.
    Detects format by what's present and dispatches accordingly.
    Returns (nodes_list, edges_list) in the internal format."""
    # Single JSON file?
    if os.path.isfile(mondo_dir_or_file) and mondo_dir_or_file.endswith('.json'):
        return load_mondo_json(mondo_dir_or_file)
    # Directory containing mondo.json?
    if os.path.isdir(mondo_dir_or_file):
        json_path = os.path.join(mondo_dir_or_file, 'mondo.json')
        if os.path.isfile(json_path):
            return load_mondo_json(json_path)
        # Otherwise fall through to KGX TSV
        return load_mondo_kgx(mondo_dir_or_file)
    raise FileNotFoundError(
        f'{mondo_dir_or_file} is neither a Mondo JSON file nor a directory '
        f'containing mondo.json / mondo_kgx_tsv_*.tsv'
    )


def load_mondo_kgx(mondo_dir):
    """Load nodes and edges TSVs. Returns (nodes_list, edges_list) where
    nodes is a list of dicts and edges is a list of (subject, predicate,
    object) tuples."""
    nodes_path = os.path.join(mondo_dir, 'mondo_kgx_tsv_nodes.tsv')
    edges_path = os.path.join(mondo_dir, 'mondo_kgx_tsv_edges.tsv')
    nodes = []
    with open(nodes_path, encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            nodes.append(row)
    edges = []
    with open(edges_path, encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            edges.append((row.get('subject',''),
                          row.get('predicate',''),
                          row.get('object','')))
    log(f'  Loaded {len(nodes):,} nodes, {len(edges):,} edges (KGX TSV)')
    return nodes, edges


# ============================================================================
# GRAPH BUILDING (with networkx or stdlib fallback)
# ============================================================================
def build_disease_graph(nodes, edges):
    """Construct a directed graph from edges where:
       - direction is parent -> child (i.e. 'is_a' / subclass_of inverted)
       - nodes are restricted to biolink:Disease
       - obsolete nodes removed
    Returns (graph_obj, disease_node_ids_set). The graph object is either
    a networkx.DiGraph or a dict {node -> set(children)} depending on
    availability."""
    # Build node index first
    disease_nodes = {n['id']: n for n in nodes
                     if n.get('category') == 'biolink:Disease'
                     and 'obsolete' not in (n.get('name') or '')}
    log(f'  Disease nodes (post-obsolete filter): {len(disease_nodes):,}')

    # Edges: subclass_of, between two disease nodes
    parent_to_children = defaultdict(set)
    for subj, pred, obj in edges:
        if pred != 'biolink:subclass_of':
            continue
        if subj not in disease_nodes or obj not in disease_nodes:
            continue
        # subj is_a obj  =>  obj is parent, subj is child
        parent_to_children[obj].add(subj)

    if HAVE_NETWORKX:
        G = nx.DiGraph()
        for parent, children in parent_to_children.items():
            for c in children:
                G.add_edge(parent, c)
        return G, set(disease_nodes.keys())
    # Stdlib fallback
    return dict(parent_to_children), set(disease_nodes.keys())


def descendants_of(graph, root):
    """Return the set of all descendants of `root` in a parent->child
    graph (excluding root itself)."""
    if HAVE_NETWORKX and isinstance(graph, nx.DiGraph):
        if root not in graph: return set()
        return set(nx.descendants(graph, root))
    # Stdlib BFS
    out = set()
    queue = deque([root])
    while queue:
        cur = queue.popleft()
        for child in graph.get(cur, ()):
            if child in out: continue
            out.add(child)
            queue.append(child)
    return out


# ============================================================================
# PARSE same_as AND subsets
# ============================================================================
def parse_same_as(node):
    """Parse Mondo's `same_as` pipe-delimited field into a list of
    (vocabulary, code) tuples, keeping only the prefixes we care about."""
    out = []
    raw = (node.get('same_as') or '').strip()
    if not raw: return out
    for url in raw.split('|'):
        url = url.strip()
        for prefix, vocab in SAMEAS_PREFIXES.items():
            if url.startswith(prefix):
                out.append((vocab, url[len(prefix):]))
                break
    return out


def parse_subsets(node):
    """Return a dict of rare disease subset flags (1/0) for this node."""
    raw = (node.get('subsets') or '').strip()
    subset_set = set(raw.split('|')) if raw else set()
    return {k: 1 if k in subset_set else 0 for k in RARE_SUBSETS}


# ============================================================================
# OPTIONAL ATHENA JOIN
# ============================================================================
def load_athena_concepts(vocab_dir):
    """Load Athena CONCEPT.csv and CONCEPT_RELATIONSHIP.csv for the
    Condition domain. Returns three dicts:
      by_source[(vocab_id, code)] -> concept_id
      maps_to[concept_id]         -> standard_concept_id (Condition domain)
      std_info[concept_id]        -> {concept_name, concept_code, vocabulary_id, domain_id}

    Vocab IDs in Athena: SNOMED, ICD10CM, MeSH (some differ slightly from
    Mondo's labels; mapping handled below).
    """
    if not vocab_dir or not os.path.isdir(vocab_dir):
        return {}, {}, {}
    concept_path = os.path.join(vocab_dir, 'CONCEPT.csv')
    rel_path     = os.path.join(vocab_dir, 'CONCEPT_RELATIONSHIP.csv')
    if not (os.path.exists(concept_path) and os.path.exists(rel_path)):
        log("  (Athena join skipped - CONCEPT.csv or CONCEPT_RELATIONSHIP.csv missing)")
        return {}, {}, {}

    log('  Loading Athena CONCEPT.csv ...')
    by_source = {}     # (vocab, code) -> concept_id (any)
    std_info  = {}     # std concept_id -> info
    with open(concept_path, encoding='utf-8', errors='replace') as f:
        r = csv.DictReader(f, delimiter='\t')
        for row in r:
            vid = (row.get('vocabulary_id') or '').strip()
            code = (row.get('concept_code') or '').strip()
            cid  = (row.get('concept_id') or '').strip()
            if not cid or not code: continue
            # Normalize Athena vocab IDs to match Mondo same_as labels
            norm = vid
            if vid in ('SNOMED','SNOMED-CT','SNOMED CT'): norm = 'SNOMED'
            if vid in ('ICD10CM','ICD-10-CM'):            norm = 'ICD10CM'
            if vid in ('MeSH','MESH'):                    norm = 'MeSH'
            by_source[(norm, code)] = cid
            if row.get('standard_concept') == 'S' and (row.get('domain_id') or '') == 'Condition':
                std_info[cid] = {
                    'concept_name':    row.get('concept_name',''),
                    'vocabulary_id':   norm,
                    'concept_code':    code,
                    'domain_id':       'Condition',
                }
    log(f'    {len(by_source):,} source concepts indexed, {len(std_info):,} Condition std concepts')

    log('  Loading Athena CONCEPT_RELATIONSHIP.csv (Maps to only) ...')
    maps_to = {}   # source concept_id -> std concept_id
    with open(rel_path, encoding='utf-8', errors='replace') as f:
        r = csv.DictReader(f, delimiter='\t')
        for row in r:
            if row.get('relationship_id') != 'Maps to': continue
            maps_to[row.get('concept_id_1','')] = row.get('concept_id_2','')
    log(f'    {len(maps_to):,} Maps-to edges loaded')
    return by_source, maps_to, std_info


def to_omop_standard(vocab, code, by_source, maps_to, std_info):
    """Resolve (vocab, code) -> OMOP standard concept_id + name, or None
    if no Condition-domain standard concept is reachable."""
    src_cid = by_source.get((vocab, code))
    if not src_cid: return None
    std_cid = maps_to.get(src_cid)
    if not std_cid: return None
    info = std_info.get(std_cid)
    if not info: return None
    return {
        'standard_concept_id': std_cid,
        'standard_concept_name': info['concept_name'],
        'standard_vocabulary_id': info['vocabulary_id'],
        'standard_concept_code': info['concept_code'],
    }


# ============================================================================
# MAIN ENTRYPOINTS
# ============================================================================
def build_master_table(nodes, graph, disease_node_ids,
                       by_source, maps_to, std_info,
                       out_path):
    """Build the MONDO2OMOP master mapping table. One row per
    (Mondo-term, source-vocabulary, source-code) combination, joined to
    OMOP standard concept_id where reachable. Returns the list of rows
    and writes them to out_path."""
    log('Building master MONDO2OMOP table ...')
    # Filter to human-disease descendants, exclude susceptibility/characteristic/injury
    keep = descendants_of(graph, HUMAN_DISEASE_ANCHOR)
    keep -= descendants_of(graph, EXCLUDE_SUSCEPTIBILITY)
    keep -= descendants_of(graph, EXCLUDE_CHARACTERISTIC)
    keep -= descendants_of(graph, EXCLUDE_INJURY)
    log(f'  Kept {len(keep):,} Mondo human-disease nodes (post-exclusions)')

    nodes_by_id = {n['id']: n for n in nodes}
    rows = []
    for mondo_id in sorted(keep):
        node = nodes_by_id.get(mondo_id)
        if not node: continue
        xrefs = parse_same_as(node)
        if not xrefs: continue
        subset_flags = parse_subsets(node)
        for vocab, code in xrefs:
            omop = to_omop_standard(vocab, code, by_source, maps_to, std_info) if by_source else None
            row = {
                'mondo_id':        mondo_id,
                'mondo_label':     node.get('name','') or '',
                'mondo_description': (node.get('description','') or '').replace('\n',' ')[:500],
                'source_vocabulary': vocab,
                'source_code':     code,
                'standard_concept_id':   omop['standard_concept_id']   if omop else '',
                'standard_concept_name': omop['standard_concept_name'] if omop else '',
                'standard_vocabulary':   omop['standard_vocabulary_id']if omop else '',
                'standard_concept_code': omop['standard_concept_code'] if omop else '',
            }
            row.update(subset_flags)
            rows.append(row)
    log(f'  {len(rows):,} (Mondo, source-code) rows written')
    write_tsv(out_path, rows,
              fieldnames=['mondo_id','mondo_label','mondo_description',
                          'source_vocabulary','source_code',
                          'standard_concept_id','standard_concept_name',
                          'standard_vocabulary','standard_concept_code',
                          *RARE_SUBSETS])
    return rows


def build_cohort(target_mondo_id, nodes, graph,
                 by_source, maps_to, std_info,
                 out_dir):
    """Build a code list and OMOP concept list for everything at or
    below a target Mondo term. Returns (code_rows, omop_rows). Writes
    two TSVs to out_dir."""
    log(f'Building cohort for {target_mondo_id} ...')
    nodes_by_id = {n['id']: n for n in nodes}
    if target_mondo_id not in nodes_by_id:
        log(f'  WARNING: {target_mondo_id} not found in Mondo nodes')
        return [], []
    target_label = nodes_by_id[target_mondo_id].get('name') or ''
    log(f'  Target: {target_mondo_id}  {target_label}')

    # Members = target + descendants
    members = {target_mondo_id} | descendants_of(graph, target_mondo_id)
    log(f'  {len(members):,} Mondo terms in cohort (target + descendants)')

    code_rows = []
    omop_rows_seen = set()
    omop_rows = []
    for mondo_id in sorted(members):
        node = nodes_by_id.get(mondo_id)
        if not node: continue
        xrefs = parse_same_as(node)
        subset_flags = parse_subsets(node)
        for vocab, code in xrefs:
            base = {
                'mondo_id':    mondo_id,
                'mondo_label': node.get('name','') or '',
                'source_vocabulary': vocab,
                'source_code': code,
            }
            base.update(subset_flags)
            code_rows.append(base)
            omop = to_omop_standard(vocab, code, by_source, maps_to, std_info) if by_source else None
            if omop and omop['standard_concept_id'] not in omop_rows_seen:
                omop_rows_seen.add(omop['standard_concept_id'])
                omop_rows.append({
                    'mondo_id':    mondo_id,
                    'mondo_label': node.get('name','') or '',
                    'standard_concept_id':   omop['standard_concept_id'],
                    'standard_concept_name': omop['standard_concept_name'],
                    'standard_vocabulary':   omop['standard_vocabulary_id'],
                    'standard_concept_code': omop['standard_concept_code'],
                })

    safe_id = target_mondo_id.replace(':','_')
    code_path = os.path.join(out_dir, f'cohort_{safe_id}_codes.tsv')
    omop_path = os.path.join(out_dir, f'cohort_{safe_id}_omop.tsv')
    write_tsv(code_path, code_rows,
              fieldnames=['mondo_id','mondo_label','source_vocabulary','source_code',
                          *RARE_SUBSETS])
    if omop_rows:
        write_tsv(omop_path, omop_rows,
                  fieldnames=['mondo_id','mondo_label','standard_concept_id',
                              'standard_concept_name','standard_vocabulary',
                              'standard_concept_code'])
    log(f'  Wrote {code_path} ({len(code_rows):,} rows)')
    log(f'  Wrote {omop_path} ({len(omop_rows):,} rows)' if omop_rows
        else '  (No Athena vocabulary supplied - cohort_*_omop.tsv skipped)')
    return code_rows, omop_rows


def write_tsv(path, rows, fieldnames):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter='\t',
                            quoting=csv.QUOTE_MINIMAL, extrasaction='ignore')
        w.writeheader()
        for r in rows: w.writerow(r)


def main(mondo_kgx_dir=None,
         mondo_version=None,
         vocab_dir=None,
         target_mondo_ids=None,
         out_root='./mondo_omop_output'):
    """Run the bridge end-to-end.

    Args:
      mondo_kgx_dir   Path to either (a) a directory containing the Mondo
                      KGX TSV files (mondo_kgx_tsv_nodes.tsv +
                      mondo_kgx_tsv_edges.tsv), (b) a directory containing
                      mondo.json, or (c) the path to mondo.json directly.
                      If None, downloads Mondo JSON from GitHub releases.
      mondo_version   Optional Mondo release date (YYYY-MM-DD) to pin.
                      Used only for the kg-hub KGX fallback path.
      vocab_dir       Athena vocabulary directory. If supplied, every cohort
                      is additionally joined to OMOP standard concept_ids.
      target_mondo_ids List of Mondo IDs to build per-cohort TSVs for.
                       e.g. ['MONDO:0019056', 'MONDO:0005027']
      out_root        Output directory.
    """
    log('=' * 72)
    log('Mondo-OMOP bridge -- starting')
    log('=' * 72)
    log(f'NetworkX available: {HAVE_NETWORKX}')
    os.makedirs(out_root, exist_ok=True)

    version_used = mondo_version
    mondo_source_path = mondo_kgx_dir

    if mondo_source_path is None:
        # Primary path: download Mondo JSON from GitHub releases.
        # Falls back to kg-hub KGX TSV if that fails.
        log('Resolving Mondo data source...')
        download_dir = os.path.join(out_root, 'mondo_data')
        try:
            mondo_source_path, version_used = download_mondo_json(download_dir)
        except Exception as primary_err:
            log(f'  Primary GitHub JSON path failed: {primary_err}')
            log('  Trying kg-hub KGX TSV fallback...')
            kgx_dir, kgx_ver = download_mondo_kgx(mondo_version, download_dir)
            if kgx_dir:
                mondo_source_path = kgx_dir
                version_used = kgx_ver
            else:
                raise RuntimeError(
                    'Could not retrieve Mondo from any source.\n'
                    'Manual workaround:\n'
                    '  1. Download https://github.com/monarch-initiative/mondo/'
                    'releases/latest/download/mondo.json (~100 MB)\n'
                    f'  2. Save it to {download_dir}/mondo.json\n'
                    '  3. Re-run with mondo_kgx_dir=that_path'
                ) from primary_err

    log(f'Mondo source: {mondo_source_path}')
    log(f'Mondo release: {version_used or "(supplied input, version unknown)"}')

    nodes, edges = load_mondo(mondo_source_path)
    graph, disease_node_ids = build_disease_graph(nodes, edges)

    by_source, maps_to, std_info = load_athena_concepts(vocab_dir)

    tag = version_used or 'unknown'
    master_path = os.path.join(out_root, f'MONDO2OMOP_{tag}.tsv')
    master_rows = build_master_table(nodes, graph, disease_node_ids,
                                      by_source, maps_to, std_info,
                                      master_path)
    log(f'Master table written: {master_path}')

    target_mondo_ids = target_mondo_ids or []
    for tid in target_mondo_ids:
        build_cohort(tid, nodes, graph,
                     by_source, maps_to, std_info, out_root)

    return {
        'mondo_version': version_used,
        'master_table': master_path,
        'master_rows':  len(master_rows),
        'targets':      target_mondo_ids,
        'out_root':     out_root,
    }


if __name__ == '__main__':
    # Default: build the ALS-spectrum and epilepsy cohorts as a worked example
    main(target_mondo_ids=['MONDO:0004976','MONDO:0005027'])
