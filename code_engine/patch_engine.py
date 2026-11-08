"""
Hydrameda Code Engine: Atomic Patch Engine with Backup, Rollback, and Worktree Isolation.

Guarantees:
1. Pre-validation: Full path safety and boundaries validated BEFORE reading original files or touching disk.
2. Original file preservation: Backups created before every modify/delete operation.
3. Atomic application: If any write fails, automatic full rollback is executed.
4. Rollback correctness: Does not swallow exceptions, tracks per-file restoration, and preserves backups on error.
5. Isolation: Operates strictly inside target Git worktree; rejects symlinks escaping worktree.
"""
from __future__ import annotations
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from .change_manifest import ChangeManifest, FileChange, FileOperation
from .patch_validator import PatchValidator, ValidationError
from .diff_generator import DiffGenerator

logger = logging.getLogger(__name__)


class PatchApplicationError(Exception):
    """Raised when patch application fails during write or backup."""
    pass


class PatchEngine:
    """Applies and rolls back multi-file patches safely inside an isolated worktree."""

    def __init__(self, target_worktree: str | Path, allow_delete: bool = False):
        self.worktree = Path(target_worktree).resolve()
        if not self.worktree.exists():
            raise FileNotFoundError(f"Target worktree not found: {self.worktree}")
        self.validator = PatchValidator(self.worktree, allow_delete=allow_delete)

    def prepare_manifest(self, changes: List[Dict[str, Any]], description: str = "", task_id: str = "") -> ChangeManifest:
        """Constructs a ChangeManifest, validating every path BEFORE reading content or calculating diffs."""
        manifest = ChangeManifest(
            task_id=task_id or "unassigned",
            description=description,
            allowed_base_path=str(self.worktree)
        )

        for c_data in changes:
            rel_path = c_data["file_path"]

            # STEP 1: Validate path safety BEFORE accessing filesystem
            target_file = self.validator.validate_path_safety(rel_path)
            op = FileOperation(c_data["operation"])
            proposed_content = c_data.get("proposed_content")
            authorized = c_data.get("authorized", False)

            orig_content = None
            # STEP 2: Re-verify boundary and symlinks to prevent TOCTOU escapes before reading
            if target_file.exists() and not target_file.is_symlink() and target_file.is_file():
                resolved_target = target_file.resolve()
                try:
                    resolved_target.relative_to(self.worktree)
                except ValueError:
                    raise ValidationError(
                        f"Symlink traversal detected during read: '{rel_path}' -> '{resolved_target}'"
                    )
                orig_content = target_file.read_text(encoding="utf-8", errors="ignore")

            change = FileChange(
                file_path=rel_path,
                operation=op,
                proposed_content=proposed_content,
                original_content=orig_content,
                authorized=authorized
            )
            manifest.add_change(change)

        DiffGenerator.generate_manifest_diff(manifest)
        return manifest

    def apply_manifest(self, manifest: ChangeManifest) -> ChangeManifest:
        """Validates and atomically applies all file changes in the manifest with backup preservation."""
        # Step 1: Pre-validation of all changes before any disk write
        self.validator.validate_manifest(manifest)

        applied_changes: List[FileChange] = []
        backup_dir = Path(tempfile.mkdtemp(prefix=f"hydrameda_backup_{manifest.task_id}_"))

        try:
            for change in manifest.changes:
                target_path = self.validator.validate_path_safety(change.file_path)

                # Step 2: Backup original file if exists
                if target_path.exists() and target_path.is_file() and not target_path.is_symlink():
                    safe_backup_name = change.file_path.replace("/", "_").replace("\\", "_")
                    b_path = backup_dir / safe_backup_name
                    shutil.copy2(target_path, b_path)
                    change.backup_path = str(b_path)

                # Step 3: Execute file operation
                if change.operation == FileOperation.CREATE:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_text(change.proposed_content or "", encoding="utf-8")

                elif change.operation == FileOperation.MODIFY:
                    target_path.write_text(change.proposed_content or "", encoding="utf-8")

                elif change.operation == FileOperation.DELETE:
                    target_path.unlink()

                applied_changes.append(change)

            manifest.applied = True
            manifest.metadata["backup_dir"] = str(backup_dir)
            return manifest

        except Exception as err:
            # Step 4: Atomic rollback on failure
            success, failed_files, restored_files = self._rollback_applied(applied_changes)
            if success:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir, ignore_errors=True)
                raise PatchApplicationError(f"Patch application failed. All changes rolled back: {err}") from err
            else:
                # PRESERVE backup data if rollback is incomplete!
                manifest.metadata["rollback_failures"] = failed_files
                manifest.metadata["rollback_restored"] = restored_files
                manifest.metadata["backup_dir"] = str(backup_dir)
                failed_str = ", ".join(f"{f}: {e}" for f, e in failed_files.items())
                raise PatchApplicationError(
                    f"Patch application failed: {err}. IN ADDITION, rollback was incomplete for: [{failed_str}]. "
                    f"Backups preserved at: {backup_dir}"
                ) from err

    def rollback_manifest(self, manifest: ChangeManifest) -> bool:
        """Rolls back all changes in an applied manifest using preserved backups.
        
        Returns True if all files were restored cleanly, False if any restoration failed.
        Preserves backup files if rollback is incomplete.
        """
        if not manifest.applied:
            return False

        success, failed_files, restored_files = self._rollback_applied(manifest.changes)
        if success:
            manifest.applied = False
            backup_dir_str = manifest.metadata.get("backup_dir")
            if backup_dir_str and Path(backup_dir_str).exists():
                shutil.rmtree(backup_dir_str, ignore_errors=True)
            return True
        else:
            manifest.metadata["rollback_failures"] = failed_files
            manifest.metadata["rollback_restored"] = restored_files
            logger.critical(
                f"Rollback incomplete for manifest '{manifest.task_id}'. "
                f"Failed files: {failed_files}. Backups preserved at: {manifest.metadata.get('backup_dir')}"
            )
            return False

    def _rollback_applied(self, changes: List[FileChange]) -> Tuple[bool, Dict[str, str], List[str]]:
        """Reverts applied changes in reverse order.
        
        Guarantees:
        - Does NOT swallow exceptions.
        - Tracks restoration success per file.
        - Returns failure flag and mapping of failed files to error reasons.
        """
        failed_files: Dict[str, str] = {}
        restored_files: List[str] = []

        for change in reversed(changes):
            try:
                target_path = self.validator.validate_path_safety(change.file_path)
            except Exception as val_err:
                failed_files[change.file_path] = f"Validation failed during rollback: {val_err}"
                continue

            try:
                if change.operation == FileOperation.CREATE:
                    if target_path.exists() or target_path.is_symlink():
                        target_path.unlink()
                        restored_files.append(change.file_path)
                elif change.operation in (FileOperation.MODIFY, FileOperation.DELETE):
                    if change.backup_path and Path(change.backup_path).exists():
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(change.backup_path, target_path)
                        restored_files.append(change.file_path)
                    else:
                        failed_files[change.file_path] = f"Backup file missing or unreadable: {change.backup_path}"
            except Exception as exc:
                failed_files[change.file_path] = str(exc)
                logger.error(f"Rollback error on '{change.file_path}': {exc}")

        success = (len(failed_files) == 0)
        return success, failed_files, restored_files
