#!/usr/bin/env python3
"""
Hydrameda Worktree TTL Pruning Utility.

Prunes stale worktree branches and directories based on age (TTL) and status.
Guarantees clean worktree removal with `git worktree remove` and `git worktree prune`.
"""
import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Any


HYDRAMEDA_ROOT = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda")).resolve()
WORKTREES_DIR = HYDRAMEDA_ROOT / "worktrees"


def prune_worktrees(
    repo_path: Path = HYDRAMEDA_ROOT,
    max_age_days: float = 7.0,
    dry_run: bool = False,
    unverified_only: bool = False,
    force: bool = False
) -> Dict[str, Any]:
    """Prunes worktrees older than max_age_days from repo_path / 'worktrees'."""
    repo = Path(repo_path).resolve()
    wt_dir = repo / "worktrees"
    if not wt_dir.exists():
        return {"pruned": [], "kept": [], "total": 0}

    now = time.time()
    cutoff_seconds = max_age_days * 86400.0

    pruned = []
    kept = []

    for item in sorted(wt_dir.iterdir()):
        if not item.is_dir():
            continue

        try:
            mtime = item.stat().st_mtime
            age_days = round((now - mtime) / 86400.0, 2)
        except Exception:
            age_days = 0.0

        is_stale = (now - mtime) > cutoff_seconds

        # Requirement A3.9: Protect active or recently updated tasks (minimum 1 hour grace period)
        if (now - mtime) < 3600.0 and not force:
            kept.append({"task_id": item.name, "age_days": age_days, "reason": "recently_active_grace_period"})
            continue

        # Check for active git locks
        if (item / ".git" / "index.lock").exists() or (item / ".git" / "HEAD.lock").exists():
            kept.append({"task_id": item.name, "age_days": age_days, "reason": "active_git_lock"})
            continue

        # Requirement A5.7: Check verification json instead of commit-message substring
        ver_file = item / ".hydrameda_verification.json"
        is_verified = False
        if ver_file.exists():
            try:
                import json
                v_data = json.loads(ver_file.read_text(encoding="utf-8"))
                is_verified = v_data.get("overall_status") == "passed"
            except Exception:
                is_verified = False
        else:
            commit_res = subprocess.run(
                ["git", "-C", str(item), "log", "-1", "--oneline"],
                capture_output=True, text=True, check=False
            )
            last_commit = commit_res.stdout.strip() or "uncommitted"
            is_verified = "verified autonomous build" in last_commit.lower()

        if unverified_only and is_verified:
            kept.append({"task_id": item.name, "age_days": age_days, "reason": "verified"})
            continue

        if not is_stale and max_age_days > 0:
            kept.append({"task_id": item.name, "age_days": age_days, "reason": "within_ttl"})
            continue

        # Target for pruning
        task_id = item.name
        branch_name = f"hydrameda/{task_id}"

        if dry_run:
            pruned.append({"task_id": task_id, "age_days": age_days, "verified": is_verified, "dry_run": True})
        else:
            try:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", "--force", str(item)],
                    capture_output=True, text=True, check=False
                )
                subprocess.run(
                    ["git", "-C", str(repo), "branch", "-D", branch_name],
                    capture_output=True, text=True, check=False
                )
                if item.exists():
                    shutil.rmtree(item, ignore_errors=True)
                pruned.append({"task_id": task_id, "age_days": age_days, "verified": is_verified, "pruned": True})
            except Exception as e:
                kept.append({"task_id": task_id, "age_days": age_days, "error": str(e)})

    if not dry_run:
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True, check=False)

    return {"pruned": pruned, "kept": kept, "total": len(pruned) + len(kept)}


def main():
    parser = argparse.ArgumentParser(description="Prune stale Hydrameda Git worktrees")
    parser.add_argument("--days", type=float, default=7.0, help="Maximum age in days before pruning (default: 7.0)")
    parser.add_argument("--repo-path", type=str, default=str(HYDRAMEDA_ROOT), help="Hydrameda repository root")
    parser.add_argument("--unverified-only", action="store_true", help="Only prune unverified worktrees")
    parser.add_argument("--dry-run", action="store_true", help="Report without deleting files or branches")
    parser.add_argument("--force", action="store_true", help="Force pruning, bypassing the 1-hour grace period")
    args = parser.parse_args()

    repo = Path(args.repo_path).resolve()
    print(f"🧹 Pruning worktrees in {repo / 'worktrees'} (older than {args.days} days, dry_run={args.dry_run})")
    res = prune_worktrees(
        repo_path=repo,
        max_age_days=args.days,
        dry_run=args.dry_run,
        unverified_only=args.unverified_only,
        force=args.force
    )
    print(f"   Pruned: {len(res['pruned'])} | Kept: {len(res['kept'])} | Total scanned: {res['total']}")
    for p in res["pruned"]:
        print(f"   - Removed {p['task_id']} (Age: {p.get('age_days')}d, Verified: {p.get('verified')})")


if __name__ == "__main__":
    main()
