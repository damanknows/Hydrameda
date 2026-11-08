"""
Hydrameda Code Engine: Patch and Path Validator.

Security & Integrity Rules:
1. Path traversal protection: Rejects any path with '..', leading slashes, absolute paths, or escaping base.
2. Allowed-path enforcement: All modifications must reside strictly within the designated target worktree.
3. Protected file restrictions: Rejects modification of .git, .env, secrets, and protected infra files.
4. Destruction authorization: DELETE operations require explicit authorized=True flag; otherwise rejected.
5. Operation consistency:
   - CREATE requires file to NOT already exist (or overwrite authorization).
   - MODIFY requires file to ALREADY exist.
   - DELETE requires file to ALREADY exist.
6. Hash validation: Ensures original content matches disk hash before modification (prevents clobbering).
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from .change_manifest import ChangeManifest, FileChange, FileOperation

PROTECTED_PATHS = {
    ".git", ".env", ".env.local", "docker-compose.yml", "Dockerfile.verifier"
}


class ValidationError(Exception):
    """Raised when a patch or file change violates safety constraints."""
    pass


class PatchValidator:
    """Validates patch operations, target paths, and authorization constraints."""

    def __init__(self, target_worktree: str | Path, allow_delete: bool = False):
        self.target_worktree = Path(target_worktree).resolve()
        self.allow_delete = allow_delete

    def validate_path_safety(self, rel_path: str) -> Path:
        """Enforces path traversal protection and allowed-path boundaries."""
        # 1. Deny empty or pure whitespace
        if not rel_path or not rel_path.strip():
            raise ValidationError("Target path cannot be empty.")

        clean_path = rel_path.strip()

        # 2. Deny absolute paths
        if clean_path.startswith("/") or clean_path.startswith("\\"):
            raise ValidationError(f"Path traversal detected: absolute paths not allowed: {clean_path}")

        # 3. Deny explicit directory traversal tokens
        parts = Path(clean_path).parts
        if ".." in parts:
            raise ValidationError(f"Path traversal detected: parent directory '..' is forbidden in '{clean_path}'.")

        # 4. Resolve against target worktree and verify boundary containment
        target_file = (self.target_worktree / clean_path).resolve()
        try:
            target_file.relative_to(self.target_worktree)
        except ValueError:
            raise ValidationError(f"Path escapes worktree boundary: '{clean_path}' -> '{target_file}'")

        # 5. Protected file check
        for part in parts:
            if part in PROTECTED_PATHS:
                raise ValidationError(f"Modification of protected path is prohibited: '{clean_path}' (contains '{part}')")

        return target_file

    def validate_change(self, change: FileChange) -> None:
        """Validates a single FileChange against security and consistency rules."""
        target_path = self.validate_path_safety(change.file_path)
        exists = target_path.exists()

        if change.operation == FileOperation.CREATE:
            if exists:
                raise ValidationError(f"Cannot CREATE existing file '{change.file_path}'. Use MODIFY instead.")
            if change.proposed_content is None:
                raise ValidationError(f"CREATE operation on '{change.file_path}' requires non-null proposed_content.")

        elif change.operation == FileOperation.MODIFY:
            if not exists:
                raise ValidationError(f"Cannot MODIFY non-existent file '{change.file_path}'. Use CREATE instead.")
            if not target_path.is_file():
                raise ValidationError(f"Target path '{change.file_path}' exists but is not a file.")
            if change.proposed_content is None:
                raise ValidationError(f"MODIFY operation on '{change.file_path}' requires proposed_content.")
            # Verify original hash if provided
            if change.original_hash:
                disk_content = target_path.read_bytes()
                disk_hash = hashlib.sha256(disk_content).hexdigest()
                if disk_hash != change.original_hash:
                    raise ValidationError(
                        f"Hash mismatch on '{change.file_path}'. Disk has changed since patch creation. "
                        f"Expected {change.original_hash}, got {disk_hash}."
                    )

        elif change.operation == FileOperation.DELETE:
            if not exists:
                raise ValidationError(f"Cannot DELETE non-existent file '{change.file_path}'.")
            if not self.allow_delete and not change.authorized:
                raise ValidationError(
                    f"Destructive operation REJECTED: DELETE on '{change.file_path}' requires explicit authorization."
                )

    def validate_manifest(self, manifest: ChangeManifest) -> List[str]:
        """Validates all changes in a manifest. Returns list of verified target paths or raises ValidationError."""
        verified_paths = []
        for change in manifest.changes:
            self.validate_change(change)
            verified_paths.append(change.file_path)
        return verified_paths
