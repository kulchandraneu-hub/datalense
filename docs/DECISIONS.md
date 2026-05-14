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
- **Date:** 2026-05-14 (planned, not yet implemented)
- **Status:** Pending (Phase 1, step 4)
- **Decision:** After `discover_keys()` returns candidates, call `validate_key(lf1, key_columns)` and `validate_key(lf2, key_columns)` on the full LazyFrames before `diff_files()` is invoked. If either key is non-unique, surface a warning and either abort or proceed with a user-acknowledged degraded state.
- **Rationale:** A non-unique key in either file causes the full-outer join to produce a Cartesian product for duplicate rows, silently inflating all diff counts. This cannot be caught after the fact.
- **Alternatives considered:**
  - Trust `discover_keys()` sample: risks Cartesian product on full file.
  - Deduplicate before join: changes the data — not acceptable without explicit user instruction.

---

### D-005 — Profiler accepts pre-computed `FileProfile` to avoid re-profiling
- **Date:** 2026-05-14 (planned, not yet implemented)
- **Status:** Pending (Phase 1, step 3)
- **Decision:** Add `profile: Optional[FileProfile] = None` to `validate_file()`. If provided, skip the internal `profile_file()` call. `compare.py` passes the profiles computed in steps 4–5 when calling `validate_two_files()`.
- **Rationale:** The compare flow currently profiles each file twice (once in `run_compare`, once in `validate_two_files`). Eliminating the duplicate roughly halves profiling I/O for compare runs.
- **Alternatives considered:**
  - Cache profile by file path: fragile (file can change between calls; requires cache invalidation).
  - Move profiling out of `validate_file()` entirely: would break the standalone validate flow (`/api/validate`) which has no pre-computed profile.

---

### D-006 — Type inference uses priority-based mutually-exclusive counting
- **Date:** 2026-05-14 (planned, not yet implemented)
- **Status:** Pending (Phase 1, step 5)
- **Decision:** In `_infer_type()`, use a priority hierarchy: Int64 > Float64 > Boolean > Date > Datetime > String. A column's dominant type is the highest-priority type that successfully parses ≥ 95% of non-null rows. `invalid_parse_count` = rows that fail to parse as the dominant type (and are not null).
- **Rationale:** The current implementation adds independent (overlapping) counts then normalizes, which always results in "string" being dominant because `counts["string"] = row_count` always. This makes the "Mixed Types" validation check permanently inactive.
- **Alternatives considered:**
  - Use Polars `dtype` from schema: fast but only reflects inferred schema from `infer_schema_length` rows, not the full distribution.
  - Use Polars `Series.dtype` after cast attempt: equivalent to priority-based approach but requires more scaffolding.
- **Threshold note:** 95% dominance means a column with 5% mixed types triggers "Mixed Types" warning. This matches the existing check in `_check_type_consistency` (fires when `max_pct < 0.95`).

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
- **Date:** 2026-05-14 (planned, not yet implemented)
- **Status:** Pending (Phase 1, step 6)
- **Decision:** Raise `infer_schema_length` from 1000 to 10,000 in both `metadata.py` and `compare.py:_load_lazy_frame()`.
- **Rationale:** The benchmark files have mixed formats (Salary int vs float, JoinDate ISO vs US). With 1000-row inference, the type may be inferred from a uniform section of the file, leading to parse errors or incorrect comparisons for later rows.
- **Alternatives considered:**
  - `infer_schema_length=0` (scan all rows): correct but slow for 500k+ files.
  - User-supplied `schema_overrides`: ideal for production; deferred to Phase 3.
  - Keep 1000 and add schema_overrides API param: the right long-term answer, but 10,000 is a safe interim fix.
- **Trade-off:** 10× more rows read during schema inference. For 500k-row, 14-column files, this is ~140k cell reads — negligible compared to profiling cost.

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
