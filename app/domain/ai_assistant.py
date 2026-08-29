"""
Domain logic for Module D: AI Review Assistant & Guardrails.

Provides reusable domain functions and dataclasses for generating AI recommendations on loan exceptions,
translating natural language into validation rules, enforcing Section 9 human-in-the-loop
audit guardrails, and processing reviewer decisions (Accept, Reject, Edit).
Follows the Stepdown Rule (Clean Code top-down narrative structure).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.models import AIRecommendation, AuditEvent, LoanException
from config.settings.base import GEMINI_API_KEY

logger = logging.getLogger(__name__)

GEMINI_API_KEY_ERROR_MESSAGE = "GEMINI_API_KEY environment variable or setting is not configured."
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
    model_choice: int = AIRecommendation.ModelProvider.GEMINI_2_5_FLASH,
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

    analysis_result: AIAnalysisResult = call_gemini_llm_analysis(prompt=prompt)

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
            created_by=user,
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
def process_reviewer_ai_decision(
    recommendation: AIRecommendation,
    action: str,
    reviewer: Any,
    reviewer_comment: str = "",
    edited_value: str = "",
) -> tuple[bool, str]:
    """
    High-level entry point: Enforces Section 9 Human-In-The-Loop compliance.

    Reviewer explicitly accepts, rejects, or edits an AI recommendation before
    any canonical data state change is applied.
    """
    action_clean = action.lower().strip()
    comment_clean = reviewer_comment.strip()
    edited_clean = edited_value.strip()

    if recommendation.status != AIRecommendation.RecommendationStatus.PENDING:
        return False, f"Recommendation #{recommendation.id} has already been reviewed."

    if action_clean == "accept":
        return _apply_accepted_ai_decision(
            recommendation=recommendation,
            reviewer=reviewer,
            comment_clean=comment_clean,
        )
    elif action_clean == "edit":
        if not edited_clean:
            return False, "Edited value cannot be empty when choosing Edit option."
        return _apply_edited_ai_decision(
            recommendation=recommendation,
            reviewer=reviewer,
            comment_clean=comment_clean,
            edited_clean=edited_clean,
        )
    elif action_clean == "reject":
        return _apply_rejected_ai_decision(
            recommendation=recommendation,
            reviewer=reviewer,
            comment_clean=comment_clean,
        )

    return False, f"Invalid decision action '{action}'."


def generate_ai_rule_recommendation(
    prompt_text: str,
    user: Any = None,
    model_choice: int = AIRecommendation.ModelProvider.GEMINI_2_5_FLASH,
) -> AIRecommendation:
    """
    High-level entry point: Translates natural language into a ValidationRule recommendation.
    If generation fails, records the failure reason in explanation for UI display.
    """
    rule_result: AIRuleResult = parse_natural_language_rule(prompt_text)

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
            created_by=user,
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
    """Builds structured prompt string for LLM analysis with conciseness directives."""
    rule_code = loan_exception.rule.rule_code if loan_exception.rule else loan_exception.rule_code
    rule_desc = (
        loan_exception.rule.description if loan_exception.rule else loan_exception.description
    )
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


def call_gemini_llm_analysis(prompt: str) -> AIAnalysisResult:
    """
    Calls Google Gemini API using GEMINI_API_KEY.

    If API key is unconfigured or call fails, returns failure AIAnalysisResult.
    Applies length truncation safeguards on 'explanation' to prevent UI overflow.
    """
    if not GEMINI_API_KEY:
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

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )

        if response and response.text:
            parsed = json.loads(response.text)

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


def _apply_accepted_ai_decision(
    recommendation: AIRecommendation,
    reviewer: Any,
    comment_clean: str,
) -> tuple[bool, str]:
    """Applies reviewer acceptance of an AI recommendation and updates underlying exception."""
    recommendation.status = AIRecommendation.RecommendationStatus.ACCEPTED
    recommendation.reviewed_by = reviewer
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


def _apply_edited_ai_decision(
    recommendation: AIRecommendation,
    reviewer: Any,
    comment_clean: str,
    edited_clean: str,
) -> tuple[bool, str]:
    """Applies reviewer edit of an AI recommendation and updates underlying exception."""
    recommendation.status = AIRecommendation.RecommendationStatus.EDITED
    recommendation.edited_value = edited_clean
    recommendation.reviewed_by = reviewer
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


def _apply_rejected_ai_decision(
    recommendation: AIRecommendation,
    reviewer: Any,
    comment_clean: str,
) -> tuple[bool, str]:
    """Applies reviewer rejection of an AI recommendation."""
    recommendation.status = AIRecommendation.RecommendationStatus.REJECTED
    recommendation.reviewed_by = reviewer
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


def parse_natural_language_rule(prompt_text: str) -> AIRuleResult:
    """
    Parses natural language prompt text into structured validation rule definition using Gemini LLM.

    If GEMINI_API_KEY is missing, API fails, or required fields are missing, returns an explicit
    failure AIRuleResult with error_message. No fallback defaults are used.
    """
    if not GEMINI_API_KEY:
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
            "5. 'strategy_key': Execution strategy (e.g., VALUE_RANGE, NON_NEGATIVE, REGEX, REQUIRED_FIELD).\n"
            "6. 'parameters': Dict of strategy parameters (e.g., {'min': 0.0, 'max': 25.0}).\n"
            "7. 'reasoning': Logic behind rule structure.\n"
            "8. 'confidence_score': Float between 0.0 and 1.0.\n"
        )

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-3.6-flash",
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

        parsed = json.loads(response.text)

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
