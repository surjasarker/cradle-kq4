#!/bin/bash
# Start the Cradle VLM agent for I'll Cop Your Heart.
# Start the game manually first: cd /home/surja/Downloads/fomo_project/pyweek-31 && python run_game.py
# SSH tunnel must be active: ssh -L 8000:<NODE>:8000 scur0507@snellius.surf.nl -N

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-$HOME/.Xauthority}

cd "$SCRIPT_DIR"
/home/surja/anaconda3/envs/cradle/bin/python runner.py \
  --llmProviderConfig ./conf/qwen_config.json \
  --embedProviderConfig ./conf/qwen_config.json \
  --envConfig ./conf/env_config_pygame_hearts.json
