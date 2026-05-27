#!/bin/bash
# Launch Simon Says game and AI agent on Linux/macOS

set -e  # Exit on error

# Configuration
PYTHON=${PYTHON:-python}
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "================================"
echo "Simon Says - AI Agent Launcher"
echo "================================"
echo ""

# Check if Game.py exists
if [ ! -f "$PROJECT_DIR/Game.py" ]; then
    echo "ERROR: Game.py not found at $PROJECT_DIR/Game.py"
    exit 1
fi

echo "Starting Simon Says game..."
echo "Activate conda environment with: conda activate cradle"
echo ""

# Launch Simon Says in the background
$PYTHON "$PROJECT_DIR/Game.py" &
GAME_PID=$!

echo "Game launched (PID: $GAME_PID)"
echo "Waiting 3 seconds for game window to appear..."
sleep 3

# Launch agent
echo ""
echo "Starting AI agent..."
$PYTHON "$PROJECT_DIR/runner.py" \
    --llmProviderConfig "./conf/qwen_config.json" \
    --embedProviderConfig "./conf/qwen_config.json" \
    --envConfig "./conf/env_config_simon_says.json"

# Cleanup
echo ""
echo "Cleaning up..."
kill $GAME_PID 2>/dev/null || true
wait $GAME_PID 2>/dev/null || true

echo "Done!"
