# LoanGuard AI - Comprehensive Setup & Operating Guide

This document provides a clean, step-by-step setup guide for **LoanGuard AI** on both **Windows (Native/PowerShell)** and **Linux / WSL (Ubuntu/Debian)** environments.

---

## 1. Prerequisites

Before installing the application, ensure the following runtimes are installed:

- **Python 3.12+**: Available from [python.org](https://www.python.org/) or package manager (`sudo apt install python3.12 python3.12-venv`).
- **Node.js 18+ & npm**: Required for compiling Tailwind CSS.
- **uv** (Recommended): Fast Python package installer and dependency resolver.
  - **Linux / WSL**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - **Windows (PowerShell)**: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

---

## 2. Step-by-Step Implementation & Installation

### Step 1: Clone & Navigate to Repository

```bash
git clone https://github.com/VeeraGokulRaj/Loan-Gaurd-Ai.git
cd Loan-Gaurd-Ai
```

### Step 2: Virtual Environment Setup

#### Linux / WSL (Bash):
```bash
# Create virtual environment using uv
uv venv .venv --prompt loanguard_ai

# Activate environment (wsl)
source .venv/bin/activate
```

#### Windows (PowerShell):
```powershell
# Create virtual environment
uv venv .venv --prompt loanguard_ai

# Activate environment
.\.venv\Scripts\Activate.ps1
```

#### Windows (Command Prompt - CMD):
```cmd
uv venv .venv --prompt loanguard_ai
.\.venv\Scripts\activate.bat
```

### Step 3: Install & Sync Dependencies

```bash
# Sync all Python production and development dependencies
uv sync
```

*(Alternative standard pip setup: `pip install -r pyproject.toml` or `uv pip install -e .`)*

### Step 4: Configure Environment Variables

Create your local `.env` file from the provided example template (`.env.example`):

#### Linux / WSL:
```bash
cp .env.example .env
```

#### Windows (PowerShell):
```powershell
Copy-Item .env.example .env
```

#### `.env` Configuration Variables Reference:

| Environment Variable | Default / Example Value | Description & Purpose |
| :--- | :--- | :--- |
| `SECRET_KEY` | `21a728a$7s8v5%g^*isfvs-jl&dewix5@tm0@tq0+j4_yl-7` | Django secret key for cryptographic signing. |
| `DJANGO_DEBUG` | `True` | Debug flag (`True` for local dev tools & static files). |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Hostnames allowed to serve requests. |
| `SITE_URL` | `http://localhost:8000` | Application base URL. |
| `GEMINI_API_KEY` | `AQ.Ab8RN6KinFcdESnbdAahJu4XakRbfn3lDPtA-JMSp3QoXZ9p8w` | Google Gemini SDK API Key. |
| `ZEN_API_KEY` | `sk-BhPstlXVyNG2we5hLBMf9NBHmNxdJwZ5msRS411QzmXMJUUEtz2sqMS5ckYQnLw1` | OpenCode Zen AI API Key. |
| `ZEN_BASE_URL` | `https://opencode.ai/zen/v1` | OpenCode Zen API base endpoint. |
| `ZEN_MODEL_NAME` | `ling-3.0-flash-fin-free` | Active free-tier LLM model name. |

---

## 3. SQLite Concurrency Setup (`db.sqlite3-wal` & `db.sqlite3-shm`)

LoanGuard AI uses **SQLite** as its lightweight, zero-dependency database. Because HTMX triggers multiple asynchronous parallel HTTP requests (e.g. background metrics polling, inline field updates, and AI recommendations), standard SQLite page locking can cause `django.db.utils.OperationalError: database is locked`.

To resolve this HTMX concurrency lock issue, the database uses **Write-Ahead Logging (WAL) Mode**.

### Enabling WAL Mode (Execution Step):

Run database migrations first, then activate WAL mode:

```bash
# 1. Run migrations to initialize db.sqlite3
uv run python manage.py migrate

# 2. Enable Write-Ahead Logging (WAL) Mode
uv run python manage.py shell -c "import django; from django.db import connection; connection.cursor().execute('PRAGMA journal_mode=WAL;')"
```

### Purpose of WAL Auxiliary Files:

When WAL mode is active, SQLite creates two persistent auxiliary files alongside `db.sqlite3`:

1. **`db.sqlite3-wal` (Write-Ahead Log File)**:
   - Contains uncommitted write frames. Writes append to this log file sequentially rather than overwriting main database pages directly.
   - **Purpose:** Enables simultaneous reader queries while a write transaction is in progress, eliminating HTMX lock contention.
2. **`db.sqlite3-shm` (Shared Memory File)**:
   - Shared memory index mapping WAL log frames to database pages.
   - **Purpose:** Acts as a high-speed shared memory cache for concurrent reader processes to locate data in the WAL file without disk overhead.

---

## 4. Existing Management Commands & Purposes

LoanGuard AI provides custom Django management commands to automate seeding, permission syncing, and data generation:

| Management Command | Purpose & Description |
| :--- | :--- |
| **`uv run python manage.py seed_demo_users_data`** | Parses [`files/users.json`](file:///home/veera/Projects/loanguard_ai/files/users.json), seeds/updates pre-configured demo users (`op_murugan`, `rev_priya`, `con_meena`), assigns Django Groups (`Data Operator`, `Reviewer`, `Data Consumer`), and synchronizes category permissions. Supports optional `--file` parameter. |
| **`uv run python manage.py seed_validation_rules`** | Reads [`files/validation_rules.json`](file:///home/veera/Projects/loanguard_ai/files/validation_rules.json) and populates the 15 domain validation rules in the `ValidationRule` database table. Supports optional `--file` parameter. |
| **`uv run python manage.py sync_permissions`** | Initializes the 3 Django Groups and syncs category permissions across roles without altering user data. |
| **`uv run python manage.py generate_sample_csv`** | Generates synthetic sample CSV datasets (`loan_tape.csv`, `servicer_update.csv`, `document_manifest.csv`) for testing. Supports optional **`--rows <number>`** (e.g. `--rows 2000` or `--rows=2000`) and **`--output-dir <directory>`** (default: `files/csv`). |

### Complete Seeding Execution Order:

```bash
# 1. Sync permissions and roles
uv run python manage.py sync_permissions

# 2. Seed validation rules
uv run python manage.py seed_validation_rules

# 3. Seed demo users from files/users.json
uv run python manage.py seed_demo_users_data

# 4. (Optional) Generate synthetic sample CSV datasets with a custom row count
uv run python manage.py generate_sample_csv --rows 200

# 5. Create an Admin Superuser
uv run python manage.py createsuperuser
```

---

## 5. Seeded User Categories & Test Credentials

Seeded directly from [`files/users.json`](file:///home/veera/Projects/loanguard_ai/files/users.json), the following pre-configured accounts provide role-isolated testing:

| User Category / Role | Username | Email | Password | Allowed Capabilities & Scope |
| :--- | :--- | :--- | :--- | :--- |
| **Data Operator** | `op_murugan` | `murugan.operator@loanguard.tn` | `pass123` | Upload raw loan CSVs (`loan_tape.csv`, `servicer_update.csv`, `document_manifest.csv`), view ingestion summaries, inspect failed import rows, and execute the validation engine. |
| **Reviewer** | `rev_priya` | `priya.reviewer@loanguard.tn` | `pass123` | Access exception queue, filter by severity, trigger AI Copilot analysis, accept/edit/reject AI suggestions, edit allowed loan fields, and approve/reject loan records. |
| **Data Consumer** | `con_meena` | `meena.consumer@loanguard.tn` | `pass123` | View verified canonical loan dataset, inspect real-time Data Quality Score meter (0-100%), open SHA-256 audit trail timeline modal, and export verified datasets (CSV/JSON). |

---

## 6. Managing Credentials & Validation Rules via Django Admin

The system includes a fully configured Django Admin console for dynamic system administration.

- **Admin Console URL**: `http://127.0.0.1:8000/admin/`

### 1. Managing Users, Passwords & Role Groups:
1. Log in with a Superuser account.
2. Navigate to **Authentication and Authorization > Users** or **Groups**.
3. Select any user (e.g., `rev_priya`) to update profile information, change passwords using the password hash form, or reassign role groups (`Data Operator`, `Reviewer`, `Data Consumer`).

### 2. Dynamically Configuring Validation Rules:
1. Navigate to **App > Validation Rules** (`/admin/app/validationrule/`).
2. Select any rule (e.g., `VAL_008: Out-of-Range Interest Rate`).
3. You can dynamically:
   - Toggle **`is_active`** (`True`/`False`) to enable or disable the rule during batch runs without redeploying code.
   - Change **`severity`** (`Low`, `Medium`, `High`, `Critical`).
   - Modify threshold **`parameters`** (JSON) such as `min_rate` or `max_rate`.

---

## 7. Development Mode Behavior (`DJANGO_DEBUG=True`)

When `DJANGO_DEBUG=True` is enabled in `.env` during development:

- **Detailed Stack Traces**: Displays interactive, unhandled exception traceback pages in the browser instead of generic HTTP 500 error pages.
- **Static Asset Serving**: Serves static CSS, JS, and media assets directly through Django's development server (`django.contrib.staticfiles`).
- **Browser Auto-Reload**: Integrates `django-browser-reload` to automatically refresh the browser when template files (`.html`) or static CSS/JS assets change.
- **SQL Profiling Toolbar**: Activates `django-debug-toolbar` (accessible via sidebar tab in browser) to inspect SQL queries, execution time, HTTP headers, and template rendering context.

*(Note: Set `DJANGO_DEBUG=False` when deploying to production environments).*

---

## 8. Frontend Compilation & Server Launch

### Step 1: Build Frontend Assets

```bash
# Install NPM packages
npm install

# Compile Tailwind CSS output
npm run build:css
```

### Step 2: Start Development Server

```bash
uv run python manage.py runserver
```

### Step 3: Access Application

Open your browser and navigate to:
- **Application Portal**: `http://127.0.0.1:8000/`
- **Django Admin Portal**: `http://127.0.0.1:8000/admin/`

---

## 9. Running Unit Tests & Automated Test Suite

LoanGuard AI uses `pytest` and `pytest-django` for executing automated test suites.

### 1. Run Complete Test Suite Across All Modules
```bash
uv run pytest
```

### 2. Run All Tests in a Specific Subdirectory
```bash
# Run domain layer tests
uv run pytest tests/domain/

# Run model layer tests
uv run pytest tests/models/

# Run view layer tests
uv run pytest tests/views/
```

### 3. Run a Specific Test File
```bash
uv run pytest tests/domain/test_validation_engine.py
```

### 4. Run a Specific Test Class
```bash
uv run pytest tests/domain/test_validation_engine.py::TestSafeFloat
```

### 5. Run a Specific Test Method (Exact Method Example)
```bash
uv run pytest tests/domain/test_validation_engine.py::TestSafeFloat::test_currency_symbol_stripped
```

### Useful Pytest Command Flags:

- **Detailed Output**: `uv run pytest -v`
- **Show Console Output (`print` statements)**: `uv run pytest -s`
- **Filter Tests by Keyword**: `uv run pytest -k "test_currency"`
- **Concise Failure Tracebacks**: `uv run pytest --tb=short`
