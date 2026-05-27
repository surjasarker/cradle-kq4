# Cradle Simon Says Agent

A VLM-based agent that plays a **Simon Says** colour-sequence memory game, built on the [Cradle](https://github.com/BAAI-Agents/Cradle) framework.

The game (`Game.py`) is a Tkinter window with four coloured buttons (red, blue, yellow, green). Each level it shows a longer sequence and the player must replicate it. The agent perceives the game through screenshots, plans actions using a vision-language model, and clicks the buttons via the operating system's mouse.

---

## Requirements

### Local machine — Linux
- Linux with **GNOME on Wayland** (XWayland must be running)
- `conda` (Anaconda or Miniconda)
- `python3-tk` for the game window — install with `sudo apt install python3-tk`
- `gnome-screenshot` — usually pre-installed

### Local machine — Windows 10/11
- `conda` (Anaconda or Miniconda) on PATH
- Tkinter ships with the standard Python installer, no extra install needed
- OpenSSH client (ships with Windows 10/11 by default) for the SSH tunnel to Snellius

### Snellius HPC (for the VLM)
- Access to Snellius (`snellius.surf.nl`) with a username of the form `scurXXXX`
- GPU partition with at least one A100/H100 (≥ 24 GB VRAM for a 7B model; ≥ 80 GB for a 32B FP8 model)

---

## 1. Set up the local Cradle environment

The same commands work on Linux, macOS and Windows:

```bash
conda create -n cradle python=3.11 -y
conda activate cradle
pip install -r requirements.txt
```

On Windows, run the commands above in **Anaconda Prompt** or **PowerShell with
conda initialised** (`conda init powershell` once).

You can sanity-check the game itself without an agent attached:

```bash
python Game.py
```

A 1920×1080 window titled "Simon Says" should open. Close it when done.

> **Linux note:** On GNOME Wayland, `pyautogui` may fail to connect to X11 on
> import. `runner.py` handles this automatically by reading the XWayland auth
> cookie from `/run/user/1000/.mutter-Xwaylandauth.*` — no manual fix needed.
> The Wayland-specific code is skipped on Windows and macOS.

---

## 2. Set up vLLM on Snellius

Replace `scurXXXX` below with your own Snellius username throughout.

### 2a. Install (one time only)

Copy the slurm scripts to Snellius and run the installer:

```bash
scp slurm/install_vllm.sh scurXXXX@snellius.surf.nl:~/
ssh scurXXXX@snellius.surf.nl
sbatch install_vllm.sh
```

This creates a `vllm_env` conda environment and downloads `Qwen/Qwen2-VL-7B-Instruct` to scratch storage. Takes ~15 minutes. Check progress with `squeue -u $USER`.

### 2b. Start the vLLM server (each session)

```bash
ssh scurXXXX@snellius.surf.nl
sbatch slurm/start_vllm_server.sh
```

Wait for the job to start (`squeue -u $USER`), then check which node it landed on:

```bash
cat vllm_node.txt
```

This file also prints the exact SSH tunnel command you need, e.g.:

```
ssh -L 8000:gcn24:8000 scurXXXX@snellius.surf.nl -N
```

### 2c. Open the SSH tunnel (each session, local machine)

Open a **separate terminal** (PowerShell works on Windows — the built-in
OpenSSH client supports `-L` forwarding) and run the tunnel command from
`vllm_node.txt`:

```bash
ssh -L 8000:gcn24:8000 scurXXXX@snellius.surf.nl -N
```

Leave this terminal open. The agent talks to the VLM via `http://localhost:8000`.

Verify the tunnel is working:

```bash
# Linux/macOS
curl http://localhost:8000/v1/models
```

```powershell
# Windows PowerShell
Invoke-RestMethod http://localhost:8000/v1/models
```

You should see the served model name (e.g. `Qwen/Qwen2-VL-7B-Instruct`) in the response.

---

## 3. Run the agent

With the SSH tunnel active, run from the project directory:

### Linux

```bash
conda activate cradle
./launch_simon_says.sh
```

### Windows (PowerShell)

```powershell
conda activate cradle
.\launch_simon_says.ps1
```

If PowerShell refuses to run the script, allow local scripts once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Either script:
1. Starts the Simon Says game window (`python Game.py`)
2. Waits 3 seconds for the window to appear
3. Starts the Cradle agent
4. Stops the game process when the agent exits

Or run each step manually:

```bash
# Linux — Terminal 1: launch the game
python Game.py &

# Linux — Terminal 2: launch agent
conda activate cradle
python runner.py \
  --llmProviderConfig ./conf/qwen_config.json \
  --embedProviderConfig ./conf/qwen_config.json \
  --envConfig ./conf/env_config_simon_says.json
```

```powershell
# Windows — PowerShell 1: launch the game
Start-Process python -ArgumentList "Game.py"

# Windows — PowerShell 2: launch agent
conda activate cradle
python runner.py `
  --llmProviderConfig .\conf\qwen_config.json `
  --embedProviderConfig .\conf\qwen_config.json `
  --envConfig .\conf\env_config_simon_says.json
```

---

## 4. Configuration

### VLM / LLM provider — `conf/qwen_config.json`

```json
{
    "comp_model": "Qwen/Qwen2-VL-7B-Instruct",
    "api_base": "http://localhost:8000/v1"
}
```

Change `comp_model` to swap models (e.g. `Qwen/Qwen2.5-VL-7B-Instruct`, `Qwen/Qwen3-VL-32B-Instruct-FP8`, `THUDM/GLM-4.1V-9B-Thinking`). The model name must match what vLLM loaded on Snellius.

### Game / environment — `conf/env_config_simon_says.json`

| Key | Purpose |
|-----|---------|
| `env_name` | Window title used to find the game window (`Simon Says`) |
| `env_window_name_pattern` | Pattern matched against window titles |
| `skill_registry_name` | Class path for the Simon Says skill registry |
| `ui_control_name` | Class path for the Simon Says UI controller |
| `task_description_list` | Natural-language task prompts the agent is given |
| `skill_configs.skill_names_basic` | Allowed primitive skills — for Simon Says just `click` |

### Switching to a different model

On Snellius, edit `slurm/start_vllm_server.sh` and change the `--model` line:

```bash
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-VL-7B-Instruct \   # ← change here
    --port 8000 \
    --host 0.0.0.0 \
    --max-model-len 4096
```

Then update `conf/qwen_config.json` to match. Larger models (32B FP8, 72B) need more VRAM and possibly multiple GPUs — change the SBATCH header accordingly (`--gres=gpu:2` or `--gres=gpu:4`) and bump `--max-model-len` to leave headroom for the vision tokens of each screenshot.

---

## 5. How it works

Each turn the agent:

1. Takes a screenshot of the game window
2. Runs **information gathering** — asks the VLM to describe what is currently shown (idle screen, the colour currently being flashed, or the player's turn)
3. Runs **action planning** — asks the VLM to choose one action:
   - `click(button="red" | "blue" | "yellow" | "green")` — clicks the corresponding coloured button
4. Executes the click via `pyautogui`
5. The game writes its current phase to `screenshots/game_state.txt`, which the runner polls instead of trying to detect game phase purely from pixels

Failures (incorrect sequences) are appended to a `failures.txt` file inside the per-run folder under `runs/<model-name>/`, recording the correct sequence, the sequence the model produced, and the step at which it diverged.

---

## 6. Troubleshooting

**Screenshot is black (Linux)**
Tkinter rendering may not be visible to X11 screenshot tools. The agent uses `gnome-screenshot` which goes through the GNOME compositor. Make sure you are running a GNOME Wayland session. On Windows the agent uses `mss` instead and this is not an issue.

**`Cannot connect to display :0` (Linux)**
The XWayland auth cookie isn't in `~/.Xauthority`. This is handled automatically in `runner.py` — if it still fails, run:
```bash
xauth add :0 . $(xauth -f /run/user/1000/.mutter-Xwaylandauth.* list | awk '{print $3}' | head -1)
```

**`OSError: Cannot find the game window`**
`Game.py` isn't running or the window didn't appear yet. Start the game first and wait a few seconds before starting the agent.

**`.\launch_simon_says.ps1 : cannot be loaded because running scripts is disabled` (Windows)**
PowerShell blocks unsigned local scripts by default. Allow them for your user once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**`APIConnectionError` / tunnel not working**
- Check the SSH tunnel is running in another terminal
- Verify the node name in `vllm_node.txt` is correct (the job may have moved to a different node if resubmitted)
- Test: `curl http://localhost:8000/v1/models`

**Cursor clicks outside game / in wrong position**
The agent auto-detects the game window position on startup. If the Simon Says window is moved or resized after the agent starts, restart the agent.

**Agent always clicks the same colour (e.g. always `red`)**
This is a known failure mode for smaller VLMs that fail to visually ground the currently-flashing button. It is not a configuration bug — try a stronger model (see Section 4, *Switching to a different model*).
