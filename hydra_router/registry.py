"""Centralized model registry with role-based routing and fallback chains."""
import os
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from dotenv import dotenv_values

from hydra_router.exceptions import RoleNotFoundError, ConfigurationError


class ModelRole(str, Enum):
    """Recognized operational roles across Hydrameda."""
    PLANNER = "planner"
    REPOSITORY_ANALYZER = "repository_analyzer"
    CODER = "coder"
    REPAIRER = "repairer"
    VERIFICATION_ANALYZER = "verification_analyzer"
    REVIEWER = "reviewer"

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
            "architect": cls.PLANNER,
            "hybrid_architect": cls.PLANNER,
            "spec_researcher": cls.PLANNER,
            "planner": cls.PLANNER,

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
            raise RoleNotFoundError(f"Unknown role '{role_input}'. Supported roles: {[r.value for r in cls]}")


@dataclass
class ModelSpec:
    """Specification of an individual LLM provider target."""
    model: str
    provider: str
    api_key_env: str = ""
    api_base: Optional[str] = None
    timeout: float = 30.0
    max_retries: int = 2
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def resolve_api_key(self, env_dict: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Resolves API key from given dictionary or system environment."""
        if not self.api_key_env:
            return None
        env = env_dict if env_dict is not None else os.environ
        return env.get(self.api_key_env)


@dataclass
class RoleConfig:
    """Configured model fallback chain and generation defaults for a role."""
    role: ModelRole
    models: List[ModelSpec]
    default_temperature: float = 0.1
    default_max_tokens: int = 4096


class ModelRegistry:
    """Central registry mapping roles to prioritized model fallback chains."""

    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = root_dir or Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda"))
        self._roles: Dict[ModelRole, RoleConfig] = {}
        self._load_defaults()

    def _get_env_vars(self) -> Dict[str, str]:
        env_file = self.root_dir / ".env"
        loaded = dotenv_values(env_file) if env_file.exists() else {}
        # Merge system environment taking precedence
        return {**loaded, **os.environ}

    def _load_defaults(self) -> None:
        """Sets up default provider chains for each role."""
        # Common model definitions
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

        # 1. PLANNER: Architecting specifications
        self._roles[ModelRole.PLANNER] = RoleConfig(
            role=ModelRole.PLANNER,
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
        """Retrieves the configuration for a given role."""
        normalized = ModelRole.normalize(str(role))
        if normalized not in self._roles:
            raise RoleNotFoundError(f"Role '{normalized}' not configured in registry.")
        return self._roles[normalized]

    def get_active_models(self, role: ModelRole | str, require_api_key: bool = True) -> List[ModelSpec]:
        """Returns the fallback chain of models for a role, filtered by available credentials."""
        config = self.get_role_config(role)
        env = self._get_env_vars()

        # Check for role-specific environment model override (e.g., HYDRAMEDA_CODER_MODEL)
        override_env_key = f"HYDRAMEDA_{config.role.value.upper()}_MODEL"
        if override_env_key in env:
            override_names = [m.strip() for m in env[override_env_key].split(",") if m.strip()]
            custom_models = []
            for m_name in override_names:
                # Deduce provider
                prov = m_name.split("/")[0] if "/" in m_name else "custom"
                key_env = f"{prov.upper()}_API_KEY"
                custom_models.append(ModelSpec(model=m_name, provider=prov, api_key_env=key_env))
            if custom_models:
                return custom_models

        if not require_api_key:
            return list(config.models)

        active = []
        for spec in config.models:
            key = spec.resolve_api_key(env)
            # If key exists or no key required (e.g. local ollama or mock)
            if key or not spec.api_key_env:
                active.append(spec)

        # If no models have active keys, return full list so mock/fallback handlers can still operate
        return active if active else list(config.models)
