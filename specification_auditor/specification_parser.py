"""Hydrameda Specification Auditor: Specification AST & Content Parser.

Decomposes Markdown technical specifications into structural elements:
classes, functions, section headings, imports, explicit assumptions, and exceptions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedSpecification:
    """Structured extraction of elements within a technical specification."""
    raw_markdown: str
    headings: list[str] = field(default_factory=list)
    proposed_classes: list[str] = field(default_factory=list)
    proposed_functions: list[str] = field(default_factory=list)
    referenced_imports: list[str] = field(default_factory=list)
    declared_exceptions: list[str] = field(default_factory=list)
    declared_assumptions: list[str] = field(default_factory=list)
    code_blocks: list[str] = field(default_factory=list)


class SpecificationParser:
    """Extracts structural and semantic tokens from markdown specification text."""

    @classmethod
    def parse(cls, spec_md: str) -> ParsedSpecification:
        headings: list[str] = []
        classes: list[str] = []
        functions: list[str] = []
        imports: list[str] = []
        exceptions: list[str] = []
        assumptions: list[str] = []
        code_blocks: list[str] = []

        # 1. Extract markdown code blocks
        cb_matches = re.findall(r"```(?:python)?\s*\n(.*?)\n```", spec_md, re.DOTALL | re.IGNORECASE)
        code_blocks.extend(cb_matches)

        # 2. Extract headings
        for line in spec_md.splitlines():
            line_str = line.strip()
            if line_str.startswith("#"):
                headings.append(line_str.lstrip("#").strip())

            # Check for assumptions
            lower_line = line_str.lower()
            if any(lower_line.startswith(prefix) for prefix in ("assumes", "assumption:", "- assumption:", "* assumption:")):
                assumptions.append(line_str)

        # 3. Extract functions and classes (from code blocks and inline text)
        func_regex = r"(?:def\s+([A-Za-z0-9_]+)\s*\(|`([A-Za-z0-9_]+)\(\)`|`([A-Za-z0-9_]+)\([^)]*\)`|[-*]\s+([A-Za-z0-9_]+)\s*\(|\b([A-Za-z0-9_]{3,})\s*\([^)]*\))"
        skip_fn_names = {
            "if", "for", "while", "return", "with", "class", "def", "assert", "print",
            "int", "str", "dict", "list", "set", "tuple", "bytes", "bool", "float",
            "type", "len", "range", "format", "super", "any", "all", "optional", "union"
        }
        for match in re.finditer(func_regex, spec_md):
            fn = match.group(1) or match.group(2) or match.group(3) or match.group(4) or match.group(5)
            if fn and fn.lower() not in skip_fn_names and fn not in functions:
                functions.append(fn)

        class_regex = r"(?:class\s+([A-Za-z0-9_]+)\s*[:\(]|`class\s+([A-Za-z0-9_]+)`)"
        for match in re.finditer(class_regex, spec_md):
            cls_name = match.group(1) or match.group(2)
            if cls_name and cls_name not in classes:
                classes.append(cls_name)

        # 4. Extract imports
        import_regex = r"(?:from\s+([A-Za-z0-9_\.]+)\s+import|import\s+([A-Za-z0-9_\.]+))"
        for match in re.finditer(import_regex, spec_md):
            imp = match.group(1) or match.group(2)
            if imp and imp not in imports:
                imports.append(imp.split(".")[0])

        # 5. Extract raised exceptions
        exc_regex = r"raise\s+([A-Za-z0-9_]+)"
        for match in re.finditer(exc_regex, spec_md):
            exc = match.group(1)
            if exc and exc not in exceptions:
                exceptions.append(exc)

        return ParsedSpecification(
            raw_markdown=spec_md,
            headings=headings,
            proposed_classes=classes,
            proposed_functions=functions,
            referenced_imports=imports,
            declared_exceptions=exceptions,
            declared_assumptions=assumptions,
            code_blocks=code_blocks,
        )
