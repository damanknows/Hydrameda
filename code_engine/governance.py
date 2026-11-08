"""
Hydrameda Code Engine: Governance, Risk Assessment, Human Approval Gate, and Safe Consolidation.

Guarantees:
1. Human-in-the-Loop Governance: LangGraph interrupt/resume gate for proposed change sets.
2. Review Transparency: Displays task specifications, unified diffs, affected file paths, operations,
   risk classification, and verification status.
3. Cryptographic Binding: Approvals are cryptographically bound to the exact change-set hash (SHA256).
4. Stale Approval Revocation: If the proposed changes or hashes change (e.g., following a repair cycle),
   any previous approval is automatically revoked.
5. Destructive Protection: Destructive operations (DELETE) require explicit destructive authorization.
6. Main Branch Protection: Direct merging into the main branch requires explicit merge authorization.
7. Original Repository Preservation: The main repository is never touched unless merge is explicitly approved.
8. Safe Rollback: Changes are reverted from disk backups if rejected or if approval is stale.
"""
from __future__ import annotations
import enum
import hashlib
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from .change_manifest import ChangeManifest, FileChange, FileOperation
from .patch_engine import PatchEngine
from .diff_generator import DiffGenerator

logger = logging.getLogger(__name__)


class RiskLevel(str, enum.Enum):
    """Risk severity classification for proposed changes."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class RiskAssessment:
    """Evaluation of risk, impact, and safety boundaries for a proposed change-set."""
    level: RiskLevel
    reasons: List[str] = field(default_factory=list)
    destructive_operations: List[str] = field(default_factory=list)
    total_files: int = 0
    created_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    verification_passed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["level"] = self.level.value
        return d


@dataclass
class ApprovalDecision:
    """Structured human approval decision bound to an exact change-set hash."""
    approved: bool = False
    approved_manifest_hash: Optional[str] = None
    approver: str = "human"
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    destructive_approved: bool = False
    merge_approved: bool = False
    comments: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ApprovalDecision:
        return cls(
            approved=bool(data.get("approved", False)),
            approved_manifest_hash=data.get("approved_manifest_hash"),
            approver=data.get("approver", "human"),
            timestamp=data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
            destructive_approved=bool(data.get("destructive_approved", False)),
            merge_approved=bool(data.get("merge_approved", False)),
            comments=data.get("comments")
        )


@dataclass
class ConsolidationReport:
    """Comprehensive final report documenting change execution, git operations, and risk."""
    task_id: str
    status: str  # "CONSOLIDATED" | "COMMITTED_WORKTREE_ONLY" | "REJECTED" | "ROLLED_BACK" | "STALE_APPROVAL_REJECTED" | "VERIFICATION_FAILED"
    approved: bool
    merged_to_main: bool
    worktree_commit: Optional[str] = None
    main_commit: Optional[str] = None
    final_diff: Optional[str] = None
    manifest_hash: Optional[str] = None
    risk_level: Optional[str] = None
    files_affected: List[str] = field(default_factory=list)
    summary: str = ""
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            f"# 📜 Hydrameda Execution & Consolidation Report: Task `{self.task_id}`",
            f"- **Timestamp:** {self.timestamp}",
            f"- **Status:** `{self.status}`",
            f"- **Approved:** `{self.approved}`",
            f"- **Merged to Main:** `{self.merged_to_main}`",
            f"- **Risk Level:** `{self.risk_level or 'N/A'}`",
            f"- **Change-Set Hash:** `{self.manifest_hash or 'N/A'}`",
            f"- **Worktree Commit:** `{self.worktree_commit or 'N/A'}`",
            f"- **Main Commit:** `{self.main_commit or 'N/A'}`",
            "",
            "## Summary",
            self.summary,
            "",
            "## Affected Files",
        ]
        for f in self.files_affected:
            lines.append(f"- `{f}`")
        if self.final_diff:
            lines.extend([
                "",
                "## Final Diff",
                "```diff",
                self.final_diff,
                "```"
            ])
        return "\n".join(lines)


class GovernanceEngine:
    """Evaluates risk, formats review displays, and cryptographically validates approvals."""

    CRITICAL_FILE_PATTERNS = [
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "Dockerfile", "docker-compose.yml", ".github/", ".gitignore"
    ]

    @classmethod
    def assess_risk(cls, manifest: ChangeManifest, verification_passed: bool = True) -> RiskAssessment:
        """Computes risk classification based on file operations, file counts, critical paths, and verification status."""
        reasons: List[str] = []
        deleted = manifest.deleted_files
        created = manifest.created_files
        modified = manifest.modified_files
        total_files = len(manifest.changes)

        destructive = list(deleted)
        if destructive:
            reasons.append(f"Destructive operation: {len(destructive)} file(s) marked for DELETE: {', '.join(destructive)}")

        if not verification_passed:
            reasons.append("Verification checks failed or incomplete.")

        # Check critical file modifications
        critical_modified = []
        for path in manifest.target_files:
            if any(crit in path for crit in cls.CRITICAL_FILE_PATTERNS):
                critical_modified.append(path)
        if critical_modified:
            reasons.append(f"Modifies critical infrastructure file(s): {', '.join(critical_modified)}")

        # Check total file count
        if total_files > 5:
            reasons.append(f"Large changeset: affects {total_files} files.")

        # Determine level
        if destructive or not verification_passed:
            level = RiskLevel.CRITICAL
        elif critical_modified or total_files > 5:
            level = RiskLevel.HIGH
        elif total_files > 2 or modified:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW

        if not reasons:
            reasons.append("Low risk: additive modifications with verified test suite.")

        return RiskAssessment(
            level=level,
            reasons=reasons,
            destructive_operations=destructive,
            total_files=total_files,
            created_files=created,
            modified_files=modified,
            deleted_files=deleted,
            verification_passed=verification_passed
        )

    @classmethod
    def format_review_display(
        cls,
        spec_md: str,
        manifest: ChangeManifest,
        risk: RiskAssessment,
        verification_summary: Optional[Dict[str, Any]] = None
    ) -> str:
        """Builds a rich markdown display for human review in LangGraph interrupt."""
        if not manifest.unified_diff:
            DiffGenerator.generate_manifest_diff(manifest)

        lines = [
            f"# 🛡️ Hydrameda Governance Review: Task `{manifest.task_id}`",
            "",
            f"**Change-Set Hash (SHA256):** `{manifest.manifest_hash}`",
            f"**Risk Level:** `{risk.level.value}`",
            f"**Total Affected Files:** {risk.total_files} (Created: {len(risk.created_files)}, Modified: {len(risk.modified_files)}, Deleted: {len(risk.deleted_files)})",
            "",
            "## ⚠️ Risk Assessment",
        ]
        for r in risk.reasons:
            lines.append(f"- {r}")

        lines.extend([
            "",
            "## 🧪 Verification Status",
        ])
        if verification_summary:
            status = verification_summary.get("overall_status", "UNKNOWN")
            lines.append(f"**Overall Status:** `{status}`")
            checks = verification_summary.get("checks", {})
            for c_name, c_info in checks.items():
                c_status = c_info.get("status") if isinstance(c_info, dict) else getattr(c_info, "status", str(c_info))
                lines.append(f"- `{c_name}`: `{c_status}`")
        else:
            lines.append(f"**Verification Passed:** `{risk.verification_passed}`")

        lines.extend([
            "",
            "## 📁 Affected Files & Operations",
            "| File Path | Operation | Proposed Hash | Authorized |",
            "|:---|:---:|:---:|:---:|"
        ])
        for c in sorted(manifest.changes, key=lambda x: x.file_path):
            p_hash = (c.proposed_hash or "N/A")[:10]
            lines.append(f"| `{c.file_path}` | `{c.operation.value}` | `{p_hash}` | `{c.authorized}` |")

        lines.extend([
            "",
            "## 📝 Proposed Unified Diff",
            "```diff",
            manifest.unified_diff or "No unified diff available.",
            "```",
            "",
            "## 📋 Specification Preview",
            spec_md[:800] + ("..." if len(spec_md) > 800 else "")
        ])

        return "\n".join(lines)

    @classmethod
    def validate_approval(
        cls,
        decision: Optional[ApprovalDecision],
        manifest: ChangeManifest,
        verification_passed: bool = True
    ) -> Tuple[bool, List[str]]:
        """Cryptographically validates the approval decision against the exact manifest hash and security constraints."""
        reasons: List[str] = []

        if decision is None:
            return False, ["Missing approval decision: approval data is required."]

        if not isinstance(decision, ApprovalDecision):
            return False, ["Invalid approval decision: must be an instance of ApprovalDecision."]

        if not decision.approved:
            return False, ["Approval explicitly denied or not granted."]

        # Verification check
        if not verification_passed:
            reasons.append("Cannot consolidate: deterministic verification checks failed.")

        # Cryptographic Hash Binding Check (Prevent Stale Approvals)
        current_hash = manifest.manifest_hash
        if not decision.approved_manifest_hash:
            reasons.append("Approval lacks cryptographic hash binding (approved_manifest_hash is empty).")
        elif decision.approved_manifest_hash != current_hash:
            reasons.append(
                f"STALE APPROVAL: Approved hash `{decision.approved_manifest_hash}` does not match "
                f"current manifest hash `{current_hash}`. The codebase has changed since approval was granted."
            )

        # Destructive Operation Authorization Check
        if manifest.deleted_files and not decision.destructive_approved:
            reasons.append(
                f"DESTRUCTIVE OPERATIONS UNAPPROVED: Manifest contains {len(manifest.deleted_files)} DELETE operations "
                f"({', '.join(manifest.deleted_files)}), but destructive_approved is False."
            )

        if reasons:
            return False, reasons
        return True, ["Approval cryptographically verified and valid."]


class SafeConsolidator:
    """Executes safe change consolidation, rollback on rejection/stale approval, and git operations."""

    @classmethod
    def consolidate_changes(
        cls,
        worktree_path: Path | str,
        main_repo_path: Path | str,
        manifest: ChangeManifest,
        decision: ApprovalDecision,
        verification_passed: bool = True,
        author: str = "Hydrameda Agent"
    ) -> ConsolidationReport:
        """Consolidates changes after cryptographically validating the approval decision."""
        wt = Path(worktree_path).resolve()
        main_repo = Path(main_repo_path).resolve()

        # Step 1: Validate Approval
        valid, val_reasons = GovernanceEngine.validate_approval(decision, manifest, verification_passed)
        risk = GovernanceEngine.assess_risk(manifest, verification_passed)

        # Ensure diff is generated
        if not manifest.unified_diff:
            DiffGenerator.generate_manifest_diff(manifest)

        # If invalid or rejected: execute rollback if applied
        if not valid:
            status = "REJECTED"
            if any("STALE APPROVAL" in r for r in val_reasons):
                status = "STALE_APPROVAL_REJECTED"
            elif any("DESTRUCTIVE OPERATIONS UNAPPROVED" in r for r in val_reasons):
                status = "DESTRUCTIVE_UNAPPROVED"
            elif not verification_passed:
                status = "VERIFICATION_FAILED"

            if manifest.applied and wt.exists() and wt != main_repo:
                try:
                    patch_engine = PatchEngine(target_worktree=wt, allow_delete=True)
                    rollback_success = patch_engine.rollback_manifest(manifest)
                    if not rollback_success:
                        val_reasons.append(
                            f"Rollback incomplete for files: {manifest.metadata.get('rollback_failures')}. "
                            f"Backups preserved at: {manifest.metadata.get('backup_dir')}"
                        )
                except Exception as rollback_err:
                    val_reasons.append(f"Rollback error: {rollback_err}")

            return ConsolidationReport(
                task_id=manifest.task_id,
                status=status,
                approved=False,
                merged_to_main=False,
                worktree_commit=None,
                main_commit=None,
                final_diff=manifest.unified_diff,
                manifest_hash=manifest.manifest_hash,
                risk_level=risk.level.value,
                files_affected=manifest.target_files,
                summary=f"Consolidation halted: {'; '.join(val_reasons)}"
            )

        # Step 2: Valid approval - commit to worktree
        worktree_commit = None
        main_commit = None
        merged_to_main = False
        summary_messages = ["Approval validated."]

        if wt.exists() and (wt / ".git").exists():
            try:
                subprocess.run(["git", "-C", str(wt), "config", "user.name", author], check=False, capture_output=True)
                subprocess.run(["git", "-C", str(wt), "config", "user.email", "hydrameda@local"], check=False, capture_output=True)

                subprocess.run(["git", "-C", str(wt), "add", "-A"], check=False, capture_output=True)
                commit_msg = f"feat({manifest.task_id}): {manifest.description or 'verified autonomous build'}"
                subprocess.run(["git", "-C", str(wt), "commit", "-m", commit_msg], check=False, capture_output=True, text=True)
                rev = subprocess.run(["git", "-C", str(wt), "rev-parse", "--short", "HEAD"], check=False, capture_output=True, text=True)
                worktree_commit = rev.stdout.strip() or "committed"
                summary_messages.append(f"Changes committed to worktree branch ({worktree_commit}).")
            except Exception as e:
                summary_messages.append(f"Worktree commit warning: {e}")
                worktree_commit = "commit-error"

        # Step 3: Conditional Merge into Main Branch
        # STRICT CONSTRAINT: Require approval before merging into the main branch.
        # Preserve the original repository intact if merge_approved is False!
        if decision.merge_approved:
            if main_repo.exists() and (main_repo / ".git").exists() and main_repo != wt:
                branch_name = f"hydrameda/{manifest.task_id}"
                try:
                    merge_res = subprocess.run(
                        ["git", "-C", str(main_repo), "merge", "--no-ff", branch_name, "-m", f"Merge {branch_name} into main"],
                        check=False, capture_output=True, text=True
                    )
                    if merge_res.returncode == 0:
                        rev_main = subprocess.run(["git", "-C", str(main_repo), "rev-parse", "--short", "HEAD"], check=False, capture_output=True, text=True)
                        main_commit = rev_main.stdout.strip()
                        merged_to_main = True
                        summary_messages.append(f"Merged {branch_name} into main ({main_commit}).")
                    else:
                        summary_messages.append(f"Merge to main failed: {merge_res.stderr.strip()}")
                except Exception as merge_err:
                    summary_messages.append(f"Merge error: {merge_err}")
            else:
                summary_messages.append("Main repository git merge skipped (main_repo == worktree or missing .git).")
        else:
            summary_messages.append("Merge to main branch was NOT requested (changes preserved in worktree branch only). Original repository preserved intact.")

        status = "CONSOLIDATED" if merged_to_main else "COMMITTED_WORKTREE_ONLY"

        return ConsolidationReport(
            task_id=manifest.task_id,
            status=status,
            approved=True,
            merged_to_main=merged_to_main,
            worktree_commit=worktree_commit,
            main_commit=main_commit,
            final_diff=manifest.unified_diff,
            manifest_hash=manifest.manifest_hash,
            risk_level=risk.level.value,
            files_affected=manifest.target_files,
            summary=" ".join(summary_messages)
        )
