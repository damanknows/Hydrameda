with open("/home/daman/hydrameda/langgraph_engine/preprocessor.py", "r") as f:
    code = f.read()

# Make create_isolated_worktree robust against existing branches/worktrees
old_func = """def create_isolated_worktree(repo_path: str, task_id: str) -> str:
    \"\"\"Creates a clean Git worktree for background agent execution.\"\"\"
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

    if worktree_target.exists():
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree_target)], check=False, capture_output=True)

    subprocess.run(["git", "-C", str(repo), "branch", "-D", branch_name], check=False, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", branch_name, str(worktree_target)], check=True, capture_output=True)
    return str(worktree_target)"""

new_func = """def create_isolated_worktree(repo_path: str, task_id: str) -> str:
    \"\"\"Creates a clean Git worktree for background agent execution.\"\"\"
    repo = Path(repo_path).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    worktree_target = WORKTREES_DIR / task_id
    branch_name = f"hydrameda/{task_id}"

    if not (repo / ".git").exists():
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "hydrameda@local"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Hydrameda Agent"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=False, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "Initial commit"], check=False, capture_output=True)

    # Prune stale worktree references
    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=False, capture_output=True)

    if worktree_target.exists():
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree_target)], check=False, capture_output=True)

    subprocess.run(["git", "-C", str(repo), "branch", "-D", branch_name], check=False, capture_output=True)
    
    # Check if branch exists as remote or local ref elsewhere and detach/delete if needed
    try:
        subprocess.run(["git", "-C", str(repo), "worktree", "add", worktree_target, branch_name], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", branch_name, str(worktree_target)], check=True, capture_output=True)
        
    return str(worktree_target)"""

if old_func in code:
    code = code.replace(old_func, new_func)
    with open("/home/daman/hydrameda/langgraph_engine/preprocessor.py", "w") as f:
        f.write(code)
    print("✅ Successfully patched preprocessor.py worktree creation!")
else:
    print("⚠️ Function signature mismatch. Please check preprocessor.py.")
