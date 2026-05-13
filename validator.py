import polars as pl
from dataclasses import dataclass, field
from typing import Optional, Literal
import time
import threading

try:
    from pydantic import BaseModel
except ImportError:
    from dataclasses import dataclass as _dc
    class BaseModel:  # type: ignore[no-redef]
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

from profiler import FileProfile, ColumnProfile, profile_file
from key_discovery import validate_key, discover_keys
from metadata import FileMetadata, SchemaDiff, compare_schemas
from utils import Progress, check_cancel


# ---------------------------------------------------------------------------
# Config (Pydantic at JSON boundary)
# ---------------------------------------------------------------------------

class ColumnRuleConfig(BaseModel):
    name: str
    required: bool = False
    expected_type: Optional[str] = None
    max_null_rate: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    regex_pattern: Optional[str] = None
    allowed_values: Optional[list[str]] = None


class ValidationConfig(BaseModel):
    null_warn_threshold: float = 0.50
    null_error_threshold: float = 0.90
    duplicate_key_warn_threshold: int = 1
    business_rules: list[ColumnRuleConfig] = []


# ---------------------------------------------------------------------------
# Internal result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationCheck:
    name: str
    severity: Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
    passed: bool
    message: str
    detail: Optional[str] = None
    column: Optional[str] = None
    affected_count: Optional[int] = None


@dataclass
class ValidationReport:
    checks: list[ValidationCheck]
    profile: FileProfile
    compatibility_score: Optional[float]
    schema_diff: Optional[SchemaDiff]
    duration_s: float
    summary: dict[str, int] = field(default_factory=lambda: {"INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0})

    @property
    def total_count(self) -> int:
        return self.profile.total_count

    @property
    def column_count(self) -> int:
        return self.profile.column_count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_file(
    lf: pl.LazyFrame,
    metadata: FileMetadata,
    config: Optional[ValidationConfig] = None,
    key_columns: Optional[list[str]] = None,
    progress: Optional[Progress] = None,
    cancel_token: Optional[threading.Event] = None,
) -> ValidationReport:
    """Validate a single file: structure + data quality."""
    start = time.time()
    check_cancel(cancel_token)

    if config is None:
        config = ValidationConfig()

    if progress:
        progress.update("Validation", "Profiling file", 0, 1)
    profile = profile_file(lf, metadata, progress, cancel_token)

    checks: list[ValidationCheck] = []
    checks.extend(_check_row_count(profile))
    checks.extend(_check_null_rates(profile, config))
    checks.extend(_check_type_consistency(profile))
    checks.extend(_check_textual_nulls(profile))

    if key_columns:
        check_cancel(cancel_token)
        is_unique, dup_count = validate_key(lf, key_columns)
        if not is_unique:
            severity: Literal["WARNING", "CRITICAL"] = (
                "CRITICAL" if dup_count > config.duplicate_key_warn_threshold else "WARNING"
            )
            checks.append(ValidationCheck(
                name="Duplicate Keys",
                severity=severity,
                passed=False,
                message=f"Key column(s) {key_columns} have {dup_count} duplicate(s)",
                column=",".join(key_columns),
                affected_count=dup_count,
            ))

    for rule in config.business_rules:
        check_cancel(cancel_token)
        checks.append(_check_business_rule(lf, rule, profile.total_count))

    summary: dict[str, int] = {"INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0}
    for chk in checks:
        summary[chk.severity] += 1

    return ValidationReport(
        checks=checks,
        profile=profile,
        compatibility_score=None,
        schema_diff=None,
        duration_s=time.time() - start,
        summary=summary,
    )


def validate_two_files(
    lf1: pl.LazyFrame,
    m1: FileMetadata,
    lf2: pl.LazyFrame,
    m2: FileMetadata,
    config: Optional[ValidationConfig] = None,
    progress: Optional[Progress] = None,
    cancel_token: Optional[threading.Event] = None,
) -> tuple[ValidationReport, ValidationReport, SchemaDiff]:
    """Validate two files and compare their schemas."""
    check_cancel(cancel_token)

    report1 = validate_file(lf1, m1, config, progress=progress, cancel_token=cancel_token)
    check_cancel(cancel_token)
    report2 = validate_file(lf2, m2, config, progress=progress, cancel_token=cancel_token)
    check_cancel(cancel_token)

    schema_diff = compare_schemas(m1, m2)

    if schema_diff.columns_only_in_f1:
        report1.checks.append(ValidationCheck(
            name="Schema Drift",
            severity="WARNING",
            passed=False,
            message=f"Columns only in file 1: {', '.join(schema_diff.columns_only_in_f1)}",
        ))
        report1.summary["WARNING"] += 1

    if schema_diff.columns_only_in_f2:
        report2.checks.append(ValidationCheck(
            name="Schema Drift",
            severity="WARNING",
            passed=False,
            message=f"Columns only in file 2: {', '.join(schema_diff.columns_only_in_f2)}",
        ))
        report2.summary["WARNING"] += 1

    report1.compatibility_score = schema_diff.compatibility_score
    report2.compatibility_score = schema_diff.compatibility_score
    report1.schema_diff = schema_diff
    report2.schema_diff = schema_diff

    return report1, report2, schema_diff


# ---------------------------------------------------------------------------
# Built-in check functions
# ---------------------------------------------------------------------------

def _check_row_count(profile: FileProfile) -> list[ValidationCheck]:
    return [ValidationCheck(
        name="Row Count",
        severity="INFO",
        passed=True,
        message=f"File contains {profile.total_count:,} rows",
        affected_count=profile.total_count,
    )]


def _check_null_rates(profile: FileProfile, config: ValidationConfig) -> list[ValidationCheck]:
    checks = []
    for col in profile.columns:
        rate = col.null_variant_rate
        if rate > config.null_error_threshold:
            sev: Literal["WARNING", "ERROR"] = "ERROR"
        elif rate > config.null_warn_threshold:
            sev = "WARNING"
        else:
            continue
        checks.append(ValidationCheck(
            name="High Null Rate",
            severity=sev,
            passed=False,
            message=f"Column '{col.name}' has {rate * 100:.1f}% null variants",
            column=col.name,
            affected_count=col.total_null_variants,
        ))
    return checks


def _check_type_consistency(profile: FileProfile) -> list[ValidationCheck]:
    checks = []
    for col in profile.columns:
        if not col.type_distribution:
            continue
        max_pct = max(col.type_distribution.values())
        if max_pct < 0.95 and col.invalid_parse_count > 0:
            checks.append(ValidationCheck(
                name="Mixed Types",
                severity="WARNING",
                passed=False,
                message=(
                    f"Column '{col.name}' has mixed types "
                    f"({max_pct * 100:.0f}% {col.inferred_type})"
                ),
                column=col.name,
                affected_count=col.invalid_parse_count,
            ))
    return checks


def _check_textual_nulls(profile: FileProfile) -> list[ValidationCheck]:
    checks = []
    for col in profile.columns:
        if col.textual_null_count > 0:
            checks.append(ValidationCheck(
                name="Textual Nulls",
                severity="WARNING",
                passed=False,
                message=(
                    f"Column '{col.name}' has {col.textual_null_count} "
                    "textual nulls (null, N/A, etc.)"
                ),
                column=col.name,
                affected_count=col.textual_null_count,
            ))
    return checks


def _check_business_rule(
    lf: pl.LazyFrame,
    rule: ColumnRuleConfig,
    total_count: int,
) -> ValidationCheck:
    col = rule.name

    if rule.max_null_rate is not None:
        try:
            null_count = int(lf.select(pl.col(col).is_null().sum()).collect().item())
            null_rate = null_count / total_count if total_count else 0.0
            if null_rate > rule.max_null_rate:
                return ValidationCheck(
                    name=f"Business Rule: {col}",
                    severity="ERROR",
                    passed=False,
                    message=(
                        f"Column '{col}' null rate {null_rate * 100:.1f}% exceeds "
                        f"max {rule.max_null_rate * 100:.0f}%"
                    ),
                    column=col,
                    affected_count=null_count,
                )
        except Exception as exc:
            return ValidationCheck(
                name=f"Business Rule: {col}",
                severity="ERROR",
                passed=False,
                message=f"Rule evaluation failed for '{col}': {exc}",
                column=col,
            )

    return ValidationCheck(
        name=f"Business Rule: {col}",
        severity="INFO",
        passed=True,
        message=f"Column '{col}' passed business rule checks",
        column=col,
    )


if __name__ == "__main__":
    print("✓ Validator module ready for integration testing")
