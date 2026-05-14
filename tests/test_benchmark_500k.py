"""
500k benchmark milestone tests.

These are the authoritative Phase 1 acceptance tests.  Every assertion here locks
in a specific, reasoned expected value.  Do NOT change an expected value without
updating KNOWN_ISSUES.md, BENCHMARK_TEST_PLAN.md, and DECISIONS.md first.

Usage:
    pytest -m benchmark                      # run only these tests
    pytest tests/test_benchmark_500k.py     # run this module directly

Runtime: ~5 minutes on reference hardware.
Marker: benchmark
"""
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from compare import CompareRequest, run_compare  # noqa: E402

pytestmark = pytest.mark.benchmark

BASE = Path(__file__).parent.parent / "testing_input_files"
FILE_A = BASE / "benchmark_500k_file_A.csv"
FILE_B = BASE / "benchmark_500k_file_B.csv"


@pytest.fixture(scope="module")
def result_500k():
    if not FILE_A.exists() or not FILE_B.exists():
        pytest.skip("500k benchmark files not found in testing_input_files/")
    return run_compare(CompareRequest(
        file1=FILE_A,
        file2=FILE_B,
        key_columns=["EmployeeID"],
    ))


# ─── Diff count hard assertions ────────────────────────────────────────────────

def test_total_rows_f1(result_500k):
    assert result_500k.diff.total_rows_f1 == 500_000


def test_total_rows_f2(result_500k):
    assert result_500k.diff.total_rows_f2 == 500_000


def test_added_rows(result_500k):
    assert result_500k.diff.added_rows == 5_000


def test_removed_rows(result_500k):
    assert result_500k.diff.removed_rows == 5_000


def test_modified_rows(result_500k):
    """
    KI-016 resolved (2026-05-14): 45,563 is the correct count.
    4,437 rows where ONLY Salary format changed (Int64 50000 → Float64 50000.0, same
    numeric value) are correctly classified as formatting_only via Polars type promotion.
    See D-012 and BENCHMARK_TEST_PLAN.md for full explanation.
    """
    assert result_500k.diff.modified_rows == 45_563


def test_formatting_only_rows_exact(result_500k):
    """
    All 495k matched rows have at least a Salary raw-string diff ("50000" vs "50000.0").
    raw_both_diff = 495,000.  formatting_only = 495,000 − 45,563 = 449,437.
    """
    assert result_500k.diff.formatting_only_rows == 449_437


def test_formatting_only_includes_salary_format_rows(result_500k):
    """
    P2-T2: The 4,437 rows where ONLY Salary changed format must be in formatting_only,
    not in modified_rows.  Polars numeric type promotion makes Int64(N) == Float64(N.0).
    """
    assert result_500k.diff.formatting_only_rows >= 4_437


def test_is_full_count(result_500k):
    assert result_500k.diff.is_full_count is True


def test_rows_scanned(result_500k):
    """495k matched + 5k added + 5k removed = 505,000."""
    assert result_500k.diff.rows_scanned == 505_000


def test_key_columns(result_500k):
    assert result_500k.diff.key_columns == ["EmployeeID"]


# ─── Consistency guarantees ────────────────────────────────────────────────────

def test_modified_plus_formatting_only_equals_matched_rows(result_500k):
    """
    P2-T2: Every matched row falls into either modified or formatting_only (or unchanged,
    but unchanged rows have no raw diff so they don't appear in either count).
    modified + formatting_only must equal the count of matched rows with any raw diff.
    For this benchmark all matched rows have at least a Salary raw diff → both sums match.
    """
    d = result_500k.diff
    matched = d.rows_scanned - d.added_rows - d.removed_rows
    assert d.modified_rows + d.formatting_only_rows == matched


def test_row_total_accounting(result_500k):
    d = result_500k.diff
    total_changes = d.added_rows + d.removed_rows + d.modified_rows + d.formatting_only_rows
    assert total_changes <= (d.total_rows_f1 + d.total_rows_f2)


def test_confidence_score_below_one(result_500k):
    """10,000 added+removed rows → non-trivial churn → confidence_score < 1.0."""
    assert result_500k.diff.confidence_score < 1.0


# ─── Validation: File B JoinDate Mixed Types (P2-T3) ──────────────────────────

def test_joindate_mixed_types_in_f2(result_500k):
    """
    P2-T3 / P1-T3 regression: File B JoinDate has ~9,904 US-format dates.
    _infer_type detects the mismatch and Mixed Types check must fire.
    """
    checks = [
        c for c in result_500k.validation_f2.checks
        if c.column == "JoinDate" and c.name == "Mixed Types"
    ]
    assert len(checks) > 0, "Mixed Types check not found for JoinDate in file B"


# ─── Validation: File B Salary nulls (P2-T4) ──────────────────────────────────

def test_salary_null_variants_in_f2_profile(result_500k):
    """
    P2-T4: File B has ~9,897 empty Salary cells.
    The column profile must reflect total_null_variants > 0 and null_variant_rate > 0.
    Note: 9,897 / 500,000 ≈ 2% — below the 50% warning threshold, so 'High Null Rate'
    does not fire.  The null count is documented in the profile regardless.
    """
    salary = next(
        (c for c in result_500k.validation_f2.profile.columns if c.name == "Salary"),
        None,
    )
    assert salary is not None, "Salary column not found in file B profile"
    assert salary.total_null_variants > 0
    assert salary.null_variant_rate > 0.0


def test_f1_salary_no_nulls(result_500k):
    """File A Salary has no null entries → profile must confirm zero variants."""
    salary = next(
        (c for c in result_500k.validation_f1.profile.columns if c.name == "Salary"),
        None,
    )
    assert salary is not None
    assert salary.total_null_variants == 0
