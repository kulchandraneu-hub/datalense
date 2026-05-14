# Known Issues — DataLens

_Last updated: 2026-05-14 (P1-T1 + P1-T2 + P1-T6 + P1-T7 applied)_
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
- **Status:** Unfixed
- **File:** `profiler.py:159–209`
- **Impact:** `counts["string"] = row_count` always. Since string count always equals or exceeds any other type count, `max(counts)` always returns "string". Downstream effect: `invalid_parse_count` is always 0, so the "Mixed Types" validation check (`validator.py:221–237`) **never fires**.
- **Root cause:** Type counts are not mutually exclusive; string count is not "rows that only parse as string", it is all rows.
- **Fix:** P1-T3 — Use priority-based mutually exclusive type counting.
- **Fix target:** Phase 1, step 5.

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
- **Status:** Unfixed
- **Files:** `compare.py:207–214`, `metadata.py:54–58`
- **Impact:** Polars infers Salary as `Int64` in file A (integer format) and `Float64` in file B (float format). When cast to string: `50000` ≠ `50000.0`. Equal salary values appear as "modified" (false positive).
- **Fix:** P1-T8 — Raise `infer_schema_length` to at minimum 10,000.
- **Fix target:** Phase 1, step 6.

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

### KI-016 — `modified_rows` under-counts by ~4,437 due to cross-type column comparison
- **Status:** Unfixed — introduced during P1-T1 investigation
- **File:** `differ.py` (`any_sem_diff` expression)
- **Severity:** MEDIUM
- **Impact:** Benchmark shows `modified_rows = 45,563` vs expected `50,000`. Gap = 4,437 rows. Root cause: when file A infers JoinDate (or similar column) as `Date` and file B infers it as `Utf8` (because of mixed ISO/US formats), `pl.col("{c}_f1") != pl.col("{c}_f2")` produces `null` (not True) when comparing `Date` vs `Utf8`. `pl.any_horizontal` treats `null` as False, so those rows slip to `same_or_fmt` → `formatting_only` even though JoinDate actually changed.
- **Fix:** P1-T8 — Raise `infer_schema_length` to 10,000. With more rows, both files will infer JoinDate as Utf8 consistently, making the comparison work correctly. Alternatively, `schema_overrides` to force Utf8 for ambiguous columns.
- **Fix target:** Phase 1, step 6 (P1-T8).

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
### KI-010 — No `is_full_count` field → FIXED (P1-T5 implemented alongside P1-T1/T2, 2026-05-14)
