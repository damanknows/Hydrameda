"""Unit and integration tests for Centralized Multi-Model HydraRouter."""
import os
import sys
import time
from pathlib import Path
import pytest
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hydra_router import (
    HydraRouter,
    ModelRole,
    ModelSpec,
    RoleConfig,
    ModelRegistry,
    ModelCallMetrics,
    RouterTelemetry,
    OutputValidator,
    AllModelsFailedError,
    RoleNotFoundError,
    StructuredOutputValidationError,
    get_router
)


# ============================================================================
# 1. Registry & Role-Based Model Selection Tests
# ============================================================================

def test_model_role_normalization():
    assert ModelRole.normalize("planner") == ModelRole.PLANNER
    assert ModelRole.normalize("architect") == ModelRole.PLANNER
    assert ModelRole.normalize("hybrid-architect") == ModelRole.PLANNER
    assert ModelRole.normalize("coder") == ModelRole.CODER
    assert ModelRole.normalize("hybrid-coder") == ModelRole.CODER
    assert ModelRole.normalize("repairer") == ModelRole.REPAIRER
    assert ModelRole.normalize("healer") == ModelRole.REPAIRER
    assert ModelRole.normalize("verification_analyzer") == ModelRole.VERIFICATION_ANALYZER
    assert ModelRole.normalize("error_analyzer") == ModelRole.VERIFICATION_ANALYZER
    assert ModelRole.normalize("repository_analyzer") == ModelRole.REPOSITORY_ANALYZER
    assert ModelRole.normalize("reviewer") == ModelRole.REVIEWER

    with pytest.raises(RoleNotFoundError):
        ModelRole.normalize("non_existent_role_xyz")


def test_registry_roles_and_fallbacks():
    reg = ModelRegistry()
    for role in [
        ModelRole.PLANNER,
        ModelRole.REPOSITORY_ANALYZER,
        ModelRole.CODER,
        ModelRole.REPAIRER,
        ModelRole.VERIFICATION_ANALYZER,
        ModelRole.REVIEWER
    ]:
        cfg = reg.get_role_config(role)
        assert cfg.role == role
        assert len(cfg.models) >= 2  # Primary + at least one fallback
        assert cfg.default_max_tokens > 0


def test_custom_role_registration():
    reg = ModelRegistry()
    custom_spec = ModelSpec(model="mock/custom-model", provider="mock", timeout=15.0)
    custom_cfg = RoleConfig(
        role=ModelRole.REVIEWER,
        models=[custom_spec],
        default_temperature=0.05,
        default_max_tokens=2048
    )
    reg.register_role(custom_cfg)
    retrieved = reg.get_role_config(ModelRole.REVIEWER)
    assert retrieved.models[0].model == "mock/custom-model"
    assert retrieved.default_max_tokens == 2048


def test_environment_variable_model_override(monkeypatch):
    reg = ModelRegistry()
    monkeypatch.setenv("HYDRAMEDA_CODER_MODEL", "custom-provider/custom-coder,fallback/coder")
    active = reg.get_active_models(ModelRole.CODER)
    assert len(active) == 2
    assert active[0].model == "custom-provider/custom-coder"
    assert active[1].model == "fallback/coder"


# ============================================================================
# 2. Output Validator Tests
# ============================================================================

class SampleDiagnosis(BaseModel):
    category: str
    confidence: float
    fix_suggested: str


def test_pydantic_output_validation_success():
    validator = OutputValidator.validate_pydantic(SampleDiagnosis)
    raw = '{"category": "SYNTAX_ERROR", "confidence": 0.95, "fix_suggested": "Add colon"}'
    parsed = validator(raw)
    assert isinstance(parsed, SampleDiagnosis)
    assert parsed.category == "SYNTAX_ERROR"
    assert parsed.confidence == 0.95


def test_pydantic_output_validation_markdown_fence():
    validator = OutputValidator.validate_pydantic(SampleDiagnosis)
    raw = '```json\n{"category": "TYPE_ERROR", "confidence": 0.88, "fix_suggested": "Cast to int"}\n```'
    parsed = validator(raw)
    assert parsed.category == "TYPE_ERROR"


def test_pydantic_output_validation_failure():
    validator = OutputValidator.validate_pydantic(SampleDiagnosis)
    invalid_raw = '{"category": "SYNTAX_ERROR"}'  # missing required fields
    with pytest.raises(StructuredOutputValidationError) as exc:
        validator(invalid_raw)
    assert "SampleDiagnosis" in str(exc.value)


def test_xml_files_validation():
    validator = OutputValidator.validate_xml_files(min_files=2)
    valid_xml = (
        '<file name="src/math.py">\ndef add(a, b): return a + b\n</file>\n'
        '<file name="tests/test_math.py">\ndef test_add(): assert add(1, 2) == 3\n</file>'
    )
    extracted = validator(valid_xml)
    assert "src/math.py" in extracted
    assert "tests/test_math.py" in extracted

    # Test failure on insufficient files
    insufficient = '<file name="src/math.py">\ndef add(): pass\n</file>'
    with pytest.raises(StructuredOutputValidationError) as exc:
        validator(insufficient)
    assert "Expected at least 2" in str(exc.value)


def test_markdown_spec_validation():
    validator = OutputValidator.validate_markdown_spec(required_headings=["# Architecture", "## Invariants"])
    valid_doc = "# Architecture Spec\nDetailed blueprint.\n## Invariants\nMust be thread-safe."
    assert validator(valid_doc) == valid_doc

    invalid_doc = "# Architecture Spec\nMissing invariants section."
    with pytest.raises(StructuredOutputValidationError) as exc:
        validator(invalid_doc)
    assert "## Invariants" in str(exc.value)


# ============================================================================
# 3. Router Execution, Fallback Chains, & Telemetry
# ============================================================================

def test_router_successful_completion_with_mock():
    telemetry = RouterTelemetry()
    router = HydraRouter(telemetry=telemetry)

    def mock_backend(role: str, prompt: str):
        assert role == "coder"
        return "<file name=\"hello.py\">\nprint('hello')\n</file>", "mock-coder-v1"

    router.set_mock_handler(mock_backend)
    resp = router.complete(role=ModelRole.CODER, prompt="Write hello world")

    assert "hello.py" in resp.content
    assert resp.role == "coder"
    assert resp.model_used == "mock-coder-v1"
    assert resp.metrics.success is True
    assert resp.metrics.total_tokens > 0
    assert resp.metrics.latency_ms >= 0.0

    summary = telemetry.get_summary()
    assert summary["total_calls"] == 1
    assert summary["successful_calls"] == 1
    assert summary["tokens"]["total"] > 0


def test_fallback_chain_on_provider_failure(monkeypatch):
    """Verifies that when the primary model fails, the router falls back to the secondary model."""
    import litellm

    telemetry = RouterTelemetry()
    reg = ModelRegistry()
    m1 = ModelSpec(model="provider-a/fail-model", provider="prov-a")
    m2 = ModelSpec(model="provider-b/success-model", provider="prov-b")
    reg.register_role(RoleConfig(role=ModelRole.PLANNER, models=[m1, m2]))

    router = HydraRouter(registry=reg, telemetry=telemetry)

    def mock_litellm_completion(**kwargs):
        model = kwargs.get("model")
        if model == "provider-a/fail-model":
            raise RuntimeError("API connection timeout to provider A")
        # Secondary model succeeds
        class MockChoice:
            message = type("Msg", (), {"content": "# Spec from Provider B"})()
        class MockUsage:
            prompt_tokens = 120
            completion_tokens = 60
            total_tokens = 180
        return type("MockResponse", (), {"choices": [MockChoice()], "usage": MockUsage()})()

    monkeypatch.setattr(litellm, "completion", mock_litellm_completion)

    resp = router.complete(role=ModelRole.PLANNER, prompt="Design user service")

    assert resp.content == "# Spec from Provider B"
    assert resp.model_used == "provider-b/success-model"
    assert resp.provider == "prov-b"

    summary = telemetry.get_summary()
    assert summary["total_calls"] == 2
    assert summary["successful_calls"] == 1
    assert summary["failed_calls"] == 1
    assert summary["fallback_events"] == 1


def test_rate_limit_and_timeout_failover(monkeypatch):
    """Verifies that 429 rate limit triggers immediate failover to the next candidate."""
    import litellm

    telemetry = RouterTelemetry()
    reg = ModelRegistry()
    m1 = ModelSpec(model="prov-a/rate-limited", provider="prov-a")
    m2 = ModelSpec(model="prov-b/ready-model", provider="prov-b")
    reg.register_role(RoleConfig(role=ModelRole.REPAIRER, models=[m1, m2]))

    router = HydraRouter(registry=reg, telemetry=telemetry)

    call_counts = {"prov-a": 0, "prov-b": 0}

    def mock_litellm_completion(**kwargs):
        model = kwargs.get("model")
        if "rate-limited" in model:
            call_counts["prov-a"] += 1
            raise Exception("RateLimitError: 429 Too Many Requests")
        call_counts["prov-b"] += 1
        class MockChoice:
            message = type("Msg", (), {"content": "<file name=\"fix.py\">\n# fixed\n</file>"})()
        return type("MockResponse", (), {"choices": [MockChoice()], "usage": None})()

    monkeypatch.setattr(litellm, "completion", mock_litellm_completion)
    # Monkeypatch sleep to avoid test slowdown
    monkeypatch.setattr(time, "sleep", lambda s: None)

    resp = router.complete(role=ModelRole.REPAIRER, prompt="Repair assertion error")
    assert "fix.py" in resp.content
    assert call_counts["prov-a"] == 1
    assert call_counts["prov-b"] == 1
    assert telemetry.get_summary()["fallback_events"] == 1


def test_all_models_failed_raises_explicit_exception(monkeypatch):
    """Verifies that if every model in the fallback chain fails, AllModelsFailedError is raised without silent swallowing."""
    import litellm

    telemetry = RouterTelemetry()
    reg = ModelRegistry()
    m1 = ModelSpec(model="prov-a/fail-1", provider="prov-a")
    m2 = ModelSpec(model="prov-b/fail-2", provider="prov-b")
    reg.register_role(RoleConfig(role=ModelRole.VERIFICATION_ANALYZER, models=[m1, m2]))

    router = HydraRouter(registry=reg, telemetry=telemetry)

    def mock_litellm_completion(**kwargs):
        raise RuntimeError("Service unavailable 503")

    monkeypatch.setattr(litellm, "completion", mock_litellm_completion)

    with pytest.raises(AllModelsFailedError) as exc_info:
        router.complete(role=ModelRole.VERIFICATION_ANALYZER, prompt="Analyze crash")

    err = exc_info.value
    assert err.role == "verification_analyzer"
    assert len(err.attempts) == 2
    assert "prov-a/fail-1" in str(err)
    assert "prov-b/fail-2" in str(err)


def test_structured_validation_during_completion():
    router = HydraRouter()

    def mock_backend(role: str, prompt: str):
        return '{"category": "SYNTAX_ERROR", "confidence": 0.99, "fix_suggested": "close bracket"}', "mock-analyzer"

    router.set_mock_handler(mock_backend)

    resp = router.complete(
        role=ModelRole.VERIFICATION_ANALYZER,
        prompt="Analyze error log",
        validator=OutputValidator.validate_pydantic(SampleDiagnosis)
    )

    assert isinstance(resp.parsed, SampleDiagnosis)
    assert resp.parsed.category == "SYNTAX_ERROR"
    assert resp.parsed.confidence == 0.99


def test_singleton_get_router():
    r1 = get_router()
    r2 = get_router()
    assert r1 is r2
    assert isinstance(r1, HydraRouter)
