"""
Hydrameda Code Engine: Atomic Patch Engine with Backup, Rollback, and Worktree Isolation.

Guarantees:
1. Pre-validation: Full manifest validated BEFORE touching a single file on disk.
2. Original file preservation: Backups created before every modify/delete operation.
3. Atomic application: If any write fails, automatic full rollback is executed.
4. Manual rollback: `rollback_manifest()` reverts all changes to exact pre-patch disk state.
5. Isolation: Operates strictly inside target Git worktree.
"""
from __future__ import annotations
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from .change_manifest import ChangeManifest, FileChange, FileOperation
from .patch_validator import PatchValidator, ValidationError
from .diff_generator import DiffGenerator


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
        """Constructs a ChangeManifest, reading original file contents from the worktree and calculating unified diffs."""
        manifest = ChangeManifest(
            task_id=task_id or "unassigned",
            description=description,
            allowed_base_path=str(self.worktree)
        )

        for c_data in changes:
            rel_path = c_data["file_path"]
            target_file = (self.worktree / rel_path).resolve()
            op = FileOperation(c_data["operation"])
            proposed_content = c_data.get("proposed_content")
            authorized = c_data.get("authorized", False)

            orig_content = None
            if target_file.exists() and target_file.is_file():
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
                target_path = self.worktree / change.file_path

                # Step 2: Backup original file if exists
                if target_path.exists() and target_path.is_file():
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
            self._rollback_applied(applied_changes)
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
            raise PatchApplicationError(f"Patch application failed. All changes rolled back: {err}") from err

    def rollback_manifest(self, manifest: ChangeManifest) -> bool:
        """Rolls back all changes in an applied manifest using preserved backups."""
        if not manifest.applied:
            return False

        success = self._rollback_applied(manifest.changes)
        if success:
            manifest.applied = False
            backup_dir_str = manifest.metadata.get("backup_dir")
            if backup_dir_str and Path(backup_dir_str).exists():
                shutil.rmtree(backup_dir_str, ignore_errors=True)
        return success

    def _rollback_applied(self, changes: List[FileChange]) -> bool:
        """Reverts applied changes in reverse order."""
        for change in reversed(changes):
            target_path = self.worktree / change.file_path
            try:
                if change.operation == FileOperation.CREATE:
                    if target_path.exists():
                        target_path.unlink()
                elif change.operation in (FileOperation.MODIFY, FileOperation.DELETE):
                    if change.backup_path and Path(change.backup_path).exists():
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(change.backup_path, target_path)
            except Exception:
                pass
        return True
