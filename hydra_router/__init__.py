"""HydraRouter: Centralized Multi-Model Routing Layer for Hydrameda."""
from hydra_router.exceptions import (
    AllModelsFailedError,
    AuthenticationError,
    ConfigurationError,
    ContextLengthExceededError,
    HydraRouterError,
    MalformedResponseError,
    ModelProviderError,
    ProviderErrorKind,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    RoleNotFoundError,
    StructuredOutputValidationError,
    normalize_provider_error,
    redact_credentials,
)
from hydra_router.registry import ModelRegistry, ModelRole, ModelSpec, RoleConfig
from hydra_router.router import HydraRouter, RouterResponse, get_router
from hydra_router.telemetry import ModelCallMetrics, RouterTelemetry
from hydra_router.validator import OutputValidator

__all__ = [
    "AllModelsFailedError",
    "AuthenticationError",
    "ConfigurationError",
    "ContextLengthExceededError",
    "HydraRouter",
    "HydraRouterError",
    "MalformedResponseError",
    "ModelCallMetrics",
    "ModelProviderError",
    "ModelRegistry",
    "ModelRole",
    "ModelSpec",
    "OutputValidator",
    "ProviderErrorKind",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RateLimitError",
    "RoleConfig",
    "RoleNotFoundError",
    "RouterResponse",
    "RouterTelemetry",
    "StructuredOutputValidationError",
    "get_router",
    "normalize_provider_error",
    "redact_credentials",
]
