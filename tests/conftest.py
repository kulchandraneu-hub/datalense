"""
Shared pytest fixtures for the DataLens regression suite.

Fixture scoping:
  session — quick tests reuse a single compare result for all modules in the run
  module  — expensive 100k/500k results are scoped to their own test module

All fixtures invoke run_compare() directly; no mocking of engine logic.
"""
import sys
import pytest
from pathlib import Path

# Add project root to sys.path so imports work regardless of how pytest is invoked
sys.path.insert(0, str(Path(__file__).parent.parent))

from compare import CompareRequest, run_compare  # noqa: E402
from differ import IgnoreRules  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
DEMO = Path(__file__).parent.parent / "testing_input_files"


def _run(file_a: Path, file_b: Path, key_cols: list, ignore_rules=None):
    return run_compare(CompareRequest(
        file1=file_a,
        file2=file_b,
        key_columns=key_cols,
        ignore_rules=ignore_rules,
    ))


@pytest.fixture(scope="session")
def demo_result():
    """
    demo_small files — 10-row/11-row real-world mix.

    File A: 10 rows (EmployeeID 1001-1010).
    File B: 11 rows — 1009 removed, 2001 added, 1008 duplicated (key_degraded),
            multiple Salary format changes (Int→Float), JoinDate string mismatch,
            email change, whitespace-in-name, dept case change, null Salary.

    Expected diff (no ignore rules):
      added=1, removed=1, modified=6, formatting_only=4, is_full_count=False
    """
    return _run(
        DEMO / "demo_small_file_A.csv",
        DEMO / "demo_small_file_B.csv",
        ["EmployeeID"],
    )


@pytest.fixture(scope="session")
def clean_result():
    """
    5-row synthetic fixture without date columns or type drift.
    Isolates classification logic with exact, deterministic counts.

    Changes in B vs A:
      ID=1: Value 100→150 (semantic)
      ID=2: Category B→b (case-only, semantic without ignore_case)
      ID=5: removed
      ID=6: added

    Expected: added=1, removed=1, modified=2, formatting_only=0, is_full_count=True
    """
    return _run(FIXTURES / "clean_A.csv", FIXTURES / "clean_B.csv", ["ID"])


@pytest.fixture(scope="session")
def clean_result_ignore_case():
    """
    Same synthetic files with ignore_case=True.
    ID=2 (Category B→b) must reclassify from modified → formatting_only.

    Expected: added=1, removed=1, modified=1, formatting_only=1, is_full_count=True
    """
    return _run(
        FIXTURES / "clean_A.csv",
        FIXTURES / "clean_B.csv",
        ["ID"],
        IgnoreRules(case=True),
    )


@pytest.fixture(scope="session")
def null_sentinel_result():
    """
    P2-T5 fixture: file A row 3 has null non-key value (Score); row 3 absent from B.
    Must classify as REMOVED, not modified.  Verifies sentinel-based detection.

    Expected: removed=1, added=0, modified=0, is_full_count=True
    """
    return _run(
        FIXTURES / "null_nonkey_A.csv",
        FIXTURES / "null_nonkey_B.csv",
        ["ID"],
    )


@pytest.fixture(scope="session")
def null_intro_result():
    """
    Row 3 present in both files; Score transitions 999 → null (null introduced).
    Must classify as MODIFIED; Score column must show null_introduced_count >= 1.

    Expected: removed=0, added=0, modified=1, is_full_count=True
    """
    return _run(
        FIXTURES / "null_intro_A.csv",
        FIXTURES / "null_intro_B.csv",
        ["ID"],
    )


@pytest.fixture(scope="session")
def dup_key_result():
    """
    File B has a duplicate key (ID=3 appears twice) → key_degraded → is_full_count=False.
    """
    return _run(FIXTURES / "clean_A.csv", FIXTURES / "dup_key_B.csv", ["ID"])
