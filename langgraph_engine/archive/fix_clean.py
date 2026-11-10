with open("/home/daman/hydrameda/langgraph_engine/graph.py", "r") as f:
    lines = f.readlines()

# Keep everything up until the main execution block
new_lines = []
for line in lines:
    if 'if __name__ == "__main__":' in line:
        break
    new_lines.append(line)

# Append a clean, properly parenthesized main execution block
main_block = '''if __name__ == "__main__":
    prompt_arg = sys.argv[1] if len(sys.argv) > 1 else "Create a secure password hashing utility function with salt using hashlib and unit tests."
    task_id = "task-jwt-01"
    
    print(f"🚀 Starting Hydrameda State Machine for task: '{task_id}'")
    app = build_hydrameda_graph()
    
    initial_state = HydramedaState(
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
    )
    
    config = {"configurable": {"thread_id": task_id}}
    final_state = app.invoke(initial_state, config)
    print(f"\\n🏁 Hydrameda pipeline execution completed with status: {final_state['status']}")
'''

new_lines.append(main_block)

with open("/home/daman/hydrameda/langgraph_engine/graph.py", "w") as f:
    f.writelines(new_lines)

print("✅ Successfully cleaned and fixed graph.py!")
