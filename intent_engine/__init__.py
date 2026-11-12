"""
Hydrameda Intent Engine.

Autonomous Prompt Understanding, Constraint Enforcement, and Intent Preservation Subsystem.
"""
from __future__ import annotations

from .constraint_compiler import ConstraintCompiler
from .constraint_normalizer import ConstraintNormalizer
from .constraint_validator import (
    ConstraintValidationReport,
    ConstraintValidator,
    ValidationFinding,
)
from .intent_guard import IntentGuard
from .intent_parser import IntentParser
from .models import (
    ConstraintPriority,
    EnforcementStrategy,
    RequirementItem,
    RequirementType,
    StructuredIntent,
    VerificationStatus,
)
from .policy import PrecedencePolicy

__all__ = [
    "ConstraintCompiler",
    "ConstraintNormalizer",
    "ConstraintPriority",
    "ConstraintValidationReport",
    "ConstraintValidator",
    "EnforcementStrategy",
    "IntentGuard",
    "IntentParser",
    "PrecedencePolicy",
    "RequirementItem",
    "RequirementType",
    "StructuredIntent",
    "ValidationFinding",
    "VerificationStatus",
]
