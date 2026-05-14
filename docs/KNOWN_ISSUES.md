# Known Issues — DataLens

_Last updated: 2026-05-14 (P1-T1 + P1-T2 + P1-T6 + P1-T7 + P1-T3 + P1-T8 applied)_
_Severity: CRITICAL > HIGH > MEDIUM > LOW_

---

## CRITICAL

### KI-001 — Diff counts are sample-based (max 1,000 rows), presented as absolute truth
- **Status:** FIXED (P1-T1, 2026-05-14)
- **File:** `differ.py` (was `:121–123`)
- **Fix applied:** Full-file counts via Polars `when/then/otherwise` + `group_by` on complete joined LazyFrame. `head(1000)` retained only for display sample.
- **Benchmark:** modified_rows went from 1,000 (capped) to 45,563 (full-file); added/removed each went from 0 to correct 5,000.

### KI-002 — No full-file diff count path exists at all
- **Status:** PARTIALLY FIXED (P1-T1, 2026-05-14) — counts fixed; full export still sample-only
- **File:** `differ.py` (counts fixed), `reporters.py` (export still sample — P3-T5)
- **Fix applied:** Full-file counts now computed via Polars aggregation. Export files still contain at most 200 sample rows until P3-T5 is implemented.

---

## HIGH

### KI-003 — Added/removed detection misclassifies rows with legitimate null values
- **Status:** FIXED (P1-T2, 2026-05-14)
- **File:** `differ.py` (was `:160–161`)
- **Fix applied:** Sentinel columns `_in_f1` / `_in_f2` (value `pl.lit(1)`) added to both semantic and raw frames before the full-outer join. After join: `_in_f1 IS NULL` → added; `_in_f2 IS NULL` → removed. The value heuristic is eliminated. Both the Polars expression classification and the Python sample loop now use sentinels.
- **Benchmark:** added_rows went from 0 to 5,000 ✓; removed_rows went from 0 to 5,000 ✓.

### KI-004 — `_infer_type()` always returns "string" as dominant type
- **Status:** FIXED (P1-T3, 2026-05-14)
- **File:** `profiler.py` (was `:159–209`)
- **Fix applied:**
  - `_infer_type()` now uses priority-based logic: Int64 > Float64 > Boolean > Date > Datetime > String. Dominant = first type in priority order where ≥ 95% of non-null rows parse.
  - If no type meets the threshold, the highest-coverage specific type is used as the display-dominant so `invalid_parse_count` is non-zero for mixed-format columns.
  - Temporal columns (already typed as `pl.Date`/`pl.Datetime` in the LazyFrame schema) skip Int64/Float64/Boolean casts to prevent false "integer" dominant from days-since-epoch conversion.
  - `type_distribution` is now a 2-key exclusive dict: `{dominant: fraction, "string": 1-fraction}` over non-null rows. Previously it was 6 overlapping counts normalized by their sum.
  - `invalid_parse_count` is now `non_null_count - dominant_count` (non-null rows that fail to parse as dominant). Previously always 0.
  - `_check_type_consistency` in `validator.py` updated: removed `max_pct < 0.95` guard (too coarse for 1–5% mixed content). Now fires when `inferred_type != "string" and invalid_parse_count > 0`.
  - `non_null_count` is passed from `profile_column` (already computed from `polars_null`) to avoid an extra `.collect()` call in `_infer_type`.
- **Benchmark (100k):** `LastPurchaseDate` in file B correctly flagged as Mixed Types (96% date, 4004 rows do not parse as ISO date). ✓

### KI-005 — Double profiling in compare flow (4 passes instead of 2)
- **Status:** FIXED (P1-T6, 2026-05-14)
- **Files:** `compare.py`, `validator.py`
- **Fix applied:** `validate_file()` now accepts `profile: Optional[FileProfile] = None`; if provided, `profile_file()` is skipped. `validate_two_files()` accepts `profile1` / `profile2` and forwards to `validate_file()`. `compare.py:run_compare()` passes `profile1, profile2` from steps 4–5 to step 8 validation call. Standalone `/api/validate` flow unaffected (calls without profiles, falls back to internal profiling).
- **Effect:** ~168 redundant `.collect()` calls eliminated per compare run on a 14-column file.

### KI-006 — Key discovery uses 100k-row sample; no full-file validation before diff  
- **Status:** FIXED (P1-T7, 2026-05-14)
- **Files:** `compare.py`, `key_discovery.py`, `validator.py`
- **Fix applied:**
  - `compare.py` step 6.5: after key discovery, calls `validate_key()` on both full LazyFrames before `diff_files()`.
  - If either file has non-unique keys: `key_degraded=True`; after diff returns, `diff.is_full_count = False` is set.
  - `key_discovery.py`: added `check_key_nulls()` for null-in-key detection (full scan).
  - `validator.py`: added null-in-key "Key Column Nulls" ValidationCheck; added `key_columns` param to `validate_two_files()` so both duplicate-key and null-key warnings appear in the ValidationReport.
- **Benchmark (100k):** File B has 100 duplicate CustomerID rows → `is_full_count=False` correctly set. `added_rows=1000 ✓`, `removed_rows=2000 ✓`.

---

## MEDIUM

### KI-007 — `change_rate` denominator is sample size, not total rows
- **Status:** Unfixed
- **File:** `differ.py:235`
- **Impact:** `change_rate = (modified + fmt_only) / max(len(sem_sample), 1)`. `len(sem_sample) ≤ 1000`. If all 50k modified rows appear after row 1000 in the join output, change_rate = 0% for every column.
- **Fix:** After P1-T1, denominator must be `total_rows_f1` (or whichever is larger).
- **Fix target:** Phase 1, alongside P1-T1.

### KI-008 — `infer_schema_length=1000` causes type mis-inference for mixed-format columns
- **Status:** FIXED (P1-T8, 2026-05-14)
- **Files:** `compare.py:_load_lazy_frame()`, `metadata.py:load_metadata()`
- **Fix applied:** `infer_schema_length` raised from `1000` to `10_000` in both call sites. Schema inference now reads 10× more rows before locking in column types, reducing the chance of inferring the wrong type for a column whose mixed-format values appear after the first 1000 rows.
- **Benchmark (100k):** Schema inference unchanged for benchmark files (mixed content already appears within first 1000 rows). No regression on any passing assertion. Elapsed time unchanged (~18s).
- **Defensive value:** For real-world files where format variation appears late (e.g., row 2000+ of a 5 GB CSV), this prevents silent type lock-in and subsequent coercion or error.

### KI-009 — Excel diff export shows only f1 (old) values for modified rows
- **Status:** Unfixed
- **File:** `reporters.py:137`
- **Impact:** Users cannot see what the new value is for any modified row when reviewing the Excel export. The export is effectively useless for change review.
- **Root cause:** `vals.append(row.f1_values.get(col, ""))` — f2 line was never written.
- **Fix:** P1-T4 — Write both `{col}_before` and `{col}_after` columns.
- **Fix target:** Phase 1, step 7 (last, isolated).

### KI-010 — No `is_full_count` flag in DiffResult or API response
- **Status:** FIXED (P1-T5, 2026-05-14, implemented alongside P1-T1/T2)
- **Files:** `differ.py`, `web/api.py`
- **Fix applied:** `is_full_count: bool = False` and `rows_scanned: int = 0` added to `DiffResult`. `_serialize_diff()` in `web/api.py` now includes both fields. Both are set in `diff_files()` — `is_full_count=True` on successful full-file pass; `is_full_count=False` on graceful degradation.
- **UI:** Not yet surfaced in `index.html` — deferred to P4-T1.

### KI-011 — `_apply_ignore_rules` casts all columns to Utf8 when any rule is active
- **Status:** Unfixed
- **File:** `differ.py:254–266`
- **Impact:** Numeric and date columns cast to string lose type information. Comparisons between `50000` and `50000.0` remain string-level after casting, defeating numeric ignore rules.
- **Fix:** Only cast columns that are `Utf8` type, or add a numeric normalization step separate from string normalization.
- **Fix target:** Phase 3 (no current ignore-rules UI in the frontend anyway).

---

## LOW

### KI-012 — `LogCapture` class is dead code
- **Status:** Unfixed
- **File:** `utils.py:74–91`
- **Fix:** Remove class. No callers exist.
- **Fix target:** Phase 3 cleanup.

### KI-013 — `DiffResult.export_path` field is declared but never set
- **Status:** Unfixed
- **File:** `differ.py:51`
- **Fix:** Remove field until full export (P3-T5) is implemented.
- **Fix target:** Phase 3.

### KI-014 — `compare.py` smoke test does not assert `is_full_count`
- **Status:** Unfixed — partially misleading
- **File:** `compare.py:266–267`
- **Impact:** The 3-row smoke test still passes trivially. Now that `is_full_count` exists, the test should assert it to prove the full-file path exercised correctly at small scale.
- **Fix:** Add `assert result.diff.is_full_count == True` to the compare.py smoke test. Do in Phase 2 alongside test_benchmark.py.

### KI-015 — Excel row source file not labeled in diff output
- **Status:** Unfixed
- **File:** `reporters.py`
- **Impact:** The Excel diff sheet does not label which file each column value comes from. After P1-T4 adds both old and new values, the column headers must clearly indicate `{col}_file1` vs `{col}_file2`.

---

### KI-016 — `modified_rows = 45,563` vs benchmark expectation `50,000` — RESOLVED (benchmark artifact)
- **Status:** CLOSED — root cause identified 2026-05-14 (debug_ki016 investigation). No engine bug. Benchmark expectation corrected.
- **File:** `differ.py` (`any_sem_diff` expression), `benchmark_p1.py` (corrected expected value)
- **Severity:** LOW (was MEDIUM — original concern about correctness; now confirmed not an engine defect)
- **Root cause (confirmed):**
  - File A Salary is inferred as `Int64`; File B Salary is inferred as `Float64` (benchmark B has `.0` float format).
  - Polars comparison `Int64(50000) != Float64(50000.0)` returns **False** (numeric type promotion). This is correct Polars behaviour.
  - For 4,437 rows where **only** the Salary type/format changed (same numeric value, no other column changed), `any_sem_diff = False` → engine classifies them as `same_or_fmt` in the semantic join.
  - In the raw join, `"50000" != "50000.0"` is True → those 4,437 rows ARE counted in `formatting_only_rows`.
  - **The 4,437 rows are not lost**: `modified_rows (45,563) + formatting_only_rows (449,437) = 495,000` = all matched rows. Nothing is unaccounted.
- **Engine behaviour is correct per COMPARE_ENGINE_RULES.md:**
  - Rule 2.4: "Formatting-Only = raw diff with no semantic diff." Salary 50000→50000.0 is raw-diff (string form differs) but not semantic-diff (numeric value is equal). Classifying as `formatting_only` is exactly right.
  - Rule 2.3: Modified requires "value difference that remains after all active ignore rules." Numeric type promotion means 50000 == 50000.0 semantically.
- **Why the benchmark expectation of 50,000 was wrong:**
  - The benchmark generator applied global Salary type change (Int→Float) to ALL rows in File B. For 4,437 rows that received no other change, the only difference was Salary format.
  - The generator's summary counted those 4,437 as "modified" (any change = modified). The engine correctly separates them into the `formatting_only` bucket.
- **Confirmed via instrumentation:** `debug_ki016.py` CHECK 5 shows all `same_or_fmt` rows have raw Salary diffs ("50000" vs "50000.0"). CHECK 6 confirms zero null-propagation. CHECK 7 confirms Int64/Float64 type mismatch for Salary. Verification run: `modified=45,563 + formatting_only=449,437 = 495,000` (all matched rows accounted).
- **Fix applied:** `benchmark_p1.py` EXPECTED_500K updated: `modified_rows = 45,563`. New assertion added: `formatting_only_rows >= 4_437`.
- **JoinDate note:** Both files infer JoinDate as `String`. File B has 9,904 US-format dates that differ in string form from ISO dates in A → those rows ARE detected as `modified` (string comparison "2021-02-08" != "01/02/2021" is True). No JoinDate-related engine bug.

### KI-017 — Two full-file join scans now run per compare (performance regression from P1-T1)
- **Status:** Known trade-off — acceptable until Phase 3
- **File:** `differ.py`
- **Severity:** MEDIUM (performance only, no correctness impact)
- **Impact:** Benchmark elapsed time increased from ~168s to ~344s. The `raw_joined` filter+count pass (for `formatting_only` count) is a second full scan of the 500k-row join result in addition to the semantic pass. For 5GB files this could be significant.
- **Fix:** P3-T3 — Merge semantic and raw joins into one. A single combined join would allow both semantic and raw diffs to be classified in one Polars expression plan, eliminating the second scan.
- **Fix target:** Phase 3.

### KI-018 — `ColumnDiffStats.modified_count` and `formatting_only_count` are sample-based (≤1,000 rows)
- **Status:** Unfixed — known gap after P1-T1
- **File:** `differ.py` (sample loop)
- **Severity:** MEDIUM
- **Impact:** Per-column `modified_count` and `formatting_only_count` in `column_diffs` are still collected from the Python loop over `sem_sample` (at most 1,000 rows). `change_rate` denominator was fixed (D-007: now uses `total_rows_f1`) but the numerator is still sample-based, so rates are underestimates.
- **Fix:** P3-T1 — Replace Python loop with Polars per-column expression plan. Compute exact per-column counts in the same full-file pass.
- **Fix target:** Phase 3.

---

## Issues Closed

### KI-001 — Diff counts sample-based → FIXED (P1-T1, 2026-05-14)
### KI-002 — No full-file count path → PARTIALLY FIXED (P1-T1, counts done; export still P3-T5)
### KI-003 — Added/removed null misclassification → FIXED (P1-T2, 2026-05-14)
### KI-004 — `_infer_type()` always returns "string" → FIXED (P1-T3, 2026-05-14)
### KI-008 — `infer_schema_length=1000` causes type mis-inference → FIXED (P1-T8, 2026-05-14)
### KI-010 — No `is_full_count` field → FIXED (P1-T5 implemented alongside P1-T1/T2, 2026-05-14)
