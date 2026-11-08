"""
Hydrameda Code Engine: Unified Change Manifest and Operation Models.

Defines:
- FileOperation: CREATE, MODIFY, DELETE
- FileChange: Proposed individual file modification with content/diff/hash
- ChangeManifest: Complete atomic multi-file changeset with validation metadata
"""
from __future__ import annotations
import enum
import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any


class FileOperation(str, enum.Enum):
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    DELETE = "DELETE"


@dataclass
class FileChange:
    file_path: str                          # Relative path within target repository/worktree
    operation: FileOperation                # CREATE, MODIFY, DELETE
    proposed_content: Optional[str] = None  # Full content for CREATE or MODIFY
    original_content: Optional[str] = None  # Original content before modification
    diff: Optional[str] = None              # Unified diff representation
    original_hash: Optional[str] = None     # SHA256 of original file if existed
    proposed_hash: Optional[str] = None     # SHA256 of new content
    authorized: bool = False                # Explicit authorization (required for DELETE)
    backup_path: Optional[str] = None       # Preserved rollback path

    def __post_init__(self):
        if isinstance(self.operation, str):
            self.operation = FileOperation(self.operation.upper())
        if self.proposed_content is not None and self.proposed_hash is None:
            self.proposed_hash = hashlib.sha256(self.proposed_content.encode("utf-8")).hexdigest()
        if self.original_content is not None and self.original_hash is None:
            self.original_hash = hashlib.sha256(self.original_content.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["operation"] = self.operation.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FileChange:
        d = dict(data)
        d["operation"] = FileOperation(d["operation"])
        return cls(**d)


@dataclass
class ChangeManifest:
    task_id: str
    description: str
    changes: List[FileChange] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    allowed_base_path: Optional[str] = None
    applied: bool = False
    unified_diff: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_change(self, change: FileChange) -> None:
        self.changes.append(change)

    @property
    def target_files(self) -> List[str]:
        return [c.file_path for c in self.changes]

    @property
    def created_files(self) -> List[str]:
        return [c.file_path for c in self.changes if c.operation == FileOperation.CREATE]

    @property
    def modified_files(self) -> List[str]:
        return [c.file_path for c in self.changes if c.operation == FileOperation.MODIFY]

    @property
    def deleted_files(self) -> List[str]:
        return [c.file_path for c in self.changes if c.operation == FileOperation.DELETE]

    @property
    def manifest_hash(self) -> str:
        """Cryptographically binds the manifest to its exact set of changes."""
        payload = []
        for c in sorted(self.changes, key=lambda x: x.file_path):
            payload.append(f"{c.file_path}:{c.operation.value}:{c.proposed_hash or ''}:{c.original_hash or ''}")
        raw = "\n".join(payload).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "timestamp": self.timestamp,
            "allowed_base_path": self.allowed_base_path,
            "applied": self.applied,
            "unified_diff": self.unified_diff,
            "manifest_hash": self.manifest_hash,
            "metadata": self.metadata,
            "changes": [c.to_dict() for c in self.changes]
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ChangeManifest:
        changes = [FileChange.from_dict(c) for c in data.get("changes", [])]
        return cls(
            task_id=data["task_id"],
            description=data.get("description", ""),
            timestamp=data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
            allowed_base_path=data.get("allowed_base_path"),
            applied=data.get("applied", False),
            unified_diff=data.get("unified_diff"),
            metadata=data.get("metadata", {}),
            changes=changes
        )

    @classmethod
    def from_json(cls, json_str: str) -> ChangeManifest:
        return cls.from_dict(json.loads(json_str))
