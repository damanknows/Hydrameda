"""Centralized model registry with role-based routing and fallback chains."""
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from hydra_router.exceptions import ConfigurationError, RoleNotFoundError


class ModelRole(str, Enum):
    """Recognized operational roles across Hydrameda."""
    ARCHITECT = "architect"
    PLANNER = "architect"  # noqa: PIE796 - Backward-compatible alias for ARCHITECT
    CODER = "coder"
    REPAIRER = "repairer"
    REVIEWER = "reviewer"
    REPOSITORY_ANALYZER = "repository_analyzer"
    VERIFICATION_ANALYZER = "verification_analyzer"

    @classmethod
    def _missing_(cls, value: object) -> Any:
        if isinstance(value, str):
            v = value.strip().lower().replace("-", "_")
            if v == "planner":
                return cls.ARCHITECT
        return super()._missing_(value)

    @classmethod
    def normalize(cls, role_input: "ModelRole | str") -> "ModelRole":
        """Normalizes role strings, legacy aliases, and node labels."""
        if isinstance(role_input, cls):
            return role_input

        role_str = getattr(role_input, "value", str(role_input)).strip()
        if role_str.startswith("ModelRole."):
            role_str = role_str.split(".", 1)[1]

        raw = role_str.lower().replace("-", "_")
        aliases = {
            "architect": cls.ARCHITECT,
            "hybrid_architect": cls.ARCHITECT,
            "spec_researcher": cls.ARCHITECT,
            "planner": cls.ARCHITECT,

            "repository_analyzer": cls.REPOSITORY_ANALYZER,
            "repo_analyzer": cls.REPOSITORY_ANALYZER,
            "spec_analyzer": cls.REPOSITORY_ANALYZER,
            "ast_analyzer": cls.REPOSITORY_ANALYZER,

            "coder": cls.CODER,
            "code_builder": cls.CODER,
            "hybrid_coder": cls.CODER,
            "default": cls.CODER,

            "repairer": cls.REPAIRER,
            "repair_agent": cls.REPAIRER,
            "healer": cls.REPAIRER,

            "verification_analyzer": cls.VERIFICATION_ANALYZER,
            "error_analyzer": cls.VERIFICATION_ANALYZER,

            "reviewer": cls.REVIEWER,
            "approval_gate": cls.REVIEWER,
        }
        if raw in aliases:
            return aliases[raw]
        try:
            return cls(raw)
        except ValueError:
            raise RoleNotFoundError(
                f"Unknown role '{role_input}'. Supported roles: {[r.value for r in cls]}"
            ) from None


@dataclass
class ModelSpec:
    """Specification of an individual LLM provider target."""
    model: str
    provider: str
    api_key_env: str = ""
    api_base: str | None = None
    timeout: float = 30.0
    max_retries: int = 2
    extra_params: dict[str, Any] = field(default_factory=dict)

    def resolve_api_key(self, env_dict: dict[str, str] | None = None) -> str | None:
        """Resolves API key from given dictionary or system environment."""
        if not self.api_key_env:
            return None
        env = env_dict if env_dict is not None else os.environ
        return env.get(self.api_key_env)


@dataclass
class RoleConfig:
    """Configured model fallback chain and generation defaults for a role."""
    role: ModelRole
    models: list[ModelSpec]
    default_temperature: float = 0.1
    default_max_tokens: int = 4096


class ModelRegistry:
    """Central registry mapping roles to prioritized model fallback chains."""

    def __init__(self, root_dir: Path | None = None, strict: bool = False):
        self.root_dir = root_dir or Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda"))
        self.strict = strict
        self._roles: dict[ModelRole, RoleConfig] = {}
        self._load_defaults()

    def _get_env_vars(self) -> dict[str, str]:
        env_file = self.root_dir / ".env"
        loaded = dotenv_values(env_file) if env_file.exists() else {}
        merged: dict[str, str] = {k: v for k, v in loaded.items() if v is not None}
        merged.update(dict(os.environ))
        return merged

    def _load_defaults(self) -> None:
        """Sets up default provider chains for each role."""
        groq_120b = ModelSpec(
            model="groq/openai/gpt-oss-120b",
            provider="groq",
            api_key_env="GROQ_API_KEY",
            timeout=30.0,
            max_retries=2
        )
        cerebras_120b = ModelSpec(
            model="cerebras/gpt-oss-120b",
            provider="cerebras",
            api_key_env="CEREBRAS_API_KEY",
            timeout=30.0,
            max_retries=2
        )
        gemini_pro = ModelSpec(
            model="gemini/gemini-2.5-pro",
            provider="gemini",
            api_key_env="GEMINI_API_KEY",
            timeout=45.0,
            max_retries=2
        )

        # 1. ARCHITECT: Technical architecture and specification synthesis
        self._roles[ModelRole.ARCHITECT] = RoleConfig(
            role=ModelRole.ARCHITECT,
            models=[groq_120b, cerebras_120b, gemini_pro],
            default_temperature=0.1,
            default_max_tokens=4096
        )

        # 2. REPOSITORY_ANALYZER: Tree-sitter & AST intent extraction
        self._roles[ModelRole.REPOSITORY_ANALYZER] = RoleConfig(
            role=ModelRole.REPOSITORY_ANALYZER,
            models=[cerebras_120b, groq_120b],
            default_temperature=0.0,
            default_max_tokens=3000
        )

        # 3. CODER: Production code and unit test generation
        self._roles[ModelRole.CODER] = RoleConfig(
            role=ModelRole.CODER,
            models=[cerebras_120b, groq_120b],
            default_temperature=0.1,
            default_max_tokens=8192
        )

        # 4. REPAIRER: Targeted self-healing repair patches
        self._roles[ModelRole.REPAIRER] = RoleConfig(
            role=ModelRole.REPAIRER,
            models=[groq_120b, cerebras_120b],
            default_temperature=0.1,
            default_max_tokens=8192
        )

        # 5. VERIFICATION_ANALYZER: Neuro-symbolic error diagnosis (separate from deterministic verifier)
        self._roles[ModelRole.VERIFICATION_ANALYZER] = RoleConfig(
            role=ModelRole.VERIFICATION_ANALYZER,
            models=[cerebras_120b, groq_120b],
            default_temperature=0.0,
            default_max_tokens=4096
        )

        # 6. REVIEWER: Specification evaluation and pre-approval audit
        self._roles[ModelRole.REVIEWER] = RoleConfig(
            role=ModelRole.REVIEWER,
            models=[groq_120b, cerebras_120b],
            default_temperature=0.1,
            default_max_tokens=3000
        )

    def register_role(self, config: RoleConfig) -> None:
        """Registers or overrides a role configuration."""
        self._roles[config.role] = config

    def get_role_config(self, role: ModelRole | str) -> RoleConfig:
        """Retrieves the configuration for a given role, applying env overrides if present."""
        normalized = ModelRole.normalize(str(role))
        if normalized not in self._roles:
            raise RoleNotFoundError(f"Role '{normalized}' not configured in registry.")
        base_config = self._roles[normalized]
        env = self._get_env_vars()

        role_name = normalized.value.upper()
        temp_key = f"HYDRAMEDA_{role_name}_TEMPERATURE"
        max_tokens_key = f"HYDRAMEDA_{role_name}_MAX_TOKENS"

        if normalized == ModelRole.ARCHITECT:
            if temp_key not in env and "HYDRAMEDA_PLANNER_TEMPERATURE" in env:
                temp_key = "HYDRAMEDA_PLANNER_TEMPERATURE"
            if max_tokens_key not in env and "HYDRAMEDA_PLANNER_MAX_TOKENS" in env:
                max_tokens_key = "HYDRAMEDA_PLANNER_MAX_TOKENS"

        temp = float(env[temp_key]) if temp_key in env else base_config.default_temperature
        tokens = int(env[max_tokens_key]) if max_tokens_key in env else base_config.default_max_tokens

        return RoleConfig(
            role=base_config.role,
            models=list(base_config.models),
            default_temperature=temp,
            default_max_tokens=tokens
        )

    def get_active_models(
        self,
        role: ModelRole | str,
        require_api_key: bool = False,
        strict: bool | None = None
    ) -> list[ModelSpec]:
        """Returns the fallback chain of models for a role, filtered by available credentials and env overrides."""
        is_strict = self.strict if strict is None else strict
        config = self.get_role_config(role)
        env = self._get_env_vars()

        role_name = config.role.value.upper()
        override_env_key = f"HYDRAMEDA_{role_name}_MODEL"
        if config.role == ModelRole.ARCHITECT and override_env_key not in env and "HYDRAMEDA_PLANNER_MODEL" in env:
            override_env_key = "HYDRAMEDA_PLANNER_MODEL"

        models = list(config.models)
        if override_env_key in env:
            raw_override = env[override_env_key].strip()
            if not raw_override:
                raise ConfigurationError(f"Environment variable '{override_env_key}' is set but empty.")
            override_names = [m.strip() for m in raw_override.split(",") if m.strip()]
            custom_models = []
            for m_name in override_names:
                prov = m_name.split("/")[0] if "/" in m_name else "custom"
                key_env = f"{prov.upper()}_API_KEY"
                api_base = env.get(f"HYDRAMEDA_{role_name}_API_BASE") or env.get(f"{prov.upper()}_API_BASE")
                timeout_val = float(env[f"HYDRAMEDA_{role_name}_TIMEOUT"]) if f"HYDRAMEDA_{role_name}_TIMEOUT" in env else 30.0
                custom_models.append(ModelSpec(
                    model=m_name,
                    provider=prov,
                    api_key_env=key_env,
                    api_base=api_base,
                    timeout=timeout_val
                ))
            if custom_models:
                models = custom_models

        if require_api_key or is_strict:
            active = [s for s in models if s.resolve_api_key(env) or not s.api_key_env]
            if not active and is_strict:
                needed = [s.api_key_env for s in models if s.api_key_env]
                raise ConfigurationError(
                    f"Missing provider configuration for role '{config.role.value}'. "
                    f"Required environment variables not set: {', '.join(needed)}"
                )
            return active

        return models
