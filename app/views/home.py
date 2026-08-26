from django.shortcuts import render


def home(request):
    """
    Dummy Home Page View for LoanGuard AI.
    """
    context = {
        "title": "LoanGuard AI - Smart Risk Assessment & Fraud Detection",
        "description": "Next-generation AI-powered loan evaluation, risk analytics, and automated decision system.",
    }
    return render(request, "home.html", context)
