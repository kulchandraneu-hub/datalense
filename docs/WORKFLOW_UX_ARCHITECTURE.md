# DataLens — Phase 2.6 Workflow & Reconciliation UX Architecture

_Created: 2026-05-16. Status: Proposal — awaiting approval before implementation._

---

## 1. Current State (as of Phase 2.5)

| Feature | Status |
|---------|--------|
| Vertical file-card layout (F-1) | Implemented |
| Key-column dropdowns per file (F-2) | Implemented |
| Column mapping panel — auto-match, extra cols, fuzzy Levenshtein (F-3) | Implemented |
| Value Set Compare tab (F-4) | Implemented |
| Full-file diff counts, sentinel-based added/removed | Implemented |
| Regression test suite (quick / regression / benchmark tiers) | Implemented |

The app is a solid **two-file, master-data diff tool** with a basic but functional reconciliation panel.

---

## 2. What Phase 2.6 Must Solve

Six user problems that the current architecture cannot cleanly address:

| # | Problem | Root cause |
|---|---------|-----------|
| P-1 | User cannot say "only compare these 5 columns, ignore the rest" | No column include/exclude control |
| P-2 | Column mapping panel shows what columns exist but not what the compare will actually do | No "compare plan" explainer |
| P-3 | Key column suggestions are silent — user must guess which column is the right key | `discover_keys()` output never surfaced in UI |
| P-4 | Composite keys require two separate `key_columns_f1` values but the UI has only one dropdown | UI only supports single-column key |
| P-5 | Files with non-unique / repeated keys (invoices, transactions) cannot be compared meaningfully | Engine assumes unique key; marks as `is_full_count=False` and stops |
| P-6 | No way to compare a group aggregate (e.g. "total Salary per Department") between files | No aggregation compare path |

---

## 3. Compare Mode Taxonomy

Before any implementation, the team needs to agree on a mode vocabulary. Proposed modes:

```
┌─────────────────────────────────────────────────────┐
│  Mode A  MASTER-DATA COMPARE (current / default)    │
│  Assumption: each key value is unique in both files  │
│  Engine: full-outer join on key → row diff           │
│  Output: added / removed / modified / fmt-only rows  │
│  Status: FULLY IMPLEMENTED                           │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Mode B  VALUE-SET / OVERLAP COMPARE                │
│  Assumption: key column may repeat; only unique      │
│  values matter (e.g. "what ProductCodes exist?")     │
│  Engine: distinct() per column → set intersection   │
│  Output: only-in-A / only-in-B / in-both counts     │
│  Status: IMPLEMENTED (Value Set tab, F-4)           │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Mode C  TRANSACTIONAL COMPARE (future)             │
│  Assumption: key repeats; rows are events/lines      │
│  Engine: group-by key → count/sum comparison        │
│  Output: keys with count/sum mismatch               │
│  Status: NOT IMPLEMENTED — architecture needed      │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Mode D  AGGREGATION COMPARE (future, complex)      │
│  Assumption: arbitrary group-by + aggregate measure  │
│  Engine: user-defined group cols + agg cols         │
│  Output: per-group diff on aggregated values        │
│  Status: DEFERRED — high complexity                 │
└─────────────────────────────────────────────────────┘
```

**Phase 2.6 scope:** Modes A and B are done. Phase 2.6 focuses on P-1 through P-4 (improvements to existing modes) and lays the foundation for Mode C.

---

## 4. UX State-Flow Proposal

### 4.1 Proposed Workflow Stages

The current UX has an implicit single stage: load → run. The proposed model makes the stages explicit:

```
Stage 1: LOAD FILES
  ├── File 1 card (Reference)
  └── File 2 card (Candidate)
       ↓ (both files loaded)

Stage 2: CONFIGURE COMPARE
  ├── Mode selector  [Master-Data | Value-Set | Transactional(future)]
  ├── Key column(s)  [auto-suggest panel + manual override]
  ├── Column include/exclude panel  [checkboxes, replaces mapping panel]
  └── Ignore rules  [case / whitespace]
       ↓ (user clicks ▶ Compare)

Stage 3: RESULTS
  ├── Summary metric bar
  ├── Diff table (Tabulator)
  ├── Profile sidebar
  └── Export controls
```

The Compare Configuration card (stage 2) replaces the current flat "inputs + options" layout. It becomes a **vertical accordion or step panel** where each section is collapsible after being configured.

### 4.2 Column Include/Exclude Panel (replaces column mapping panel)

Current column mapping panel groups columns into Matched / Extra / Fuzzy. Proposed replacement adds a fourth dimension: **Include / Exclude from compare**.

```
┌── COLUMN CONFIGURATION ──────────────────────────────┐
│  Matched columns (will be compared)        [✓ all]   │
│  ☑ EmployeeID   ☑ Department   ☑ Salary              │
│  ☑ JoinDate     ☑ Status                             │
│                                                       │
│  Renamed / Mapped                                     │
│  ☑ Emp_ID → EmployeeID          [accepted rename]    │
│                                                       │
│  Only in Reference (excluded by default)             │
│  ☐ LegacyCode   ☐ OldRegion                         │
│                                                       │
│  Only in Candidate (excluded by default)             │
│  ☐ NewField     ☐ AuditTimestamp                     │
│                                                       │
│  Fuzzy suggestions (pending)                         │
│  ? Emp_Name → EmployeeName   [Accept] [Ignore]       │
└──────────────────────────────────────────────────────┘
```

State model change: `S.colMap.included: Set<string>` — columns the user explicitly includes in the compare. Passed to the backend as `compare_columns: list[str]`.

### 4.3 Compare Plan Summary (new)

A small read-only summary that renders between the column config and the Run button. It answers "what will happen when I click Run?":

```
┌── COMPARE PLAN ──────────────────────────────────────┐
│  Mode:     Master-Data                               │
│  Key:      EmployeeID (auto-detected, unique ✓)      │
│  Columns:  7 compared, 2 excluded, 1 renamed         │
│  Ignore:   Case-insensitive                          │
└──────────────────────────────────────────────────────┘
```

This is a pure frontend render — no API call needed. State is fully derivable from `S.f1`, `S.f2`, `S.colMap`, `S.mode`.

### 4.4 Key Column UX Improvements

**Auto-suggest from `discover_keys()`:**

Currently `discover_keys()` runs server-side during compare but its results are never shown to the user. Proposed: add a lightweight `/api/suggest-keys` endpoint that returns the top-3 candidate key columns with their uniqueness scores, called after both files load (debounced, async, non-blocking).

```
Key Column ─────────────────────────────────────────
  Reference key: [EmployeeID ▾]  ← populated from file columns
  Candidate key: [EmployeeID ▾]

  Suggestions (auto-detected):
  ● EmployeeID  — unique in both files  ✓
  ○ Email       — 99.8% unique in Ref, 100% in Cand
  ○ Name+Dept   — composite candidate
```

**Composite key builder:**

Replace single-dropdown key select with an addable list (up to 3 items). Each item is a column dropdown. The final key is the concatenation of selected columns (matching `key_discovery.py`'s existing `max_composite=3`).

```
Reference key columns:  [Department ▾]  [EmployeeID ▾]  [+ Add]
Candidate key columns:  [Department ▾]  [Emp_ID ▾]      [+ Add]
```

---

## 5. Backend / API Implications

### 5.1 New endpoint: `/api/suggest-keys`

```
POST /api/suggest-keys
Request:  { file1, file2, sheet1, sheet2 }
Response: {
  f1: [ { columns: ["EmployeeID"], unique_rate: 1.0, is_unique: true }, ... ],
  f2: [ { columns: ["Emp_ID"], unique_rate: 0.999, is_unique: true }, ... ]
}
```

Implementation: calls `discover_keys()` on a 100k-row sample (already the current behaviour) and returns the candidate list. No new backend logic needed — just expose what's already computed.

### 5.2 Extend `CompareAPIRequest` with `compare_columns`

```python
class CompareAPIRequest(BaseModel):
    # ... existing fields ...
    compare_columns: Optional[list[str]] = None   # subset of matched columns to diff
```

Passed through to `CompareRequest` → `diff_files()`. In `differ.py`, apply filter after column matching but before the join: `shared_cols = [c for c in shared_cols if c in compare_columns]` when `compare_columns` is set.

This is a **5-line change in differ.py** and is fully backward-compatible (None = use all columns, same as today).

### 5.3 Composite key support in API

Already wired for single-column. Extend `key_columns_f1` / `key_columns_f2` to support lists of up to 3 columns:

```python
key_columns_f1: Optional[list[str]] = None   # supports composite e.g. ["Dept", "EmpID"]
key_columns_f2: Optional[list[str]] = None
```

`differ.py` already supports composite keys via `_apply_ignore_rules` concatenation. The only change needed is the UI composite builder and the API passing the list.

### 5.4 Mode C (Transactional) — future backend shape

When Mode C is introduced, it will require a new code path in `differ.py` or a sibling `transact_differ.py`:

```
Input: lf1, lf2, group_key: list[str], count_col: Optional[str], sum_cols: list[str]
Logic: group_by(group_key).agg([pl.count(), pl.sum(col) for col in sum_cols])
Join the two aggregations and diff the aggregate values.
Output: TransactDiffResult with per-key count/sum mismatches.
```

This is architecturally clean and does not touch the existing `differ.py` at all.

---

## 6. Scalability Implications

| Concern | Current | Phase 2.6 impact |
|---------|---------|-----------------|
| `/api/suggest-keys` on 5 GB file | N/A | Uses 100k-row sample (existing behaviour) — fast |
| `compare_columns` filter | All columns joined | Fewer columns in join → less memory, faster join |
| Composite key | 1 column | Up to 3: concatenation is a single Polars `pl.concat_str()` — negligible |
| Value Set on large files | `.collect()` after `unique()` | Unique on a single column of 13M rows fits in RAM (~100 MB for string col) |
| Mode C aggregation | N/A | `group_by + agg` is a Polars strength — 13M rows aggregated by key is cheap |

No scalability regressions from the Phase 2.6 changes. The `compare_columns` filter is actually a net win.

---

## 7. Phased Implementation Plan

### Phase 2.6.1 — Immediate wins (frontend-only, no backend changes)

Estimated effort: 1 session. Risk: low. No backend changes.

| Task | What | Why |
|------|------|-----|
| 2.6.1-A | Add column include/exclude checkboxes to the existing column mapping panel | Fixes P-1. Purely frontend state change. |
| 2.6.1-B | Add "Compare Plan" summary box above Run button | Fixes P-2. Pure frontend render from existing state. |
| 2.6.1-C | Pass `compare_columns` array in the compare API request | Glue layer — requires the 5-line `differ.py` change (see 5.2) |

**Deliverable:** User can uncheck noisy columns before running compare. Plan summary shows what will happen.

---

### Phase 2.6.2 — Key UX improvements (small backend addition)

Estimated effort: 1–2 sessions. Risk: low-medium.

| Task | What | Why |
|------|------|-----|
| 2.6.2-A | Add `/api/suggest-keys` endpoint | Fixes P-3. Exposes existing `discover_keys()` output. |
| 2.6.2-B | UI: show key suggestions in key-column area | Fixes P-3 in UI. |
| 2.6.2-C | Composite key builder UI (up to 3 columns per file) | Fixes P-4. Backend already supports composite keys. |

**Deliverable:** App suggests the right key columns and supports composite keys in the UI.

---

### Phase 2.6.3 — Compare mode selector (medium complexity)

Estimated effort: 2 sessions. Risk: medium.

| Task | What | Why |
|------|------|-----|
| 2.6.3-A | Add mode selector to the Configure stage | UX scaffolding for future modes |
| 2.6.3-B | Mode A (Master-Data) = current behaviour, just labeled | No engine change |
| 2.6.3-C | Mode B (Value-Set) = existing Value Set tab, surfaced as a mode | Unify the two entry points |
| 2.6.3-D | Mode C (Transactional) = count/sum per key group — stub UI + new `transact_differ.py` | Fixes P-5 |

**Deliverable:** User picks a compare mode. Transactional mode gives per-key count mismatch analysis.

---

### Phase 2.6.4 — Deferred / high complexity

Do not start until 2.6.1–2.6.3 are stable and approved.

| Item | Complexity | Notes |
|------|-----------|-------|
| Mode D (Aggregation compare) | High | Requires user-defined group cols + measure cols UI; engine is straightforward once UI is designed |
| 3+ file support | High | Requires rethinking `S.f1/S.f2` state model; session manager; compare graph |
| Saved compare configurations | Medium | YAML/JSON config export/import; load configuration from file |
| Side-by-side old/new diff values (P4-T2) | Medium | Already in roadmap; blocked on full diff export (P3-T5) |

---

## 8. Immediate Low-Risk UX Wins

_Status: **IMPLEMENTED** (Phase 2.6, 2026-05-16)_

### Win-1: "Compare Plan" summary card — DONE

A read-only summary panel rendered inside `#compare-plan` (below the column mapping panel, above the Validation Rules panel). Shows: mode, key columns, columns compared/excluded/ignored/renamed, active ignore rules.

- Pure frontend. No API call.
- Rendered by `renderComparePlan()`, triggered by: `renderColMappingPanel()`, `onKeyChange()`, `switchTab()`, and the ignore-rule checkbox `onchange` handlers.
- Hidden in Validate and Value Set tabs.

### Win-2: Column include/exclude checkboxes — DONE

Each matched column in the F-3 panel now has a checkbox (default: checked = included). Unchecked columns accumulate in `S.colMap.excluded` (Set). `getCompareColumns()` returns the filtered list, or `null` when nothing is excluded (no API change for the common case). The list is sent as `compare_columns` in the compare request.

- Backend: `compare_columns: Optional[list[str]] = None` added to `CompareAPIRequest`, `CompareRequest`, and `diff_files()`. Filters `shared_cols` after `column_map` rename. See D-014.
- 10 new tests in `TestCompareColumns` (test_diff_semantics.py). All pass.

### Win-3: Export button loading state — DONE (KI-019 fixed)

`exportCSV()` rewritten as async. Button shows "⏳ Exporting…" and is disabled during the fetch. Restored after success or error. Filename extracted from `Content-Disposition` header.

---

## 9. What Must NOT Change

These constraints apply to all Phase 2.6 work:

| Constraint | Reason |
|------------|--------|
| INV-1: `pl.scan_csv()` only | All new endpoints must use `_load_lazy_frame` |
| INV-5: differ / validator separation | `compare_columns` filter goes in `differ.py`, not `validator.py` |
| INV-6: full-file counts or `is_full_count=False` | Any new count in Mode C must follow the same rule |
| Benchmark assertions must still pass | No change to existing diff logic — `compare_columns=None` is the default (unchanged behaviour) |
| No breaking API changes | `compare_columns` and `key_columns_f1/f2` must remain optional with backward-compatible defaults |

---

## 10. Open Design Questions (resolve before implementation)

| # | Question | Options |
|---|----------|---------|
| Q-1 | Should unmatched columns (only in one file) be excluded from compare by default, or should the user explicitly exclude them? | **Option A:** Excluded by default (cleaner; avoids "column not found" errors). **Option B:** Warn but include (preserve existing behaviour). Recommendation: Option A. |
| Q-2 | Should the "Compare Plan" panel be a collapsible card or always-visible summary? | Always-visible is lower implementation cost. Collapsible is better UX for power users. |
| Q-3 | Should `/api/suggest-keys` be called automatically after both files load, or require a button click? | Auto-call (debounced, async) gives better UX but adds a small server hit per file-load. Given 13M-row files, the sample is capped at 100k rows — fast enough. Recommendation: auto-call. |
| Q-4 | Transactional mode — what aggregation functions to expose first? | Count-only (simplest) vs count + sum (most useful). Recommendation: count + sum on user-selected numeric columns. |

---

## Appendix — Previous Implementation Spec (F-1 through F-4)

The content below was the prior version of this document. It describes features that are **fully implemented** as of Phase 2.5.

Preserved for reference only. Do not implement from this section again.

```
F-1: Vertical file upload layout
F-2: Key column dropdowns per file
F-3: Column mapping panel (auto-match, Levenshtein fuzzy suggestions, accept/reject)
F-4: Value Set Compare tab (/api/value_set_compare endpoint)
Version badge v1.1.0
```

Full implementation detail is in git history (commit: Phase 2.5 UX audit).
