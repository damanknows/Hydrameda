#!/usr/bin/env python3
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import lancedb
from fastembed import TextEmbedding
from tree_sitter_language_pack import get_parser

HYDRAMEDA_ROOT = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda"))
OBSIDIAN_VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", Path.home() / "ObsidianVault"))
LANCEDB_DIR = HYDRAMEDA_ROOT / "lancedb_data"
WORKTREES_DIR = HYDRAMEDA_ROOT / "worktrees"
FASTEMBED_CACHE = HYDRAMEDA_ROOT / ".fastembed_cache"

IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "dist", "build", "n8n_data", "lancedb_data", "worktrees", ".fastembed_cache",
    ".python-3.12", "site-packages", ".idea", ".vscode"
}

_EMBEDDER_INSTANCE = None

def get_embedder() -> TextEmbedding:
    global _EMBEDDER_INSTANCE
    if _EMBEDDER_INSTANCE is None:
        FASTEMBED_CACHE.mkdir(parents=True, exist_ok=True)
        _EMBEDDER_INSTANCE = TextEmbedding(
            model_name="BAAI/bge-small-en-v1.5",
            cache_dir=str(FASTEMBED_CACHE)
        )
    return _EMBEDDER_INSTANCE


def extract_ast_skeleton(repo_path: str, max_chars: int = 5000) -> str:
    root = Path(repo_path).resolve()
    if not root.exists():
        return f"# Error: Path {repo_path} does not exist."

    py_parser = get_parser("python")
    lines: list[str] = [f"# AST REPO MAP: {root.name}"]

    for file_path in sorted(root.rglob("*.py")):
        rel_parts = file_path.relative_to(root).parts
        if any(part in IGNORE_DIRS or (part.startswith(".") and part != ".") for part in rel_parts):
            continue
        try:
            source_bytes = file_path.read_bytes()
            tree = py_parser.parse(source_bytes)
            rel_path = file_path.relative_to(root)
            file_sigs: list[str] = []

            for node in tree.root_node.children:
                if node.type in ("import_statement", "import_from_statement"):
                    stmt = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()
                    file_sigs.append(f"  {stmt}")
                elif node.type == "class_definition":
                    cls_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                    cls_header = cls_text.splitlines()[0].strip()
                    file_sigs.append(f"  {cls_header}")
                    body_node = node.child_by_field_name("body")
                    if body_node:
                        for child in body_node.children:
                            if child.type == "function_definition":
                                fn_header = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore").splitlines()[0].strip()
                                file_sigs.append(f"    {fn_header} ...")
                elif node.type == "function_definition":
                    fn_header = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").splitlines()[0].strip()
                    file_sigs.append(f"  {fn_header} ...")

            if file_sigs:
                lines.append(f"\n## File: {rel_path}")
                lines.extend(file_sigs)
        except Exception as e:  # noqa: BLE001 - graceful fallback on AST extraction error
            lines.append(f"\n## File: {file_path.name} (Parse warning: {e})")

    return "\n".join(lines)[:max_chars]


class ObsidianRAG:
    def __init__(self, vault_path: Path = OBSIDIAN_VAULT):
        self.vault_path = vault_path
        self.db = lancedb.connect(str(LANCEDB_DIR))
        self.embedder = get_embedder()
        self.table_name = "obsidian_notes"

    def sync_vault(self) -> int:
        md_files = [f for f in self.vault_path.rglob("*.md") if not any(p.startswith(".") for p in f.parts)]
        if not md_files:
            seed_file = self.vault_path / "Specs" / "hydrameda-architecture.md"
            seed_file.parent.mkdir(parents=True, exist_ok=True)
            seed_file.write_text(
                "# Hydrameda System Invariants\n"
                "- All generated Python code must include type hints and pass ruff, mypy, bandit, and pytest.\n"
                "- Avoid hardcoded secrets or unsafe eval/exec calls.\n"
            )
            md_files = [seed_file]

        texts = [f.read_text(encoding="utf-8", errors="ignore")[:3000] for f in md_files]
        embeddings = list(self.embedder.embed(texts))

        records: list[dict[str, Any]] = []
        for f, text, vec in zip(md_files, texts, embeddings):
            records.append({
                "path": str(f.relative_to(self.vault_path)),
                "content": text,
                "vector": vec.tolist()
            })

        existing_tables = self.db.list_tables() if hasattr(self.db, "list_tables") else self.db.table_names()
        if self.table_name not in existing_tables:
            self.db.create_table(self.table_name, data=records)
        else:
            tbl = self.db.open_table(self.table_name)
            try:
                tbl.merge_insert("path").when_matched_update_all().when_not_matched_insert_all().execute(records)
            except Exception:  # noqa: BLE001 - Fallback to table overwrite if schema changed
                # Fallback to table creation if schema changed
                self.db.create_table(self.table_name, data=records, mode="overwrite")
        return len(records)

    def query(self, query_text: str, top_k: int = 2) -> str:
        existing_tables = self.db.list_tables() if hasattr(self.db, "list_tables") else self.db.table_names()
        if self.table_name not in existing_tables:
            self.sync_vault()
        tbl = self.db.open_table(self.table_name)
        q_vec = next(iter(self.embedder.embed([query_text]))).tolist()
        results = tbl.search(q_vec).limit(top_k).to_list()
        return "\n\n".join([f"--- Vault Note: {r['path']} ---\n{r['content']}" for r in results])


TASK_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_task_id(task_id: str) -> str:
    """Enforce strict allowlist for task IDs.
    
    Rejects:
    - Non-string types, empty strings, strings > 64 chars
    - Path traversal sequences (..), absolute paths, separators (/, \\)
    - Null bytes and non-alphanumeric/underscore/hyphen characters
    """
    if not isinstance(task_id, str):
        raise TypeError(f"Task ID must be a string, got {type(task_id).__name__}")
    task_id = task_id.strip()
    if not task_id:
        raise ValueError("Task ID cannot be empty")
    if len(task_id) > 64:
        raise ValueError(f"Task ID exceeds maximum length of 64 characters: '{task_id}'")
    if "\0" in task_id:
        raise ValueError(f"Task ID contains null bytes: '{task_id}'")
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        raise ValueError(f"Task ID contains path separators or traversal sequences: '{task_id}'")
    if not TASK_ID_REGEX.match(task_id):
        raise ValueError(f"Task ID contains invalid characters (must match ^[a-zA-Z0-9_\\-]+$): '{task_id}'")
    return task_id


def create_isolated_worktree(repo_path: str, task_id: str) -> str:
    """Provisions an isolated Git worktree branch for safe execution.
    
    Guarantees:
    - Task ID validated against strict allowlist.
    - Target worktree path strictly contained beneath WORKTREES_DIR.
    - Symlink escape prevention.
    - Explicit collision rejection (never deletes existing worktrees implicitly).
    """
    valid_task_id = validate_task_id(task_id)
    repo = Path(repo_path).resolve()
    repo.mkdir(parents=True, exist_ok=True)

    worktree_root = WORKTREES_DIR.resolve()
    worktree_root.mkdir(parents=True, exist_ok=True)
    worktree_target = (worktree_root / valid_task_id).resolve()

    # Verify containment beneath configured worktree root
    if not worktree_target.is_relative_to(worktree_root) or worktree_target == worktree_root:
        raise ValueError(f"Worktree path '{worktree_target}' escapes worktree root '{worktree_root}'")

    # Reject collisions with existing task IDs
    if worktree_target.exists():
        raise FileExistsError(f"Task ID collision: Worktree already exists for '{valid_task_id}' at {worktree_target}")

    branch_name = f"hydrameda/{valid_task_id}"

    if not (repo / ".git").exists():
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Hydrameda Agent",
            "GIT_AUTHOR_EMAIL": "hydrameda@local",
            "GIT_COMMITTER_NAME": "Hydrameda Agent",
            "GIT_COMMITTER_EMAIL": "hydrameda@local",
        }
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "Initial root commit"],
            env=git_env, check=True, capture_output=True
        )

    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=False, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-D", branch_name], check=False, capture_output=True)
    try:
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-B", branch_name, str(worktree_target)], check=True, capture_output=True)
    except subprocess.CalledProcessError as wt_err:
        err_msg = wt_err.stderr.decode("utf-8", errors="ignore") if wt_err.stderr else str(wt_err)
        # If git worktree add cannot write to parent .git (e.g. read-only container/sandbox), fall back to isolated local clone
        if "read-only" in err_msg.lower() or "read-only" in str(wt_err).lower():
            subprocess.run(["git", "clone", "--no-hardlinks", str(repo), str(worktree_target)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(worktree_target), "checkout", "-b", branch_name], check=True, capture_output=True)
        else:
            raise
    return str(worktree_target)



