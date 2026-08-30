"""
URL configuration for loanguard_ai project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from app.views.auth import LoginView, LogoutView
from app.views.dashboard import dashboard_view
from app.views.data_operator import (
    BatchListView,
    ExecuteValidationView,
    FailedRowListView,
    IngestPipelineView,
)
from app.views.reviewer import (
    ExceptionActionHistoryView,
    ExceptionLoanDetailView,
    GenerateAIRecommendationView,
    GenerateAIRuleView,
    LoanExceptionListView,
    OpenAICopilotModalView,
    ProcessAIRecommendationView,
    ReviewerDashboardView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", dashboard_view, name="dashboard"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("ingest/pipeline/", IngestPipelineView.as_view(), name="ingest_pipeline"),
    path("ingest/batches/", BatchListView.as_view(), name="batch_list"),
    path("ingest/failed-rows/", FailedRowListView.as_view(), name="failed_row_list"),
    path(
        "validation/execute/",
        ExecuteValidationView.as_view(),
        name="execute_validation",
    ),
    path("reviewer/", ReviewerDashboardView.as_view(), name="reviewer_dashboard"),
    path(
        "reviewer/exceptions/",
        LoanExceptionListView.as_view(),
        name="loan_exceptions_list",
    ),
    path(
        "reviewer/exceptions/<int:pk>/detail/",
        ExceptionLoanDetailView.as_view(),
        name="exception_loan_detail",
    ),
    path(
        "reviewer/exceptions/<int:pk>/history/",
        ExceptionActionHistoryView.as_view(),
        name="exception_action_history",
    ),
    path(
        "reviewer/ai/modal/",
        OpenAICopilotModalView.as_view(),
        name="open_ai_copilot_modal_general",
    ),
    path(
        "reviewer/exceptions/<int:pk>/ai/modal/",
        OpenAICopilotModalView.as_view(),
        name="open_ai_copilot_modal",
    ),
    path(
        "reviewer/exceptions/<int:pk>/ai/generate/",
        GenerateAIRecommendationView.as_view(),
        name="generate_ai_recommendation",
    ),
    path(
        "reviewer/ai/rules/generate/",
        GenerateAIRuleView.as_view(),
        name="generate_ai_rule",
    ),
    path(
        "reviewer/ai/<int:pk>/decision/",
        ProcessAIRecommendationView.as_view(),
        name="process_ai_recommendation",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if "debug_toolbar" in settings.INSTALLED_APPS:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
    if "django_browser_reload" in settings.INSTALLED_APPS:
        urlpatterns += [path("__reload__/", include("django_browser_reload.urls"))]
