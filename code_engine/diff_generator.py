"""
Hydrameda Code Engine: Unified Diff Generator.

Generates standard Git / GNU unified diffs between original content and proposed changes.
Computes change statistics (additions, deletions, modified files).
"""
from __future__ import annotations
import difflib
from typing import Dict, Any, Optional
from .change_manifest import FileChange, ChangeManifest, FileOperation


class DiffGenerator:
    """Generates standard unified diffs for file changes and full change manifests."""

    @staticmethod
    def generate_file_diff(change: FileChange) -> str:
        """Generates unified diff string for an individual FileChange."""
        orig_lines = (change.original_content or "").splitlines(keepends=True)
        new_lines = (change.proposed_content or "").splitlines(keepends=True)
        fname = change.file_path

        from_desc = f"a/{fname}" if change.operation != FileOperation.CREATE else "/dev/null"
        to_desc = f"b/{fname}" if change.operation != FileOperation.DELETE else "/dev/null"

        diff_lines = list(difflib.unified_diff(
            orig_lines,
            new_lines,
            fromfile=from_desc,
            tofile=to_desc,
            lineterm="\n"
        ))
        diff_str = "".join(diff_lines)
        change.diff = diff_str
        return diff_str

    @classmethod
    def generate_manifest_diff(cls, manifest: ChangeManifest) -> str:
        """Computes and concatenates unified diffs for all changes in a manifest."""
        full_diff = []
        for change in manifest.changes:
            d = cls.generate_file_diff(change)
            if d:
                full_diff.append(d)
        combined = "\n".join(full_diff)
        manifest.unified_diff = combined
        return combined

    @staticmethod
    def compute_diff_stats(diff_text: str) -> Dict[str, int]:
        """Calculates line additions and deletions from a unified diff."""
        additions = 0
        deletions = 0
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1
        return {"additions": additions, "deletions": deletions}
