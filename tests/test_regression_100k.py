"""
100k benchmark regression tests.

These tests lock in Phase 1 correctness against the 100k benchmark files.
Run after every code change to catch regressions before they reach the 500k gate.

Usage:
    pytest -m regression                     # run only these tests
    pytest tests/test_regression_100k.py    # run this module directly

Runtime: ~30s on reference hardware.
Marker: regression
"""
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from compare import CompareRequest, run_compare  # noqa: E402

pytestmark = pytest.mark.regression

BASE = Path(__file__).parent.parent / "testing_input_files"
FILE_A = BASE / "benchmark_100k_file_A.csv"
FILE_B = BASE / "benchmark_100k_file_B.csv"


@pytest.fixture(scope="module")
def result_100k():
    if not FILE_A.exists() or not FILE_B.exists():
        pytest.skip("100k benchmark files not found in testing_input_files/")
    return run_compare(CompareRequest(
        file1=FILE_A,
        file2=FILE_B,
        key_columns=["CustomerID"],
    ))


# ─── Hard count assertions ─────────────────────────────────────────────────────

def test_total_rows_f1(result_100k):
    assert result_100k.diff.total_rows_f1 == 100_000


def test_added_rows(result_100k):
    assert result_100k.diff.added_rows == 1_000


def test_removed_rows(result_100k):
    assert result_100k.diff.removed_rows == 2_000


def test_is_full_count_false(result_100k):
    """
    File B has 100 duplicate CustomerID rows → key_degraded=True → is_full_count=False.
    This is the CORRECT engine behavior: counts are unreliable when the key is non-unique.
    """
    assert result_100k.diff.is_full_count is False


def test_key_columns(result_100k):
    assert "CustomerID" in result_100k.diff.key_columns


# ─── Validation: Mixed Types ───────────────────────────────────────────────────

def test_lastpurchasedate_mixed_types_in_f2(result_100k):
    """
    P1-T3 regression: LastPurchaseDate in file B has ~4% US-format dates.
    _infer_type must detect this and Mixed Types check must fire.
    """
    checks = [
        c for c in result_100k.validation_f2.checks
        if c.column == "LastPurchaseDate" and c.name == "Mixed Types"
    ]
    assert len(checks) > 0, "Mixed Types check not found for LastPurchaseDate in file B"


# ─── Validation: Duplicate Keys ────────────────────────────────────────────────

def test_duplicate_key_warning_in_f2(result_100k):
    """File B has 100 duplicate CustomerID rows → Duplicate Keys check must appear."""
    dup_checks = [
        c for c in result_100k.validation_f2.checks
        if "Duplicate" in c.name
    ]
    assert len(dup_checks) > 0, "Duplicate Keys check not found in file B validation"


# ─── Structural guarantees ─────────────────────────────────────────────────────

def test_diff_result_fields_present(result_100k):
    """DiffResult must expose is_full_count, rows_scanned, formatting_only_rows."""
    d = result_100k.diff
    assert hasattr(d, "is_full_count")
    assert hasattr(d, "rows_scanned")
    assert hasattr(d, "formatting_only_rows")


def test_row_total_accounting(result_100k):
    """No row can be counted more times than the total rows in both files combined."""
    d = result_100k.diff
    total_changes = d.added_rows + d.removed_rows + d.modified_rows + d.formatting_only_rows
    assert total_changes <= (d.total_rows_f1 + d.total_rows_f2)


def test_sample_diffs_not_empty(result_100k):
    """At least some sample rows must be populated for display."""
    assert len(result_100k.diff.sample_diffs) > 0


def test_column_diffs_populated(result_100k):
    """column_diffs dict must contain at least one entry."""
    assert len(result_100k.diff.column_diffs) > 0
