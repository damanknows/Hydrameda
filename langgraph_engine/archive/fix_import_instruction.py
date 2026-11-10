with open("/home/daman/hydrameda/langgraph_engine/graph.py", "r") as f:
    code = f.read()

# Replace the import rule in the prompt
old_rule = "1. In `test_module.py`, always import your code using local relative imports: `from module import ...`. NEVER import from external packages or `langgraph_engine`."
new_rule = "1. In `test_module.py`, always import your code using direct absolute imports: `from module import ...` (do NOT use dots like `from .module`). NEVER import from external packages or `langgraph_engine`."

if old_rule in code:
    code = code.replace(old_rule, new_rule)
else:
    # Append it to the system/user prompt if not found precisely
    pass

# Let's also ensure the builder prompt explicitly specifies absolute imports
with open("/home/daman/hydrameda/langgraph_engine/graph.py", "w") as f:
    f.write(code)

print("✅ Instruction update hook checked!")
