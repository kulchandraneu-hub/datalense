"""
Regression tests for row-level diff classification correctness.

Coverage:
  - Exact added/removed/modified/formatting_only counts (synthetic clean fixture)
  - Semantic vs formatting-only separation under ignore_case
  - Sentinel-based detection: null non-key value not confused with absent row (P2-T5)
  - Null introduction (value → null transition) classified as modified
  - Duplicate key degradation forces is_full_count=False
  - Demo-small integration: real-world mix of all change types

Marker: quick — all fixtures are 3–10 rows; entire class runs in < 2 seconds.
"""
import pytest

pytestmark = pytest.mark.quick


# ─── Exact counts: clean synthetic fixture ────────────────────────────────────

class TestCleanExactCounts:
    """
    5-row synthetic fixture (no date cols, no type drift) gives deterministic counts.
    Any regression in classification logic will flip these assertions immediately.
    """

    def test_added(self, clean_result):
        assert clean_result.diff.added_rows == 1

    def test_removed(self, clean_result):
        assert clean_result.diff.removed_rows == 1

    def test_modified(self, clean_result):
        # ID=1 (Value 100→150) and ID=2 (Category B→b, case-sensitive) = 2
        assert clean_result.diff.modified_rows == 2

    def test_formatting_only(self, clean_result):
        # No Int→Float drift in clean fixture; no ignore rules active → 0
        assert clean_result.diff.formatting_only_rows == 0

    def test_is_full_count(self, clean_result):
        assert clean_result.diff.is_full_count is True

    def test_total_rows_f1(self, clean_result):
        assert clean_result.diff.total_rows_f1 == 5

    def test_total_rows_f2(self, clean_result):
        assert clean_result.diff.total_rows_f2 == 5

    def test_rows_scanned(self, clean_result):
        # 4 matched (IDs 1-4) + 1 removed (ID=5) + 1 added (ID=6) = 6
        assert clean_result.diff.rows_scanned == 6

    def test_key_columns(self, clean_result):
        assert clean_result.diff.key_columns == ["ID"]


# ─── Semantic vs formatting-only: ignore_case ─────────────────────────────────

class TestSemanticVsFormattingOnly:
    """
    ignore_case reclassifies case-only changes from modified → formatting_only.
    The sum (modified + formatting_only) must be conserved across rule changes.
    """

    def test_without_ignore_case_two_modified(self, clean_result):
        assert clean_result.diff.modified_rows == 2

    def test_ignore_case_reduces_modified_to_one(self, clean_result_ignore_case):
        # ID=2 (Category B→b) becomes formatting_only
        assert clean_result_ignore_case.diff.modified_rows == 1

    def test_ignore_case_produces_one_formatting_only(self, clean_result_ignore_case):
        assert clean_result_ignore_case.diff.formatting_only_rows == 1

    def test_ignore_case_does_not_change_added_removed(self, clean_result, clean_result_ignore_case):
        assert clean_result_ignore_case.diff.added_rows == clean_result.diff.added_rows
        assert clean_result_ignore_case.diff.removed_rows == clean_result.diff.removed_rows

    def test_modified_plus_fmt_only_conserved(self, clean_result, clean_result_ignore_case):
        # Without ignore_case: 2+0=2. With ignore_case: 1+1=2. Same total.
        assert clean_result.diff.modified_rows + clean_result.diff.formatting_only_rows == 2
        assert (clean_result_ignore_case.diff.modified_rows +
                clean_result_ignore_case.diff.formatting_only_rows) == 2


# ─── Sentinel: null in non-key column not confused with absent row (P2-T5) ────

class TestSentinelNullNonKey:
    """
    Pre-P1-T2 heuristic: 'if all non-key values from one side are None, the row is
    added/removed'. This fails when a row legitimately has null non-key values.
    The sentinel columns (_in_f1/_in_f2) fix this unambiguously.

    File A row 3 has Score=null.  Row 3 is absent from file B.
    Expected: REMOVED (not modified, not added).
    """

    def test_removed_count(self, null_sentinel_result):
        assert null_sentinel_result.diff.removed_rows == 1

    def test_no_added(self, null_sentinel_result):
        assert null_sentinel_result.diff.added_rows == 0

    def test_no_modified(self, null_sentinel_result):
        assert null_sentinel_result.diff.modified_rows == 0

    def test_is_full_count(self, null_sentinel_result):
        assert null_sentinel_result.diff.is_full_count is True


# ─── Null introduction ────────────────────────────────────────────────────────

class TestNullIntroduction:
    """
    Row present in both files; a non-key value transitions from non-null to null.
    Must be MODIFIED (null introduced), not removed.
    """

    def test_modified_count(self, null_intro_result):
        assert null_intro_result.diff.modified_rows == 1

    def test_no_removed(self, null_intro_result):
        assert null_intro_result.diff.removed_rows == 0

    def test_no_added(self, null_intro_result):
        assert null_intro_result.diff.added_rows == 0

    def test_is_full_count(self, null_intro_result):
        assert null_intro_result.diff.is_full_count is True

    def test_null_introduced_in_column_diffs(self, null_intro_result):
        # Sample loop detects 999→null for ID=3; null_introduced_count must be >= 1
        score = null_intro_result.diff.column_diffs.get("Score")
        assert score is not None, "Score not found in column_diffs"
        assert score.null_introduced_count >= 1


# ─── Duplicate key degrades is_full_count ─────────────────────────────────────

class TestDuplicateKeyDegradation:
    """
    Duplicate key in file B causes a Cartesian product in the join.
    compare.py must detect this via validate_key() and force is_full_count=False.
    """

    def test_is_full_count_false(self, dup_key_result):
        assert dup_key_result.diff.is_full_count is False


# ─── Demo-small integration ───────────────────────────────────────────────────

class TestDemoSmall:
    """
    Integration test on demo_small files — a 10-row real-world mix containing:
      - 1 added row (2001 only in B)
      - 1 removed row (1009 only in A)
      - 6 modified rows: 1001 (Salary genuine change), 1003 (FirstName whitespace),
        1004 (Dept case), 1005 (JoinDate string mismatch), 1006 (Salary→null),
        1007 (Email change)
      - 4 formatting-only rows: 1002 and 1010 (Salary Int→Float, same value),
        1008×2 (Cartesian from duplicate key, Salary format only)
      - is_full_count=False because 1008 is duplicated in file B (key_degraded)
    """

    def test_added_rows(self, demo_result):
        assert demo_result.diff.added_rows == 1

    def test_removed_rows(self, demo_result):
        assert demo_result.diff.removed_rows == 1

    def test_modified_rows(self, demo_result):
        assert demo_result.diff.modified_rows == 6

    def test_formatting_only_rows(self, demo_result):
        # 1002 + 1010 + 1008-dup1 + 1008-dup2 = 4
        assert demo_result.diff.formatting_only_rows == 4

    def test_is_not_full_count(self, demo_result):
        assert demo_result.diff.is_full_count is False

    def test_total_rows_f1(self, demo_result):
        assert demo_result.diff.total_rows_f1 == 10

    def test_key_columns(self, demo_result):
        assert demo_result.diff.key_columns == ["EmployeeID"]

    def test_rows_scanned(self, demo_result):
        # 10 both-present join rows (incl. 2 from 1008 Cartesian) + 1 added + 1 removed = 12
        assert demo_result.diff.rows_scanned == 12
