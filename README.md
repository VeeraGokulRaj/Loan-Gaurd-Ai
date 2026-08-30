# LoanGuard AI — Loan Data Verification Copilot

> 📖 **Technical Installation & Operating Guide**: For step-by-step environment setup, `.env` environment variables configuration, pre-seeded user credentials, database migrations, management commands, and running unit tests, please refer directly to **[SETUP.md](SETUP.md)**.

---

## 1. Executive Overview: What is LoanGuard AI?

In asset-backed finance and fintech operations, financial institutions evaluate portfolios using **loan tapes**—tables detailing borrower records, loan balances, interest rates, origination dates, and payment statuses.

However, raw loan data rarely arrives clean. It originates across fragmented sources: legacy origination systems, third-party servicer CSV updates, document availability ledgers, and manual spreadsheets. Data flaws such as missing identifiers, invalid dates, negative balances, servicer update conflicts, or unverified documents expose institutions to compliance risk and costly underwriting errors.

**LoanGuard AI** is an AI-assisted verification console designed to transform messy, unverified loan records into clean, validated, and cryptographically tamper-evident financial datasets. It establishes a trusted verification layer between raw data ingestion and downstream reporting.

---

## 2. Core Capabilities & Trust Architecture

LoanGuard AI builds trust in financial data through five key functional pillars:

```
+------------------+     +--------------------+     +---------------------+     +--------------------+
|  1. Ingestion    | --> |  2. Validation     | --> |  3. AI Copilot &    | --> |  4. Verified Tape  |
|  (Multi-Source   |     |  (15 Domain        |     |     Human Review    |     |     & SHA-256      |
|   Lineage Track) |     |   Strategy Rules)  |     |   (Guardrails)      |     |     Record Hash)   |
+------------------+     +--------------------+     +---------------------+     +--------------------+
                                                                                       |
                                                                                       v
                                                                            +--------------------+
                                                                            |  5. Audit Ledger   |
                                                                            |     (SHA-256 Hash  |
                                                                            |      Chaining)     |
                                                                            +--------------------+
```

### 🔍 1. Configurable Validation Engine
Automates 15 domain validation checks across ingested datasets. It instantly detects missing identifiers, date anomalies, interest rate threshold breaches, payment status vs. days-past-due mismatches, stale records, and cross-file servicer balance conflicts.

> 🛠️ **Fully Configurable via Database & Admin**: Validation rules are not hardcoded. Administrators can dynamically toggle rules ON/OFF, adjust severity levels (`Low`, `Medium`, `High`, `Critical`), and tune threshold parameters (such as `max_rate` or `max_stale_days`) without redeploying code. For administration setup details, see **[SETUP.md Section 6](SETUP.md#6-managing-credentials--validation-rules-via-django-admin)**.

### 📥 2. Multi-Source Ingestion & Lineage Tracking
Ingests primary datasets (`loan_tape.csv`), servicer updates (`servicer_update.csv`), and document availability records (`document_manifest.csv`). It preserves 100% of raw string inputs and line-number lineage so every verified record can be traced back to its exact source file line.

### 🛡️ 3. Anti-Tamper Verified Loan Records
Approved loans generate a standardized canonical record stamped with a unique **SHA-256 Record Hash** calculated over the verified payload and timestamp. Any post-verification alteration invalidates the cryptographic signature.

### 🔗 4. Immutable Cryptographic Audit Ledger
Maintains an append-only event ledger with **SHA-256 hash chaining** (`prev_hash` $\rightarrow$ `event_hash`). Every file upload, validation execution, field edit, AI suggestion decision, and verification action is permanently recorded in a tamper-proof event stream.

### 📊 5. Real-Time Data Quality Score Meter
Provides downstream reporting teams with a dynamic **Data Quality Score (0% – 100%)** visual meter representing the percentage of clean, verified loan records relative to total ingested volume.

---

## 3. Role-Based Access & Governance Matrix

To enforce strict separation of duties and protect financial data integrity, LoanGuard AI restricts capabilities across 3 distinct operational roles alongside System Administration:

| Role Category | Primary Purpose | Capabilities & Operational Scope |
| :--- | :--- | :--- |
| 🔑 **Superuser / Admin** | System Governance | Manages user accounts, assigns role permissions, and dynamically configures validation rules (toggling rules ON/OFF, adjusting severity levels, and tuning threshold parameters) via Django Admin. |
| 📤 **Data Operator** | Data Ingestion | Accesses the Operator Workspace to upload raw CSV files (`loan_tape.csv`, `servicer_update.csv`, `document_manifest.csv`), inspect ingestion summaries and unparsed error rows, and execute the validation engine. |
| 🕵️ **Reviewer** | Risk Analysis & Exception Resolution | Accesses the Exception Queue workspace to inspect flagged validation errors, filter by severity (`Critical`, `High`, `Medium`, `Low`), trigger the AI Copilot for error explanations, accept/edit/reject AI suggestions, edit allowed loan fields with mandatory audit comments, and approve or reject records. |
| 📈 **Data Consumer** | Reporting & Audit Inspection | Accesses the Data Consumer console to view verified canonical loan tapes, inspect the real-time Data Quality Score meter (0–100%), open the SHA-256 audit trail timeline modal, and export clean datasets (CSV/JSON). |

---

## 4. Role of AI & Real-Time Issue Resolution

### Human-in-the-Loop Safety Guardrails
AI in LoanGuard AI operates strictly as an **intelligent Copilot**, never as an autonomous silent decision maker.

- **No Silent Data Mutations**: AI recommendations are presented in a dedicated sidebar/modal and **never write directly to canonical database tables**.
- **Explicit Human Approval**: A human Reviewer must explicitly click `[Accept Suggestion]`, `[Edit Value]`, or `[Reject Suggestion]` before any data is updated.
- **Complete Prompt Transparency**: Every AI prompt, model identifier (`gemini-3.6-flash` / `opencode_zen`), confidence score, and timestamp is permanently recorded in the audit trail.

### How AI Solves Real-Time Data Issues
1. **Plain-Language Error Explanations**: Translates complex validation failures (e.g., a closed loan retaining a positive balance) into clear, actionable business summaries.
2. **Multi-Source Conflict Resolution**: Reconciles conflicting balances between `loan_tape.csv` and `servicer_update.csv`, providing context-aware recommendations for reliable values.
3. **Natural Language Rule Generation**: Translates plain English business requirements (e.g., *"Flag loans where interest rate exceeds 25%"*) into structured, executable validation rules.

> ℹ️ **API Infrastructure Note**: LoanGuard AI integrates multiple LLM providers, including the **OpenCode Zen free API tier**. During periods of high global web traffic, public free API endpoints may occasionally experience rate limiting or latency. The system handles this gracefully using automatic retry logic and fallback mechanisms without disrupting core application workflows.

---

## 5. Output Summary & Deliverables

| Deliverable | Description | Format / Interface |
| :--- | :--- | :--- |
| **Ingestion Metrics** | Real-time batch processing metrics and isolated unparsed rows | Operator Dashboard Banner |
| **Exception Workspace** | Filterable exception queue featuring side-by-side record diffs | Reviewer Interactive Workspace |
| **Verified Loan Tape** | Clean, canonical financial loan dataset with SHA-256 hashes | Consumer Console & CSV / JSON Export |
| **Audit Ledger** | Chronological event history featuring SHA-256 hash chaining | Audit Modal & REST API Endpoints |

---

> 🚀 **Ready to Install?**: Proceed to **[SETUP.md](SETUP.md)** for step-by-step instructions on environment setup, database migrations, management commands, and running unit tests.
