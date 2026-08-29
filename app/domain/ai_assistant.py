"""
Domain logic for Module D: AI Review Assistant & Guardrails.

Provides reusable domain functions and dataclasses for generating AI recommendations on loan exceptions,
translating natural language into validation rules, enforcing Section 9 human-in-the-loop
audit guardrails, and processing reviewer decisions (Accept, Reject, Edit).
Follows the Stepdown Rule (Clean Code top-down narrative structure).
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.models import AIRecommendation, AuditEvent, LoanException, ValidationRule

logger = logging.getLogger(__name__)

GEMINI_API_KEY_ERROR_MESSAGE = "GEMINI_API_KEY environment variable or setting is not configured."


def get_gemini_api_key() -> str | None:
    """Helper function to resolve GEMINI_API_KEY from Django settings or environment."""
    return getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class AIAnalysisResult:
    """Dataclass representing structured output from AI exception analysis."""

    explanation: str
    suggested_value: str
    confidence_score: float
    reasoning: str
    raw_response: str = ""
    success: bool = True
    error_message: str = ""


@dataclass
class AIRuleResult:
    """Dataclass representing structured output from AI rule generation."""

    rule_code: str
    rule_name: str
    description: str
    field_name: str
    severity: int
    strategy_key: str
    parameters: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    confidence_score: float = 0.0
    success: bool = True
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Converts rule result into dictionary payload for ValidationRule creation."""
        return {
            "success": self.success,
            "error_message": self.error_message,
            "rule_code": self.rule_code,
            "rule_name": self.rule_name,
            "description": self.description,
            "field_name": self.field_name,
            "severity": self.severity,
            "strategy_key": self.strategy_key,
            "parameters": self.parameters,
            "reasoning": self.reasoning,
            "confidence_score": self.confidence_score,
        }


def generate_exception_ai_recommendation(
    loan_exception: LoanException,
    user: Any = None,
    servicer_record: dict[str, Any] | None = None,
    doc_manifest: dict[str, Any] | None = None,
    model_choice: int = AIRecommendation.ModelProvider.GEMINI,
) -> AIRecommendation:
    """
    High-level entry point: Generates an AI recommendation for a LoanException.

    Builds the analysis prompt and calls the Gemini API. If AI generation fails,
    stores the failure response state transparently.
    """
    raw_data = (
        loan_exception.raw_record.raw_data
        if (loan_exception.raw_record and loan_exception.raw_record.raw_data)
        else {}
    )

    prompt = build_ai_prompt_for_exception(
        loan_exception=loan_exception,
        raw_data=raw_data,
        servicer_record=servicer_record,
        doc_manifest=doc_manifest,
    )

    analysis_result: AIAnalysisResult = call_gemini_for_exception(
        prompt=prompt, model_choice=model_choice
    )

    with transaction.atomic():
        recommendation = AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.EXCEPTION_REVIEW,
            exception=loan_exception,
            rule=loan_exception.rule,
            suggested_value=analysis_result.suggested_value,
            explanation=analysis_result.explanation,
            reasoning=analysis_result.reasoning,
            confidence_score=float(analysis_result.confidence_score),
            prompt_text=prompt,
            model_name=model_choice,
            raw_response=analysis_result.raw_response,
            status=AIRecommendation.RecommendationStatus.PENDING,
            created_by=user if (hasattr(user, "pk") and user.pk) else None,
        )

        AuditEvent.log_event(
            event_type="AI_RECOMMENDATION_GENERATED",
            actor=user,
            actor_role=AuditEvent.ActorRole.SYSTEM,
            loan_id=loan_exception.loan_id,
            batch_id=loan_exception.batch_id,
            payload={
                "recommendation_id": recommendation.id,
                "exception_id": loan_exception.id,
                "model": recommendation.get_model_name_display(),
                "confidence_score": recommendation.confidence_score,
                "suggested_value": recommendation.suggested_value,
                "success": analysis_result.success,
                "error_message": analysis_result.error_message,
            },
        )

    return recommendation


@transaction.atomic
def process_ai_recommendation_decision(
    recommendation: AIRecommendation,
    action: str,
    actor: Any,
    comment: str = "",
    edited_value: str = "",
    edited_rule_data: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """
    High-level entry point: Enforces Section 9 Human-In-The-Loop compliance.

    Dispatches review decisions (Accept, Reject, Edit) to type-specific handlers
    (Exception Review vs Rule Generation) before applying canonical state changes.
    """
    if recommendation.status != AIRecommendation.RecommendationStatus.PENDING:
        return False, f"Recommendation #{recommendation.id} has already been reviewed."

    if recommendation.recommendation_type == AIRecommendation.RecommendationType.RULE_GENERATION:
        return process_rule_ai_decision(
            recommendation=recommendation,
            action=action,
            actor=actor,
            comment=comment,
            edited_rule_data=edited_rule_data,
        )
    else:
        return process_exception_ai_decision(
            recommendation=recommendation,
            action=action,
            reviewer=actor,
            reviewer_comment=comment,
            edited_value=edited_value,
        )


def process_exception_ai_decision(
    recommendation: AIRecommendation,
    action: str,
    reviewer: Any,
    reviewer_comment: str = "",
    edited_value: str = "",
) -> tuple[bool, str]:
    """Processes decisions (Accept, Edit, Reject) for Exception Review AI Recommendations."""
    action_clean = action.lower().strip()
    comment_clean = reviewer_comment.strip()
    edited_clean = edited_value.strip()

    if action_clean == "accept":
        return _apply_accepted_exception_decision(
            recommendation=recommendation,
            reviewer=reviewer,
            comment_clean=comment_clean,
        )
    elif action_clean == "edit":
        if not edited_clean:
            return False, "Edited value cannot be empty when choosing Edit option."
        return _apply_edited_exception_decision(
            recommendation=recommendation,
            reviewer=reviewer,
            comment_clean=comment_clean,
            edited_clean=edited_clean,
        )
    elif action_clean == "reject":
        return _apply_rejected_exception_decision(
            recommendation=recommendation,
            reviewer=reviewer,
            comment_clean=comment_clean,
        )

    return False, f"Invalid decision action '{action}' for exception recommendation."


def process_rule_ai_decision(
    recommendation: AIRecommendation,
    action: str,
    actor: Any,
    comment: str = "",
    edited_rule_data: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Processes decisions (Accept, Edit, Reject) for Rule Generation AI Recommendations."""
    action_clean = action.lower().strip()
    comment_clean = comment.strip()

    if action_clean == "accept":
        return _apply_accepted_rule_decision(
            recommendation=recommendation,
            actor=actor,
            comment_clean=comment_clean,
        )
    elif action_clean == "edit":
        if not edited_rule_data:
            return False, "Edited rule data cannot be empty when choosing Edit option."
        return _apply_edited_rule_decision(
            recommendation=recommendation,
            actor=actor,
            comment_clean=comment_clean,
            edited_rule_data=edited_rule_data,
        )
    elif action_clean == "reject":
        return _apply_rejected_rule_decision(
            recommendation=recommendation,
            actor=actor,
            comment_clean=comment_clean,
        )

    return False, f"Invalid decision action '{action}' for rule recommendation."


def generate_ai_rule_recommendation(
    prompt_text: str,
    user: Any = None,
    model_choice: int = AIRecommendation.ModelProvider.GEMINI,
) -> AIRecommendation:
    """
    High-level entry point: Translates natural language into a ValidationRule recommendation.
    If generation fails, records the failure reason in explanation for UI display.
    """
    rule_result: AIRuleResult = call_gemini_for_rules(
        prompt_text=prompt_text, model_choice=model_choice
    )

    explanation_text = (
        rule_result.description
        if rule_result.success
        else f"AI Rule Generation Failed: {rule_result.error_message}"
    )

    with transaction.atomic():
        recommendation = AIRecommendation.objects.create(
            recommendation_type=AIRecommendation.RecommendationType.RULE_GENERATION,
            suggested_value=rule_result.rule_code if rule_result.success else "",
            explanation=explanation_text,
            reasoning=rule_result.reasoning,
            confidence_score=float(rule_result.confidence_score),
            prompt_text=prompt_text,
            model_name=model_choice,
            suggested_rule_data=rule_result.to_dict(),
            status=AIRecommendation.RecommendationStatus.PENDING,
            created_by=user if (hasattr(user, "pk") and user.pk) else None,
        )

        AuditEvent.log_event(
            event_type="AI_RULE_GENERATED",
            actor=user,
            actor_role=AuditEvent.ActorRole.DATA_OPERATOR,
            payload={
                "recommendation_id": recommendation.id,
                "rule_code": rule_result.rule_code,
                "rule_name": rule_result.rule_name,
                "success": rule_result.success,
                "error_message": rule_result.error_message,
            },
        )

    return recommendation


def build_ai_prompt_for_exception(
    loan_exception: LoanException,
    raw_data: dict[str, Any],
    servicer_record: dict[str, Any] | None,
    doc_manifest: dict[str, Any] | None,
) -> str:
    """Builds structured prompt string for LLM exception analysis."""
    rule_code = getattr(loan_exception.rule, "rule_code", loan_exception.rule_code)
    rule_desc = getattr(loan_exception.rule, "description", loan_exception.description)
    current_val = raw_data.get(loan_exception.field_name, "") if raw_data else ""

    return (
        "You are a 20+ years expert Loan Data Audit Assistant for LoanGuard AI.\n"
        "Analyze the following loan validation exception and suggest the correct value.\n\n"
        f"Loan Exception Details:\n"
        f"- Loan ID: {loan_exception.loan_id}\n"
        f"- Field Name: {loan_exception.field_name}\n"
        f"- Rule Code: {rule_code}\n"
        f"- Rule Description: {rule_desc}\n"
        f"- Current Field Value: {current_val}\n"
        f"- Severity: {loan_exception.get_severity_display()}\n\n"
        f"Context Data:\n"
        f"- Raw Record Payload: {json.dumps(raw_data)}\n"
        f"- Servicer Update Payload: {json.dumps(servicer_record or {})}\n"
        f"- Document Manifest: {json.dumps(doc_manifest or {})}\n\n"
        "Respond strictly in JSON format with keys:\n"
        "1. 'explanation': Clear, concise summary explanation of why validation failed (MAXIMUM 3-4 sentences / 150 words).\n"
        "2. 'suggested_value': Corrected string value for field.\n"
        "3. 'confidence_score': Float between 0.0 and 1.0.\n"
        "4. 'reasoning': Step-by-step logic used (place detailed multi-step audit breakdowns here, NOT in explanation).\n"
    )


def clean_json_response_text(text: str) -> str:
    """Removes markdown code fences and strips whitespace from LLM response text."""
    text_clean = text.strip()
    if text_clean.startswith("```"):
        lines = text_clean.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text_clean = "\n".join(lines).strip()
    return text_clean


def resolve_model_name(model_choice: int) -> str:
    """Maps ModelProvider enum choice to Gemini API model name string."""
    if model_choice == AIRecommendation.ModelProvider.GEMINI:
        return "gemini-3.6-flash"
    return "gemini-3.6-flash"


def call_gemini_for_exception(
    prompt: str,
    model_choice: int = AIRecommendation.ModelProvider.GEMINI,
) -> AIAnalysisResult:
    """
    Calls Google Gemini API using GEMINI_API_KEY.

    If API key is unconfigured or call fails, returns failure AIAnalysisResult.
    Applies length truncation safeguards on 'explanation' to prevent UI overflow.
    """
    api_key = get_gemini_api_key()
    if not api_key:
        return AIAnalysisResult(
            explanation=f"AI generation failed: {GEMINI_API_KEY_ERROR_MESSAGE}",
            suggested_value="",
            confidence_score=0.0,
            reasoning="Unable to invoke Gemini LLM because GEMINI_API_KEY is missing.",
            raw_response=json.dumps({"error": "Missing GEMINI_API_KEY"}),
            success=False,
            error_message=GEMINI_API_KEY_ERROR_MESSAGE,
        )

    try:
        from google import genai

        model_name = resolve_model_name(model_choice)
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )

        if response and response.text:
            cleaned_text = clean_json_response_text(response.text)
            parsed = json.loads(cleaned_text)

            # Strict check for required keys without fallback defaults
            if "explanation" not in parsed or "suggested_value" not in parsed:
                err_msg = "Gemini API response did not contain required JSON fields ('explanation', 'suggested_value')."
                return AIAnalysisResult(
                    explanation=f"AI generation failed: {err_msg}",
                    suggested_value="",
                    confidence_score=0.0,
                    reasoning=f"LLM JSON payload missing required keys. Raw: {response.text}",
                    raw_response=response.text,
                    success=False,
                    error_message=err_msg,
                )

            explanation_raw = str(parsed["explanation"])
            reasoning_raw = str(parsed.get("reasoning", ""))
            confidence_val = float(parsed.get("confidence_score", 0.0))

            explanation_clean, reasoning_updated = truncate_long_explanation(
                explanation=explanation_raw,
                reasoning=reasoning_raw,
                max_chars=800,
            )

            return AIAnalysisResult(
                explanation=explanation_clean,
                suggested_value=str(parsed["suggested_value"]),
                confidence_score=confidence_val,
                reasoning=reasoning_updated,
                raw_response=response.text,
                success=True,
                error_message="",
            )

        err_msg = "Received empty response from Gemini API."
        return AIAnalysisResult(
            explanation=f"AI generation failed: {err_msg}",
            suggested_value="",
            confidence_score=0.0,
            reasoning="Gemini API returned an empty response body.",
            raw_response=json.dumps({"error": "Empty API response"}),
            success=False,
            error_message=err_msg,
        )
    except Exception as exc:
        logger.error("Gemini API generation failed: %s", exc, exc_info=True)
        err_msg = str(exc)
        return AIAnalysisResult(
            explanation=f"AI generation failed: {err_msg}",
            suggested_value="",
            confidence_score=0.0,
            reasoning=f"Exception encountered during Gemini API call: {err_msg}",
            raw_response=json.dumps({"error": err_msg}),
            success=False,
            error_message=err_msg,
        )


def truncate_long_explanation(
    explanation: str,
    reasoning: str,
    max_chars: int = 800,
) -> tuple[str, str]:
    """
    Safeguard function: If the LLM generates an overly verbose explanation,
    truncates 'explanation' cleanly for UI rendering and preserves full details in 'reasoning'.
    """
    if len(explanation) <= max_chars and explanation.count("\n") < 10:
        return explanation, reasoning

    truncated_explanation = explanation[:max_chars].rsplit(" ", 1)[0] + "..."
    extended_reasoning = (
        f"--- Full AI Explanation ---\n{explanation}\n\n"
        f"--- Step-by-Step Reasoning ---\n{reasoning}"
    )
    return truncated_explanation, extended_reasoning


def _apply_accepted_exception_decision(
    recommendation: AIRecommendation,
    reviewer: Any,
    comment_clean: str,
) -> tuple[bool, str]:
    """Applies reviewer acceptance of an AI exception recommendation and updates underlying exception."""
    recommendation.status = AIRecommendation.RecommendationStatus.ACCEPTED
    recommendation.reviewed_by = reviewer if (hasattr(reviewer, "pk") and reviewer.pk) else None
    recommendation.reviewed_at = timezone.now()
    recommendation.reviewer_comment = comment_clean
    recommendation.save()

    exception = recommendation.exception
    if exception:
        from app.domain.exception_handling import apply_exception_resolution

        if recommendation.suggested_value and exception.raw_record and exception.field_name:
            raw_data = exception.raw_record.raw_data or {}
            raw_data[exception.field_name] = recommendation.suggested_value
            exception.raw_record.raw_data = raw_data
            exception.raw_record.save()
            exception.override_value = recommendation.suggested_value

        apply_exception_resolution(
            loan_exception=exception,
            actor=reviewer,
            status=LoanException.ExceptionStatus.RESOLVED_ACCEPTED,
            comment=comment_clean or f"Accepted AI suggestion: {recommendation.suggested_value}",
        )

    AuditEvent.log_event(
        event_type="AI_RECOMMENDATION_ACCEPTED",
        actor=reviewer,
        actor_role=AuditEvent.ActorRole.REVIEWER,
        loan_id=exception.loan_id if exception else None,
        batch_id=exception.batch_id if exception else None,
        payload={
            "recommendation_id": recommendation.id,
            "exception_id": exception.id if exception else None,
            "suggested_value": recommendation.suggested_value,
            "comment": comment_clean,
        },
    )
    return True, f"AI Recommendation #{recommendation.id} accepted successfully."


def _apply_edited_exception_decision(
    recommendation: AIRecommendation,
    reviewer: Any,
    comment_clean: str,
    edited_clean: str,
) -> tuple[bool, str]:
    """Applies reviewer edit of an AI exception recommendation and updates underlying exception."""
    recommendation.status = AIRecommendation.RecommendationStatus.EDITED
    recommendation.edited_value = edited_clean
    recommendation.reviewed_by = reviewer if (hasattr(reviewer, "pk") and reviewer.pk) else None
    recommendation.reviewed_at = timezone.now()
    recommendation.reviewer_comment = comment_clean
    recommendation.save()

    exception = recommendation.exception
    if exception:
        from app.domain.exception_handling import apply_exception_resolution

        if exception.raw_record and exception.field_name:
            raw_data = exception.raw_record.raw_data or {}
            raw_data[exception.field_name] = edited_clean
            exception.raw_record.raw_data = raw_data
            exception.raw_record.save()
            exception.override_value = edited_clean

        apply_exception_resolution(
            loan_exception=exception,
            actor=reviewer,
            status=LoanException.ExceptionStatus.RESOLVED_EDITED,
            comment=comment_clean or f"Edited AI suggestion to: {edited_clean}",
        )

    AuditEvent.log_event(
        event_type="AI_RECOMMENDATION_EDITED",
        actor=reviewer,
        actor_role=AuditEvent.ActorRole.REVIEWER,
        loan_id=exception.loan_id if exception else None,
        batch_id=exception.batch_id if exception else None,
        payload={
            "recommendation_id": recommendation.id,
            "exception_id": exception.id if exception else None,
            "original_suggested_value": recommendation.suggested_value,
            "edited_value": edited_clean,
            "comment": comment_clean,
        },
    )
    return True, f"AI Recommendation #{recommendation.id} edited and saved."


def _apply_rejected_exception_decision(
    recommendation: AIRecommendation,
    reviewer: Any,
    comment_clean: str,
) -> tuple[bool, str]:
    """Applies reviewer rejection of an AI exception recommendation."""
    recommendation.status = AIRecommendation.RecommendationStatus.REJECTED
    recommendation.reviewed_by = reviewer if (hasattr(reviewer, "pk") and reviewer.pk) else None
    recommendation.reviewed_at = timezone.now()
    recommendation.reviewer_comment = comment_clean
    recommendation.save()

    exception = recommendation.exception
    if exception:
        from app.domain.exception_handling import apply_exception_resolution

        apply_exception_resolution(
            loan_exception=exception,
            actor=reviewer,
            status=LoanException.ExceptionStatus.REJECTED,
            comment=comment_clean or "Rejected AI Copilot recommendation.",
        )

    AuditEvent.log_event(
        event_type="AI_RECOMMENDATION_REJECTED",
        actor=reviewer,
        actor_role=AuditEvent.ActorRole.REVIEWER,
        loan_id=exception.loan_id if exception else None,
        batch_id=exception.batch_id if exception else None,
        payload={
            "recommendation_id": recommendation.id,
            "exception_id": exception.id if exception else None,
            "comment": comment_clean,
        },
    )
    return True, f"AI Recommendation #{recommendation.id} rejected."


def create_canonical_validation_rule(
    rule_data: dict[str, Any],
    actor: Any = None,
    default_description: str = "",
) -> tuple[ValidationRule | None, str]:
    """
    Helper function to validate rule parameters and create a canonical ValidationRule record.
    Returns (ValidationRule | None, error_message).
    """
    rule_code = str(rule_data.get("rule_code", "")).strip()
    rule_name = str(rule_data.get("rule_name", "")).strip()
    field_name = str(rule_data.get("field_name", "")).strip()

    SEVERITY_MAP = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    raw_severity = rule_data.get("severity", 2)
    if isinstance(raw_severity, str):
        raw_sev_clean = raw_severity.strip().upper()
        if raw_sev_clean in SEVERITY_MAP:
            severity = SEVERITY_MAP[raw_sev_clean]
        else:
            try:
                severity = int(raw_sev_clean)
            except ValueError:
                severity = 2
    else:
        try:
            severity = int(raw_severity)
        except (ValueError, TypeError):
            severity = 2

    strategy_key = str(rule_data.get("strategy_key", "")).strip()
    parameters = rule_data.get("parameters", {})
    description = str(rule_data.get("description", default_description)).strip()

    if not rule_code or not rule_name or not field_name or not strategy_key:
        return (
            None,
            "Rule payload missing required fields (rule_code, rule_name, field_name, strategy_key).",
        )

    if ValidationRule.objects.filter(rule_code=rule_code).exists():
        return None, f"Validation rule with code '{rule_code}' already exists."

    val_rule = ValidationRule.objects.create(
        rule_code=rule_code,
        rule_name=rule_name,
        description=description,
        field_name=field_name,
        severity=severity,
        strategy_key=strategy_key,
        parameters=parameters if isinstance(parameters, dict) else {},
        is_active=True,
    )
    return val_rule, ""


def _apply_accepted_rule_decision(
    recommendation: AIRecommendation,
    actor: Any,
    comment_clean: str,
) -> tuple[bool, str]:
    """Applies acceptance of an AI rule recommendation and creates canonical ValidationRule."""
    rule_data = recommendation.suggested_rule_data or {}
    if not rule_data.get("success"):
        return (
            False,
            f"Cannot accept AI Recommendation #{recommendation.id}: rule generation payload failed.",
        )

    val_rule, err_msg = create_canonical_validation_rule(
        rule_data=rule_data,
        actor=actor,
        default_description=recommendation.explanation,
    )
    if not val_rule:
        return False, f"Cannot accept AI Recommendation #{recommendation.id}: {err_msg}"

    recommendation.status = AIRecommendation.RecommendationStatus.ACCEPTED
    recommendation.rule = val_rule
    recommendation.reviewed_by = actor if (hasattr(actor, "pk") and actor.pk) else None
    recommendation.reviewed_at = timezone.now()
    recommendation.reviewer_comment = comment_clean
    recommendation.save()

    AuditEvent.log_event(
        event_type="AI_RULE_ACCEPTED",
        actor=actor,
        actor_role=AuditEvent.ActorRole.DATA_OPERATOR,
        payload={
            "recommendation_id": recommendation.id,
            "rule_id": val_rule.id,
            "rule_code": val_rule.rule_code,
            "rule_name": val_rule.rule_name,
            "comment": comment_clean,
        },
    )
    return (
        True,
        f"AI Rule Recommendation #{recommendation.id} accepted. ValidationRule '{val_rule.rule_code}' created successfully.",
    )


def _apply_edited_rule_decision(
    recommendation: AIRecommendation,
    actor: Any,
    comment_clean: str,
    edited_rule_data: dict[str, Any],
) -> tuple[bool, str]:
    """Applies reviewer edit of an AI rule recommendation and creates canonical ValidationRule with modified parameters."""
    rule_data = recommendation.suggested_rule_data or {}
    merged_data = {**rule_data, **edited_rule_data}

    val_rule, err_msg = create_canonical_validation_rule(
        rule_data=merged_data,
        actor=actor,
        default_description=recommendation.explanation,
    )
    if not val_rule:
        return False, f"Cannot edit & accept AI Recommendation #{recommendation.id}: {err_msg}"

    recommendation.status = AIRecommendation.RecommendationStatus.EDITED
    recommendation.rule = val_rule
    recommendation.edited_value = val_rule.rule_code
    recommendation.reviewed_by = actor if (hasattr(actor, "pk") and actor.pk) else None
    recommendation.reviewed_at = timezone.now()
    recommendation.reviewer_comment = comment_clean
    recommendation.save()

    AuditEvent.log_event(
        event_type="AI_RULE_EDITED",
        actor=actor,
        actor_role=AuditEvent.ActorRole.DATA_OPERATOR,
        payload={
            "recommendation_id": recommendation.id,
            "rule_id": val_rule.id,
            "rule_code": val_rule.rule_code,
            "rule_name": val_rule.rule_name,
            "edited_fields": list(edited_rule_data.keys()),
            "comment": comment_clean,
        },
    )
    return (
        True,
        f"AI Rule Recommendation #{recommendation.id} edited & saved. ValidationRule '{val_rule.rule_code}' created successfully.",
    )


def _apply_rejected_rule_decision(
    recommendation: AIRecommendation,
    actor: Any,
    comment_clean: str,
) -> tuple[bool, str]:
    """Applies rejection of an AI rule recommendation."""
    recommendation.status = AIRecommendation.RecommendationStatus.REJECTED
    recommendation.reviewed_by = actor if (hasattr(actor, "pk") and actor.pk) else None
    recommendation.reviewed_at = timezone.now()
    recommendation.reviewer_comment = comment_clean
    recommendation.save()

    AuditEvent.log_event(
        event_type="AI_RULE_REJECTED",
        actor=actor,
        actor_role=AuditEvent.ActorRole.DATA_OPERATOR,
        payload={
            "recommendation_id": recommendation.id,
            "comment": comment_clean,
        },
    )
    return True, f"AI Rule Recommendation #{recommendation.id} rejected."


def call_gemini_for_rules(
    prompt_text: str,
    model_choice: int = AIRecommendation.ModelProvider.GEMINI,
) -> AIRuleResult:
    """
    Parses natural language prompt text into structured validation rule definition using Gemini LLM.

    If GEMINI_API_KEY is missing, API fails, or required fields are missing, returns an explicit
    failure AIRuleResult with error_message. No fallback defaults are used.
    """
    api_key = get_gemini_api_key()
    if not api_key:
        return AIRuleResult(
            rule_code="",
            rule_name="",
            description=prompt_text,
            field_name="",
            severity=0,
            strategy_key="",
            reasoning="Unable to parse rule because GEMINI_API_KEY is missing.",
            confidence_score=0.0,
            success=False,
            error_message=GEMINI_API_KEY_ERROR_MESSAGE,
        )

    try:
        from google import genai

        prompt = (
            "You are an AI rule generator for LoanGuard AI.\n"
            "Translate the following plain text rule request into a structured JSON validation rule.\n\n"
            f"User Request: {prompt_text}\n\n"
            "Return a JSON object strictly containing these keys:\n"
            "1. 'rule_code': Unique rule string starting with R_AI_ (e.g., R_AI_MAX_RATE).\n"
            "2. 'rule_name': Concise rule title.\n"
            "3. 'field_name': Target loan record field (e.g., interest_rate, current_balance, dti_ratio, days_past_due).\n"
            "4. 'severity': Integer (1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL).\n"
            "5. 'strategy_key': Execution strategy. Built-in keys: (MISSING_LOAN_ID, DUPLICATE_LOAN_ID, INVALID_DATE_FORMAT, MATURITY_BEFORE_ORIGINATION, NEGATIVE_PRINCIPAL_BALANCE, VALUE_RANGE, NON_NEGATIVE, REGEX, REQUIRED_FIELD). For generic expression evaluation, set strategy_key to 'GENERIC_EXPRESSION'.\n"
            "6. 'parameters': Dict of strategy parameters. When strategy_key is 'GENERIC_EXPRESSION', parameters MUST include 'operator' and optional 'target_value':\n"
            "   - 'IS_NULL': Fails if field value is missing or empty (parameters: {'operator': 'IS_NULL'}).\n"
            "   - 'NOT_NULL': Fails if field value is present/non-empty (parameters: {'operator': 'NOT_NULL'}).\n"
            "   - '>', '<', '>=', '<=': Numerical threshold check against target_value (e.g., parameters: {'operator': '>', 'target_value': 25.0}).\n"
            "   - '==', '!=': Equality comparison against target_value (e.g., parameters: {'operator': '==', 'target_value': 'INVALID'}).\n"
            "   - 'IN', 'NOT_IN': List membership check against target_value (e.g., parameters: {'operator': 'IN', 'target_value': ['REJECTED', 'TERMINATED']}).\n"
            "7. 'reasoning': Logic behind rule structure.\n"
            "8. 'confidence_score': Float between 0.0 and 1.0.\n"
        )

        model_name = resolve_model_name(model_choice)
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )

        if not response or not response.text:
            err_msg = "Received empty response from Gemini API."
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=prompt_text,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning="Gemini API returned an empty response body.",
                confidence_score=0.0,
                success=False,
                error_message=err_msg,
            )

        cleaned_text = clean_json_response_text(response.text)
        parsed = json.loads(cleaned_text)

        # Enforce strict field presence without fallback defaults
        required_keys = ["rule_code", "rule_name", "field_name", "severity", "strategy_key"]
        missing_keys = [
            k for k in required_keys if k not in parsed or parsed[k] is None or parsed[k] == ""
        ]
        if missing_keys:
            err_msg = f"Gemini response missing required schema fields: {', '.join(missing_keys)}."
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=prompt_text,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning=f"LLM JSON payload incomplete. Missing: {missing_keys}. Raw: {response.text}",
                confidence_score=0.0,
                success=False,
                error_message=err_msg,
            )

        return AIRuleResult(
            rule_code=str(parsed["rule_code"]),
            rule_name=str(parsed["rule_name"]),
            description=prompt_text,
            field_name=str(parsed["field_name"]),
            severity=int(parsed["severity"]),
            strategy_key=str(parsed["strategy_key"]),
            parameters=parsed.get("parameters", {}),
            reasoning=str(parsed.get("reasoning", "")),
            confidence_score=float(parsed.get("confidence_score", 0.0)),
            success=True,
            error_message="",
        )

    except Exception as exc:
        logger.error("Gemini LLM rule parsing failed: %s", exc, exc_info=True)
        err_msg = str(exc)
        return AIRuleResult(
            rule_code="",
            rule_name="",
            description=prompt_text,
            field_name="",
            severity=0,
            strategy_key="",
            reasoning=f"Exception encountered during Gemini API call: {err_msg}",
            confidence_score=0.0,
            success=False,
            error_message=err_msg,
        )
