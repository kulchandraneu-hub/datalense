import polars as pl
from dataclasses import dataclass, field
from typing import Optional
import threading
import time

from metadata import FileMetadata
from utils import Progress, check_cancel


TEXTUAL_NULLS = frozenset({
    "null", "none", "na", "n/a", "#n/a", "nan", "nil",
    "missing", "unknown", "undefined", "n.a.", "n.a", "-",
})


@dataclass
class ColumnProfile:
    name: str
    inferred_type: str                   # "integer", "float", "date", "datetime", "boolean", "string"
    polars_null_count: int
    empty_string_count: int
    whitespace_only_count: int
    textual_null_count: int
    total_null_variants: int
    null_variant_rate: float
    distinct_count: int
    total_count: int
    type_distribution: dict[str, float]  # e.g. {"integer": 0.95, "string": 0.05}
    invalid_parse_count: int
    min_value: Optional[str]
    max_value: Optional[str]
    sample_values: list[str]             # up to 5 non-null examples


@dataclass
class FileProfile:
    metadata: FileMetadata
    columns: list[ColumnProfile]
    profiling_duration_s: float
    total_count: int
    column_count: int
    memory_peak_mb: float = 0.0


def profile_file(
    lf: pl.LazyFrame,
    metadata: FileMetadata,
    progress: Optional[Progress] = None,
    cancel_token: Optional[threading.Event] = None,
) -> FileProfile:
    """
    P3-T3: Profile all columns in ONE batched .select().collect(streaming=True) call.
    All null variant counts, type-parse counts, distinct, min, max are computed in a
    single expression plan. sample_values are extracted from one lf.head(100) scan.
    profile_column() is preserved unchanged for external / standalone callers.
    """
    start = time.time()
    check_cancel(cancel_token)

    cols = metadata.columns
    row_count = metadata.row_count
    total_cols = len(cols)

    # Schema lookup: needed for temporal detection and for the Boolean cast guard below.
    # Always assign schema so the expression-building loop can reference it safely.
    try:
        schema = lf.collect_schema()
    except Exception:
        schema = {}
    temporal_cols = frozenset(c for c in cols if schema.get(c) in (pl.Date, pl.Datetime))

    # Build one aggregation expression per statistic per column.
    # Suffixes: __pn=polars_null, __es=empty_str, __ws=whitespace, __tn=textual_null,
    #           __nd=n_unique, __min/__max, __date/__dt, __int/__flt/__bool (non-temporal only).
    all_exprs: list[pl.Expr] = []
    for c in cols:
        is_temporal = c in temporal_cols
        # Polars 1.x represents string columns as Utf8View internally.
        # str(dtype) returns "String" for pl.String/pl.Utf8 in Polars 1.x, "Utf8" in older.
        col_dtype_str = str(schema.get(c)) if schema.get(c) is not None else "String"
        utf8 = pl.col(c).cast(pl.Utf8)
        not_null = pl.col(c).is_not_null()
        all_exprs += [
            pl.col(c).is_null().sum().alias(f"{c}__pn"),
            (pl.when(not_null)
               .then(utf8.str.len_chars() == 0)
               .otherwise(False).sum()).alias(f"{c}__es"),
            (pl.when(not_null)
               .then((utf8.str.len_chars() > 0) &
                     (utf8.str.strip_chars().str.len_chars() == 0))
               .otherwise(False).sum()).alias(f"{c}__ws"),
            (pl.when(not_null)
               .then(utf8.str.to_lowercase().is_in(list(TEXTUAL_NULLS)))
               .otherwise(False).sum()).alias(f"{c}__tn"),
            pl.col(c).n_unique().alias(f"{c}__nd"),
            utf8.min().alias(f"{c}__min"),
            utf8.max().alias(f"{c}__max"),
            utf8.str.to_date(strict=False).is_not_null().sum().alias(f"{c}__date"),
            utf8.str.to_datetime(strict=False).is_not_null().sum().alias(f"{c}__dt"),
        ]
        # Skip int/float/bool casts for temporal cols — they give days-since-epoch values.
        if not is_temporal:
            all_exprs += [
                pl.col(c).cast(pl.Int64,   strict=False).is_not_null().sum().alias(f"{c}__int"),
                pl.col(c).cast(pl.Float64, strict=False).is_not_null().sum().alias(f"{c}__flt"),
            ]
            # Polars 1.x does not support cast from Utf8View (String) → Boolean.
            # For string columns bool_count defaults to 0 via row.get(..., 0), same as
            # the original _try_cast() exception path in profile_column().
            if col_dtype_str not in ("String", "Utf8", "Categorical", "Enum"):
                all_exprs.append(
                    pl.col(c).cast(pl.Boolean, strict=False).is_not_null().sum().alias(f"{c}__bool")
                )

    if progress:
        progress.update("Profiling", "Computing batch statistics", 0, total_cols)

    # Single full-file collect — streaming engine avoids materialising the entire frame.
    # engine="streaming" is the Polars 1.25+ API; fall back to default engine on failure.
    try:
        batch = lf.select(all_exprs).collect(engine="streaming")
    except Exception:
        batch = lf.select(all_exprs).collect()

    batch_row = batch.to_dicts()[0]

    # One lightweight head scan for sample values across all columns.
    try:
        head_df = lf.head(100).collect()
    except Exception:
        head_df = None

    check_cancel(cancel_token)

    # Build ColumnProfiles from pre-computed batch values — no further .collect() calls.
    columns: list[ColumnProfile] = []
    for i, c in enumerate(cols):
        check_cancel(cancel_token)

        pn   = int(batch_row.get(f"{c}__pn",   0) or 0)
        es   = int(batch_row.get(f"{c}__es",   0) or 0)
        ws   = int(batch_row.get(f"{c}__ws",   0) or 0)
        tn   = int(batch_row.get(f"{c}__tn",   0) or 0)
        nd   = int(batch_row.get(f"{c}__nd",   0) or 0)
        mn   = batch_row.get(f"{c}__min")
        mx   = batch_row.get(f"{c}__max")
        int_n  = int(batch_row.get(f"{c}__int",  0) or 0)
        flt_n  = int(batch_row.get(f"{c}__flt",  0) or 0)
        bool_n = int(batch_row.get(f"{c}__bool", 0) or 0)
        date_n = int(batch_row.get(f"{c}__date", 0) or 0)
        dt_n   = int(batch_row.get(f"{c}__dt",   0) or 0)

        non_null = row_count - pn
        inferred_type, type_dist, invalid_parse = _infer_type_from_counts(
            non_null, int_n, flt_n, bool_n, date_n, dt_n
        )

        total_null_variants = pn + es + ws + tn
        null_variant_rate = total_null_variants / row_count if row_count > 0 else 0.0

        # Sample values from single head scan (no per-column filter scan).
        sample_values: list[str] = []
        if head_df is not None and c in head_df.columns:
            for v in head_df[c].cast(pl.Utf8, strict=False).to_list():
                if v is not None and len(sample_values) < 5:
                    sample_values.append(str(v))

        columns.append(ColumnProfile(
            name=c,
            inferred_type=inferred_type,
            polars_null_count=pn,
            empty_string_count=es,
            whitespace_only_count=ws,
            textual_null_count=tn,
            total_null_variants=total_null_variants,
            null_variant_rate=null_variant_rate,
            distinct_count=nd,
            total_count=row_count,
            type_distribution=type_dist,
            invalid_parse_count=invalid_parse,
            min_value=mn,
            max_value=mx,
            sample_values=sample_values,
        ))

        if progress:
            progress.update("Profiling", f"Column {i + 1}/{total_cols}: {c}", i + 1, total_cols)

    return FileProfile(
        metadata=metadata,
        columns=columns,
        profiling_duration_s=time.time() - start,
        total_count=row_count,
        column_count=total_cols,
    )


def profile_column(
    lf: pl.LazyFrame,
    col: str,
    row_count: int,
) -> ColumnProfile:
    """
    Profile one column: all four null types collected in a single .select() pass.
    """
    # Pass 1: null variant counts + distinct
    null_stats = lf.select([
        pl.col(col).is_null().sum().alias("polars_null"),
        pl.when(pl.col(col).is_not_null())
          .then(pl.col(col).cast(pl.Utf8).str.len_chars() == 0)
          .otherwise(False).sum().alias("empty_string"),
        pl.when(pl.col(col).is_not_null())
          .then(
              (pl.col(col).cast(pl.Utf8).str.len_chars() > 0) &
              (pl.col(col).cast(pl.Utf8).str.strip_chars().str.len_chars() == 0)
          )
          .otherwise(False).sum().alias("whitespace_only"),
        pl.when(pl.col(col).is_not_null())
          .then(pl.col(col).cast(pl.Utf8).str.to_lowercase().is_in(list(TEXTUAL_NULLS)))
          .otherwise(False).sum().alias("textual_null"),
        pl.col(col).n_unique().alias("distinct_count"),
    ]).collect()

    nd = null_stats.to_dicts()[0]
    polars_null = int(nd["polars_null"])
    empty_str = int(nd["empty_string"])
    whitespace = int(nd["whitespace_only"])
    textual = int(nd["textual_null"])
    distinct = int(nd["distinct_count"])

    total_null_variants = polars_null + empty_str + whitespace + textual
    null_variant_rate = total_null_variants / row_count if row_count > 0 else 0.0

    # Type inference — pass non_null_count to avoid an extra .collect() inside _infer_type
    non_null = row_count - polars_null
    inferred_type, type_dist, invalid_parse = _infer_type(lf, col, row_count, non_null)

    # Pass 2: min / max / sample (cheap — polars aggregates lazily)
    try:
        stats = lf.select([
            pl.col(col).cast(pl.Utf8).min().alias("min_val"),
            pl.col(col).cast(pl.Utf8).max().alias("max_val"),
        ]).collect().to_dicts()[0]
        min_value = stats["min_val"]
        max_value = stats["max_val"]
    except Exception:
        min_value = None
        max_value = None

    try:
        sample_df = (
            lf.filter(pl.col(col).is_not_null())
              .select(pl.col(col).cast(pl.Utf8))
              .head(5)
              .collect()
        )
        sample_values = sample_df[col].to_list()
    except Exception:
        sample_values = []

    return ColumnProfile(
        name=col,
        inferred_type=inferred_type,
        polars_null_count=polars_null,
        empty_string_count=empty_str,
        whitespace_only_count=whitespace,
        textual_null_count=textual,
        total_null_variants=total_null_variants,
        null_variant_rate=null_variant_rate,
        distinct_count=distinct,
        total_count=row_count,
        type_distribution=type_dist,
        invalid_parse_count=invalid_parse,
        min_value=min_value,
        max_value=max_value,
        sample_values=[str(v) for v in sample_values if v is not None],
    )


def _infer_type_from_counts(
    non_null_count: int,
    int_count: int,
    float_count: int,
    bool_count: int,
    date_count: int,
    datetime_count: int,
) -> tuple[str, dict[str, float], int]:
    """
    Pure type-inference computation from pre-computed parse counts. No .collect() calls.
    int/float/bool counts must already be zeroed for temporal columns by the caller.

    Priority: Int64 > Float64 > Boolean > Date > Datetime > String.
    Dominant = highest-priority type where >= 95% of non-null rows parse successfully.
    If none qualify, uses the highest-coverage specific type so the Mixed Types
    validation check can fire (e.g. JoinDate with mixed ISO + US date formats).
    """
    if non_null_count == 0:
        return "string", {"string": 1.0}, 0

    specific = {
        "integer":  int_count,
        "float":    float_count,
        "boolean":  bool_count,
        "date":     date_count,
        "datetime": datetime_count,
    }

    THRESHOLD = 0.95
    priority = ["integer", "float", "boolean", "date", "datetime"]

    dominant = "string"
    dominant_count = non_null_count
    for t in priority:
        if specific[t] / non_null_count >= THRESHOLD:
            dominant = t
            dominant_count = specific[t]
            break

    if dominant == "string":
        best_t = max(priority, key=lambda k: specific[k])
        if specific[best_t] > 0:
            dominant = best_t
            dominant_count = specific[best_t]

    if dominant == "string":
        return "string", {"string": 1.0}, 0

    best_frac = dominant_count / non_null_count
    type_dist = {dominant: best_frac, "string": max(0.0, 1.0 - best_frac)}
    invalid_count = non_null_count - dominant_count
    return dominant, type_dist, invalid_count


def _infer_type(
    lf: pl.LazyFrame,
    col: str,
    row_count: int,
    non_null_count: int,
) -> tuple[str, dict[str, float], int]:
    """
    Priority-based type inference via individual .collect() calls.
    Signature unchanged — called by profile_column() for external / standalone use.
    profile_file() uses _infer_type_from_counts() directly with batch-collected counts.
    """
    if non_null_count == 0:
        return "string", {"string": 1.0}, 0

    def _try_cast(dtype) -> int:
        try:
            return int(
                lf.select(pl.col(col).cast(dtype, strict=False).is_not_null().sum()).collect().item()
            )
        except Exception:
            return 0

    # Columns already typed as Date/Datetime by Polars schema cast to Int64 as
    # days-since-epoch, which would falsely dominate as "integer". Skip those casts.
    try:
        col_dtype = lf.collect_schema()[col]
    except Exception:
        col_dtype = None
    is_temporal = col_dtype in (pl.Date, pl.Datetime)

    int_count   = 0 if is_temporal else _try_cast(pl.Int64)
    float_count = 0 if is_temporal else _try_cast(pl.Float64)
    bool_count  = 0 if is_temporal else _try_cast(pl.Boolean)

    try:
        date_count = int(
            lf.select(
                pl.col(col).cast(pl.Utf8).str.to_date(strict=False).is_not_null().sum()
            ).collect().item()
        )
    except Exception:
        date_count = 0

    try:
        datetime_count = int(
            lf.select(
                pl.col(col).cast(pl.Utf8).str.to_datetime(strict=False).is_not_null().sum()
            ).collect().item()
        )
    except Exception:
        datetime_count = 0

    return _infer_type_from_counts(
        non_null_count, int_count, float_count, bool_count, date_count, datetime_count
    )


if __name__ == "__main__":
    df = pl.DataFrame({
        "id": pl.Series([1, 2, None, 4, 5], dtype=pl.Int64),
        "name": ["Alice", "Bob", "", "David", "   "],
        "value": ["10.5", None, "20.5", "15.0", "N/A"],
    })
    lf = df.lazy()

    p_id = profile_column(lf, "id", 5)
    assert p_id.polars_null_count == 1, f"Expected 1 null, got {p_id.polars_null_count}"

    p_name = profile_column(lf, "name", 5)
    assert p_name.empty_string_count == 1, f"Expected 1 empty, got {p_name.empty_string_count}"
    assert p_name.whitespace_only_count == 1, f"Expected 1 whitespace, got {p_name.whitespace_only_count}"

    p_val = profile_column(lf, "value", 5)
    assert p_val.textual_null_count == 1, f"Expected 1 textual null, got {p_val.textual_null_count}"

    # P1-T3: mixed-type detection — ISO dates and US dates in same column
    df_mixed = pl.DataFrame({
        "date_col": ["2021-01-15", "2021-02-20", "03/15/2021", "2021-04-10", "05/20/2021"],
    })
    p_mixed = profile_column(df_mixed.lazy(), "date_col", 5)
    assert p_mixed.inferred_type == "date", \
        f"P1-T3: expected 'date', got '{p_mixed.inferred_type}'"
    assert p_mixed.invalid_parse_count > 0, \
        f"P1-T3: expected invalid_parse_count > 0, got {p_mixed.invalid_parse_count}"
    assert p_mixed.type_distribution.get("date", 0) < 0.95, \
        f"P1-T3: date fraction should be < 0.95, got {p_mixed.type_distribution.get('date', 0)}"

    # P1-T3: pure integer column must NOT trigger mixed-type
    df_int = pl.DataFrame({"ids": ["1001", "1002", "1003", "1004", "1005"]})
    p_int = profile_column(df_int.lazy(), "ids", 5)
    assert p_int.inferred_type == "integer", \
        f"P1-T3: expected 'integer', got '{p_int.inferred_type}'"
    assert p_int.invalid_parse_count == 0, \
        f"P1-T3: pure int column should have invalid_parse_count=0, got {p_int.invalid_parse_count}"

    # P1-T3: pure string column must NOT trigger mixed-type
    df_str = pl.DataFrame({"names": ["Alice", "Bob", "Charlie", "David", "Eve"]})
    p_str = profile_column(df_str.lazy(), "names", 5)
    assert p_str.inferred_type == "string", \
        f"P1-T3: expected 'string', got '{p_str.inferred_type}'"
    assert p_str.invalid_parse_count == 0, \
        f"P1-T3: pure string column should have invalid_parse_count=0"

    print("✓ Profiler tests passed")
