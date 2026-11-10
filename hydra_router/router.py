"""HydraRouter: Centralized Multi-Model router powered by LiteLLM."""
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Prevent LiteLLM from making unnecessary remote network requests during import
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm

litellm.telemetry = False
litellm.drop_params = True

from hydra_router.exceptions import (
    AllModelsFailedError,
    MalformedResponseError,
    ModelProviderError,
    ProviderErrorKind,
    StructuredOutputValidationError,
    normalize_provider_error,
    redact_credentials,
)
from hydra_router.registry import ModelRegistry, ModelRole
from hydra_router.telemetry import ModelCallMetrics, RouterTelemetry

logger = logging.getLogger("hydrameda.hydra_router")


@dataclass
class RouterResponse:
    """Standardized response from HydraRouter."""
    content: str
    role: str
    model_used: str
    provider: str
    metrics: ModelCallMetrics
    parsed: Any | None = None


class HydraRouter:
    """Centralized Multi-Model Router orchestrating LLM calls through LiteLLM."""

    def __init__(self, registry: ModelRegistry | None = None, telemetry: RouterTelemetry | None = None):
        self.registry = registry or ModelRegistry()
        self.telemetry = telemetry or RouterTelemetry()
        self._mock_handler: Callable[..., Any] | None = None

    def set_mock_handler(self, handler: Callable[..., Any] | None) -> None:
        """Sets a mock completion handler for unit/integration testing."""
        self._mock_handler = handler

    def clear_mock_handler(self) -> None:
        """Clears the mock completion handler."""
        self._mock_handler = None

    def complete(
        self,
        role: ModelRole | str,
        prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        validator: Callable[[str], Any] | None = None,
        mock_response: str | None = None,
        **kwargs
    ) -> RouterResponse:
        """Routes a completion request to the appropriate role-based model chain via LiteLLM."""
        norm_role = ModelRole.normalize(str(role))
        role_config = self.registry.get_role_config(norm_role)
        active_models = self.registry.get_active_models(norm_role)

        # Prepare messages
        if messages is None:
            formatted_msgs = []
            if system_prompt:
                formatted_msgs.append({"role": "system", "content": system_prompt})
            if prompt is not None:
                formatted_msgs.append({"role": "user", "content": prompt})
            else:
                formatted_msgs.append({"role": "user", "content": ""})
        else:
            formatted_msgs = list(messages)
            if system_prompt and not any(m.get("role") == "system" for m in formatted_msgs):
                formatted_msgs.insert(0, {"role": "system", "content": system_prompt})

        # Check if pluggable mock handler is active
        if self._mock_handler is not None:
            start_t = time.perf_counter()
            content, model_used = self._mock_handler(norm_role.value, prompt or formatted_msgs[-1].get("content", ""))
            latency = (time.perf_counter() - start_t) * 1000.0
            metrics = ModelCallMetrics(
                role=norm_role.value,
                model=model_used,
                provider="mock",
                latency_ms=latency,
                prompt_tokens=len(str(formatted_msgs)) // 4,
                completion_tokens=len(content) // 4,
                total_tokens=(len(str(formatted_msgs)) + len(content)) // 4,
                success=True
            )
            self.telemetry.record_call(metrics)
            parsed = validator(content) if validator else None
            return RouterResponse(
                content=content,
                role=norm_role.value,
                model_used=model_used,
                provider="mock",
                metrics=metrics,
                parsed=parsed
            )

        attempts_log = []
        env_vars = self.registry._get_env_vars()

        for idx, spec in enumerate(active_models):
            api_key = spec.resolve_api_key(env_vars)
            api_base = spec.api_base or env_vars.get(f"{spec.provider.upper()}_API_BASE")
            req_timeout = timeout if timeout is not None else spec.timeout
            req_temp = temperature if temperature is not None else role_config.default_temperature
            req_max_tokens = max_tokens if max_tokens is not None else role_config.default_max_tokens

            call_kwargs = {
                "model": spec.model,
                "messages": formatted_msgs,
                "temperature": req_temp,
                "max_tokens": req_max_tokens,
                "timeout": req_timeout,
            }
            if api_key:
                call_kwargs["api_key"] = api_key
            if api_base:
                call_kwargs["api_base"] = api_base
            if mock_response is not None:
                call_kwargs["mock_response"] = mock_response

            call_kwargs.update(kwargs)

            start_time = time.perf_counter()
            try:
                # Dispatch through LiteLLM
                resp = litellm.completion(**call_kwargs)
                latency_ms = (time.perf_counter() - start_time) * 1000.0

                content = resp.choices[0].message.content or ""
                if not content.strip():
                    raise MalformedResponseError(
                        model=spec.model,
                        provider=spec.provider,
                        message="Provider returned empty or whitespace-only completion content"
                    )

                # Token usage tracking
                usage = getattr(resp, "usage", None)
                p_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                c_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                t_tokens = getattr(usage, "total_tokens", 0) if usage else (p_tokens + c_tokens)

                # Validate structured output if validator supplied
                parsed_data = None
                if validator:
                    parsed_data = validator(content)

                metrics = ModelCallMetrics(
                    role=norm_role.value,
                    model=spec.model,
                    provider=spec.provider,
                    latency_ms=latency_ms,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=t_tokens,
                    success=True
                )
                self.telemetry.record_call(metrics)

                return RouterResponse(
                    content=content,
                    role=norm_role.value,
                    model_used=spec.model,
                    provider=spec.provider,
                    metrics=metrics,
                    parsed=parsed_data
                )

            except Exception as e:  # noqa: BLE001 - Router fallback loop catches arbitrary provider SDK and HTTP exceptions
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                norm_err: ModelProviderError
                if isinstance(e, StructuredOutputValidationError):
                    norm_err = MalformedResponseError(
                        model=spec.model,
                        provider=spec.provider,
                        message=f"Structured output validation failed: {e}",
                    )
                else:
                    norm_err = normalize_provider_error(e, model=spec.model, provider=spec.provider)

                err_str = redact_credentials(str(norm_err))
                err_type = norm_err.__class__.__name__
                error_kind = norm_err.error_kind.value
                is_rate_limit = norm_err.error_kind == ProviderErrorKind.RATE_LIMIT
                is_timeout = norm_err.error_kind == ProviderErrorKind.TIMEOUT

                metrics = ModelCallMetrics(
                    role=norm_role.value,
                    model=spec.model,
                    provider=spec.provider,
                    latency_ms=latency_ms,
                    success=False,
                    error_type=err_type,
                    error_message=err_str
                )
                self.telemetry.record_call(metrics)
                attempts_log.append({
                    "model": spec.model,
                    "provider": spec.provider,
                    "error": err_str,
                    "error_type": err_type,
                    "error_kind": error_kind,
                    "is_rate_limit": is_rate_limit,
                    "is_timeout": is_timeout
                })

                # If next fallback model exists, log fallback transition
                if idx + 1 < len(active_models):
                    next_spec = active_models[idx + 1]
                    self.telemetry.record_fallback(
                        role=norm_role.value,
                        from_model=spec.model,
                        to_model=next_spec.model,
                        reason=redact_credentials(f"{err_type}: {err_str[:120]}")
                    )
                    # Brief pause if rate-limited
                    if is_rate_limit:
                        time.sleep(2.0)
                    continue

        # All models in fallback chain failed
        raise AllModelsFailedError(role=norm_role.value, attempts=attempts_log)


_GLOBAL_ROUTER: HydraRouter | None = None

def get_router() -> HydraRouter:
    """Returns or initializes the global singleton HydraRouter instance."""
    global _GLOBAL_ROUTER
    if _GLOBAL_ROUTER is None:
        _GLOBAL_ROUTER = HydraRouter()
    return _GLOBAL_ROUTER
