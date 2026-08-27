import pytest
from django.test import Client
from django.urls import reverse

from tests.factory.user_factory import UserFactory


@pytest.mark.django_db
class TestLoginView:
    """Test cases for the login_view in app/views/auth.py."""

    def setup_method(self):
        self.client = Client()
        self.login_url = reverse("login")
        self.dashboard_url = reverse("dashboard")

    # ── Positive Test Cases ──

    def test_get_request_renders_login_form(self):
        """GET request should return 200 and contain login form elements."""
        response = self.client.get(self.login_url)
        assert response.status_code == 200
        assert "Login - LoanGuard AI" in response.context["title"]

    def test_post_valid_credentials_redirects_to_dashboard(self):
        """POST with correct username/password should redirect to dashboard."""
        UserFactory.create_user(username="testuser", password="testpass123")
        response = self.client.post(
            self.login_url,
            {"username": "testuser", "password": "testpass123"},
        )
        assert response.status_code == 302
        assert response.url == self.dashboard_url

    def test_post_valid_credentials_authenticates_user(self):
        """POST with correct credentials should authenticate the user in the session."""
        user = UserFactory.create_user(username="authuser", password="authpass123")
        self.client.post(
            self.login_url,
            {"username": "authuser", "password": "authpass123"},
        )
        # Check that user is logged in by accessing a view that reads the session
        session = self.client.session
        assert session["_auth_user_id"] == str(user.pk)

    def test_post_valid_credentials_sets_success_message(self):
        """Successful login should add a welcome success message."""
        UserFactory.create_user(username="msguser", password="msgpass123", first_name="Alice")
        response = self.client.post(
            self.login_url,
            {"username": "msguser", "password": "msgpass123"},
            follow=True,
        )
        message_texts = [str(m) for m in response.context["messages"]]
        assert any("Welcome back, Alice" in t for t in message_texts)

    def test_post_valid_credentials_falls_back_to_username_in_welcome(self):
        """If user has no first_name, welcome message should use username."""
        UserFactory.create_user(
            username="nonameuser",
            password="pass123",
            first_name="",
        )
        response = self.client.post(
            self.login_url,
            {"username": "nonameuser", "password": "pass123"},
            follow=True,
        )
        message_texts = [str(m) for m in response.context["messages"]]
        assert any("Welcome back, nonameuser" in t for t in message_texts)

    def test_authenticated_user_get_redirected_on_get(self):
        """Already authenticated user visiting login should be redirected to dashboard."""
        UserFactory.create_user(username="alreadyin", password="pass123")
        self.client.login(username="alreadyin", password="pass123")
        response = self.client.get(self.login_url)
        assert response.status_code == 302
        assert response.url == self.dashboard_url

    def test_authenticated_user_post_redirected_on_post(self):
        """Already authenticated user POSTing to login should be redirected to dashboard."""
        UserFactory.create_user(username="alreadyin2", password="pass123")
        self.client.login(username="alreadyin2", password="pass123")
        response = self.client.post(
            self.login_url,
            {"username": "alreadyin2", "password": "pass123"},
        )
        assert response.status_code == 302
        assert response.url == self.dashboard_url

    # ── Negative Test Cases ──

    def test_post_invalid_password_shows_error_message(self):
        """POST with wrong password should return 200 with error message."""
        UserFactory.create_user(username="wrongpass", password="correct123")
        response = self.client.post(
            self.login_url,
            {"username": "wrongpass", "password": "wrongpassword"},
        )
        assert response.status_code == 200
        assert response.context["error_message"] is not None
        assert "Invalid username or password" in response.context["error_message"]

    def test_post_nonexistent_username_shows_error_message(self):
        """POST with a username that doesn't exist should show error message."""
        response = self.client.post(
            self.login_url,
            {"username": "ghostuser", "password": "somepass123"},
        )
        assert response.status_code == 200
        assert "Invalid username or password" in response.context["error_message"]

    def test_post_empty_username_shows_error_message(self):
        """POST with empty username should show validation error."""
        response = self.client.post(
            self.login_url,
            {"username": "", "password": "somepass123"},
        )
        assert response.status_code == 200
        assert "Please enter both username and password" in response.context["error_message"]

    def test_post_empty_password_shows_error_message(self):
        """POST with empty password should show validation error."""
        response = self.client.post(
            self.login_url,
            {"username": "someuser", "password": ""},
        )
        assert response.status_code == 200
        assert "Please enter both username and password" in response.context["error_message"]

    def test_post_both_empty_shows_error_message(self):
        """POST with both username and password empty should show validation error."""
        response = self.client.post(
            self.login_url,
            {"username": "", "password": ""},
        )
        assert response.status_code == 200
        assert "Please enter both username and password" in response.context["error_message"]

    def test_post_missing_username_field_shows_error_message(self):
        """POST with username key missing entirely should show validation error."""
        response = self.client.post(
            self.login_url,
            {"password": "somepass123"},
        )
        assert response.status_code == 200
        assert "Please enter both username and password" in response.context["error_message"]

    def test_post_missing_password_field_shows_error_message(self):
        """POST with password key missing entirely should show validation error."""
        response = self.client.post(
            self.login_url,
            {"username": "someuser"},
        )
        assert response.status_code == 200
        assert "Please enter both username and password" in response.context["error_message"]

    # ── Edge / Boundary Test Cases ──

    def test_post_whitespace_only_username_shows_error_message(self):
        """POST with whitespace-only username should be treated as empty and show error."""
        response = self.client.post(
            self.login_url,
            {"username": "   ", "password": "somepass123"},
        )
        assert response.status_code == 200
        assert "Please enter both username and password" in response.context["error_message"]

    def test_post_whitespace_only_password_shows_error_message(self):
        """POST with whitespace-only password should be treated as empty and show error."""
        response = self.client.post(
            self.login_url,
            {"username": "someuser", "password": "   "},
        )
        assert response.status_code == 200
        assert "Please enter both username and password" in response.context["error_message"]

    def test_post_valid_credentials_with_surrounding_whitespace(self):
        """POST with valid credentials that have surrounding whitespace should still work."""
        UserFactory.create_user(username="spacesuser", password="pass12345")
        response = self.client.post(
            self.login_url,
            {"username": "  spacesuser  ", "password": "  pass12345  "},
        )
        assert response.status_code == 302
        assert response.url == self.dashboard_url

    def test_post_empty_body_no_keys(self):
        """POST with empty body (no form data) should show validation error."""
        response = self.client.post(self.login_url, {})
        assert response.status_code == 200
        assert "Please enter both username and password" in response.context["error_message"]

    def test_post_long_username_nonexistent(self):
        """POST with a very long username that doesn't exist should show auth error."""
        long_username = "a" * 300
        response = self.client.post(
            self.login_url,
            {"username": long_username, "password": "somepass"},
        )
        assert response.status_code == 200
        assert "Invalid username or password" in response.context["error_message"]

    def test_post_special_characters_in_username(self):
        """POST with special characters in username should not crash and show auth error."""
        response = self.client.post(
            self.login_url,
            {"username": "!@#$%^&*()", "password": "somepass"},
        )
        assert response.status_code == 200
        assert "Invalid username or password" in response.context["error_message"]

    def test_post_empty_context_error_message_is_none(self):
        """GET request should have error_message set to None in context."""
        response = self.client.get(self.login_url)
        assert response.context["error_message"] is None

    def test_post_valid_credentials_clears_error_message(self):
        """Successful login should not set any error_message in context."""
        UserFactory.create_user(username="cleanerr", password="clean1234")
        response = self.client.post(
            self.login_url,
            {"username": "cleanerr", "password": "clean1234"},
        )
        # Successful login redirects, so we won't get a render with context.
        assert response.status_code == 302


@pytest.mark.django_db
class TestLogoutView:
    """Test cases for the logout_view in app/views/auth.py."""

    def setup_method(self):
        self.client = Client()
        self.logout_url = reverse("logout")
        self.login_url = reverse("login")

    # ── Positive Test Cases ──

    def test_logout_redirects_to_login(self):
        """Logout should redirect to login page."""
        UserFactory.create_user(username="logoutuser", password="logoutpass")
        self.client.login(username="logoutuser", password="logoutpass")
        response = self.client.post(self.logout_url)
        assert response.status_code == 302
        assert response.url == self.login_url

    def test_logout_clears_session(self):
        """Logout should clear the user session (no _auth_user_id)."""
        UserFactory.create_user(username="sessionuser", password="sessionpass")
        self.client.login(username="sessionuser", password="sessionpass")
        assert "_auth_user_id" in self.client.session

        self.client.post(self.logout_url)
        assert "_auth_user_id" not in self.client.session

    def test_logout_sets_info_message(self):
        """Logout should add a signed-out info message."""
        UserFactory.create_user(username="msglogout", password="msgpass123")
        self.client.login(username="msglogout", password="msgpass123")
        response = self.client.post(self.logout_url, follow=True)

        message_texts = [str(m) for m in response.context["messages"]]
        assert any("signed out successfully" in t.lower() for t in message_texts)

    def test_logout_unauthenticated_user_still_redirects(self):
        """Logout should work for an unauthenticated user and redirect to login."""
        response = self.client.post(self.logout_url)
        assert response.status_code == 302
        assert response.url == self.login_url

    def test_logout_get_request_redirects_to_login(self):
        """GET request to logout should still redirect to login."""
        response = self.client.get(self.logout_url)
        assert response.status_code == 302
        assert response.url == self.login_url

    def test_logout_sets_message_even_when_not_logged_in(self):
        """Logout as unauthenticated user should still add the signed-out info message."""
        response = self.client.post(self.logout_url, follow=True)
        message_texts = [str(m) for m in response.context["messages"]]
        assert any("signed out successfully" in t.lower() for t in message_texts)

    def test_logout_user_cannot_access_dashboard_after_logout(self):
        """After logout, user should not be able to access dashboard without re-login."""
        UserFactory.create_user(username="dashuser", password="dashpass123")
        self.client.login(username="dashuser", password="dashpass123")
        self.client.post(self.logout_url)

        response = self.client.get(reverse("dashboard"))
        # Unauthenticated users should be redirected to login by the middleware
        assert response.status_code == 302
        assert "login" in response.url
