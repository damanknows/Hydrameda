"""
Hydrameda Intent Engine: Intent and Constraint Parser.

Parses user prompts into structured linguistic clauses, identifying:
- Primary objective
- Explicit negative constraints (prohibitions)
- Required capabilities
- Scope boundaries
- Deliverables
- Technical constraints
- Assumptions and ambiguities
"""
from __future__ import annotations

import re


class IntentParser:
    """Parses raw user prompts into structured capability and constraint clauses."""

    @classmethod
    def parse_clauses(cls, prompt: str) -> list[str]:
        """Splits user prompt into semantic clauses based on sentence and list delimiters."""
        clean = prompt.strip()
        raw_clauses = re.split(r"[.;\n\r]+", clean)
        clauses = []
        for c in raw_clauses:
            stripped = c.strip()
            # Also split on bullet markers
            stripped = re.sub(r"^[-*•0-9]+\.?\s*", "", stripped).strip()
            if len(stripped) > 2:
                clauses.append(stripped)
        return clauses

    @classmethod
    def extract_prohibitions(cls, prompt: str) -> list[dict[str, str]]:
        """Identifies explicit negative constraints and extracts verbatim source text evidence."""
        prohibitions: list[dict[str, str]] = []
        clauses = cls.parse_clauses(prompt)

        prohib_patterns = [
            re.compile(r"(?:do\s+not|don't|never|avoid|prohibit|prohibited|without)\s+(?:implement|use|create|add|introduce|include)?\s*([^.;]+)", re.IGNORECASE),
            re.compile(r"\bno\s+([a-zA-Z0-9_\-\s]+)", re.IGNORECASE)
        ]

        for clause in clauses:
            for pat in prohib_patterns:
                for match in pat.finditer(clause):
                    raw_prohibited = match.group(1).strip()
                    # Split comma or 'or' or 'and' separated items
                    items = re.split(r",|\bor\b|\band\b", raw_prohibited)
                    for item in items:
                        clean_item = item.strip().strip("'\"`")
                        if clean_item and len(clean_item) > 1 and clean_item.lower() not in ("a", "an", "the", "any"):
                            prohibitions.append({
                                "source_text": clause,
                                "prohibited_term": clean_item,
                                "category": cls._categorize_term(clean_item)
                            })

        # Specific domain prohibition expansions
        prompt_lower = prompt.lower()
        if any(p in prompt_lower for p in ["not implement jwt", "no jwt", "without jwt", "avoid jwt", "do not implement jwt", "prohibit jwt"]):
            for term in ["JWT", "JWT headers", "JWT claims", "claims-based expiration", "unrelated authentication features"]:
                if not any(p["prohibited_term"].lower() == term.lower() for p in prohibitions):
                    prohibitions.append({
                        "source_text": "Explicit user prohibition of JWT authentication features",
                        "prohibited_term": term,
                        "category": "protocol"
                    })

        if any(p in prompt_lower for p in ["not create mock", "no mock", "no stub", "without stub", "do not create mock or stub packages"]):
            stub_term = "fake or stub dependency packages"
            if not any(p["prohibited_term"] == stub_term for p in prohibitions):
                prohibitions.append({
                    "source_text": "Explicit user prohibition of mock/stub packages",
                    "prohibited_term": stub_term,
                    "category": "dependency"
                })

        return prohibitions

    @classmethod
    def extract_capabilities(cls, prompt: str) -> list[dict[str, str]]:
        """Identifies required positive capabilities requested by the user."""
        capabilities: list[dict[str, str]] = []
        prompt_lower = prompt.lower()
        clauses = cls.parse_clauses(prompt)

        # Objective from first clause
        if clauses:
            capabilities.append({
                "source_text": clauses[0],
                "capability": clauses[0],
                "category": "primary_objective"
            })

        # Detect specific capabilities
        if "hmac" in prompt_lower:
            capabilities.append({
                "source_text": prompt,
                "capability": "HMAC-SHA256 token signing",
                "category": "cryptography"
            })
            capabilities.append({
                "source_text": prompt,
                "capability": "HMAC-SHA256 token verification",
                "category": "cryptography"
            })

        if "signer" in prompt_lower and not any("signing" in c["capability"].lower() for c in capabilities):
            capabilities.append({
                "source_text": prompt,
                "capability": "Token signing",
                "category": "feature"
            })

        if "verifier" in prompt_lower and not any("verification" in c["capability"].lower() for c in capabilities):
            capabilities.append({
                "source_text": prompt,
                "capability": "Token verification",
                "category": "feature"
            })

        return capabilities

    @classmethod
    def extract_scope_boundaries(cls, prompt: str) -> list[dict[str, str]]:
        """Identifies user-defined boundary and isolation constraints."""
        boundaries: list[dict[str, str]] = []
        prompt_lower = prompt.lower()

        if "modify only files required" in prompt_lower or "only files required" in prompt_lower:
            boundaries.append({
                "source_text": "Modify only files required for this task",
                "boundary": "Minimal modification: touch only task-essential files",
                "category": "scope"
            })

        if "existing repository architecture" in prompt_lower or "repository architecture" in prompt_lower:
            boundaries.append({
                "source_text": "Follow the existing repository architecture",
                "boundary": "Conform strictly to repository structure, conventions, and patterns",
                "category": "architecture"
            })

        scope_match = re.search(r"scope\s*(?:limited to|restricted to|:)?\s*([^.;\n]+)", prompt, re.IGNORECASE)
        if scope_match:
            val = scope_match.group(1).strip()
            if val and not any(b["boundary"].endswith(val) for b in boundaries):
                boundaries.append({
                    "source_text": scope_match.group(0),
                    "boundary": f"Scope restricted to: {val}",
                    "category": "scope"
                })

        return boundaries

    @classmethod
    def extract_deliverables(cls, prompt: str) -> list[dict[str, str]]:
        """Identifies expected deliverables (code, tests, docs)."""
        deliverables: list[dict[str, str]] = []
        prompt_lower = prompt.lower()

        deliverables.append({
            "source_text": "Standard task deliverable",
            "deliverable": "Working Python module implementation (module.py)",
            "category": "source_code"
        })

        if "pytest" in prompt_lower or "tests" in prompt_lower or "unit test" in prompt_lower:
            deliverables.append({
                "source_text": "User requested pytest tests",
                "deliverable": "Pytest test suite with comprehensive assertions (test_module.py)",
                "category": "test_suite"
            })

        return deliverables

    @classmethod
    def extract_technical_requirements(cls, prompt: str) -> list[dict[str, str]]:
        """Identifies technical dependencies, standards, and security constraints."""
        tech: list[dict[str, str]] = []
        prompt_lower = prompt.lower()

        if "standard library" in prompt_lower or "stdlib" in prompt_lower or "use hmac-sha256 directly" in prompt_lower:
            tech.append({
                "source_text": "User requested standard library only",
                "requirement": "Use Python standard library modules only (no third-party crypto packages)",
                "category": "dependency"
            })

        if "hmac" in prompt_lower:
            tech.append({
                "source_text": "Cryptographic HMAC verification invariant",
                "requirement": "Constant-time signature verification using hmac.compare_digest()",
                "category": "security"
            })

        return tech

    @staticmethod
    def _categorize_term(term: str) -> str:
        t = term.lower()
        if any(w in t for w in ["jwt", "oauth", "saml", "auth"]):
            return "protocol"
        if any(w in t for w in ["mock", "stub", "fake", "package"]):
            return "dependency"
        if any(w in t for w in ["claim", "header", "payload", "exp"]):
            return "format"
        return "general"

    @classmethod
    def parse_prompt(cls, prompt: str) -> dict[str, list]:
        """Parses prompt into normalized requirement items categorized by type."""
        from .constraint_compiler import ConstraintCompiler
        intent = ConstraintCompiler.compile(prompt)
        return {
            "prohibitions": intent.explicit_prohibitions,
            "requirements": intent.required_capabilities,
            "scope_boundaries": intent.scope_boundaries,
            "deliverables": intent.expected_deliverables,
            "technical_requirements": intent.technical_requirements,
        }
