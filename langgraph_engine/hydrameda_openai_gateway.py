#!/usr/bin/env python3
"""
Hydrameda OpenAI-Compatible Token-Optimizing Gateway (Port 4000)
Intercepts Cursor IDE & n8n /v1/chat/completions requests, compresses prompt context,
injects concise epistemic rules, and routes via Cerebras -> Groq with streaming support.
"""
import json
import os
import re
import time
import uuid
from pathlib import Path
from dotenv import dotenv_values
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from openai import OpenAI
import uvicorn

HYDRAMEDA_ROOT = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda"))
env_vars = {**dotenv_values(HYDRAMEDA_ROOT / ".env"), **os.environ}
MASTER_KEY = env_vars.get("LITELLM_MASTER_KEY", "sk-hydrameda-local-key")

app = FastAPI(title="Hydrameda Token-Optimizing OpenAI Gateway")

def verify_token(authorization: str | None) -> None:
    """Enforces Bearer token authentication against MASTER_KEY."""
    if not MASTER_KEY:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != MASTER_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")


def compress_cursor_prompt(text: str) -> tuple[str, int, int]:
    """Compresses bloated IDE context dumps while preserving AST signatures & logic."""
    orig_len = len(text)
    # 1. Collapse 3+ consecutive blank lines into a single newline
    compressed = re.sub(r"\n{3,}", "\n\n", text)
    # 2. Strip long repetitive license/header comment blocks from attached files
    compressed = re.sub(r"(?m)^(?:\s*#\s*[-=*]{10,}\s*\n)+", "", compressed)
    # 3. Trim trailing horizontal whitespace on every line
    compressed = "\n".join(line.rstrip() for line in compressed.splitlines())
    new_len = len(compressed)
    return compressed, orig_len, new_len

def optimize_messages(messages: list[dict]) -> list[dict]:
    optimized = []
    total_saved = 0
    hydrameda_sys = (
        "[Hydrameda Epistemic Guard] Output strictly typed, vulnerability-free code. "
        "Prefer stdlib & concise implementations."
    )
    has_sys = False
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            comp, orig_l, new_l = compress_cursor_prompt(content)
            total_saved += (orig_l - new_l)
            if role == "system" and not has_sys:
                comp = hydrameda_sys + "\n" + comp
                has_sys = True
            optimized.append({**msg, "content": comp})
        else:
            optimized.append(msg)
    if not has_sys:
        optimized.insert(0, {"role": "system", "content": hydrameda_sys})
    print(f"⚡ [Hydrameda Gateway] Compressed prompt payload (saved {total_saved} chars)", flush=True)
    return optimized

def get_providers():
    e = {**dotenv_values(HYDRAMEDA_ROOT / ".env"), **os.environ}
    providers = []
    if e.get("CEREBRAS_API_KEY"):
        providers.append(("cerebras", "https://api.cerebras.ai/v1", e["CEREBRAS_API_KEY"], "gpt-oss-120b"))
    if e.get("GROQ_API_KEY"):
        providers.append(("groq", "https://api.groq.com/openai/v1", e["GROQ_API_KEY"], "openai/gpt-oss-120b"))
    return providers

@app.get("/health")
@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "hydrameda-coder", "object": "model", "created": 1700000000, "owned_by": "hydrameda"},
            {"id": "default", "object": "model", "created": 1700000000, "owned_by": "hydrameda"},
            {"id": "gpt-oss-120b", "object": "model", "created": 1700000000, "owned_by": "hydrameda"},
            {"id": "gpt-4o", "object": "model", "created": 1700000000, "owned_by": "hydrameda"},
        ]
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    auth_header = request.headers.get("Authorization")
    verify_token(auth_header)

    body = await request.json()
    messages = optimize_messages(body.get("messages", []))
    stream = body.get("stream", False)
    temperature = body.get("temperature", 0.1)
    max_tokens = body.get("max_tokens", 4096)
    req_model = body.get("model", "hydrameda-coder")

    providers = get_providers()
    if not providers:
        raise HTTPException(status_code=500, detail="No CEREBRAS_API_KEY or GROQ_API_KEY configured in ~/hydrameda/.env")

    last_err = None
    for name, base_url, api_key, target_model in providers:
        try:
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=30.0)
            if not stream:
                resp = client.chat.completions.create(
                    model=target_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                data = resp.model_dump()
                data["model"] = req_model
                return JSONResponse(content=data)
            else:
                def event_stream():
                    completion_stream = client.chat.completions.create(
                        model=target_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=True,
                    )
                    for chunk in completion_stream:
                        d = chunk.model_dump()
                        d["model"] = req_model
                        yield f"data: {json.dumps(d)}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(event_stream(), media_type="text/event-stream")
        except Exception as e:
            last_err = e
            print(f"⚠️ Provider {name} failed ({e}), failing over...", flush=True)
            continue

    raise HTTPException(status_code=502, detail=f"All upstream providers failed: {last_err}")

if __name__ == "__main__":
    host = os.getenv("GATEWAY_HOST", "127.0.0.1")
    port = int(os.getenv("GATEWAY_PORT", "4001"))
    uvicorn.run(app, host=host, port=port)
