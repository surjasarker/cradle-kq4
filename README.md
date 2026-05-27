# VLM Game Agent — Cradle Harness

A modular harness for evaluating vision-language models (VLMs) on three games of increasing complexity. Built on the [Cradle](https://arxiv.org/abs/2403.03186) framework.

| Game | Task | Skills |
|---|---|---|
| **I'll Cop Your Heart** | Identify the red heart (up/down/left/right) and press the matching arrow key | `press_key`, `press_start` |
| **Simon Says** | Watch a growing colour sequence and reproduce it by clicking the correct buttons | `click` |
| **King's Quest IV** | Navigate a point-and-click adventure, solve multi-step puzzles | `move_to`, `type_command` |

---

## Architecture

```
Cradle/
├── runner.py                        # Main entry point (all three games)
├── Game.py                          # Simon Says tkinter game
├── launch_pyhearts.sh               # Launch I'll Cop Your Heart agent
├── launch_simon_says.sh             # Launch Simon Says game + agent
├── launch_kq4.sh                    # Launch KQ4 (ScummVM) + agent
├── conf/
│   ├── qwen_config.json             # VLM endpoint (vLLM on Snellius)
│   ├── env_config_pygame_hearts.json
│   ├── env_config_simon_says.json
│   └── env_config_scummvm_kq4.json
├── cradle/
│   ├── runner/
│   │   ├── pygame_hearts_runner.py  # Hearts: single-shot VLM → key press
│   │   ├── simon_says_runner.py     # Simon Says: sequence VLM pipeline
│   │   └── scummvm_runner.py        # KQ4: full planning pipeline
│   └── environment/
│       ├── pygame_hearts/           # Skills: press_key, press_start
│       ├── simon_says/              # Skill: click(color)
│       └── scummvm/                 # Skills: move_to, type_command
├── res/
│   ├── simon_says/prompts/          # Prompt templates for Simon Says
│   └── scummvm/prompts/             # Prompt templates for KQ4
└── slurm/                           # HPC job scripts for Snellius vLLM
```

### Per-turn loop (all games)
```
Screenshot → base64 encode → VLM prompt → parse action → execute via xdotool/pyautogui
```

---

## Setup

### 1. Conda environment

```bash
conda create -n cradle python=3.11
conda activate cradle
pip install -r requirements.txt
```

System packages needed:
```bash
sudo apt install xdotool wmctrl gnome-screenshot
```

### 2. VLM server on Snellius (HPC)

Install vLLM once:
```bash
sbatch slurm/install_vllm.sh
```

Start the server before each run:
```bash
sbatch slurm/start_vllm_server.sh
# Check the output — it prints the node name and the exact ssh tunnel command
cat vllm_node.txt
```

Open the SSH tunnel (keep this terminal open):
```bash
ssh -L 8000:<NODE>:8000 scur0507@snellius.surf.nl -N
```

Switch model by editing `conf/qwen_config.json`:
```json
{
    "comp_model": "THUDM/GLM-4.1V-9B-Thinking",
    "api_base": "http://localhost:8000/v1"
}
```
And update `--model` in `slurm/start_vllm_server.sh` to match.

---

## Running the games

All three scripts assume `conda activate cradle` is already active.

### I'll Cop Your Heart

Start the game first (needs the `pyhearts` conda env):
```bash
conda activate pyhearts
cd /path/to/pyweek-31
python run_game.py
```

Then in a second terminal (SSH tunnel must be active):
```bash
conda activate cradle
cd Cradle
./launch_pyhearts.sh
```

### Simon Says

```bash
conda activate cradle
cd Cradle
./launch_simon_says.sh
# Starts Game.py automatically, then launches the agent
```

### King's Quest IV

Requires ScummVM and the KQ4 game files. Edit the paths at the top of `launch_kq4.sh` to match your installation, then:

```bash
conda activate cradle
cd Cradle
./launch_kq4.sh
# Starts ScummVM via PTY wrapper, waits 4 s, then launches the agent
```

---

## How each agent works

### I'll Cop Your Heart (`pygame_hearts_runner.py`)
Each turn:
1. Screenshot via `xwd` → lossless PNG
2. Single VLM call with a structured chain-of-thought prompt:
   - Step 1: menu or gameplay?
   - Step 2: colour of each heart (above / below / left / right)?
   - Step 3: which is red?
   - Step 4: emit `press_key(key=...)` or `press_start()`
3. Parse action from response, execute via `xdotool`

### Simon Says (`simon_says_runner.py`)
Each turn:
1. Poll `screenshots/game_state.txt` — wait until state is `your_turn`
2. Collect all `level_N_sequence.png` files Game.py wrote (one per sequence step)
3. Crop each to the button area and encode as JPEG
4. Single VLM call with all N images: "identify the highlighted button in each screenshot, output N `click(color=...)` calls in order"
5. Validate response (exactly N clicks, valid colours), retry up to 2× if malformed
6. Execute clicks via live pixel scan (`cv2` connected-components on exact button RGB)
7. On game over: log failure to `failures.txt`, auto-restart via START button click

### King's Quest IV (`scummvm_runner.py`)
Each turn (two-screenshot design):
1. Screenshot of any text overlay from the previous action
2. Dismiss overlay, wait 1.5 s, screenshot of the scene
3. **Information gathering** — VLM describes the current scene
4. **Action planning** — VLM selects `move_to(x, y)` or `type_command(word)` given scene, task, and forbidden-action list
5. Execute via `xdotool`

Room transitions are tracked via the ScummVM debug log; a per-room action history prevents the agent from repeating failed actions in the same room.

---

## Configuration

### Changing the VLM model

Edit `conf/qwen_config.json` and update `slurm/start_vllm_server.sh` to use the same HuggingFace model ID. No agent code changes needed.

### Tuning turn budget and timing

Each environment config (`conf/env_config_*.json`) exposes:
```json
{
    "max_turn_count": 1000,
    "post_action_delay": 0.5,
    "min_turn_time": 1.0
}
```

### Simon Says button calibration

If the click skill misses buttons after moving the game window, run the calibration script:
```bash
python test_simon_says_skills.py
```
This writes updated pixel coordinates to `conf/simon_says_buttons.json`.

---

## Output

Each run creates a timestamped folder under `runs/`:
```
runs/<timestamp>/
├── screen_*.png              # Per-turn screenshots (lossless PNG)
├── sequence_screenshots/     # Exact images sent to VLM (Simon Says)
├── failures.txt              # Per-game failure log (Simon Says)
├── video.mp4                 # Full-run video (if enabled)
└── *.log                     # Full VLM request/response logs
```

Use `log_processor.py` to convert raw logs to readable markdown:
```bash
python log_processor.py runs/<timestamp>/
```

---

## Troubleshooting

**Black screenshots / `xwd` fails**
```bash
python check_window.py   # Draws a red border around the detected game region
export DISPLAY=:0
export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
```

**VLM returns a connection error**
- Verify the SSH tunnel is alive: `curl http://localhost:8000/v1/models`
- Check the SLURM job is still running: `squeue -u $USER` on Snellius

**Simon Says clicks the wrong button**
- Re-run the button calibration script (see above)
- Make sure no other window with matching red/blue/yellow/green pixels overlaps the game window
