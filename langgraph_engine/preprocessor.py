#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any
import lancedb
from fastembed import TextEmbedding
from tree_sitter_language_pack import get_parser

OBSIDIAN_VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", Path.home() / "ObsidianVault"))
LANCEDB_DIR = Path.home() / "hydrameda" / "lancedb_data"
WORKTREES_DIR = Path.home() / "hydrameda" / "worktrees"
FASTEMBED_CACHE = Path.home() / "hydrameda" / ".fastembed_cache"

IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build", "n8n_data", "lancedb_data", "worktrees", ".fastembed_cache"}

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
    lines: List[str] = [f"# AST REPO MAP: {root.name}"]

    for file_path in sorted(root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in file_path.relative_to(root).parts):
            continue
        try:
            source_bytes = file_path.read_bytes()
            tree = py_parser.parse(source_bytes)
            rel_path = file_path.relative_to(root)
            file_sigs: List[str] = []

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
        except Exception as e:
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

        records: List[Dict[str, Any]] = []
        for f, text, vec in zip(md_files, texts, embeddings):
            records.append({
                "path": str(f.relative_to(self.vault_path)),
                "content": text,
                "vector": vec.tolist()
            })

        self.db.create_table(self.table_name, data=records, mode="overwrite")
        return len(records)

    def query(self, query_text: str, top_k: int = 2) -> str:
        existing_tables = self.db.list_tables() if hasattr(self.db, "list_tables") else self.db.table_names()
        if self.table_name not in existing_tables:
            self.sync_vault()
        tbl = self.db.open_table(self.table_name)
        q_vec = list(self.embedder.embed([query_text]))[0].tolist()
        results = tbl.search(q_vec).limit(top_k).to_list()
        return "\n\n".join([f"--- Vault Note: {r['path']} ---\n{r['content']}" for r in results])


def create_isolated_worktree(repo_path: str, task_id: str) -> str:
    repo = Path(repo_path).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    worktree_target = WORKTREES_DIR / task_id
    branch_name = f"hydrameda/{task_id}"

    if not (repo / ".git").exists():
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "hydrameda@local"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Hydrameda Agent"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "Initial root commit"], check=True, capture_output=True)

    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=False, capture_output=True)

    if worktree_target.exists():
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree_target)], check=False, capture_output=True)

    subprocess.run(["git", "-C", str(repo), "branch", "-D", branch_name], check=False, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", branch_name, str(worktree_target)], check=True, capture_output=True)
    return str(worktree_target)
