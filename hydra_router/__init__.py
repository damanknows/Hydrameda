"""HydraRouter: Centralized Multi-Model Routing Layer for Hydrameda."""
from hydra_router.registry import ModelRole, ModelSpec, RoleConfig, ModelRegistry
from hydra_router.telemetry import ModelCallMetrics, RouterTelemetry
from hydra_router.validator import OutputValidator
from hydra_router.exceptions import (
    HydraRouterError,
    RoleNotFoundError,
    ConfigurationError,
    ModelProviderError,
    AllModelsFailedError,
    StructuredOutputValidationError,
)
from hydra_router.router import HydraRouter, RouterResponse, get_router

__all__ = [
    "HydraRouter",
    "RouterResponse",
    "ModelRole",
    "ModelSpec",
    "RoleConfig",
    "ModelRegistry",
    "ModelCallMetrics",
    "RouterTelemetry",
    "OutputValidator",
    "HydraRouterError",
    "RoleNotFoundError",
    "ConfigurationError",
    "ModelProviderError",
    "AllModelsFailedError",
    "StructuredOutputValidationError",
    "get_router",
]
