with open("/home/daman/hydrameda/langgraph_engine/graph.py", "r") as f:
    content = f.read()

# Remove the bad import line
bad_import = "from preprocessor import extract_ast_skeleton, ObsidianRAG, create_worktree = None # type: ignore"
content = content.replace(bad_import, "")

with open("/home/daman/hydrameda/langgraph_engine/graph.py", "w") as f:
    f.write(content)

print("✅ Fixed imports in graph.py!")
