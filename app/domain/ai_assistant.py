"""
Domain logic for Module D: AI Review Assistant & Guardrails.

Provides OOP strategy classes and registry for LLM providers (Google Gemini, OpenCode Zen, OpenAI),
generating AI recommendations on loan exceptions, translating natural language into validation rules,
enforcing Section 9 human-in-the-loop audit guardrails, and processing reviewer decisions.

Follows SOLID (Single Responsibility, Open/Closed) and DRY design patterns with a extensible
LLM Provider Registry for simple dynamic addition of future models.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.models import AIRecommendation, AuditEvent, LoanException, ValidationRule

logger = logging.getLogger(__name__)

GEMINI_API_KEY_ERROR_MESSAGE = "GEMINI_API_KEY environment variable or setting is not configured."
ZEN_API_KEY_ERROR_MESSAGE = "ZEN_API_KEY environment variable or setting is not configured."
OPENAI_API_KEY_ERROR_MESSAGE = "OPENAI_API_KEY environment variable or setting is not configured."


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


def truncate_long_explanation(
    explanation: str,
    reasoning: str,
    max_chars: int = 800,
) -> tuple[str, str]:
    """Truncates verbose LLM explanation for UI rendering while preserving full details in reasoning."""
    if len(explanation) <= max_chars and explanation.count("\n") < 10:
        return explanation, reasoning

    truncated = explanation[:max_chars].rsplit(" ", 1)[0] + "..."
    extended_reasoning = (
        f"--- Full AI Explanation ---\n{explanation}\n\n"
        f"--- Step-by-Step Reasoning ---\n{reasoning}"
    )
    return truncated, extended_reasoning


class BaseLLMProvider(ABC):
    """
    Abstract Strategy Base Class for all LLM Providers in LoanGuard AI.
    Subclasses implement API execution for specific providers (Gemini, OpenCode Zen, OpenAI).
    """

    provider_id: int
    provider_key: str
    display_name: str

    @property
    @abstractmethod
    def api_key(self) -> str | None:
        """Returns the configured API key for this provider."""
        pass

    @property
    def is_configured(self) -> bool:
        """Returns True if the API key for this provider is configured."""
        return bool(self.api_key)

    @abstractmethod
    def analyze_exception(self, prompt: str) -> AIAnalysisResult:
        """Executes LLM exception analysis for given prompt."""
        pass

    @abstractmethod
    def generate_rule(self, prompt_text: str) -> AIRuleResult:
        """Executes LLM natural language rule generation for given prompt."""
        pass

    def _parse_exception_json(self, raw_text: str) -> AIAnalysisResult:
        """Helper to parse and validate exception analysis JSON payload (DRY)."""
        cleaned = clean_json_response_text(raw_text)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            err = f"Failed to parse JSON response: {exc}"
            return AIAnalysisResult(
                explanation=f"AI generation failed: {err}",
                suggested_value="",
                confidence_score=0.0,
                reasoning=f"Invalid JSON string: {raw_text}",
                raw_response=raw_text,
                success=False,
                error_message=err,
            )

        if "explanation" not in parsed or "suggested_value" not in parsed:
            err = "LLM payload missing required JSON keys ('explanation', 'suggested_value')."
            return AIAnalysisResult(
                explanation=f"AI generation failed: {err}",
                suggested_value="",
                confidence_score=0.0,
                reasoning=f"Parsed JSON payload incomplete: {parsed}",
                raw_response=raw_text,
                success=False,
                error_message=err,
            )

        exp_clean, reasoning_updated = truncate_long_explanation(
            explanation=str(parsed["explanation"]),
            reasoning=str(parsed.get("reasoning", "")),
            max_chars=800,
        )

        return AIAnalysisResult(
            explanation=exp_clean,
            suggested_value=str(parsed["suggested_value"]),
            confidence_score=float(parsed.get("confidence_score", 0.0)),
            reasoning=reasoning_updated,
            raw_response=raw_text,
            success=True,
            error_message="",
        )

    def _parse_rule_json(self, raw_text: str, original_prompt: str) -> AIRuleResult:
        """Helper to parse and validate rule generation JSON payload (DRY)."""
        cleaned = clean_json_response_text(raw_text)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            err = f"Failed to parse JSON rule payload: {exc}"
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=original_prompt,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning=f"Invalid JSON string: {raw_text}",
                confidence_score=0.0,
                success=False,
                error_message=err,
            )

        req_keys = ["rule_code", "rule_name", "field_name", "severity", "strategy_key"]
        missing = [k for k in req_keys if k not in parsed or parsed[k] in (None, "")]
        if missing:
            err = f"LLM rule payload missing required fields: {', '.join(missing)}"
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=original_prompt,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning=f"Incomplete schema fields missing {missing}. Raw: {raw_text}",
                confidence_score=0.0,
                success=False,
                error_message=err,
            )

        SEVERITY_MAP = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        raw_sev = parsed.get("severity", 2)
        if isinstance(raw_sev, str):
            severity_val = SEVERITY_MAP.get(raw_sev.strip().upper(), 2)
        else:
            try:
                severity_val = int(raw_sev)
            except (ValueError, TypeError):
                severity_val = 2

        return AIRuleResult(
            rule_code=str(parsed["rule_code"]),
            rule_name=str(parsed["rule_name"]),
            description=original_prompt,
            field_name=str(parsed["field_name"]),
            severity=severity_val,
            strategy_key=str(parsed["strategy_key"]),
            parameters=parsed.get("parameters", {}),
            reasoning=str(parsed.get("reasoning", "")),
            confidence_score=float(parsed.get("confidence_score", 0.0)),
            success=True,
            error_message="",
        )


class OpenCodeZenProvider(BaseLLMProvider):
    """Concrete Provider Strategy for OpenCode Zen OpenAI-compatible REST API."""

    provider_id = AIRecommendation.ModelProvider.OPENCODE_ZEN
    provider_key = "opencode_zen"
    display_name = "OpenCode Zen"

    @property
    def api_key(self) -> str | None:
        return (
            getattr(settings, "ZEN_API_KEY", None)
            or os.getenv("ZEN_API_KEY")
            or os.getenv("OPENCODE_ZEN_API_KEY")
        )

    @property
    def base_url(self) -> str:
        return getattr(settings, "ZEN_BASE_URL", "https://opencode.ai/zen/v1")

    @property
    def model_name(self) -> str:
        return getattr(settings, "ZEN_MODEL_NAME", "ling-3.0-flash-fin-free")

    def _post_request(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.1
    ) -> tuple[bool, str]:
        if not self.is_configured:
            return False, ZEN_API_KEY_ERROR_MESSAGE

        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }

        import time

        import requests

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=60)
                resp.raise_for_status()
                data = resp.json()

                choices = data.get("choices", [])
                content = choices[0].get("message", {}).get("content", "") if choices else ""
                if not content:
                    return False, "OpenCode Zen API returned empty response content."
                return True, content
            except requests.exceptions.Timeout as exc:
                if attempt < max_retries:
                    logger.warning("OpenCode Zen timeout on attempt %d. Retrying...", attempt + 1)
                    time.sleep(2)
                    continue
                logger.error(
                    "OpenCode Zen call timed out after %d attempts: %s", max_retries + 1, exc
                )
                return False, f"OpenCode Zen API read timed out: {exc}"
            except Exception as exc:
                logger.error("OpenCode Zen call failed: %s", exc, exc_info=True)
                return False, str(exc)

    def analyze_exception(self, prompt: str) -> AIAnalysisResult:
        sys_prompt = (
            "You are a 20+ years expert Loan Data Audit Assistant. "
            "Analyze the loan exception request and respond strictly in valid JSON format with keys: "
            "explanation, suggested_value, confidence_score, reasoning."
        )
        ok, raw = self._post_request(sys_prompt, prompt, temperature=0.2)
        if not ok:
            return AIAnalysisResult(
                explanation=f"AI generation failed: {raw}",
                suggested_value="",
                confidence_score=0.0,
                reasoning=f"OpenCode Zen Execution Error: {raw}",
                raw_response=json.dumps({"error": raw}),
                success=False,
                error_message=raw,
            )
        return self._parse_exception_json(raw)

    def generate_rule(self, prompt_text: str) -> AIRuleResult:
        sys_prompt = (
            "You are an AI rule generator for LoanGuard AI. Respond strictly in valid JSON format."
        )
        full_prompt = (
            "Translate the plain text rule request into a structured JSON validation rule.\n\n"
            f"User Request: {prompt_text}\n\n"
            "Return JSON with keys: rule_code, rule_name, field_name, severity, strategy_key, parameters, reasoning, confidence_score."
        )
        ok, raw = self._post_request(sys_prompt, full_prompt, temperature=0.1)
        if not ok:
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=prompt_text,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning=f"OpenCode Zen Execution Error: {raw}",
                confidence_score=0.0,
                success=False,
                error_message=raw,
            )
        return self._parse_rule_json(raw, prompt_text)


class GeminiProvider(BaseLLMProvider):
    """Concrete Provider Strategy for Google Gemini SDK."""

    provider_id = AIRecommendation.ModelProvider.GEMINI
    provider_key = "gemini"
    display_name = "Google Gemini"

    @property
    def api_key(self) -> str | None:
        key = getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
        return None if not key or key == "GENERATE_GEMINI_API_KEY" else key

    @property
    def model_name(self) -> str:
        return "gemini-3.6-flash"

    def analyze_exception(self, prompt: str) -> AIAnalysisResult:
        if not self.is_configured:
            return AIAnalysisResult(
                explanation=f"AI generation failed: {GEMINI_API_KEY_ERROR_MESSAGE}",
                suggested_value="",
                confidence_score=0.0,
                reasoning="Missing GEMINI_API_KEY.",
                raw_response=json.dumps({"error": GEMINI_API_KEY_ERROR_MESSAGE}),
                success=False,
                error_message=GEMINI_API_KEY_ERROR_MESSAGE,
            )

        try:
            from google import genai

            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            if response and response.text:
                return self._parse_exception_json(response.text)
            return AIAnalysisResult(
                explanation="AI generation failed: Empty response body.",
                suggested_value="",
                confidence_score=0.0,
                reasoning="Gemini returned empty text.",
                raw_response="",
                success=False,
                error_message="Empty response body.",
            )
        except Exception as exc:
            logger.error("Gemini API exception analysis failed: %s", exc, exc_info=True)
            return AIAnalysisResult(
                explanation=f"AI generation failed: {exc}",
                suggested_value="",
                confidence_score=0.0,
                reasoning=str(exc),
                raw_response=json.dumps({"error": str(exc)}),
                success=False,
                error_message=str(exc),
            )

    def generate_rule(self, prompt_text: str) -> AIRuleResult:
        if not self.is_configured:
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=prompt_text,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning="Missing GEMINI_API_KEY.",
                confidence_score=0.0,
                success=False,
                error_message=GEMINI_API_KEY_ERROR_MESSAGE,
            )

        full_prompt = (
            "Translate the plain text rule request into a structured JSON validation rule.\n\n"
            f"User Request: {prompt_text}\n\n"
            "Return JSON with keys: rule_code, rule_name, field_name, severity, strategy_key, parameters, reasoning, confidence_score."
        )

        try:
            from google import genai

            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model_name,
                contents=full_prompt,
                config={"response_mime_type": "application/json"},
            )
            if response and response.text:
                return self._parse_rule_json(response.text, prompt_text)
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=prompt_text,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning="Empty Gemini response.",
                confidence_score=0.0,
                success=False,
                error_message="Empty response body.",
            )
        except Exception as exc:
            logger.error("Gemini API rule generation failed: %s", exc, exc_info=True)
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=prompt_text,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning=str(exc),
                confidence_score=0.0,
                success=False,
                error_message=str(exc),
            )


class OpenAIProvider(BaseLLMProvider):
    """Concrete Provider Strategy for OpenAI REST API."""

    provider_id = AIRecommendation.ModelProvider.CHATGPT
    provider_key = "openai"
    display_name = "ChatGPT / OpenAI"

    @property
    def api_key(self) -> str | None:
        return getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")

    @property
    def model_name(self) -> str:
        return "gpt-4o-mini"

    def _post_request(self, system_prompt: str, user_prompt: str) -> tuple[bool, str]:
        if not self.is_configured:
            return False, OPENAI_API_KEY_ERROR_MESSAGE

        endpoint = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }

        try:
            import requests

            resp = requests.post(endpoint, json=payload, headers=headers, timeout=45)
            resp.raise_for_status()
            data = resp.json()

            choices = data.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            if not content:
                return False, "OpenAI API returned empty response content."
            return True, content
        except Exception as exc:
            logger.error("OpenAI call failed: %s", exc, exc_info=True)
            return False, str(exc)

    def analyze_exception(self, prompt: str) -> AIAnalysisResult:
        sys_prompt = (
            "You are a 20+ years expert Loan Data Audit Assistant. "
            "Analyze the loan exception request and respond strictly in valid JSON format with keys: "
            "explanation, suggested_value, confidence_score, reasoning."
        )
        ok, raw = self._post_request(sys_prompt, prompt)
        if not ok:
            return AIAnalysisResult(
                explanation=f"AI generation failed: {raw}",
                suggested_value="",
                confidence_score=0.0,
                reasoning=f"OpenAI Execution Error: {raw}",
                raw_response=json.dumps({"error": raw}),
                success=False,
                error_message=raw,
            )
        return self._parse_exception_json(raw)

    def generate_rule(self, prompt_text: str) -> AIRuleResult:
        sys_prompt = (
            "You are an AI rule generator for LoanGuard AI. Respond strictly in valid JSON format."
        )
        full_prompt = (
            "Translate the plain text rule request into a structured JSON validation rule.\n\n"
            f"User Request: {prompt_text}\n\n"
            "Return JSON with keys: rule_code, rule_name, field_name, severity, strategy_key, parameters, reasoning, confidence_score."
        )
        ok, raw = self._post_request(sys_prompt, full_prompt)
        if not ok:
            return AIRuleResult(
                rule_code="",
                rule_name="",
                description=prompt_text,
                field_name="",
                severity=0,
                strategy_key="",
                reasoning=f"OpenAI Execution Error: {raw}",
                confidence_score=0.0,
                success=False,
                error_message=raw,
            )
        return self._parse_rule_json(raw, prompt_text)


class LLMProviderRegistry:
    """
    Central Registry for all LLM Providers in LoanGuard AI.
    Allows easy dynamic registration of new AI models (Groq, Ollama, Anthropic, etc.).
    """

    _registry: dict[int, type[BaseLLMProvider]] = {}
    _key_registry: dict[str, type[BaseLLMProvider]] = {}

    @classmethod
    def register(cls, provider_cls: type[BaseLLMProvider]) -> type[BaseLLMProvider]:
        """Registers a new LLM provider class into the global registry."""
        cls._registry[provider_cls.provider_id] = provider_cls
        cls._key_registry[provider_cls.provider_key] = provider_cls
        return provider_cls

    @classmethod
    def get_provider(cls, choice: int | str | None = None) -> BaseLLMProvider:
        """
        Returns an initialized provider strategy instance based on choice or auto-detects
        the first active configured provider.
        """
        target_cls: type[BaseLLMProvider] | None = None

        if isinstance(choice, int) and choice in cls._registry:
            target_cls = cls._registry[choice]
        elif isinstance(choice, str) and choice in cls._key_registry:
            target_cls = cls._key_registry[choice]

        if target_cls:
            instance = target_cls()
            if instance.is_configured:
                return instance

        # Auto-fallback to any configured active provider
        for provider_cls in cls._registry.values():
            instance = provider_cls()
            if instance.is_configured:
                return instance

        # Default fallback to OpenCodeZen / Gemini
        default_cls = cls._registry.get(
            AIRecommendation.ModelProvider.OPENCODE_ZEN,
            cls._registry.get(AIRecommendation.ModelProvider.GEMINI, OpenCodeZenProvider),
        )
        return default_cls()

    @classmethod
    def list_available_providers(cls) -> list[dict[str, Any]]:
        """Returns metadata for all configured active LLM providers."""
        available = []
        for provider_cls in cls._registry.values():
            instance = provider_cls()
            available.append(
                {
                    "provider_id": instance.provider_id,
                    "provider_key": instance.provider_key,
                    "display_name": instance.display_name,
                    "is_configured": instance.is_configured,
                }
            )
        return available


# Auto-register core providers
LLMProviderRegistry.register(OpenCodeZenProvider)
LLMProviderRegistry.register(GeminiProvider)
LLMProviderRegistry.register(OpenAIProvider)


# Helper key resolvers for backward compatibility
def get_gemini_api_key() -> str | None:
    return GeminiProvider().api_key


def get_zen_api_key() -> str | None:
    return OpenCodeZenProvider().api_key


def get_openai_api_key() -> str | None:
    return OpenAIProvider().api_key


def generate_exception_ai_recommendation(
    loan_exception: LoanException,
    user: Any = None,
    servicer_record: dict[str, Any] | None = None,
    doc_manifest: dict[str, Any] | None = None,
    model_choice: int | str | None = AIRecommendation.ModelProvider.OPENCODE_ZEN,
) -> AIRecommendation:
    """
    High-level entry point: Generates an AI recommendation for a LoanException using provider registry.
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

    provider = LLMProviderRegistry.get_provider(model_choice)
    analysis_result = provider.analyze_exception(prompt)

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
            model_name=provider.provider_id,
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


def generate_ai_rule_recommendation(
    prompt_text: str,
    user: Any = None,
    model_choice: int | str | None = AIRecommendation.ModelProvider.OPENCODE_ZEN,
) -> AIRecommendation:
    """
    High-level entry point: Translates natural language into a ValidationRule recommendation using provider registry.
    """
    provider = LLMProviderRegistry.get_provider(model_choice)
    rule_result = provider.generate_rule(prompt_text)

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
            model_name=provider.provider_id,
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
    Dispatches decisions (Accept, Reject, Edit) to type-specific handlers.
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
    """Processes reviewer decisions (Accept, Edit, Reject) for Exception Review recommendations."""
    action_clean = action.lower().strip()
    comment_clean = reviewer_comment.strip()
    edited_clean = edited_value.strip()

    if action_clean == "accept":
        return _apply_exception_decision(
            recommendation=recommendation,
            status=AIRecommendation.RecommendationStatus.ACCEPTED,
            reviewer=reviewer,
            comment=comment_clean,
            applied_value=recommendation.suggested_value,
        )
    elif action_clean == "edit":
        if not edited_clean:
            return False, "Edited value cannot be empty when choosing Edit option."
        return _apply_exception_decision(
            recommendation=recommendation,
            status=AIRecommendation.RecommendationStatus.EDITED,
            reviewer=reviewer,
            comment=comment_clean,
            applied_value=edited_clean,
        )
    elif action_clean == "reject":
        return _apply_exception_decision(
            recommendation=recommendation,
            status=AIRecommendation.RecommendationStatus.REJECTED,
            reviewer=reviewer,
            comment=comment_clean,
        )

    return False, f"Invalid decision action '{action}' for exception recommendation."


def _apply_exception_decision(
    recommendation: AIRecommendation,
    status: int,
    reviewer: Any,
    comment: str,
    applied_value: str | None = None,
) -> tuple[bool, str]:
    """Unified decision applier for Exception AI recommendations."""
    recommendation.status = status
    recommendation.reviewed_by = reviewer if (hasattr(reviewer, "pk") and reviewer.pk) else None
    recommendation.reviewed_at = timezone.now()
    recommendation.reviewer_comment = comment
    if status == AIRecommendation.RecommendationStatus.EDITED and applied_value:
        recommendation.edited_value = applied_value
    recommendation.save()

    exception = recommendation.exception
    if exception and status in (
        AIRecommendation.RecommendationStatus.ACCEPTED,
        AIRecommendation.RecommendationStatus.EDITED,
    ):
        from app.domain.exception_handling import apply_exception_resolution

        if applied_value and exception.raw_record and exception.field_name:
            raw_data = exception.raw_record.raw_data or {}
            raw_data[exception.field_name] = applied_value
            exception.raw_record.raw_data = raw_data
            exception.raw_record.save()
            exception.override_value = applied_value

        res_status = (
            LoanException.ExceptionStatus.RESOLVED_ACCEPTED
            if status == AIRecommendation.RecommendationStatus.ACCEPTED
            else LoanException.ExceptionStatus.RESOLVED_EDITED
        )
        apply_exception_resolution(
            loan_exception=exception,
            actor=reviewer,
            status=res_status,
            comment=comment or f"Applied AI decision: {applied_value}",
        )

    event_map = {
        AIRecommendation.RecommendationStatus.ACCEPTED: "AI_RECOMMENDATION_ACCEPTED",
        AIRecommendation.RecommendationStatus.EDITED: "AI_RECOMMENDATION_EDITED",
        AIRecommendation.RecommendationStatus.REJECTED: "AI_RECOMMENDATION_REJECTED",
    }
    AuditEvent.log_event(
        event_type=event_map.get(status, "AI_RECOMMENDATION_REVIEWED"),
        actor=reviewer,
        actor_role=AuditEvent.ActorRole.REVIEWER,
        loan_id=exception.loan_id if exception else None,
        batch_id=exception.batch_id if exception else None,
        payload={
            "recommendation_id": recommendation.id,
            "exception_id": exception.id if exception else None,
            "applied_value": applied_value,
            "comment": comment,
        },
    )

    action_label = (
        "accepted"
        if status == AIRecommendation.RecommendationStatus.ACCEPTED
        else (
            "edited & saved"
            if status == AIRecommendation.RecommendationStatus.EDITED
            else "rejected"
        )
    )
    return True, f"AI Recommendation #{recommendation.id} {action_label} successfully."


def process_rule_ai_decision(
    recommendation: AIRecommendation,
    action: str,
    actor: Any,
    comment: str = "",
    edited_rule_data: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Processes reviewer decisions (Accept, Edit, Reject) for Rule Generation recommendations."""
    action_clean = action.lower().strip()
    comment_clean = comment.strip()

    if action_clean == "accept":
        rule_data = recommendation.suggested_rule_data or {}
        if not rule_data.get("success"):
            return (
                False,
                f"Cannot accept AI Recommendation #{recommendation.id}: generation failed.",
            )
        return _apply_rule_decision(recommendation, actor, comment_clean, rule_data, is_edit=False)

    elif action_clean == "edit":
        if not edited_rule_data:
            return False, "Edited rule data cannot be empty when choosing Edit option."
        merged_data = {**(recommendation.suggested_rule_data or {}), **edited_rule_data}
        return _apply_rule_decision(recommendation, actor, comment_clean, merged_data, is_edit=True)

    elif action_clean == "reject":
        recommendation.status = AIRecommendation.RecommendationStatus.REJECTED
        recommendation.reviewed_by = actor if (hasattr(actor, "pk") and actor.pk) else None
        recommendation.reviewed_at = timezone.now()
        recommendation.reviewer_comment = comment_clean
        recommendation.save()

        AuditEvent.log_event(
            event_type="AI_RULE_REJECTED",
            actor=actor,
            actor_role=AuditEvent.ActorRole.DATA_OPERATOR,
            payload={"recommendation_id": recommendation.id, "comment": comment_clean},
        )
        return True, f"AI Rule Recommendation #{recommendation.id} rejected."

    return False, f"Invalid decision action '{action}' for rule recommendation."


def _apply_rule_decision(
    recommendation: AIRecommendation,
    actor: Any,
    comment: str,
    rule_payload: dict[str, Any],
    is_edit: bool = False,
) -> tuple[bool, str]:
    """Helper to validate and create canonical ValidationRule for Accept/Edit actions."""
    val_rule, err_msg = create_canonical_validation_rule(
        rule_data=rule_payload,
        actor=actor,
        default_description=recommendation.explanation,
    )
    if not val_rule:
        return False, f"Cannot approve AI Recommendation #{recommendation.id}: {err_msg}"

    recommendation.status = (
        AIRecommendation.RecommendationStatus.EDITED
        if is_edit
        else AIRecommendation.RecommendationStatus.ACCEPTED
    )
    recommendation.rule = val_rule
    if is_edit:
        recommendation.edited_value = val_rule.rule_code
    recommendation.reviewed_by = actor if (hasattr(actor, "pk") and actor.pk) else None
    recommendation.reviewed_at = timezone.now()
    recommendation.reviewer_comment = comment
    recommendation.save()

    AuditEvent.log_event(
        event_type="AI_RULE_EDITED" if is_edit else "AI_RULE_ACCEPTED",
        actor=actor,
        actor_role=AuditEvent.ActorRole.DATA_OPERATOR,
        payload={
            "recommendation_id": recommendation.id,
            "rule_id": val_rule.id,
            "rule_code": val_rule.rule_code,
            "comment": comment,
        },
    )
    label = "edited & accepted" if is_edit else "accepted"
    return (
        True,
        f"AI Rule Recommendation #{recommendation.id} {label}. ValidationRule '{val_rule.rule_code}' created.",
    )


def create_canonical_validation_rule(
    rule_data: dict[str, Any],
    actor: Any = None,
    default_description: str = "",
) -> tuple[ValidationRule | None, str]:
    """Validates rule parameters and creates canonical ValidationRule record."""
    rule_code = str(rule_data.get("rule_code", "")).strip()
    rule_name = str(rule_data.get("rule_name", "")).strip()
    field_name = str(rule_data.get("field_name", "")).strip()

    SEVERITY_MAP = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    raw_sev = rule_data.get("severity", 2)
    if isinstance(raw_sev, str):
        clean_sev = raw_sev.strip().upper()
        severity = SEVERITY_MAP.get(clean_sev, 2)
    else:
        try:
            severity = int(raw_sev)
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
        "4. 'reasoning': Step-by-Step logic used.\n"
    )


# Convenient Top-Level Helper Interfaces
def call_ai_for_exception(prompt: str, model_choice: int | str | None = None) -> AIAnalysisResult:
    """Convenience helper to analyze an exception prompt with the active LLM provider strategy."""
    return LLMProviderRegistry.get_provider(model_choice).analyze_exception(prompt)


def call_ai_for_rules(prompt_text: str, model_choice: int | str | None = None) -> AIRuleResult:
    """Convenience helper to generate a rule from text with the active LLM provider strategy."""
    return LLMProviderRegistry.get_provider(model_choice).generate_rule(prompt_text)
