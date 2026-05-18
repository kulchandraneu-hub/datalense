# Design Decisions — DataLens

_Log of all significant design and implementation decisions made during the project._
_Add a new entry for every non-trivial decision. Never delete old entries._
_Format: date, decision, rationale, alternatives considered._

---

## Decision Log

---

### D-001 — Use sentinel columns (`_in_f1`, `_in_f2`) to detect added/removed rows
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T2, 2026-05-14)
- **Decision:** Before the full-outer join, add `pl.lit(1).alias("_in_f1")` to the file 1 LazyFrame and `pl.lit(1).alias("_in_f2")` to the file 2 LazyFrame. After joining, `_in_f1 IS NULL` means the row was added (only in file 2); `_in_f2 IS NULL` means the row was removed (only in file 1).
- **Rationale:** The previous heuristic (`all non-key cols from one side are None`) produces false positives when a row legitimately has null values in all non-key columns. Sentinel columns provide unambiguous row-origin tracking with zero chance of collision.
- **Alternatives considered:**
  - Row index as sentinel: fragile for large files (row order not preserved in joins).
  - Check key column null after join: not possible with `coalesce=True` (keys are merged and always non-null).
- **Impact:** Sentinel columns must be dropped from the frame before any column-level comparisons to avoid including `_in_f1`/`_in_f2` in the shared_cols loop.

---

### D-002 — Full-file diff counts via Polars expressions, not Python loop
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T1, 2026-05-14)
- **Decision:** Classify every row in the full join result using a Polars `when/then/otherwise` expression to create a `_change_type` column. Aggregate counts using `.group_by("_change_type").agg(pl.len()).collect()`. The Python loop over `sem_sample` is retained only for building `sample_diffs` (≤200 rows for display).
- **Rationale:** A Python `for` loop over 500k rows takes seconds and holds everything in Python memory. Polars can do the same classification in a single vectorized pass without materializing row dicts.
- **Alternatives considered:**
  - Keep Python loop but over full `collect()`: would collect entire join result (~500k row dicts) into Python memory — violates the LazyFrame constraint.
  - Keep `head(1000)` but label as sample: fixes the labeling problem but not the accuracy problem. Not acceptable.

---

### D-003 — `is_full_count` and `rows_scanned` added to `DiffResult`
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T5, implemented alongside P1-T1/T2, 2026-05-14)
- **Decision:** Add two fields to `DiffResult`: `is_full_count: bool = False` (True when all counts come from a full-file scan) and `rows_scanned: int = 0` (total join rows evaluated). Set both in `diff_files()`. Expose in API serializer.
- **Rationale:** Consumers (UI, history, API clients) must be able to distinguish accurate counts from estimates without parsing the confidence score heuristic. An explicit boolean is unambiguous.
- **Alternatives considered:**
  - Embed in `confidence_score` only: confidence is already semantically overloaded and not machine-parseable.
  - Add a `mode: Literal["full", "sample"]` field: more expressive but `is_full_count` is simpler and sufficient for current needs.

---

### D-004 — Validate key on full file before diff (not just in validator)
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T7, 2026-05-14)
- **Decision:** After `discover_keys()` returns candidates, call `validate_key(lf1, key_columns)` and `validate_key(lf2, key_columns)` on the full LazyFrames before `diff_files()` is invoked. If either key is non-unique, surface a warning and either abort or proceed with a user-acknowledged degraded state.
- **Rationale:** A non-unique key in either file causes the full-outer join to produce a Cartesian product for duplicate rows, silently inflating all diff counts. This cannot be caught after the fact.
- **Alternatives considered:**
  - Trust `discover_keys()` sample: risks Cartesian product on full file.
  - Deduplicate before join: changes the data — not acceptable without explicit user instruction.

---

### D-005 — Profiler accepts pre-computed `FileProfile` to avoid re-profiling
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T6, 2026-05-14)
- **Decision:** Add `profile: Optional[FileProfile] = None` to `validate_file()`. If provided, skip the internal `profile_file()` call. `compare.py` passes the profiles computed in steps 4–5 when calling `validate_two_files()`.
- **Rationale:** The compare flow currently profiles each file twice (once in `run_compare`, once in `validate_two_files`). Eliminating the duplicate roughly halves profiling I/O for compare runs.
- **Alternatives considered:**
  - Cache profile by file path: fragile (file can change between calls; requires cache invalidation).
  - Move profiling out of `validate_file()` entirely: would break the standalone validate flow (`/api/validate`) which has no pre-computed profile.

---

### D-006 — Type inference uses priority-based mutually-exclusive counting
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T3, 2026-05-14)
- **Decision:** In `_infer_type()`, use a priority hierarchy: Int64 > Float64 > Boolean > Date > Datetime > String. Dominant = first type in priority order where ≥ 95% of non-null rows parse. If none qualifies, best specific type is used (enables Mixed Types detection for moderate mixing). `type_distribution` is a 2-key exclusive dict; `invalid_parse_count` = non-null rows that fail the dominant type.
- **Rationale:** The previous implementation set `counts["string"] = row_count` unconditionally, always making string the max in `max(counts)`. This kept `invalid_parse_count = 0` permanently, suppressing the Mixed Types check.
- **Alternatives considered:**
  - Use Polars `dtype` from schema: only reflects inferred schema from `infer_schema_length` rows, not full distribution.
  - Truly mutually-exclusive counts (subtract each type from the next): requires subtracting overlapping counts across types, which is fragile and harder to reason about.
- **Temporal guard:** Columns already typed as `pl.Date`/`pl.Datetime` in the LazyFrame schema skip Int64/Float64/Boolean casts; without this guard, dates cast to days-since-epoch integers would falsely dominate as "integer".
- **Implementation note:** `non_null_count` is passed as a parameter from `profile_column` (already has `polars_null` count) to avoid an extra `.collect()` call inside `_infer_type`.

---

### D-011 — Mixed Types check fires on any non-zero invalid_parse_count for specific-type columns
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T3, 2026-05-14)
- **Decision:** `_check_type_consistency` in `validator.py` now fires when `inferred_type != "string" and invalid_parse_count > 0`. The previous condition `max_pct < 0.95 and invalid_parse_count > 0` suppressed warnings for columns with 1–5% mixed content.
- **Rationale:** The 100k benchmark has `LastPurchaseDate` with 4% US-format dates (4004 rows). The 500k benchmark has `JoinDate` with ~0.9% US-format dates (4437 rows). Both are below the previous 5% threshold, so the check never fired even after fixing `_infer_type`. The new condition fires whenever any non-string-dominant column has rows that fail to parse as the dominant type.
- **Alternatives considered:**
  - Lower `max_pct` threshold from 0.95 to 0.99: still uses a percentage-based gate, but the validator check's threshold and `_infer_type`'s THRESHOLD would need to stay consistent. More fragile.
  - Minimum rate threshold (e.g., `invalid_rate > 0.001`): avoids firing for a single corrupt row, but introduces a second tunable constant with no clear basis.
- **Trade-off:** May fire for a column with even a single parsing failure (e.g., one corrupt row in an otherwise clean integer column). The `affected_count` in the ValidationCheck output tells users how many rows are affected, so they can judge significance themselves.

---

### D-007 — `change_rate` denominator is `total_rows_f1`, not sample size
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (alongside P1-T1, 2026-05-14)
- **Decision:** After Phase 1, compute `change_rate` as `col_modified_count / total_rows_f1` (full-file denominator), not `count_in_sample / len(sample)`.
- **Rationale:** The current denominator (`len(sem_sample) ≤ 1000`) makes change rates meaningless for large files. A column with 50k changes in 500k rows should show 10%, not an arbitrary fraction of 1000.

---

### D-008 — Excel diff shows both `{col}_before` and `{col}_after` columns
- **Date:** 2026-05-14 (planned, not yet implemented)
- **Status:** Pending (Phase 1, step 7)
- **Decision:** For each data column in the Excel diff export, write two columns: `{col}_before` (f1 value) and `{col}_after` (f2 value). For added rows, `_before` is empty; for removed rows, `_after` is empty.
- **Rationale:** The current output (`f1_values` only) is useless for change review — the reviewer can see the old value but not the new value.
- **Alternatives considered:**
  - Single column with `old → new` string: harder to filter/sort in Excel.
  - Separate sheets for before/after: requires more openpyxl work; side-by-side columns are more scannable.

---

### D-009 — `infer_schema_length` raised from 1000 to 10,000
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T8, 2026-05-14)
- **Decision:** Raise `infer_schema_length` from `1000` to `10_000` in both `metadata.py:load_metadata()` and `compare.py:_load_lazy_frame()`. Both call sites use the same value so schema inference is consistent between the metadata pass and the diff pass.
- **Rationale:** Files where mixed-format content (e.g. US-format dates, float-suffix integers) appears after row 1000 would be silently mis-typed and cause incorrect comparisons or coercion errors. 10,000 rows provides a 10× larger sample with negligible overhead.
- **Actual benchmark impact:** For the current benchmark files, mixed content appears within the first 1000 rows already, so schema inference is unchanged at 10,000 rows. The fix is defensive for real-world production files.
- **Alternatives considered:**
  - `infer_schema_length=0` (scan all rows): correct but slow for 500k+ files.
  - User-supplied `schema_overrides`: ideal for production; deferred to Phase 3.
  - Keep 1000 and add schema_overrides API param: the right long-term answer; 10,000 is a safe interim.
- **Trade-off:** 10× more rows read during schema inference. For 500k-row, 14-column files, this is ~140k cell reads — negligible compared to profiling and join cost.
- **KI-016 finding:** Investigation during P1-T8 confirmed that `Date != String` in this Polars version raises `InvalidOperationError` (not null propagation). The 4,437-row gap has a different root cause; see D-012.

---

### D-010 — `formatting_only_rows` count via subtraction: raw_both_diff − modified
- **Date:** 2026-05-14
- **Status:** IMPLEMENTED (P1-T1, 2026-05-14)
- **Decision:** Rather than merging the semantic and raw joins (P3-T3), compute `formatting_only_rows` as `max(0, raw_both_present_diff_count − modified)`. `raw_both_present_diff_count` = rows in `raw_joined` where both sentinels are present AND any raw column differs. This equals `modified + formatting_only` since formatting_only = raw-diff but no semantic-diff.
- **Rationale:** Avoids a third join. Keeps P1-T1 changes localized to `differ.py`. The formula is algebraically correct: rows with raw diff that aren't semantically different must be formatting-only.
- **Alternatives considered:**
  - Anti-join of raw-diff rows against modified keys: correct but requires materializing key sets.
  - Merge sem + raw into one join (P3-T3): the right long-term answer; deferred to Phase 3.
- **Trade-off:** Requires a second full scan of `raw_joined` (~344s vs ~168s for 500k files). Accepted for Phase 1; Phase 3 will eliminate the extra scan by merging joins.

---

### D-012 — KI-016 resolved: modified_rows = 45,563 is correct; benchmark expectation of 50,000 was wrong
- **Date:** 2026-05-14
- **Status:** RESOLVED — investigation complete; no engine fix needed; benchmark expectation corrected.
- **Finding:** The 4,437 gap is caused by Polars Int64/Float64 numeric type promotion during semantic comparison.
  - File A: Salary inferred as `Int64` (pure integer CSV values like `50000`).
  - File B: Salary inferred as `Float64` (float CSV values like `50000.0`).
  - Polars comparison `Int64(50000) != Float64(50000.0)` returns **False** (numeric type promotion treats them as equal).
  - For the 4,437 rows where **only** the Salary format changed (same numeric value, no other column changed), `any_sem_diff = False` → classified as `same_or_fmt` by the semantic join.
  - In the raw join: `"50000" != "50000.0"` is True → those 4,437 rows are counted in `formatting_only_rows`.
  - **The rows are not lost**: `modified (45,563) + formatting_only (449,437) = 495,000` = all 495,000 matched rows. Total join rows = 505,000 = 495,000 matched + 5,000 added + 5,000 removed. Fully accounted.
- **Why the benchmark expected 50,000:**
  - The benchmark generator applied a global Salary type change (Int→Float) to all File B rows. For 4,437 rows that received no other modification, the only observable difference was Salary format.
  - The generator's summary counted "any change" as "modified row." The engine correctly separates semantic from formatting-only changes.
- **Engine behaviour is correct per COMPARE_ENGINE_RULES.md rule 2.4:**
  - "Formatting-Only: raw diff with no semantic diff." Salary 50000→50000.0 qualifies: raw strings differ, numeric values are equal.
  - Per BENCHMARK_TEST_PLAN.md note: "Salary format (50000 vs 50000.0): classify as formatting_only if numeric equivalence is detected." The engine detects numeric equivalence via Polars type promotion and classifies correctly.
- **Action taken:** `benchmark_p1.py` EXPECTED_500K corrected to `modified_rows=45_563` and `formatting_only_rows=449_437`. KI-016 marked CLOSED in KNOWN_ISSUES.md.
- **Null propagation check:** Confirmed zero rows where `any_sem_diff = NULL` for matched rows. The `is_null()` mismatch guard in `any_sem_diff` works correctly.
- **JoinDate check:** Both files infer JoinDate as `String`. File B's 9,904 US-format dates are correctly detected as semantic diffs (string "2021-02-08" != "01/02/2021" → True). These 9,904 rows ARE in `modified_rows`, not in the gap.

---

### D-013 — Phase 2 test suite: synthetic fixtures for exact count assertions; demo/benchmark for integration
- **Date:** 2026-05-15
- **Status:** IMPLEMENTED (Phase 2 complete)
- **Decision:** Three-tier fixture strategy:
  1. **Synthetic fixtures** (`tests/fixtures/*.csv`, 3–5 rows, no date cols, no type drift) — exact equality assertions with zero ambiguity. Used for: added/removed/modified/formatting_only counts, sentinel null detection, null introduction, duplicate key degradation, ignore_case reclassification.
  2. **demo_small files** (10/11 rows) — integration assertions on a real-world mix. Used for: validation checks (Mixed Types, Duplicate Keys, Null profile), exact counts where all interactions are traced.
  3. **100k / 500k benchmark files** — regression gate and milestone gate. All Phase 1 acceptance criteria locked in here.
- **Rationale:** Demo files have complex interactions (Cartesian from duplicate key, Int→Float type promotion, mixed date formats) that make count assertions fragile without full analysis. Synthetic fixtures isolate each behavior so a failure pinpoints exactly what broke. Demo and benchmark files serve as integration/acceptance layers.
- **Marker strategy:** `quick` (~11s), `regression` (~26s), `benchmark` (~5min). Developers run `quick` on every save and `regression` before committing.
- **KI-014 fix included:** `compare.py` smoke test now asserts `is_full_count is True`.

---

---

### D-014 — `compare_columns` filter: applied after `column_map` rename, in f1 name-space
- **Date:** 2026-05-16
- **Status:** IMPLEMENTED (Phase 2.6 Win-2)
- **Decision:** `diff_files()` accepts an optional `compare_columns: list[str]`. When non-None, `shared_cols` is filtered to only those columns immediately after the `column_map` rename step. The filter operates on f1 column names (i.e., after any renames are applied). `None` means compare all shared columns — backward-compatible default.
- **Rationale:** Users need to exclude noisy columns (e.g., audit timestamps, legacy fields) from the diff without removing them from the schema. Filtering `shared_cols` is the only change needed; the join, sentinel detection, added/removed counts, and is_full_count logic are all unaffected.
- **Alternatives considered:**
  - Filter inside `_run_compare_job` by dropping columns from the LazyFrame before calling `diff_files()`: would also affect profiling and validation, which must see all columns.
  - Filter inside `_apply_ignore_rules`: wrong layer — ignore rules only normalize values, not select columns.
  - Separate `exclude_columns` parameter (blacklist): whitelist (`compare_columns`) is simpler because "compare only these" is unambiguous when files have different column sets.
- **Invariants preserved:** INV-5 (validation does not see the filter — it runs on unfiltered LazyFrames). INV-6 (full-file counts still use the same join; added/removed detection is key-based and unaffected).
- **Frontend:** The column mapping panel (F-3) was extended with checkboxes (Win-2). Unchecked matched columns are collected into `S.colMap.excluded`; `getCompareColumns()` returns the filtered list or `null` (no filter) if nothing is excluded.

---

### D-P3-T1 — Per-column counts moved to Polars expression plan
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (Phase 3, P3-T1, 2026-05-18)
- **Decision:** Replace Python `for sem_row in sem_sample:` loop with `sem_agg_exprs` + `raw_agg_exprs` computed in a single `.select().collect()` pass over the full joined LazyFrame.
- **Rationale:** Python loop over ≤1,000 sample rows gave wrong per-column counts for large files. Numerators were sample-capped even after the D-007 denominator fix (D-007 fixed `change_rate` denominator to `total_rows_f1` but the numerator remained sample-based).
- **Fix:** `sem_agg_exprs` + `raw_agg_exprs` in a single `.select().collect()`. Exact per-column `modified_count` and `formatting_only_count` for all file sizes.
- **Benchmark impact:** 344s → 93.9s (dominant single-step gain).

---

### D-P3-T2 — Two joins merged into one combined join
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (Phase 3, P3-T2, 2026-05-18)
- **Decision:** Replace the separate semantic join and raw join (two full-outer joins) with a single combined join. Each file's frame carries both semantic ({c}_s1/{c}_s2) and raw ({c}_r1/{c}_r2) columns before the join. Row classification runs in a single `when/then/otherwise` expression plan.
- **Rationale:** Each full-outer join was a full file scan. Two joins = two scans of the 500k-row result. The D-010 subtraction approach (formatting_only = raw_both_diff − modified) required the second scan and was explicitly noted as a Phase 3 trade-off.
- **Result:** 93.9s → 85.7s. Collects reduced from 6 → 4.
- **Alternatives considered:** Anti-join approach (materialise key sets) — more complex, no performance advantage.

---

### D-P3-T3 — Batch column profiling via single streaming collect
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (Phase 3, P3-T3, 2026-05-18)
- **Decision:** Replace per-column `.collect()` calls in `profile_file()` with a single `.select([all_exprs]).collect(engine="streaming")` that computes all column stats in one pass.
- **Rationale:** `profile_file()` was executing ~176 `.collect()` calls (8 type-check expressions × 22 columns). Each `.collect()` triggers a full file scan. At 500k rows × 22 columns this dominated the benchmark runtime.
- **Fix:** All null-type expressions, type-check casts, and aggregate stats assembled into one expression list; a single `.select().collect()` executes the full plan in one pass. Boolean cast guard added for Utf8/String columns (Polars 1.x raises on Boolean cast of string columns).
- **Benchmark impact:** 85.7s → 2.1s (P3-T3 was the dominant gain; 176 `.collect()` calls reduced to 2).

---

### D-P3-T4 — Pre-sort keys before join + streaming CSV sink
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (Phase 3, P3-T4, 2026-05-18)
- **Decision:** Sort both LazyFrames by key columns before the combined join. Use Polars streaming sink for CSV diff export rather than collecting the full diff into RAM.
- **Rationale:** Pre-sorting enables a merge-join strategy (O(n) merge pass) over a hash-join (O(n) hash table in RAM) for very large files. The streaming sink avoids loading full diff CSVs into RAM during export, which would be prohibitive for 5GB files.
- **Benchmark impact:** 2.1s → 2.4s at 500k rows (within noise; sort overhead visible at small scale). Benefit realises at 5GB scale where the hash table would otherwise require significant RAM.
- **Trade-off:** Marginal regression at small scale is acceptable given the 5GB production use case.

---

### D-P4-T1 — `excel_loader.py` created as public API for Excel support
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (P4-T1, 2026-05-18)
- **Decision:** New `excel_loader.py` module provides `load_excel_sheet()` and `list_sheets()`. `compare.py` already had internal `_excel_to_temp_csv` — new module adds testability and the `/api/sheets` endpoint without duplicating engine logic.
- **Rationale:** Isolating Excel handling into its own module enables unit tests for sheet listing and conversion independently of the compare flow. INV-3 compliant: `mkstemp()` + `os.close()` + `newline=""` for Windows handle safety.
- **Alternatives considered:** Expanding `compare.py` internal function — rejected as untestable and would violate module ownership (compare.py is orchestration only).

---

### D-P4-T2 — Side-by-side diff view in UI
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (P4-T2, 2026-05-18)
- **Decision:** Diff table shows before/after column pairs per changed field. Cell-level coloring (not row-level). Toggle preserves inline view. Frozen Key+Type columns for wide tables. `S._lastDiff` stores the last result so toggle rebuilds the view without an extra network request.
- **Rationale:** Row-level coloring alone doesn't show which values changed; cell-level coloring makes change review faster for wide files with many columns.
- **Alternatives considered:** Separate "before" and "after" tabs — rejected as harder to visually compare than paired columns.

---

### D-P4-T3 — SSE progress made deterministic via 8-phase step map
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (P4-T3, 2026-05-18)
- **Decision:** 8-phase step map in `api.py`. Frontend uses `_STEP_PCT` lookup for bar width. Unknown phases fall back to current/total path (backward-compatible).
- **Rationale:** The indeterminate bar gave users no signal of which phase was the bottleneck. Deterministic % per named phase makes progress predictable and debuggable.

---

### D-P4-T4 — Excel report upgraded with summary sheet and professional layout
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (P4-T4, 2026-05-18)
- **Decision:** Summary sheet added as first tab. Frozen header, auto-width columns (max 50 chars). Pastel row colors replace dark fills.
- **Rationale:** Dark fills are hard to read when printed; pastel is print-friendly. Summary sheet gives stakeholders a quick overview without opening the diff tab.
- **Known gap:** `DiffResult` does not store file paths — Summary sheet shows row counts only. Fix deferred to Phase 5 (add optional `f1_path`/`f2_path` fields to `DiffResult`).

---

### D-P4-T5 — Validation tab pass/fail distinction with per-file toggle
- **Date:** 2026-05-18
- **Status:** IMPLEMENTED (P4-T5, 2026-05-18)
- **Decision:** Per-file scoped toggle (default OFF = failures only). Passed checks get ✓ prefix + 0.6 opacity de-emphasis. Failed checks get ✗ prefix + severity color. Toggle resets on each new result load.
- **Rationale:** Analysts reviewing validation output need to see failures immediately without scanning through all passing checks. Default-off for passing checks reduces noise while toggle allows full audit when needed.

---

## Invariants (never violate these)

These are non-negotiable constraints carried forward from the original architecture:

| ID | Invariant |
|----|-----------|
| INV-1 | Always `pl.scan_csv()`. Never `pl.read_csv()`. `.collect()` only for stats, samples, row counts. |
| INV-2 | Cancel checks at phase boundaries only — never inside a Polars expression. |
| INV-3 | Excel temp files: `mkstemp()` + `os.close(fd)` before writing. Windows handle safety. |
| INV-4 | Pydantic only at API boundaries. Internal types are `@dataclass`. |
| INV-5 | Validation must not perform row-comparison logic. Diff must not perform validation logic. |
| INV-6 | Full-file counts only. Any sample-based count must set `is_full_count=False`. |
| INV-7 | Export files written eagerly to disk, not lazily at download time. |
