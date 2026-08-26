# LoanGuard AI - Project Setup Guide

This document provides step-by-step instructions to set up, configure, and run the **LoanGuard AI** Django application.

---

## 1. Prerequisites

Ensure you have the following installed on your system:
- **Python 3.12+**
- **uv** (Fast Python package installer and resolver)

---

## 2. Virtual Environment & Dependencies Setup

Create a virtual environment using `uv` and sync all project dependencies:

```bash
# Initialize virtual environment with Python 3.12 and loan_guard_ai prompt
uv venv .venv --prompt loan_guard_ai

# Activate the virtual environment
source .venv/bin/activate

# Sync production and development dependencies
uv sync
```

---

## 3. Environment Variables

Copy the sample environment file to `.env`:

```bash
cp .env.example .env
```

Key environment variables in `.env`:
- `SECRET_KEY`: Django secret key
- `DJANGO_DEBUG`: Set to `True` for development
- `ALLOWED_HOSTS`: Comma-separated list of hostnames

---

## 4. Pre-commit Hooks Setup

Enable code formatting and linting pre-commit hooks (Ruff, formatting, YAML & template checks):

```bash
# Install git hooks
uv run pre-commit install
```

To manually trigger pre-commit checks across all files:
```bash
uv run pre-commit run --all-files
```

---

## 5. Database Migrations

Initialize the SQLite database (no PostgreSQL, Celery, or Redis dependencies):

```bash
uv run python manage.py migrate
```

Create a superuser (optional):
```bash
uv run python manage.py createsuperuser
```

---

## 6. Frontend Setup (Tailwind CSS & Preline UI)

Install npm packages and compile Tailwind CSS:

```bash
# Install frontend dependencies
npm install

# Build compiled CSS for production/distribution
npm run build:css

# Watch for CSS changes during development
npm run dev:css
```

---

## 7. Running the Development Server

Start the Django development server using the local settings module (`config.settings.local`):

```bash
uv run python manage.py runserver
```

Access the application in your web browser:
- Application URL: `http://127.0.0.1:8000/`
- Admin Portal: `http://127.0.0.1:8000/admin/`

---

## 8. Directory Architecture

```
loanguard_ai/
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── local.py
│   │   └── production.py
│   ├── asgi.py
│   ├── urls.py
│   └── wsgi.py
├── app/
│   ├── admin/
│   ├── domain/
│   ├── filters/
│   ├── forms/
│   ├── models/
│   ├── tests/
│   └── views/
├── templates/
│   ├── base.html
├── static/
│   ├── src/input.css
│   └── dist/css/output.css
├── .env.example
├── .pre-commit-config.yaml
├── pyproject.toml
├── package.json
└── manage.py
```
