"""
Phase 1 benchmark runner.
Runs diff against the 500k benchmark files and prints counts.
Usage: python benchmark_p1.py
"""
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from compare import CompareRequest, run_compare
from differ import IgnoreRules

FILE_A = Path(__file__).parent / "testing_input_files" / "benchmark_500k_file_A.csv"
FILE_B = Path(__file__).parent / "testing_input_files" / "benchmark_500k_file_B.csv"

EXPECTED = {
    "added_rows": 5_000,
    "removed_rows": 5_000,
    "modified_rows": 50_000,
    "total_rows_f1": 500_000,
    "is_full_count": True,
}


def run_benchmark(label: str) -> dict:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    request = CompareRequest(
        file1=FILE_A,
        file2=FILE_B,
        key_columns=["EmployeeID"],
        # No ignore rules: preserves native column types so Int64 vs Float64
        # salary comparisons are numeric (not string), avoiding KI-011 confounding.
    )

    t0 = time.time()
    result = run_compare(request)
    elapsed = time.time() - t0

    d = result.diff
    actual = {
        "added_rows": d.added_rows,
        "removed_rows": d.removed_rows,
        "modified_rows": d.modified_rows,
        "formatting_only_rows": d.formatting_only_rows,
        "total_rows_f1": d.total_rows_f1,
        "total_rows_f2": d.total_rows_f2,
        "is_full_count": getattr(d, "is_full_count", "FIELD_MISSING"),
        "rows_scanned": getattr(d, "rows_scanned", "FIELD_MISSING"),
        "elapsed_s": round(elapsed, 1),
    }

    print(f"  added_rows        = {actual['added_rows']:>10}   expected: {EXPECTED['added_rows']:>10}  {'OK' if actual['added_rows'] == EXPECTED['added_rows'] else 'FAIL'}")
    print(f"  removed_rows      = {actual['removed_rows']:>10}   expected: {EXPECTED['removed_rows']:>10}  {'OK' if actual['removed_rows'] == EXPECTED['removed_rows'] else 'FAIL'}")
    print(f"  modified_rows     = {actual['modified_rows']:>10}   expected: {EXPECTED['modified_rows']:>10}  {'OK' if actual['modified_rows'] == EXPECTED['modified_rows'] else 'FAIL'}")
    print(f"  formatting_only   = {actual['formatting_only_rows']:>10}")
    print(f"  total_rows_f1     = {actual['total_rows_f1']:>10}   expected: {EXPECTED['total_rows_f1']:>10}  {'OK' if actual['total_rows_f1'] == EXPECTED['total_rows_f1'] else 'FAIL'}")
    print(f"  total_rows_f2     = {actual['total_rows_f2']:>10}")
    print(f"  is_full_count     = {str(actual['is_full_count']):>10}   expected:       True  {'OK' if actual['is_full_count'] == True else 'FAIL'}")
    print(f"  rows_scanned      = {str(actual['rows_scanned']):>10}")
    print(f"  elapsed           = {actual['elapsed_s']:>10}s")

    failures = []
    for k, exp in EXPECTED.items():
        got = actual.get(k)
        if got != exp:
            failures.append(f"  FAIL: {k} = {got!r}, expected {exp!r}")

    if failures:
        print(f"\n  ASSERTIONS FAILED ({len(failures)}):")
        for f in failures:
            print(f)
    else:
        print(f"\n  ALL ASSERTIONS PASSED")

    return actual


if __name__ == "__main__":
    run_benchmark("Benchmark run")
