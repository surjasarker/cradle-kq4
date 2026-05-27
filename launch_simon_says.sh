#!/bin/bash
# Launch Simon Says game and AI agent.
# Prerequisites: conda activate cradle && SSH tunnel active (see README).

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-$HOME/.Xauthority}

echo "Starting Simon Says game..."
python "$SCRIPT_DIR/Game.py" &
GAME_PID=$!

echo "Waiting for game window..."
sleep 3

echo "Starting agent..."
cd "$SCRIPT_DIR"
/home/surja/anaconda3/envs/cradle/bin/python runner.py \
    --llmProviderConfig ./conf/qwen_config.json \
    --embedProviderConfig ./conf/qwen_config.json \
    --envConfig ./conf/env_config_simon_says.json

kill $GAME_PID 2>/dev/null || true
wait $GAME_PID 2>/dev/null || true
