"""
Hydrameda Code Engine: Relevant File Selector.

Uses lexical matching, symbol intersection, and dependency graphs
to identify the exact subset of files that must be read or modified for a task.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import List, Dict, Set, Any
from .repository_mapper import RepositoryMapper


class FileSelector:
    """Selects files relevant to a given user task prompt or architectural spec."""

    def __init__(self, mapper: RepositoryMapper):
        self.mapper = mapper
        self._cached_map: Dict[str, Any] = mapper.map_repository()

    def select_relevant_files(self, prompt: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Scores repository files against task prompt keywords and symbol names."""
        keywords = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", prompt.lower()))
        scored_files = []

        for f in self._cached_map.get("files", []):
            path_str = f["file"].lower()
            score = 0.0

            # 1. Path match
            for kw in keywords:
                if kw in path_str:
                    score += 5.0

            # 2. Function / Method name match
            for fn in f.get("functions", []):
                fn_lower = fn.lower()
                for kw in keywords:
                    if kw in fn_lower or fn_lower in kw:
                        score += 3.0

            # 3. Class name match
            for cls in f.get("classes", []):
                cls_lower = cls["name"].lower()
                for kw in keywords:
                    if kw in cls_lower or cls_lower in kw:
                        score += 4.0

            # 4. Imports / dependencies match
            for dep in f.get("dependencies", []):
                if dep.lower() in keywords:
                    score += 1.5

            if score > 0:
                scored_files.append({"file": f["file"], "score": score, "symbols": f})

        scored_files.sort(key=lambda x: x["score"], reverse=True)
        return scored_files[:top_k]

    def get_related_dependencies(self, selected_files: List[str]) -> Set[str]:
        """Finds all internal and external dependencies referenced by selected files."""
        deps = set()
        selected_set = set(selected_files)
        for f in self._cached_map.get("files", []):
            if f["file"] in selected_set:
                deps.update(f.get("dependencies", []))
        return deps
