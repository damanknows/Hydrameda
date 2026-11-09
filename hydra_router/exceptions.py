"""Custom exceptions for HydraRouter."""
from typing import Any


class HydraRouterError(Exception):
    """Base exception for all HydraRouter operations."""


class RoleNotFoundError(HydraRouterError):
    """Raised when an unknown role is requested."""


class ConfigurationError(HydraRouterError):
    """Raised when router or model configuration is invalid."""


class ModelProviderError(HydraRouterError):
    """Raised when an individual model provider fails."""
    def __init__(
        self,
        model: str,
        provider: str,
        message: str,
        status_code: int | None = None,
        is_rate_limit: bool = False,
        is_timeout: bool = False,
        original_error: Exception | None = None
    ):
        super().__init__(f"[{provider}/{model}] {message}")
        self.model = model
        self.provider = provider
        self.message = message
        self.status_code = status_code
        self.is_rate_limit = is_rate_limit
        self.is_timeout = is_timeout
        self.original_error = original_error


class AllModelsFailedError(HydraRouterError):
    """Raised when all candidate models in a role's fallback chain fail."""
    def __init__(self, role: str, attempts: list[dict[str, Any]]):
        self.role = role
        self.attempts = attempts
        summary = "; ".join(
            f"{a.get('model', 'unknown')} ({a.get('error', 'error')})"
            for a in attempts
        )
        super().__init__(f"All models failed for role '{role}'. Attempts: {summary}")


class StructuredOutputValidationError(HydraRouterError):
    """Raised when LLM output fails schema or structural validation."""
    def __init__(self, message: str, raw_output: str, schema_name: str | None = None):
        super().__init__(f"Output validation failed for {schema_name or 'schema'}: {message}")
        self.raw_output = raw_output
        self.schema_name = schema_name
