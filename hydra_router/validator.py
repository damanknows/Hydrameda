"""Structured output validation for LLM responses."""
import re
import json
from typing import Any, Callable, Dict, Optional, Type
from pydantic import BaseModel, ValidationError

from hydra_router.exceptions import StructuredOutputValidationError


class OutputValidator:
    """Helper methods for validating and parsing structured LLM responses."""

    @staticmethod
    def validate_pydantic(model_class: Type[BaseModel]) -> Callable[[str], BaseModel]:
        """Returns a validator function that parses JSON text into a Pydantic model."""
        def _validator(text: str) -> BaseModel:
            # Strip markdown json fences if present
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
                cleaned = re.sub(r"\n```$", "", cleaned)
            try:
                data = json.loads(cleaned)
                return model_class.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                raise StructuredOutputValidationError(
                    message=str(e),
                    raw_output=text,
                    schema_name=model_class.__name__
                ) from e
        return _validator

    @staticmethod
    def validate_xml_files(min_files: int = 1) -> Callable[[str], Dict[str, str]]:
        """Validates that the output contains valid <file name="...">...</file> blocks."""
        def _validator(text: str) -> Dict[str, str]:
            pattern = r'<file\s+name=["\']([^"\']+)["\']>\s*(.*?)\s*</file>'
            matches = re.findall(pattern, text, re.DOTALL)
            if len(matches) < min_files:
                raise StructuredOutputValidationError(
                    message=f"Expected at least {min_files} <file name=\"...\"> blocks, found {len(matches)}.",
                    raw_output=text,
                    schema_name="XML_File_Blocks"
                )
            extracted = {}
            for fname, code in matches:
                clean_name = fname.strip()
                if not clean_name:
                    raise StructuredOutputValidationError(
                        message="Encountered empty file name in <file> tag.",
                        raw_output=text,
                        schema_name="XML_File_Blocks"
                    )
                extracted[clean_name] = code
            return extracted
        return _validator

    @staticmethod
    def validate_markdown_spec(required_headings: Optional[list[str]] = None) -> Callable[[str], str]:
        """Validates that the output is non-empty markdown containing required specification headers."""
        reqs = required_headings or ["#"]

        def _validator(text: str) -> str:
            stripped = text.strip()
            if not stripped:
                raise StructuredOutputValidationError(
                    message="Specification output cannot be empty.",
                    raw_output=text,
                    schema_name="Markdown_Spec"
                )
            for h in reqs:
                if h not in stripped:
                    raise StructuredOutputValidationError(
                        message=f"Missing required heading pattern '{h}' in specification.",
                        raw_output=text,
                        schema_name="Markdown_Spec"
                    )
            return stripped
        return _validator
