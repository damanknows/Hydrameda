with open("/home/daman/hydrameda/langgraph_engine/graph.py", "r") as f:
    code = f.read()

# Fix the mismatched parenthesis in initial_state
old_block = """    initial_state = HydramedaState(
        task_id=task_id,
        user_prompt=prompt_arg,
        repo_path=str(Path.home() / "hydrameda"),
        vault_context="",
        ast_skeleton="",
        spec_md="",    
        worktree_path="",
        generated_files=[],
        pytest_stderr="", 
        retry_count=0,
        status="PENDING"
    }"""

new_block = """    initial_state = HydramedaState(
        task_id=task_id,
        user_prompt=prompt_arg,
        repo_path=str(Path.home() / "hydrameda"),
        vault_context="",
        ast_skeleton="",
        spec_md="",    
        worktree_path="",
        generated_files=[],
        pytest_stderr="", 
        retry_count=0,
        status="PENDING"
    )"""

code = code.replace(old_block, new_block)
with open("/home/daman/hydrameda/langgraph_engine/graph.py", "w") as f:
    f.write(code)
print("✅ Fixed initial_state parenthesis!")
