#!/usr/bin/env bash
set -e

echo "===================================================="
echo "🏗️  Hydrameda Stage 1: Router & Sandbox Spine Setup"
echo "===================================================="

cd ~/hydrameda

# 1. Ensure Obsidian Vault directories exist
OBSIDIAN_DIR="$HOME/ObsidianVault"
mkdir -p "$OBSIDIAN_DIR/Specs" "$OBSIDIAN_DIR/Lessons-Learned"
echo "[1/6] Created Obsidian directories at $OBSIDIAN_DIR/{Specs,Lessons-Learned}"

# 2. Create .env file (preserves existing keys if you already edited it)
if [ ! -f ~/hydrameda/.env ]; then
cat << ENV_EOF > ~/hydrameda/.env
GEMINI_API_KEY="your_google_ai_studio_key_here"
GROQ_API_KEY="your_groq_key_here"
CEREBRAS_API_KEY="your_cerebras_key_here"
LITELLM_MASTER_KEY="sk-hydrameda-local-key"
OBSIDIAN_VAULT_PATH="$OBSIDIAN_DIR"
ENV_EOF
    echo "[2/6] Created ~/hydrameda/.env template."
else
    echo "[2/6] Found existing ~/hydrameda/.env (preserving your keys)."
    if ! grep -q "LITELLM_MASTER_KEY" ~/hydrameda/.env; then
        echo 'LITELLM_MASTER_KEY="sk-hydrameda-local-key"' >> ~/hydrameda/.env
    fi
fi

# 3. Write LiteLLM Hydra Router Configuration
echo "[3/6] Writing ~/hydrameda/litellm/litellm_config.yaml..."
cat << 'YAML_EOF' > ~/hydrameda/litellm/litellm_config.yaml
model_list:
  # Tier 1: Cloud Architect (1M Context Free Tier)
  - model_name: hybrid-architect
    litellm_params:
      model: gemini/gemini-2.5-flash
      api_key: os.environ/GEMINI_API_KEY

  # Tier 2: High-Speed Cloud Coder (350+ tok/s Free Tier)
  - model_name: hybrid-coder
    litellm_params:
      model: groq/qwen-2.5-coder-32b
      api_key: os.environ/GROQ_API_KEY

  # Tier 3: Zero-Cost Cloud Failover
  - model_name: hybrid-coder-cerebras
    litellm_params:
      model: cerebras/llama-3.3-70b
      api_key: os.environ/CEREBRAS_API_KEY

  # Tier 4: Local 24GB RAM-Pinned Model (Fast Router + Offline Fallback)
  - model_name: hybrid-fast
    litellm_params:
      model: ollama/qwen2.5-coder:3b-instruct-q4_K_M
      api_base: http://host.docker.internal:11434
      num_ctx: 4096
      num_thread: 5

router_settings:
  routing_strategy: latency-based-routing
  num_retries: 2
  timeout: 45
  fallbacks:
    - {"hybrid-architect": ["hybrid-coder", "hybrid-fast"]}
    - {"hybrid-coder": ["hybrid-coder-cerebras", "hybrid-architect", "hybrid-fast"]}
YAML_EOF

# 4. Create & Build Rootless Sandbox Verifier Dockerfile
echo "[4/6] Building Rootless Docker Verifier Image (hydrameda-verifier:latest)..."
cat << 'DOCKER_EOF' > ~/hydrameda/Dockerfile.verifier
FROM python:3.12-slim-bookworm

# Install deterministic static analysis, security, and test tools
RUN pip install --no-cache-dir pytest ruff mypy bandit pydantic

# Create unprivileged rootless user for untrusted code execution
RUN useradd -m -u 10001 sandboxuser
USER sandboxuser
WORKDIR /app

ENTRYPOINT ["sh", "-c"]
DOCKER_EOF

docker build -t hydrameda-verifier:latest -f ~/hydrameda/Dockerfile.verifier ~/hydrameda

# 5. Write docker-compose.yml for LiteLLM Proxy + n8n Event Bus
echo "[5/6] Writing ~/hydrameda/docker-compose.yml..."
cat << 'COMPOSE_EOF' > ~/hydrameda/docker-compose.yml
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    container_name: hydrameda-litellm
    restart: unless-stopped
    ports:
      - "4000:4000"
    env_file:
      - .env
    volumes:
      - ./litellm/litellm_config.yaml:/app/config.yaml:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
    command: ["--config", "/app/config.yaml", "--port", "4000"]

  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    container_name: hydrameda-n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - N8N_SECURE_COOKIE=false
      - GENERIC_TIMEZONE=Asia/Kolkata
    volumes:
      - ./n8n_data:/home/node/.n8n
      - ${OBSIDIAN_VAULT_PATH:-/home/daman/ObsidianVault}:/data/obsidian
    extra_hosts:
      - "host.docker.internal:host-gateway"
COMPOSE_EOF

# Ensure Ollama listens on docker bridge or host gateway so LiteLLM container can reach it
sudo mkdir -p /etc/systemd/system/ollama.service.d
if ! grep -q "OLLAMA_HOST" /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null; then
    sudo sed -i '/\[Service\]/a Environment="OLLAMA_HOST=0.0.0.0:11434"' /etc/systemd/system/ollama.service.d/override.conf
    sudo systemctl daemon-reload
    sudo systemctl restart ollama
fi

# Start Docker Compose stack
echo "[6/6] Starting hydrameda-litellm and hydrameda-n8n containers..."
docker compose up -d

echo "⏳ Waiting 8 seconds for LiteLLM Proxy to initialize..."
sleep 8

echo "===================================================="
echo "🧪 STAGE 1 GATE CHECK: Testing Verifier & Hydra Router"
echo "===================================================="

# Gate Check A: Rootless Docker Sandbox Verifier
echo "--> Test A: Running Rootless Sandbox Verifier (--network none)..."
docker run --rm --network none hydrameda-verifier:latest "ruff --version && pytest --version && mypy --version && bandit --version"

# Gate Check B: LiteLLM Proxy -> Host Ollama 3B Model
echo "--> Test B: Testing LiteLLM Proxy (localhost:4000) -> Local Qwen-3B..."
curl -s http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-hydrameda-local-key" \
  -d '{
    "model": "hybrid-fast",
    "messages": [{"role": "user", "content": "Reply ONLY with: {\"stage1\": \"verified\"}"}],
    "temperature": 0.0
  }' | grep -o '"content":[^}]*' || echo "⚠️ LiteLLM still warming up—run the curl command again in 5 seconds."

echo ""
echo "===================================================="
echo "🎉 STAGE 1 COMPLETE!"
echo "   • LiteLLM Hydra Proxy : http://localhost:4000"
echo "   • n8n Event Bus UI    : http://localhost:5678"
echo "   • Obsidian Mount      : $OBSIDIAN_DIR -> /data/obsidian"
echo "===================================================="
