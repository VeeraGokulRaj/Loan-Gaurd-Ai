"""
Django Admin Configuration for LoanGuard AI User Management & Dynamic users.json Editing.

This module provides the UserAdmin interface with embedded support for managing users.json
dynamically from the Django Admin Panel.

Required Data Specifications for users.json:
    - `username` (str): Unique login identifier (Unique constraint enforced).
    - `first_name` (str): User's first name (e.g. Tamil Nadu touch: Murugan, Priya).
    - `last_name` (str): User's last name (e.g. Raman, Dharshini).
    - `email` (str): Valid email address.
    - `mobile` / `phone` (str): Contact mobile number.
    - `password` (str): Initial setup password (e.g. 'pass123').
    - `role` (str): Exactly one of:
        1. "Data Operator" (Category 1)
        2. "Reviewer"      (Category 2)
        3. "Data Consumer" (Category 3)

Error Handling Strategy:
    - Gracefully intercepts JSON parsing syntax errors.
    - Reports duplicate usernames across entries.
    - Catches missing required fields or empty whitespace strings.
    - Flags invalid or mismatched role category names with clear hints.
    - Displays errors in Django Admin error banners without crashing or corrupting data.
"""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.translation import gettext_lazy as _

from app.domain.user_service import (
    USER_MOCK_FILE_PATH,
    UserJsonValidator,
    UserSeederService,
    get_user_mock_file_path,
)
from app.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Custom Django Admin for User Model supporting dynamic users.json management.
    """

    list_display = (
        "username",
        "first_name",
        "last_name",
        "email",
        "phone",
        "get_category_badge",
        "is_staff",
        "is_superuser",
    )
    list_filter = ("category", "is_staff", "is_superuser", "is_active")
    search_fields = ("username", "first_name", "last_name", "email", "phone")
    ordering = ("username",)

    fieldsets = BaseUserAdmin.fieldsets + (
        (_("Role Category & Contact"), {"fields": ("category", "phone")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (_("Role Category & Contact"), {"fields": ("category", "phone")}),
    )

    actions = ["sync_database_from_users_json"]

    def get_category_badge(self, obj: User) -> str:
        """Returns pretty display string for user category role."""
        if obj.is_superuser:
            return "Superuser (System Admin)"
        return obj.get_category_display() or "No Role Assigned"

    get_category_badge.short_description = _("Category Role")

    def get_urls(self):
        """Adds custom URL route for managing users.json in Django Admin."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "manage-users-json/",
                self.admin_site.admin_view(self.manage_users_json_view),
                name="app_user_manage_users_json",
            ),
        ]
        return custom_urls + urls

    def _get_context(self, request, json_content: str):
        """Helper to construct rich context for manage_users_json template."""
        total_users = User.objects.filter(is_superuser=False).count()
        operator_count = User.objects.filter(category=User.Category.DATA_OPERATOR).count()
        reviewer_count = User.objects.filter(category=User.Category.REVIEWER).count()
        consumer_count = User.objects.filter(category=User.Category.DATA_CONSUMER).count()

        file_user_count = 0
        try:
            import json

            data = json.loads(json_content)
            if isinstance(data, list):
                file_user_count = len(data)
        except Exception:
            file_user_count = 0

        return {
            **self.admin_site.each_context(request),
            "title": _("Manage Mock Users Directory"),
            "json_content": json_content,
            "total_users": total_users,
            "operator_count": operator_count,
            "reviewer_count": reviewer_count,
            "consumer_count": consumer_count,
            "file_user_count": file_user_count,
            "user_mock_file_path": USER_MOCK_FILE_PATH,
            "opts": self.model._meta,
        }

    def manage_users_json_view(self, request):
        """
        Admin view to inspect, upload, edit, validate, and persist users.json.

        Handles validation gracefully and reports duplicate usernames, invalid roles,
        and missing fields via Django messages framework.
        """
        json_file_path = get_user_mock_file_path()

        if request.method == "POST":
            action = request.POST.get("action")

            if action in ["reload_file", "Reload File"]:
                messages.info(request, f"Reloaded {USER_MOCK_FILE_PATH} content from disk.")
                return redirect("admin:app_user_manage_users_json")

            raw_json = ""
            if request.FILES.get("json_file"):
                uploaded_file = request.FILES["json_file"]
                try:
                    raw_json = uploaded_file.read().decode("utf-8")
                except Exception as exc:
                    messages.error(request, f"Error reading uploaded file: {str(exc)}")
                    return redirect("admin:app_user_manage_users_json")
            else:
                raw_json = request.POST.get("json_content", "")

            # Perform validation
            valid_users, errors = UserJsonValidator.validate_raw_json(raw_json)

            if errors:
                for err in errors:
                    messages.error(request, f"Validation Error: {err}")
                return render(
                    request,
                    "admin/manage_users_json.html",
                    self._get_context(request, raw_json),
                )

            # If valid, save to target mock file & sync database
            try:
                saved_path = UserSeederService.save_to_file(valid_users)
                created_cnt, updated_cnt = UserSeederService.seed_database(valid_users)

                messages.success(
                    request,
                    f"Successfully validated & saved {len(valid_users)} users to {saved_path.as_posix()}! "
                    f"Database updated: {created_cnt} created, {updated_cnt} updated.",
                )
                return redirect("admin:app_user_changelist")
            except Exception as exc:
                messages.error(request, f"Failed to persist users: {str(exc)}")
                return render(
                    request,
                    "admin/manage_users_json.html",
                    self._get_context(request, raw_json),
                )

        # GET request: read mock file from disk
        initial_json = ""
        if json_file_path.exists():
            with open(json_file_path, encoding="utf-8") as f:
                initial_json = f.read()

        return render(
            request,
            "admin/manage_users_json.html",
            self._get_context(request, initial_json),
        )

    def sync_database_from_users_json(self, request, queryset):
        """Admin action to trigger DB sync directly from current USER_MOCK_FILE_PATH file."""
        json_file_path = get_user_mock_file_path()
        if not json_file_path.exists():
            messages.error(request, f"User mock file not found at {json_file_path}")
            return

        with open(json_file_path, encoding="utf-8") as f:
            raw_content = f.read()

        valid_users, errors = UserJsonValidator.validate_raw_json(raw_content)
        if errors:
            for err in errors:
                messages.error(request, f"❌ {err}")
            return

        created_cnt, updated_cnt = UserSeederService.seed_database(valid_users)
        messages.success(
            request,
            f"✅ Synced users.json successfully! {created_cnt} created, {updated_cnt} updated in database.",
        )

    sync_database_from_users_json.short_description = _(
        "🔄 Sync Database Users from users.json File"
    )
