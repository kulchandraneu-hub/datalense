"""
Phase 1 benchmark runner.
Runs diff against benchmark files and prints counts with PASS/FAIL assertions.

Usage:
    python benchmark_p1.py            # 100k quick mode (default)
    python benchmark_p1.py --full     # 500k full milestone mode
    python benchmark_p1.py --all      # both in sequence

Benchmark files (do not modify):
    testing_input_files/benchmark_100k_file_{A,B}.csv
    testing_input_files/benchmark_500k_file_{A,B}.csv

Regression rule: if a previously-passing assertion flips to FAIL, a change
broke something. Fix the code, not the expected value, unless the spec changed.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from compare import CompareRequest, run_compare

BASE = Path(__file__).parent / "testing_input_files"

# ---------------------------------------------------------------------------
# 100k benchmark — fast regression and correctness checks (default)
# ---------------------------------------------------------------------------
FILE_A_100K = BASE / "benchmark_100k_file_A.csv"
FILE_B_100K = BASE / "benchmark_100k_file_B.csv"

# Ground-truth targets from benchmark_100k_summary.txt.
# Key: CustomerID (not EmployeeID — different domain from the 500k dataset).
#
# P1-T7 note: File B has 100 duplicate CustomerID rows. validate_key() will
# detect this, set key_degraded=True, and force is_full_count=False.
# This is CORRECT and expected behavior — is_full_count=False is the right
# answer when the key is non-unique (Cartesian product inflates counts).
#
# modified_rows target is the true semantic count (8,000). Without ignore rules
# the engine also classifies whitespace-only (3,000) and case-only (3,000) as
# semantic — so this will show FAIL until ignore rules are active. That gap is
# expected and intentional; it documents the remaining work.
EXPECTED_100K = {
    "added_rows":    1_000,
    "removed_rows":  2_000,
    "modified_rows": 8_000,   # true semantic target; FAIL expected until P1-T3 + ignore rules
    "total_rows_f1": 100_000,
    "is_full_count": False,   # P1-T7: File B has 100 duplicate keys → key_degraded → False is correct
}

# ---------------------------------------------------------------------------
# 500k benchmark — milestone validation and architecture verification
# ---------------------------------------------------------------------------
FILE_A_500K = BASE / "benchmark_500k_file_A.csv"
FILE_B_500K = BASE / "benchmark_500k_file_B.csv"

# modified_rows is currently ~45,563 due to KI-016 (JoinDate cross-type comparison).
# It will show FAIL until P1-T8 (raise infer_schema_length) closes the gap.
EXPECTED_500K = {
    "added_rows":    5_000,
    "removed_rows":  5_000,
    "modified_rows": 50_000,  # FAIL expected until P1-T8
    "total_rows_f1": 500_000,
    "is_full_count": True,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_benchmark(label: str, file_a: Path, file_b: Path, expected: dict, key_columns: list) -> dict:
    print(f"\n{'='*62}")
    print(f"  {label}")
    print(f"{'='*62}")

    if not file_a.exists():
        print(f"  ERROR: benchmark file not found: {file_a}")
        return {}
    if not file_b.exists():
        print(f"  ERROR: benchmark file not found: {file_b}")
        return {}

    request = CompareRequest(
        file1=file_a,
        file2=file_b,
        key_columns=key_columns,
        # No ignore rules: keeps native column types so comparisons are
        # numeric (not string), which avoids confounding KI-011.
    )

    t0 = time.time()
    result = run_compare(request)
    elapsed = time.time() - t0

    d = result.diff
    actual = {
        "added_rows":       d.added_rows,
        "removed_rows":     d.removed_rows,
        "modified_rows":    d.modified_rows,
        "formatting_only":  d.formatting_only_rows,
        "total_rows_f1":    d.total_rows_f1,
        "total_rows_f2":    d.total_rows_f2,
        "is_full_count":    getattr(d, "is_full_count", "FIELD_MISSING"),
        "rows_scanned":     getattr(d, "rows_scanned", "FIELD_MISSING"),
        "elapsed_s":        round(elapsed, 1),
    }

    rows = [
        ("added_rows",    "added_rows",    True),
        ("removed_rows",  "removed_rows",  True),
        ("modified_rows", "modified_rows", True),
        ("formatting_only_rows", "formatting_only", False),
        ("total_rows_f1", "total_rows_f1", True),
        ("total_rows_f2", "total_rows_f2", False),
        ("is_full_count", "is_full_count", True),
        ("rows_scanned",  "rows_scanned",  False),
    ]

    for label_key, actual_key, has_expected in rows:
        val = actual[actual_key]
        exp = expected.get(label_key)
        if has_expected and exp is not None:
            status = "OK  " if val == exp else "FAIL"
            print(f"  {label_key:<22} = {str(val):>10}   expected: {str(exp):>10}  {status}")
        else:
            print(f"  {label_key:<22} = {str(val):>10}")

    print(f"  {'elapsed':<22} = {actual['elapsed_s']:>10}s")

    failures = [k for k, exp in expected.items()
                if actual.get(k) is not None and actual.get(k) != exp]

    print()
    if failures:
        print(f"  ASSERTIONS FAILED ({len(failures)}): {', '.join(failures)}")
    else:
        print(f"  ALL ASSERTIONS PASSED")

    return actual


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--quick"

    if mode == "--full":
        run_benchmark("500k Full — milestone validation", FILE_A_500K, FILE_B_500K, EXPECTED_500K, ["EmployeeID"])
    elif mode == "--all":
        run_benchmark("100k Quick — regression check", FILE_A_100K, FILE_B_100K, EXPECTED_100K, ["CustomerID"])
        run_benchmark("500k Full — milestone validation", FILE_A_500K, FILE_B_500K, EXPECTED_500K, ["EmployeeID"])
    else:  # --quick or default
        run_benchmark("100k Quick — regression check", FILE_A_100K, FILE_B_100K, EXPECTED_100K, ["CustomerID"])
