"""Hydrameda Specification Auditor: Check D - Context Contamination.

Detects whether foreign concepts or outdated patterns from:
1. Historical RAG documentation (Obsidian notes)
2. Previous task implementations
3. Irrelevant repository modules
4. Unrelated agent assumptions
have contaminated the proposed specification and altered task intent.
"""
from __future__ import annotations

from typing import Any

from intent_engine.models import StructuredIntent

from .models import AuditFinding, ContaminationItem, ContaminationSource
from .specification_parser import ParsedSpecification


class ContextContaminationChecker:
    """Detects context leakage and semantic contamination from external reference materials."""

    @classmethod
    def check_contamination(
        cls,
        intent: StructuredIntent,
        parsed_spec: ParsedSpecification,
        vault_context: str = "",
        repo_context: str = "",
        retrieved_skills: list[dict[str, Any]] | None = None,
        retrieved_lessons: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, list[ContaminationItem], list[AuditFinding]]:
        """Audits context contamination.

        Returns:
            passed: bool (True if NO high-severity contamination altering task intent detected)
            contaminations: list of ContaminationItem objects
            findings: list of AuditFinding objects
        """
        contaminations: list[ContaminationItem] = []
        findings: list[AuditFinding] = []
        spec_text = parsed_spec.raw_markdown.lower()
        prompt_text = intent.original_prompt.lower()
        prohibitions = [p.normalized_description.lower() for p in intent.get_prohibitions()]

        # 1. Historical RAG Vault Contamination Check
        # Check if terms in vault notes that are NOT in prompt appear in spec
        if vault_context:
            vault_lower = vault_context.lower()
            # Filter out negative disclaimer lines (e.g. "- NO JWT headers")
            spec_lines_without_disclaimers = [
                line.lower() for line in spec_text.splitlines()
                if not any(disclaim in line.lower() for disclaim in (
                    "do not", "never", "avoid", "prohibit", "without", "unlike", "reject", "not include", "no ", "not ", "non-", "zero "
                ))
            ]
            affirmative_spec_text = "\n".join(spec_lines_without_disclaimers)

            # Look for distinctive architectural keywords in vault context
            candidate_keywords = ["jwt", "token_refresh", "bearer", "claims", "oauth", "fernet", "rsa"]
            for kw in candidate_keywords:
                is_prohibited = any(kw in p for p in prohibitions)
                is_unrequested = kw not in prompt_text
                if kw in vault_lower and (is_unrequested or is_prohibited) and kw in affirmative_spec_text:
                    severity = "CRITICAL" if is_prohibited else "HIGH"
                    item = ContaminationItem(
                        concept=kw,
                        source=ContaminationSource.HISTORICAL_RAG,
                        evidence=f"Keyword '{kw}' is present in background Obsidian vault notes and appeared in specification, but was not requested by user.",
                        provenance_id="RAG-VAULT-NOTE",
                        severity=severity,
                    )
                    contaminations.append(item)
                    findings.append(
                        AuditFinding(
                            check_name="CONTEXT_CONTAMINATION",
                            severity=severity,
                            description=f"Specification contaminated by historical RAG context: '{kw}'",
                            details=f"The architect incorporated '{kw}' from background vault notes, altering the requested design.",
                            remediation_instruction=f"Purge '{kw}' and design strictly according to the original user prompt."
                        )
                    )

        # 2. Previous Task / Foreign Implementation Check
        # Detect fake mock files (e.g. litellm, langgraph stubs)
        for imp in parsed_spec.referenced_imports:
            imp_lower = imp.lower()
            if imp_lower in {"litellm", "langgraph", "tree_sitter_language_pack"}:
                item = ContaminationItem(
                    concept=imp,
                    source=ContaminationSource.PREVIOUS_TASK,
                    evidence=f"Specification references framework stub '{imp}' characteristic of previous task import workarounds.",
                    provenance_id="PREV-TASK-STUB",
                    severity="CRITICAL",
                )
                contaminations.append(item)
                findings.append(
                    AuditFinding(
                        check_name="CONTEXT_CONTAMINATION",
                        severity="CRITICAL",
                        description=f"Specification references mock framework package '{imp}'",
                        details=f"Module '{imp}' resembles third-party framework stubs from previous failed tasks.",
                        remediation_instruction=f"Remove import of '{imp}'. Rely only on standard library or user-requested packages."
                    )
                )

        # 3. Agent Assumption Contamination
        for assumption in parsed_spec.declared_assumptions:
            ass_lower = assumption.lower()
            if any(p in ass_lower for p in prohibitions):
                item = ContaminationItem(
                    concept="Prohibited assumption",
                    source=ContaminationSource.AGENT_ASSUMPTION,
                    evidence=f"Declared assumption violates task constraints: '{assumption}'",
                    provenance_id="AGENT-ASSUMPTION",
                    severity="CRITICAL",
                )
                contaminations.append(item)
                findings.append(
                    AuditFinding(
                        check_name="CONTEXT_CONTAMINATION",
                        severity="CRITICAL",
                        description=f"Declared assumption introduces prohibited concept: '{assumption}'",
                        details=f"The agent assumed '{assumption}', which directly violates task prohibitions.",
                        remediation_instruction="Remove the assumption and adhere strictly to user constraints."
                    )
                )

        critical_or_high = [c for c in contaminations if c.severity in ("CRITICAL", "HIGH")]
        passed = (len(critical_or_high) == 0)
        return passed, contaminations, findings
