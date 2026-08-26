import json

import pytest

from app.domain.user_service import (
    UserJsonValidator,
    UserSeederService,
)
from app.models import User


@pytest.mark.django_db
def test_existing_username_update():
    """Verifies that re-seeding an existing username updates attributes without duplicate errors."""
    initial_json = json.dumps(
        [
            {
                "username": "op_murugan",
                "first_name": "Murugan",
                "last_name": "Raman",
                "email": "murugan@loanguard.tn",
                "mobile": "9840112341",
                "password": "pass123",
                "role": "Data Operator",
            }
        ]
    )
    valid_users, _ = UserJsonValidator.validate_raw_json(initial_json)
    UserSeederService.seed_database(valid_users)

    # Update first name and email for existing user
    updated_json = json.dumps(
        [
            {
                "username": "op_murugan",
                "first_name": "Murugan Updated",
                "last_name": "Raman",
                "email": "murugan.new@loanguard.tn",
                "mobile": "9840112341",
                "password": "pass123",
                "role": "Data Operator",
            }
        ]
    )
    valid_users_2, _ = UserJsonValidator.validate_raw_json(updated_json)
    created_cnt, updated_cnt = UserSeederService.seed_database(valid_users_2)

    assert created_cnt == 0
    assert updated_cnt == 1

    updated_user = User.objects.get(username="op_murugan")
    assert updated_user.first_name == "Murugan Updated"
    assert updated_user.email == "murugan.new@loanguard.tn"


@pytest.mark.django_db
def test_unknown_extra_fields_ignored():
    """Verifies that unknown custom fields in user JSON are safely accepted and ignored."""
    custom_json = json.dumps(
        [
            {
                "username": "rev_priya",
                "first_name": "Priya",
                "last_name": "Dharshini",
                "email": "priya@loanguard.tn",
                "mobile": "9840223451",
                "password": "pass123",
                "role": "Reviewer",
                "department": "Engineering",
                "custom_note": "Hackathon Demo User",
            }
        ]
    )
    valid_users, errors = UserJsonValidator.validate_raw_json(custom_json)
    assert len(errors) == 0

    created_cnt, _ = UserSeederService.seed_database(valid_users)
    assert created_cnt == 1
    assert User.objects.filter(username="rev_priya").exists()


@pytest.mark.django_db
def test_unrecognized_role_fallback_to_data_consumer():
    """Verifies that missing or invalid role defaults to Data Consumer (Category 3)."""
    invalid_role_json = json.dumps(
        [
            {
                "username": "con_fallback",
                "first_name": "Fallback",
                "last_name": "User",
                "email": "fallback@loanguard.tn",
                "mobile": "9840999999",
                "password": "pass123",
                "role": "Super Admin Custom",
            }
        ]
    )
    valid_users, errors = UserJsonValidator.validate_raw_json(invalid_role_json)
    assert len(errors) == 0  # Fallback handles invalid role gracefully
    assert valid_users[0]["role"] == "Data Consumer"

    UserSeederService.seed_database(valid_users)
    seeded_user = User.objects.get(username="con_fallback")
    assert seeded_user.category == User.Category.DATA_CONSUMER


@pytest.mark.django_db
def test_empty_required_fields_and_invalid_email_error():
    """Verifies that empty string required fields and malformed email trigger validation errors."""
    bad_json = json.dumps(
        [
            {
                "username": "",
                "first_name": "   ",
                "last_name": "Test",
                "email": "not-an-email",
                "mobile": "",
                "password": "pass123",
                "role": "Data Operator",
            }
        ]
    )
    _, errors = UserJsonValidator.validate_raw_json(bad_json)
    assert len(errors) >= 2
    err_msg = " ".join(errors)
    assert "missing or has empty required field" in err_msg
    assert "invalid/misleading email address" in err_msg
