# Hydrameda Implementation Roadmap

> Generated: 2026-11-07 (evening re-audit)  
> Constraint: preserve the existing four-node LangGraph architecture; do not rewrite the project; do not delete working functionality  
> Earlier same-day roadmap listed Docker flags and Node 4 LLM summary as P0/P1 — those are **already in `graph.py`**. This plan starts from remaining gaps.

Priority: P0 security/correctness that can still bite; P1 user-visible control-plane gaps; P2 quality of generation and memory; P3 hygiene.

---

## P0 — Security and trust-boundary leftovers

### P0-1: Confine generated file paths to the worktree

**File:** `/home/daman/hydrameda/langgraph_engine/graph.py` (`node_code_builder`, ~277–282)

**Current:** `f_path = wt / fname` then `mkdir` + `write_text`. LLM-controlled `fname` is not checked with `Path.resolve()` against `wt.resolve()`.

**Recommended:** After join, require `f_path.resolve().is_relative_to(wt.resolve())` (or equivalent prefix check). Reject `..`, absolute names, and non-`.py` unless a later multi-file design explicitly allows a whitelist. Do not change the two-file happy path.

### P0-2: Finish sandbox policy without breaking `/tmp/work`

**Files:** `/home/daman/hydrameda/langgraph_engine/graph.py` (docker `cmd`), `/home/daman/hydrameda/Dockerfile.verifier`

**Current:** cap-drop, read-only, no-new-privileges, memory 512m, cpus 1.0, tmpfs `/tmp:exec`, net none, volume `:ro`, image USER 10001. Remaining: no seccomp profile, no pids limit, Bandit only on `module.py`, `--tmpfs ...exec` is an executable scratch FS.

**Recommended (additive):**

- Add `--pids-limit` (e.g. 256).
- Pass `--user 10001` explicitly at runtime.
- Point Bandit at `/tmp/work` (or at least both `module.py` and `test_module.py`), keeping existing `-s` suppressions unless a later security pass revisits them.
- Optionally add a default Docker seccomp profile (`--security-opt seccomp=...`) after verifying ruff/mypy/pytest still run.

Keep the existing tmpfs+copy pattern; it is what makes `--read-only` viable.

### P0-3: Gateway bind address and auth

**File:** `/home/daman/hydrameda/langgraph_engine/hydrameda_openai_gateway.py`

**Current:** `uvicorn.run(app, host="0.0.0.0", port=4000)`. `LITELLM_MASTER_KEY` is loaded and unused on `/v1/chat/completions`.

**Recommended:** Bind `127.0.0.1` by default; require `Authorization: Bearer {LITELLM_MASTER_KEY}` on chat completions. Do not remove streaming or Cerebras→Groq failover.

### P0-4: MCP merge is an unsupervised write

**File:** `/home/daman/hydrameda/mcp_server/server.py` (`merge_verified_worktree`)

**Current:** Copies every `*.py` from the worktree into caller-supplied `target_dir` and rewrites imports. No confirmation, no “PASSED-only” check.

**Recommended:** Require the worktree’s latest commit message to contain `verified autonomous build` (same signal the benchmark already uses), refuse overwrite unless a `force` flag is added later, and document that IDE agents must treat this as destructive. Human approval for graph-side git commit is P1-1; this is the copy-side equivalent.

---

## P1 — Control plane: approval, routing honesty, portability

### P1-1: Human checkpoint before Git commit

**File:** `/home/daman/hydrameda/langgraph_engine/graph.py` (`build_hydrameda_graph`, `__main__`)

**Current:** `workflow.compile(checkpointer=memory)` with no interrupt. Node 4 always commits on PASSED.

**Recommended:** `interrupt_before=["memory_consolidator"]` for interactive CLI. Add `--auto-approve` (default on for `evaluate_benchmark.py` / `backlog_runner.sh`) so benchmarks keep working. On interrupt, print worktree path and wait for resume via second `invoke(None, config)`.

Do not remove auto-commit itself; gate it.

### P1-2: Single owner for port 4000

**Files:** `/home/daman/hydrameda/docker-compose.yml`, `/home/daman/hydrameda/langgraph_engine/hydrameda_openai_gateway.py`, `/home/daman/hydrameda/langgraph_engine/graph.py` (`LITELLM_URL`)

**Current:** LiteLLM compose and the custom gateway both claim 4000. Graph fallback always hits `127.0.0.1:4000`. Gateway does not understand `hybrid-architect` / `hybrid-coder` names the same way LiteLLM does.

**Recommended (preserve both tools):**

- Keep LiteLLM on 4000 as the named-model proxy.
- Move gateway default to `127.0.0.1:4001`.
- Make `LITELLM_URL` env-overridable in `graph.py`.
- Document: IDE compression gateway is optional; pipeline fallback is LiteLLM.

### P1-3: Honor `model_group` on the direct path or stop passing it

**File:** `/home/daman/hydrameda/langgraph_engine/graph.py` (`call_llm`)

**Current:** Direct calls ignore `hybrid-architect` vs `hybrid-coder`. LiteLLM yaml’s per-group shuffle never runs unless both APIs fail.

**Recommended:** Keep direct-first (it is the documented reliability choice). Either (a) pass group-specific model ids if they ever diverge, or (b) comment in yaml + README that groups are LiteLLM-only fallback labels. Adding `hybrid-fast` without wiring it is not useful until Node 4 or a new node actually needs a smaller model.

### P1-4: Portable `HYDRAMEDA_ROOT`

**Files:** `preprocessor.py`, `graph.py`, `mcp_server/server.py`, `hermes_bridge.py`, `hydrameda_openai_gateway.py`, `evaluate_benchmark.py`, `setup_stage1.sh`, `n8n_hydrameda_workflow.json`

**Current:** `Path.home() / "hydrameda"` (and some `/home/daman/...`). Cloning to another directory breaks LanceDB, worktrees, dotenv, and MCP.

**Recommended:** `HYDRAMEDA_ROOT = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda"))` in one module (preprocessor) and import it. Change n8n JSON `repo_path` to an expression or env. Check host wrappers into `scripts/` that use `"$(dirname ...)"` instead of `/home/daman`. Do not require moving existing data; default can stay `~/hydrameda`.

### P1-5: Pipeline exit status

**Files:** `langgraph_engine/graph.py` `__main__`, `hermes_bridge.py`, `backlog_runner.sh`

**Current:** Always process exit 0. Hermes greps `'status: PASSED'`. Backlog `if hydrameda` is not a pass detector.

**Recommended:** `sys.exit(0 if final_state["status"] == "PASSED" else 1)` while **keeping** the existing print line so current parsers still work. Then Hermes can use returncode with substring as fallback.

### P1-6: MCP import failure must not be silent

**File:** `/home/daman/hydrameda/mcp_server/server.py` lines 14–26

**Current:** Stub `run()` swallows missing MCP libs.

**Recommended:** `sys.exit` with install hint if all three imports fail. Keep the three-import chain; `fastmcp` is not installed here and is not required while `mcp.server.fastmcp` works.

### P1-7: `requirements.txt` (or `pyproject.toml`)

**Missing file** at repo root.

**Recommended:** Freeze or pin major versions of the packages listed in `docs/DEPENDENCY_MAP.md` from the current venv. Do not invent versions. This is reproducibility, not a rewrite.

---

## P2 — Generation, RAG, and AST quality

### P2-1: Two-file generation ceiling

**File:** `/home/daman/hydrameda/langgraph_engine/graph.py` (`node_spec_researcher` + `node_code_builder` prompts), Bandit path in verifier

**Current:** Spec and coder prompts mandate `module.py` / `test_module.py`. Healer and Bandit assume those names. RepoBench-style packages cannot be expressed.

**Recommended (incremental, keep default):**

- Keep two-file as the default prompt (HumanEval/SecurityEval still fit).
- Allow extra `<file name="...">` when the spec lists additional Python modules.
- Extend `heal_module_imports` to heal `from {sibling} import` for any generated `.py` stem, not only `module`.
- Run Bandit on all generated `.py` except tests if suppressions require it.

Do not jump to a general patch engine in this phase.

### P2-2: Ignore `.python-3.12` (and similar) in AST maps

**File:** `/home/daman/hydrameda/langgraph_engine/preprocessor.py` `IGNORE_DIRS`

**Current:** MCP `get_repo_ast_map(/home/daman/hydrameda)` returns CPython stdlib skeletons first; the 5000/8000 char budget is wasted.

**Recommended:** Add `.python-3.12`, `site-packages`, and other interpreter trees to `IGNORE_DIRS`. Optionally skip any dir whose name starts with `.` except the vault. This is the highest-leverage RAG/AST fix.

### P2-3: LanceDB overwrite → upsert

**File:** `/home/daman/hydrameda/langgraph_engine/preprocessor.py` `sync_vault` (`mode="overwrite"`)

**Current:** Every PASSED/FAILED consolidation rebuilds the whole table.

**Recommended:** Create table if missing; delete+add by `path` or `tbl.add` for new/changed hashes. Keep FastEmbed model unchanged. MCP `query_obsidian_memory` docstring should say “vector search” until a real hybrid retriever exists.

### P2-4: Spec vs SecurityEval third-party stack

**Files:** `graph.py` stdlib-only instructions vs `benchmarks/combined_research_30.jsonl` Flask/yaml tasks

**Current:** Pass@1 on SecurityEval in the recorded JSON is 0/10; self-heal recovered 8/10. The stdlib-only rule fights those prompts.

**Recommended:** Condition the stdlib-only clause on the spec/prompt (allow Flask/PyYAML when the user prompt names them). Keep Bandit+mypy+ruff as the security backstop. This is a prompt change, not a new node.

### P2-5: Hermes non-blocking wait

**File:** `/home/daman/hydrameda/langgraph_engine/hermes_bridge.py`

**Current:** `subprocess.run` inside a daemon thread.

**Recommended:** `Popen` + `communicate` is enough; or document single-flight. Prefer Popen so `/health` remains responsive (it already is; only worker threads block). Optional job queue later.

### P2-6: Stop `setup_stage1.sh` from clobbering live config

**File:** `/home/daman/hydrameda/setup_stage1.sh`

**Current:** Overwrites `litellm_config.yaml`, `Dockerfile.verifier`, `docker-compose.yml` with an older Gemini/Qwen/Ollama template.

**Recommended:** Write files only if absent, or target `*.template`. Leave committed yaml as source of truth.

### P2-7: Archive `fix_*.py`

**Files:** `/home/daman/hydrameda/langgraph_engine/fix_*.py`

**Recommended:** `git mv` to `langgraph_engine/archive/` with a one-line README. They are not part of the graph. Do not execute them.

---

## P3 — Hygiene

| ID | Item | Files |
|---|---|---|
| P3-1 | Compose healthcheck for LiteLLM | `docker-compose.yml` |
| P3-2 | Align Pass@3 wording in `evaluate_benchmark.py` with “self-healed ≤3 retries” | `evaluate_benchmark.py` |
| P3-3 | `list_verified_worktrees` should filter to commits whose message contains `verified autonomous build` | `mcp_server/server.py` |
| P3-4 | Worktree prune older than N days | new small script or flag on evaluate |
| P3-5 | Optional `conftest.py` only if tests fail on path; benchmark already injects fixture warning | `graph.py` — low priority |
| P3-6 | Check `scripts/hydrameda{,-merge,-voice}` into git | currently host-only under `~/.local/bin` |

---

## Prioritized implementation order

Do these in order. Each step is additive.

**Phase 1 — close remaining unsafe writes (short diffs)**

1. P0-1 generated path confinement  
2. P0-2 pids-limit, explicit `--user`, Bandit on more than `module.py`  
3. P0-3 gateway localhost + bearer check  
4. P2-2 AST `IGNORE_DIRS` (fixes MCP/Node 1 context immediately)  
5. P1-5 `sys.exit` on status  
6. P1-6 MCP import fatal  

**Phase 2 — operator control**

7. P1-1 interrupt + `--auto-approve`  
8. P0-4 merge only verified worktrees  
9. P1-2 port split + `LITELLM_URL`  
10. P1-4 `HYDRAMEDA_ROOT` env  
11. P1-7 dependency manifest  
12. P1-3 document or thread `model_group` through direct calls  

**Phase 3 — generation and memory**

13. P2-4 drop stdlib-only when the prompt requires Flask/yaml  
14. P2-1 allow extra `.py` files without abandoning the default two-file protocol  
15. P2-3 LanceDB upsert  
16. P2-5 / P2-6 / P2-7 Hermes Popen, setup script safety, archive fix scripts  

**Phase 4 — ops polish**

17. P3-1…P3-6 as time allows  

---

## Explicitly out of scope (do not do in the next pass)

- Rewriting LangGraph into another orchestrator  
- Replacing Tree-sitter, LanceDB, or the Docker verifier  
- Deleting the custom gateway or LiteLLM (split ports instead)  
- Claiming new Pass@1 / Pass@3 numbers without a new `evaluate_benchmark.py` run  
- Making Node 2 emit arbitrary patch diffs before extra-file support exists  
- Removing auto-commit; gate it instead  

---

## Suggested first PR slice

One PR: P0-1 + P2-2 + P1-5 + P1-6. No behavior change on the happy path of `module.py`/`test_module.py`, but AST maps stop ingesting `.python-3.12`, generated names cannot leave the worktree, and downstream scripts can trust process exit codes.
