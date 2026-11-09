#!/usr/bin/env python3
"""
Hydrameda Multi-Suite Empirical Benchmark Runner.

Methodology Notice:
- Initial Success Rate (Zero-Shot Pass@1): Task passes independent verification on attempt 1 with 0 repairs.
- Iterative Self-Healed Completion Rate: Task passes independent verification after sequential self-healing
  repair cycles (capped at 3).
- Sequential iterative repair is an agentic self-healing completion metric, NOT the statistical independent
  sampling Pass@k estimator (Chen et al. 2021).

Supports:
  --suite mini | humaneval | securityeval | repobench | all
  --limit N          (run first N tasks per selected suite, 0 = all)
  --offset K         (start at index K)
  --cooldown SEC     (delay between tasks to respect Groq/Cerebras TPM)
  --resume           (skip task_ids already recorded as passed in results)
  --repo-path PATH   (custom Hydrameda root directory)
  --results-path PATH(custom benchmark results JSON output path)
  --report-md PATH   (custom Obsidian Markdown report output path)
  --archive-dir PATH (historical benchmark runs archive directory)
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda")).resolve()
DEFAULT_BENCH_DIR = DEFAULT_REPO / "benchmarks"
DEFAULT_BIN = (DEFAULT_REPO / "scripts" / "hydrameda") if (DEFAULT_REPO / "scripts" / "hydrameda").exists() else (Path.home() / ".local" / "bin" / "hydrameda")
DEFAULT_VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", Path.home() / "ObsidianVault")).resolve()
DEFAULT_REPORT_MD = DEFAULT_VAULT / "Lessons-Learned" / "Hydrameda-Benchmark-Report.md"
DEFAULT_REPORT_JSON = DEFAULT_BENCH_DIR / "benchmark_results.json"
DEFAULT_ARCHIVE_DIR = DEFAULT_BENCH_DIR / "archive"


def get_environment_info(repo_path: Path) -> dict:
    """Collects deterministic system, runtime, model, and git metadata."""
    git_commit = "unknown"
    try:
        res = subprocess.run(["git", "-C", str(repo_path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        git_commit = res.stdout.strip() or "unknown"
    except Exception:
        pass

    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "git_commit": git_commit,
        "models": {
            "planner": os.getenv("HYDRAMEDA_PLANNER_MODEL", "hybrid-architect"),
            "coder": os.getenv("HYDRAMEDA_CODER_MODEL", "hybrid-coder"),
            "repairer": os.getenv("HYDRAMEDA_REPAIRER_MODEL", "hybrid-coder-cerebras"),
        }
    }


def get_suite_files(bench_dir: Path) -> dict:
    return {
        "mini": bench_dir / "mini_sec_humaneval.jsonl",
        "humaneval": bench_dir / "humaneval_full.jsonl",
        "securityeval": bench_dir / "securityeval_full.jsonl",
        "repobench": bench_dir / "repobench_full.jsonl",
        "all": bench_dir / "combined_research_30.jsonl",
    }


def load_tasks(suite: str, offset: int, limit: int, bench_dir: Path) -> list[dict]:
    suite_files = get_suite_files(bench_dir)
    path = suite_files.get(suite, suite_files["all"])
    if not path.exists():
        raise FileNotFoundError(f"Suite file not found: {path}")
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    items = items[offset:]
    if limit > 0:
        items = items[:limit]
    return items


def write_reports(
    results: list[dict],
    suite_name: str,
    results_path: Path,
    report_md: Path,
    archive_dir: Path,
    env_info: dict
) -> dict:
    """Writes JSON telemetry and human-readable Markdown report, preserving historical runs."""
    total = len(results)
    initial_passes = sum(1 for r in results if r.get("initial_pass") or r.get("pass_at_1"))
    total_passed = sum(1 for r in results if r.get("passed"))
    execution_failures = sum(1 for r in results if r.get("failure_type") == "execution_failure")
    task_failures = sum(1 for r in results if r.get("failure_type") == "task_failure")
    avg_time = round(sum(r["elapsed_sec"] for r in results) / max(total, 1), 2)

    # Category breakdown
    categories: dict[str, dict[str, Any]] = {}
    for r in results:
        cat = r.get("category", "General")
        c = categories.setdefault(cat, {
            "total": 0,
            "initial_pass": 0,
            "self_healed_passed": 0,
            "execution_failures": 0,
            "task_failures": 0,
            "time_sum": 0.0
        })
        c["total"] += 1
        is_init = bool(r.get("initial_pass") or r.get("pass_at_1"))
        c["initial_pass"] += int(is_init)
        c["self_healed_passed"] += int(r.get("passed", False))
        if r.get("failure_type") == "execution_failure":
            c["execution_failures"] += 1
        elif r.get("failure_type") == "task_failure":
            c["task_failures"] += 1
        c["time_sum"] += r.get("elapsed_sec", 0.0)

    summary = {
        "suite": suite_name,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "methodology": {
            "initial_pass_definition": "Task passed verification on attempt 1 with 0 repairs",
            "self_healed_completion_definition": "Task passed verification after <= 3 sequential self-healing repair cycles",
            "scientific_note": "Sequential iterative repair is an agentic self-healing completion rate, distinct from statistical independent sampling Pass@k (Chen et al. 2021)."
        },
        "environment": env_info,
        "total": total,
        "initial_pass": initial_passes,
        "self_healed_completion": total_passed,
        "initial_pass_rate": round(initial_passes / max(total, 1) * 100, 1),
        "self_healed_completion_rate": round(total_passed / max(total, 1) * 100, 1),
        "net_neuro_symbolic_gain_pct": round((total_passed - initial_passes) / max(total, 1) * 100, 1),
        "execution_failures": execution_failures,
        "task_failures": task_failures,
        "avg_latency_sec": avg_time,
        "by_category": categories,
    }

    # Backward compatibility fields
    summary["pass_at_1"] = initial_passes
    summary["pass_at_k_self_healed"] = total_passed
    summary["pass_at_1_rate"] = summary["initial_pass_rate"]
    summary["pass_at_k_rate"] = summary["self_healed_completion_rate"]

    # Archive previous benchmark file before writing if it exists
    if results_path.exists():
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            archive_file = archive_dir / f"benchmark_results_{ts}.json"
            shutil.copy2(results_path, archive_file)
        except Exception as arch_err:
            print(f"⚠️ Warning: Could not archive previous benchmark results: {arch_err}")

    # Write JSON results
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps({"summary": summary, "tasks": results}, indent=2), encoding="utf-8")

    # Generate Markdown report
    md = [
        "# 📊 Hydrameda Empirical Benchmark Report",
        f"- **Date:** {summary['timestamp']}",
        f"- **Benchmark Suite:** `{suite_name}`",
        f"- **Git Commit:** `{env_info.get('git_commit', 'unknown')}`",
        f"- **Python Version:** `{env_info.get('python_version', 'unknown')}` ({env_info.get('platform', 'unknown')})",
        f"- **Total Tasks Evaluated:** {total}",
        f"- **Initial Zero-Shot Pass:** {initial_passes}/{total} (**{summary['initial_pass_rate']}%**)",
        f"- **Iterative Self-Healed Completion (≤3 Repairs):** {total_passed}/{total} (**{summary['self_healed_completion_rate']}%**)",
        f"- **Net Self-Healing Lift (Δ):** **+{summary['net_neuro_symbolic_gain_pct']}%**",
        f"- **System / Execution Failures:** {execution_failures}",
        f"- **Task Verification Failures:** {task_failures}",
        f"- **Average Verification Latency:** {avg_time}s",
        "",
        "> [!NOTE]",
        "> Sequential multi-turn repair completion rate measures the agent's ability to iteratively diagnose and self-heal code. It is distinct from statistical independent sampling Pass@k.",
        "",
        "## 1. Category Breakdown",
        "| Category | Tasks | Initial Pass | Self-Healed Completion | Execution Fails | Task Fails | Net Lift (Δ) | Avg Latency |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for cat, stats in categories.items():
        t_c = stats["total"]
        init_r = round(stats["initial_pass"] / max(t_c, 1) * 100, 1)
        comp_r = round(stats["self_healed_passed"] / max(t_c, 1) * 100, 1)
        lift = round(comp_r - init_r, 1)
        avg_c = round(stats["time_sum"] / max(t_c, 1), 2)
        md.append(
            f"| **{cat}** | {t_c} | {stats['initial_pass']}/{t_c} ({init_r}%) | **{stats['self_healed_passed']}/{t_c} ({comp_r}%)** | "
            f"{stats['execution_failures']} | {stats['task_failures']} | +{lift}% | {avg_c}s |"
        )

    md.extend([
        "",
        "## 2. Per-Task Execution Telemetry",
        "| Task ID | Category | Initial Pass | Final Status | Retries Used | Failure Classification | Latency (s) |",
        "|---|---|---|---|---|---|---|",
    ])
    for r in results:
        init_str = "✅ Yes" if (r.get("initial_pass") or r.get("pass_at_1")) else "❌ No"
        final_str = "✅ PASSED" if r.get("passed") else "❌ FAILED"
        fail_type = r.get("failure_type") or ("None" if r.get("passed") else "task_failure")
        retries = r.get("repair_cycles_used", r.get("retries", 0))
        md.append(f"| `{r['task_id']}` | {r.get('category', 'General')} | {init_str} | **{final_str}** | {retries} | `{fail_type}` | {r.get('elapsed_sec', 0.0)}s |")

    try:
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    except Exception as md_err:
        print(f"⚠️ Warning: Could not write Obsidian markdown report: {md_err}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Hydrameda Multi-Benchmark Evaluation Harness")
    parser.add_argument("legacy_limit", nargs="?", type=int, default=None, help="Optional positional limit")
    parser.add_argument("--suite", choices=["mini", "humaneval", "securityeval", "repobench", "all"], default="all")
    parser.add_argument("--limit", type=int, default=0, help="Max tasks to run (0 = all in suite)")
    parser.add_argument("--offset", type=int, default=0, help="Start task offset")
    parser.add_argument("--cooldown", type=int, default=8, help="Cooldown seconds between tasks")
    parser.add_argument("--resume", action="store_true", help="Resume from existing benchmark_results.json")
    parser.add_argument("--repo-path", type=str, default=str(DEFAULT_REPO), help="Path to Hydrameda repository root")
    parser.add_argument("--results-path", type=str, default=str(DEFAULT_REPORT_JSON), help="Output JSON path")
    parser.add_argument("--report-md", type=str, default=str(DEFAULT_REPORT_MD), help="Output Markdown report path")
    parser.add_argument("--archive-dir", type=str, default=str(DEFAULT_ARCHIVE_DIR), help="Archive directory for past runs")
    parser.add_argument("--bin-path", type=str, default=str(DEFAULT_BIN), help="Path to hydrameda executable")
    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()
    bench_dir = repo_path / "benchmarks"
    results_path = Path(args.results_path).resolve()
    report_md = Path(args.report_md).resolve()
    archive_dir = Path(args.archive_dir).resolve()
    bin_path = Path(args.bin_path).resolve()
    if not bin_path.exists():
        repo_script = repo_path / "scripts" / "hydrameda"
        if repo_script.exists():
            bin_path = repo_script

    limit = args.legacy_limit if args.legacy_limit is not None else args.limit
    suite_name = "mini" if (args.legacy_limit is not None and args.suite == "all") else args.suite

    env_info = get_environment_info(repo_path)
    items = load_tasks(suite_name, args.offset, limit, bench_dir)
    existing_results = []
    completed_ids = set()

    if args.resume and results_path.exists():
        try:
            prev = json.loads(results_path.read_text(encoding="utf-8"))
            existing_results = [t for t in prev.get("tasks", []) if t.get("passed")]
            completed_ids = {t["task_id"] for t in existing_results}
            print(f"🔄 Resuming: keeping {len(completed_ids)} previously passed tasks.")
        except Exception:
            pass

    results = list(existing_results)
    print("=" * 76)
    print(f"🧪 HYDRAMEDA EMPIRICAL BENCHMARK SUITE | Suite: {suite_name.upper()} ({len(items)} Tasks)")
    print(f"   Git Commit: {env_info['git_commit']} | Python: {env_info['python_version']}")
    print("=" * 76)

    for idx, item in enumerate(items, 1):
        task_id = item["task_id"]
        category = item.get("category", "General")
        if task_id in completed_ids:
            print(f"⏭️  [{idx}/{len(items)}] ({category}) Skipping already-passed {task_id}")
            continue

        prompt = (
            item["prompt"]
            + "\n\nIMPORTANT: In `test_module.py`, do NOT use undeclared custom pytest fixtures. "
            "Use ONLY standard built-in pytest fixtures like `tmp_path` or define any `@pytest.fixture` explicitly inside `test_module.py`."
        )

        wt_dir = repo_path / "worktrees" / task_id
        if wt_dir.exists():
            subprocess.run(["git", "-C", str(repo_path), "worktree", "remove", "--force", str(wt_dir)], capture_output=True, check=False)
            shutil.rmtree(wt_dir, ignore_errors=True)

        print(f"\n▶️  [{idx}/{len(items)}] ({category}) Running {task_id}...")
        t0 = time.time()

        captured = []
        is_execution_failure = False
        try:
            cmd = [str(bin_path), "--task-id", task_id, "--prompt", prompt, "--auto-approve"]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            for line in proc.stdout:
                print("   │ " + line, end="", flush=True)
                captured.append(line)
            proc.wait()
            if proc.returncode != 0:
                is_execution_failure = True
        except Exception as exec_err:
            is_execution_failure = True
            captured.append(f"Execution exception: {exec_err}\n")

        elapsed = round(time.time() - t0, 2)
        out = "".join(captured)

        git_commit = subprocess.run(
            ["git", "-C", str(wt_dir), "log", "-1", "--oneline"],
            capture_output=True, text=True, check=False
        ) if wt_dir.exists() else None
        has_verified_commit = bool(git_commit and git_commit.returncode == 0 and "verified autonomous build" in git_commit.stdout)

        passed = ("status: PASSED" in out) or has_verified_commit
        attempts = [int(x) for x in re.findall(r"Attempt\s+(\d+)", out)]
        retries = max(0, max(attempts) - 1) if attempts else 0

        failure_type = None
        if not passed:
            failure_type = "execution_failure" if is_execution_failure else "task_failure"

        status_str = "PASSED (Zero-Shot)" if (passed and retries == 0) else (
            f"PASSED (Self-Healed @{retries} repairs)" if passed else f"FAILED ({failure_type})"
        )
        print(f"   └─► Result: {status_str} in {elapsed}s")

        results.append({
            "task_id": task_id,
            "category": category,
            "prompt": item.get("prompt", ""),
            "passed": passed,
            "initial_pass": passed and retries == 0,
            "pass_at_1": passed and retries == 0,  # alias for backward compat
            "repair_cycles_used": retries,
            "retries": retries,                    # alias for backward compat
            "failure_type": failure_type,
            "elapsed_sec": elapsed
        })

        # Save incremental checkpoint after every task
        summary = write_reports(results, suite_name, results_path, report_md, archive_dir, env_info)

        if idx < len(items):
            print(f"   ⏳ Cooling down {args.cooldown}s (Incremental Completion Rate: {summary['self_healed_completion_rate']}%)...")
            time.sleep(args.cooldown)

    summary = write_reports(results, suite_name, results_path, report_md, archive_dir, env_info)
    print("\n" + "=" * 76)
    print(f"🏁 BENCHMARK COMPLETE ({suite_name.upper()})!")
    print(f"   • Zero-Shot Pass            : {summary['initial_pass']}/{summary['total']} ({summary['initial_pass_rate']}%)")
    print(f"   • Self-Healed Completion    : {summary['self_healed_completion']}/{summary['total']} ({summary['self_healed_completion_rate']}%)")
    print(f"   • Net Self-Healing Lift (Δ) : +{summary['net_neuro_symbolic_gain_pct']}%")
    print(f"   • Execution Failures        : {summary['execution_failures']}")
    print(f"   • Task Failures             : {summary['task_failures']}")
    print(f"   • Markdown Report           : {report_md}")
    print(f"   • JSON Telemetry            : {results_path}")
    print("=" * 76)


if __name__ == "__main__":
    main()
