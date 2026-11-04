with open("/home/daman/hydrameda/langgraph_engine/graph.py", "r") as f:
    code = f.read()

# Locate and replace the prompt inside node_code_builder
old_prompt_snippet = """    prompt = f\"\"\"You are Hydrameda's Autonomous Coder.
Specification:
{state['spec_md']}
{retry_msg}

Return a valid JSON object with EXACTLY this structure (no markdown wrapper formatting outside JSON if possible, or standard json codeblock):"""

new_prompt_snippet = """    prompt = f\"\"\"You are Hydrameda's Autonomous Coder.
Specification:
{state['spec_md']}
{retry_msg}

CRITICAL RULES FOR CODE & TESTS:
1. In `test_module.py`, always import your code using local relative imports: `from module import ...`. NEVER import from external packages or `langgraph_engine`.
2. Do not hardcode magic cryptographic hashes in tests; instead, compute the expected hash dynamically or assert behavior (e.g. `assert hash_password(p) != p`, `assert hasher.verify_password(p)`).

Return a valid JSON object with EXACTLY this structure (no markdown wrapper formatting outside JSON if possible, or standard json codeblock):"""

if old_prompt_snippet in code:
    code = code.replace(old_prompt_snippet, new_prompt_snippet)
    with open("/home/daman/hydrameda/langgraph_engine/graph.py", "w") as f:
        f.write(code)
    print("✅ Successfully updated Node 2 prompt instructions!")
else:
    print("⚠️ Snippet match not found. Let's check manually if needed.")
