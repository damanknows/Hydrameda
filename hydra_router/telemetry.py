"""Token usage, latency tracking, and structured model failure logging."""
import time
import uuid
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from threading import Lock

logger = logging.getLogger("hydrameda.hydra_router")


@dataclass
class ModelCallMetrics:
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    role: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    success: bool = False
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RouterTelemetry:
    """Thread-safe collector for router performance, token consumption, and errors."""

    def __init__(self):
        self._lock = Lock()
        self._history: List[ModelCallMetrics] = []
        self._fallback_count: int = 0

    def record_call(self, metrics: ModelCallMetrics) -> None:
        """Records completed model invocation metrics."""
        with self._lock:
            self._history.append(metrics)

        if not metrics.success:
            logger.warning(
                "[HydraRouter Failure] Call %s | Role: %s | Model: %s (%s) | "
                "Error: %s (%s) | Latency: %.2f ms",
                metrics.call_id[:8],
                metrics.role,
                metrics.model,
                metrics.provider,
                metrics.error_type or "UnknownError",
                metrics.error_message or "",
                metrics.latency_ms
            )
        else:
            logger.debug(
                "[HydraRouter Success] Call %s | Role: %s | Model: %s | "
                "Tokens: %d (in: %d, out: %d) | Latency: %.2f ms",
                metrics.call_id[:8],
                metrics.role,
                metrics.model,
                metrics.total_tokens,
                metrics.prompt_tokens,
                metrics.completion_tokens,
                metrics.latency_ms
            )

    def record_fallback(self, role: str, from_model: str, to_model: str, reason: str) -> None:
        """Logs and tracks a fallback event."""
        with self._lock:
            self._fallback_count += 1
        logger.info(
            "[HydraRouter Fallback] Role: %s | Switching from '%s' to '%s' | Reason: %s",
            role, from_model, to_model, reason
        )

    def get_summary(self) -> Dict[str, Any]:
        """Calculates global aggregation statistics."""
        with self._lock:
            calls = list(self._history)
            fallbacks = self._fallback_count

        total_calls = len(calls)
        successful_calls = sum(1 for c in calls if c.success)
        failed_calls = total_calls - successful_calls

        total_latency = sum(c.latency_ms for c in calls)
        avg_latency = (total_latency / total_calls) if total_calls > 0 else 0.0

        prompt_tokens = sum(c.prompt_tokens for c in calls)
        completion_tokens = sum(c.completion_tokens for c in calls)
        total_tokens = sum(c.total_tokens for c in calls)

        return {
            "total_calls": total_calls,
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "success_rate": (successful_calls / total_calls) if total_calls > 0 else 1.0,
            "fallback_events": fallbacks,
            "total_latency_ms": round(total_latency, 2),
            "average_latency_ms": round(avg_latency, 2),
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": total_tokens
            }
        }

    def get_role_summary(self, role: str) -> Dict[str, Any]:
        """Calculates metrics for a specific role."""
        with self._lock:
            calls = [c for c in self._history if c.role == role]

        total = len(calls)
        success = sum(1 for c in calls if c.success)
        latency = sum(c.latency_ms for c in calls)
        tokens = sum(c.total_tokens for c in calls)

        return {
            "role": role,
            "total_calls": total,
            "successful_calls": success,
            "failed_calls": total - success,
            "average_latency_ms": round((latency / total) if total > 0 else 0.0, 2),
            "total_tokens": tokens
        }

    def reset(self) -> None:
        """Resets telemetry history (primarily for tests)."""
        with self._lock:
            self._history.clear()
            self._fallback_count = 0
