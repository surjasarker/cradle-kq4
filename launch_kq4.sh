#!/bin/bash
# Launch King's Quest IV with the Cradle AI agent.
# Usage: ./launch_kq4.sh
#
# Prerequisites:
#   1. SSH tunnel to Snellius must be active:
#      ssh -L 8000:gcn24:8000 scur0507@snellius.surf.nl -N  (in a separate terminal)
#   2. vLLM server must be running on Snellius (sbatch slurm/start_vllm_server.sh)

set -e

SCUMMVM=/home/surja/Downloads/fomo_project/scummvm/bin/scummvm-linux/scummvm
GAME_PATH=/home/surja/Downloads/fomo_project/scummvm/games/KQ4
GAME_ID=sci:kq4sci

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-$HOME/.Xauthority}

echo "Starting ScummVM..."
# Launch ScummVM through a PTY wrapper so debugC(Room,...) output is
# line-buffered and appears in the log immediately (not buffered until exit).
python "$SCRIPT_DIR/scummvm_pty_wrapper.py" /tmp/scummvm_debug.log \
  "$SCUMMVM" --path="$GAME_PATH" \
  --debugflags=Room -d 2 \
  "$GAME_ID" &
SCUMMVM_PID=$!

echo "Waiting for ScummVM window to appear..."
sleep 4

echo "Starting Cradle agent..."
cd "$SCRIPT_DIR"
/home/surja/anaconda3/envs/cradle/bin/python runner.py \
  --llmProviderConfig ./conf/qwen_config.json \
  --embedProviderConfig ./conf/qwen_config.json \
  --envConfig ./conf/env_config_scummvm_kq4.json

# Cleanup on exit
kill $SCUMMVM_PID 2>/dev/null || true
