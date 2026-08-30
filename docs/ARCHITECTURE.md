# LoanGuard AI — Master Architecture & Implementation Note

This document provides a comprehensive technical overview of the architecture, data models, validation strategy pattern, AI guardrail engine, cryptographic audit ledger, REST API design, and trade-offs of **LoanGuard AI**.

---

## 1. High-Level System Architecture

LoanGuard AI is built using Python 3.12+ and Django 5.x following Domain-Driven Design (DDD) principles with a clean separation of concerns:

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
|  | (CSV Parsing,      |  | Engine (Rule Strategy |  | (LLM Integration, Prompt |  |
|  | Lineage Tracking)  |  | & Exception Detector) |  | Audit, Conflict Resolver)|  |
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
|                           STORAGE LAYER (SQLite WAL Mode)                         |
|  UploadBatch | RawLoanRecord | ValidationRule | LoanException | VerifiedRecord    |
|                         AuditEvent | AIRecommendation                             |
+-----------------------------------------------------------------------------------+
```

### Modular Code Architecture (`app/`):
- `app/models/`: Database ORM schemas with explicit type choices and indexes.
- `app/domain/`: Pure business logic (Ingestion, Strategy Validation Engine, AI Assistant, Verified Service, Audit Ledger).
- `app/views/`: Thin, role-secured HTMX controller views.
- `app/api/`: Django REST Framework ViewSets and serializers.

---

## 2. Role-Based Access Control (RBAC) Architecture

To guarantee strict separation of duties, permissions are enforced via Django `Group` permissions (`Data Operator`, `Reviewer`, `Data Consumer`).

The custom view decorator `@role_required` explicitly **prohibits superuser bypass** during role-based view evaluation, ensuring that role isolation can be reliably tested and verified.

---

## 3. Data Model Architecture

The data architecture spans five functional schema layers:

1. **Ingestion Layer**:
   - `UploadBatch`: Tracks file metadata, uploader, timestamp, total/successful/failed record counts, and status (`INGESTED`, `PARTIAL_SUCCESS`, `FAILED`).
   - `RawLoanRecord`: Preserves 100% of original uncleaned string data as a JSON dictionary (`raw_data = JSONField()`), capturing line-number lineage (`row_number`) and source system attributes.
   - `FailedImportRow`: Stores raw text lines that failed initial CSV structural parsing.
   - `ServicerUpdateRecord` & `DocumentManifestRecord`: Second-source data stores for cross-file reconciliation.

2. **Validation & Exception Layer**:
   - `ValidationRule`: Dynamic database table storing configurable rule metadata (`rule_code`, `strategy_key`, `field_name`, `severity`, `is_active`, `parameters`).
   - `LoanException`: Tracks flagged errors (`OPEN`, `UNDER_REVIEW`, `RESOLVED_ACCEPTED`, `RESOLVED_EDITED`, `REJECTED`), linked to reviewer comments and resolved timestamps.

3. **AI Assistant Layer**:
   - `AIRecommendation`: Persists LLM outputs (`explanation`, `suggested_value`, `confidence_score`, `reasoning`, `prompt_text`, `model_name`, `raw_response`) in `PENDING` status prior to human review.

4. **Verified Loan Record Layer**:
   - `VerifiedLoanRecord`: Immutable canonical loan representation. Stores clean JSON payload, verifier user ID, verification timestamp, and a SHA-256 `record_hash`.

5. **Cryptographic Audit Ledger**:
   - `AuditEvent`: Append-only audit stream tracking every system lifecycle event with SHA-256 hash chaining.

---

## 4. Configurable Validation Engine (Strategy Pattern + O(1) Context Indexing)

The validation engine separates rule configuration from rule execution using the **Strategy Pattern**:

- **Abstraction**: `BaseValidationRule` defines the standard `validate(raw_record, db_rule, context)` interface.
- **15 Strategy Handlers**: Specific classes (`MissingLoanIdRule`, `DuplicateLoanIdRule`, `DuplicateBorrowerTripletRule`, `MaturityBeforeOriginationRule`, `ClosedLoanPositiveBalanceRule`, `ServicerUpdateConflictRule`, etc.) execute complex domain logic.
- **Dynamic Fallback**: `GenericExpressionRule` handles parameterized dynamic rules (`IS_NULL`, `>`, `<`, `==`, `IN`, `NOT_IN`).
- **O(1) Batch Context Indexing**: Rather than executing nested $O(N^2)$ loops during duplicate detection, `ValidationContext.build()` constructs batch frequency dictionary maps (`Counter`) in a single $O(N)$ pass. This enables instantaneous $O(1)$ duplicate lookups across 100,000+ records.

---

## 5. AI Review Assistant & Human-in-the-Loop Safety Controls

The AI engine integrates an extensible `LLMProviderRegistry` supporting **Google Gemini** (`gemini-3.6-flash`), **OpenCode Zen** (`ling-3.0-flash-fin-free`), and **OpenAI ChatGPT** (`gpt-4o-mini`).

### Mandatory AI Safety Controls (Section 9 Compliance):
1. **Separation of Decision**: AI suggestions are rendered in a dedicated sidebar/modal and **never overwrite database fields automatically**.
2. **Explicit Human Review**: Data modifications execute ONLY when a human Reviewer explicitly submits an `Accept` or `Edit` form action.
3. **Full Prompt Transparency**: Every prompt, LLM response, confidence score, and model name is logged into `AIRecommendation` and `AuditEvent`.

---

## 6. Cryptographic Audit Trail & SHA-256 Hash Lineage

LoanGuard AI enforces end-to-end data auditability and anti-tamper verification using dual-layer SHA-256 hashing:

### 1. Verified Record Hashing
Every canonical `VerifiedLoanRecord` generates a deterministic SHA-256 hash over its payload:
$$\text{Record Hash} = \text{SHA256}(\text{Canonical JSON Payload} \parallel \text{ISO Verification Timestamp})$$

### 2. Append-Only Audit Hash Chaining
The unified `AuditEvent` log implements cryptographic hash chaining similar to a blockchain ledger:
$$\text{Event Hash}_t = \text{SHA256}(\text{Event Hash}_{t-1} \parallel \text{Timestamp} \parallel \text{Event Type} \parallel \text{Actor} \parallel \text{Payload JSON})$$

An unbroken hash chain proves that audit log history has not been retroactively altered or deleted.

---

## 7. REST API Suite (Module H)

LoanGuard AI exposes a complete suite of RESTful API endpoints via Django REST Framework:

- `GET /api/v1/loans/`: Paginated list of raw loan records.
- `GET /api/v1/loans/<id>/`: Detail view of a single raw loan record.
- `GET /api/v1/exceptions/`: Paginated list of validation exceptions filtered by severity or status.
- `GET /api/v1/verified-loans/`: Paginated list of verified canonical loan records with SHA-256 hashes.
- `GET /api/v1/verified-loans/<id>/`: Detail view of a verified loan record.
- `GET /api/v1/audit/<loan_id>/`: Complete SHA-256 hash-chained audit trail for a specific loan.
- `GET /api/v1/summary/`: System-wide ingestion metrics, exception counts, and Data Quality Score.

---

## 8. Architectural Trade-Offs & Decisions

### 1. SQLite WAL Mode vs. PostgreSQL (Demo Simplicity vs. Production Scale)
- **Decision**: Used SQLite with Write-Ahead Logging (`PRAGMA journal_mode=WAL;`).
- **Rationale**: Provides zero-dependency, zero-friction local setup for hackathon evaluation while enabling concurrent reads during write operations to resolve HTMX async request locking.
- **Production Path**: System uses standard Django ORM `JSONField`; switching to production PostgreSQL requires zero code changes (updating `config/settings/production.py`).
