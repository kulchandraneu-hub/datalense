"""
Regression tests for validation behavior.

Coverage:
  - Mixed Types warning fires when a date column contains non-ISO values (P1-T3)
  - Mixed Types warning does NOT fire when all values match the dominant type
  - Null variants are correctly reflected in the column profile
  - Duplicate Keys check fires when key column is non-unique
  - Profile structure: total_count, column_count, null_variant_rate in [0,1]

Marker: quick — uses demo_small and clean synthetic fixtures.
"""
import pytest

pytestmark = pytest.mark.quick


class TestMixedTypesDetection:
    """P1-T3 regression: _check_type_consistency fires on any non-zero invalid_parse_count."""

    def test_joindate_mixed_types_fires_for_f2(self, demo_result):
        """
        File B JoinDate has 1 US-format date ('11/03/2023') out of 11 rows.
        _infer_type: 10/11 = 90.9% parse as Date → inferred_type='date',
        invalid_parse_count=1 → Mixed Types check fires.
        """
        checks = [
            c for c in demo_result.validation_f2.checks
            if c.column == "JoinDate" and c.name == "Mixed Types"
        ]
        assert len(checks) > 0, "Mixed Types check not found for JoinDate in file B"

    def test_joindate_no_mixed_types_for_f1(self, demo_result):
        """File A JoinDate is all ISO-8601 → no invalid parses → no Mixed Types check."""
        checks = [
            c for c in demo_result.validation_f1.checks
            if c.column == "JoinDate" and c.name == "Mixed Types"
        ]
        assert len(checks) == 0, "Unexpected Mixed Types check on File A JoinDate"

    def test_mixed_types_check_has_affected_count(self, demo_result):
        """affected_count must be 1 (the one non-ISO JoinDate row in demo B)."""
        checks = [
            c for c in demo_result.validation_f2.checks
            if c.column == "JoinDate" and c.name == "Mixed Types"
        ]
        assert checks[0].affected_count == 1


class TestNullVariantDetection:
    """Null variants are detected and surfaced in the column profile."""

    def test_salary_null_in_f2_profile(self, demo_result):
        """File B Salary has 1 null (ID=1006 has empty cell) → profile reflects it."""
        salary = next(
            (c for c in demo_result.validation_f2.profile.columns if c.name == "Salary"),
            None,
        )
        assert salary is not None, "Salary column not found in file B profile"
        assert salary.total_null_variants > 0
        assert salary.null_variant_rate > 0.0

    def test_salary_no_nulls_in_f1(self, demo_result):
        """File A Salary has no nulls → profile confirms zero null variants."""
        salary = next(
            (c for c in demo_result.validation_f1.profile.columns if c.name == "Salary"),
            None,
        )
        assert salary is not None
        assert salary.total_null_variants == 0
        assert salary.null_variant_rate == 0.0


class TestDuplicateKeyWarning:
    """Duplicate Keys validation check fires when key column is non-unique."""

    def test_duplicate_key_check_fires_for_f2(self, demo_result):
        """ID=1008 appears twice in file B → Duplicate Keys check in validation_f2."""
        dup_checks = [
            c for c in demo_result.validation_f2.checks
            if "Duplicate" in c.name
        ]
        assert len(dup_checks) > 0, "Duplicate Keys check not found in file B validation"

    def test_duplicate_key_check_not_in_f1(self, demo_result):
        """File A has no duplicate keys → no Duplicate Keys check."""
        dup_checks = [
            c for c in demo_result.validation_f1.checks
            if "Duplicate" in c.name
        ]
        assert len(dup_checks) == 0


class TestProfileStructure:
    """Column profile fields are populated correctly."""

    def test_profile_has_all_columns(self, demo_result):
        names = {c.name for c in demo_result.validation_f1.profile.columns}
        expected = {"EmployeeID", "FirstName", "LastName", "Email",
                    "Country", "Department", "Salary", "JoinDate", "Status"}
        assert expected.issubset(names)

    def test_profile_total_count(self, demo_result):
        assert demo_result.validation_f1.profile.total_count == 10

    def test_profile_null_variant_rate_in_range(self, clean_result):
        for col in clean_result.validation_f1.profile.columns:
            assert 0.0 <= col.null_variant_rate <= 1.0, (
                f"Column {col.name}: null_variant_rate={col.null_variant_rate} out of [0,1]"
            )

    def test_profile_column_count_matches(self, clean_result):
        assert clean_result.validation_f1.profile.column_count == 4  # ID,Name,Value,Category

    def test_profile_total_count_clean(self, clean_result):
        assert clean_result.validation_f1.profile.total_count == 5

    def test_validation_checks_is_list(self, clean_result):
        assert isinstance(clean_result.validation_f1.checks, list)
