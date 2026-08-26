"""
User JSON Validation & Seeding Service for LoanGuard AI.

This module provides validation, error handling, and database synchronization logic for users.json.

Required Fields per User Entry:
    - username (str): Unique login identifier (MUST BE UNIQUE across entries).
    - first_name (str): User's first name (cannot be empty).
    - last_name (str): User's last name (cannot be empty).
    - email (str): User's email address (must be valid format).
    - mobile / phone (str): User's mobile contact number (cannot be empty).
    - password (str): User's authentication password (cannot be empty).
    - role (str): Exact category role. Must be one of:
        1. "Data Operator"
        2. "Reviewer"
        3. "Data Consumer"

Validation Error Handling:
    - Handles JSON syntax decode errors with line and column reporting.
    - Handles empty payloads or non-list root structure.
    - Flags missing or empty required fields.
    - Detects duplicate usernames within the JSON payload.
    - Rejects invalid or mismatched role categories with explicit hints.
"""

import json
from pathlib import Path
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

User = get_user_model()

ALLOWED_ROLES = {
    "Data Operator": User.Category.DATA_OPERATOR,
    "Reviewer": User.Category.REVIEWER,
    "Data Consumer": User.Category.DATA_CONSUMER,
}

REQUIRED_FIELDS = ["username", "first_name", "last_name", "email", "password"]

USER_MOCK_FILE_PATH = "files/users.json"


def get_user_mock_file_path() -> Path:
    """Returns absolute resolved Path for USER_MOCK_FILE_PATH."""
    path = Path(USER_MOCK_FILE_PATH)
    if not path.is_absolute():
        base_dir = Path(__file__).resolve().parent.parent.parent
        path = base_dir / USER_MOCK_FILE_PATH
    return path


class UserJsonValidator:
    """
    Validator engine for user JSON payload data.
    """

    @classmethod
    def validate_raw_json(cls, raw_json_str: str) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Parses and validates raw JSON string payload.

        Args:
            raw_json_str (str): Raw string containing JSON user data.

        Returns:
            Tuple[List[Dict[str, Any]], List[str]]: (parsed_users_list, error_messages_list)
        """
        errors: list[str] = []

        if not raw_json_str or not raw_json_str.strip():
            return [], ["The provided JSON content is empty."]

        try:
            data = json.loads(raw_json_str)
        except json.JSONDecodeError as exc:
            return [], [f"Invalid JSON Format (Line {exc.lineno}, Col {exc.colno}): {exc.msg}"]

        if not isinstance(data, list):
            return [], ["JSON root element must be a list (Array) of user objects."]

        if len(data) == 0:
            return [], ["JSON array must contain at least 1 user object."]

        parsed_data, validation_errors = cls.validate_user_list(data)
        errors.extend(validation_errors)

        return parsed_data, errors

    @classmethod
    def validate_user_list(cls, data: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Validates an array of user dictionary entries.

        Args:
            data (List[Any]): List of user dict items.

        Returns:
            Tuple[List[Dict[str, Any]], List[str]]: (valid_users_list, errors)
        """
        errors: list[str] = []
        seen_usernames: dict[str, int] = {}
        valid_users: list[dict[str, Any]] = []

        for idx, entry in enumerate(data, start=1):
            if not isinstance(entry, dict):
                errors.append(
                    f"Entry #{idx} is invalid: must be a JSON object, got {type(entry).__name__}."
                )
                continue

            entry_label = f"Entry #{idx}"
            username = str(entry.get("username", "")).strip()

            if username:
                entry_label = f"Entry #{idx} ('{username}')"

            # 1. Required Field Checks (empty / missing string values marked as error)
            missing_fields = []
            for field in REQUIRED_FIELDS:
                val = entry.get(field)
                if val is None or (isinstance(val, str) and not val.strip()):
                    missing_fields.append(field)

            # Check phone or mobile
            phone = str(entry.get("mobile") or entry.get("phone") or "").strip()
            if not phone:
                missing_fields.append("mobile/phone")

            if missing_fields:
                errors.append(
                    f"{entry_label} is missing or has empty required field(s): {', '.join(missing_fields)}."
                )

            # 2. Duplicate Username Check within JSON payload
            if username:
                lower_uname = username.lower()
                if lower_uname in seen_usernames:
                    prev_idx = seen_usernames[lower_uname]
                    errors.append(
                        f"{entry_label} has duplicate username '{username}', which was already defined in Entry #{prev_idx}."
                    )
                else:
                    seen_usernames[lower_uname] = idx

            # 3. Role Fallback: If invalid or missing category, set to "Data Consumer"
            role = str(entry.get("role", "")).strip()
            if not role or role not in ALLOWED_ROLES:
                entry["role"] = "Data Consumer"

            # 4. Email Format Validation
            email = str(entry.get("email", "")).strip()
            if email:
                try:
                    validate_email(email)
                except ValidationError:
                    errors.append(f"{entry_label} has invalid/misleading email address '{email}'.")

            # Note: Extra unknown fields in entry dictionary are preserved but ignored during seeding
            valid_users.append(entry)

        return valid_users, errors


class UserSeederService:
    """
    Database Seeder & JSON Persister Service.
    """

    @classmethod
    def seed_database(cls, users_data: list[dict[str, Any]]) -> tuple[int, int]:
        """
        Synchronizes valid user dict list into Django Database User records.

        Args:
            users_data (List[Dict[str, Any]]): List of validated user objects.

        Returns:
            Tuple[int, int]: (created_count, updated_count)
        """
        created_count = 0
        updated_count = 0

        for info in users_data:
            username = str(info["username"]).strip()
            email = str(info.get("email", "")).strip()
            first_name = str(info.get("first_name", "")).strip()
            last_name = str(info.get("last_name", "")).strip()
            phone = str(info.get("mobile") or info.get("phone", "")).strip()
            role_name = str(info.get("role", "Data Consumer")).strip()
            password = str(info.get("password", "pass123"))

            # Fallback to DATA_CONSUMER if role is not recognized
            category = ALLOWED_ROLES.get(role_name, User.Category.DATA_CONSUMER)

            user = User.objects.filter(username=username).first()
            created = False
            if not user:
                user = User(username=username)
                created = True

            user.first_name = first_name
            user.last_name = last_name
            user.email = email
            user.phone = phone
            user.category = category
            user.set_password(password)
            user.save()

            if created:
                created_count += 1
            else:
                updated_count += 1

        # Automatically trigger permission sync after seeding data
        from app.domain.roles import sync_category_permissions

        sync_category_permissions()

        return created_count, updated_count

    @classmethod
    def save_to_file(cls, users_data: list[dict[str, Any]], target_path: str = None) -> Path:
        """
        Saves user list to target mock file with pretty formatting.

        Args:
            users_data (List[Dict[str, Any]]): Validated user list.
            target_path (str, optional): Target file path (default: USER_MOCK_FILE_PATH).

        Returns:
            Path: Path to saved file.
        """
        if not target_path or target_path == USER_MOCK_FILE_PATH:
            file_path = get_user_mock_file_path()
        else:
            file_path = Path(target_path)
            if not file_path.is_absolute():
                base_dir = Path(__file__).resolve().parent.parent.parent
                file_path = base_dir / target_path

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(users_data, f, indent=2, ensure_ascii=False)

        return file_path
