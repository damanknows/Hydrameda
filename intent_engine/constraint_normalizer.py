"""
Hydrameda Intent Engine: Constraint Normalizer.

Transforms parsed raw clauses into strongly typed, normalized RequirementItem objects
with deterministic identifiers, precedence priorities, and enforcement strategies.
"""
from __future__ import annotations

from .models import (
    ConstraintPriority,
    EnforcementStrategy,
    RequirementItem,
    RequirementType,
    VerificationStatus,
)


class ConstraintNormalizer:
    """Normalizes raw parsed intent elements into structured RequirementItem instances."""

    @classmethod
    def normalize_prohibition(cls, index: int, raw: dict[str, str]) -> RequirementItem:
        req_id = f"PROHIB-{index:03d}"
        term = raw.get("prohibited_term", "")
        desc = f"Do not implement, use, or introduce: {term}"
        return RequirementItem(
            id=req_id,
            source_text=raw.get("source_text", ""),
            normalized_description=desc,
            req_type=RequirementType.PROHIBITED,
            priority=ConstraintPriority.EXPLICIT_PROHIBITION,
            enforcement_strategy=EnforcementStrategy.HARD_GATE,
            verification_status=VerificationStatus.PENDING,
            category=raw.get("category", "general"),
            metadata={"prohibited_term": term}
        )

    @classmethod
    def normalize_capability(cls, index: int, raw: dict[str, str]) -> RequirementItem:
        req_id = f"REQ-{index:03d}"
        cap = raw.get("capability", "")
        desc = f"Implement requested capability: {cap}"
        return RequirementItem(
            id=req_id,
            source_text=raw.get("source_text", ""),
            normalized_description=desc,
            req_type=RequirementType.REQUIRED,
            priority=ConstraintPriority.EXPLICIT_REQUIREMENT,
            enforcement_strategy=EnforcementStrategy.STATIC_VERIFICATION,
            verification_status=VerificationStatus.PENDING,
            category=raw.get("category", "general"),
            metadata={"capability": cap}
        )

    @classmethod
    def normalize_scope_boundary(cls, index: int, raw: dict[str, str]) -> RequirementItem:
        req_id = f"SCOPE-{index:03d}"
        bound = raw.get("boundary", "")
        return RequirementItem(
            id=req_id,
            source_text=raw.get("source_text", ""),
            normalized_description=bound,
            req_type=RequirementType.REQUIRED,
            priority=ConstraintPriority.USER_SCOPE_BOUNDARY,
            enforcement_strategy=EnforcementStrategy.HARD_GATE,
            verification_status=VerificationStatus.PENDING,
            category=raw.get("category", "scope"),
            metadata={"boundary": bound}
        )

    @classmethod
    def normalize_deliverable(cls, index: int, raw: dict[str, str]) -> RequirementItem:
        req_id = f"DELIV-{index:03d}"
        deliv = raw.get("deliverable", "")
        return RequirementItem(
            id=req_id,
            source_text=raw.get("source_text", ""),
            normalized_description=f"Produce deliverable: {deliv}",
            req_type=RequirementType.REQUIRED,
            priority=ConstraintPriority.ACCEPTANCE_CRITERIA,
            enforcement_strategy=EnforcementStrategy.RUNTIME_TEST,
            verification_status=VerificationStatus.PENDING,
            category=raw.get("category", "deliverable"),
            metadata={"deliverable": deliv}
        )

    @classmethod
    def normalize_technical_requirement(cls, index: int, raw: dict[str, str]) -> RequirementItem:
        req_id = f"TECH-{index:03d}"
        tech = raw.get("requirement", "")
        return RequirementItem(
            id=req_id,
            source_text=raw.get("source_text", ""),
            normalized_description=tech,
            req_type=RequirementType.REQUIRED,
            priority=ConstraintPriority.REPOSITORY_COMPATIBILITY,
            enforcement_strategy=EnforcementStrategy.STATIC_VERIFICATION,
            verification_status=VerificationStatus.PENDING,
            category=raw.get("category", "technical"),
            metadata={"technical_requirement": tech}
        )

    @classmethod
    def normalize_assumption(cls, index: int, source_text: str, assumption_desc: str) -> RequirementItem:
        req_id = f"ASSUMP-{index:03d}"
        return RequirementItem(
            id=req_id,
            source_text=source_text,
            normalized_description=assumption_desc,
            req_type=RequirementType.ASSUMPTION,
            priority=ConstraintPriority.MODEL_ASSUMPTION,
            enforcement_strategy=EnforcementStrategy.ADVISORY,
            verification_status=VerificationStatus.PENDING,
            category="assumption",
            metadata={"assumption": assumption_desc}
        )

    @classmethod
    def normalize_ambiguity(cls, index: int, source_text: str, ambiguity_desc: str) -> RequirementItem:
        req_id = f"AMBIG-{index:03d}"
        return RequirementItem(
            id=req_id,
            source_text=source_text,
            normalized_description=ambiguity_desc,
            req_type=RequirementType.AMBIGUOUS,
            priority=ConstraintPriority.MODEL_ASSUMPTION,
            enforcement_strategy=EnforcementStrategy.ADVISORY,
            verification_status=VerificationStatus.UNVERIFIABLE,
            category="ambiguity",
            metadata={"ambiguity": ambiguity_desc}
        )
