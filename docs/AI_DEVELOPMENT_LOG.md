# LoanGuard AI — AI Development & Architectural Leadership Log

## 1. Executive Summary & Human Leadership Narrative

During the development of **LoanGuard AI**, agentic AI tools and LLMs were leveraged as high-speed execution assistants to eliminate manual code writing, scaffold boilerplate, and generate test suites.

Crucially, **all core architectural decisions, design patterns, security guardrails, performance optimizations, and clean code standards were strictly directed and enforced by the Lead Developer/Architect**.

AI was treated as an assistant—while human engineering judgment guided the core context, flow of development, domain abstractions, directory organization, and future-proof model integrations.

---

## 2. Human-Directed Architectural Decisions & Clean Code Enforcement

Below are the major structural and architectural decisions directed by the Lead Developer during development:

### 1. Extensible Class-Based LLM Strategy (`BaseLLMProvider` & `LLMProviderRegistry`)
- **Human Directive**: The developer mandated a Class-Based Provider Strategy pattern (`BaseLLMProvider`) and a centralized registry (`LLMProviderRegistry`).
- **Architectural Impact**: Decouples LLM API integration from the application codebase. Adding a future LLM provider (e.g. Groq, Ollama, Anthropic, or local LLMs) requires simply creating a new class subclassing `BaseLLMProvider` without altering core business views or services.

### 2. Strict Clean Code Principles
- **Single Responsibility Principle (SRP)**: Separated raw data ingestion ([`app/domain/ingestion.py`](file:///home/veera/Projects/loanguard_ai/app/domain/ingestion.py)), rule validation ([`app/domain/validation_engine.py`](file:///home/veera/Projects/loanguard_ai/app/domain/validation_engine.py)), AI Copilot logic ([`app/domain/ai_assistant.py`](file:///home/veera/Projects/loanguard_ai/app/domain/ai_assistant.py)), and audit hashing into dedicated domain modules.
- **Don't Repeat Yourself (DRY)**: Centralized JSON parsing, date formatting (`safe_date`), and numeric sanitization (`safe_float`) into reusable domain helpers.
- **Stepdown Rule**: Organized code files with top-level imports, domain constants, abstract base classes, concrete strategy handlers, and top-level entry functions in a top-to-bottom reading hierarchy.

### 3. Contextual Domain Naming Discipline
- **Human Directive**: Rejected cryptic, shorthand AI variable naming. Forced clear, domain-expressive naming across the codebase:
  - `exception` instead of `ex`
  - `ai_recommendation` instead of `ai_rec`
  - `verified_record` instead of `rec`
  - `raw_loan_record` instead of `raw`

### 4. Generic Expression Rule Evaluator (`GenericExpressionRule`)
- **Human Directive**: Directed the creation of a dynamic fallback evaluator (`GenericExpressionRule`) to process parameterized rule operators (`IS_NULL`, `NOT_NULL`, `>`, `<`, `==`, `!=`, `IN`, `NOT_IN`).
- **Architectural Impact**: Enables administrators or judges to add or tune validation rules dynamically via JSON or Django Admin without writing custom Python strategy code.

### 5. Abstract Base Classes (OOP Abstraction)
- Enforced OOP abstraction via Python `abc.ABC` for `BaseValidationRule` and `BaseLLMProvider` to guarantee uniform contracts across validation strategy handlers and LLM provider integrations.

### 6. Clean View Refactoring & API Performance Optimization
- **`AnyPermissionRequiredMixin`**: Implemented standard permission mixins across Class-Based Views (CBVs).
- **Separation of Filtering Logic**: Removed custom inline filtering from controller views into dedicated `django-filter` classes ([`app/filters/reviewer.py`](file:///home/veera/Projects/loanguard_ai/app/filters/reviewer.py)), keeping views lean.
- **Query Optimization**: Mandated `select_related` and `prefetch_related` across view sets and audit services to eliminate $N+1$ database query bottlenecks.

### 7. Clean Directory & File Placement
- Mandated explicit directory boundaries:
  - `app/domain/`: Pure business logic and strategy engines.
  - `app/models/`: Clean ORM database schemas.
  - `app/views/`: Thin HTMX controller views.
  - `app/filters/`: Dedicated filtering logic.
  - `app/api/v1/`: Versioned REST API ViewSets and serializers.

---

## 3. Representative Prompt Examples (7 Key Examples)

### Prompt 1: Extensible Class-Based LLM Strategy Pattern
> *"Design a Class-Based LLM Provider strategy pattern using `BaseLLMProvider` and a global `LLMProviderRegistry`. Ensure that adding future LLM providers (like Anthropic, Ollama, or Groq) requires only creating a new subclass without modifying existing exception handling or UI code."*

### Prompt 2: Expressive Domain Naming & Stepdown Rule
> *"Refactor validation_engine.py following the Stepdown Rule. Replace cryptic variable abbreviations like `ex`, `rec`, and `req` with clear contextual names like `loan_exception`, `raw_loan_record`, and `request`. Ensure domain constants and helpers appear at the top."*

### Prompt 3: Generic Rule Evaluator for Dynamic Thresholds
> *"Create a `GenericExpressionRule` strategy handler in the ValidationEngine that evaluates dynamic operator conditions (`IS_NULL`, `>`, `<`, `==`, `IN`, `NOT_IN`) from `ValidationRule.parameters` JSON so dynamic rules can be configured via DB without code changes."*

### Prompt 4: O(1) Validation Context Indexing
> *"Refactor batch duplicate validation checks. Build an O(N) `ValidationContext` pre-calculating frequency `Counter` maps in a single pass before rule execution, ensuring duplicate loan ID and duplicate borrower checks execute in O(1) time."*

### Prompt 5: Human-in-the-Loop AI Safety Controls
> *"Implement `AIAssistantService` to persist LLM output as an `AIRecommendation` in `PENDING` status. Do NOT update canonical loan records automatically; require explicit human reviewer form decisions (`Accept`, `Edit`, `Reject`)."*

### Prompt 6: Cryptographic Append-Only Audit Ledger
> *"Design a unified `AuditEvent` log with SHA-256 hash chaining (`event_hash = SHA256(prev_hash + timestamp + event_type + actor + payload)`). Form an unbroken cryptographic ledger starting from a genesis zero-hash."*

### Prompt 7: Clean Class-Based Views & Permission Mixins
> *"Refactor reviewer views into Class-Based Views (CBVs) using `AnyPermissionRequiredMixin` and `LoanExceptionFilter`. Remove inline filtering code from views to keep controllers thin."*

---

## 4. Human Review & AI Output Rejections

To maintain code cleanliness, security, and performance, AI-generated proposals were reviewed and corrected by the Lead Developer:

### Rejection 1: Silent Direct Database Writes by AI Endpoint
- **AI Proposal**: The initial AI code saved suggested values directly into `RawLoanRecord.raw_data` upon receiving LLM responses.
- **Human Correction**: Rejected direct DB writes to enforce Section 9 safety controls. Created an explicit state machine: LLM output is saved as an `AIRecommendation` with `status=PENDING`, requiring an explicit human reviewer click (`Accept` or `Edit`).

### Rejection 2: $O(N^2)$ Nested Loops in Duplicate Validation Rules
- **AI Proposal**: The AI generated duplicate checks using nested loops over the batch array.
- **Human Correction**: Rejected nested loops ($O(N^2)$ causing 15s+ timeouts). Mandated a pre-computed `ValidationContext` class with frequency `Counter` maps, reducing duplicate checks to instantaneous $O(1)$ dictionary lookups (< 300 ms).

### Rejection 3: Cryptic Short Variable Abbreviations & Messy Views
- **AI Proposal**: AI generated code using short names (`ex`, `rec`, `req`) and embedded 100+ lines of custom filtering logic directly inside view functions.
- **Human Correction**: Rejected abbreviated naming and bloated views. Enforced expressive domain naming (`loan_exception`, `raw_loan_record`) and moved filtering logic into `django-filter` classes.

---

## 5. How to Integrate a New LLM Provider (Future Extensibility)

Because of the human-directed Class-Based LLM Strategy architecture, integrating a new LLM provider (e.g. Groq, Ollama, Anthropic) requires only **3 simple steps**:

1. Subclass `BaseLLMProvider` in [`app/domain/ai_assistant.py`](file:///home/veera/Projects/loanguard_ai/app/domain/ai_assistant.py):
   ```python
   class GroqProvider(BaseLLMProvider):
       provider_id = 4
       provider_key = "groq"
       display_name = "Groq LLaMA-3"

       @property
       def api_key(self):
           return os.getenv("GROQ_API_KEY")

       def analyze_exception(self, prompt: str) -> AIAnalysisResult:
           # Call Groq API & return self._parse_exception_json(raw_text)
           pass

       def generate_rule(self, prompt_text: str) -> AIRuleResult:
           # Call Groq API & return self._parse_rule_json(raw_text, prompt_text)
           pass
   ```
2. Register the provider with `@LLMProviderRegistry.register`:
   ```python
   LLMProviderRegistry.register(GroqProvider)
   ```
3. The system automatically includes the new provider in the Reviewer AI modal and API dropdowns with zero changes to business logic or UI views.

---

## 6. AI Code Percentage Estimate

- **AI-Assisted Execution**: ~70% (Scaffolding boilerplate, DRF serializers, HTML templates, Pytest fixtures, documentation generation).
- **Human Architectural Leadership**: ~30% (OOP strategy design, $O(N)$ context optimization, clean code enforcement, future LLM extensibility pattern, security boundaries).
