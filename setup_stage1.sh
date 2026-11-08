#!/usr/bin/env bash
# ==============================================================================
# Hydrameda Stage 1: Router & Sandbox Spine Setup (Idempotent & Safe)
# ==============================================================================
set -euo pipefail

HYDRAMEDA_ROOT="${HYDRAMEDA_ROOT:-$HOME/hydrameda}"
OBSIDIAN_DIR="${OBSIDIAN_VAULT_PATH:-$HOME/ObsidianVault}"

echo "===================================================="
echo "🏗️  Hydrameda Stage 1: Router & Sandbox Spine Setup"
echo "   Repository Root : $HYDRAMEDA_ROOT"
echo "   Obsidian Vault  : $OBSIDIAN_DIR"
echo "===================================================="

cd "$HYDRAMEDA_ROOT"

# Helper for safe backups
safe_backup() {
    local target="$1"
    if [ -f "$target" ]; then
        local ts
        ts=$(date +%s)
        local backup="${target}.bak.${ts}"
        echo "   📦 Backing up existing $(basename "$target") -> $(basename "$backup")"
        cp "$target" "$backup"
    fi
}

# 1. Ensure Obsidian Vault directories exist
mkdir -p "$OBSIDIAN_DIR/Specs" "$OBSIDIAN_DIR/Lessons-Learned"
echo "[1/6] Obsidian directories ready at $OBSIDIAN_DIR/{Specs,Lessons-Learned}"

# 2. Manage .env (Preserves existing keys and secrets)
ENV_FILE="$HYDRAMEDA_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat << ENV_EOF > "$ENV_FILE"
GEMINI_API_KEY="your_google_ai_studio_key_here"
GROQ_API_KEY="your_groq_key_here"
CEREBRAS_API_KEY="your_cerebras_key_here"
LITELLM_MASTER_KEY="sk-hydrameda-local-key"
OBSIDIAN_VAULT_PATH="$OBSIDIAN_DIR"
HYDRAMEDA_ROOT="$HYDRAMEDA_ROOT"
ENV_EOF
    echo "[2/6] Created $ENV_FILE template."
else
    echo "[2/6] Found existing $ENV_FILE (preserving existing keys)."
    if ! grep -q "LITELLM_MASTER_KEY" "$ENV_FILE"; then
        echo 'LITELLM_MASTER_KEY="sk-hydrameda-local-key"' >> "$ENV_FILE"
    fi
    if ! grep -q "OBSIDIAN_VAULT_PATH" "$ENV_FILE"; then
        echo "OBSIDIAN_VAULT_PATH=\"$OBSIDIAN_DIR\"" >> "$ENV_FILE"
    fi
fi

# 3. LiteLLM Hydra Router Configuration (Idempotent: preserves custom config)
LITELLM_CONFIG="$HYDRAMEDA_ROOT/litellm/litellm_config.yaml"
mkdir -p "$(dirname "$LITELLM_CONFIG")"
if [ -f "$LITELLM_CONFIG" ]; then
    echo "[3/6] Found existing $LITELLM_CONFIG (preserving existing configuration)."
else
    echo "[3/6] Writing default $LITELLM_CONFIG..."
    cat << 'YAML_EOF' > "$LITELLM_CONFIG"
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
fi

# 4. Build Rootless Sandbox Verifier Dockerfile
DOCKERFILE="$HYDRAMEDA_ROOT/Dockerfile.verifier"
if [ ! -f "$DOCKERFILE" ]; then
    echo "[4/6] Creating $DOCKERFILE..."
    cat << 'DOCKER_EOF' > "$DOCKERFILE"
FROM python:3.12-slim-bookworm

# Security & Determinism: Pin explicit tool versions
RUN pip install --no-cache-dir \
    pytest==8.3.4 \
    ruff==0.8.4 \
    mypy==1.14.0 \
    bandit==1.8.0 \
    pydantic==2.10.4

# Create unprivileged non-root user and group (UID:GID 10001:10001)
RUN groupadd -g 10001 sandboxgroup && \
    useradd -m -u 10001 -g 10001 -s /bin/sh sandboxuser

USER 10001:10001
WORKDIR /app

ENTRYPOINT ["sh", "-c"]
DOCKER_EOF
else
    echo "[4/6] Found existing $DOCKERFILE."
fi

if command -v docker >/dev/null 2>&1; then
    echo "   🐳 Building Docker Verifier Image (hydrameda-verifier:latest)..."
    docker build -t hydrameda-verifier:latest -f "$DOCKERFILE" "$HYDRAMEDA_ROOT"
else
    echo "   ⚠️ Docker CLI not found; skipping image build."
fi

# 5. docker-compose.yml for LiteLLM Proxy + n8n Event Bus
COMPOSE_FILE="$HYDRAMEDA_ROOT/docker-compose.yml"
if [ -f "$COMPOSE_FILE" ]; then
    echo "[5/6] Found existing $COMPOSE_FILE (preserving custom services)."
else
    echo "[5/6] Creating $COMPOSE_FILE..."
    cat << 'COMPOSE_EOF' > "$COMPOSE_FILE"
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
    security_opt:
      - no-new-privileges:true
    command: ["--config", "/app/config.yaml", "--port", "4000"]

  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    container_name: hydrameda-n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    security_opt:
      - no-new-privileges:true
    environment:
      - N8N_SECURE_COOKIE=false
      - GENERIC_TIMEZONE=Asia/Kolkata
    volumes:
      - ./n8n_data:/home/node/.n8n
      - ${OBSIDIAN_VAULT_PATH:-/home/daman/ObsidianVault}:/data/obsidian
    extra_hosts:
      - "host.docker.internal:host-gateway"
COMPOSE_EOF
fi

# 6. Configure Ollama service override if running with systemd and permissions
if command -v systemctl >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
        echo "[6/6] Checking Ollama host bridge binding..."
        sudo mkdir -p /etc/systemd/system/ollama.service.d
        if ! grep -q "OLLAMA_HOST" /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null; then
            sudo bash -c 'cat << CONF > /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
CONF'
            sudo systemctl daemon-reload || true
            sudo systemctl restart ollama || true
        fi
    else
        echo "[6/6] Sudo requires password; skipping automatic systemd Ollama override."
    fi
else
    echo "[6/6] Systemd not active or container environment; skipping Ollama systemd override."
fi

# Start Docker Compose stack if docker compose is available
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "🚀 Starting hydrameda-litellm and hydrameda-n8n containers..."
    docker compose up -d || echo "⚠️ Warning: docker compose up failed; check daemon status."
fi

echo "===================================================="
echo "🎉 STAGE 1 SETUP COMPLETE (Idempotent & Safe)!"
echo "   • Repository Root     : $HYDRAMEDA_ROOT"
echo "   • Obsidian Vault      : $OBSIDIAN_DIR"
echo "   • LiteLLM Proxy       : http://localhost:4000"
echo "   • n8n Event Bus       : http://localhost:5678"
echo "===================================================="
