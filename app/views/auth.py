"""
Authentication Views for LoanGuard AI.

Provides login and logout class-based views with credential validation and session management.
"""

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View


class LoginView(View):
    """
    Class-Based Login View for LoanGuard AI.

    On GET: Displays sleek glassmorphic login form.
    On POST: Authenticates user credentials and redirects to single dashboard view (`/`).
    """

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("dashboard")

        context = {
            "title": "Login - LoanGuard AI",
            "error_message": None,
        }
        return render(request, "auth/login.html", context)

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("dashboard")

        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        error_message = None

        if not username or not password:
            error_message = _("Please enter both username and password.")
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, {user.first_name or user.username}!")
                return redirect("dashboard")
            else:
                error_message = _("Invalid username or password. Please check your credentials.")

        context = {
            "title": "Login - LoanGuard AI",
            "error_message": error_message,
        }
        return render(request, "auth/login.html", context)


class LogoutView(View):
    """
    Class-Based Logout View for LoanGuard AI.
    """

    def get(self, request):
        logout(request)
        messages.info(request, _("You have been signed out successfully."))
        return redirect("login")

    def post(self, request):
        return self.get(request)


login_view = LoginView.as_view()
logout_view = LogoutView.as_view()
