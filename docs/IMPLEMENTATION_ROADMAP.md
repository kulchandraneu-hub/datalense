# Implementation Roadmap — DataLens

_Created: 2026-05-14. Last updated: 2026-05-14 (Phase 1 Step 3 — P1-T7 complete)._

---

## Status Legend

- `[ ]` Pending
- `[~]` In progress
- `[x]` Complete
- `[!]` Blocked

---

## Phase 1 — Correctness and Trustworthiness

**Goal:** Make all diff counts accurate for files of any size.
**Prerequisite for:** All other phases. No speed work until Phase 1 is complete.

### P1-T1: Full-file diff counts `[x]`
- **File:** `differ.py`
- **Completed:** 2026-05-14
- **Fix applied:** Polars `when/then/otherwise` on full `sem_joined` LazyFrame; `group_by("_change_type").agg(pl.len()).collect()` for counts. `head(1000)` retained only for display sample. Second pass on `raw_joined` computes `formatting_only_rows` as `raw_both_present_diff - modified`.
- **Benchmark result:** `added_rows=5000 ✓`, `removed_rows=5000 ✓`, `modified_rows=45563` (expected 50000; gap from KI-016 — cross-type column comparison), `is_full_count=True ✓`.
- **Remaining gap:** `modified_rows` off by 4,437 due to KI-016 (JoinDate type mismatch). Will close after P1-T8.

### P1-T2: Sentinel-column added/removed detection `[x]`
- **File:** `differ.py`
- **Completed:** 2026-05-14
- **Fix applied:** `pl.lit(1).alias("_in_f1")` added to `lf1_sem` and `lf1_raw`; `pl.lit(1).alias("_in_f2")` added to `lf2_sem` and `lf2_raw` — before join. After join: `_in_f1 IS NULL` → added; `_in_f2 IS NULL` → removed. Sentinel-based check applied in both the Polars expression plan and the Python sample loop. Value heuristic eliminated.
- **Benchmark result:** `added_rows=5000 ✓`, `removed_rows=5000 ✓` (both were 0 before).

### P1-T3: Fix `_infer_type()` type distribution `[ ]`
- **File:** `profiler.py`
- **Problem:** `counts["string"] = row_count` always. Normalization by sum of overlapping counts makes string always dominant. `invalid_parse_count` always 0. "Mixed Types" validation check permanently inactive.
- **Fix:** Use mutually-exclusive counting. Priority: Int64 → Float64 → Boolean → Date → Datetime → String (fallback). Invalid count = rows that don't parse as dominant type and are not null.
- **Acceptance:** `JoinDate` column in benchmark file B must be flagged as "Mixed Types" (some rows YYYY-MM-DD, others MM/DD/YYYY).

### P1-T4: Fix `render_excel_diff()` — include f2 values `[ ]`
- **File:** `reporters.py`
- **Problem:** Modified rows in Excel export show only old (`f1`) values. New (`f2`) values are silently dropped.
- **Fix:** For each data column, write both `{col}_before` and `{col}_after` columns. Update header row. Color-code changed cells in addition to changed rows.

### P1-T5: Add `is_full_count` and `rows_scanned` to `DiffResult` `[x]`
- **Files:** `differ.py`, `web/api.py`
- **Completed:** 2026-05-14 (implemented alongside P1-T1/T2 as planned)
- **Fix applied:** `is_full_count: bool = False` and `rows_scanned: int = 0` added to `DiffResult`. Set in `diff_files()`. `_serialize_diff()` in `web/api.py` now exposes both fields. Graceful-degradation path sets `is_full_count=False`.
- **Remaining:** UI display (`index.html`) deferred to P4-T1.

### P1-T6: Remove duplicate profiling in compare flow `[x]`
- **Files:** `compare.py`, `validator.py`
- **Completed:** 2026-05-14
- **Fix applied:** Added `profile: Optional[FileProfile] = None` to `validate_file()` — if provided, `profile_file()` is skipped. Added `profile1: Optional[FileProfile] = None, profile2: Optional[FileProfile] = None` to `validate_two_files()` and forwarded to each `validate_file()` call. `compare.py` step 8 now passes `profile1, profile2` computed in steps 4–5. Standalone validate flow (`/api/validate`) unaffected — it calls `validate_two_files()` without profiles so the existing internal profiling path is preserved.
- **Effect:** Profiling passes reduced from 4 to 2 per compare run. ~168 `.collect()` calls for profiling eliminated.

### P1-T7: Validate key on full file before diff `[x]`
- **Files:** `compare.py`, `key_discovery.py`, `validator.py`
- **Completed:** 2026-05-14
- **Fix applied:**
  - `key_discovery.py`: added `check_key_nulls(lf, key_columns) -> int` — counts rows with any null key column via full LazyFrame scan.
  - `compare.py` step 6.5: after key discovery, calls `validate_key(lf1, key_columns)` and `validate_key(lf2, key_columns)` on the full LazyFrames (not the 100k sample). If either file has duplicate keys, sets `key_degraded=True`. After diff, if `key_degraded`, sets `diff.is_full_count = False` (Cartesian product makes counts unreliable).
  - `validator.py`: added `key_columns` parameter to `validate_two_files()`, forwarded to both `validate_file()` calls. `validate_file()` now runs both uniqueness check (existing) and null-in-key check (new "Key Column Nulls" ValidationCheck) when `key_columns` are provided.
  - Step 8 (`validate_two_files`) now receives `key_columns` so "Duplicate Keys" and "Key Column Nulls" warnings appear in the ValidationReport.
- **Benchmark result (100k):** `added=1000 ✓`, `removed=2000 ✓`, `is_full_count=False ✓` (correct — File B has 100 duplicate CustomerID rows), `modified=19580` (expected FAIL — same gap as before, from whitespace/case/date changes without ignore rules + 100 Cartesian duplicate rows).

### P1-T8: Raise `infer_schema_length` `[ ]`
- **Files:** `compare.py:207–214`, `metadata.py:54–58`
- **Problem:** Type inference from 1000 rows mis-identifies mixed-format columns (Salary int vs float, JoinDate ISO vs US).
- **Fix:** Raise to `infer_schema_length=10_000` minimum. Consider `schema_overrides` support via API for user-supplied schemas.

---

## Phase 2 — Benchmark-Driven Regression Coverage

**Goal:** Automated tests that lock in Phase 1 correctness permanently.
**Prerequisite:** Phase 1 complete.

### P2-T1: Create `tests/test_benchmark.py` `[ ]`
- Pytest test module using both 500k benchmark files.
- Hard assertions against known expected counts (see BENCHMARK_TEST_PLAN.md).
- Marks each test with a phase tag so failures are attributable.

### P2-T2: Test formatting-only vs semantic separation `[ ]`
- Assert that rows where Salary changed from integer to float format with same value (`50000` → `50000.0`) are classified as `formatting_only` when numeric normalization is active.
- Assert that rows where Salary value genuinely changed appear as `modified`.

### P2-T3: Test JoinDate mixed-type detection `[ ]`
- Assert that `JoinDate` in benchmark file B is flagged as "Mixed Types" after P1-T3 fix.
- Assert that the mixed-type check fires at the validation level (not just profiler level).

### P2-T4: Test null Salary detection `[ ]`
- Assert that benchmark file B Salary column has `null_variant_rate > 0`.
- Assert that the "High Null Rate" check fires at appropriate threshold.

### P2-T5: Test sentinel-based added/removed count `[ ]`
- Create a small synthetic CSV where one file has null in a non-key column, other file doesn't have the row.
- Assert correct classification (not confused with null-value modified row).

---

## Phase 3 — Architecture for Future Speed

**Goal:** Enable 13M-row / 5GB file support without redesigning Phase 1 work.
**Prerequisite:** Phase 1 + Phase 2 complete.

### P3-T1: Single-pass full-file diff using Polars expressions `[ ]`
- Replace Python `for sem_row in sem_sample:` loop with a Polars expression plan.
- One `.collect()` for counts, one `.head(1000).collect()` for display sample.

### P3-T2: Reduce profiler to 1 `.collect()` per column `[ ]`
- Merge null stats + min/max into a single `lf.select([all_exprs]).collect()`.
- Restructure `_infer_type()` to return expressions rather than calling `.collect()` internally.

### P3-T3: Merge semantic + raw joins into one `[ ]`
- Currently two full-outer joins are executed. Merge into one join with both `_f1/_f2` and `_raw1/_raw2` columns.

### P3-T4: Key discovery — validate uniqueness lazily `[ ]`
- `validate_key()` currently calls `lf.select(key_columns).collect()` (materialises key columns fully).
- Replace with `lf.select(key_columns).unique().select(pl.len()).collect()` minus total — avoids holding full key column in memory.

### P3-T5: Full diff export (not just sample) `[ ]`
- Implement streaming export of full diff result to CSV/JSON using Polars sink or batched write.
- Populate `DiffResult.export_path`.

---

## Phase 4 — UI and Report Clarity

**Goal:** Make the UI trustworthy and the reports actionable.
**Prerequisite:** Phase 1 complete (especially P1-T5 for scope indicator).

### P4-T1: Show compare scope in UI summary card `[ ]`
- "Full-file counts" vs "Estimate — N rows scanned of M total".
- Linked to `is_full_count` / `rows_scanned` from DiffResult.

### P4-T2: Side-by-side old/new values in diff table `[ ]`
- For each changed column in a modified row, show `old_value → new_value`.

### P4-T3: Show which key columns were used and how selected `[ ]`
- "Key: EmployeeID (auto-detected)" vs "Key: EmployeeID (user-specified)".

### P4-T4: Improve Excel report layout `[ ]`
- Depends on P1-T4 (both old and new values present).
- Freeze first row, auto-width columns, colour changed cells (not just rows).

---

## Implementation Order for Phase 1

Execute in this exact sequence to avoid rework:

```
1. P1-T2 + P1-T1  [x] DONE 2026-05-14 — sentinel + full-file counts
   P1-T5          [x] DONE 2026-05-14 — is_full_count/rows_scanned (safe to do alongside)
   Benchmark: added=5000 ✓  removed=5000 ✓  modified=45563 (gap: KI-016)

2. P1-T6          [x] DONE 2026-05-14 — remove duplicate profiling (4 passes → 2)
3. P1-T7          [x] DONE 2026-05-14 — full-file key validation before diff; is_full_count=False on duplicate keys
4. P1-T3          [ ] fix type inference — unblocks Mixed Types check
5. P1-T8          [ ] raise infer_schema_length — closes KI-016 (modified count gap)
6. P1-T4          [ ] fix Excel export — isolated, no dependencies
```

Run benchmark assertions after step 5 (P1-T8) and again after step 6 (P1-T4).

---

## Decisions Log

See `docs/DECISIONS.md` for all design decisions made during implementation.
