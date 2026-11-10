with open("/home/daman/hydrameda/langgraph_engine/graph.py", "r") as f:
    code = f.read()

# Update chat.completions.create inside node_code_builder to specify max_tokens
old_call = """    response = client.chat.completions.create(
        model="hybrid-coder",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )"""

new_call = """    response = client.chat.completions.create(
        model="hybrid-coder",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=4096
    )"""

if old_call in code:
    code = code.replace(old_call, new_call)
    with open("/home/daman/hydrameda/langgraph_engine/graph.py", "w") as f:
        f.write(code)
    print("✅ Successfully updated max_tokens in node_code_builder!")
else:
    print("⚠️ Call signature match not found.")
