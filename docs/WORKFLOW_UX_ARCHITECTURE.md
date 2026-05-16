# DataLens — F-1 through F-4 + Version Tracking

## Context
Adding four UI/backend features to the DataLens compare app. The `differ.py` column_map support is **already implemented** (lines 89–103). Everything else is net-new. Zero existing flows must break.

---

## What's Already Done

- `differ.py`: `column_map` parameter fully implemented — renames lf2 columns into f1 namespace before join.
- `web/api.py`: `_load_lazy_frame` is already imported from `compare.py` (line 35), so the value-set endpoint can reuse it directly.

---

## Change Flow

```
F-2 key dropdowns      → loadFileInfo() populates them from S[slot].columns
                       → startRun() reads key-f1/key-f2 instead of key-cols
                       
F-3 col-map panel      → buildColMapping(S.f1.columns, S.f2.columns) after both loaded
                       → user accepts/rejects fuzzy suggestions → S.colMap.userMapped
                       → startRun() sends column_map to /api/compare

backend column_map     → CompareAPIRequest gets column_map field
                       → _run_compare_job merges key pair → column_map, passes to CoreCompareRequest
                       → CompareRequest gets column_map field, threads to diff_files()

F-4 value set          → vset tab reads S.f1/S.f2 paths (no new /api/headers needed)
                       → dropdowns populated from S.f1.columns / S.f2.columns
                       → POST /api/value_set_compare → new endpoint in api.py
```

---

## Execution Order

1. **`compare.py`** — add `column_map` to `CompareRequest`; pass it to `diff_files()` in `run_compare()`
2. **`web/api.py`** — extend `CompareAPIRequest`; update `_run_compare_job`; add `/api/value_set_compare`
3. **`web/static/index.html`** — CSS + HTML + JS changes (targeted edits, not a rewrite)

---

## Step 1 — `compare.py`

**File:** `compare.py`

Two edits:

### 1a. `CompareRequest` dataclass (line 22)
Add field after `ignore_rules`:
```python
column_map: Optional[list[dict]] = None
```

### 1b. `diff_files()` call in `run_compare()` (line 144)
Add `column_map=request.column_map` to the call:
```python
diff = diff_files(
    lf1, m1, lf2, m2,
    key_columns=key_columns,
    ignore_rules=request.ignore_rules,
    progress=progress,
    cancel_token=cancel_token,
    column_map=request.column_map,   # ← add this
)
```

---

## Step 2 — `web/api.py`

### 2a. Extend `CompareAPIRequest` (after line 112)
```python
class CompareAPIRequest(BaseModel):
    file1: str
    file2: str
    sheet1: Optional[str] = None
    sheet2: Optional[str] = None
    key_columns: Optional[list[str]] = None
    ignore_case: bool = False
    ignore_whitespace: bool = False
    output_dir: Optional[str] = None
    key_columns_f1: Optional[list[str]] = None   # new
    key_columns_f2: Optional[list[str]] = None   # new
    column_map: Optional[list[dict]] = None       # new
```

### 2b. Update `_run_compare_job` (around line 416)
Replace the `CoreCompareRequest` construction with:
```python
# Resolve key_columns: prefer per-file keys, fall back to shared key_columns
key_columns = req.key_columns_f1 or req.key_columns

# Merge asymmetric key columns into column_map so the f2 key col gets renamed to f1 name
column_map = list(req.column_map or [])
if req.key_columns_f1 and req.key_columns_f2:
    for f1k, f2k in zip(req.key_columns_f1, req.key_columns_f2):
        if f1k != f2k:
            column_map.append({"f1": f1k, "f2": f2k})

core_req = CoreCompareRequest(
    file1=Path(req.file1),
    file2=Path(req.file2),
    sheet1=req.sheet1,
    sheet2=req.sheet2,
    key_columns=key_columns,
    ignore_rules=IgnoreRules(case=req.ignore_case, whitespace=req.ignore_whitespace),
    output_dir=out_dir,
    column_map=column_map or None,
)
```

### 2c. New `/api/value_set_compare` endpoint
Add after the existing `/api/browse` endpoint. Uses `_load_lazy_frame` (already imported) and `load_metadata` (already imported):

```python
class ValueSetPair(BaseModel):
    f1_col: str
    f2_col: str

class ValueSetRequest(BaseModel):
    file1: str
    file2: str
    sheet1: Optional[str] = None
    sheet2: Optional[str] = None
    pairs: list[ValueSetPair]

@app.post("/api/value_set_compare")
async def value_set_compare(req: ValueSetRequest):
    """Compare distinct value sets for column pairs across two files."""
    import polars as pl
    path1, path2 = Path(req.file1), Path(req.file2)
    for p in (path1, path2):
        if not p.exists():
            raise HTTPException(404, f"File not found: {p}")
    m1 = load_metadata(path1, req.sheet1)
    m2 = load_metadata(path2, req.sheet2)
    temp_files: list[Path] = []
    try:
        lf1 = _load_lazy_frame(path1, m1, temp_files)
        lf2 = _load_lazy_frame(path2, m2, temp_files)
        results = []
        for pair in req.pairs:
            vals1 = set(
                lf1.select(pl.col(pair.f1_col).cast(pl.Utf8)).collect()[pair.f1_col].to_list()
            ) - {None, "None", ""}
            vals2 = set(
                lf2.select(pl.col(pair.f2_col).cast(pl.Utf8)).collect()[pair.f2_col].to_list()
            ) - {None, "None", ""}
            only_f1 = sorted(vals1 - vals2)[:500]
            only_f2 = sorted(vals2 - vals1)[:500]
            in_both = len(vals1 & vals2)
            results.append({
                "f1_col": pair.f1_col,
                "f2_col": pair.f2_col,
                "f1_total": len(vals1),
                "f2_total": len(vals2),
                "in_both": in_both,
                "only_f1_count": len(vals1 - vals2),
                "only_f2_count": len(vals2 - vals1),
                "only_f1_sample": only_f1,
                "only_f2_sample": only_f2,
            })
        return {"results": results}
    finally:
        for tmp in temp_files:
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
```

**Note:** `/api/headers` from the draft plan is skipped — `S.f1.columns` / `S.f2.columns` from the already-loaded `/api/file-info` responses are used directly in the frontend.

---

## Step 3 — `web/static/index.html`

All edits are targeted — no full rewrite.

### 3a. CSS additions (inside `<style>`, after existing `.file-grid` block ~line 81)

Replace `.file-grid` rule to stack vertically:
```css
.file-grid{display:grid;grid-template-columns:1fr;gap:12px;margin-bottom:14px}
```
_(Removes the `1fr 1fr` two-column; each file card now spans full width.)_

Add column-map panel styles:
```css
.col-map-panel{margin-top:12px;border-top:1px solid var(--bdr);padding-top:12px}
.col-map-group{margin-bottom:10px}
.col-map-group-title{font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);margin-bottom:5px}
.col-map-row{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:11px;border-bottom:1px solid rgba(51,65,85,.2)}
.col-map-row:last-child{border-bottom:none}
.col-map-name{flex:1;color:var(--txt)}
.col-map-arrow{color:var(--mut);flex-shrink:0}
.col-map-accept{background:var(--grn);border:none;color:#fff;font-size:10px;padding:1px 8px;cursor:pointer;font-family:var(--font)}
.col-map-reject{background:transparent;border:1px solid var(--bdr);color:var(--mut);font-size:10px;padding:1px 8px;cursor:pointer;font-family:var(--font)}
.col-map-rejected{text-decoration:line-through;opacity:.4}
```

Add key-row styles (two selects side by side):
```css
.key-row{display:flex;align-items:flex-end;gap:10px}
.key-row-g{flex:1}
.key-row-sep{color:var(--mut);font-size:14px;flex-shrink:0;padding-bottom:8px}
```

Add value-set styles:
```css
.vset-card{margin-bottom:12px}
.vset-pair-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.vset-pair-row select{flex:1}
.vset-pair-sep{color:var(--mut);flex-shrink:0}
.vset-results{margin-top:14px}
.vset-result-block{background:var(--bg);border:1px solid var(--bdr);padding:12px;margin-bottom:10px}
.vset-result-title{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin-bottom:8px}
.vset-counts{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.vset-count{font-size:11px;padding:3px 10px;border-left:3px solid}
.vset-both{border-color:var(--grn);color:var(--grn)}
.vset-onlyA{border-color:var(--red);color:var(--red)}
.vset-onlyB{border-color:var(--yel);color:var(--yel)}
.vset-samples{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.vset-sample-col h5{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin-bottom:5px}
.vset-sample-list{font-size:11px;color:var(--txt);max-height:150px;overflow-y:auto;background:var(--sur2);padding:6px 8px}
.vset-sample-item{padding:1px 0;border-bottom:1px solid rgba(51,65,85,.2)}
.vset-sample-item:last-child{border-bottom:none}
```

### 3b. Header — version badge + new tab

In `<header>`, after the `.logo-sub` span, add version:
```html
<span class="logo-sub">Compare · Validate · CSV &amp; Excel · v1.1.0</span>
```

In `.tabs` div, add Value Set tab after Validate:
```html
<button class="tab-btn" id="tb-vset" onclick="switchTab('vset')">Value Set</button>
```

### 3c. File grid — key dropdowns

Replace `<input type="text" id="key-cols" ...>` (and its wrapping `.opt-g`) with:
```html
<div class="key-row">
  <div class="key-row-g">
    <span class="lbl" style="font-size:9px">Reference key</span>
    <select id="key-f1" onchange="onKeyChange()"><option value="">— Auto-detect —</option></select>
  </div>
  <span class="key-row-sep">↔</span>
  <div class="key-row-g">
    <span class="lbl" style="font-size:9px">Candidate key</span>
    <select id="key-f2" onchange="onKeyChange()"><option value="">— Auto-detect —</option></select>
  </div>
</div>
```

### 3d. Column mapping panel

Add after `.opts-row` div (before `#val-rules-panel`), still inside the inputs card:
```html
<div id="col-map-panel" class="col-map-panel" style="display:none">
  <div class="card-title">Column Mapping</div>
  <div id="col-map-matched"></div>
  <div id="col-map-only-a"></div>
  <div id="col-map-only-b"></div>
  <div id="col-map-fuzzy"></div>
</div>
```

### 3e. Value Set section in `<main>`

Add after `#card-res` and before `</main>`:
```html
<div id="vset-section" style="display:none">
  <div class="card vset-card">
    <div class="card-title">Value Set Compare</div>
    <div id="vset-pair-list"></div>
    <button class="btn-sm" onclick="vsetAddPair()" id="vset-add-btn" style="margin-bottom:10px">+ Add Column Pair</button>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn-primary" id="vset-run-btn" onclick="startValueSetCompare()" disabled>▶ Compare Values</button>
      <span id="vset-status" style="font-size:11px;color:var(--mut)"></span>
    </div>
  </div>
  <div id="vset-results"></div>
</div>
```

### 3f. JS — version constant (top of `<script>`)
```js
const APP_VERSION = '1.1.0';
```

### 3g. JS — state additions

In the `S` object, add:
```js
colMap: null,   // { matched, onlyA, onlyB, fuzzy, userMapped, rejected }
```

### 3h. JS — `switchTab()` update

Extend to handle `vset` tab:
```js
function switchTab(tab) {
  S.tab = tab;
  document.getElementById('tb-compare').classList.toggle('active', tab==='compare');
  document.getElementById('tb-validate').classList.toggle('active', tab==='validate');
  document.getElementById('tb-vset').classList.toggle('active', tab==='vset');
  
  const isVset = tab === 'vset';
  ge('vset-section').style.display = isVset ? '' : 'none';
  ge('card-res').style.display = isVset ? 'none' : (S.result ? '' : 'none');
  
  ge('run-lbl').textContent = tab==='compare' ? '▶ Compare Files' : '▶ Validate Files';
  ge('val-rules-panel').style.display = tab==='validate' ? '' : 'none';
  
  // Run button hidden in vset tab (has its own run button)
  ge('run-btn').style.display = isVset ? 'none' : '';
  
  if (isVset) vsetRefreshDropdowns();
  checkRunnable();
}
```

### 3i. JS — `loadFileInfo()` additions

After `S[slot] = info;` (line ~1165), add calls to populate key select and trigger col-map:
```js
populateKeySelect(slot, info.columns);
if (S.f1 && S.f2) buildColMappingPanel();
if (S.f1 && S.f2 && S.tab === 'vset') vsetRefreshDropdowns();
```

### 3j. JS — `lockInputs()` additions

Add `key-f1`, `key-f2` to the locked IDs list:
```js
function lockInputs(lock) {
  ['path-f1','path-f2','sheet-f1','sheet-f2','key-f1','key-f2','ign-case','ign-ws','br-btn-f1','br-btn-f2']
    .forEach(id=>{ const el=ge(id); if(el) el.disabled=lock; });
  ge('run-btn').disabled=lock;
}
```

### 3k. JS — `startRun()` — send column_map + per-file keys

Replace the `keyCols` reading with per-file key select reading:
```js
const keyF1 = ge('key-f1').value;
const keyF2 = ge('key-f2').value;
const keyColsF1 = keyF1 ? [keyF1] : null;
const keyColsF2 = keyF2 ? [keyF2] : null;
// For validate tab, use f1 key only (single-file semantics)
const keyCols = keyF1 ? [keyF1] : null;

// Build column_map from accepted fuzzy + always-matched (user-mapped)
const column_map = S.colMap?.userMapped || [];
```

In the `apiPost('/api/compare', {...})` call, add:
```js
key_columns_f1: keyColsF1,
key_columns_f2: keyColsF2,
column_map: column_map.length ? column_map : null,
```

### 3l. JS — new functions to add

```js
// F-2 key dropdown population
function populateKeySelect(slot, columns) {
  const sel = ge(`key-${slot}`);
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">— Auto-detect —</option>' +
    (columns||[]).map(c => `<option value="${ea(c)}"${c===cur?' selected':''}>${esc(c)}</option>`).join('');
}

function onKeyChange() {
  // If both key selects have the same column and col-map panel is shown, no-op; just re-check runnable
  checkRunnable();
}

// F-3 column mapping
function normCol(s){ return s.toLowerCase().replace(/[\s_\-.]/g,''); }

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  const d = Array.from({length:m+1}, (_,i) => Array.from({length:n+1}, (_,j) => i===0?j:j===0?i:0));
  for (let i=1;i<=m;i++) for (let j=1;j<=n;j++)
    d[i][j] = a[i-1]===b[j-1] ? d[i-1][j-1] : 1+Math.min(d[i-1][j],d[i][j-1],d[i-1][j-1]);
  return d[m][n];
}

function buildColMapping(cols1, cols2) {
  const set2 = new Set(cols2);
  const set1 = new Set(cols1);
  const matched   = cols1.filter(c => set2.has(c));
  const onlyA     = cols1.filter(c => !set2.has(c));
  const onlyB     = cols2.filter(c => !set1.has(c));
  // Fuzzy: for each unmatched A col, find the best B col by normalised levenshtein
  const fuzzy = [];
  for (const a of onlyA) {
    let best = null, bestScore = Infinity;
    for (const b of onlyB) {
      const na = normCol(a), nb = normCol(b);
      const maxLen = Math.max(na.length, nb.length);
      if (!maxLen) continue;
      const score = levenshtein(na, nb) / maxLen;
      if (score < 0.4 && score < bestScore) { best = b; bestScore = score; }
    }
    if (best) fuzzy.push({f1: a, f2: best, score: bestScore});
  }
  return { matched, onlyA, onlyB, fuzzy, userMapped: [], rejected: new Set() };
}

function buildColMappingPanel() {
  if (!S.f1?.columns || !S.f2?.columns) return;
  S.colMap = buildColMapping(S.f1.columns, S.f2.columns);
  renderColMapPanel();
}

function renderColMapPanel() {
  const p = ge('col-map-panel');
  const cm = S.colMap;
  if (!cm) { p.style.display='none'; return; }

  // Only show if there are extra/fuzzy columns worth surfacing
  const hasInteresting = cm.onlyA.length || cm.onlyB.length || cm.fuzzy.length;
  p.style.display = hasInteresting ? '' : 'none';

  // Matched (informational)
  ge('col-map-matched').innerHTML = cm.matched.length
    ? `<div class="col-map-group"><div class="col-map-group-title">Matched (${cm.matched.length})</div>` +
      cm.matched.map(c=>`<div class="col-map-row"><span class="col-map-name">${esc(c)}</span><span style="color:var(--grn)">✓</span></div>`).join('') + '</div>'
    : '';

  // Only in A
  ge('col-map-only-a').innerHTML = cm.onlyA.length
    ? `<div class="col-map-group"><div class="col-map-group-title" style="color:var(--yel)">Only in Reference (${cm.onlyA.length})</div>` +
      cm.onlyA.filter(c=>!cm.fuzzy.find(f=>f.f1===c)).map(c=>
        `<div class="col-map-row"><span class="col-map-name">${esc(c)}</span><span style="color:var(--yel)">⚠ not in Candidate</span></div>`
      ).join('') + '</div>'
    : '';

  // Only in B
  ge('col-map-only-b').innerHTML = cm.onlyB.length
    ? `<div class="col-map-group"><div class="col-map-group-title" style="color:var(--yel)">Only in Candidate (${cm.onlyB.length})</div>` +
      cm.onlyB.filter(c=>!cm.fuzzy.find(f=>f.f2===c)).map(c=>
        `<div class="col-map-row"><span class="col-map-name">${esc(c)}</span><span style="color:var(--yel)">⚠ not in Reference</span></div>`
      ).join('') + '</div>'
    : '';

  // Fuzzy suggestions
  if (cm.fuzzy.length) {
    ge('col-map-fuzzy').innerHTML =
      `<div class="col-map-group"><div class="col-map-group-title" style="color:var(--pur)">Possible Renames (${cm.fuzzy.length}) — accept to map during compare</div>` +
      cm.fuzzy.map((f,i) => {
        const accepted = cm.userMapped.find(m=>m.f1===f.f1&&m.f2===f.f2);
        const rejected = cm.rejected.has(i);
        return `<div class="col-map-row" id="fuzzy-row-${i}">
          <span class="col-map-name ${rejected?'col-map-rejected':''}">${esc(f.f1)}</span>
          <span class="col-map-arrow">→</span>
          <span class="col-map-name ${rejected?'col-map-rejected':''}">${esc(f.f2)}</span>
          ${accepted
            ? '<span style="color:var(--grn);font-size:11px">✓ Mapped</span>'
            : rejected
              ? '<span style="color:var(--mut);font-size:11px">Ignored</span>'
              : `<button class="col-map-accept" onclick="colMapAccept(${i})">Accept</button>
                 <button class="col-map-reject" onclick="colMapReject(${i})">Ignore</button>`
          }
        </div>`;
      }).join('') + '</div>';
  } else {
    ge('col-map-fuzzy').innerHTML = '';
  }
}

function colMapAccept(i) {
  const f = S.colMap.fuzzy[i];
  if (!S.colMap.userMapped.find(m=>m.f1===f.f1&&m.f2===f.f2))
    S.colMap.userMapped.push({f1: f.f1, f2: f.f2});
  S.colMap.rejected.delete(i);
  renderColMapPanel();
}

function colMapReject(i) {
  S.colMap.rejected.add(i);
  S.colMap.userMapped = S.colMap.userMapped.filter(m=>m.f1!==S.colMap.fuzzy[i].f1);
  renderColMapPanel();
}

// F-4 value set
let vsetPairCount = 0;

function vsetRefreshDropdowns() {
  const cols1 = S.f1?.columns || [];
  const cols2 = S.f2?.columns || [];
  ge('vset-run-btn').disabled = !(S.f1 && S.f2 && vsetPairCount > 0);
  
  // Refresh all existing pair dropdowns
  document.querySelectorAll('.vset-sel-f1').forEach(sel => {
    const cur = sel.value;
    sel.innerHTML = '<option value="">— pick column —</option>' +
      cols1.map(c=>`<option value="${ea(c)}"${c===cur?' selected':''}>${esc(c)}</option>`).join('');
  });
  document.querySelectorAll('.vset-sel-f2').forEach(sel => {
    const cur = sel.value;
    sel.innerHTML = '<option value="">— pick column —</option>' +
      cols2.map(c=>`<option value="${ea(c)}"${c===cur?' selected':''}>${esc(c)}</option>`).join('');
  });
}

function vsetAddPair() {
  if (vsetPairCount >= 3) return;
  vsetPairCount++;
  const id = vsetPairCount;
  const cols1 = S.f1?.columns || [];
  const cols2 = S.f2?.columns || [];
  const makeOpts = (cols) => '<option value="">— pick column —</option>' +
    cols.map(c=>`<option value="${ea(c)}">${esc(c)}</option>`).join('');
  
  const row = document.createElement('div');
  row.className = 'vset-pair-row';
  row.id = `vset-pair-${id}`;
  row.innerHTML = `
    <select class="vset-sel-f1" id="vset-f1-${id}" onchange="vsetCheckRunnable()">${makeOpts(cols1)}</select>
    <span class="vset-pair-sep">↔</span>
    <select class="vset-sel-f2" id="vset-f2-${id}" onchange="vsetCheckRunnable()">${makeOpts(cols2)}</select>
    <button class="btn-ghost" onclick="vsetRemovePair(${id})" title="Remove">✕</button>`;
  ge('vset-pair-list').appendChild(row);
  
  if (vsetPairCount >= 3) ge('vset-add-btn').disabled = true;
  vsetCheckRunnable();
}

function vsetRemovePair(id) {
  const row = ge(`vset-pair-${id}`);
  if (row) row.remove();
  vsetPairCount = ge('vset-pair-list').querySelectorAll('.vset-pair-row').length;
  ge('vset-add-btn').disabled = false;
  vsetCheckRunnable();
}

function vsetCheckRunnable() {
  const pairs = vsetGetPairs();
  ge('vset-run-btn').disabled = !(S.f1 && S.f2 && pairs.length > 0);
}

function vsetGetPairs() {
  const pairs = [];
  ge('vset-pair-list').querySelectorAll('.vset-pair-row').forEach(row => {
    const f1c = row.querySelector('.vset-sel-f1')?.value;
    const f2c = row.querySelector('.vset-sel-f2')?.value;
    if (f1c && f2c) pairs.push({f1_col: f1c, f2_col: f2c});
  });
  return pairs;
}

async function startValueSetCompare() {
  const pairs = vsetGetPairs();
  if (!pairs.length || !S.f1 || !S.f2) return;
  ge('vset-status').textContent = 'Running…';
  ge('vset-run-btn').disabled = true;
  ge('vset-results').innerHTML = '';
  try {
    const resp = await apiPost('/api/value_set_compare', {
      file1: ge('path-f1').value.trim(),
      file2: ge('path-f2').value.trim(),
      sheet1: S.f1.sheet_name || null,
      sheet2: S.f2.sheet_name || null,
      pairs,
    });
    ge('vset-status').textContent = '';
    renderVsetResults(resp.results);
  } catch(e) {
    ge('vset-status').textContent = '';
    ge('vset-results').innerHTML = `<div class="banner-err">${esc(e.message)}</div>`;
  } finally {
    ge('vset-run-btn').disabled = false;
  }
}

function renderVsetResults(results) {
  const el = ge('vset-results');
  el.innerHTML = results.map(r => `
    <div class="card vset-result-block">
      <div class="vset-result-title">${esc(r.f1_col)} ↔ ${esc(r.f2_col)}</div>
      <div class="vset-counts">
        <span class="vset-count vset-both">Both: ${r.in_both.toLocaleString()}</span>
        <span class="vset-count vset-onlyA">Only in Ref: ${r.only_f1_count.toLocaleString()}</span>
        <span class="vset-count vset-onlyB">Only in Cand: ${r.only_f2_count.toLocaleString()}</span>
      </div>
      ${(r.only_f1_sample.length || r.only_f2_sample.length) ? `
      <div class="vset-samples">
        <div class="vset-sample-col">
          <h5>Only in Reference${r.only_f1_count>500?' (sample 500)':''}</h5>
          <div class="vset-sample-list">${r.only_f1_sample.map(v=>`<div class="vset-sample-item">${esc(String(v))}</div>`).join('')||'<span style="color:var(--mut)">—</span>'}</div>
        </div>
        <div class="vset-sample-col">
          <h5>Only in Candidate${r.only_f2_count>500?' (sample 500)':''}</h5>
          <div class="vset-sample-list">${r.only_f2_sample.map(v=>`<div class="vset-sample-item">${esc(String(v))}</div>`).join('')||'<span style="color:var(--mut)">—</span>'}</div>
        </div>
      </div>` : ''}
    </div>`).join('');
}
```

### 3m. Help modal footer — version

Replace:
```
DataLens · CSV &amp; Excel comparison engine powered by Polars · Exports saved to <code>exports/</code>
```
With:
```
DataLens v1.1.0 · CSV &amp; Excel comparison engine powered by Polars · Updated 2026-05-15 · Exports saved to <code>exports/</code>
```

---

## Critical Invariants (preserved throughout)

| Invariant | Preserved |
|-----------|-----------|
| INV-1 `pl.scan_csv()` only | Value set endpoint uses `_load_lazy_frame` which already enforces this |
| INV-4 Pydantic at API boundary only | `ValueSetRequest`/`ValueSetPair` are Pydantic; internal logic uses plain lists |
| INV-5 Diff/Validator separation | No changes to differ.py, validator.py |
| INV-6 Full-file counts only | Value set uses `.collect()` on the full lazy scan; no sampling for counts |
| INV-7 Eager exports | Value set response is in-memory JSON, no file writes |

---

## Verification

```bash
set PYTHONUTF8=1
python run_web.py
# Opens at http://127.0.0.1:8787
```

Manual test checklist:
1. Load two CSV files → they stack vertically (each full-width card) ✓
2. Key dropdowns populate from columns of each file ✓
3. Column mapping panel appears (if columns differ) with matched/extra/fuzzy groups ✓
4. Accept a fuzzy rename → runs Compare → column_map included in request, diff correct ✓
5. Value Set tab visible in header ✓
6. Value Set tab: add up to 3 pairs, run → see counts + sample values ✓
7. Version badge `v1.1.0` visible in header subtitle ✓
8. Validate tab still works (lockInputs covers key-f1/key-f2 but validate uses key-f1 only) ✓
9. History/export/profile result tabs still work ✓
10. Cancel mid-run still works ✓


------------- summary-----------
  # DataLens — Feature Requirements (Saved Spec)

  ## App context
  DataLens is a CSV/Excel compare and validate web app.
  Backend: Python, FastAPI, Polars (LazyFrame only — scan_csv, never read_csv).
  Frontend: Single-file vanilla JS (web/static/index.html), dark theme, Tabulator 6.3.
  Server: run_web.py → web/api.py on port 8787. No React, no build step.

  ## Already implemented features (do not re-implement, just preserve)
  - FEATURE F-1: Vertical file upload layout (.file-stack, stacked cards)
  - FEATURE F-2: Key column selection via dropdown (populated from loaded file headers)
  - FEATURE F-3: Column discovery & mapping panel (auto-match, extra columns, fuzzy
    Levenshtein suggestions with accept/reject, user-defined pairs sent as column_map)
  - FEATURE F-4: Value Set Compare tab (/api/value_set_compare endpoint, unique value
    sets per column pair: only-in-A / only-in-B / in-both with counts and samples)
  - Version badge v1.1.0 in header and help modal

  ## Core design intent (for any future work)

  ### File loading UX
  - File cards are stacked vertically. Designed to support 3+ files in future.
  - Each card: label (Reference / Candidate), path input, browse button, inline chip
    showing filename · rows · cols · size once loaded.
  - Sheet picker appears below the card only for Excel files with multiple sheets.

  ### Key column
  - Shown as two independent dropdowns (one per file), populated from the file's
    column headers the moment the file loads.
  - Default option is "Auto-detect" (empty). User can override per file.
  - If Reference key column name ≠ Candidate key column name, the pair is
    automatically added to column_map so differ.py renames before joining.

  ### Column mapping
  - After both files load, a panel auto-appears between the file cards and Run button.
  - Groups: Matched (same name in both) / Extra in Reference / Extra in Candidate /
    Fuzzy suggestions (Levenshtein ≤ 2 on normalised names).
  - Fuzzy suggestions show ✓ (accept → moves to user-mapped) and ✕ (reject).
  - All accepted/user-mapped pairs are sent as column_map: [{f1, f2}] to /api/compare.
  - Columns not in the map and not auto-matched are excluded from diff by default.

  ### Value Set Compare
  - Separate tab "Value Set" in the header.
  - User picks up to 3 column pairs (one column from each file per pair).
  - Use case: transactional data where an ID appears many times — user just wants
    "what unique values are in column A of file 1 vs column B of file 2?"
  - Results per pair: count + sample (≤500) for only-in-file1, only-in-file2, in-both.
  - Click a count badge to expand/collapse the sample values inline.
  - Backend: POST /api/value_set_compare — uses pl.scan_csv().select([col]).unique().collect().

  ### Backend invariants (never violate)
  - Always pl.scan_csv(). Never pl.read_csv(). .collect() only for stats/samples.
  - Pydantic only at API boundaries. Internal types are @dataclass.
  - column_map applies in differ.py before any join: renames f2 columns into f1
    name-space so all subsequent logic uses f1 column names throughout.
  - Full-file counts only. is_full_count=False if key is non-unique.

  ## Files to modify for new features
  - web/static/index.html — all UI (single file, vanilla JS)
  - web/api.py — new endpoints, extend request models
  - differ.py — column_map already wired; extend if needed
  - compare.py — column_map already in CompareRequest; extend if needed
