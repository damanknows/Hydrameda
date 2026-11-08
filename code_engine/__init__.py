"""
Hydrameda Code Engine: Package Exports.
"""
from .change_manifest import FileOperation, FileChange, ChangeManifest
from .repository_mapper import RepositoryMapper
from .file_selector import FileSelector
from .diff_generator import DiffGenerator
from .patch_validator import PatchValidator, ValidationError
from .patch_engine import PatchEngine, PatchApplicationError
from .governance import (
    RiskLevel,
    RiskAssessment,
    ApprovalDecision,
    ConsolidationReport,
    GovernanceEngine,
    SafeConsolidator,
)

__all__ = [
    "FileOperation",
    "FileChange",
    "ChangeManifest",
    "RepositoryMapper",
    "FileSelector",
    "DiffGenerator",
    "PatchValidator",
    "ValidationError",
    "PatchEngine",
    "PatchApplicationError",
    "RiskLevel",
    "RiskAssessment",
    "ApprovalDecision",
    "ConsolidationReport",
    "GovernanceEngine",
    "SafeConsolidator",
]
