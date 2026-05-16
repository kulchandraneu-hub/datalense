# DataLens — Next-Generation UI Spec

_Created: 2026-05-16. Status: Approved for implementation. Phase 1 of 3: frontend-only._
_Architecture background: `docs/NEXT_GEN_WORKFLOW.md`_

---

## Section 1 — Design Goals

| ID | Goal |
|----|------|
| G-1 | Make the Reference → Candidate pattern explicit in every visible label. Internal code variables (`f1`, `f2`, `lf1`, `m1`, etc.) do NOT change. |
| G-2 | Fix the column mapping panel so it uses the active candidate slot, not a hardcoded `S.f2`. |
| G-3 | Fix the export CSV path so switching between pair results serves the correct file. |
| G-4 | Stage the UI into three visually distinct phases (Load → Configure → Results) with numbered stage headers. Stage 2 and 3 only appear after Stage 1 is satisfied. |
| G-5 | Preserve the dark theme, all INV-1 through INV-7 invariants, and all benchmark assertions. |
| G-6 | No API contract changes. All `/api/*` endpoint signatures, request fields, and response fields stay the same. |

---

## Section 2 — Stage Layout

The current flat card pattern is replaced by an explicit 3-stage workflow:

```
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1 — LOAD FILES                                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  REFERENCE FILE                [full-width, blue border] │   │
│  │  path input  │  browse btn  │  file chip               │   │
│  │  columns (key chips)                                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  CANDIDATE  [thinner card, green border, remove button]  │  │
│  │  path input  │  browse btn  │  file chip                │  │
│  │  columns (key chips)                                     │  │
│  └──────────────────────────────────────────────────────────┘  │
│  [ + Add another candidate ]  (dashed button)                   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  STAGE 2 — CONFIGURE  (hidden until Stage 1 is satisfied)       │
│  Column mapping panel  |  Key select  |  Ignore rules           │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │  COMPARE PLAN  (always visible before Run button)         │ │
│  │  Mode: Full Row Compare  |  Key: EmployeeID               │ │
│  │  Comparing 20 columns  (2 excluded)  |  0 ignore rules    │ │
│  └───────────────────────────────────────────────────────────┘ │
│  [ RUN COMPARE ]                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  STAGE 3 — RESULTS  (hidden until a run completes)              │
│  Results switcher bar (when N > 1 candidate)                    │
│  Metric bar  |  Schema panel                                    │
│  Diff / Validation / Profile tabs                               │
└─────────────────────────────────────────────────────────────────┘
```

Stage progression:
- Stage 2 header + content: visible only after `S.f1` and at least one candidate are loaded.
- Stage 3 header + content: visible only after at least one compare run completes.
- Stage 1 is always visible (loading is always possible).

---

## Section 3 — Reference Card

Visual treatment:
- Full-width (not in a 2-column grid)
- Left border: `3px solid var(--blu)` — accent blue
- Label: **REFERENCE** (uppercase, 10px, blue accent)
- Larger padding than candidate cards (20px vs 14px)
- File metadata chip (rows, columns, encoding) displayed prominently after load
- Key column chips row appears after load

HTML element IDs unchanged: `path-f1`, `chip-f1`, `lbl-f1`, `key-f1`, `sheet-f1`, `sheet-row-f1`, `cols-f1`, `cols-wrap-f1`, `mem-f1`.

---

## Section 4 — Candidate Cards

Visual treatment:
- Slightly less padding than Reference (14px)
- Left border: `3px solid var(--grn)` — muted green
- Label: **CANDIDATE** / **CANDIDATE 2** / **CANDIDATE 3** (uppercase, 10px, muted color)
- Remove button (✕) on each candidate card
- `+ Add another candidate` dashed button below the last candidate card

One candidate card (`f2`) is always present. Extra candidates (`f3`, `f4`, …) are added by clicking the dashed button.

Label mapping:
| Slot | Visible label |
|------|--------------|
| f2   | CANDIDATE    |
| f3   | CANDIDATE 2  |
| f4   | CANDIDATE 3  |
| fN   | CANDIDATE N-1 |

This label is what appears in:
- The file card header (`lbl-f2`, `lbl-f3`, etc.)
- The results switcher bar buttons
- The progress phase text
- The Export CSV button label

---

## Section 5 — Configure Panel

The configure panel (Stage 2) contains, in order:

1. **Column Mapping Panel** (`col-map-panel`) — shows Reference × active-candidate column alignment.
   - Active candidate = the last candidate slot that loaded, or the pair currently selected in results.
   - Each matched column has a checkbox to include/exclude from compare.
   - Fuzzy-match suggestions with Accept / Reject actions.

2. **Key Column Selection** — key chips for Reference and the active candidate.

3. **Ignore Rules** — existing ignore rules form (unchanged).

4. **Compare Plan Card** — always visible above the Run button. Shows:
   - Mode (Full Row Compare / Formatting Only / Validate Only)
   - Key columns (auto / manual)
   - N columns compared, M excluded
   - N ignore rules active

5. **Run Compare button** (`run-btn`) — disabled until Stage 1 is satisfied.

---

## Section 6 — Results

### Pair switcher bar
Appears when N > 1 candidate. Buttons labeled:
- First candidate: `vs Candidate`
- Second candidate: `vs Candidate 2`
- Third candidate: `vs Candidate 3`

(Derived from the `lbl-${slot}` text of each candidate card.)

### Export CSV button
- Label: `⬇ Export CSV — {CandidateLabel} (sample, ≤100 rows)`
- Where `{CandidateLabel}` is the label of the active candidate result being displayed.
- The export path served is the `csv_export` field from the active pair's result object (not the global `_last_csv_path`).

### Metric cards (large stats)
Primary numbers (added / removed / modified) shown as large stat cards:

```
┌───────────┐  ┌───────────┐  ┌──────────────┐
│  ADDED    │  │  REMOVED  │  │   MODIFIED   │
│  5,000    │  │  5,000    │  │   50,000     │
└───────────┘  └───────────┘  └──────────────┘
```

(Currently these are compact metric chips — the redesign makes them larger.)

### Validation section headers
- `S.validation_f1` → rendered as **Reference File**
- `S.validation_f2` → rendered as **Candidate File**

(Already implemented in the current code — preserve this.)

---

## Section 7 — Visual Style Rules

| Rule | Detail |
|------|--------|
| Dark theme preserved | Same CSS variable tokens (`--bg`, `--sur`, `--bdr`, `--txt`, `--mut`, `--blu`, `--grn`, `--red`, `--yel`, `--pur`) |
| More breathing room | Reference card padding: 20px. Candidate card padding: 14px (current). Configure/Results card padding: 16px. |
| Accent color discipline | Accent color only on: CTA buttons, status badges, active state indicators. Not on neutral informational elements. |
| Softer borders on non-critical cards | Non-critical card borders at 60% opacity: `rgba(var(--bdr-rgb), 0.6)`. |
| Stage number headers | `STAGE 1 — LOAD FILES`, `STAGE 2 — CONFIGURE`, `STAGE 3 — RESULTS` in 9px uppercase muted text. |
| Reference card left accent | `border-left: 3px solid var(--blu)` |
| Candidate card left accent | `border-left: 3px solid var(--grn)` |
| Compare Plan card | Distinct background: `var(--bg)` interior, `1px solid var(--bdr)` border, no left accent. |

---

## Section 8 — What Must NOT Change

These are non-negotiable invariants from CLAUDE.md and NEXT_GEN_WORKFLOW.md:

| Item | Constraint |
|------|-----------|
| JS variable names | `S.f1`, `S.f2`, `S.f3`, `S.extraSlots`, `S.allResults`, `S.runQueue`, `S.colMap` — unchanged |
| API request fields | `file1`, `file2`, `key_columns_f1`, `key_columns_f2`, `compare_columns` — unchanged |
| API response fields | `validation_f1`, `validation_f2`, `total_rows_f1`, `total_rows_f2`, `csv_export`, `html_report`, `json_export` — unchanged |
| Python modules | `differ.py`, `compare.py`, `validator.py`, `profiler.py`, `key_discovery.py` — no changes in Phase 1 |
| Export column headers | `{col}_before`, `{col}_after`, `{col}_f1`, `{col}_f2` — unchanged in export files |
| Benchmark assertions | `added_rows == 5000`, `removed_rows == 5000`, `modified_rows == 50000` — must still pass |
| INV-1 through INV-7 | All architectural invariants from CLAUDE.md — unchanged |
| `web/` folder structure | `web/__init__.py`, `web/api.py`, `web/history.py`, `web/static/index.html` — preserved |
| Tabulator usage | Diff table uses Tabulator.js — unchanged |
| History sidebar | SQLite history via `web/history.py` — unchanged |

---

## Section 9 — Out of Scope (This Phase)

The following are explicitly deferred. Do not implement them in Phase 1.

| Feature | Deferred to |
|---------|------------|
| Full 3-stage visual layout (stage headers, card hierarchy) | Phase 2 of this UI work |
| Large stat cards for primary metrics | Phase 2 |
| Compare Plan card in configure panel | Phase 2 |
| Stage gating (hide Stage 2 until Stage 1 done) | Phase 2 |
| Reference card visual redesign (blue border, larger padding) | Phase 2 |
| Candidate card visual redesign (green border) | Phase 2 |
| True N-way reconciliation engine | Deferred indefinitely (see NEXT_GEN_WORKFLOW.md §6) |
| Transactional compare (Mode C) | Phase 2.6.3 |
| Aggregation compare (Mode D) | Phase 2.6.4 |
| `/api/suggest-keys` endpoint | Phase 2.6.2 |
| Composite key builder UI | Phase 2.6.2 |
| Per-phase SSE progress percentage | KI-020 |
| Saved compare configurations | No current requirement |

### What IS in scope for Phase 1

| Task | Status |
|------|--------|
| Create this spec file | ✅ Done |
| Fix Bug 1: column mapping uses hardcoded S.f2 | ✅ Done |
| Fix Bug 2: export CSV path overwritten across runs | ✅ Done |
| Label-only rename: File 1 → Reference, File 2 → Candidate | ✅ Done |
| Label-only rename: extra slots → Candidate 2, Candidate 3, … | ✅ Done |
| Label-only rename: pair switcher → vs Candidate, vs Candidate 2 | ✅ Done |
| Label-only rename: validate tab Template/File to Validate → Reference/Candidate | ✅ Done |
