"""Custom exceptions and provider-independent error normalization for HydraRouter."""
import os
import re
from enum import Enum
from typing import Any


def redact_credentials(text: str) -> str:
    """Redacts API keys, tokens, and sensitive credentials from error messages and logs."""
    if not text:
        return ""

    redacted = str(text)

    # Redact known environment secrets if present
    for k, v in os.environ.items():
        if (k.endswith(("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_AUTH")) or "KEY" in k) and v and len(v) >= 6:
            redacted = redacted.replace(v, "[REDACTED]")

    # Common API key and credential patterns
    patterns = [
        # OpenAI / LiteLLM / generic sk- keys
        (r"sk-[a-zA-Z0-9_\-]{16,}", "[REDACTED_API_KEY]"),
        # Groq gsk_ keys
        (r"gsk_[a-zA-Z0-9_\-]{16,}", "[REDACTED_API_KEY]"),
        # Cerebras csk- keys
        (r"csk-[a-zA-Z0-9_\-]{16,}", "[REDACTED_API_KEY]"),
        # Google AIza keys
        (r"AIza[0-9A-Za-z\-_]{20,}", "[REDACTED_API_KEY]"),
        # Bearer tokens
        (r"(Bearer\s+)[a-zA-Z0-9_\-\.]{12,}", r"\1[REDACTED_BEARER_TOKEN]"),
        # Generic query/header tokens: api_key=xyz, token=xyz, password=xyz
        (r"((?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?)[a-zA-Z0-9_\-]{8,}(['\"]?)", r"\1[REDACTED]\2"),
    ]

    for pat, repl in patterns:
        redacted = re.sub(pat, repl, redacted, flags=re.IGNORECASE)

    return redacted


class ProviderErrorKind(str, Enum):
    """Normalized categories for provider errors."""
    AUTHENTICATION_FAILURE = "authentication_failure"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    UNKNOWN = "unknown"


class HydraRouterError(Exception):
    """Base exception for all HydraRouter operations."""


class RoleNotFoundError(HydraRouterError):
    """Raised when an unknown role is requested."""


class ConfigurationError(HydraRouterError):
    """Raised when router or model configuration is invalid."""


class ModelProviderError(HydraRouterError):
    """Normalized exception representing an individual model provider failure."""
    def __init__(
        self,
        model: str,
        provider: str,
        message: str,
        error_kind: ProviderErrorKind = ProviderErrorKind.UNKNOWN,
        status_code: int | None = None,
        is_rate_limit: bool = False,
        is_timeout: bool = False,
        original_error: Exception | None = None
    ):
        clean_msg = redact_credentials(message)
        super().__init__(f"[{provider}/{model}] ({error_kind.value}) {clean_msg}")
        self.model = model
        self.provider = provider
        self.message = clean_msg
        self.error_kind = error_kind
        self.status_code = status_code
        self.is_rate_limit = is_rate_limit or (error_kind == ProviderErrorKind.RATE_LIMIT)
        self.is_timeout = is_timeout or (error_kind == ProviderErrorKind.TIMEOUT)
        self.original_error = original_error


class AuthenticationError(ModelProviderError):
    """Raised on 401/403 or invalid API credentials."""
    def __init__(self, model: str, provider: str, message: str, **kwargs: Any):
        super().__init__(model, provider, message, error_kind=ProviderErrorKind.AUTHENTICATION_FAILURE, **kwargs)


class RateLimitError(ModelProviderError):
    """Raised on 429 or quota exceeded."""
    def __init__(self, model: str, provider: str, message: str, **kwargs: Any):
        super().__init__(model, provider, message, error_kind=ProviderErrorKind.RATE_LIMIT, is_rate_limit=True, **kwargs)


class ProviderTimeoutError(ModelProviderError):
    """Raised when a provider request times out."""
    def __init__(self, model: str, provider: str, message: str, **kwargs: Any):
        super().__init__(model, provider, message, error_kind=ProviderErrorKind.TIMEOUT, is_timeout=True, **kwargs)


class ContextLengthExceededError(ModelProviderError):
    """Raised when prompt or context exceeds provider's maximum token limit."""
    def __init__(self, model: str, provider: str, message: str, **kwargs: Any):
        super().__init__(model, provider, message, error_kind=ProviderErrorKind.CONTEXT_LENGTH_EXCEEDED, **kwargs)


class ProviderUnavailableError(ModelProviderError):
    """Raised on 500, 502, 503, 504, or network connection drop."""
    def __init__(self, model: str, provider: str, message: str, **kwargs: Any):
        super().__init__(model, provider, message, error_kind=ProviderErrorKind.PROVIDER_UNAVAILABLE, **kwargs)


class MalformedResponseError(ModelProviderError):
    """Raised when a provider returns empty, unparseable, or schema-violating output."""
    def __init__(self, model: str, provider: str, message: str, **kwargs: Any):
        super().__init__(model, provider, message, error_kind=ProviderErrorKind.MALFORMED_RESPONSE, **kwargs)


class AllModelsFailedError(HydraRouterError):
    """Raised when all candidate models in a role's fallback chain fail."""
    def __init__(self, role: str, attempts: list[dict[str, Any]]):
        self.role = role
        # Redact any sensitive credentials in the attempts log
        self.attempts = []
        for a in attempts:
            clean_attempt = dict(a)
            if "error" in clean_attempt:
                clean_attempt["error"] = redact_credentials(str(clean_attempt["error"]))
            self.attempts.append(clean_attempt)

        summary = "; ".join(
            f"{a.get('model', 'unknown')} ({a.get('error', 'error')})"
            for a in self.attempts
        )
        super().__init__(f"All models failed for role '{role}'. Attempts: {summary}")


class StructuredOutputValidationError(HydraRouterError):
    """Raised when LLM output fails schema or structural validation."""
    def __init__(self, message: str, raw_output: str, schema_name: str | None = None):
        super().__init__(f"Output validation failed for {schema_name or 'schema'}: {message}")
        self.raw_output = raw_output
        self.schema_name = schema_name


def normalize_provider_error(
    exc: Exception,
    model: str = "",
    provider: str = ""
) -> ModelProviderError:
    """Normalizes arbitrary provider and network exceptions into provider-independent errors."""
    if isinstance(exc, ModelProviderError):
        return exc

    err_msg = str(exc)
    err_lower = err_msg.lower()
    exc_type_name = type(exc).__name__.lower()
    status_code = getattr(exc, "status_code", None)

    if status_code is None:
        m = re.search(r"\b(401|403|429|408|500|502|503|504)\b", err_msg)
        if m:
            status_code = int(m.group(1))

    # 1. Authentication failure
    if (
        status_code in (401, 403)
        or "authentication" in exc_type_name
        or any(k in err_lower for k in [
            "unauthorized", "invalid api key", "invalid_api_key",
            "authenticationerror", "permission_denied", "access denied", "forbidden"
        ])
    ):
        return AuthenticationError(
            model=model,
            provider=provider,
            message=err_msg,
            status_code=status_code or 401,
            original_error=exc
        )

    # 2. Rate limit
    if (
        status_code == 429
        or "ratelimit" in exc_type_name
        or any(k in err_lower for k in [
            "rate limit", "ratelimit", "too many requests", "quota exceeded", "resource exhausted", "429"
        ])
    ):
        return RateLimitError(
            model=model,
            provider=provider,
            message=err_msg,
            status_code=status_code or 429,
            original_error=exc
        )

    # 3. Timeout
    if (
        status_code in (408, 504)
        or isinstance(exc, TimeoutError)
        or "timeout" in exc_type_name
        or any(k in err_lower for k in ["timeout", "timed out", "deadline exceeded", "request timed out"])
    ):
        return ProviderTimeoutError(
            model=model,
            provider=provider,
            message=err_msg,
            status_code=status_code or 408,
            original_error=exc
        )

    # 4. Context length exceeded
    if (
        "contextwindow" in exc_type_name
        or "contextlength" in exc_type_name
        or (status_code == 400 and any(k in err_lower for k in ["context", "token limit", "maximum context"]))
        or any(k in err_lower for k in [
            "context length", "context_length", "maximum context length",
            "token limit exceeded", "prompt is too long", "exceeds token limit"
        ])
    ):
        return ContextLengthExceededError(
            model=model,
            provider=provider,
            message=err_msg,
            status_code=status_code or 400,
            original_error=exc
        )

    # 5. Provider unavailable
    if (
        status_code in (500, 502, 503)
        or any(k in exc_type_name for k in ["connection", "unavailable", "servererror", "badgateway"])
        or any(k in err_lower for k in [
            "service unavailable", "bad gateway", "internal server error",
            "connection refused", "connection error", "connection reset", "cannot connect", "503", "502", "500"
        ])
    ):
        return ProviderUnavailableError(
            model=model,
            provider=provider,
            message=err_msg,
            status_code=status_code or 503,
            original_error=exc
        )

    # 6. Malformed response
    if (
        isinstance(exc, StructuredOutputValidationError)
        or any(k in exc_type_name for k in ["jsondecodeerror", "validationerror", "decodeerror"])
        or any(k in err_lower for k in [
            "malformed", "jsondecodeerror", "invalid json", "schema validation",
            "empty response", "decode json", "failed to decode", "json parse",
            "parsing error", "unparseable"
        ])
    ):
        return MalformedResponseError(
            model=model,
            provider=provider,
            message=err_msg,
            status_code=status_code,
            original_error=exc
        )

    # Fallback to generic ModelProviderError
    return ModelProviderError(
        model=model,
        provider=provider,
        message=err_msg,
        error_kind=ProviderErrorKind.UNKNOWN,
        status_code=status_code,
        original_error=exc
    )
