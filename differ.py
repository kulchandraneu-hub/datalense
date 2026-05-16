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

    if progress:
        progress.update("Diff", "Applying ignore rules", 0, 4)

    # Build semantic (post-rules) frames
    lf1_sem = _apply_ignore_rules(lf1, ignore_rules, m1.columns)
    lf2_sem = _apply_ignore_rules(lf2, ignore_rules, m2_cols)

    # Build raw (pre-rules, cast to string for comparison) frames
    lf1_raw = _cast_to_str(lf1, m1.columns)
    lf2_raw = _cast_to_str(lf2, m2_cols)

    # P1-T2: Sentinel columns added before join to track row origin unambiguously.
    # After full-outer join: _in_f1 IS NULL → row absent from file 1 (added to f2).
    #                        _in_f2 IS NULL → row absent from file 2 (removed from f1).
    # Sentinels are NOT in m1/m2.columns so the rename step below leaves them intact.
    lf1_sem = lf1_sem.with_columns(pl.lit(1).alias("_in_f1"))
    lf2_sem = lf2_sem.with_columns(pl.lit(1).alias("_in_f2"))
    lf1_raw = lf1_raw.with_columns(pl.lit(1).alias("_in_f1"))
    lf2_raw = lf2_raw.with_columns(pl.lit(1).alias("_in_f2"))

    check_cancel(cancel_token)

    if progress:
        progress.update("Diff", "Joining files", 1, 4)

    # Rename non-key data columns to avoid collision after join.
    # Sentinel columns (_in_f1/_in_f2) are not in m1/m2.columns → not renamed.
    def _rename(lf: pl.LazyFrame, cols: list[str], suffix: str) -> pl.LazyFrame:
        return lf.rename({c: f"{c}{suffix}" for c in cols if c not in key_columns})

    lf1_sem_r = _rename(lf1_sem, m1.columns, "_f1")
    lf2_sem_r = _rename(lf2_sem, m2_cols, "_f2")
    lf1_raw_r = _rename(lf1_raw, m1.columns, "_raw1")
    lf2_raw_r = _rename(lf2_raw, m2_cols, "_raw2")

    # Semantic join (full outer)
    sem_joined = lf1_sem_r.join(lf2_sem_r, on=key_columns, how="full", coalesce=True)

    # Raw join (full outer) — same key structure
    raw_joined = lf1_raw_r.join(lf2_raw_r, on=key_columns, how="full", coalesce=True)

    check_cancel(cancel_token)

    if progress:
        progress.update("Diff", "Computing full-file counts", 2, 4)

    # P1-T1: Full-file counts via Polars expressions over the complete join result.
    try:
        # Detect any semantic difference across shared columns.
        # Uses is_null() mismatch to catch null-vs-value transitions correctly.
        if shared_cols:
            any_sem_diff = pl.any_horizontal([
                (pl.col(f"{c}_f1") != pl.col(f"{c}_f2")) |
                (pl.col(f"{c}_f1").is_null() != pl.col(f"{c}_f2").is_null())
                for c in shared_cols
            ])
        else:
            any_sem_diff = pl.lit(False)

        # Classify every join row using sentinel presence, then semantic diff.
        change_type_expr = (
            pl.when(pl.col("_in_f1").is_null()).then(pl.lit("added"))
            .when(pl.col("_in_f2").is_null()).then(pl.lit("removed"))
            .when(any_sem_diff).then(pl.lit("modified"))
            .otherwise(pl.lit("same_or_fmt"))
            .alias("_change_type")
        )

        counts_df = (
            sem_joined
            .with_columns(change_type_expr)
            .group_by("_change_type")
            .agg(pl.len().alias("n"))
            .collect()
        )

        counts = dict(zip(counts_df["_change_type"].to_list(), counts_df["n"].to_list()))
        added = counts.get("added", 0)
        removed = counts.get("removed", 0)
        modified = counts.get("modified", 0)
        rows_scanned = sum(counts.values())

        # formatting_only: both files have the row, raw values differ, semantic values don't.
        # = (rows with any raw diff, both present) - (rows with any semantic diff, both present)
        if shared_cols:
            any_raw_diff = pl.any_horizontal([
                (pl.col(f"{c}_raw1") != pl.col(f"{c}_raw2")) |
                (pl.col(f"{c}_raw1").is_null() != pl.col(f"{c}_raw2").is_null())
                for c in shared_cols
            ])
            raw_both_diff = (
                raw_joined
                .filter(pl.col("_in_f1").is_not_null() & pl.col("_in_f2").is_not_null())
                .filter(any_raw_diff)
                .select(pl.len())
                .collect()
                .item()
            )
            fmt_only = max(0, raw_both_diff - modified)
        else:
            fmt_only = 0

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

    # --- Collect display sample (head 1000 from join; at most 200 RowDiff objects) ---
    # These rows are for UI display only. All counts above come from the full-file pass.
    try:
        sem_sample = sem_joined.head(1000).collect().to_dicts()
        raw_sample = raw_joined.head(1000).collect().to_dicts()
    except Exception:
        sem_sample = []
        raw_sample = []

    check_cancel(cancel_token)

    # Build a lookup from key → raw row for the sample
    def _key_str(row: dict, keys: list[str]) -> str:
        return "|".join(str(row.get(k, "")) for k in keys)

    raw_by_key = {_key_str(r, key_columns): r for r in raw_sample}

    # Per-column stats are collected from the sample (Phase 3 will extend to full file).
    col_modified: dict[str, int] = {c: 0 for c in shared_cols}
    col_fmt: dict[str, int] = {c: 0 for c in shared_cols}
    col_null_in: dict[str, int] = {c: 0 for c in shared_cols}
    col_null_res: dict[str, int] = {c: 0 for c in shared_cols}
    sample_diffs: list[RowDiff] = []

    for sem_row in sem_sample:
        key_str = _key_str(sem_row, key_columns)
        raw_row = raw_by_key.get(key_str, {})

        # P1-T2: Use sentinel columns — not value heuristics — to detect row origin.
        is_added = sem_row.get("_in_f1") is None
        is_removed = sem_row.get("_in_f2") is None

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
                v1_sem = sem_row.get(f"{c}_f1")
                v2_sem = sem_row.get(f"{c}_f2")
                v1_raw = raw_row.get(f"{c}_raw1")
                v2_raw = raw_row.get(f"{c}_raw2")

                sem_diff = (v1_sem != v2_sem) and not (v1_sem is None and v2_sem is None)
                raw_diff = (v1_raw != v2_raw) and not (v1_raw is None and v2_raw is None)

                if sem_diff:
                    changed_sem.append(c)
                    col_modified[c] = col_modified.get(c, 0) + 1
                    if v1_sem is None and v2_sem is not None:
                        col_null_res[c] = col_null_res.get(c, 0) + 1
                    elif v1_sem is not None and v2_sem is None:
                        col_null_in[c] = col_null_in.get(c, 0) + 1
                elif raw_diff:
                    changed_raw.append(c)
                    col_fmt[c] = col_fmt.get(c, 0) + 1

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
