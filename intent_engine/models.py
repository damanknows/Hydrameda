"""
Hydrameda Intent Engine: Structured Data Models.

Defines the immutable representations for task intent, requirement items,
constraint priorities, enforcement strategies, and verification statuses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any


class RequirementType(str, Enum):
    """Classification of requirement semantics."""
    REQUIRED = "REQUIRED"
    PROHIBITED = "PROHIBITED"
    OPTIONAL = "OPTIONAL"
    CONTEXTUAL = "CONTEXTUAL"
    ASSUMPTION = "ASSUMPTION"
    AMBIGUOUS = "AMBIGUOUS"


class ConstraintPriority(IntEnum):
    """Precedence hierarchy for constraint enforcement.

    Lower integer indicates strictly higher priority. Lower-priority context
    can NEVER override or weaken higher-priority instructions.
    """
    EXPLICIT_PROHIBITION = 1
    EXPLICIT_REQUIREMENT = 2
    USER_SCOPE_BOUNDARY = 3
    ACCEPTANCE_CRITERIA = 4
    REPOSITORY_COMPATIBILITY = 5
    RETRIEVED_EXPERIENCE = 6
    RAG_SUGGESTION = 7
    MODEL_ASSUMPTION = 8

    @classmethod
    def get_description(cls, priority: int) -> str:
        names = {
            cls.EXPLICIT_PROHIBITION: "Explicit user prohibition (absolute veto)",
            cls.EXPLICIT_REQUIREMENT: "Explicit user requirement",
            cls.USER_SCOPE_BOUNDARY: "User-defined scope boundary",
            cls.ACCEPTANCE_CRITERIA: "Current task acceptance criteria",
            cls.REPOSITORY_COMPATIBILITY: "Repository integration and compatibility",
            cls.RETRIEVED_EXPERIENCE: "Retrieved engineering lessons/experience",
            cls.RAG_SUGGESTION: "RAG knowledge vault contextual suggestions",
            cls.MODEL_ASSUMPTION: "Model-generated working assumption",
        }
        return names.get(cls(priority), f"Priority {priority}")


class EnforcementStrategy(str, Enum):
    """Enforcement mechanism used to verify or guard a constraint."""
    HARD_GATE = "HARD_GATE"                    # Must halt pipeline immediately if violated
    STATIC_VERIFICATION = "STATIC_VERIFICATION"  # AST, import, types, syntax verification
    RUNTIME_TEST = "RUNTIME_TEST"              # Pytest verification execution
    REPUTATION_FILTER = "REPUTATION_FILTER"    # Filter/redact from prompt context
    ADVISORY = "ADVISORY"                      # Guidance/suggestion only


class VerificationStatus(str, Enum):
    """Verification lifecycle status of an individual requirement item."""
    PENDING = "PENDING"
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass
class RequirementItem:
    """An individual structured requirement or constraint in the task ledger."""
    id: str
    source_text: str
    normalized_description: str
    req_type: RequirementType
    priority: ConstraintPriority
    enforcement_strategy: EnforcementStrategy
    verification_status: VerificationStatus = VerificationStatus.PENDING
    category: str = "general"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def raw_text(self) -> str:
        """Alias for source_text."""
        return self.source_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_text": self.source_text,
            "normalized_description": self.normalized_description,
            "req_type": self.req_type.value,
            "priority": self.priority.value,
            "enforcement_strategy": self.enforcement_strategy.value,
            "verification_status": self.verification_status.value,
            "category": self.category,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequirementItem:
        return cls(
            id=str(data["id"]),
            source_text=str(data.get("source_text", "")),
            normalized_description=str(data.get("normalized_description", "")),
            req_type=RequirementType(data.get("req_type", RequirementType.REQUIRED.value)),
            priority=ConstraintPriority(int(data.get("priority", ConstraintPriority.EXPLICIT_REQUIREMENT.value))),
            enforcement_strategy=EnforcementStrategy(data.get("enforcement_strategy", EnforcementStrategy.STATIC_VERIFICATION.value)),
            verification_status=VerificationStatus(data.get("verification_status", VerificationStatus.PENDING.value)),
            category=str(data.get("category", "general")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class StructuredIntent:
    """Canonical, structured task intent ledger compiled from the user request."""
    original_prompt: str
    primary_objective: str
    required_capabilities: list[RequirementItem] = field(default_factory=list)
    explicit_prohibitions: list[RequirementItem] = field(default_factory=list)
    scope_boundaries: list[RequirementItem] = field(default_factory=list)
    expected_deliverables: list[RequirementItem] = field(default_factory=list)
    technical_requirements: list[RequirementItem] = field(default_factory=list)
    repository_integration_requirements: list[RequirementItem] = field(default_factory=list)
    explicit_assumptions: list[RequirementItem] = field(default_factory=list)
    unresolved_ambiguities: list[RequirementItem] = field(default_factory=list)
    acceptance_criteria: list[RequirementItem] = field(default_factory=list)
    ledger: dict[str, RequirementItem] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ledger:
            self._rebuild_ledger()

    def _rebuild_ledger(self) -> None:
        self.ledger.clear()
        for collection in (
            self.required_capabilities,
            self.explicit_prohibitions,
            self.scope_boundaries,
            self.expected_deliverables,
            self.technical_requirements,
            self.repository_integration_requirements,
            self.explicit_assumptions,
            self.unresolved_ambiguities,
            self.acceptance_criteria,
        ):
            for item in collection:
                self.ledger[item.id] = item

    @property
    def requirements(self) -> list[RequirementItem]:
        """Alias to all_requirements for convenience."""
        return self.all_requirements()

    def all_requirements(self) -> list[RequirementItem]:
        """Returns all requirement items sorted by priority ascending (highest priority first)."""
        return sorted(self.ledger.values(), key=lambda item: int(item.priority))

    def get_prohibitions(self) -> list[RequirementItem]:
        """Returns all explicit prohibitions (Priority 1)."""
        return [item for item in self.ledger.values() if item.req_type == RequirementType.PROHIBITED]

    def get_positive_requirements(self) -> list[RequirementItem]:
        """Returns all positive capabilities and functional requirements."""
        return [item for item in self.ledger.values() if item.req_type == RequirementType.REQUIRED]

    def to_markdown_ledger(self) -> str:
        """Renders markdown summary of the intent ledger."""
        from .constraint_compiler import ConstraintCompiler
        return ConstraintCompiler.render_markdown_summary(self)

    def get_requirements_by_priority(self, priority: ConstraintPriority) -> list[RequirementItem]:
        """Returns items matching a specific constraint priority level."""
        return [item for item in self.ledger.values() if item.priority == priority]

    def get_item(self, req_id: str) -> RequirementItem | None:
        """Retrieves a requirement item by its unique ID."""
        return self.ledger.get(req_id)

    def add_item(self, item: RequirementItem) -> None:
        """Adds a requirement item to the ledger and its respective collection."""
        self.ledger[item.id] = item
        collection_map = {
            RequirementType.REQUIRED: self.required_capabilities,
            RequirementType.PROHIBITED: self.explicit_prohibitions,
            RequirementType.CONTEXTUAL: self.technical_requirements,
            RequirementType.ASSUMPTION: self.explicit_assumptions,
            RequirementType.AMBIGUOUS: self.unresolved_ambiguities,
        }
        target = collection_map.get(item.req_type, self.required_capabilities)
        if item not in target:
            target.append(item)

    def to_dict(self) -> dict[str, Any]:
        """Serializes StructuredIntent to a dictionary."""
        return {
            "original_prompt": self.original_prompt,
            "primary_objective": self.primary_objective,
            "required_capabilities": [item.to_dict() for item in self.required_capabilities],
            "explicit_prohibitions": [item.to_dict() for item in self.explicit_prohibitions],
            "scope_boundaries": [item.to_dict() for item in self.scope_boundaries],
            "expected_deliverables": [item.to_dict() for item in self.expected_deliverables],
            "technical_requirements": [item.to_dict() for item in self.technical_requirements],
            "repository_integration_requirements": [item.to_dict() for item in self.repository_integration_requirements],
            "explicit_assumptions": [item.to_dict() for item in self.explicit_assumptions],
            "unresolved_ambiguities": [item.to_dict() for item in self.unresolved_ambiguities],
            "acceptance_criteria": [item.to_dict() for item in self.acceptance_criteria],
            "ledger": {k: v.to_dict() for k, v in self.ledger.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StructuredIntent:
        """Deserializes StructuredIntent from a dictionary."""
        if not data:
            return cls(original_prompt="", primary_objective="")

        inst = cls(
            original_prompt=str(data.get("original_prompt", "")),
            primary_objective=str(data.get("primary_objective", "")),
            required_capabilities=[RequirementItem.from_dict(d) for d in data.get("required_capabilities", [])],
            explicit_prohibitions=[RequirementItem.from_dict(d) for d in data.get("explicit_prohibitions", [])],
            scope_boundaries=[RequirementItem.from_dict(d) for d in data.get("scope_boundaries", [])],
            expected_deliverables=[RequirementItem.from_dict(d) for d in data.get("expected_deliverables", [])],
            technical_requirements=[RequirementItem.from_dict(d) for d in data.get("technical_requirements", [])],
            repository_integration_requirements=[RequirementItem.from_dict(d) for d in data.get("repository_integration_requirements", [])],
            explicit_assumptions=[RequirementItem.from_dict(d) for d in data.get("explicit_assumptions", [])],
            unresolved_ambiguities=[RequirementItem.from_dict(d) for d in data.get("unresolved_ambiguities", [])],
            acceptance_criteria=[RequirementItem.from_dict(d) for d in data.get("acceptance_criteria", [])],
        )
        inst._rebuild_ledger()
        return inst
