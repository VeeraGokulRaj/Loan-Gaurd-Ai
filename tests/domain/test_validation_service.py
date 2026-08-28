"""
Test cases for app.domain.validation_service.ValidationRuleJsonService.

Covers the JSON path resolution, raw JSON structural validation, file loading/saving,
and the database seeding/syncing logic with positive, negative, edge, boundary and
invalid input scenarios.
"""

import json

import pytest

from app.domain.validation_service import (
    ValidationRuleJsonService,
    get_validation_json_path,
)
from app.models.validation import ValidationRule, ValidationSeverity


def _rule(rule_code="VAL_001", severity="MEDIUM", **overrides):
    base = {
        "rule_code": rule_code,
        "strategy_key": "RULE_STRATEGY",
        "rule_name": "Sample Rule",
        "field_name": "loan_id",
        "description": "A sample validation rule.",
        "severity": severity,
        "is_active": True,
        "parameters": {},
    }
    base.update(overrides)
    return base


def _dump(rules):
    return json.dumps(rules)


@pytest.mark.django_db
class TestValidationServicePathResolution:
    """Tests for get_validation_json_path."""

    def test_default_path_is_resolved_absolute(self):
        """The default validation path should resolve to an absolute Path."""
        path = get_validation_json_path()
        assert path.is_absolute()
        assert path.name in ("validation.json", "validation_rules.json")

    def test_explicit_absolute_path_returns_as_is(self):
        """An absolute custom path should be returned unchanged."""
        import pathlib

        custom = pathlib.Path("/tmp/custom_validation.json")
        assert get_validation_json_path(str(custom)) == custom

    def test_custom_relative_path_resolved_to_base(self):
        """A relative custom path should be resolved against the project base dir."""
        from app.domain.validation_service import get_validation_json_path

        path = get_validation_json_path("files/custom_rules.json")
        assert path.name == "custom_rules.json"
        assert path.is_absolute()


@pytest.mark.django_db
class TestValidateRawJson:
    """Tests for ValidationRuleJsonService.validate_raw_json."""

    # ── Root / Syntax Level (Invalid / Negative) ──

    def test_empty_json_returns_empty_error(self):
        valid, errors = ValidationRuleJsonService.validate_raw_json("")
        assert valid == []
        assert "JSON content is empty" in errors[0]

    def test_whitespace_only_json_returns_empty_error(self):
        valid, errors = ValidationRuleJsonService.validate_raw_json("   \n  ")
        assert valid == []
        assert errors[0] == "JSON content is empty."

    def test_invalid_json_syntax_reports_line_and_col(self):
        valid, errors = ValidationRuleJsonService.validate_raw_json("{bad json")
        assert valid == []
        assert "JSON syntax error: Line " in errors[0]
        assert "Col " in errors[0]

    def test_non_list_root_rejected(self):
        valid, errors = ValidationRuleJsonService.validate_raw_json('{"a": 1}')
        assert valid == []
        assert "Root payload must be a JSON array" in errors[0]

    def test_empty_list_returns_no_errors(self):
        valid, errors = ValidationRuleJsonService.validate_raw_json("[]")
        assert valid == []
        assert errors == []

    # ── Entry Level (Negative / Invalid) ──

    def test_non_dict_entry_rejected(self):
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump([1, "x"]))
        assert valid == []
        assert len(errors) == 2
        assert "Rule item must be a JSON object" in errors[0]

    def test_missing_required_fields_rejected(self):
        entry = _rule()
        del entry["field_name"]
        del entry["severity"]
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump([entry]))
        assert valid == []
        assert len(errors) == 1
        assert "Missing required fields: field_name, severity" in errors[0]

    def test_null_required_fields_rejected(self):
        entry = _rule(field_name=None, description=None)
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump([entry]))
        assert valid == []
        assert "Missing required fields" in errors[0]

    def test_duplicate_rule_code_rejected(self):
        data = [_rule("VAL_001"), _rule("VAL_001")]
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump(data))
        assert len(valid) == 1
        assert len(errors) == 1
        assert "Duplicate rule_code 'VAL_001'" in errors[0]

    def test_duplicate_rule_code_case_insensitive(self):
        data = [_rule("val_001"), _rule("VAL_001")]
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump(data))
        # Both normalize to VAL_001 -> second is a duplicate
        assert len(valid) == 1
        assert "Duplicate rule_code" in errors[0]

    def test_invalid_severity_rejected(self):
        valid, errors = ValidationRuleJsonService.validate_raw_json(
            _dump([_rule(severity="URGENT")])
        )
        assert valid == []
        assert "Invalid severity 'URGENT'" in errors[0]

    # ── Cleaning / Normalization (Positive / Edge) ──

    def test_valid_single_rule_passes(self):
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump([_rule()]))
        assert len(valid) == 1
        assert errors == []
        assert valid[0]["rule_code"] == "VAL_001"
        assert valid[0]["severity"] == "MEDIUM"
        assert valid[0]["is_active"] is True

    def test_rule_code_uppercased_and_stripped(self):
        valid, _ = ValidationRuleJsonService.validate_raw_json(_dump([_rule("  val_007  ")]))
        assert valid[0]["rule_code"] == "VAL_007"

    def test_lowercase_severity_normalized(self):
        valid, _ = ValidationRuleJsonService.validate_raw_json(_dump([_rule(severity="critical")]))
        assert valid[0]["severity"] == "CRITICAL"

    def test_severity_mapped_to_enum_value_on_seed(self):
        valid, _ = ValidationRuleJsonService.validate_raw_json(_dump([_rule(severity="HIGH")]))
        created, _ = ValidationRuleJsonService.seed_database(valid)
        assert created == 1
        rule = ValidationRule.objects.get(rule_code="VAL_001")
        assert rule.severity == ValidationSeverity.HIGH

    def test_empty_strategy_key_defaults_to_rule_code(self):
        valid, _ = ValidationRuleJsonService.validate_raw_json(_dump([_rule(strategy_key="")]))
        assert valid[0]["strategy_key"] == "VAL_001"

    def test_parameters_dict_preserved(self):
        params = {"min_rate": 0.0, "max_rate": 35.0}
        valid, _ = ValidationRuleJsonService.validate_raw_json(_dump([_rule(parameters=params)]))
        assert valid[0]["parameters"] == params

    def test_non_dict_parameters_coerced_to_empty(self):
        valid, _ = ValidationRuleJsonService.validate_raw_json(
            _dump([_rule(parameters=["a", "b"])])
        )
        assert valid[0]["parameters"] == {}

    def test_missing_parameters_is_a_required_field_error(self):
        """parameters is a required field; omitting it should reject the entry."""
        entry = _rule()
        del entry["parameters"]
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump([entry]))
        assert valid == []
        assert "Missing required fields: parameters" in errors[0]

    def test_string_fields_stripped(self):
        valid, _ = ValidationRuleJsonService.validate_raw_json(
            _dump([_rule(field_name="  loan_id  ", rule_name="  My Rule  ")])
        )
        assert valid[0]["field_name"] == "loan_id"
        assert valid[0]["rule_name"] == "My Rule"

    def test_missing_is_active_is_a_required_field_error(self):
        """is_active is a required field; omitting it should reject the entry."""
        entry = _rule()
        del entry["is_active"]
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump([entry]))
        assert valid == []
        assert "Missing required fields: is_active" in errors[0]

    def test_is_active_false_boolean_preserved(self):
        valid, _ = ValidationRuleJsonService.validate_raw_json(_dump([_rule(is_active=False)]))
        assert valid[0]["is_active"] is False

    def test_is_active_string_false_coerced_to_true(self):
        """bool('false') is truthy, so a non-empty string is_active stays enabled."""
        valid, _ = ValidationRuleJsonService.validate_raw_json(_dump([_rule(is_active="false")]))
        assert valid[0]["is_active"] is True

    def test_mixed_valid_and_invalid_entries(self):
        data = [_rule("VAL_001"), _rule("VAL_002", severity="NOPE"), _rule("VAL_001")]
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump(data))
        assert len(valid) == 1  # only VAL_001 kept
        assert len(errors) == 2  # invalid severity + duplicate


@pytest.mark.django_db
class TestLoadAndValidateJson:
    """Tests for ValidationRuleJsonService.load_and_validate_json."""

    def test_missing_file_returns_error(self, tmp_path):
        missing = str(tmp_path / "nope.json")
        valid, errors = ValidationRuleJsonService.load_and_validate_json(missing)
        assert valid == []
        assert "file not found" in errors[0]

    def test_load_valid_file(self, tmp_path):
        path = tmp_path / "rules.json"
        path.write_text(_dump([_rule()]), encoding="utf-8")
        valid, errors = ValidationRuleJsonService.load_and_validate_json(str(path))
        assert len(valid) == 1
        assert errors == []

    def test_load_invalid_content_returns_error(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        valid, errors = ValidationRuleJsonService.load_and_validate_json(str(path))
        assert valid == []
        assert errors  # an error is reported


@pytest.mark.django_db
class TestSaveToFile:
    """Tests for ValidationRuleJsonService.save_to_file."""

    def test_save_then_reload_round_trip(self, tmp_path):
        target = str(tmp_path / "sub" / "rules.json")
        rules = [_rule()]
        saved = ValidationRuleJsonService.save_to_file(rules, target)
        assert saved.exists()
        valid, errors = ValidationRuleJsonService.load_and_validate_json(str(saved))
        assert errors == []
        assert valid[0]["rule_code"] == "VAL_001"

    def test_save_creates_parent_directories(self, tmp_path):
        target = str(tmp_path / "deep" / "nested" / "rules.json")
        saved = ValidationRuleJsonService.save_to_file([_rule()], target)
        assert saved.parent.exists()


@pytest.mark.django_db
class TestSeedDatabase:
    """Tests for ValidationRuleJsonService.seed_database."""

    def setup_method(self):
        ValidationRule.objects.all().delete()

    def test_empty_rules_returns_zero_zero(self):
        created, updated = ValidationRuleJsonService.seed_database([])
        assert (created, updated) == (0, 0)

    def test_create_new_rules(self):
        valid = [_rule("VAL_001"), _rule("VAL_002", severity="HIGH")]
        created, updated = ValidationRuleJsonService.seed_database(valid)
        assert created == 2
        assert updated == 0
        assert ValidationRule.objects.count() == 2
        assert ValidationRule.objects.filter(
            rule_code="VAL_002", severity=ValidationSeverity.HIGH
        ).exists()

    def test_inactive_rule_flag_persisted(self):
        valid = [_rule("VAL_003", is_active=False)]
        ValidationRuleJsonService.seed_database(valid)
        rule = ValidationRule.objects.get(rule_code="VAL_003")
        assert rule.is_active is False

    def test_parameters_persisted(self):
        params = {"max_rate": 35.0}
        valid = [_rule("VAL_004", parameters=params)]
        ValidationRuleJsonService.seed_database(valid)
        rule = ValidationRule.objects.get(rule_code="VAL_004")
        assert rule.parameters == params

    def test_update_existing_rule(self):
        valid_initial = [_rule("VAL_005")]
        ValidationRuleJsonService.seed_database(valid_initial)

        updated_entry = _rule("VAL_005", severity="CRITICAL", field_name="maturity_date")
        created, updated = ValidationRuleJsonService.seed_database([updated_entry])
        assert created == 0
        assert updated == 1
        assert ValidationRule.objects.count() == 1

        rule = ValidationRule.objects.get(rule_code="VAL_005")
        assert rule.severity == ValidationSeverity.CRITICAL
        assert rule.field_name == "maturity_date"

    def test_mixed_create_and_update(self):
        ValidationRule.objects.create(
            rule_code="VAL_EXIST",
            rule_name="Existing",
            field_name="loan_id",
            description="d",
        )
        data = [
            _rule("VAL_NEW"),
            _rule("VAL_EXIST", rule_name="Updated Name"),
        ]
        created, updated = ValidationRuleJsonService.seed_database(data)
        assert created == 1
        assert updated == 1
        assert ValidationRule.objects.get(rule_code="VAL_EXIST").rule_name == "Updated Name"

    def test_seed_with_rules_missing_from_db_does_not_duplicate(self):
        """Seeding the same codes twice should only update, never duplicate."""
        data = [_rule("VAL_010")]
        ValidationRuleJsonService.seed_database(data)
        created, updated = ValidationRuleJsonService.seed_database(data)
        assert (created, updated) == (0, 1)
        assert ValidationRule.objects.filter(rule_code="VAL_010").count() == 1

    def test_strategy_key_defaulted_before_seed(self):
        """A rule lacking strategy_key is normalized by validation before seeding."""
        entry = _rule("VAL_011", strategy_key="")
        valid, errors = ValidationRuleJsonService.validate_raw_json(_dump([entry]))
        assert errors == []
        assert valid[0]["strategy_key"] == "VAL_011"
        ValidationRuleJsonService.seed_database(valid)
        rule = ValidationRule.objects.get(rule_code="VAL_011")
        assert rule.strategy_key == "VAL_011"
