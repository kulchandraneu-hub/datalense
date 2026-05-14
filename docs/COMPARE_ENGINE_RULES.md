# Compare Engine Rules — DataLens

_These rules define the intended behavior of the diff engine. Implement to these specs exactly._
_Last updated: 2026-05-14_

---

## 1. Core Principle

The compare engine answers one question: **given two files that represent the same dataset at different points in time, what rows changed, and how?**

- Rows are matched by key, not by position.
- Changes are classified by type (added / removed / modified / formatting_only).
- All counts must be **full-file**, not sample-based.
- Sample rows are provided only for **display purposes** and must be labeled as such.

---

## 2. Row Classification Rules

Every row in the full-outer join result falls into exactly one category.

### 2.1 Added
- **Definition:** Row key is present in file 2 (candidate) but absent from file 1 (reference).
- **Detection:** After full-outer join with sentinel columns — `_in_f1 IS NULL` and `_in_f2 IS NOT NULL`.
- **Severity:** High. These are new records.
- **Must NOT be confused with:** A row present in both files where all non-key values happen to be null.

### 2.2 Removed
- **Definition:** Row key is present in file 1 (reference) but absent from file 2 (candidate).
- **Detection:** After full-outer join with sentinel columns — `_in_f1 IS NOT NULL` and `_in_f2 IS NULL`.
- **Severity:** High. These are deleted records.
- **Must NOT be confused with:** A row present in both files where all non-key values happen to be null.

### 2.3 Modified (semantic change)
- **Definition:** Row key is present in both files AND at least one non-key column has a value difference that remains after all active ignore rules are applied.
- **Detection:** `_in_f1 IS NOT NULL AND _in_f2 IS NOT NULL` AND at least one `col_f1 != col_f2` in the semantic (post-rules) frame.
- **Severity:** Medium to high depending on which columns changed.

### 2.4 Formatting-Only
- **Definition:** Row key is present in both files AND at least one non-key column differs in raw form, but after all active ignore rules are applied, no column differs.
- **Detection:** `_in_f1 IS NOT NULL AND _in_f2 IS NOT NULL` AND no semantic diff AND at least one raw diff.
- **Severity:** Low. These are presentational differences only.
- **Examples:**
  - `"BOB"` vs `"bob"` when `ignore_case=True`
  - `"  Alice  "` vs `"Alice"` when `ignore_whitespace=True`
  - `"50000"` vs `"50000.0"` when numeric normalization is active (future)
  - `"2021-02-08"` vs `"02/08/2021"` when `ignore_date_format=True` (future)

### 2.5 Unchanged
- **Definition:** Row key is present in both files AND no column differs in either raw or semantic form.
- **Detection:** Both sides present, no diffs.
- **Not reported** in diff counts. Not included in sample_diffs.

---

## 3. Semantic vs Raw Frames

Two parallel representations of each file are maintained:

| Frame | How produced | Used for |
|-------|-------------|---------|
| Raw | `_cast_to_str(lf)` — all columns cast to Utf8 | Detecting formatting-only changes |
| Semantic | `_apply_ignore_rules(lf, rules)` — normalization applied | Detecting semantic changes |

**Rule:** A change is **semantic** if it appears in the semantic frame diff.
**Rule:** A change is **formatting-only** if it appears in the raw frame diff but NOT in the semantic frame diff.

---

## 4. Ignore Rules

Ignore rules reduce false positives by normalizing both files before semantic comparison.

| Rule | Effect | Applied to |
|------|--------|-----------|
| `ignore_case` | `.str.to_lowercase()` | String columns only |
| `ignore_whitespace` | `.str.strip_chars()` | String columns only |
| `ignore_date_format` | Normalize to ISO 8601 | Date/datetime columns (future) |
| `null_vs_blank` | Treat null and empty string as equivalent | All columns (future) |

**Important constraints:**
- Ignore rules apply only to the **semantic** frame comparison.
- The raw frame is never modified by ignore rules (it is used for detecting what changed).
- Ignore rules do NOT apply to key columns during the join — keys must match exactly.
- Numeric type differences (`50000` vs `50000.0`) are NOT currently normalized by any ignore rule. This is a known gap (KI-008).

---

## 5. Key Column Rules

### 5.1 Selection priority
1. User-specified key columns via API (`key_columns` parameter) — always trusted.
2. Auto-detected by `discover_keys()` — must be validated against full file before use.
3. Fallback: first column in file 1.

### 5.2 Uniqueness requirement
- Key columns MUST form a unique key in both files.
- `validate_key(lf, key_columns)` must be called on the full LazyFrame for BOTH files before `diff_files()` is called.
- If the key is not unique in either file, diff counts are unreliable. Options:
  - Abort the diff and report the duplicate key error.
  - Warn the user and proceed with the understanding that counts may be inflated.
- **Current behavior:** No full-file key validation before diff. (Bug KI-006, fix P1-T7.)

### 5.3 Composite keys
- Up to 3 columns supported (`max_composite=3` in `discover_keys()`).
- Composite keys must be concatenated consistently for join matching.
- Null values in key columns are unpredictable in joins — warn if null rate > 0.

### 5.4 Key columns in diff output
- Key column values are always preserved in `RowDiff.key_value` as a `|`-separated string.
- Key columns are NOT compared for changes (only non-key columns are diff'd).
- Key columns are included in every export row for traceability.

---

## 6. Null Handling Rules

Four null types are tracked independently:

| Type | Definition | Detection |
|------|-----------|-----------|
| Polars null | Python `None` / CSV empty cell | `pl.col(c).is_null()` |
| Empty string | `""` — zero-length string after parsing | `cast(Utf8).str.len_chars() == 0` (when not null) |
| Whitespace-only | `"   "` — non-zero length, all whitespace | `stripped_len == 0 AND len > 0` (when not null) |
| Textual null | `"null"`, `"N/A"`, `"nan"`, etc. | `cast(Utf8).str.to_lowercase().is_in(TEXTUAL_NULLS)` |

**Total null variants** = sum of all four types.
**Null variant rate** = total null variants / row_count.

### Null detection in diff
- A semantic diff between `None` and `"hello"` is a **null resolution** (null_resolved_count +1).
- A semantic diff between `"hello"` and `None` is a **null introduction** (null_introduced_count +1).
- When `null_vs_blank=True` (future): Polars null and empty string are treated as equivalent (not a semantic change).

### Null in key columns
- Null values in key columns cause rows to be unmatched in the join.
- These rows appear as added (if null key is in file 2) or removed (if null key is in file 1).
- Report null key count as a warning in validation output.

---

## 7. Diff Output Guarantees

After Phase 1 is complete, the following must hold:

| Field | Guarantee |
|-------|-----------|
| `added_rows` | Exact count over full file, not sample |
| `removed_rows` | Exact count over full file, not sample |
| `modified_rows` | Exact count over full file, not sample |
| `formatting_only_rows` | Exact count over full file, not sample |
| `is_full_count` | `True` if all counts are full-file; `False` if any are estimated |
| `rows_scanned` | Number of join rows actually evaluated |
| `sample_diffs` | Up to 200 rows for display; labeled as sample |
| `column_diffs[c].change_rate` | Fraction of `total_rows_f1` (not sample size) |
| `confidence_score` | Reflects actual count certainty, not a naive fixed value |

---

## 8. What the Compare Engine Must NOT Do

Per PROJECT_BRIEF.md:

- Must NOT perform validation logic inside the diff (null checks, type checks are for `validator.py`).
- Must NOT silently sample data unless `is_full_count = False` is set explicitly.
- Must NOT mix schema comparison results into diff row counts.
- Must NOT modify the original LazyFrames (work on copies / derived frames only).
- Must NOT collect full dataframes into Python memory for counting — use Polars aggregations.

---

## 9. Scope of Each Engine Module

| Module | Owns | Does NOT own |
|--------|------|-------------|
| `differ.py` | Row classification, diff counts, sample_diffs, ignore rule application | Validation checks, profiling, key discovery |
| `validator.py` | Null rates, type consistency, business rules, schema drift | Row-level diff, added/removed detection |
| `profiler.py` | Column-level statistics (null types, type distribution, min/max) | Row comparisons, diff counting |
| `key_discovery.py` | Key candidate detection, key uniqueness validation | Diff logic, validation |
| `compare.py` | Orchestration only — calls other modules in order | Any logic that belongs in sub-modules |
