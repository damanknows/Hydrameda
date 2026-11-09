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
import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from .change_manifest import ChangeManifest
from .diff_generator import DiffGenerator
from .patch_engine import PatchEngine

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
    reasons: list[str] = field(default_factory=list)
    destructive_operations: list[str] = field(default_factory=list)
    total_files: int = 0
    created_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    verification_passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["level"] = self.level.value
        return d


@dataclass
class ApprovalDecision:
    """Structured human approval decision bound to an exact change-set hash and task ID."""
    approved: bool = False
    approved_manifest_hash: str | None = None
    task_id: str | None = None
    approver: str = "human"
    approval_mode: str = "interactive"  # "interactive" | "explicit_async" | "trusted_policy"
    authorization_source: str = "human"
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    destructive_approved: bool = False
    merge_approved: bool = False
    comments: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalDecision:
        approved_val = data.get("approved")
        # STRICT REQUIREMENT: Require actual boolean approval value. Reject strings ("false", "yes", "true", "approved").
        if not isinstance(approved_val, bool):
            is_approved = False
        else:
            is_approved = approved_val

        destructive_val = data.get("destructive_approved", False)
        destructive_approved = destructive_val if isinstance(destructive_val, bool) else False

        merge_val = data.get("merge_approved", False)
        merge_approved = merge_val if isinstance(merge_val, bool) else False

        return cls(
            approved=is_approved,
            approved_manifest_hash=data.get("approved_manifest_hash"),
            task_id=data.get("task_id"),
            approver=str(data.get("approver", "human")),
            approval_mode=str(data.get("approval_mode", "interactive")),
            authorization_source=str(data.get("authorization_source", "human")),
            timestamp=data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
            destructive_approved=destructive_approved,
            merge_approved=merge_approved,
            comments=data.get("comments")
        )


@dataclass
class ConsolidationReport:
    """Comprehensive final report documenting change execution, git operations, and risk."""
    task_id: str
    status: str  # "CONSOLIDATED" | "COMMITTED_WORKTREE_ONLY" | "REJECTED" | "ROLLED_BACK" | "STALE_APPROVAL_REJECTED" | "VERIFICATION_FAILED"
    approved: bool
    merged_to_main: bool
    worktree_commit: str | None = None
    main_commit: str | None = None
    final_diff: str | None = None
    manifest_hash: str | None = None
    risk_level: str | None = None
    files_affected: list[str] = field(default_factory=list)
    summary: str = ""
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
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

    CRITICAL_FILE_PATTERNS: ClassVar[list[str]] = [
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "Dockerfile", "docker-compose.yml", ".github/", ".gitignore"
    ]

    @classmethod
    def assess_risk(cls, manifest: ChangeManifest, verification_passed: bool = True) -> RiskAssessment:
        """Computes risk classification based on file operations, file counts, critical paths, and verification status."""
        reasons: list[str] = []
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
        verification_summary: dict[str, Any] | None = None
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
        decision: ApprovalDecision | None,
        manifest: ChangeManifest,
        verification_passed: bool = True
    ) -> tuple[bool, list[str]]:
        """Cryptographically validates the approval decision against the exact manifest hash and security constraints."""
        reasons: list[str] = []

        if decision is None:
            return False, ["Missing approval decision: approval data is required."]

        if not isinstance(decision, ApprovalDecision):
            return False, ["Invalid approval decision: must be an instance of ApprovalDecision."]

        # Requirement A1.1 & A1.2: Strictly require boolean True; reject strings or falsy values
        if not isinstance(decision.approved, bool) or decision.approved is not True:
            return False, ["Approval explicitly denied or not granted as a boolean True."]

        # Requirement A1.7: Ensure approval is tied to the task ID
        if decision.task_id and decision.task_id != manifest.task_id:
            reasons.append(
                f"TASK ID MISMATCH: Approval decision is for task '{decision.task_id}', "
                f"but manifest is for task '{manifest.task_id}'."
            )

        # Verification check
        if not verification_passed:
            reasons.append("Cannot consolidate: deterministic verification checks failed.")

        # Cryptographic Hash Binding Check (Prevent Stale Approvals)
        current_hash = manifest.manifest_hash
        if not decision.approved_manifest_hash or not str(decision.approved_manifest_hash).strip():
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
            elif any("TASK ID MISMATCH" in r for r in val_reasons):
                status = "REPLAY_REJECTED"
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
                except Exception as rollback_err:  # noqa: BLE001 - Safely report any error during worktree rollback
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

        git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": author,
            "GIT_AUTHOR_EMAIL": "hydrameda@local",
            "GIT_COMMITTER_NAME": author,
            "GIT_COMMITTER_EMAIL": "hydrameda@local",
        }

        if wt and wt != main_repo and wt.exists() and (wt / ".git").exists():
            try:
                # Requirement A5.3: Check worktree status before consolidation
                subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True, check=True)

                # Requirement A5.6: Associate verification record with exact commit SHA and manifest hash
                ver_record = {
                    "task_id": manifest.task_id,
                    "manifest_hash": manifest.manifest_hash,
                    "overall_status": "passed" if verification_passed else "failed",
                    "approver": decision.approver,
                    "approval_mode": decision.approval_mode,
                    "authorization_source": decision.authorization_source,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                ver_file = wt / ".hydrameda_verification.json"
                ver_file.write_text(json.dumps(ver_record, indent=2), encoding="utf-8")

                # Requirement A5.1: Validate all Git return codes
                subprocess.run(["git", "-C", str(wt), "add", "-A"], capture_output=True, text=True, check=True)
                status_after_add = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True, check=True)
                if status_after_add.stdout.strip():
                    commit_msg = f"feat({manifest.task_id}): {manifest.description or 'verified autonomous build'}"
                    subprocess.run(
                        ["git", "-C", str(wt), "commit", "-m", commit_msg],
                        env=git_env,
                        capture_output=True, text=True, check=True
                    )

                rev = subprocess.run(["git", "-C", str(wt), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
                worktree_commit = rev.stdout.strip()
                summary_messages.append(f"Changes committed to worktree branch ({worktree_commit}).")
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr.strip() if e.stderr else str(e)
                logger.error(f"Git operation failed during worktree commit: {err_msg}")
                return ConsolidationReport(
                    task_id=manifest.task_id,
                    status="GIT_COMMIT_FAILED",
                    approved=False,
                    merged_to_main=False,
                    worktree_commit=None,
                    main_commit=None,
                    final_diff=manifest.unified_diff,
                    manifest_hash=manifest.manifest_hash,
                    risk_level=risk.level.value,
                    files_affected=manifest.target_files,
                    summary=f"Git commit failed: {err_msg}"
                )
        else:
            summary_messages.append("No separate isolated worktree branch to commit to.")

        # Step 3: Conditional Merge into Main Branch
        if decision.merge_approved:
            if main_repo.exists() and (main_repo / ".git").exists() and main_repo != wt:
                branch_name = f"hydrameda/{manifest.task_id}"
                try:
                    # If worktree was cloned independently, ensure the branch is fetched into main_repo
                    if wt and wt.exists():
                        subprocess.run(
                            ["git", "-C", str(main_repo), "fetch", str(wt), f"{branch_name}:{branch_name}"],
                            env=git_env,
                            capture_output=True, check=False
                        )

                    merge_res = subprocess.run(
                        ["git", "-C", str(main_repo), "merge", "--no-ff", branch_name, "-m", f"Merge {branch_name} into main"],
                        env=git_env,
                        capture_output=True, text=True, check=False
                    )
                    if merge_res.returncode == 0:
                        rev_main = subprocess.run(["git", "-C", str(main_repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
                        main_commit = rev_main.stdout.strip()
                        merged_to_main = True
                        summary_messages.append(f"Merged {branch_name} into main ({main_commit}).")
                    else:
                        # Requirement A5.9: Handle Git conflicts explicitly; abort merge cleanly
                        subprocess.run(["git", "-C", str(main_repo), "merge", "--abort"], capture_output=True, check=False)
                        conflict_msg = merge_res.stderr.strip() or merge_res.stdout.strip()
                        return ConsolidationReport(
                            task_id=manifest.task_id,
                            status="MERGE_FAILED",
                            approved=False,
                            merged_to_main=False,
                            worktree_commit=worktree_commit,
                            main_commit=None,
                            final_diff=manifest.unified_diff,
                            manifest_hash=manifest.manifest_hash,
                            risk_level=risk.level.value,
                            files_affected=manifest.target_files,
                            summary=f"Merge to main failed: {conflict_msg}"
                        )
                except Exception as merge_err:  # noqa: BLE001 - Safely abort and report merge failure
                    subprocess.run(["git", "-C", str(main_repo), "merge", "--abort"], capture_output=True, check=False)
                    return ConsolidationReport(
                        task_id=manifest.task_id,
                        status="MERGE_FAILED",
                        approved=False,
                        merged_to_main=False,
                        worktree_commit=worktree_commit,
                        main_commit=None,
                        final_diff=manifest.unified_diff,
                        manifest_hash=manifest.manifest_hash,
                        risk_level=risk.level.value,
                        files_affected=manifest.target_files,
                        summary=f"Merge error: {merge_err}"
                    )
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
