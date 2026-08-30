# Intain Campus FinTech Challenge 2026: Comprehensive Hackathon Specification & Implementation Guide

> **Document Purpose:** Complete, line-by-line coverage of the official competition prompt (`Intain_Full_Stack_Track_Problem_Statement.docx3b4003f.pdf`). Every requirement, module, data file, validation rule, AI control, and judging criterion is explained with **"What is it"** and **"How will I implement it"** using Python Django Full-Stack architecture.

---

## Table of Contents
1. [Challenge Overview & Context](#1-challenge-overview--context)
2. [System Architecture & Infrastructure Decisions (Demo vs. Production)](#2-system-architecture--infrastructure-decisions-demo-vs-production)
3. [Input Data Package & Sample Records](#3-input-data-package--sample-records)
4. [15 Intentional Data Quality Issues](#4-15-intentional-data-quality-issues)
5. [Module-by-Module Implementation Guide (Modules A to H)](#5-module-by-module-implementation-guide-modules-a-to-h)
6. [Required AI Controls & Human-in-the-Loop Guardrails](#6-required-ai-controls--human-in-the-loop-guardrails)
7. [Agentic Coding Requirement & AI Log Specification](#7-agentic-coding-requirement--ai-log-specification)
8. [Full Deliverables, Deliverable Credentials & Architecture Note](#8-full-deliverables-deliverable-credentials--architecture-note)
9. [Judging Criteria Breakdown (100 Points)](#9-judging-criteria-breakdown-100-points)
10. [5-Minute Demo Walkthrough Script](#10-5-minute-demo-walkthrough-script)
11. [Execution Roadmap & Hackathon Milestones](#11-execution-roadmap--hackathon-milestones)
12. [Explicit Out-of-Scope Items](#12-explicit-out-of-scope-items)

---

## 1. Challenge Overview & Context

### What is it?
Financial institutions depend on loan-level records (loan tapes) to evaluate portfolios. However, raw loan data arrives messy, inconsistent, and fragmented across legacy origination systems, servicing CSV exports, third-party APIs, and spreadsheets.

The **Loan Data Verification Copilot** is an AI-assisted full-stack console that ingests messy loan records, executes validation checks, uses AI to explain errors and resolve multi-source conflicts, enables human reviewer overrides, creates tamper-evident verified datasets with SHA-256 hashes, and maintains an immutable audit trail.

### How Will I Implement It?
- **Stack:** Python 3.12+ / Django 5.x / Django REST Framework (DRF) / HTMX / Vite + Tailwind CSS / SQLite.
- **Core Design:** Single monolithic Django application (`app/`) structured with a clean separation of concerns:
  - `app/models/`: Database models for raw data, rules, exceptions, AI responses, verified records, audit logs.
  - `app/domain/`: Pure business logic (Ingestion, Validation Engine, AI Integration, Hashing, Lineage).
  - `app/api/`: Django REST Framework endpoints for programmatic consumer access.
  - `app/views/`: Thin HTMX-powered controller views rendering dynamic role-based UI.

---

### Role-Based Hierarchy & Permission Matrix

To enforce strict separation of duties, the system defines 3 distinct roles using Django `Group` permissions. **Superuser accounts are explicitly restricted from bypassing role checks** during judging and testing to guarantee true role isolation.

#### 1. Permission Matrix Across 3 Roles:

| Feature / Action | Data Operator | Reviewer | Data Consumer |
| :--- | :---: | :---: | :---: |
| **Upload Raw CSV Files** (`loan_tape`, `servicer_update`, `document_manifest`) | ✅ **ALLOWED** | ❌ BLOCKED | ❌ BLOCKED |
| **View Ingestion Summary & Failed Import Rows** | ✅ **ALLOWED** | ❌ BLOCKED | ❌ BLOCKED |
| **Trigger Validation Engine** | ✅ **ALLOWED** | ❌ BLOCKED | ❌ BLOCKED |
| **Inspect Exception Queue & Filter by Severity** | ❌ BLOCKED | ✅ **ALLOWED** | ❌ BLOCKED |
| **Trigger AI Copilot (Gemini Explanations & Suggestions)** | ❌ BLOCKED | ✅ **ALLOWED** | ❌ BLOCKED |
| **Accept, Reject, or Edit AI Suggestions** | ❌ BLOCKED | ✅ **ALLOWED** | ❌ BLOCKED |
| **Edit Allowed Loan Fields & Add Comments** | ❌ BLOCKED | ✅ **ALLOWED** | ❌ BLOCKED |
| **Approve / Reject Loan Records -> Create Verified Record** | ❌ BLOCKED | ✅ **ALLOWED** | ❌ BLOCKED |
| **View Verified Loan Tape & Data Quality Score Meter** | ❌ BLOCKED | ❌ BLOCKED | ✅ **ALLOWED** |
| **Inspect Complete Audit Trail & SHA-256 Hashes** | ❌ BLOCKED | ❌ BLOCKED | ✅ **ALLOWED** |
| **Export Verified Dataset (CSV / JSON) & Query REST APIs** | ❌ BLOCKED | ❌ BLOCKED | ✅ **ALLOWED** |

#### 2. Implementation Strategy in Django:

- **Django Groups Initialization (`python manage.py seed_demo_data`):**
  Seeds 3 Groups in `django.contrib.auth.models.Group`: `Data Operator`, `Reviewer`, `Data Consumer`.

- **Web UI Permission Decorator (`app/utils/permissions.py`):**
```python
from functools import wraps
from django.core.exceptions import PermissionDenied

def role_required(*allowed_roles):
    """Enforces that user belongs strictly to allowed Django Groups without superuser bypass."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                raise PermissionDenied("Authentication required.")
            user_groups = set(request.user.groups.values_list('name', flat=True))
            if not user_groups.intersection(set(allowed_roles)):
                raise PermissionDenied(f"Access Denied: Requires one of roles {allowed_roles}")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
```

- **REST API Permissions (`app/api/permissions/roles.py`):**
```python
from rest_framework.permissions import BasePermission

class IsDataOperator(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.groups.filter(name='Data Operator').exists()

class IsReviewer(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.groups.filter(name='Reviewer').exists()

class IsDataConsumer(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.groups.filter(name='Data Consumer').exists()
```

---

## 2. System Architecture & Infrastructure Decisions (Demo vs. Production)

```
+-----------------------------------------------------------------------------------+
|                                 FRONTEND LAYER                                   |
|   Role-Based UI (Operator Dashboard | Reviewer Queue & AI Panel | Consumer View)  |
+-----------------------------------------------------------------------------------+
                                         |
                                  REST API / HTMX
                                         v
+-----------------------------------------------------------------------------------+
|                              DJANGO BACKEND LAYER                                 |
|  +--------------------+  +-----------------------+  +--------------------------+  |
|  | Ingestion Engine   |  | Config Validation     |  | AI Review Assistant      |  |
|  | (CSV Parsing,      |  | Engine (Rule Strategy |  | (Gemini 2.5 Flash, Prompt|  |
|  | Summary & Lineage) |  | & Exception Detector) |  | Audit, Conflict Resolver)|  |
|  +--------------------+  +-----------------------+  +--------------------------+  |
|  +--------------------+  +-----------------------+  +--------------------------+  |
|  | Exception Manager  |  | Verified Record       |  | Cryptographic Audit Log  |  |
|  | (Human Review,     |  | Engine (SHA-256 Hash, |  | (SHA-256 Hash Chained    |  |
|  | Manual Overrides)  |  | Canonical State)      |  | Append-Only Event Ledger)|  |
|  +--------------------+  +-----------------------+  +--------------------------+  |
+-----------------------------------------------------------------------------------+
                                         |
                                   Django ORM
                                         v
+-----------------------------------------------------------------------------------+
|                           LOCAL DEMO STORAGE LAYER                                |
|  SQLite (db.sqlite3) with Native JSONField Support & In-Memory LocMemCache        |
+-----------------------------------------------------------------------------------+
```

### Key Infrastructure Decisions for the Hackathon Demo

#### 1. Why the Hackathon Demo uses SQLite (`db.sqlite3`) instead of PostgreSQL:
- **Zero-Setup & Zero-Friction Judging:** Evaluators can clone the repo and run `python manage.py runserver` immediately without installing PostgreSQL, creating DB users, setting up passwords, or managing socket connections.
- **Native Django `JSONField` Support:** Modern Django natively supports `models.JSONField()` on SQLite via SQLite's built-in `json1` extension. Raw CSV payload storage and audit JSON logging work 100% identically to PostgreSQL.
- **In-Memory Speed for 5,000 Records:** Executing queries on a 5,000-row dataset in SQLite is faster than PostgreSQL because it eliminates local TCP/socket network round-trips.
- **Pre-Seeded Demo Database:** Shipped with a pre-seeded `db.sqlite3` file so all 3 role dashboards (**Data Operator**, **Reviewer**, **Data Consumer**) load instantly with rich sample data.

#### 2. Why the Hackathon Demo uses Synchronous In-Memory Processing instead of Celery & Redis:
- **Zero-Dependency Architecture:** Judges do not need to run a background `redis-server` daemon or manage a separate `celery worker` terminal process.
- **Sub-300ms Python Processing:** In Python, parsing 5,000 CSV lines and evaluating 15 validation rules in memory takes less than 300 milliseconds—making background queueing redundant for demo data volumes.
- **`CELERY_TASK_ALWAYS_EAGER = True` Strategy:** Task functions are structured in `app/tasks/` to demonstrate clean async architecture, but configured to run in-process for 100% demo reliability.
- **Built-in `LocMemCache` for Metrics:** Uses Django's built-in in-memory cache for Data Quality Score calculations without installing external Redis servers.

#### Production-Grade Django ORM Model Specs (`app/models/`):

Below is the complete database model schema specs utilizing `models.IntegerChoices` for clean type safety and enum performance:

```python
import uuid
import hashlib
import json
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

# 1. INGESTION & RAW DATA MODELS
class UploadBatch(TimeStampedModel):
    class SourceType(models.IntegerChoices):
        LOAN_TAPE = 1, 'Primary Loan Tape'
        SERVICER_UPDATE = 2, 'Servicer Update File'
        DOCUMENT_MANIFEST = 3, 'Document Manifest Ledger'


    class BatchStatus(models.IntegerChoices):
        PROCESSING = 1, 'Processing'
        INGESTED = 2, 'Ingested'
        PARTIAL_SUCCESS = 3, 'Partial Success'
        FAILED = 4, 'Failed'

    file_name = models.CharField(max_length=255, null=True, blank=True, help_text="Original filename of the uploaded CSV file.")
    source_type = models.IntegerField(choices=SourceType.choices, default=SourceType.LOAN_TAPE, null=True, blank=True, help_text="Type/category of the uploaded source file.")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='upload_batches', help_text="User (Data Operator) who uploaded this batch.")

    total_records = models.IntegerField(default=0, null=True, blank=True, help_text="Total number of data rows present in the CSV file.")
    successful_records = models.IntegerField(default=0, null=True, blank=True, help_text="Count of rows successfully parsed and stored.")
    failed_records = models.IntegerField(default=0, null=True, blank=True, help_text="Count of rows that failed initial CSV parsing.")
    status = models.IntegerField(choices=BatchStatus.choices, default=BatchStatus.PROCESSING, null=True, blank=True, help_text="Execution status of the upload batch.")


class RawLoanRecord(TimeStampedModel):
    batch = models.ForeignKey(UploadBatch, on_delete=models.CASCADE, null=True, blank=True, related_name='raw_records', help_text="Associated upload batch.")
    row_number = models.IntegerField(null=True, blank=True, help_text="Lineage: Line number in source CSV")
    raw_data = models.JSONField(default=dict, null=True, blank=True, help_text="Uncleaned raw string dictionary payload preserving 100% of original CSV columns.")
    source_system = models.CharField(max_length=100, null=True, blank=True, help_text="Source system or file discriminator.")


class FailedImportRow(TimeStampedModel):
    batch = models.ForeignKey(UploadBatch, on_delete=models.CASCADE, null=True, blank=True, related_name='failed_rows', help_text="Associated upload batch.")
    row_number = models.IntegerField(null=True, blank=True, help_text="Line number in source CSV where parsing failed.")
    raw_line = models.TextField(null=True, blank=True, help_text="Unparsed raw text of the failing CSV line.")
    failure_reason = models.TextField(null=True, blank=True, help_text="Detailed error message explaining failure reason.")


class ServicerUpdateRecord(TimeStampedModel):
    batch = models.ForeignKey(UploadBatch, on_delete=models.CASCADE, null=True, blank=True, related_name='servicer_records', help_text="Associated upload batch.")
    loan_id = models.CharField(max_length=100, db_index=True, null=True, blank=True, help_text="Unique loan identifier matching primary loan tape.")
    updated_current_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Latest current balance reported by servicer.")
    updated_payment_status = models.CharField(max_length=50, null=True, blank=True, help_text="Payment status reported by servicer.")
    updated_days_past_due = models.IntegerField(null=True, blank=True, help_text="Days past due counter reported by servicer.")
    last_payment_date = models.DateField(null=True, blank=True, help_text="Date of last recorded payment.")
    servicer_as_of_date = models.DateField(null=True, blank=True, help_text="As-of date of servicer update.")


class DocumentManifestRecord(TimeStampedModel):
    batch = models.ForeignKey(UploadBatch, on_delete=models.CASCADE, null=True, blank=True, related_name='document_manifest_records', help_text="Associated upload batch.")
    loan_id = models.CharField(max_length=100, db_index=True, null=True, blank=True, help_text="Unique loan identifier matching primary loan tape.")
    promissory_note_present = models.BooleanField(default=False, null=True, blank=True, help_text="Flag indicating if Promissory Note is present.")
    id_proof_present = models.BooleanField(default=False, null=True, blank=True, help_text="Flag indicating if identity proof is present.")
    income_verification_present = models.BooleanField(default=False, null=True, blank=True, help_text="Flag indicating if income proof is present.")
    document_verification_status = models.CharField(max_length=50, default='MISSING', null=True, blank=True, help_text="Overall document verification status.")




# 2. VALIDATION & EXCEPTION WORKFLOW MODELS
class ValidationSeverity(models.IntegerChoices):
    LOW = 1, 'Low'
    MEDIUM = 2, 'Medium'
    HIGH = 3, 'High'
    CRITICAL = 4, 'Critical'


class ValidationRule(models.Model):
    rule_code = models.CharField(max_length=50, unique=True, help_text="Display identifier e.g. VAL_001 or VL-0001")
    strategy_key = models.CharField(max_length=50, db_index=True, blank=True, help_text="Explicit strategy handler key e.g. MISSING_LOAN_ID")
    rule_name = models.CharField(max_length=255)
    field_name = models.CharField(max_length=100)
    description = models.TextField()
    severity = models.IntegerField(choices=ValidationSeverity.choices, default=ValidationSeverity.MEDIUM)
    is_active = models.BooleanField(default=True)
    parameters = models.JSONField(default=dict, blank=True, help_text="Configurable rule threshold parameters")


class LoanException(models.Model):
    class ExceptionStatus(models.IntegerChoices):
        OPEN = 1, 'Open'
        UNDER_REVIEW = 2, 'Under Review'
        RESOLVED_ACCEPTED = 3, 'Resolved (Accepted)'
        RESOLVED_EDITED = 4, 'Resolved (Edited)'
        REJECTED = 5, 'Rejected'

    batch = models.ForeignKey(UploadBatch, on_delete=models.CASCADE, related_name='exceptions')
    raw_record = models.ForeignKey(RawLoanRecord, on_delete=models.CASCADE, related_name='exceptions')
    rule_code = models.CharField(max_length=50, db_index=True)
    field_name = models.CharField(max_length=100)
    severity = models.IntegerField(choices=ValidationSeverity.choices, default=ValidationSeverity.MEDIUM)
    description = models.TextField()
    status = models.IntegerField(choices=ExceptionStatus.choices, default=ExceptionStatus.OPEN)

    reviewer_comment = models.TextField(blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)


# 3. AI REVIEW ASSISTANT MODEL
class AIRecommendation(models.Model):
    class RecommendationStatus(models.IntegerChoices):
        PENDING = 1, 'Pending Review'
        ACCEPTED = 2, 'Accepted'
        REJECTED = 3, 'Rejected'
        EDITED = 4, 'Edited'

    exception = models.ForeignKey(LoanException, on_delete=models.CASCADE, related_name='ai_recommendations')
    suggested_value = models.CharField(max_length=255)
    explanation = models.TextField()
    confidence_score = models.FloatField(default=0.0)

    prompt_text = models.TextField()
    model_name = models.CharField(max_length=100, default='gemini-2.5-flash')
    raw_response = models.TextField()
    status = models.IntegerField(choices=RecommendationStatus.choices, default=RecommendationStatus.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)


# 4. VERIFIED LOAN RECORD & AUDIT LEDGER
class VerifiedLoanRecord(models.Model):
    raw_record = models.OneToOneField(RawLoanRecord, on_delete=models.CASCADE, related_name='verified_record')
    ai_recommendation_used = models.ForeignKey(AIRecommendation, on_delete=models.SET_NULL, null=True, blank=True)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    loan_id = models.CharField(max_length=100, db_index=True, unique=True)
    borrower_id = models.CharField(max_length=100, db_index=True)
    canonical_data = models.JSONField(help_text="Cleaned, standardized loan JSON payload")

    record_hash = models.CharField(max_length=64, help_text="SHA-256 hash over canonical payload + timestamp")
    verified_at = models.DateTimeField(default=timezone.now)


class AuditEvent(models.Model):
    class ActorRole(models.IntegerChoices):
        SYSTEM = 1, 'System Engine'
        DATA_OPERATOR = 2, 'Data Operator'
        REVIEWER = 3, 'Reviewer'
        AI_COPILOT = 4, 'AI Copilot'
        DATA_CONSUMER = 5, 'Data Consumer'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    loan_id = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    batch_id = models.IntegerField(null=True, blank=True)

    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    actor_role = models.IntegerField(choices=ActorRole.choices, default=ActorRole.SYSTEM)

    event_type = models.CharField(max_length=100, db_index=True)
    payload = models.JSONField(default=dict)

    prev_hash = models.CharField(max_length=64)
    event_hash = models.CharField(max_length=64)
```

#### Production Scaling Strategy (Enterprise Deployment Note):
> *While the hackathon demo uses SQLite and synchronous task execution for zero-dependency local judging, switching to production scale requires zero code changes: toggle `ENGINE: 'django.db.backends.postgresql'` in `config/production.py` and set `USE_CELERY=True` with a Redis broker URL.*

---

## 2. Input Data Package & Sample Records

The organizer package includes **6 key files**. Below is the exact format and sample records for each file type:

### File 1: `loan_tape.csv` (Primary Dataset)
- **What is it:** Primary input CSV file containing 1,000 to 5,000 raw, uncleaned loan records with attributes like borrower ID, principal, balance, interest rate, dates, and status.
- **How will I implement it:** Uploaded via Data Operator UI (`GET/POST /ingest/`), read line-by-line using Python `csv.DictReader`, stored as raw JSON in `RawLoanRecord` table with upload batch ID.

#### Sample `loan_tape.csv` Content:
```csv
loan_id,borrower_id,loan_type,origination_date,maturity_date,original_principal,current_balance,interest_rate,term_months,borrower_state,loan_purpose,credit_grade,employment_length,income_band,payment_status,days_past_due,servicer_name,last_payment_date,last_updated_at,document_status,source_system
LN-1001,BW-5001,PERSONAL,2023-01-15,2028-01-15,25000.00,18500.50,6.50,60,CA,DEBT_CONSOLIDATION,A,5 years,$75k-$100k,CURRENT,0,ApexServicing,2024-07-15,2024-08-01,VERIFIED,LOS_ALPHA
LN-1002,BW-5002,AUTO,2024-05-10,2022-05-10,15000.00,16200.00,8.25,48,NY,AUTO_PURCHASE,B,2 years,$50k-$75k,CLOSED,45,BetaServicing,2024-06-10,2023-01-01,MISSING,SERVICER_CSV
LN-1003,BW-5003,MORTGAGE,2022-11-01,2052-11-01,-5000.00,450000.00,45.00,360,ZZ,HOME_PURCHASE,C,10+ years,$100k+,LATE_30,0,GammaServicing,2024-05-01,2024-08-10,PENDING,ORIGINATION_API
,BW-5004,STUDENT,2023-08-20,2033-08-20,30000.00,28000.00,5.75,120,TX,EDUCATION,A,1 year,$25k-$50k,CURRENT,0,ApexServicing,2024-07-20,2024-08-05,VERIFIED,MANUAL_EXCEL
```

---

### File 2: `servicer_update.csv` (Second-Source Update File)
- **What is it:** Secondary servicer extract providing updated or conflicting loan balances, payment statuses, and last payment dates to verify against `loan_tape.csv`.
- **How will I implement it:** Ingested into `ServicerUpdateRecord` table; linked during validation by `loan_id` to flag data discrepancies.

#### Sample `servicer_update.csv` Content:
```csv
loan_id,servicer_id,updated_current_balance,updated_payment_status,updated_days_past_due,last_payment_date,servicer_as_of_date
LN-1001,SERV-01,18500.50,CURRENT,0,2024-07-15,2024-08-15
LN-1002,SERV-02,12100.00,LATE_45,45,2024-07-10,2024-08-15
LN-1003,SERV-03,448000.00,LATE_30,30,2024-06-15,2024-08-15
```

---

### File 3: `document_manifest.csv` (Mock Document Availability)
- **What is it:** Ledger tracking physical/digital document availability (Promissory Note, ID Proof, Income Verification) for each loan.
- **How will I implement it:** Ingested into `DocumentManifest` model; cross-checked by Rule 10 (`MISSING_DOCUMENT_STATUS`).

#### Sample `document_manifest.csv` Content:
```csv
loan_id,promissory_note_present,id_proof_present,income_verification_present,document_verification_status,uploaded_at
LN-1001,TRUE,TRUE,TRUE,VERIFIED,2023-01-16
LN-1002,TRUE,FALSE,TRUE,INCOMPLETE,2024-05-11
LN-1003,FALSE,FALSE,FALSE,MISSING,2022-11-02
```

---

### File 4: `validation_rules.json` (Configurable Validation Rules)
- **What is it:** Configurable JSON file defining validation thresholds, severities, and error descriptions.
- **How will I implement it:** Loaded at startup into `ValidationRule` ORM model, allowing administrators to add or adjust validation rules dynamically without code redeployment.

#### Sample `validation_rules.json` Content:
```json
[
  {
    "rule_code": "VAL_001",
    "strategy_key": "MISSING_LOAN_ID",
    "rule_name": "Missing Loan ID",
    "field": "loan_id",
    "severity": "CRITICAL",
    "description": "Loan record must possess a non-null, non-empty primary identifier."
  },
  {
    "rule_code": "VAL_005",
    "strategy_key": "MATURITY_BEFORE_ORIGINATION",
    "rule_name": "Maturity Before Origination",
    "field": "maturity_date",
    "severity": "HIGH",
    "description": "Maturity date must be chronologically after the origination date."
  },
  {
    "rule_code": "VAL_007",
    "strategy_key": "BALANCE_EXCEEDS_PRINCIPAL",
    "rule_name": "Balance Exceeds Principal",
    "field": "current_balance",
    "severity": "HIGH",
    "description": "Current outstanding balance cannot exceed the original principal loan amount."
  },
  {
    "rule_code": "VAL_015",
    "strategy_key": "CLOSED_LOAN_POSITIVE_BALANCE",
    "rule_name": "Closed Loan Positive Balance",
    "field": "payment_status",
    "severity": "CRITICAL",
    "description": "Loans marked as CLOSED must have a current balance of zero."
  }
]
```

---

### File 5: `users.json` (Mock Users & Roles)
- **What is it:** Mock user directory providing pre-seeded accounts for role-based testing.
- **How will I implement it:** Loaded via `manage.py seed_users` command into Django `User` and `Group` models with pre-set password authentication.

#### Sample `users.json` Content:
```json
[
  {
    "username": "operator_jane",
    "email": "jane.operator@intain.com",
    "role": "Data Operator",
    "first_name": "Jane",
    "last_name": "Doe"
  },
  {
    "username": "reviewer_alex",
    "email": "alex.reviewer@intain.com",
    "role": "Reviewer",
    "first_name": "Alex",
    "last_name": "Smith"
  },
  {
    "username": "consumer_sam",
    "email": "sam.consumer@intain.com",
    "role": "Data Consumer",
    "first_name": "Sam",
    "last_name": "Taylor"
  }
]
```

---

### File 6: `expected_exception_sample.csv` (Orientation Exceptions)
- **What is it:** Sample file containing known benchmark exceptions used to verify that the validation engine flags expected errors correctly.
- **How will I implement it:** Used in automated unit tests (`tests/domain/test_validation.py`) to assert 100% detection recall.

#### Sample `expected_exception_sample.csv` Content:
```csv
loan_id,expected_rule_code,expected_severity,reason
LN-1002,VAL_005,HIGH,Maturity date 2022-05-10 is prior to origination date 2024-05-10
LN-1002,VAL_015,CRITICAL,Loan status is CLOSED but balance is 16200.00
LN-1003,VAL_006,HIGH,Original principal is negative (-5000.00)
LN-1003,VAL_008,MEDIUM,Interest rate 45.00% exceeds max threshold 35.00%
```

---

## 3. 15 Intentional Data Quality Issues

The system must automatically detect all 15 intentional data flaws described in Section 7 of the problem statement:

| # | Data Issue Name | **What is it?** | **How will I implement it? (Python Django)** |
|---|---|---|---|
| **1** | **Missing Loan IDs** | Loan row lacks primary `loan_id` identifier. | Check `if not row.get('loan_id')` -> Flag `VAL_001` (`CRITICAL`). |
| **2** | **Duplicate Loan IDs** | Two or more rows share identical `loan_id`. | Maintain `seen_ids` set during batch run; duplicate occurrences flag `VAL_002` (`HIGH`). |
| **3** | **Duplicate Borrower Triplets** | Matching `(borrower_id, original_principal, origination_date)`. | Hash composite tuple `(borrower_id, principal, orig_date)`; flag duplicates as potential double-entry (`VAL_003`). |
| **4** | **Invalid Date Formats** | Unparseable date strings (e.g., `31/02/2023`, `2024-13-45`). | Attempt parsing via `python-dateutil.parser.parse()`; catch `ValueError/OverflowError` -> flag `VAL_004`. |
| **5** | **Maturity Before Origination** | Loan maturity date is earlier than origination date. | Compare `maturity_date <= origination_date` -> flag `VAL_005` (`HIGH`). |
| **6** | **Negative Principal Balance** | `original_principal` or `current_balance` < 0. | Evaluate `float(val) < 0` -> flag `VAL_006` (`CRITICAL`). |
| **7** | **Balance > Original Principal** | `current_balance` exceeds `original_principal`. | Evaluate `current_balance > original_principal` -> flag `VAL_007` (`HIGH`). |
| **8** | **Out-of-Range Interest Rate** | Interest rate < 0.0% or > 35.0% APY. | Evaluate `rate < 0 or rate > 35.0` -> flag `VAL_008` (`MEDIUM`). |
| **9** | **Status vs DPD Inconsistency** | `payment_status == 'CURRENT'` but `days_past_due > 30`. | Check logical mismatch between status string and DPD integer -> flag `VAL_009`. |
| **10**| **Missing Document Status** | Required docs marked missing in manifest or loan tape. | Reconcile with `DocumentManifest` table; flag if `document_verification_status != 'VERIFIED'`. |
| **11**| **Servicer Conflict** | Mismatch between `loan_tape.csv` balance and `servicer_update.csv`. | Compare balance delta `abs(tape_bal - servicer_bal) > $1.00` -> flag `VAL_011`. |
| **12**| **Stale Record Detection** | `last_updated_at` is older than 180 days. | Evaluate `(today - last_updated_at).days > 180` -> flag `VAL_012` (`LOW`). |
| **13**| **Invalid State Code** | State code not in US 50 state list (e.g. `ZZ`, `99`). | Validate against standard `US_STATES` set (e.g., `CA`, `NY`, `TX`) -> flag `VAL_013`. |
| **14**| **Suspicious Borrower Duplication** | Single `borrower_id` associated with > 5 new loans in 30 days. | Query DB window count for `borrower_id` -> flag fraud warning `VAL_014`. |
| **15**| **Closed Loan Positive Balance** | `payment_status == 'CLOSED'` but `current_balance > 0`. | Evaluate status == `CLOSED` and `balance > 0` -> flag `VAL_015` (`CRITICAL`). |

---

## 4. Module-by-Module Implementation Guide (Modules A to H)

### Module A: Data Ingestion Engine
- **What is it:** Ingestion pipeline that receives uploaded CSV files (`loan_tape.csv`, `servicer_update.csv`, `document_manifest.csv`), parses records, stores raw un-normalized data, generates an upload summary, isolates failed import rows, and preserves complete source-file lineage.
- **How will I implement it:**
  - Build `IngestionService` in `app/domain/ingestion.py`.
  - Create an `UploadBatch` model instance for **every uploaded file** with a `source_type` discriminator field (`LOAN_TAPE`, `SERVICER_UPDATE`, `DOCUMENT_MANIFEST`).
  - Save raw uncleaned dictionaries into `RawLoanRecord` model with `batch_id`, `row_number`, `raw_data` (JSON), and `source_system`.
  - Provide immediate JSON/HTMX response with full upload metrics and isolated error rows.

#### Detailed Feature Breakdown for Module A:

1. **Upload CSV & Parse Records:**
   - **What is it:** Drag-and-drop CSV upload endpoint capable of reading multiple files simultaneously (`loan_tape.csv`, `servicer_update.csv`, `document_manifest.csv`). Handles encoding edge-cases (BOM UTF-8, ISO-8859-1).
   - **How will I implement it:** Django `MultiPartParser` handling file streams, parsed via Python `csv.DictReader` into memory buffers.

2. **Store Raw Uploaded Data:**
   - **What is it:** Storing uncleaned data exactly as received prior to schema transformation to guarantee zero data loss.
   - **How will I implement it:** `RawLoanRecord` model with `raw_data = models.JSONField()` storing exact string values per row.

3. **Normalize Records into Internal Schema:**
   - **What is it:** Standardizing field names, stripping whitespace, casting data types (floats, dates, uppercase status codes).
   - **How will I implement it:** `IngestionService.normalize_record()` maps raw dictionary keys to canonical Django model fields.

4. **Show Upload Summary:**
   - **What is it:** High-level dashboard banner and API payload displaying real-time ingestion metrics after file upload.
   - **Metrics Shown:** Total rows uploaded, successfully ingested rows, failed/malformed import rows, batch execution time (ms), timestamp, and batch status (`INGESTED`, `PARTIAL_SUCCESS`, `FAILED`).
   - **How will I implement it:** `UploadBatch` model aggregates `total_records`, `successful_records`, and `failed_records`, rendered instantly on the Data Operator dashboard.

5. **Identify Failed Import Rows:**
   - **What is it:** Isolating rows that failed basic structural CSV parsing (e.g., mismatched column counts, unescaped quotes, corrupt character encoding, missing row-level identifiers) before passing to the validation engine.
   - **How will I implement it:** Catch `csv.Error` or row parsing exceptions during ingestion; record failed row indices in `FailedImportRow` model storing `row_number`, `raw_line_text`, and `failure_reason` so operators can inspect and fix raw files.

6. **Preserve Source-File Lineage:**
   - **What is it:** End-to-end audit traceability linking every downstream `LoanException`, `AIRecommendation`, and `VerifiedLoanRecord` back to its exact origin (`batch_id`, `filename`, `source_system`, and original CSV `row_number`).
   - **How will I implement it:** Foreign keys on `RawLoanRecord` (`batch_id`, `row_number`, `source_system`). Every `VerifiedLoanRecord` stores `source_batch_id` and `source_row_number` so any auditor can trace a verified record back to line 142 of `loan_tape_2026_08_25.csv`.

7. **Handle Missing Required Fields (Non-Blocking Ingestion + Rule Flagging):**
   - **What is it:** Safe non-blocking database ingestion for CSV rows containing missing or null required fields (e.g. blank `loan_id`, missing `current_balance`), combined with automated validation rule flags.
   - **How will I implement it:** Models use `null=True, blank=True` and default values (e.g. `MISSING`). Raw payloads preserve exact string nulls. Post-ingestion, Module B validation rules (`VR-001`, `VR-002`, `VR-005`, `VR-008`) detect missing fields and populate `LoanException` entries.

8. **Handle Unhandled / Unexpected Extra Columns (Zero-Data-Loss JSON):**
   - **What is it:** Capturing extra, unmapped, or custom CSV columns sent by servicers or third parties without throwing schema errors or dropping data.
   - **How will I implement it:** 100% of original CSV columns are retained in `RawLoanRecord.raw_data` (`JSONField`), eliminating redundant data duplication while preserving complete raw payload lineage.


9. **Handle Unmentioned / Unexpected Document File Types:**
   - **What is it:** Identifying and handling file uploads that do not match standard file signatures (`loan_tape`, `servicer_update`, `document_manifest`).
   - **How will I implement it:** Header signature inspection assigns `UploadBatch.SourceType.UNKNOWN` (4) with `BatchStatus.FAILED`. Detailed failure reasons are recorded in `FailedImportRow` and audited via `AuditEvent.log_event("FILE_UPLOAD_REJECTED", ...)`.


```python
# Pseudo-Code: Ingestion Engine with Summary, Error Row Isolation & Lineage (app/domain/ingestion.py)
import csv
from django.db import transaction
from app.models import UploadBatch, RawLoanRecord, FailedImportRow, AuditEvent

class IngestionService:
    @classmethod
    @transaction.atomic
    def process_csv_upload(cls, file_obj, user, source_system="loan_tape") -> dict:
        batch = UploadBatch.objects.create(
            file_name=file_obj.name,
            uploaded_by=user,
            source_system=source_system,
            status="PROCESSING"
        )

        lines = file_obj.read().decode('utf-8-sig', errors='replace').splitlines()
        reader = csv.DictReader(lines)

        successful_records = []
        failed_rows = []

        for row_idx, row in enumerate(reader, start=2): # Line 1 is header
            try:
                # Validate minimal row structure
                if not row or all(v is None or v.strip() == "" for v in row.values()):
                    failed_rows.append(FailedImportRow(
                        batch=batch, row_number=row_idx, raw_line=str(row),
                        failure_reason="Empty row or missing values across all columns"
                    ))
                    continue

                successful_records.append(RawLoanRecord(
                    batch=batch,
                    row_number=row_idx, # Lineage: Line number in source CSV
                    raw_data=dict(row), # Exact raw payload
                    source_system=source_system
                ))
            except Exception as parse_err:
                failed_rows.append(FailedImportRow(
                    batch=batch, row_number=row_idx, raw_line=str(row),
                    failure_reason=f"Parse Error: {str(parse_err)}"
                ))

        # Bulk persistence
        RawLoanRecord.objects.bulk_create(successful_records)
        FailedImportRow.objects.bulk_create(failed_rows)

        # Update Upload Summary Metrics
        batch.total_records = len(lines) - 1
        batch.successful_records = len(successful_records)
        batch.failed_records = len(failed_rows)
        batch.status = "INGESTED" if len(failed_rows) == 0 else "PARTIAL_SUCCESS"
        batch.save()

        # Audit Lineage Event
        AuditEvent.log_event(
            event_type="FILE_UPLOADED", actor=user,
            payload={
                "batch_id": batch.id, "file_name": file_obj.name,
                "total_rows": batch.total_records, "success_count": batch.successful_records,
                "failed_count": batch.failed_records
            }
        )

        return {
            "batch_id": batch.id, "file_name": batch.file_name, "total_records": batch.total_records,
            "successful_records": batch.successful_records, "failed_records": batch.failed_records,
            "failed_rows_detail": [{"row": fr.row_number, "reason": fr.failure_reason} for fr in failed_rows]
        }
```

---

### Module B: Configurable Validation Engine
- **What is it:** Automated rule execution framework that scans ingested records against configurable rules loaded into `ValidationRule` ORM model and populates the `LoanException` model.
- **Why use BOTH `ValidationRule` (DB Model) AND Python Strategy Classes?**
  This is an intentional enterprise design pattern (**Strategy Pattern + Dynamic Metadata Registry**):
  - **`ValidationRule` (DB Model):** Holds configurable metadata (`rule_code`, `field_name`, `severity`, `is_active`, `description`). This allows administrators or hackathon judges to toggle rules on/off or change severity thresholds (`CRITICAL` vs `HIGH`) dynamically in the UI/Admin without redeploying code.
  - **Strategy Classes (`app/domain/validation.py`):** Encapsulates the actual Python code execution for complex checks (e.g. date arithmetic, DPD vs status logic, multi-file servicer reconciliation).
  - **Connecting Execution:** `ValidationEngine` checks active DB rules (`ValidationRule.objects.filter(is_active=True)`), maps `rule_code` to the matching Strategy class, and applies DB-configured severity to generated `LoanException` entries.

```python
# Pseudo-Code: Validation Strategy Pattern + DB Config Connection (app/domain/validation.py)
class BaseRule:
    rule_code = ""
    def validate(self, record: RawLoanRecord) -> Optional[RuleResult]:
        raise NotImplementedError

class ClosedLoanPositiveBalanceRule(BaseRule):
    rule_code = "VAL_015"
    def validate(self, record):
        data = record.raw_data
        if data.get("payment_status", "").upper() == "CLOSED" and float(data.get("current_balance", 0)) > 0:
            return RuleResult(
                is_valid=False, field_name="current_balance", rule_code=self.rule_code,
                message=f"Closed loan contains positive balance (${data.get('current_balance')})."
            )
        return None

class ValidationEngine:
    # Rule registry mapping rule_code -> Strategy class instance
    STRATEGY_MAP = {
        "VAL_015": ClosedLoanPositiveBalanceRule(),
        # ... Other rules 1 through 15 ...
    }

    @classmethod
    def validate_batch(cls, batch):
        # 1. Fetch active rule configurations from Database
        active_db_rules = {r.rule_code: r for r in ValidationRule.objects.filter(is_active=True)}
        exceptions_to_create = []

        # 2. Evaluate records against active strategies
        for record in RawLoanRecord.objects.filter(batch=batch):
            for rule_code, strategy in cls.STRATEGY_MAP.items():
                db_rule = active_db_rules.get(rule_code)
                if not db_rule:
                    continue # Skip if rule was disabled in DB/UI

                res = strategy.validate(record)
                if res and not res.is_valid:
                    exceptions_to_create.append(
                        LoanException(
                            batch=batch, raw_record=record, rule_code=rule_code,
                            field_name=res.field_name, description=res.message,
                            severity=db_rule.severity, # Use DB-configured severity!
                            status=LoanException.ExceptionStatus.OPEN
                        )
                    )
        LoanException.objects.bulk_create(exceptions_to_create)
```

---

### Module C: Exception Queue & Reviewer Workflow
- **What is it:** Interactive workspace for Reviewers to inspect, search, filter, comment on, edit, and resolve exceptions.
- **How will I implement it:**
  - Render dynamic queue with HTMX filtering (`app/views/exceptions.py`).
  - Provide side-by-side comparison modal for `loan_tape.csv` vs `servicer_update.csv`.
  - Enforce field edit permissions and track reviewer action history.

---

### Module D: AI Review Assistant
- **What is it:** AI Copilot powered by Gemini API that explains validation failures, proposes numeric corrections, resolves conflicting records, and generates validation rules from natural language.
- **How will I implement it:**
  - Implement `AIAssistantService` in `app/domain/ai_assistant.py` using `google-genai` SDK.
  - Structure prompt to return strict JSON responses containing `explanation`, `suggested_value`, `reasoning`, and `confidence_score`.

```python
# Pseudo-Code: AI Review Assistant (app/domain/ai_assistant.py)
class AIAssistantService:
    def analyze_exception(self, exception: LoanException):
        prompt = f"Explain exception {exception.rule_code} for record {exception.raw_record.raw_data}."
        client = genai.Client()
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        data = json.loads(response.text)
        return AIRecommendation.objects.create(
            exception=exception, explanation=data['explanation'],
            suggested_value=data['suggested_value'], prompt_text=prompt
        )
```

---

### Module E: Verified Loan Record & Cryptographic Hashing
- **What is it:** Canonical representation of approved loan data assigned a SHA-256 cryptographic hash to guarantee anti-tampering and immutability.
- **How will I implement it:**
  - Create `VerifiedLoanRecord` model in `app/models/verified.py`.
  - Compute deterministic SHA-256 string over canonical JSON fields + ISO verification timestamp.

```python
# Pseudo-Code: SHA-256 Hashing (app/domain/verified.py)
def compute_record_hash(canonical_data: dict, verified_at_iso: str) -> str:
    serialized = json.dumps(canonical_data, sort_keys=True)
    hash_input = f"{serialized}|{verified_at_iso}".encode('utf-8')
    return hashlib.sha256(hash_input).hexdigest()
```

---

### Module F: Audit Trail Engine & Event Ledger

#### Simple Model History vs. Unified Audit Event Ledger:
- **Is Django `HistoricalModel` / simple history optimal?**
  **NO.** Standard model history (`django-simple-history`) creates separate historical tables per model (`HistoricalRawRecord`, `HistoricalLoanException`). This approach is **sub-optimal** for FinTech verification because:
  1. **Fragmented Logs:** Querying a loan's history requires joining across 5 separate tables.
  2. **Lacks Context:** Model history only logs raw DB field changes (`old_val` vs `new_val`); it cannot capture AI prompts, model metadata (`gemini-2.5-flash`), confidence scores, batch IDs, or reviewer comments.
  3. **No Cryptographic Proof:** Standard history tables can be modified or deleted without detection.

- **The OPTIMAL Solution: Unified Append-Only Audit Event Ledger with Cryptographic Hash Chaining**
  - **What is it:** A single, append-only `AuditEvent` ledger table that records every action across the entire system lifecycle in a chronological stream with SHA-256 hash chaining.
  - **How will I implement it:**
    - Implement `AuditEvent` model in `app/models/audit.py`.
    - Every event links `loan_id`, `actor_user`, `actor_role` (`DATA_OPERATOR`, `REVIEWER`, `AI_ASSISTANT`, `SYSTEM`), `event_type`, and a rich `payload` JSON object.
    - Compute `event_hash = SHA256(prev_hash + event_type + actor + timestamp + payload)` to guarantee tamper-proof immutability.

#### Event Taxonomy (What Gets Logged?):
1. **`FILE_UPLOADED`:** Actor: Data Operator | Payload: `batch_id`, `file_name`, total/success/failed row counts.
2. **`LOAN_RECORD_IMPORTED`:** Actor: System | Payload: `raw_record_id`, `source_system`, row index.
3. **`VALIDATION_EXECUTED`:** Actor: System Engine | Payload: Total rules evaluated, exceptions flagged.
4. **`EXCEPTION_CREATED`:** Actor: System | Payload: `rule_code`, `severity`, `field_name`, error message.
5. **`AI_RECOMMENDATION_GENERATED`:** Actor: AI Copilot | Payload: `model_name`, `prompt_text`, `suggested_value`, `confidence_score`, `raw_response`.
6. **`REVIEWER_COMMENT_ADDED`:** Actor: Reviewer | Payload: `comment_text`, target `exception_id`.
7. **`FIELD_EDITED`:** Actor: Reviewer | Payload: `field_name`, `old_value`, `new_value`, edit timestamp.
8. **`AI_SUGGESTION_DECISION`:** Actor: Reviewer | Payload: Decision (`ACCEPTED`, `REJECTED`, `EDITED`), `ai_rec_id`.
9. **`LOAN_APPROVED_OR_REJECTED`:** Actor: Reviewer | Payload: Status decision, reviewer ID.
10. **`VERIFIED_RECORD_CREATED`:** Actor: Reviewer/System | Payload: `verified_id`, `record_hash`, canonical snapshot.
11. **`VERIFIED_RECORD_EXPORTED`:** Actor: Data Consumer | Payload: Export format (CSV/JSON), recipient.

```python
# Pseudo-Code: Unified Append-Only Audit Event Ledger (app/models/audit.py)
import hashlib
import json
import uuid
from django.db import models
from django.utils import timezone

class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    # Traceability Identifiers
    loan_id = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    batch_id = models.IntegerField(null=True, blank=True)

    # Identity & Actor Role
    actor = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL)
    actor_role = models.CharField(max_length=50, default="SYSTEM") # OPERATOR, REVIEWER, AI_COPILOT, SYSTEM

    # Event Category
    event_type = models.CharField(max_length=100, db_index=True)

    # Rich Context & AI Metadata
    payload = models.JSONField(default=dict)

    # Cryptographic Chain
    prev_hash = models.CharField(max_length=64)
    event_hash = models.CharField(max_length=64)

    @classmethod
    def log(cls, event_type: str, actor=None, actor_role="SYSTEM", loan_id=None, batch_id=None, payload=None):
        payload = payload or {}
        last_event = cls.objects.order_by('-timestamp', '-id').first()
        prev_hash = last_event.event_hash if last_event else "0" * 64

        ts_str = timezone.now().isoformat()
        actor_name = actor.username if actor else actor_role

        # Build SHA-256 Hash Input
        payload_str = json.dumps(payload, sort_keys=True)
        hash_input = f"{prev_hash}|{ts_str}|{event_type}|{actor_name}|{loan_id}|{payload_str}".encode('utf-8')
        event_hash = hashlib.sha256(hash_input).hexdigest()

        return cls.objects.create(
            event_type=event_type,
            actor=actor,
            actor_role=actor_role,
            loan_id=loan_id,
            batch_id=batch_id,
            payload=payload,
            prev_hash=prev_hash,
            event_hash=event_hash
        )
```

---

### Module G: Dashboards

#### 1. Data Operator Dashboard
- **What is it:** Management workspace for data ingestion personnel to upload loan files, review import history, inspect validation summaries, and track records needing corrections.
- **Components (Verbatim PDF Requirements):**
  - **Upload:** Drag-and-drop file upload zone for `loan_tape.csv`, `servicer_update.csv`, and `document_manifest.csv`.
  - **Import History:** Batch processing log showing filename, timestamp, uploader, total rows, success count, and failed rows.
  - **Validation Summary:** Real-time metrics breakdown showing passing records vs. flagged validation errors across severity levels.
  - **Corrections Needed:** Actionable counter and list of un-parsed CSV rows (`FailedImportRow`) requiring file-level operator fixes.

#### 2. Reviewer Dashboard
- **What is it:** Operational workspace for risk analysts to inspect validation failures, interact with the AI Copilot, manage pending decisions, and review recent audit resolutions.
- **Components (Verbatim PDF Requirements):**
  - **Exception Queue:** Filterable queue of validation errors (filtered by severity `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, rule code, or status).
  - **AI Panel:** Interactive AI Assistant sidebar showing Gemini LLM error explanations, suggested values, confidence scores, and `[Accept]`, `[Reject]`, `[Edit]` controls.
  - **Pending Decisions:** Priority queue of loan records awaiting human reviewer approval or rejection.
  - **Recent Decisions:** Activity feed of recently resolved exceptions, reviewer edits, and approved records.

#### 3. Data Consumer Dashboard
- **What is it:** Read-only analytics console for reporting teams, management, and downstream consumers to view verified loan records, inspect data quality metrics, view verification history, and export audited datasets.
- **Components (Verbatim PDF Requirements):**
  - **Verified Records:** Clean canonical loan dataset table featuring verified attributes and SHA-256 record hashes.
  - **Data-Quality Score:** Real-time visual score meter ($0\% - 100\%$) calculated as $\frac{\text{Verified Records}}{\text{Total Records}} \times 100$.
  - **Verification History:** Historical timeline tracking verification timestamps, verifier identity, and raw-to-verified lineage.
  - **Export and Audit Trail:** Export button (CSV / JSON) and interactive audit trail timeline modal displaying the complete SHA-256 hash-chained event ledger.

---

### Module H: Verified Records REST API
- **What is it:** RESTful API suite enabling downstream external software to consume verified loans and audit logs programmatically.
- **How will I implement it:** Django REST Framework ViewSets mapped to exact required endpoints:
  - `GET /api/v1/loans`
  - `GET /api/v1/loans/:id`
  - `GET /api/v1/exceptions`
  - `GET /api/v1/verified-loans`
  - `GET /api/v1/verified-loans/:id`
  - `GET /api/v1/audit/:loanId`
  - `GET /api/v1/summary`

---

## 5. Required AI Controls & Human-in-the-Loop Guardrails

Section 9 of the problem statement explicitly mandates strict AI safety controls:

1. **Separation of Decision:** AI recommendations appear in an "AI Suggestions" sidebar; they NEVER overwrite data automatically.
2. **Human Control Actions:** Reviewer must explicitly click `[Accept Suggestion]`, `[Reject Suggestion]`, or `[Edit Value]`.
3. **Audit Metadata Display:** The UI must display the LLM model name (`gemini-2.5-flash`), confidence score, prompt text, and timestamp.
4. **No Silent Data Mutations:** DB triggers block direct unconfirmed writes from AI endpoints.

---

## 6. Agentic Coding Requirement & AI Log Specification

Section 10 requires participants to demonstrate how agentic coding tools (Antigravity / Claude Code / Gemini) were used during development.

### Structure of `AI_DEVELOPMENT_LOG.md`:
- **Tools Used:** Antigravity CLI, Gemini 3.6 Flash, Claude 3.7 Sonnet.
- **Use Cases:** Schema design, rule engine logic, HTMX view creation, unit test generation.
- **5-10 Prompt Examples:** Included verbatim in log.
- **Human Review & Rejections (At least 2 required):**
  - *Rejection 1:* AI generated direct DB write from LLM response. Rejected to enforce human approval guardrail.
  - *Rejection 2:* AI used naive string splitting for dates instead of robust `python-dateutil` parsing.
- **AI Code Percentage Estimate:** ~75% AI-assisted, 25% human architectural refinement.

---

## 7. Full Deliverables, Deliverable Credentials & Architecture Note

1. **GitHub Repository:** Complete source code with `README.md`.
2. **Architecture Note:** 2-page document (`docs/ARCHITECTURE.md`) covering system design, schema, hashing, and trade-offs.
3. **AI Development Log:** `AI_DEVELOPMENT_LOG.md` in repository root.
4. **Pre-Seeded Test Credentials:**
   - **Data Operator:** `operator@intain.com` / `OperatorPass123!`
   - **Reviewer:** `reviewer@intain.com` / `ReviewerPass123!`
   - **Data Consumer:** `consumer@intain.com` / `ConsumerPass123!`
5. **5-Minute Video Walkthrough:** Loom / MP4 recording demonstration.

---

## 8. Judging Criteria Breakdown (100 Points)

| Category | Points | Key Focus for Scoring Maximum Points |
| :--- | :--- | :--- |
| **Full-Stack Product Completeness** | **20** | Fully functional CSV ingestion, validation, AI panel, verified export, runnable app. |
| **Backend Architecture & Modeling** | **15** | Modular `app/domain/` logic, clean ORM models, transaction integrity, robust error handling. |
| **Frontend Workflow & UX** | **15** | Usable exception queue, side-by-side comparison, dynamic role-based dashboards. |
| **AI Feature Quality** | **15** | Relevant AI explanations, prompt transparency, human-in-the-loop controls. |
| **Agentic Coding Demonstration** | **15** | Thorough `AI_DEVELOPMENT_LOG.md`, prompt evidence, 2+ rejected AI output examples. |
| **Traceability & Auditability** | **10** | End-to-end lineage (Raw -> Exception -> AI -> Verified), SHA-256 record hashing. |
| **Demo Quality** | **10** | Smooth 5-minute video, clear feature walkthrough, honest limitation analysis. |

---

## 9. 5-Minute Demo Walkthrough Script

Follow this step-by-step checklist during the video demo recording:

1. **[0:00 - 0:45] Operator Role:** Log in as `operator@intain.com`, upload `loan_tape.csv` and `servicer_update.csv`, show import summary.
2. **[0:45 - 2:30] Reviewer & AI Role:** Log in as `reviewer@intain.com`, filter exception queue by `CRITICAL`, trigger AI explanation on a closed loan with positive balance, accept AI suggestion, approve record.
3. **[2:30 - 3:30] Consumer Role:** Log in as `consumer@intain.com`, inspect Verified Loan Tape, show Data Quality Score (94%), open audit trail modal showing SHA-256 hash.
4. **[3:30 - 4:15] REST API Demo:** Execute `GET /api/v1/verified-loans/` and `GET /api/v1/audit/LN-1001/` in Postman/browser.
5. **[4:15 - 5:00] AI Log Review:** Present `AI_DEVELOPMENT_LOG.md` and wrap up architecture trade-offs.

---

## 11. Execution Roadmap & Hackathon Milestones

A structured 12-hour implementation timetable ensuring all modules, UI views, AI integrations, and deliverables are completed on schedule:

- **Hours 0 - 2 (Core Ingestion & Database Models):**
  - Define Django ORM models (`UploadBatch`, `RawLoanRecord`, `FailedImportRow`, `ValidationRule`, `LoanException`, `AIRecommendation`, `VerifiedLoanRecord`, `AuditEvent`).
  - Implement `IngestionService` in `app/domain/ingestion.py` for CSV parsing, line-number lineage, and error row isolation.
  - Seed database with mock users (`Data Operator`, `Reviewer`, `Data Consumer`) from `users.json`.

- **Hours 2 - 5 (Rule Strategy Engine & Exception Queue):**
  - Implement 15 validation rules in `app/domain/validation.py` covering missing IDs, maturity dates, interest rates, DPD mismatches, closed loans with balances, and servicer reconciliation.
  - Build Reviewer Exception Queue UI with HTMX filtering (by severity, type, rule code) and side-by-side record diff viewer.

- **Hours 5 - 7 (AI Review Assistant Integration):**
  - Build `AIAssistantService` in `app/domain/ai_assistant.py` integrating `google-genai` SDK (`gemini-2.5-flash`).
  - Enforce human-in-the-loop guardrails: render AI suggestions in dedicated sidebar with explicit `[Accept]`, `[Reject]`, `[Edit]` controls.
  - Log prompt, confidence score, model name, and raw JSON in audit trail.

- **Hours 7 - 9 (Verified Loan Records, Cryptographic Hashing & API):**
  - Implement `VerifiedRecordService` generating canonical loan snapshot and SHA-256 `record_hash`.
  - Implement Unified Append-Only `AuditEvent` ledger with SHA-256 hash chaining.
  - Build Django REST Framework ViewSets for required `/api/v1/` endpoints.

- **Hours 9 - 11 (Dashboards & UX Polish):**
  - Polish Data Operator dashboard (upload dropzone, batch metrics banner).
  - Polish Data Consumer dashboard (Verified loans table, Data Quality Score meter 0-100%, audit trail modal).
  - Apply Tailwind CSS styling and HTMX dynamic updates across all role views.

- **Hours 11 - 12 (Documentation, AI Log & Video Recording):**
  - Generate `AI_DEVELOPMENT_LOG.md` documenting prompts, agentic coding tools, and 2+ rejected AI output examples.
  - Write 2-page `docs/ARCHITECTURE.md` summary note.
  - Execute automated test suite (`pytest`) to assert zero regressions.
  - Record and export 5-minute Loom/MP4 demo video following Section 10 walkthrough script.

---

## 12. Explicit Out-of-Scope Items

To prevent scope creep during the hackathon, the following features are **explicitly out of scope** (Section 16 of PDF):
- Real structured-finance waterfall models & securitization logic
- Borrowing-base calculations
- Real OCR document extraction
- Real blockchain network deployment
- Real credit scoring models or underwriting algorithms
- Live payment gateway workflows
- Production-grade regulatory compliance engines
