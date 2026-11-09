"""HydraRouter: Centralized Multi-Model Routing Layer for Hydrameda."""
from hydra_router.exceptions import (
    AllModelsFailedError,
    ConfigurationError,
    HydraRouterError,
    ModelProviderError,
    RoleNotFoundError,
    StructuredOutputValidationError,
)
from hydra_router.registry import ModelRegistry, ModelRole, ModelSpec, RoleConfig
from hydra_router.router import HydraRouter, RouterResponse, get_router
from hydra_router.telemetry import ModelCallMetrics, RouterTelemetry
from hydra_router.validator import OutputValidator

__all__ = [
    "AllModelsFailedError",
    "ConfigurationError",
    "HydraRouter",
    "HydraRouterError",
    "ModelCallMetrics",
    "ModelProviderError",
    "ModelRegistry",
    "ModelRole",
    "ModelSpec",
    "OutputValidator",
    "RoleConfig",
    "RoleNotFoundError",
    "RouterResponse",
    "RouterTelemetry",
    "StructuredOutputValidationError",
    "get_router",
]
