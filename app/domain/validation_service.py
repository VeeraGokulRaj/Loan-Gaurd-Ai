"""
Validation Rules JSON Validation & Seeding Service for LoanGuard AI.

This module provides structural validation, error handling, database seeding/syncing,
and admin JSON editing logic for `validation.json` (or `validation_rules.json`).
"""

import json
from pathlib import Path
from typing import Any

from django.db import transaction

from app.models.validation import ValidationRule, ValidationSeverity

SEVERITY_MAP = {
    "LOW": ValidationSeverity.LOW,
    "MEDIUM": ValidationSeverity.MEDIUM,
    "HIGH": ValidationSeverity.HIGH,
    "CRITICAL": ValidationSeverity.CRITICAL,
}

REQUIRED_RULE_FIELDS = [
    "rule_code",
    "strategy_key",
    "rule_name",
    "field_name",
    "description",
    "severity",
    "is_active",
    "parameters",
]
DEFAULT_VALIDATION_FILE_PATH = "files/validation.json"


def get_validation_json_path(file_path: str | None = None) -> Path:
    """Returns absolute resolved Path for validation JSON file."""
    target_path = file_path or DEFAULT_VALIDATION_FILE_PATH
    path = Path(target_path)
    if not path.is_absolute():
        base_dir = Path(__file__).resolve().parent.parent.parent
        path = base_dir / target_path
        # Fallback to validation_rules.json if validation.json does not exist
        if not path.exists() and target_path == DEFAULT_VALIDATION_FILE_PATH:
            alt_path = base_dir / "files/validation_rules.json"
            if alt_path.exists():
                return alt_path
    return path


class ValidationRuleJsonService:
    """
    Service for parsing, validating, saving, and seeding configurable validation rules from JSON.
    Mirroring UserSeederService architectural pattern.
    """

    @classmethod
    def validate_raw_json(cls, raw_json: str) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Parses and validates raw JSON string content for validation rules.

        Returns:
            tuple: (valid_rules_list, error_messages_list)
        """
        errors: list[str] = []
        if not raw_json or not raw_json.strip():
            return [], ["JSON content is empty."]

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            return [], [f"JSON syntax error: Line {exc.lineno}, Col {exc.colno} - {exc.msg}"]

        if not isinstance(data, list):
            return [], ["Root payload must be a JSON array (list of rule objects)."]

        valid_rules: list[dict[str, Any]] = []
        seen_codes: set[str] = set()

        for idx, entry in enumerate(data, start=1):
            if not isinstance(entry, dict):
                errors.append(f"Entry #{idx}: Rule item must be a JSON object.")
                continue

            # 1. Required fields check
            missing = [f for f in REQUIRED_RULE_FIELDS if entry.get(f) is None]
            if missing:
                errors.append(
                    f"Entry #{idx} ({entry.get('rule_code', 'UNKNOWN')}): Missing required fields: {', '.join(missing)}"
                )
                continue

            rule_code = str(entry["rule_code"]).strip().upper()

            # 2. Duplicate rule_code check
            if rule_code in seen_codes:
                errors.append(
                    f"Entry #{idx}: Duplicate rule_code '{rule_code}' detected in JSON payload."
                )
                continue
            seen_codes.add(rule_code)

            # 3. Severity string check
            raw_severity = str(entry.get("severity", "MEDIUM")).upper()
            if raw_severity not in SEVERITY_MAP:
                errors.append(
                    f"Rule {rule_code}: Invalid severity '{raw_severity}'. Must be one of LOW, MEDIUM, HIGH, CRITICAL."
                )
                continue

            cleaned_entry = {
                "rule_code": rule_code,
                "strategy_key": str(entry.get("strategy_key") or rule_code).strip(),
                "rule_name": str(entry["rule_name"]).strip(),
                "field_name": str(entry["field_name"]).strip(),
                "description": str(entry["description"]).strip(),
                "severity": raw_severity,
                "is_active": bool(entry.get("is_active", True)),
                "parameters": entry.get("parameters")
                if isinstance(entry.get("parameters"), dict)
                else {},
            }
            valid_rules.append(cleaned_entry)

        return valid_rules, errors

    @classmethod
    def load_and_validate_json(
        cls, file_path: str | None = None
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Reads and validates validation.json payload from file disk."""
        json_path = get_validation_json_path(file_path)
        if not json_path.exists():
            return [], [f"Validation JSON file not found at: {json_path}"]

        try:
            with open(json_path, encoding="utf-8") as f:
                raw_content = f.read()
            return cls.validate_raw_json(raw_content)
        except Exception as exc:
            return [], [f"Failed to read {json_path.name}: {str(exc)}"]

    @classmethod
    def save_to_file(cls, valid_rules: list[dict[str, Any]], file_path: str | None = None) -> Path:
        """Saves validated rules list back to target validation.json file."""
        json_path = get_validation_json_path(file_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(valid_rules, f, indent=2)

        # Also sync to validation_rules.json if it exists to maintain consistency
        alt_path = json_path.parent / "validation_rules.json"
        if alt_path.exists() and alt_path != json_path:
            with open(alt_path, "w", encoding="utf-8") as f:
                json.dump(valid_rules, f, indent=2)

        return json_path

    @classmethod
    @transaction.atomic
    def seed_database(cls, valid_rules: list[dict[str, Any]]) -> tuple[int, int]:
        """
        Seeds or updates ValidationRule DB models in bulk for high performance.
        Reduces DB queries from 2*N down to 2 bulk operations.
        """
        if not valid_rules:
            return 0, 0

        target_codes = [item["rule_code"] for item in valid_rules]
        existing_map = {
            rule.rule_code: rule
            for rule in ValidationRule.objects.filter(rule_code__in=target_codes)
        }

        to_create: list[ValidationRule] = []
        to_update: list[ValidationRule] = []
        update_fields = [
            "strategy_key",
            "rule_name",
            "field_name",
            "description",
            "severity",
            "is_active",
            "parameters",
        ]

        for item in valid_rules:
            code = item["rule_code"]
            severity_enum = SEVERITY_MAP.get(item["severity"].upper(), ValidationSeverity.MEDIUM)

            if code in existing_map:
                obj = existing_map[code]
                obj.strategy_key = item["strategy_key"]
                obj.rule_name = item["rule_name"]
                obj.field_name = item["field_name"]
                obj.description = item["description"]
                obj.severity = severity_enum
                obj.is_active = item["is_active"]
                obj.parameters = item["parameters"]
                to_update.append(obj)
            else:
                to_create.append(
                    ValidationRule(
                        rule_code=code,
                        strategy_key=item["strategy_key"],
                        rule_name=item["rule_name"],
                        field_name=item["field_name"],
                        description=item["description"],
                        severity=severity_enum,
                        is_active=item["is_active"],
                        parameters=item["parameters"],
                    )
                )

        if to_create:
            ValidationRule.objects.bulk_create(to_create, batch_size=500)

        if to_update:
            ValidationRule.objects.bulk_update(to_update, fields=update_fields, batch_size=500)

        return len(to_create), len(to_update)
