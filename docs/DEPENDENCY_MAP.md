# Hydrameda Dependency Map

> Generated: 2026-11-07 (evening re-audit)  
> Method: import statements, `docker-compose.yml`, host venv `importlib.metadata`, and runtime path constants in source

---

## 1. Module import graph

```
langgraph_engine/graph.py
├── stdlib: argparse, ast, os, re, sys, time, subprocess, sqlite3, pathlib, typing
├── httpx                         # trust_env=False client for LiteLLM fallback
├── openai.OpenAI                 # LiteLLM client + direct Cerebras/Groq clients
├── langgraph.graph               # StateGraph, END
├── langgraph.checkpoint.sqlite   # SqliteSaver
├── dotenv.dotenv_values          # ~/hydrameda/.env
└── preprocessor                  # local sibling import
    ├── lancedb
    ├── fastembed.TextEmbedding
    ├── tree_sitter_language_pack.get_parser
    └── stdlib: os, subprocess, pathlib, typing

mcp_server/server.py
├── stdlib: os, re, sys, time, shutil, subprocess, pathlib
├── mcp.server.fastmcp.FastMCP    # live path on this host (mcp 2.3.0)
│   └── fallback: mcp.server.mcpserver, fastmcp, then no-op stub
└── preprocessor (sys.path insert of ~/hydrameda/langgraph_engine)

langgraph_engine/hermes_bridge.py
├── stdlib: asyncio, json, re, time, threading, subprocess, http.server, pathlib
├── edge_tts                      # optional, imported inside TTS helper
└── graph.py invoked as subprocess (not imported)

langgraph_engine/hydrameda_openai_gateway.py
├── stdlib: json, os, re, time, uuid, pathlib
├── fastapi, fastapi.responses
├── openai.OpenAI                 # direct providers only
├── uvicorn
└── dotenv.dotenv_values

evaluate_benchmark.py
├── stdlib: argparse, json, re, shutil, subprocess, time, pathlib
└── ~/.local/bin/hydrameda subprocess (not imported)

backlog_runner.sh → ~/.local/bin/hydrameda
hydrameda-voice → HTTP POST hermes_bridge :5679
hydrameda-merge → mcp_server/server.py CLI flags

langgraph_engine/fix_*.py
└── stdlib open() of hardcoded /home/daman/hydrameda/... (not imported by pipeline)
```

There is **no** `import litellm` in application code. LiteLLM is a Docker sidecar spoken to over OpenAI HTTP.

---

## 2. LangGraph runtime data flow

```
CLI / MCP / Hermes
        │
        ▼
graph.build_hydrameda_graph()
        │
        ├── SqliteSaver ──► ~/hydrameda/state.db
        │
        ▼
node_spec_researcher
        ├── ObsidianRAG.query ──► LanceDB ~/hydrameda/lancedb_data
        │                             └── FastEmbed cache ~/hydrameda/.fastembed_cache
        ├── extract_ast_skeleton(repo_path) ──► Tree-sitter python grammar
        ├── call_llm("hybrid-architect")
        │       1. https://api.cerebras.ai/v1
        │       2. https://api.groq.com/openai/v1
        │       3. http://127.0.0.1:4000/v1  (LiteLLM or custom gateway)
        ├── write {vault}/Specs/{task_id}.md
        └── create_isolated_worktree
                git -C {repo_path} worktree add -b hydrameda/{task_id}
                target: ~/hydrameda/worktrees/{task_id}
        ▼
node_code_builder
        ├── call_llm("hybrid-coder")  (same 1-2-3 routing)
        ├── extract_files_from_response
        ├── heal_module_imports
        └── write module.py / test_module.py into worktree (host FS)
        ▼
node_sandbox_verifier
        └── docker run hydrameda-verifier:latest
                volume: worktree:/app:ro
                tools inside image: ruff, mypy, bandit, pytest, pydantic
        ▼
node_memory_consolidator
        ├── git add + commit in worktree (PASSED only)
        ├── call_llm("hybrid-coder") summary
        ├── write {vault}/Lessons-Learned/{task_id}-report.md
        └── ObsidianRAG.sync_vault() overwrite
```

---

## 3. External services and ports

| Service | Address | Consumers | Auth / config |
|---|---|---|---|
| Cerebras | `https://api.cerebras.ai/v1` | `graph.call_llm`, gateway | `CEREBRAS_API_KEY` |
| Groq | `https://api.groq.com/openai/v1` | `graph.call_llm`, gateway | `GROQ_API_KEY` |
| LiteLLM container | host `4000` → container 4000 | `call_llm` fallback only | `LITELLM_MASTER_KEY` / compose `env_file: .env` |
| Custom OpenAI gateway | `0.0.0.0:4000` | Cursor/n8n if started instead of LiteLLM | key loaded, **not checked** |
| Hermes bridge | `127.0.0.1:5679` | `hydrameda-voice`, n8n HTTP node | none |
| n8n | host `5678` | browser / inbound webhooks | `N8N_SECURE_COOKIE=false` |
| Docker engine | local socket | verifier | image `hydrameda-verifier:latest` |
| Ollama (optional) | `11434` via `host.docker.internal` | only if `setup_stage1.sh` yaml were in use | not in committed yaml |
| Obsidian vault | `OBSIDIAN_VAULT_PATH` or `~/ObsidianVault` | spec write, lessons, RAG | filesystem |
| Edge-TTS cloud | Microsoft TTS via `edge_tts` | Hermes | none in code |

**Port 4000 conflict:** LiteLLM compose mapping and `uvicorn.run(..., port=4000)` cannot both bind.

---

## 4. Filesystem layout (runtime, not all git-tracked)

```
~/hydrameda/                          # assumed HYDRAMEDA_ROOT (not env-overridable in Python)
├── .env                              # gitignored secrets
├── .env.example
├── state.db                          # LangGraph SqliteSaver (gitignored)
├── langgraph_engine/
│   ├── graph.py                      # pipeline
│   ├── preprocessor.py               # RAG / AST / worktree
│   ├── hermes_bridge.py
│   ├── hydrameda_openai_gateway.py
│   └── fix_*.py                      # stale
├── mcp_server/server.py
├── litellm/litellm_config.yaml
├── Dockerfile.verifier
├── docker-compose.yml
├── evaluate_benchmark.py
├── backlog_runner.sh
├── backlog_tasks.txt
├── setup_stage1.sh
├── n8n_hydrameda_workflow.json
├── README.md
├── docs/                             # this audit set
├── benchmarks/
│   ├── benchmark_results.json
│   ├── combined_research_30.jsonl
│   ├── humaneval_full.jsonl
│   ├── securityeval_full.jsonl
│   ├── repobench_full.jsonl
│   └── mini_sec_humaneval.jsonl
├── exported_modules/                 # sample merge
├── worktrees/                        # gitignored; ~41 task dirs on this host
├── lancedb_data/                     # gitignored
├── .fastembed_cache/                 # gitignored
├── n8n_data/                         # gitignored
├── .venv/                            # gitignored
└── .python-3.12/                     # gitignored; pollutes AST if repo_path is hydrameda root

~/ObsidianVault/
├── Specs/{task_id}.md
└── Lessons-Learned/{task_id}-report.md

~/.local/bin/hydrameda
~/.local/bin/hydrameda-merge
~/.local/bin/hydrameda-voice

~/.hermes/voice_outbox/{task_id}.{mp3,json}
```

---

## 5. Python packages (this host’s `.venv`)

No lockfile exists. Versions below are **what is installed now**, not a declared requirement.

| Package | Installed version | Used by |
|---|---|---|
| langgraph | 1.2.18 | `graph.py` |
| langgraph-checkpoint-sqlite | 3.1.2 | `graph.py` |
| openai | 2.54.0 | `graph.py`, gateway |
| httpx | 0.28.1 | `graph.py` |
| python-dotenv | 1.2.4 | `graph.py`, gateway |
| lancedb | 0.41.0 | `preprocessor.py` |
| fastembed | 0.9.1 | `preprocessor.py` |
| tree-sitter-language-pack | 1.22.1 | `preprocessor.py` |
| fastapi | 0.162.0 | gateway |
| uvicorn | 0.54.0 | gateway |
| edge-tts | 7.3.1 | Hermes |
| mcp | 2.3.0 | MCP server |
| fastmcp | not installed | import tried, unused if `mcp.server.fastmcp` works |

Verifier image (pip, unpinned in Dockerfile): `pytest`, `ruff`, `mypy`, `bandit`, `pydantic`.

---

## 6. Path coupling (`~/hydrameda`)

| File | Constant / default |
|---|---|
| `langgraph_engine/graph.py` | dotenv `Path.home()/"hydrameda"/".env"`; `state.db`; `--repo` default |
| `langgraph_engine/preprocessor.py` | `lancedb_data`, `worktrees`, `.fastembed_cache` |
| `mcp_server/server.py` | `HYDRAMEDA_ROOT`, engine dir, venv python, graph script |
| `langgraph_engine/hermes_bridge.py` | root, venv python, graph script |
| `langgraph_engine/hydrameda_openai_gateway.py` | root / `.env` |
| `evaluate_benchmark.py` | root, `~/.local/bin/hydrameda` |
| `setup_stage1.sh` | `cd ~/hydrameda` |
| `docker-compose.yml` | default vault `/home/daman/ObsidianVault` |
| `n8n_hydrameda_workflow.json` | `"repo_path": "/home/daman/hydrameda"` |
| `fix_*.py` | absolute `/home/daman/hydrameda/...` |
| Host CLI wrappers | absolute `/home/daman/hydrameda/...` |

Overridable: `OBSIDIAN_VAULT_PATH` only (plus `--repo` for the target git repo, which still parks worktrees under `~/hydrameda/worktrees`).

---

## 7. Who calls whom (control plane)

```
Cursor IDE
  ├── MCP stdio ──► mcp_server/server.py tools
  └── optional HTTP ──► gateway :4000  (if running)

User shell
  ├── hydrameda ──► graph.py
  ├── hydrameda-merge ──► mcp_server CLI
  ├── hydrameda-voice ──► Hermes :5679
  └── evaluate_benchmark.py / backlog_runner.sh ──► hydrameda

n8n :5678 webhook ──► Hermes :5679 ──► graph.py subprocess
```
