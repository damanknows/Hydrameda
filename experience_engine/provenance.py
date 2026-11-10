"""Hydrameda Experience Engine: Provenance Tracker and Hash Verifier.

Maintains tamper-evident audit trails for all learned knowledge records,
linking every skill, lesson, and recipe back to its origin task execution trace.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import ExecutionOutcome, ProvenanceRecord


class ProvenanceTracker:
    """Tracks and validates provenance of knowledge records."""

    @staticmethod
    def compute_record_hash(
        source_task_id: str,
        outcome: ExecutionOutcome,
        content_payload: dict[str, Any]
    ) -> str:
        """Computes a deterministic SHA-256 validation hash for a knowledge item."""
        canon = json.dumps(
            {
                "source_task_id": source_task_id,
                "outcome": outcome.value,
                "payload": content_payload,
            },
            sort_keys=True
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    @classmethod
    def create_provenance(
        cls,
        source_task_id: str,
        outcome: ExecutionOutcome,
        content_payload: dict[str, Any],
        context_source: str = "execution_trace"
    ) -> ProvenanceRecord:
        """Creates a signed ProvenanceRecord with a cryptographic validation hash."""
        val_hash = cls.compute_record_hash(source_task_id, outcome, content_payload)
        return ProvenanceRecord(
            source_task_id=source_task_id,
            outcome=outcome,
            validation_hash=val_hash,
            context_source=context_source,
            authoritative=False,  # Explicitly non-authoritative (advisory)
        )

    @classmethod
    def verify_provenance(
        cls,
        record_provenance: ProvenanceRecord,
        content_payload: dict[str, Any]
    ) -> bool:
        """Verifies that a knowledge record's payload matches its validation hash."""
        if not record_provenance.validation_hash:
            return False
        expected = cls.compute_record_hash(
            record_provenance.source_task_id,
            record_provenance.outcome,
            content_payload
        )
        return record_provenance.validation_hash == expected
