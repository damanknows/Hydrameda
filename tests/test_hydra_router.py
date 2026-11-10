"""Unit and integration tests for Centralized Multi-Model HydraRouter."""
import sys
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hydra_router import (
    AllModelsFailedError,
    AuthenticationError,
    ConfigurationError,
    ContextLengthExceededError,
    HydraRouter,
    MalformedResponseError,
    ModelCallMetrics,
    ModelRegistry,
    ModelRole,
    ModelSpec,
    OutputValidator,
    ProviderErrorKind,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    RoleConfig,
    RoleNotFoundError,
    RouterTelemetry,
    StructuredOutputValidationError,
    get_router,
    normalize_provider_error,
    redact_credentials,
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
            raise RuntimeError("RateLimitError: 429 Too Many Requests")
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


# ============================================================================
# 4. Multi-Provider Preparedness & Robustness Tests
# ============================================================================

def test_logical_roles_and_legacy_compatibility():
    """Verifies that architect, coder, repairer, reviewer are distinct logical roles,
    and that legacy 'planner' maps seamlessly without breaking backward compatibility.
    """
    assert ModelRole.ARCHITECT.value == "architect"
    assert ModelRole.CODER.value == "coder"
    assert ModelRole.REPAIRER.value == "repairer"
    assert ModelRole.REVIEWER.value == "reviewer"

    # Alias / backward compatibility check
    assert ModelRole.PLANNER is ModelRole.ARCHITECT
    assert ModelRole("planner") == ModelRole.ARCHITECT
    assert ModelRole.normalize("planner") == ModelRole.ARCHITECT
    assert ModelRole.normalize("architect") == ModelRole.ARCHITECT

    reg = ModelRegistry()
    for role in (ModelRole.ARCHITECT, ModelRole.CODER, ModelRole.REPAIRER, ModelRole.REVIEWER):
        cfg = reg.get_role_config(role)
        assert cfg.role == role
        assert len(cfg.models) >= 2


def test_env_var_overrides_comprehensive(monkeypatch):
    """Verifies environment variable overrides for model chains, temperatures, and token limits."""
    reg = ModelRegistry()

    # Override architect
    monkeypatch.setenv("HYDRAMEDA_ARCHITECT_MODEL", "custom/arch-primary,custom/arch-secondary")
    arch_models = reg.get_active_models(ModelRole.ARCHITECT)
    assert [m.model for m in arch_models] == ["custom/arch-primary", "custom/arch-secondary"]

    # Legacy planner env var works if architect var not set
    monkeypatch.delenv("HYDRAMEDA_ARCHITECT_MODEL")
    monkeypatch.setenv("HYDRAMEDA_PLANNER_MODEL", "legacy/planner-1")
    planner_models = reg.get_active_models(ModelRole.ARCHITECT)
    assert [m.model for m in planner_models] == ["legacy/planner-1"]

    # Override coder temperature and max tokens
    monkeypatch.setenv("HYDRAMEDA_CODER_TEMPERATURE", "0.42")
    monkeypatch.setenv("HYDRAMEDA_CODER_MAX_TOKENS", "16384")
    coder_cfg = reg.get_role_config(ModelRole.CODER)
    assert coder_cfg.default_temperature == pytest.approx(0.42)
    assert coder_cfg.default_max_tokens == 16384


def test_missing_provider_configuration_strict(monkeypatch, tmp_path):
    """Verifies ConfigurationError is raised on missing keys or empty model configs when strict=True."""
    # Empty override string should raise ConfigurationError
    reg = ModelRegistry()
    monkeypatch.setenv("HYDRAMEDA_CODER_MODEL", "   ")
    with pytest.raises(ConfigurationError) as exc_info:
        reg.get_active_models(ModelRole.CODER)
    assert "is set but empty" in str(exc_info.value)

    monkeypatch.delenv("HYDRAMEDA_CODER_MODEL")
    # In strict mode without API keys in environment, ConfigurationError is raised
    # Clear all possible provider API keys
    for k in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    strict_reg = ModelRegistry(root_dir=tmp_path, strict=True)
    with pytest.raises(ConfigurationError) as exc_strict:
        strict_reg.get_active_models(ModelRole.CODER)
    assert "Missing provider configuration for role 'coder'" in str(exc_strict.value)


def test_provider_independent_error_normalization():
    """Verifies deterministic classification of provider errors into 6 canonical categories."""
    m = "test-model"
    p = "test-provider"

    # 1. Authentication Failure
    auth_err = normalize_provider_error(RuntimeError("Invalid API Key: 401 Unauthorized"), model=m, provider=p)
    assert isinstance(auth_err, AuthenticationError)
    assert auth_err.error_kind == ProviderErrorKind.AUTHENTICATION_FAILURE

    # 2. Rate Limit
    rate_err = normalize_provider_error(RuntimeError("Rate limit reached. 429 Too Many Requests"), model=m, provider=p)
    assert isinstance(rate_err, RateLimitError)
    assert rate_err.error_kind == ProviderErrorKind.RATE_LIMIT

    # 3. Timeout
    timeout_err = normalize_provider_error(TimeoutError("Read operation timed out after 30s"), model=m, provider=p)
    assert isinstance(timeout_err, ProviderTimeoutError)
    assert timeout_err.error_kind == ProviderErrorKind.TIMEOUT

    # 4. Context Length Exceeded
    ctx_err = normalize_provider_error(RuntimeError("maximum context length is 8192 tokens. Please reduce prompt"), model=m, provider=p)
    assert isinstance(ctx_err, ContextLengthExceededError)
    assert ctx_err.error_kind == ProviderErrorKind.CONTEXT_LENGTH_EXCEEDED

    # 5. Provider Unavailable
    unavail_err = normalize_provider_error(RuntimeError("503 Service Unavailable: upstream server down"), model=m, provider=p)
    assert isinstance(unavail_err, ProviderUnavailableError)
    assert unavail_err.error_kind == ProviderErrorKind.PROVIDER_UNAVAILABLE

    # 6. Malformed Response
    malformed_err = normalize_provider_error(ValueError("Failed to decode JSON from provider payload"), model=m, provider=p)
    assert isinstance(malformed_err, MalformedResponseError)
    assert malformed_err.error_kind == ProviderErrorKind.MALFORMED_RESPONSE

    # Idempotent normalization
    assert normalize_provider_error(auth_err) is auth_err


def test_secret_redaction():
    """Verifies that API keys and sensitive tokens are strictly redacted from logs, errors, and telemetry."""
    dirty_text = (
        "Failed connection with sk-proj-1234567890abcdef12345678 and gsk_abcdef1234567890abcdef1234. "
        "Also csk-cerebras987654321012345678 and AIzaSyDfakegooglekey987654321. "
        "Bearer secret_token_xyz_12345678."
    )
    clean_text = redact_credentials(dirty_text)
    assert "sk-proj" not in clean_text
    assert "gsk_" not in clean_text
    assert "csk-" not in clean_text
    assert "AIzaSy" not in clean_text
    assert "secret_token" not in clean_text
    assert "[REDACTED_API_KEY]" in clean_text
    assert "[REDACTED_BEARER_TOKEN]" in clean_text

    # Verify AllModelsFailedError redacts attempts
    attempts = [
        {"model": "m1", "provider": "p1", "error": "Auth failed for sk-1234567890abcdef12345678"},
        {"model": "m2", "provider": "p2", "error": "Bearer token_secret_12345678 expired"},
    ]
    all_failed = AllModelsFailedError(role="coder", attempts=attempts)
    assert "sk-12345" not in str(all_failed)
    assert "token_secret" not in str(all_failed)
    for att in all_failed.attempts:
        assert "sk-12345" not in att["error"]

    # Verify Telemetry redacts error_message and fallback reason
    telemetry = RouterTelemetry()
    metrics = ModelCallMetrics(
        role="coder",
        model="m1",
        provider="p1",
        success=False,
        error_message="Call failed with key sk-secret1234567890123456"
    )
    telemetry.record_call(metrics)
    assert "sk-secret" not in metrics.error_message
    assert "[REDACTED_API_KEY]" in metrics.error_message

    telemetry.record_fallback("coder", "m1", "m2", "Error with gsk_9876543210abcdef123456")


def test_fallback_on_empty_and_malformed_response(monkeypatch):
    """Verifies that an empty completion or malformed response fails over to the next candidate model."""
    import litellm

    telemetry = RouterTelemetry()
    reg = ModelRegistry()
    m1 = ModelSpec(model="prov-a/empty-output", provider="prov-a")
    m2 = ModelSpec(model="prov-b/valid-output", provider="prov-b")
    reg.register_role(RoleConfig(role=ModelRole.CODER, models=[m1, m2]))

    router = HydraRouter(registry=reg, telemetry=telemetry)

    def mock_litellm_completion(**kwargs):
        model = kwargs.get("model")
        if model == "prov-a/empty-output":
            # Returns empty content
            class EmptyChoice:
                message = type("Msg", (), {"content": "   \n\t  "})()
            return type("MockResponse", (), {"choices": [EmptyChoice()], "usage": None})()
        # prov-b succeeds
        class ValidChoice:
            message = type("Msg", (), {"content": "<file name=\"solution.py\">\ndef solve(): pass\n</file>"})()
        return type("MockResponse", (), {"choices": [ValidChoice()], "usage": None})()

    monkeypatch.setattr(litellm, "completion", mock_litellm_completion)

    resp = router.complete(role=ModelRole.CODER, prompt="Write solution")
    assert resp.model_used == "prov-b/valid-output"
    assert "solution.py" in resp.content

    summary = telemetry.get_summary()
    assert summary["failed_calls"] == 1
    assert summary["successful_calls"] == 1
    assert summary["fallback_events"] == 1


def test_fallback_on_validation_failure(monkeypatch):
    """Verifies that when structured output validation fails, router fails over to next candidate model."""
    import litellm

    telemetry = RouterTelemetry()
    reg = ModelRegistry()
    m1 = ModelSpec(model="prov-a/bad-json", provider="prov-a")
    m2 = ModelSpec(model="prov-b/good-json", provider="prov-b")
    reg.register_role(RoleConfig(role=ModelRole.VERIFICATION_ANALYZER, models=[m1, m2]))

    router = HydraRouter(registry=reg, telemetry=telemetry)

    def mock_litellm_completion(**kwargs):
        model = kwargs.get("model")
        if model == "prov-a/bad-json":
            # Missing required fields for SampleDiagnosis
            class BadChoice:
                message = type("Msg", (), {"content": '{"category": "SYNTAX_ERROR"}'})()
            return type("MockResponse", (), {"choices": [BadChoice()], "usage": None})()
        # prov-b provides valid JSON
        class GoodChoice:
            message = type("Msg", (), {"content": '{"category": "SYNTAX_ERROR", "confidence": 0.98, "fix_suggested": "add return"}'})()
        return type("MockResponse", (), {"choices": [GoodChoice()], "usage": None})()

    monkeypatch.setattr(litellm, "completion", mock_litellm_completion)

    resp = router.complete(
        role=ModelRole.VERIFICATION_ANALYZER,
        prompt="Analyze error",
        validator=OutputValidator.validate_pydantic(SampleDiagnosis)
    )
    assert resp.model_used == "prov-b/good-json"
    assert isinstance(resp.parsed, SampleDiagnosis)
    assert resp.parsed.confidence == 0.98
