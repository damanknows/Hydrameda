# Hydrameda Architecture Audit

> Generated: 2026-11-07 (evening re-audit against live source)  
> Scope: read-only inspection of `/home/daman/hydrameda`  
> Constraint: no source modifications, no invented status, no fabricated benchmarks

This document supersedes the earlier same-day audit in this folder. Several findings in that draft are **no longer true**: `node_sandbox_verifier` now passes `--cap-drop ALL --read-only --security-opt no-new-privileges` plus memory/CPU/tmpfs limits; `call_llm()`’s docstring matches direct-first routing; `node_memory_consolidator` now calls `call_llm("hybrid-coder", ...)`.

---

## 1. System Overview

Hydrameda is a four-node LangGraph state machine plus two sidecar processes (FastMCP IDE bridge, Hermes HTTP/voice bridge). It generates a two-file Python artifact (`module.py` + `test_module.py`), verifies it in a Docker sandbox, retries on failure, then auto-commits the isolated Git worktree and writes an Obsidian lesson note.

```
User prompt (CLI hydrameda | MCP trigger_autonomous_build | Hermes POST)
        │
        ▼
 spec_researcher  ──►  code_builder  ──►  sandbox_verifier
 (Node 1)                (Node 2)              (Node 3)
   RAG + AST + spec         LLM + import healer     Docker ruff/mypy/bandit/pytest
        │                       ▲                    │
        │                       │ FAILED             │
        │                       └── retry_count < 4 ─┤
        │                                            │ PASSED or retry_count >= 4
        │                                            ▼
        │                                   memory_consolidator (Node 4)
        │                                   git commit + LLM summary + LanceDB overwrite
        ▼
       END
        │
        ├── MCP merge_verified_worktree  (optional copy into a target dir)
        └── Hermes Edge-TTS outbox       (if started via port 5679)
```

**LangGraph compile:** `StateGraph(HydramedaState)` with `SqliteSaver` on `~/hydrameda/state.db`. No `interrupt_before` / `interrupt_after`. Entry node: `spec_researcher`.

---

## 2. Component Audit

### 2.1 `langgraph_engine/graph.py` — Core pipeline

**Path:** `/home/daman/hydrameda/langgraph_engine/graph.py`  
**Status:** Implemented and syntactically complete. This is the only LangGraph definition in the repo.

#### Graph topology (actual)

| Graph node id | Python function | LLM? | Edges |
|---|---|---|---|
| `spec_researcher` | `node_spec_researcher` | `call_llm("hybrid-architect", ..., max_tokens=4096)` | → `code_builder` |
| `code_builder` | `node_code_builder` | `call_llm("hybrid-coder", ..., max_tokens=8192)` | → `sandbox_verifier` |
| `sandbox_verifier` | `node_sandbox_verifier` | none | conditional `decide_next_step` |
| `memory_consolidator` | `node_memory_consolidator` | `call_llm("hybrid-coder", ..., max_tokens=512)` | → `END` |

`decide_next_step()`:

- `status == "PASSED"` → `"consolidate"`
- `retry_count >= 4` → `"consolidate"` (prints “Max retries (4) reached”)
- else → `"rebuild"` → `code_builder`

**Retry semantics (verified by tracing increment):** `retry_count` starts at `0`. Each failed verifier run does `retry_count += 1`. After the 4th failure `retry_count == 4` and the graph stops rebuilding. That is **4 generation/verify cycles** (1 initial + **3 self-healing retries**). README line 14 (“up to 3 self-healing retries”) matches this. Failed rows in `benchmarks/benchmark_results.json` record `"retries": 3`, which matches.

Prompt text `Attempt {retry_count}/4` on the rebuild path is a **labeling quirk** (first rebuild shows `1/4`), not a fifth attempt.

#### State schema (`HydramedaState`)

Fields in source: `task_id`, `user_prompt`, `repo_path`, `vault_context`, `ast_skeleton`, `spec_md`, `worktree_path`, `target_files`, `current_files`, `pytest_stderr`, `retry_count`, `status`. No human-approval or interrupt flag exists in state.

#### `call_llm()` — routing (confirmed LiteLLM bypass)

**Path:** `langgraph_engine/graph.py` lines 29–85.

Order of attempts:

1. Direct `OpenAI(base_url="https://api.cerebras.ai/v1")` with `CEREBRAS_API_KEY` from `~/hydrameda/.env` merged with `os.environ`. Model id always `gpt-oss-120b`.
2. Direct `OpenAI(base_url="https://api.groq.com/openai/v1")` with `GROQ_API_KEY`. Model id always `openai/gpt-oss-120b`.
3. LiteLLM proxy `http://127.0.0.1:4000/v1` using the module-level `client`, **two** retries, passing `model=model_group` (`hybrid-architect` / `hybrid-coder`).

The module-level `OpenAI` client is constructed against LiteLLM, but it is unused until both direct providers fail. `model_group` is **ignored** on the direct path: architect and coder hit the same two upstream models.

Docstring now describes this order accurately (unlike the earlier audit). Bypass of LiteLLM for the primary path is still the production behavior.

Direct clients do not set `httpx.Client(trust_env=False)`; the LiteLLM client does. Proxy env vars are popped at import, so this is mostly consistent.

#### `node_spec_researcher`

- Instantiates `ObsidianRAG()`, `query(..., top_k=3)` (overrides preprocessor default `top_k=2`).
- `extract_ast_skeleton(state["repo_path"])` (5000-char cap).
- Writes spec to `{OBSIDIAN_VAULT_PATH or ~/ObsidianVault}/Specs/{task_id}.md`.
- Creates worktree via `create_isolated_worktree`.
- Spec prompt itself already locks the design to `module.py` + `test_module.py` and stdlib-only.

#### `node_code_builder` — generation limits (confirmed)

Prompt hard-instructs exactly two XML files:

```
<file name="module.py">...</file>
<file name="test_module.py">...</file>
```

`extract_files_from_response()` can parse **any** filename. Written files are whatever keys exist in `merged_files` (previous attempt ∪ new extract). In practice the prompt plus healer special-case `module.py` / `test_module.py`, so packages, `__init__.py`, and non-Python files are not a supported product path.

**Path-containment gap:** `wt / fname` is not resolved against the worktree root. A model-emitted `name="../escape.py"` (or similar) is not rejected.

Healer (`heal_module_imports`): regex split of `import a, b`; `base64.binascii` rewrite; stdlib auto-import from a fixed list; test-file `from .module` → `from module`; AST walk to inject missing `from module import …` symbols. Does not heal third-party imports. Does not parse type-only `typing.TYPE_CHECKING` blocks specially.

On empty extract, previous files are retained (merge), which is useful on retries.

#### `node_sandbox_verifier` — Docker flags (updated)

**Actual `docker run` (lines 313–327):**

```
docker run --rm
  --network none
  --cap-drop ALL
  --read-only
  --security-opt no-new-privileges
  --memory 512m
  --cpus 1.0
  --tmpfs /tmp:size=256m,exec
  -v {worktree}:/app:ro
  hydrameda-verifier:latest
  {verify_script}
```

README’s `--network none --cap-drop ALL --read-only` claim is **now true**. Remaining sandbox gaps:

- No custom seccomp profile (`--security-opt seccomp=...`).
- `--tmpfs /tmp:exec` is required for `cp`/`ruff --fix`/`pytest`; it is also an executable scratch area for untrusted code.
- No `--pids-limit`.
- Runtime does not pass `--user 10001`; it relies on `USER sandboxuser` in `Dockerfile.verifier`.
- Verify script copies `/app` → `/tmp/work`, then: `ruff check --fix` (errors discarded), fatal ruff `E9,F63,F7,F82` only, `mypy --ignore-missing-imports`, `bandit -q -r /tmp/work/module.py -s B101,B105,B106,B107` (skipped if `bandit` missing; **only `module.py`**, not tests), `pytest -v --tb=short`.
- Failure stderr truncated to last 60 lines before feeding Node 2.

#### `node_memory_consolidator`

On `status == "PASSED"`:

```
git -C {wt} add .
git -C {wt} commit -m "feat({task_id}): verified autonomous build"
```

No human gate. `check=False` on both git calls, so a commit failure is silent except that `rev-parse` may yield empty.

Then `call_llm("hybrid-coder", summary_prompt)` (implemented). On exception, a template string is used. Writes `{vault}/Lessons-Learned/{task_id}-report.md` unconditionally (including FAILED tasks). Calls `ObsidianRAG().sync_vault()` which **overwrites** the LanceDB table.

#### CLI `__main__`

- `--prompt` / positional prompt / default JWT prompt.
- `--repo` defaults to `Path.home() / "hydrameda"`.
- `--task-id` optional slug.
- `app.invoke(initial_state, config={"configurable": {"thread_id": task_id}})`.
- Prints `🏁 Hydrameda pipeline execution completed with status: {status}`.
- **No `sys.exit` based on status** (grep of `*.py` finds zero `sys.exit` in this repo). Process exit code is 0 unless an exception is raised. Hermes/backlog therefore cannot trust OS exit codes.

Host wrappers (not in git):

- `/home/daman/.local/bin/hydrameda` → `.venv/bin/python langgraph_engine/graph.py`
- `/home/daman/.local/bin/hydrameda-merge` → `mcp_server/server.py --list|--inspect|--merge`
- `/home/daman/.local/bin/hydrameda-voice` → POST `http://127.0.0.1:5679/webhook/hydrameda-trigger`

All three hardcode `/home/daman/hydrameda`.

---

### 2.2 `langgraph_engine/preprocessor.py` — RAG, AST, worktrees

**Path:** `/home/daman/hydrameda/langgraph_engine/preprocessor.py`  
**Status:** Implemented.

| Symbol | Behavior | Problems |
|---|---|---|
| `extract_ast_skeleton` | Tree-sitter Python parser via `tree_sitter_language_pack.get_parser("python")`. Top-level imports, class headers, nested method headers, module-level functions. Truncates to `max_chars` (5000 in graph, 8000 in MCP). | Only `*.py`. `IGNORE_DIRS` omits `.python-3.12`, so scanning `~/hydrameda` as `repo_path` dumps CPython stdlib into the first 5000 chars (observed via MCP `get_repo_ast_map`). Nested functions inside non-class scopes are skipped. |
| `ObsidianRAG.sync_vault` | Embeds vault `*.md` with FastEmbed `BAAI/bge-small-en-v1.5`, `lancedb.create_table(..., mode="overwrite")`. Seeds `Specs/hydrameda-architecture.md` if vault empty. | Full-table overwrite every consolidation. Not hybrid (no BM25/keyword). Paths: `LANCEDB_DIR = ~/hydrameda/lancedb_data`. |
| `ObsidianRAG.query` | Vector `tbl.search(q_vec).limit(top_k)`. Auto-sync if table missing. Default `top_k=2`. | MCP docstring says “hybrid search”; implementation is dense cosine only. |
| `create_isolated_worktree` | `git worktree prune`; force-remove existing dir; `branch -D hydrameda/{task_id}`; `worktree add -b`. Auto `git init` + empty commit if repo has no `.git`. Worktree path is always `~/hydrameda/worktrees/{task_id}` even if `--repo` is elsewhere. | Isolation is real (separate branch + worktree). Destructive to an existing same-named branch. `git config user.email/name` is set inside a newly inited repo (local, not global). |

Hardcoded `Path.home() / "hydrameda"` for LanceDB, worktrees, FastEmbed cache. Vault path is overridable via `OBSIDIAN_VAULT_PATH`.

---

### 2.3 `langgraph_engine/hydrameda_openai_gateway.py` — custom port 4000 gateway

**Path:** `/home/daman/hydrameda/langgraph_engine/hydrameda_openai_gateway.py`  
**Status:** Implemented (FastAPI + uvicorn).

- Binds `0.0.0.0:4000` (all interfaces).
- Compresses messages, injects an epistemic system prompt.
- Routes **directly** Cerebras → Groq. Never calls LiteLLM.
- `/health` and `/v1/models` share one handler.
- Auth: `LITELLM_MASTER_KEY` is loaded but **not checked** on `/v1/chat/completions`.

**Port conflict:** `docker-compose.yml` also publishes LiteLLM on host port 4000. Only one of these can own the port. Undocumented which is intended for daily use. `graph.py` LiteLLM fallback expects an OpenAI-compatible server at `127.0.0.1:4000`; either process can satisfy that if it implements `/v1/chat/completions`, but LiteLLM understands `hybrid-architect` / `hybrid-coder` model names while this gateway remaps every request to `gpt-oss-120b`.

---

### 2.4 `langgraph_engine/hermes_bridge.py` — Stage 5 webhook / voice

**Path:** `/home/daman/hydrameda/langgraph_engine/hermes_bridge.py`  
**Status:** Implemented. `HTTPServer` on `127.0.0.1:5679`.

- POST `/webhook/hydrameda-trigger` and `/execute`: parse JSON, start daemon thread, return immediately.
- Thread runs `subprocess.run([venv python, graph.py, ...])` **blocking** that thread for the full pipeline.
- Status: `'PASSED' if 'status: PASSED' in proc.stdout else 'FAILED'`. The graph print `... with status: PASSED` contains that substring, so it works, but there is no process exit-code contract.
- Edge-TTS `en-GB-RyanNeural` → `~/.hermes/voice_outbox/{task_id}.mp3` + JSON manifest.
- GET `/health`, `/outbox`.
- `repo_path` defaults to `~/hydrameda`. n8n workflow hardcodes `/home/daman/hydrameda`.

---

### 2.5 `mcp_server/server.py` — FastMCP IDE bridge

**Path:** `/home/daman/hydrameda/mcp_server/server.py`  
**Status:** Implemented. Seven tools. Live Cursor namespace `user-hydrameda` matches these names.

| Tool | Current behavior | Problems |
|---|---|---|
| `get_active_specs` | Lists `{vault}/Specs/*.md` by mtime, 260-char preview | OK |
| `query_obsidian_memory` | `ObsidianRAG.query` | Docstring claims hybrid search; vector-only |
| `get_repo_ast_map` | `extract_ast_skeleton(..., 8000)` | Polluted by `.python-3.12` when `repo_path` is hydrameda root |
| `list_verified_worktrees` | Lists `~/hydrameda/worktrees/*` dirs with git branch/log and `*.py` names | Lists **all** worktrees, not only PASSED ones |
| `inspect_worktree_diff` | git log -2 + full `*.py` contents | Name says “diff”; returns full files, not `git diff` |
| `trigger_autonomous_build` | `Popen` graph.py, log at `worktrees/{tid}.build.log` | Fire-and-forget; no completion callback |
| `merge_verified_worktree` | `shutil.copy2` all `*.py` into `target_dir`; optional rename + import rewrite | **No approval**. Overwrites destination. `target_dir` is caller-controlled |

Import fallback: `mcp.server.mcpserver` → `fastmcp` → `mcp.server.fastmcp` → stub whose `run()` is `pass`. Host venv has `mcp 2.3.0` and **not** `fastmcp`; the third import is what the live IDE tools depend on. If it failed, the server would start and expose nothing with no error.

CLI flags `--list/--inspect/--merge/--sse` used by `hydrameda-merge`.

---

### 2.6 `langgraph_engine/fix_*.py` — stale one-shot patches

**Paths:**

- `/home/daman/hydrameda/langgraph_engine/fix_clean.py`
- `/home/daman/hydrameda/langgraph_engine/fix_coder_prompt.py`
- `/home/daman/hydrameda/langgraph_engine/fix_import_instruction.py`
- `/home/daman/hydrameda/langgraph_engine/fix_imports.py`
- `/home/daman/hydrameda/langgraph_engine/fix_init.py`
- `/home/daman/hydrameda/langgraph_engine/fix_tokens.py`
- `/home/daman/hydrameda/langgraph_engine/fix_worktree.py`

Each opens `/home/daman/hydrameda/langgraph_engine/{graph,preprocessor}.py` and writes a string replace. The intended patches are already in the live files. Re-running them would be harmful. They are duplicated development debris, not pipeline modules.

---

### 2.7 `Dockerfile.verifier` and `docker-compose.yml`

**Paths:** `/home/daman/hydrameda/Dockerfile.verifier`, `/home/daman/hydrameda/docker-compose.yml`

Image: `python:3.12-slim-bookworm`, pip installs `pytest ruff mypy bandit pydantic`, `useradd -m -u 10001 sandboxuser`, `USER sandboxuser`, `ENTRYPOINT ["sh", "-c"]`. No HEALTHCHECK, no seccomp, no dropped capabilities in the image itself (those are runtime flags in `graph.py`).

Compose services:

- `hydrameda-litellm` → `ghcr.io/berriai/litellm:main-latest`, port 4000, config bind-mount, `extra_hosts host.docker.internal`.
- `hydrameda-n8n` → port 5678, `N8N_SECURE_COOKIE=false`, vault mount default `/home/daman/ObsidianVault`. No healthcheck.

---

### 2.8 `litellm/litellm_config.yaml`

**Path:** `/home/daman/hydrameda/litellm/litellm_config.yaml`

Groups: `hybrid-architect`, `hybrid-coder`, `default`. Each is Cerebras `gpt-oss-120b` + Groq `openai/gpt-oss-120b` with `simple-shuffle`, `num_retries: 3`, `cooldown_time: 5`. **No `hybrid-fast`.**

`setup_stage1.sh` would **overwrite** this file with an older Gemini / Groq Qwen / Ollama `hybrid-fast` template if re-run.

---

### 2.9 Benchmark harness and recorded results

**Path:** `/home/daman/hydrameda/evaluate_benchmark.py`  
**Recorded run:** `/home/daman/hydrameda/benchmarks/benchmark_results.json` dated `2026-11-04 10:21:02`.

Harness invokes host `~/.local/bin/hydrameda`, parses stdout `status: PASSED` or a git commit containing `verified autonomous build`, derives retries from `Attempt N` regex. Pass@1 = passed and retries == 0. Reports are labeled Pass@3.

**Do not treat this as a new experiment.** The JSON is an existing artifact. README table matches that JSON exactly:

| Suite | Tasks | Pass@1 | Self-healed | Δ |
|---|---|---|---|---|
| HumanEval | 10 | 9/10 (90.0%) | 10/10 (100.0%) | +10.0% |
| SecurityEval | 10 | 0/10 (0.0%) | 8/10 (80.0%) | +80.0% |
| RepoBench | 10 | 2/10 (20.0%) | 8/10 (80.0%) | +60.0% |
| Overall | 30 | 11/30 (36.7%) | 26/30 (86.7%) | +50.0% |

Average latency in JSON: 44.46s/task. This audit did not re-run the suite.

Suite files present: `combined_research_30.jsonl` (30 lines), `humaneval_full.jsonl` (164), `securityeval_full.jsonl` (121), `repobench_full.jsonl` (10), `mini_sec_humaneval.jsonl` (5).

SecurityEval prompts ask for Flask/yaml/etc. while Node 2 still instructs stdlib-only — a structural reason those tasks start at Pass@1 = 0.

---

### 2.10 Other repo files

| Path | Role |
|---|---|
| `/home/daman/hydrameda/setup_stage1.sh` | Destructive bootstrap: writes `.env` template, **overwrites** LiteLLM yaml and compose, builds verifier image, `docker compose up`. Assumes `~/hydrameda`. |
| `/home/daman/hydrameda/backlog_runner.sh` | Sequential `hydrameda` over `backlog_tasks.txt`; treats any git log in the worktree as pass. Exit code of graph is always 0, so the `if hydrameda` branch is not a real pass/fail gate. |
| `/home/daman/hydrameda/backlog_tasks.txt` | Four queued prompts. |
| `/home/daman/hydrameda/n8n_hydrameda_workflow.json` | Two n8n nodes: webhook → HTTP POST Hermes. Hardcoded repo path `/home/daman/hydrameda`. |
| `/home/daman/hydrameda/exported_modules/jwt_validator.py` and `test_jwt_validator.py` | Example merge output (JWT validator). |
| `/home/daman/hydrameda/.env.example` | `CEREBRAS_API_KEY`, `GROQ_API_KEY`, `LITELLM_MASTER_KEY`, `OBSIDIAN_VAULT_PATH`. |
| `/home/daman/hydrameda/README.md` | Architecture narrative + CLI + benchmark table. |
| **Missing from repo** | `requirements.txt`, `pyproject.toml`, CLI wrapper scripts, tests for Hydrameda itself. |

Runtime packages in `~/hydrameda/.venv` (versions from `importlib.metadata`, not a lockfile): langgraph 1.2.18, langgraph-checkpoint-sqlite 3.1.2, openai 2.54.0, httpx 0.28.1, python-dotenv 1.2.4, lancedb 0.41.0, fastembed 0.9.1, tree-sitter-language-pack 1.22.1, fastapi 0.162.0, uvicorn 0.54.0, edge-tts 7.3.1, mcp 2.3.0. `fastmcp` not installed.

~41 git worktrees exist under `worktrees/` (gitignored). No TTL prune in code.

---

## 3. Known-issue verification (user list vs source)

| Observation | Verdict against current source |
|---|---|
| `node_code_builder` limited to `module.py` + `test_module.py` | **Confirmed.** Prompt + healer + Bandit path. Parser itself is generic. |
| Some model calls bypass LiteLLM | **Confirmed.** Direct Cerebras/Groq first in `call_llm()`; entire custom gateway bypasses LiteLLM. |
| Human approval checkpoints missing | **Confirmed.** No LangGraph interrupt; consolidator and MCP merge are automatic. |
| Consolidation may commit or copy files automatically | **Confirmed.** `git add/commit` in Node 4; `shutil.copy2` in `merge_verified_worktree`. |
| Sandbox security needs stronger enforcement | **Partially stale.** Runtime now has cap-drop, read-only root FS, no-new-privileges, memory/CPU, tmpfs, net none, ro volume, non-root USER. Still missing seccomp, pids limit, Bandit-on-all-files, path sanitization of generated names, gateway bind/auth. |
| Retry count README vs graph | **Aligned, not a bug.** README “3 retries” = 4 total attempts; `retry_count >= 4` after 4 failures; benchmark failures show retries=3. Pass@3 is a label for “self-healed within that budget,” not independent Pass@k sampling. |
| Modules assume `~/hydrameda` | **Confirmed** across preprocessor, graph dotenv/state.db, MCP, Hermes, evaluate_benchmark, setup_stage1, n8n JSON, and host CLI wrappers. Vault is the only major path with an env override. |

---

## 4. Duplicated / competing / broken pieces

**Duplicated LLM routing:** `graph.call_llm` and `hydrameda_openai_gateway.get_providers` both implement Cerebras→Groq failover outside LiteLLM.

**Competing port 4000:** LiteLLM container vs custom gateway.

**Duplicated / stale config:** committed `litellm_config.yaml` vs `setup_stage1.sh` embedded YAML (different models and routing strategy).

**Duplicated worktree listing:** MCP tools vs `hydrameda-merge` vs `list_verified_worktrees` (same functions).

**Broken if re-run:** `fix_*.py` and `setup_stage1.sh` overwrite live sources/config.

**Silent-broken modes:** MCP stub `run()`; Hermes status substring; backlog_runner treating graph exit 0 as success; Bandit `command -v` skip; AST map swallowed by `.python-3.12`.

---

## 5. Execution path (one prompt)

1. `hydrameda --prompt …` → `graph.py` argparse → `build_hydrameda_graph()` → SqliteSaver `~/hydrameda/state.db`.
2. `spec_researcher`: LanceDB query + Tree-sitter map + direct LLM spec + write Obsidian Specs + `git worktree add -b hydrameda/{task_id} ~/hydrameda/worktrees/{task_id}`.
3. `code_builder`: direct LLM → XML extract → heal imports → write files into worktree (host filesystem, not yet docker).
4. `sandbox_verifier`: docker run hardened flags, copy to `/tmp/work`, ruff/mypy/bandit/pytest.
5. Loop to 3 until pass or `retry_count >= 4`.
6. `memory_consolidator`: optional git commit in worktree, LLM paragraph, lesson markdown, LanceDB overwrite.
7. Print status line; process exits 0.
