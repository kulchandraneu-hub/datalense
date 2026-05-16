# DataLens — Next-Generation Workflow Architecture

_Created: 2026-05-16. Status: Approved architecture — pending UI implementation approval._

---

## 1. Multi-File Model: Reference vs Candidate Architecture

DataLens compares data in a **Reference → Candidate** pattern:

```
┌─────────────────────┐
│   REFERENCE FILE    │  The source of truth. Loaded once per session.
│  (the known-good)   │  Never changes during a session.
└──────────┬──────────┘
           │  compared against (pairwise, independently)
   ┌───────┴────────────────────────────────┐
   │          │                │            │
┌──▼──┐  ┌───▼──┐        ┌────▼──┐     [+ Add]
│ C-1 │  │ C-2  │  · · · │ C-N  │
└─────┘  └──────┘        └───────┘
Candidate 1  Candidate 2   Candidate N
```

**Session concept:** One session = one reference file, zero-to-N candidate files. Each candidate is compared independently against the reference. Results are stored per-candidate and browsable via a results switcher.

---

## 2. Why the Pairwise Engine Is Preserved

The diff engine (`differ.py`) is built on a Polars full-outer join of two LazyFrames. This is:

- **Correct:** Every engine invariant (sentinel-based added/removed detection, full-file counts, formatting_only/semantic split) is proven correct and benchmark-verified.
- **Fast:** The full-outer join on a LazyFrame is O(N log N) in Polars — tested at 500k rows (~344s, acceptable for 5 GB production files).
- **Simple:** N-way reconciliation requires a different algorithm (multi-way merge or sequential pairwise aggregation) — significantly higher complexity and no user requirement for it yet.

**Therefore:** The engine stays pairwise. Multi-candidate support is achieved by running N-1 independent pairwise comparisons (Reference vs Candidate 1, Reference vs Candidate 2, …), not by extending the engine to N-way.

This is already how the frontend works today (via `S.runQueue`). The redesign makes this model explicit in the UI rather than hiding it behind "File 1 / File 2 / File 3" labels.

---

## 3. UI Session Concept

### Session state model

```
Session {
  reference:  FileInfo | null          // the one reference file
  candidates: FileInfo[]               // zero to N candidate files
  colMap:     ColumnMapping            // built from reference × each candidate
  config:     CompareConfig            // key columns, compare_columns, ignore rules
  results:    Map<candidateIndex, CompareResult>
  activeResult: candidateIndex | null
}
```

### How a session flows

1. **Load Reference** — user browses or pastes a path. File info loaded via `/api/file-info`. Columns shown as key-selection chips.
2. **Load Candidates** — each candidate is a separate card. Each card loads its own file info independently.
3. **Configure** — key columns (per file), column include/exclude, ignore rules. A live Compare Plan summarises what will happen.
4. **Run** — clicking Run queues N-1 pairwise compares and runs them sequentially. Each compare is an independent `/api/compare` call with `file1=reference`, `file2=candidateN`.
5. **Results** — each result card is available independently. A results-switcher bar appears when N > 1.

### Column mapping in multi-candidate sessions

The current column mapping panel is built from `S.f1 × S.f2` only. In the redesigned session:
- Column mapping is built from `Reference × each Candidate` individually.
- When the user navigates between result pair cards, the column mapping shown corresponds to that pair.
- For the common case (all candidates have the same schema), the mapping is identical across pairs.

---

## 4. Terminology Migration

### Internal code: f1/f2 naming stays

No renaming of internal variables, dataclasses, API fields, or export column headers. The following stays unchanged:

| Internal | Meaning |
|----------|---------|
| `f1`, `lf1`, `m1` | The reference file (formerly "File 1") |
| `f2`, `lf2`, `m2` | The candidate file (formerly "File 2") |
| `validation_f1`, `validation_f2` | Validation reports per file |
| `total_rows_f1`, `total_rows_f2` | Row counts |
| `_f1`, `_f2`, `_raw1`, `_raw2` | Column suffixes in join result |
| `key_columns_f1`, `key_columns_f2` | Per-file key column lists in API |
| `{col}_before`, `{col}_after` | Excel export column headers |
| `{col}_f1`, `{col}_f2` | CSV/JSON export column headers |

### UI labels: Reference / Candidate

| Old UI label | New UI label |
|-------------|-------------|
| "File 1" | "Reference" |
| "File 2" | "Candidate" |
| "File 3", "File 4", … | "Candidate 2", "Candidate 3", … |
| "File 1 vs File 2" (results bar) | "vs Candidate" / "vs Candidate 2" |
| "File 1" (validation section) | "Reference File" |
| "File 2" (validation section) | "Candidate File" |
| "Template" (validate tab) | "Reference" |
| "File to Validate" (validate tab) | "Candidate" |

This is a **frontend-only change**. No backend response fields are renamed. The serializer returns `validation_f1` / `validation_f2` and the frontend maps them to "Reference File" / "Candidate File" at render time.

---

## 5. Premium UI Direction Summary

Full spec is in `docs/NEXT_GEN_UI_SPEC.md`. Key principles:

### Layout principle
Explicit 3-stage workflow replaces the current flat inputs-card pattern:
```
Stage 1: LOAD FILES     → Reference card + Candidate card(s)
Stage 2: CONFIGURE      → Key, columns, ignore rules, Compare Plan
Stage 3: RESULTS        → Metric cards + Diff / Validation / Profile tabs
```
Stages are visually separated by stage-number headers. Stage 2 and 3 only appear after Stage 1 is satisfied.

### Card hierarchy
- **Reference card:** Full-width, left border in accent blue, larger label, displays file metadata prominently.
- **Candidate cards:** Thinner (less padding), left border in muted/green, "CANDIDATE" label in muted color. Each has a remove button.
- **Compare Plan card:** Always visible between configuration and Run button. Shows the literal plan: mode, key, N compared / M excluded columns, active ignore rules.

### Visual style
- Dark theme preserved (same CSS variable tokens).
- More breathing room: card padding increased, fewer adjacent equal-weight UI elements.
- Accent color (`--blu`, `--grn`, `--red`) used only on: CTA buttons, status badges, active state indicators. No accent on neutral informational UI.
- Softer card borders: reduce border opacity for non-critical cards.
- Metric area: large stat cards (not the current mini metric chips) for the primary numbers (added / removed / modified).

### What stays the same
- All INV-1 through INV-7 invariants.
- Benchmark assertions (unchanged by any UI change).
- API contract: `/api/compare`, `/api/validate`, `/api/value_set_compare`, `/api/browse`, `/api/history` — no breaking changes.
- `web/` folder structure: `web/__init__.py`, `web/api.py`, `web/history.py`, `web/static/index.html`.
- Export formats and filenames.

---

## 6. What Is Explicitly Deferred

The following are **not** in scope for the next-generation UI redesign:

| Feature | Reason deferred |
|---------|----------------|
| True N-way reconciliation | Requires multi-way merge engine; no user requirement yet |
| Transactional compare (Mode C) | Requires `transact_differ.py`; architecture defined in WORKFLOW_UX_ARCHITECTURE.md §5.4; deferred to Phase 2.6.3 |
| Aggregation compare (Mode D) | High complexity; architecture deferred to Phase 2.6.4 |
| Saved compare configurations | Nice-to-have; no current requirement |
| Side-by-side old/new diff values | Already in roadmap as P4-T2; blocked on full diff export (P3-T5) |
| `/api/suggest-keys` endpoint | Architecture defined (WORKFLOW_UX_ARCHITECTURE.md §5.1); deferred to Phase 2.6.2 |
| Composite key builder UI | Architecture defined (WORKFLOW_UX_ARCHITECTURE.md §4.4); deferred to Phase 2.6.2 |
| Per-phase SSE progress percentage | Deferred; KI-020 |
| is_full_count drill-down detail | Deferred; KI-021 |
