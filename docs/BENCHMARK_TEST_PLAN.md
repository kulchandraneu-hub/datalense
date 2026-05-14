# Benchmark Test Plan — DataLens

_Files: `testing_input_files/benchmark_500k_file_A.csv` and `benchmark_500k_file_B.csv`_
_Source of truth: `testing_input_files/benchmark_500k_summary.txt`_
_Do NOT move, rename, or modify the benchmark files._

---

## Benchmark File Characteristics

### File A (reference / "old")
| Property | Value |
|----------|-------|
| Rows | 500,000 |
| Header row | 1 |
| Total lines | 500,001 |
| Columns | 14 |
| Column names | EmployeeID, FirstName, LastName, Department, Salary, Bonus, JoinDate, Status, Email, City, Country, ExperienceYears, Rating, Remarks |
| Key column | EmployeeID (integer, unique) |
| Salary format | Integer (`177028`) |
| JoinDate format | ISO 8601 (`2021-02-08`) |
| Salary nulls | None |

### File B (candidate / "new")
| Property | Value |
|----------|-------|
| Rows | 500,000 |
| Header row | 1 |
| Total lines | 500,001 |
| Columns | 14 (same schema) |
| Key column | EmployeeID (integer, unique) |
| Salary format | Float with `.0` suffix (`182028.0`) — formatting change |
| JoinDate format | Mixed: ISO (`2021-02-08`) and US (`01/02/2021`) — mixed types |
| Salary nulls | ~9,897 empty cells (confirmed by grep) |

---

## Injected Changes (from benchmark_500k_summary.txt)

| Change type | Expected count | Notes |
|-------------|---------------|-------|
| Modified rows | **50,000** | Semantic value changes |
| Removed rows | **5,000** | Present in A, not in B |
| Added rows | **5,000** | Present in B, not in A |
| Salary nulls | ~9,897 | Empty Salary in file B |
| Case-only changes | Some subset of 50k modified | Should classify as `formatting_only` |
| Mixed date formats | Affects JoinDate column in B | Should trigger "Mixed Types" validation |
| Email typos | Some subset of 50k modified | Semantic changes |

---

## Required Assertions (must all pass after Phase 1)

### Diff Counts

```python
assert diff.added_rows == 5_000
assert diff.removed_rows == 5_000
assert diff.modified_rows == 50_000
assert diff.is_full_count == True
assert diff.rows_scanned == diff.total_rows_f1  # full scan
assert diff.total_rows_f1 == 500_000
assert diff.total_rows_f2 == 500_000
```

### Key Detection

```python
assert diff.key_columns == ["EmployeeID"]
# Key must be validated against full file, not just sample
```

### Formatting-Only vs Semantic

```python
# Case-only changes must not appear in modified_rows
assert diff.formatting_only_rows > 0
# Total accounted rows must not exceed total changed rows
assert (diff.added_rows + diff.removed_rows + diff.modified_rows
        + diff.formatting_only_rows) <= (diff.total_rows_f1 + diff.total_rows_f2)
```

### Validation — File B Salary

```python
salary_profile = next(c for c in report_b.profile.columns if c.name == "Salary")
assert salary_profile.total_null_variants > 0
assert salary_profile.null_variant_rate > 0.0
# Should trigger WARNING or ERROR based on threshold
salary_checks = [c for c in report_b.checks if c.column == "Salary" and "Null" in c.name]
assert len(salary_checks) > 0
```

### Validation — File B JoinDate Mixed Types

```python
joindate_profile = next(c for c in report_b.profile.columns if c.name == "JoinDate")
# After P1-T3 fix: not all rows parse as date (ISO vs US format conflict)
joindate_checks = [c for c in report_b.checks
                   if c.column == "JoinDate" and c.name == "Mixed Types"]
assert len(joindate_checks) > 0
```

### Confidence Score

```python
# With 10,000 added+removed rows, confidence should reflect real uncertainty
assert diff.confidence_score < 1.0
```

---

## Pre-Phase-1 Baseline (expected FAILING behavior)

Run this before Phase 1 to confirm current broken behavior. Do not fix yet.

```python
# These will fail before Phase 1 because counts are from head(1000)
# Document the actual returned values to track improvement
baseline_added    = diff.added_rows      # expected: some small number ≤ 1000
baseline_removed  = diff.removed_rows    # expected: some small number ≤ 1000
baseline_modified = diff.modified_rows   # expected: some small number ≤ 1000
print(f"BASELINE (broken): added={baseline_added} removed={baseline_removed} modified={baseline_modified}")
# After Phase 1, these must equal 5000, 5000, 50000
```

---

## Performance Targets (Phase 3 — not Phase 1)

These are targets, not acceptance criteria for Phase 1.

| Operation | File size | Current (est.) | Target |
|-----------|-----------|----------------|--------|
| Metadata load | 500k rows | < 5s | < 2s |
| Profile (2 files) | 500k × 14 cols | unknown | < 30s |
| Key validation | 500k rows | unknown | < 10s |
| Full-file diff | 500k × 500k join | unknown | < 60s |
| Total compare run | 500k vs 500k | unknown | < 120s |

Measure actual times during Phase 1 implementation and record here.

---

## Test File Locations

```
testing_input_files/
├─ benchmark_500k_file_A.csv    # reference file (do not modify)
├─ benchmark_500k_file_B.csv    # candidate file (do not modify)
└─ benchmark_500k_summary.txt   # ground truth counts
```

Planned test module: `tests/test_benchmark.py` (created in Phase 2).

---

## Notes on False Positives to Expect

1. **Salary format (`50000` vs `50000.0`):** With `ignore_rules.date_format = False`, these are raw string differences. The system should classify them as `formatting_only` if numeric equivalence is detected, or `modified` if it is not. Document which behavior is implemented.

2. **JoinDate format (`2021-02-08` vs `01/02/2021`):** These are semantically different dates if parsed naively (Feb 8 vs Jan 2 in US format). With `ignore_rules.date_format = True`, both may normalize — but normalization requires knowing which format each side uses, which the current engine does not do. Mark as **known limitation** until a date normalization rule is added.

3. **Key column fallback:** If `EmployeeID` is not auto-detected (possible if `discover_keys` finds duplicates in sample), the fallback is the first column (`EmployeeID` anyway). Confirm this is consistent.
