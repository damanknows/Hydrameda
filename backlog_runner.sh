#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$SCRIPT_DIR/scripts/hydrameda" ]; then
    DEFAULT_HYDRAMEDA_BIN="$SCRIPT_DIR/scripts/hydrameda"
elif [ -x "$HOME/.local/bin/hydrameda" ]; then
    DEFAULT_HYDRAMEDA_BIN="$HOME/.local/bin/hydrameda"
else
    DEFAULT_HYDRAMEDA_BIN="$SCRIPT_DIR/scripts/hydrameda"
fi
HYDRAMEDA_BIN="${HYDRAMEDA_BIN:-$DEFAULT_HYDRAMEDA_BIN}"
BACKLOG_FILE="${1:-$SCRIPT_DIR/backlog_tasks.txt}"
VAULT_SUMMARY="${OBSIDIAN_VAULT_PATH:-$HOME/ObsidianVault}/Lessons-Learned/Homecoming-Backlog-Summary.md"
COOLDOWN_SECONDS=12

mkdir -p "$(dirname "$VAULT_SUMMARY")"

echo "=================================================================="
echo "🚀 HYDRAMEDA HOMECOMING BACKLOG RUNNER"
echo "📂 Queue File : $BACKLOG_FILE"
echo "📓 Vault Log  : $VAULT_SUMMARY"
echo "=================================================================="

cat << HEADER > "$VAULT_SUMMARY"
# 🏠 Hydrameda Homecoming Backlog Execution Report
- **Started:** $(date '+%Y-%m-%d %H:%M:%S')
- **Queue Source:** \`$BACKLOG_FILE\`

| Task ID | Prompt | Status | Timestamp |
|---|---|---|---|
HEADER

PASSED_COUNT=0
FAILED_COUNT=0
TOTAL_COUNT=0

while IFS='|' read -r task_id prompt || [ -n "$task_id" ]; do
    [[ -z "$task_id" || "$task_id" =~ ^# ]] && continue

    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    echo ""
    echo "------------------------------------------------------------------"
    echo "▶️  [$TOTAL_COUNT] Starting Task: $task_id"
    echo "📝 Prompt: $prompt"
    echo "------------------------------------------------------------------"

    if "$HYDRAMEDA_BIN" --task-id "$task_id" --prompt "$prompt" --auto-approve --trusted-policy "batch_backlog"; then
        if git -C "$HOME/hydrameda/worktrees/$task_id" log -1 --oneline >/dev/null 2>&1; then
            STATUS="✅ PASSED"
            PASSED_COUNT=$((PASSED_COUNT + 1))
        else
            STATUS="⚠️ FINISHED (Check Report)"
            FAILED_COUNT=$((FAILED_COUNT + 1))
        fi
    else
        STATUS="❌ ERROR"
        FAILED_COUNT=$((FAILED_COUNT + 1))
    fi

    echo "| \`$task_id\` | $prompt | **$STATUS** | $(date '+%H:%M:%S') |" >> "$VAULT_SUMMARY"
    echo "⏳ Cooling down ${COOLDOWN_SECONDS}s to keep Groq/Cloud TPM buckets fresh..."
    sleep "$COOLDOWN_SECONDS"

done < "$BACKLOG_FILE"

cat << FOOTER >> "$VAULT_SUMMARY"

## 📊 Final Telemetry
- **Total Tasks Executed:** $TOTAL_COUNT
- **Passed & Auto-Committed:** $PASSED_COUNT
- **Failed / Escalated:** $FAILED_COUNT
- **Completed At:** $(date '+%Y-%m-%d %H:%M:%S')
FOOTER

echo ""
echo "=================================================================="
echo "🏁 HOMECOMING BACKLOG COMPLETE! ($PASSED_COUNT/$TOTAL_COUNT Passed)"
echo "📄 Full Markdown table written to: $VAULT_SUMMARY"
echo "=================================================================="
