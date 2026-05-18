# Implementation Roadmap — DataLens

_Created: 2026-05-14. Last updated: 2026-05-18 (Phase 3 — performance architecture complete; 344s → 2.4s on 500k rows)._

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
- **Benchmark result:** `added_rows=5000 OK`, `removed_rows=5000 OK`, `modified_rows=45563 OK` (KI-016 resolved: 4,437 rows correctly in formatting_only_rows; see D-012), `is_full_count=True OK`.

### P1-T2: Sentinel-column added/removed detection `[x]`
- **File:** `differ.py`
- **Completed:** 2026-05-14
- **Fix applied:** `pl.lit(1).alias("_in_f1")` added to `lf1_sem` and `lf1_raw`; `pl.lit(1).alias("_in_f2")` added to `lf2_sem` and `lf2_raw` — before join. After join: `_in_f1 IS NULL` → added; `_in_f2 IS NULL` → removed. Sentinel-based check applied in both the Polars expression plan and the Python sample loop. Value heuristic eliminated.
- **Benchmark result:** `added_rows=5000 ✓`, `removed_rows=5000 ✓` (both were 0 before).

### P1-T3: Fix `_infer_type()` type distribution `[x]`
- **Files:** `profiler.py`, `validator.py`
- **Completed:** 2026-05-14
- **Fix applied:**
  - `_infer_type()` rewritten with priority-based logic (Int64 > Float64 > Boolean > Date > Datetime > String). Dominant = first type where ≥ 95% of non-null rows parse. If none qualify, best specific type used so `invalid_parse_count` is non-zero for mixed-format columns.
  - Temporal dtype guard: columns already schema-typed as `pl.Date`/`pl.Datetime` skip Int64/Float64/Boolean casts (days-since-epoch conversion would falsely dominate as "integer").
  - `type_distribution` is now exclusive 2-key dict `{dominant: fraction, "string": 1−fraction}` over non-null rows.
  - `non_null_count` is passed from `profile_column` (no extra `.collect()`).
  - `_check_type_consistency` in `validator.py`: removed `max_pct < 0.95` guard. Now fires for any `inferred_type != "string"` column with `invalid_parse_count > 0`. The old guard blocked warnings for 1–5% mixed content (benchmark has 4% mixed LastPurchaseDate in 100k file, 0.9% JoinDate in 500k).
- **Benchmark (100k):** `LastPurchaseDate` in file B flagged as Mixed Types (96% date, 4004 non-parseable rows). ✓
- **Remaining gap:** `modified_rows` gap (KI-016) unaffected by this task — closes after P1-T8.

### P1-T4: Fix `render_excel_diff()` — include f2 values `[x]`
- **File:** `reporters.py`
- **Completed:** 2026-05-14
- **Fix applied:**
  - Header changed from single `{col}` to interleaved `{col}_before` / `{col}_after` for every data column.
  - Both `f1_values.get(col, "")` and `f2_values.get(col, "")` written per row.
  - Changed cells in `modified` rows receive a bright-amber cell fill (`B45309`) on the `_after` column. Row-level fills preserved for all change types.
  - `render_csv_diff()` and `render_json_diff()` were already correct; no changes needed.
- **Known gap (deferred):** Added/removed rows still have empty before/after cells. `RowDiff.f1_values` and `f2_values` are not populated for added/removed rows in the sample loop. Deferred to Phase 4 alongside P4-T2.

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

### P1-T8: Raise `infer_schema_length` `[x]`
- **Files:** `compare.py:_load_lazy_frame()`, `metadata.py:load_metadata()`
- **Completed:** 2026-05-14
- **Fix applied:** `infer_schema_length` raised from `1000` to `10_000` in both call sites. Both `metadata.py` (schema metadata collection) and `compare.py` (LazyFrame construction for diff) now use the same value, so schema inference is consistent between the two paths.
- **Benchmark impact (100k):** Schema inference for all columns is unchanged — both benchmark files already expose mixed-format content (date formats, Salary float suffix) within the first 1000 rows, so Polars already inferred the correct types. All 4 hard assertions still pass; elapsed time unchanged (17.9s vs 17.7s — within noise).
- **Defensive value:** For real-world files where mixed-format content appears later in the file (e.g., a 5 GB CSV where US-format dates start at row 2000), `infer_schema_length=10_000` prevents Polars from locking in the wrong type and silently coercing or erroring on the tail of the file.
- **KI-016 status (final — 2026-05-14):** RESOLVED. The 4,437 gap is a benchmark expectation artifact. Root cause: Salary is Int64 in A and Float64 in B. Polars comparison `Int64(50000) != Float64(50000.0)` returns False (numeric type promotion). For 4,437 rows with ONLY Salary format change (same numeric value), `any_sem_diff = False` → classified as `formatting_only_rows`. Benchmark expectation corrected: `modified_rows=45_563`, `formatting_only_rows=449_437`. See D-012.

---

## Phase 2 — Benchmark-Driven Regression Coverage

**Goal:** Automated tests that lock in Phase 1 correctness permanently.
**Prerequisite:** Phase 1 complete.

### P2-T1: Create `tests/test_benchmark_500k.py` `[x]`
- **Completed:** 2026-05-15
- Pytest module using 500k benchmark files with all hard assertions from BENCHMARK_TEST_PLAN.md.
- Also created `tests/test_regression_100k.py` (100k quick regression, ~26s) and `tests/test_diff_semantics.py` / `tests/test_validation.py` / `tests/test_exports.py` (quick synthetic tests, ~11s total).
- Markers: `quick`, `regression`, `benchmark` — run subsets via `pytest -m quick`, etc.

### P2-T2: Test formatting-only vs semantic separation `[x]`
- **Completed:** 2026-05-15
- `test_benchmark_500k.py::test_formatting_only_rows_exact`: asserts formatting_only_rows == 449,437.
- `test_benchmark_500k.py::test_formatting_only_includes_salary_format_rows`: asserts >= 4,437.
- `test_benchmark_500k.py::test_modified_plus_formatting_only_equals_matched_rows`: conservation check.
- `test_diff_semantics.py::TestSemanticVsFormattingOnly`: clean synthetic fixture verifies ignore_case reclassifies case-only rows from modified → formatting_only.

### P2-T3: Test JoinDate mixed-type detection `[x]`
- **Completed:** 2026-05-15
- `test_benchmark_500k.py::test_joindate_mixed_types_in_f2`: asserts Mixed Types check fires for JoinDate in file B.
- `test_validation.py::TestMixedTypesDetection`: demos demo_small JoinDate (1/11 non-ISO), verifies check fires AND does not fire on clean file A.
- `test_regression_100k.py::test_lastpurchasedate_mixed_types_in_f2`: regression on 100k LastPurchaseDate.

### P2-T4: Test null Salary detection `[x]`
- **Completed:** 2026-05-15
- `test_benchmark_500k.py::test_salary_null_variants_in_f2_profile`: asserts total_null_variants > 0 and null_variant_rate > 0.
- Note: 9,897 / 500,000 ≈ 2% — below the 50% warn threshold. "High Null Rate" check does NOT fire.
  Corrected the BENCHMARK_TEST_PLAN assertion accordingly (see notes in that file).

### P2-T5: Test sentinel-based added/removed count `[x]`
- **Completed:** 2026-05-15
- `tests/fixtures/null_nonkey_A.csv`: row 3 has null Score (non-key column).
- `tests/fixtures/null_nonkey_B.csv`: row 3 absent.
- `test_diff_semantics.py::TestSentinelNullNonKey`: asserts removed=1, added=0, modified=0, is_full_count=True.

### Phase 2 completion note (2026-05-15)
```
pytest -m quick       → 60 tests, ~11s   (synthetic + demo fixtures)
pytest -m regression  → 11 tests, ~26s   (100k benchmark)
pytest -m benchmark   → 17 tests, ~5 min (500k milestone gate)
```
Also fixed KI-014: `compare.py` smoke test now asserts `is_full_count is True`.

---

## Phase 3 — Architecture for Future Speed

**Goal:** Enable 13M-row / 5GB file support without redesigning Phase 1 work.
**Prerequisite:** Phase 1 + Phase 2 complete.
**Benchmark result: 344s → 2.4s on 500k rows (all 4 tasks complete, 2026-05-18).**

### P3-T1: Full-file per-column counts via Polars expression plan `[x]`
- **Completed:** 2026-05-18
- **Fix applied:** Replaced Python `for sem_row in sem_sample:` loop with `sem_agg_exprs` + `raw_agg_exprs` in a single `.select().collect()` over the full joined LazyFrame. One `.collect()` for all per-column counts; one `.head(1000).collect()` for display sample.
- **Benchmark:** 344s → 93.9s (dominant gain from eliminating Python loop over sample).

### P3-T2: Merge semantic + raw joins into one combined join `[x]`
- **Completed:** 2026-05-18
- **Fix applied:** Built one frame per file carrying both semantic ({c}_s1/{c}_s2) and raw ({c}_r1/{c}_r2) columns. A single combined full-outer join replaces the two separate joins. Row classification via single `when/then/otherwise` expression plan. Collects reduced from 6 → 4.
- **Benchmark:** 93.9s → 85.7s.

### P3-T3: Batch column profiling `[x]`
- **Completed:** 2026-05-18
- **Fix applied:** `profile_file()` was doing ~176 `.collect()` calls (8 type checks × 22 columns). Replaced with a single `.select([all_exprs]).collect(engine="streaming")` pass. Added Boolean cast guard for Utf8/String columns (Polars 1.x limitation).
- **Benchmark:** 85.7s → 2.1s (dominant gain — 176 `.collect()` calls reduced to 2).

### P3-T4: Pre-sort keys before join + streaming CSV sink `[x]`
- **Completed:** 2026-05-18
- **Fix applied:** Both LazyFrames sorted by key columns before the join (enables merge-join over hash-join at 5GB scale). Export uses Polars streaming sink rather than loading full diff CSV into RAM.
- **Benchmark:** 2.1s → 2.4s (within noise at 500k; benefit realises at 5GB scale).

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
   Benchmark: added=5000 OK  removed=5000 OK  modified=45563 OK (KI-016 resolved)

2. P1-T6          [x] DONE 2026-05-14 — remove duplicate profiling (4 passes -> 2)
3. P1-T7          [x] DONE 2026-05-14 — full-file key validation before diff; is_full_count=False on duplicate keys
4. P1-T3          [x] DONE 2026-05-14 — priority-based _infer_type; Mixed Types check unblocked
5. P1-T8          [x] DONE 2026-05-14 — raise infer_schema_length to 10,000 (defensive)
6. P1-T4          [x] DONE 2026-05-14 — Excel export: {col}_before/{col}_after headers + cell highlighting
```

All Phase 1 tasks complete. Phase 2 (automated regression tests) is next.

**Note (2026-05-14 — KI-016 RESOLVED):** KI-016 (modified_rows gap of 4,437 in 500k) is a benchmark expectation artifact. Salary is Int64 in File A and Float64 in File B. Polars numeric type promotion makes `Int64(50000) == Float64(50000.0)`, so 4,437 rows where ONLY Salary format changed are classified as `formatting_only` (not `modified`). Engine is correct. Benchmark expected value updated to `modified_rows=45_563`. See D-012.

---

## Decisions Log

See `docs/DECISIONS.md` for all design decisions made during implementation.
