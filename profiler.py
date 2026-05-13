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
    Profile all columns. One .select() pass per column to collect all null variants.
    """
    start = time.time()
    check_cancel(cancel_token)

    total_cols = len(metadata.columns)
    row_count = metadata.row_count

    columns: list[ColumnProfile] = []
    for i, col in enumerate(metadata.columns):
        check_cancel(cancel_token)
        profile = profile_column(lf, col, row_count)
        columns.append(profile)
        if progress:
            progress.update("Profiling", f"Column {i + 1}/{total_cols}: {col}", i + 1, total_cols)

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

    # Type inference
    inferred_type, type_dist, invalid_parse = _infer_type(lf, col, row_count)

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


def _infer_type(
    lf: pl.LazyFrame,
    col: str,
    row_count: int,
) -> tuple[str, dict[str, float], int]:
    """
    Attempt type casts with strict=False. Return (dominant_type, distribution, invalid_count).
    """
    counts: dict[str, int] = {}

    def _try_cast(dtype) -> int:
        try:
            return int(
                lf.select(pl.col(col).cast(dtype, strict=False).is_not_null().sum()).collect().item()
            )
        except Exception:
            return 0

    counts["integer"] = _try_cast(pl.Int64)
    counts["float"] = _try_cast(pl.Float64)
    counts["boolean"] = _try_cast(pl.Boolean)

    # Date / datetime: try string parsing
    try:
        counts["date"] = int(
            lf.select(
                pl.col(col).cast(pl.Utf8).str.to_date(strict=False).is_not_null().sum()
            ).collect().item()
        )
    except Exception:
        counts["date"] = 0

    try:
        counts["datetime"] = int(
            lf.select(
                pl.col(col).cast(pl.Utf8).str.to_datetime(strict=False).is_not_null().sum()
            ).collect().item()
        )
    except Exception:
        counts["datetime"] = 0

    # String is always valid
    counts["string"] = row_count

    total = sum(counts.values())
    type_dist = {k: (v / total if total > 0 else 0.0) for k, v in counts.items()}

    dominant = max(counts, key=lambda k: counts[k])
    invalid_count = max(0, row_count - counts[dominant])

    return dominant, type_dist, invalid_count


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

    print("✓ Profiler tests passed")
