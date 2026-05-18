import polars as pl
from dataclasses import dataclass, field
from typing import Optional, Literal
import time
import threading

from metadata import FileMetadata
from utils import Progress, check_cancel


@dataclass
class IgnoreRules:
    case: bool = False
    whitespace: bool = False
    date_format: bool = False
    null_vs_blank: bool = False


@dataclass
class ColumnDiffStats:
    name: str
    modified_count: int
    formatting_only_count: int
    null_introduced_count: int
    null_resolved_count: int
    change_rate: float


@dataclass
class RowDiff:
    key_value: str
    change_type: Literal["added", "removed", "modified", "formatting_only"]
    columns_changed: list[str]
    f1_values: dict[str, str]
    f2_values: dict[str, str]
    severity_score: float


@dataclass
class DiffResult:
    added_rows: int
    removed_rows: int
    modified_rows: int
    formatting_only_rows: int
    total_rows_f1: int
    total_rows_f2: int
    confidence_score: float
    key_columns: list[str]
    column_diffs: dict[str, ColumnDiffStats]
    sample_diffs: list[RowDiff] = field(default_factory=list)
    export_path: Optional[str] = None
    # P1-T1/P1-T5: explicit scope indicators so consumers know counts are trustworthy
    is_full_count: bool = False
    rows_scanned: int = 0


def diff_files(
    lf1: pl.LazyFrame,
    m1: FileMetadata,
    lf2: pl.LazyFrame,
    m2: FileMetadata,
    key_columns: list[str],
    ignore_rules: Optional[IgnoreRules] = None,
    progress: Optional[Progress] = None,
    cancel_token: Optional[threading.Event] = None,
    column_map: Optional[list[dict]] = None,
    compare_columns: Optional[list[str]] = None,
) -> DiffResult:
    """
    Vectorized row-level diff via full-outer join on key columns.

    Flow:
    1. Apply column_map: rename f2 columns into f1 name-space (if supplied).
    2. Apply ignore rules to both LazyFrames (semantic frames).
    3. Cast both to Utf8 for raw comparison (raw frames).
    4. P1-T2: Add sentinel columns (_in_f1, _in_f2) before join for unambiguous
       row-origin tracking. _in_f1 IS NULL after join → row only in f2 (added).
       _in_f2 IS NULL after join → row only in f1 (removed).
    5. Full-outer join on key columns for both semantic and raw frames.
    6. P1-T1: Classify all rows using Polars when/then/otherwise; aggregate
       full-file counts via group_by. No Python loop for counting.
    7. Collect head(1000) for sample_diffs display only (labeled as sample).
    """
    start = time.time()
    check_cancel(cancel_token)

    if ignore_rules is None:
        ignore_rules = IgnoreRules()

    # Apply column_map: rename lf2 columns into f1 name-space before any join logic.
    m2_cols = list(m2.columns)
    if column_map:
        rename_map = {
            item["f2"]: item["f1"]
            for item in column_map
            if item.get("f2") and item.get("f1") and item["f2"] != item["f1"]
        }
        actual_renames = {k: v for k, v in rename_map.items() if k in m2_cols}
        if actual_renames:
            lf2 = lf2.rename(actual_renames)
            m2_cols = [actual_renames.get(c, c) for c in m2_cols]

    # Columns shared between both files (excluding key cols)
    shared_cols = [c for c in m1.columns if c in set(m2_cols) and c not in key_columns]
    # Win-2: user-selected subset — filter after column_map rename so f1 names are used
    if compare_columns:
        _cmp_set = set(compare_columns)
        shared_cols = [c for c in shared_cols if c in _cmp_set]

    if progress:
        progress.update("Diff", "Building combined frame", 0, 4)

    # P3-T2: Build one combined LazyFrame per file carrying BOTH semantic and raw columns.
    # Column naming: {c}_s1/{c}_s2 = semantic (post-rules), {c}_r1/{c}_r2 = raw (Utf8 cast).
    # One full-outer join replaces the two separate sem_joined + raw_joined scans (KI-017 fix).
    def _sem_col_expr(c: str, rules: IgnoreRules) -> pl.Expr:
        e = pl.col(c)
        if rules.case or rules.whitespace:
            e = e.cast(pl.Utf8)
        if rules.case:
            e = e.str.to_lowercase()
        if rules.whitespace:
            e = e.str.strip_chars()
        return e

    f1_select: list[pl.Expr] = (
        [pl.col(k) for k in key_columns]
        + [pl.lit(1).alias("_in_f1")]
        + [_sem_col_expr(c, ignore_rules).alias(f"{c}_s1") for c in shared_cols]
        + [pl.col(c).cast(pl.Utf8).alias(f"{c}_r1") for c in shared_cols]
    )
    f2_select: list[pl.Expr] = (
        [pl.col(k) for k in key_columns]
        + [pl.lit(1).alias("_in_f2")]
        + [_sem_col_expr(c, ignore_rules).alias(f"{c}_s2") for c in shared_cols]
        + [pl.col(c).cast(pl.Utf8).alias(f"{c}_r2") for c in shared_cols]
    )

    lf1 = lf1.sort(key_columns)
    lf2 = lf2.sort(key_columns)
    combined_lf1 = lf1.select(f1_select)
    combined_lf2 = lf2.select(f2_select)

    check_cancel(cancel_token)

    if progress:
        progress.update("Diff", "Joining files (single pass)", 1, 4)

    # Single full-outer join carries both semantic and raw columns simultaneously.
    combined_joined = combined_lf1.join(combined_lf2, on=key_columns, how="full", coalesce=True)

    check_cancel(cancel_token)

    if progress:
        progress.update("Diff", "Computing full-file counts", 2, 4)

    # P3-T2: Single collect on combined_joined for all global counts + per-column stats.
    # Classifies all 5 row types (added/removed/modified/formatting_only/unchanged) in one pass.
    # Two separate joins (sem_joined + raw_joined) are replaced by this one expression plan.
    try:
        both_present = pl.col("_in_f1").is_not_null() & pl.col("_in_f2").is_not_null()

        if shared_cols:
            any_sem_diff = pl.any_horizontal([
                (pl.col(f"{c}_s1") != pl.col(f"{c}_s2")) |
                (pl.col(f"{c}_s1").is_null() != pl.col(f"{c}_s2").is_null())
                for c in shared_cols
            ])
            any_raw_diff = pl.any_horizontal([
                (pl.col(f"{c}_r1") != pl.col(f"{c}_r2")) |
                (pl.col(f"{c}_r1").is_null() != pl.col(f"{c}_r2").is_null())
                for c in shared_cols
            ])
        else:
            any_sem_diff = pl.lit(False)
            any_raw_diff = pl.lit(False)

        # Full classification per COMPARE_ENGINE_RULES.md §2:
        #   added / removed (sentinel) → modified (sem diff) → formatting_only (raw diff, no sem diff) → unchanged
        change_type_expr = (
            pl.when(pl.col("_in_f1").is_null()).then(pl.lit("added"))
            .when(pl.col("_in_f2").is_null()).then(pl.lit("removed"))
            .when(any_sem_diff).then(pl.lit("modified"))
            .when(any_raw_diff).then(pl.lit("formatting_only"))
            .otherwise(pl.lit("unchanged"))
            .alias("_change_type")
        )

        # Aggregate global counts + exact per-column sem/raw diff counts in one collect.
        agg_exprs: list = [
            pl.len().alias("__total"),
            pl.col("_change_type").eq("added").sum().alias("__added"),
            pl.col("_change_type").eq("removed").sum().alias("__removed"),
            pl.col("_change_type").eq("modified").sum().alias("__modified"),
            pl.col("_change_type").eq("formatting_only").sum().alias("__fmt_only"),
        ]
        for c in shared_cols:
            col_sem_diff_expr = (
                (pl.col(f"{c}_s1") != pl.col(f"{c}_s2")) |
                (pl.col(f"{c}_s1").is_null() != pl.col(f"{c}_s2").is_null())
            ) & both_present
            col_raw_diff_expr = (
                (pl.col(f"{c}_r1") != pl.col(f"{c}_r2")) |
                (pl.col(f"{c}_r1").is_null() != pl.col(f"{c}_r2").is_null())
            ) & both_present
            agg_exprs.extend([
                col_sem_diff_expr.sum().alias(f"{c}__sem"),
                col_raw_diff_expr.sum().alias(f"{c}__raw"),
                (pl.col(f"{c}_s1").is_null() & pl.col(f"{c}_s2").is_not_null() & both_present)
                    .sum().alias(f"{c}__null_res"),
                (pl.col(f"{c}_s1").is_not_null() & pl.col(f"{c}_s2").is_null() & both_present)
                    .sum().alias(f"{c}__null_in"),
            ])

        stats = (
            combined_joined
            .with_columns(change_type_expr)
            .select(agg_exprs)
            .collect()
        )

        added = int(stats["__added"][0])
        removed = int(stats["__removed"][0])
        modified = int(stats["__modified"][0])
        fmt_only = int(stats["__fmt_only"][0])
        rows_scanned = int(stats["__total"][0])

        col_modified: dict[str, int] = {c: int(stats[f"{c}__sem"][0]) for c in shared_cols}
        col_null_res: dict[str, int] = {c: int(stats[f"{c}__null_res"][0]) for c in shared_cols}
        col_null_in: dict[str, int] = {c: int(stats[f"{c}__null_in"][0]) for c in shared_cols}
        # col_fmt[c] = raw_diff[c] − sem_diff[c]: exact because sem diff ⊆ raw diff
        col_fmt: dict[str, int] = {
            c: max(0, int(stats[f"{c}__raw"][0]) - col_modified.get(c, 0))
            for c in shared_cols
        }

        is_full_count = True

    except Exception:
        # Graceful degradation — e.g., duplicate key causes Cartesian product / OOM
        return DiffResult(
            added_rows=0, removed_rows=0, modified_rows=0, formatting_only_rows=0,
            total_rows_f1=0, total_rows_f2=0, confidence_score=0.0,
            key_columns=key_columns, column_diffs={},
            sample_diffs=[], is_full_count=False, rows_scanned=0,
        )

    check_cancel(cancel_token)

    if progress:
        progress.update("Diff", "Building sample diffs", 3, 4)

    # --- Collect display sample (head 1000 from combined join; ≤200 RowDiff objects) ---
    # These rows are for UI display only. All counts come from the full-file pass above.
    try:
        combined_sample = combined_joined.head(1000).collect().to_dicts()
    except Exception:
        combined_sample = []

    check_cancel(cancel_token)

    def _key_str(row: dict, keys: list[str]) -> str:
        return "|".join(str(row.get(k, "")) for k in keys)

    # col_modified, col_fmt, col_null_in, col_null_res are set by the Polars full-file pass.
    # The Python loop below builds sample_diffs only (no count accumulation).
    sample_diffs: list[RowDiff] = []

    for row in combined_sample:
        key_str = _key_str(row, key_columns)

        # Sentinel columns determine row origin (P1-T2 rule preserved in combined frame).
        is_added = row.get("_in_f1") is None
        is_removed = row.get("_in_f2") is None

        if is_removed:
            change_type: Literal["added", "removed", "modified", "formatting_only"] = "removed"
            changed_sem: list[str] = []
            f1_vals: dict[str, str] = {}
            f2_vals: dict[str, str] = {}
        elif is_added:
            change_type = "added"
            changed_sem = []
            f1_vals = {}
            f2_vals = {}
        else:
            changed_sem = []
            changed_raw: list[str] = []
            f1_vals = {}
            f2_vals = {}

            for c in shared_cols:
                v1_sem = row.get(f"{c}_s1")
                v2_sem = row.get(f"{c}_s2")
                v1_raw = row.get(f"{c}_r1")
                v2_raw = row.get(f"{c}_r2")

                sem_diff = (v1_sem != v2_sem) and not (v1_sem is None and v2_sem is None)
                raw_diff = (v1_raw != v2_raw) and not (v1_raw is None and v2_raw is None)

                if sem_diff:
                    changed_sem.append(c)
                elif raw_diff:
                    changed_raw.append(c)

                f1_vals[c] = str(v1_raw) if v1_raw is not None else ""
                f2_vals[c] = str(v2_raw) if v2_raw is not None else ""

            if changed_sem:
                change_type = "modified"
            elif changed_raw:
                change_type = "formatting_only"
            else:
                continue  # unchanged row — not included in sample_diffs

        if len(sample_diffs) < 200:
            sample_diffs.append(RowDiff(
                key_value=key_str,
                change_type=change_type,
                columns_changed=changed_sem if change_type in ("modified", "formatting_only") else [],
                f1_values=f1_vals if change_type in ("modified", "formatting_only") else {},
                f2_values=f2_vals if change_type in ("modified", "formatting_only") else {},
                severity_score=1.0 if change_type in ("removed", "added") else 0.5,
            ))

    # Total row counts (independent scan — does not re-run the join)
    try:
        total_f1 = lf1.select(pl.len()).collect().item()
        total_f2 = lf2.select(pl.len()).collect().item()
    except Exception:
        total_f1 = total_f2 = 0

    confidence = 0.95 if (added + removed) == 0 else 0.80

    column_diffs = {
        c: ColumnDiffStats(
            name=c,
            modified_count=col_modified.get(c, 0),
            formatting_only_count=col_fmt.get(c, 0),
            null_introduced_count=col_null_in.get(c, 0),
            null_resolved_count=col_null_res.get(c, 0),
            # D-007: denominator is full-file f1 row count, not sample size
            change_rate=(col_modified.get(c, 0) + col_fmt.get(c, 0)) / max(total_f1, 1),
        )
        for c in shared_cols
    }

    return DiffResult(
        added_rows=added,
        removed_rows=removed,
        modified_rows=modified,
        formatting_only_rows=fmt_only,
        total_rows_f1=total_f1,
        total_rows_f2=total_f2,
        confidence_score=confidence,
        key_columns=key_columns,
        column_diffs=column_diffs,
        sample_diffs=sample_diffs,
        is_full_count=is_full_count,
        rows_scanned=rows_scanned,
    )


def _apply_ignore_rules(lf: pl.LazyFrame, rules: IgnoreRules, columns: list[str]) -> pl.LazyFrame:
    """Return LazyFrame with ignore rules applied as Polars expressions."""
    exprs = []
    for col in columns:
        e = pl.col(col)
        if rules.case or rules.whitespace:
            e = e.cast(pl.Utf8)
        if rules.case:
            e = e.str.to_lowercase()
        if rules.whitespace:
            e = e.str.strip_chars()
        exprs.append(e.alias(col))
    return lf.select(exprs)


def _cast_to_str(lf: pl.LazyFrame, columns: list[str]) -> pl.LazyFrame:
    """Cast all columns to Utf8 for raw string comparison."""
    return lf.select([pl.col(c).cast(pl.Utf8).alias(c) for c in columns])


if __name__ == "__main__":
    df1 = pl.DataFrame({
        "id": [1, 2, 3],
        "name": ["Alice", "Bob", "Charlie"],
        "salary": ["50000", "60000", "70000"],
    })
    df2 = pl.DataFrame({
        "id": [1, 2, 4],
        "name": ["Alice", "BOB", "David"],
        "salary": ["50000", "60000", "75000"],
    })

    m1 = FileMetadata.__new__(FileMetadata)
    m1.columns = df1.columns
    m2 = FileMetadata.__new__(FileMetadata)
    m2.columns = df2.columns

    result = diff_files(
        df1.lazy(), m1,
        df2.lazy(), m2,
        key_columns=["id"],
        ignore_rules=IgnoreRules(case=True),
    )

    assert result.removed_rows == 1, f"Expected 1 removed, got {result.removed_rows}"
    assert result.added_rows == 1, f"Expected 1 added, got {result.added_rows}"
    assert result.is_full_count is True, "Expected is_full_count=True"
    assert result.rows_scanned == 4, f"Expected rows_scanned=4 (3 f1 + 1 added), got {result.rows_scanned}"
    # salary changed for id=4 vs id=3; with case-ignore, BOB==bob so name unchanged
    print(f"  added={result.added_rows} removed={result.removed_rows} "
          f"modified={result.modified_rows} fmt_only={result.formatting_only_rows} "
          f"is_full_count={result.is_full_count} rows_scanned={result.rows_scanned}")
    print("✓ Differ tests passed")
