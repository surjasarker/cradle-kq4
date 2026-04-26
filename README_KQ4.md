# Cradle KQ4 Agent

A VLM-based agent that plays **King's Quest IV: The Perils of Rosella** via ScummVM, built on the [Cradle](https://github.com/BAAI-Agents/Cradle) framework.

The agent perceives the game through screenshots, plans actions using a vision-language model (Qwen2-VL), and controls the game via mouse clicks and typed parser commands.

---

## Requirements

### Local machine
- Linux with **GNOME on Wayland** (XWayland must be running)
- `conda` (Anaconda or Miniconda)
- `xdotool` — install with `sudo apt install xdotool`
- `gnome-screenshot` — usually pre-installed
- ScummVM binary (see below)
- King's Quest IV game files (SCI version)

### Snellius HPC (for the VLM)
- Access to Snellius (`snellius.surf.nl`)
- GPU partition with at least one A100/H100 (≥ 24 GB VRAM for 7B model)

---

## 1. Set up ScummVM

Download the ScummVM Linux binary and place your KQ4 game files:

```
scummvm/
├── bin/scummvm-linux/scummvm   ← ScummVM executable
└── games/KQ4/                  ← KQ4 SCI game files (*.scr, resource.*, etc.)
```

Edit `launch_kq4.sh` to point to your paths:

```bash
SCUMMVM=/path/to/scummvm/bin/scummvm-linux/scummvm
GAME_PATH=/path/to/scummvm/games/KQ4
```

Verify ScummVM can launch the game manually before running the agent:

```bash
/path/to/scummvm --path=/path/to/games/KQ4 sci:kq4sci
```

---

## 2. Set up the local Cradle environment

```bash
conda create -n cradle python=3.11 -y
conda activate cradle
pip install -r requirements.txt
```

> **Note:** On GNOME Wayland, `pyautogui` may fail to connect to X11 on import.
> The `runner.py` script handles this automatically by reading the XWayland auth
> cookie from `/run/user/1000/.mutter-Xwaylandauth.*` — no manual fix needed.

---

## 3. Set up vLLM on Snellius

### 3a. Install (one time only)

Copy the slurm scripts to Snellius and run the installer:

```bash
scp slurm/install_vllm.sh scur0507@snellius.surf.nl:~/
ssh scur0507@snellius.surf.nl
sbatch install_vllm.sh
```

This creates a `vllm_env` conda environment and downloads `Qwen/Qwen2-VL-7B-Instruct` to scratch storage. Takes ~15 minutes. Check progress with `squeue -u $USER`.

### 3b. Start the vLLM server (each session)

```bash
ssh scur0507@snellius.surf.nl
sbatch slurm/start_vllm_server.sh
```

Wait for the job to start (`squeue -u $USER`), then check which node it landed on:

```bash
cat vllm_node.txt
```

This file also prints the exact SSH tunnel command you need, e.g.:

```
ssh -L 8000:gcn24:8000 scur0507@snellius.surf.nl -N
```

### 3c. Open the SSH tunnel (each session, local machine)

Open a **separate terminal** and run the tunnel command from `vllm_node.txt`:

```bash
ssh -L 8000:gcn24:8000 scur0507@snellius.surf.nl -N
```

Leave this terminal open. The agent talks to the VLM via `http://localhost:8000`.

Verify the tunnel is working:

```bash
curl http://localhost:8000/v1/models
```

You should see `Qwen/Qwen2-VL-7B-Instruct` in the response.

---

## 4. Run the agent

With the SSH tunnel active, run from the `Cradle/` directory:

```bash
conda activate cradle
./launch_kq4.sh
```

This script:
1. Launches ScummVM with KQ4 and room-transition debug logging
2. Waits 4 seconds for the window to appear
3. Starts the Cradle agent

Or run each step manually:

```bash
# Terminal 1 — launch ScummVM
/path/to/scummvm --path=/path/to/games/KQ4 --debugflags=Room -d 1 sci:kq4sci 2>/tmp/scummvm_debug.log &

# Terminal 2 — launch agent
conda activate cradle
python runner.py \
  --llmProviderConfig ./conf/qwen_config.json \
  --embedProviderConfig ./conf/qwen_config.json \
  --envConfig ./conf/env_config_scummvm_kq4.json
```

---

## 5. Configuration

### VLM / LLM provider — `conf/qwen_config.json`

```json
{
    "comp_model": "Qwen/Qwen2-VL-7B-Instruct",
    "api_base": "http://localhost:8000/v1"
}
```

Change `comp_model` to swap models (e.g. `Qwen/Qwen2.5-VL-7B-Instruct`). The model name must match what vLLM loaded on Snellius.

### Game / environment — `conf/env_config_scummvm_kq4.json`

| Key | Purpose |
|-----|---------|
| `env_name` | Window title substring used to find the game window |
| `scummvm_executable` | Path to ScummVM binary |
| `scummvm_game_path` | Path to KQ4 game files |
| `scummvm_game_id` | ScummVM game ID (`sci:kq4sci`) |
| `scummvm_debug_log` | Path where ScummVM writes room debug log (`/tmp/scummvm_debug.log`) |

### Switching to a different/better model

On Snellius, edit `slurm/start_vllm_server.sh` and change the `--model` line:

```bash
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-VL-7B-Instruct \   # ← change here
    --port 8000 \
    --host 0.0.0.0 \
    --max-model-len 4096
```

Then update `conf/qwen_config.json` to match. Larger models (72B) need multiple GPUs — change the SBATCH header accordingly (`--gres=gpu:4`).

---

## 6. How it works

Each turn the agent:

1. Takes a screenshot of the game window
2. Runs **information gathering** — asks the VLM to describe the scene
3. Runs **action planning** — asks the VLM to choose one action:
   - `type_command(command="verb noun")` — types a parser command into the game
   - `move_to(x=X, y=Y)` — clicks a pixel to walk the character there
4. Executes the action via `xdotool` (click) or keyboard input
5. Stores the action in a per-room and global history to prevent repeating

The agent character is **Rosella** — the woman in the red dress with blonde hair.

---

## 7. Troubleshooting

**Screenshot is black**
ScummVM uses GPU rendering not visible to X11 screenshot tools. The agent uses `gnome-screenshot` which goes through the GNOME compositor. Make sure you are running a GNOME Wayland session.

**`Cannot connect to display :0`**
The XWayland auth cookie isn't in `~/.Xauthority`. This is handled automatically in `runner.py` — if it still fails, run:
```bash
xauth add :0 . $(xauth -f /run/user/1000/.mutter-Xwaylandauth.* list | awk '{print $3}' | head -1)
```

**`OSError: Cannot find the game window`**
ScummVM isn't running or the window didn't appear yet. Start ScummVM first and wait a few seconds before starting the agent.

**`APIConnectionError` / tunnel not working**
- Check the SSH tunnel is running in another terminal
- Verify the node name in `vllm_node.txt` is correct (the job may have moved to a different node if resubmitted)
- Test: `curl http://localhost:8000/v1/models`

**Cursor clicks outside game / in wrong position**
The agent auto-detects the game window position on startup. If ScummVM is moved or resized after the agent starts, restart the agent.

**Agent repeats the same action**
The forbidden-action list covers the last 20 actions globally and all actions in the current room. If the agent still repeats, check that room detection is working by verifying `/tmp/scummvm_debug.log` is being written to.
