import polars as pl
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import threading

from utils import Progress, check_cancel


@dataclass
class KeyCandidate:
    columns: list[str]
    uniqueness_ratio: float      # distinct_count / total_rows
    is_unique: bool              # True if ratio >= 0.99
    null_rate: float             # fraction of rows with any null in key cols
    confidence: float            # composite score 0-1
    recommended: bool            # True for top-ranked candidate


def discover_keys(
    lf: pl.LazyFrame,
    max_composite: int = 3,
    sample_rows: int = 100_000,
    progress: Optional[Progress] = None,
    cancel_token: Optional[threading.Event] = None,
) -> list[KeyCandidate]:
    """
    Auto-discover unique key columns from a LazyFrame.

    Algorithm:
    1. Collect sample (up to sample_rows).
    2. Test every single column for uniqueness via n_unique().
    3. If no unique single column, test two-column composites from top candidates.
    4. Return candidates sorted by (is_unique DESC, confidence DESC).
    """
    check_cancel(cancel_token)

    df = lf.head(sample_rows).collect()
    total_rows = df.height

    if total_rows == 0:
        return []

    candidates: list[KeyCandidate] = []
    columns = df.columns

    # --- Single-column pass ---
    if progress:
        progress.update("Key Discovery", "Testing single columns", 0, len(columns))

    for i, col in enumerate(columns):
        check_cancel(cancel_token)

        n_unique = df[col].n_unique()
        null_count = int(df[col].is_null().sum())

        uniqueness_ratio = n_unique / total_rows
        null_rate = null_count / total_rows
        is_unique = uniqueness_ratio >= 0.99

        # Confidence: penalise for nulls and non-uniqueness
        confidence = uniqueness_ratio * (1.0 - null_rate)

        candidates.append(KeyCandidate(
            columns=[col],
            uniqueness_ratio=uniqueness_ratio,
            is_unique=is_unique,
            null_rate=null_rate,
            confidence=confidence,
            recommended=False,
        ))

        if progress:
            progress.update("Key Discovery", f"Column {i + 1}/{len(columns)}: {col}", i + 1, len(columns))

    # --- Two-column composites (only when no unique single col) ---
    if not any(c.is_unique for c in candidates) and len(columns) > 1:
        if progress:
            progress.update("Key Discovery", "Testing two-column composites", 0, 1)

        top_cols = sorted(candidates, key=lambda x: x.uniqueness_ratio, reverse=True)[:5]
        combo_count = 0

        for i in range(len(top_cols)):
            for j in range(i + 1, len(top_cols)):
                if combo_count >= max_composite:
                    break
                check_cancel(cancel_token)

                col_pair = [top_cols[i].columns[0], top_cols[j].columns[0]]
                n_unique = df.select(col_pair).unique().height
                is_unique = n_unique == total_rows

                candidates.append(KeyCandidate(
                    columns=col_pair,
                    uniqueness_ratio=n_unique / total_rows,
                    is_unique=is_unique,
                    null_rate=0.0,
                    confidence=0.85 if is_unique else (n_unique / total_rows * 0.85),
                    recommended=False,
                ))
                combo_count += 1

    # Sort and mark recommended
    candidates.sort(key=lambda x: (-int(x.is_unique), -x.confidence))
    if candidates:
        candidates[0].recommended = True

    return candidates


def validate_key(
    lf: pl.LazyFrame,
    key_columns: list[str],
) -> tuple[bool, int]:
    """
    Check that key_columns form a unique key over the full LazyFrame.
    Returns (is_unique, duplicate_count).
    Full-file scan — do not call on a sampled frame.
    """
    df = lf.select(key_columns).collect()
    total = df.height
    unique = df.unique().height
    duplicates = total - unique
    return duplicates == 0, duplicates


def check_key_nulls(
    lf: pl.LazyFrame,
    key_columns: list[str],
) -> int:
    """
    Return the count of rows where ANY key column is null.
    Full-file scan. Null key rows are unmatched in the join and appear as
    spurious added/removed rows — they must be surfaced as a warning.
    """
    if not key_columns:
        return 0
    null_expr = pl.any_horizontal([pl.col(c).is_null() for c in key_columns])
    return int(lf.select(null_expr.sum().alias("n")).collect().item())


if __name__ == "__main__":
    test_df = pl.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Charlie", "David", "Eve"],
        "category": ["A", "B", "A", "B", "A"],
    })
    lf = test_df.lazy()

    candidates = discover_keys(lf)
    assert any(c.columns == ["id"] and c.is_unique for c in candidates), "Should find 'id' as unique key"
    assert candidates[0].recommended, "Top candidate should be marked recommended"

    is_unique, dups = validate_key(lf, ["id"])
    assert is_unique and dups == 0

    is_unique2, dups2 = validate_key(lf, ["category"])
    assert not is_unique2 and dups2 > 0

    print("✓ Key discovery tests passed")
