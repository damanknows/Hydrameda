# Hydrameda: Neuro-Symbolic Autonomous Coding & Security Verification Pipeline

![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)
![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-orange.svg)
![FastMCP](https://img.shields.io/badge/IDE_Bridge-FastMCP_2.0-green.svg)
![Security Gate](https://img.shields.io/badge/SAST-Bandit_%2B_Mypy_%2B_Ruff-red.svg)

> **Abstract:** Current Large Language Model (LLM) software agents frequently fail in autonomous multi-file code generation due to syntax drift, hallucinated symbol imports, and undetected security vulnerabilities. **Hydrameda** introduces a cyclic neuro-symbolic state machine that combines epistemic Retrieval-Augmented Generation (LanceDB + FastEmbed over an Obsidian Vault), structural Tree-sitter AST repository mapping, deterministic AST import healing, and a rootless Docker verification sandbox (`Ruff` + `Mypy` + `Bandit SAST` + `Pytest`).

## 5-Stage System Architecture

1. **Node 1 (Epistemic Architect):** Queries Obsidian Vault via LanceDB hybrid search (`BAAI/bge-small-en-v1.5`) and extracts compressed Tree-sitter AST skeletons.
2. **Node 2 (Autonomous Coder & AST Healer):** Generates implementation and unit test suites, then runs a deterministic Python `ast` symbol healer to resolve missing stdlib and inter-module imports.
3. **Node 3 (Rootless Docker Sandbox Verifier):** Executes `ruff check`, `mypy`, `bandit -r`, and `pytest` inside a network-isolated container (`--network none --cap-drop ALL --read-only`). Routes tracebacks back to Node 2 for up to 3 self-healing retries.
4. **Node 4 (Memory Consolidator & Stage 4 FastMCP):** Commits verified code to an isolated Git worktree branch, syncs lessons learned to Obsidian, and exposes 7 MCP tools to Cursor and Google Antigravity.
5. **Stage 5 (Hermes & n8n Voice Bridge):** Asynchronous webhook server (Port 5679) that dispatches builds and synthesizes spoken `.mp3` status reports via Edge-TTS.

## Empirical Evaluation (Zero-Shot Pass@1 vs. Neuro-Symbolic Self-Healing)

| Benchmark Suite | Tasks | Zero-Shot (`Pass@1`) | Self-Healed (`Pass@3`) | Net Lift (`Δ`) |
|---|---|---|---|---|
| **OpenAI HumanEval** | 10 | 9/10 (90.0%) | **10/10 (100.0%)** | +10.0% |
| **SecurityEval (MITRE CWE)** | 10 | 0/10 (0.0%) | **8/10 (80.0%)** | **+80.0%** |
| **RepoBench (Architecture)** | 10 | 2/10 (20.0%) | **8/10 (80.0%)** | **+60.0%** |
| **Total / Overall** | **30** | **11/30 (36.7%)** | **26/30 (86.7%)** | **+50.0%** |

## Quick Start CLI Commands

```bash
# Run an autonomous verified build
hydrameda --prompt 'Create a constant-time HMAC-SHA256 webhook verifier with pytest unit tests'

# List, inspect, or merge verified Git worktrees (Stage 4)
hydrameda-merge --list
hydrameda-merge <task-id> ./exported_modules module_name

# Trigger async webhook build with Edge-TTS voice report (Stage 5)
hydrameda-voice 'Create a sliding-window rate limiter with pytest unit tests'

# Run the multi-suite benchmark harness
~/hydrameda/.venv/bin/python ~/hydrameda/evaluate_benchmark.py --suite mini
```
