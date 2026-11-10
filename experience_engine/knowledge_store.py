"""Hydrameda Experience Engine: Persistent Knowledge Store.

Maintains indexed storage of verified Skills, Lessons, and Verification Recipes.
Supports:
- Adding validated knowledge records with provenance verification.
- Querying by category, tags, and domain keywords.
- Automatic confidence decay calculation.
- Updating and revalidating records.
- Pre-populated seed knowledge for core engineering procedures and failure lessons.
"""
from __future__ import annotations

import json
from pathlib import Path

from .learning_policy import LearningPolicy
from .models import (
    ExecutionOutcome,
    KnowledgeCategory,
    KnowledgeRecord,
    ProvenanceRecord,
    ReusePolicy,
)


class KnowledgeStore:
    """Persistent storage engine for Skills and Experience Lessons."""

    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or (Path.home() / "hydrameda" / "logs" / "experience_store.json")
        self._records: dict[str, KnowledgeRecord] = {}
        self._load()
        if not self._records:
            self._seed_default_knowledge()

    def _load(self) -> None:
        """Loads records from the JSON store file."""
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in raw.get("records", []):
                rec = KnowledgeRecord.from_dict(item)
                self._records[rec.knowledge_id] = rec
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            # Fallback gracefully if file corrupted or unreadable
            self._records = {}

    def save(self) -> None:
        """Saves current records to the JSON store file."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": "1.0",
                "records": [
                    LearningPolicy.sanitize_for_persistence(rec.to_dict())
                    for rec in self._records.values()
                ]
            }
            self.storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def add_record(self, record: KnowledgeRecord) -> bool:
        """Validates and persists a new knowledge record."""
        # 1. Validate category-specific policy
        if record.category == KnowledgeCategory.SKILL:
            valid, _reason = LearningPolicy.validate_candidate_skill(record)
            if not valid:
                return False
        elif record.category == KnowledgeCategory.LESSON:
            valid, _reason = LearningPolicy.validate_candidate_lesson(record)
            if not valid:
                return False

        self._records[record.knowledge_id] = record
        self.save()
        return True

    def get_record(self, knowledge_id: str) -> KnowledgeRecord | None:
        """Retrieves a single record by its ID."""
        return self._records.get(knowledge_id)

    def list_records(
        self,
        category: KnowledgeCategory | None = None,
        min_confidence: float = 0.30
    ) -> list[KnowledgeRecord]:
        """Lists records optionally filtered by category and decayed confidence."""
        results = []
        for rec in self._records.values():
            if category is not None and rec.category != category:
                continue
            decayed = rec.calculate_decayed_confidence()
            if decayed >= min_confidence:
                results.append(rec)
        return results

    def query(
        self,
        category: KnowledgeCategory | None = None,
        tags: list[str] | None = None,
        keywords: list[str] | None = None,
        min_confidence: float = 0.30
    ) -> list[KnowledgeRecord]:
        """Searches records matching category, tags, or keywords with decayed confidence."""
        matches: list[KnowledgeRecord] = []
        tags_set = {t.lower() for t in (tags or [])}
        kw_set = {k.lower() for k in (keywords or [])}

        for rec in self._records.values():
            if category is not None and rec.category != category:
                continue

            decayed = rec.calculate_decayed_confidence()
            if decayed < min_confidence:
                continue

            # Tag match
            rec_tags = {a.lower() for a in rec.applicability}
            if tags_set and tags_set.intersection(rec_tags):
                matches.append(rec)
                continue

            # Keyword match in title or description
            text = (rec.title + " " + rec.description).lower()
            if kw_set and any(kw in text for kw in kw_set):
                matches.append(rec)
                continue

            # If no tags or keywords provided, match everything in category
            if not tags_set and not kw_set:
                matches.append(rec)

        # Sort by confidence descending
        matches.sort(key=lambda r: r.calculate_decayed_confidence(), reverse=True)
        return matches

    def _seed_default_knowledge(self) -> None:
        """Seeds canonical foundational engineering skills and lessons."""
        # Seed Skill 1: HMAC Safe Implementation
        self.add_record(
            KnowledgeRecord(
                knowledge_id="SKILL-CRYPTO-HMAC",
                category=KnowledgeCategory.SKILL,
                title="Procedural Guide: Constant-Time HMAC-SHA256 Implementation",
                description="Engineering procedure for implementing symmetric message signing and verification without third-party dependencies.",
                procedure=[
                    "Use standard library `hmac` and `hashlib` modules directly.",
                    "Format token strings as `<payload_b64>.<signature_b64>` using `base64.urlsafe_b64encode`.",
                    "Use `hmac.new(key, payload_bytes, hashlib.sha256).digest()` to compute signatures.",
                    "Always verify using `hmac.compare_digest()` to eliminate timing attack vectors.",
                    "Reject inputs missing the single dot separator or with invalid base64 padding by raising ValueError."
                ],
                provenance=ProvenanceRecord(
                    source_task_id="seed-core",
                    outcome=ExecutionOutcome.VERIFIED_SUCCESS,
                    context_source="security_standard"
                ),
                evidence="NIST SP 800-107 and RFC 2104 constant-time HMAC invariants.",
                confidence=1.0,
                applicability=["crypto", "hmac", "sha256", "security", "token"],
                reuse_policy=ReusePolicy.PROCEDURAL_GUIDE,
            )
        )

        # Seed Skill 2: Import Resolution Diagnosis
        self.add_record(
            KnowledgeRecord(
                knowledge_id="SKILL-PYTHON-IMPORT",
                category=KnowledgeCategory.SKILL,
                title="Procedural Guide: Resolving Module and Package Import Resolution",
                description="Procedure for structuring tests and modules for deterministic Python import resolution.",
                procedure=[
                    "In `test_module.py`, use clean top-level imports: `from module import ...`.",
                    "Do not use intra-package relative imports (`from .module import ...`) in test files executed by pytest.",
                    "Heal missing standard library imports automatically by scanning AST node references.",
                    "Ensure root directory is present in sys.path or pytest pythonpath."
                ],
                provenance=ProvenanceRecord(
                    source_task_id="seed-core",
                    outcome=ExecutionOutcome.VERIFIED_SUCCESS,
                    context_source="toolchain_standard"
                ),
                evidence="Python 3.12 importlib scoping and pytest discovery standards.",
                confidence=0.95,
                applicability=["python", "imports", "pytest", "toolchain"],
                reuse_policy=ReusePolicy.VERIFICATION_RECIPE,
            )
        )

        # Seed Lesson 1: Negative Constraint Contamination Warning
        self.add_record(
            KnowledgeRecord(
                knowledge_id="LESSON-ANTI-JWT",
                category=KnowledgeCategory.LESSON,
                title="Failure Warning: Contamination of Non-JWT HMAC Requests with JWT Protocols",
                description="Historical failure where model training bias and historical notes re-introduced JWT into a strictly non-JWT HMAC task.",
                procedure=[
                    "Explicit user prohibitions represent an absolute hard veto over historical specs or background notes.",
                    "Do not import or declare JWT exception classes (JWTError, etc.) or functions (validate_jwt, etc.).",
                    "Never include JWT header serialization or claims dictionaries (exp, sub, iat).",
                    "Filter retrieved notes to redact JWT patterns before presenting context to the architect."
                ],
                provenance=ProvenanceRecord(
                    source_task_id="hydrameda-hmac-integration-002",
                    outcome=ExecutionOutcome.SEMANTIC_VIOLATION,
                    context_source="execution_audit"
                ),
                evidence="Task hydrameda-hmac-integration-002 passed static tests but violated intent by implementing validate_jwt().",
                confidence=0.99,
                applicability=["jwt", "hmac", "crypto", "negative_constraint", "anti_pattern"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
        )

        # Seed Lesson 2: Fake Stub Anti-Pattern
        self.add_record(
            KnowledgeRecord(
                knowledge_id="LESSON-ANTI-STUB",
                category=KnowledgeCategory.LESSON,
                title="Failure Warning: Never Create Fake Third-Party Stub Modules",
                description="Anti-pattern where an agent synthesizes fake third-party modules (litellm.py, langgraph/) to bypass missing imports.",
                procedure=[
                    "Never create mock top-level files matching framework or third-party packages in task worktrees.",
                    "If a dependency is missing, rely on standard library alternatives or report an environment requirement.",
                    "Inspect verification error traces to distinguish task logic errors from environment misconfigurations."
                ],
                provenance=ProvenanceRecord(
                    source_task_id="hydrameda-integration-001",
                    outcome=ExecutionOutcome.VERIFICATION_FAILURE,
                    context_source="execution_audit"
                ),
                evidence="Task hydrameda-integration-001 created litellm.py and langgraph/ stubs causing verification failure.",
                confidence=0.98,
                applicability=["imports", "dependencies", "stubs", "anti_pattern"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
        )
