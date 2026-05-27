"""Simon Says game runner for VLM-based game playing.

Mirrors the structure of scummvm_runner.py but is specialised for the
Simon Says game (skills: click(color), wait(duration), screenshot()).
"""

import glob
import json
import os
import re
import time
from copy import deepcopy
from typing import Dict, Any, List, Optional

from cradle import constants
from cradle.environment.skill_registry_factory import SkillRegistryFactory
from cradle.environment.ui_control_factory import UIControlFactory
from cradle.log import Logger
from cradle.module.executor import Executor
from cradle.planner.planner import Planner
from cradle.config import Config
from cradle.memory import LocalMemory
from cradle.provider.llm.llm_factory import LLMFactory
from cradle.provider.sam_provider import SamProvider
from cradle.gameio.io_env import IOEnvironment
from cradle.gameio.game_manager import GameManager
from cradle.provider.video.video_recorder import VideoRecordProvider
from cradle.gameio.lifecycle.ui_control import switch_to_environment
import cradle.environment.simon_says

config = Config()
logger = Logger()
io_env = IOEnvironment()


class SimonSaysRunner:
    """Main runner for the Simon Says game."""

    def __init__(self,
                 llm_provider_config_path: str,
                 embed_provider_config_path: str,
                 task_description: str,
                 use_self_reflection: bool = False,
                 use_task_inference: bool = False):

        self.llm_provider_config_path = llm_provider_config_path
        self.embed_provider_config_path = embed_provider_config_path
        self.task_description = task_description
        self.use_self_reflection = use_self_reflection
        self.use_task_inference = use_task_inference

        self.stop_flag = False
        self.count_turns = 0
        self.count_empty_action = 0
        self.count_empty_action_threshold = 3
        # Stop after this many failures (game-overs) so unattended runs
        # don't burn the remote vLLM job indefinitely.
        self.failures_count = 0
        self.max_failures = 50

        self.set_internal_params()

    def set_internal_params(self):
        lf = LLMFactory()
        self.llm_provider, self.embed_provider = lf.create(
            self.llm_provider_config_path,
            self.embed_provider_config_path
        )
        logger.write("LLM and embedding providers initialized")

        self.memory = LocalMemory(memory_path=config.work_dir,
                                  max_recent_steps=config.max_recent_steps)
        self.memory.load(config.memory_load_path)
        logger.write("Memory initialized")

        srf = SkillRegistryFactory()
        srf.register_builder(config.env_short_name, config.skill_registry_name)
        self.skill_registry = srf.create(
            config.env_short_name,
            skill_configs=config.skill_configs,
            embedding_provider=self.embed_provider
        )
        logger.write(f"Skill registry initialized: {config.skill_registry_name}")

        ucf = UIControlFactory()
        ucf.register_builder(config.env_short_name, config.ui_control_name)
        self.env_ui_control = ucf.create(config.env_short_name)
        logger.write(f"UI control initialized: {config.ui_control_name}")

        self.gm = GameManager(
            env_name=config.env_name,
            embedding_provider=self.embed_provider,
            llm_provider=self.llm_provider,
            skill_registry=self.skill_registry,
            ui_control=self.env_ui_control,
        )
        logger.write("Game manager initialized")

        self.planner_params = config.planner_params

        self.sam_provider = SamProvider()
        self.frame_extractor = None
        self.icon_replacer = None
        self.gd_detector = None

        self.planner = Planner(
            llm_provider=self.llm_provider,
            planner_params=self.planner_params,
            frame_extractor=self.frame_extractor,
            icon_replacer=self.icon_replacer,
            object_detector=self.gd_detector,
            use_self_reflection=self.use_self_reflection,
            use_task_inference=self.use_task_inference,
        )
        logger.write("Planner initialized")

        skills = self.gm.retrieve_skills(
            query_task=self.task_description,
            skill_num=config.skill_configs.get(constants.SKILL_CONFIG_MAX_COUNT, 3),
            screen_type=constants.GENERAL_GAME_INTERFACE,
        )
        self.skill_library = self.gm.get_skill_information(
            skills,
            skill_library_with_code=config.skill_library_with_code,
        )
        logger.write(f"Skill library: {len(self.skill_library)} skills")

        self.video_recorder = VideoRecordProvider(
            os.path.join(config.work_dir, 'video.mp4')
        )

        self.skill_execute = Executor(env_manager=self.gm)
        logger.write("Executor initialized")

        self.checkpoint_path = os.path.join(config.work_dir, './checkpoints')
        os.makedirs(self.checkpoint_path, exist_ok=True)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    # Folder Game.py writes per-round sequence screenshots into. Project root
    # relative; cleared once at runner startup so we don't pick up stale games.
    SEQUENCE_DIR = "screenshots"

    def _clear_sequence_screenshots(self):
        try:
            from cradle.utils.file_utils import assemble_project_path
            seq_dir = assemble_project_path(self.SEQUENCE_DIR)
            if not os.path.isdir(seq_dir):
                logger.write(f"No {self.SEQUENCE_DIR}/ folder to clear")
                return
            removed = 0
            for f in glob.glob(os.path.join(seq_dir, "level_*_sequence.png")):
                try:
                    os.remove(f)
                    removed += 1
                except OSError as e:
                    logger.warn(f"Could not remove {f}: {e}")
            logger.write(f"Cleared {removed} stale sequence screenshot(s) from {seq_dir}")
        except Exception as e:
            logger.warn(f"_clear_sequence_screenshots failed: {e}")

    def _click_start_button(self) -> bool:
        """Find the bright-green START GAME button on screen and click it.

        Game.py uses #00ff00 (pure green) for START vs tk 'green' = #008000 for
        the grid green button. Scanning for pure green disambiguates them.
        Returns True if a click was issued, False if no START button found.
        """
        try:
            import pyautogui
            import numpy as np
        except Exception as e:
            logger.warn(f"_click_start_button: missing pyautogui/numpy: {e}")
            return False

        try:
            shot = pyautogui.screenshot()
            arr = np.array(shot)
            mask = (arr[:, :, 0] == 0) & (arr[:, :, 1] == 255) & (arr[:, :, 2] == 0)
            ys, xs = np.where(mask)
            if len(xs) == 0:
                logger.warn("_click_start_button: no bright-green pixels found on screen")
                return False
            x, y = int(np.median(xs)), int(np.median(ys))
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click(x, y)
            logger.write(f"Auto-clicked START GAME at ({x},{y})")
            return True
        except Exception as e:
            logger.warn(f"_click_start_button failed: {e}")
            return False

    def _check_game_state(self) -> str:
        """Read the game state Game.py writes to screenshots/game_state.txt.

        Game.py updates this file on every state transition. Returns one of:
          - 'idle'      : game hasn't started (or was reset)
          - 'watching'  : Simon is currently flashing
          - 'your_turn' : player should click
          - 'game_over' : player got it wrong; restart needed
          - 'unknown'   : state file missing/unreadable (treated as 'watching'
                          by the main loop, i.e. wait and retry)
        """
        try:
            from cradle.utils.file_utils import assemble_project_path
            state_path = assemble_project_path(
                os.path.join(self.SEQUENCE_DIR, "game_state.txt")
            )
            if not os.path.exists(state_path):
                logger.warn(f"Game state file missing: {state_path}")
                return "unknown"
            with open(state_path, "r", encoding="utf-8") as f:
                state = f.read().strip()
            valid = {"idle", "watching", "your_turn", "game_over"}
            if state not in valid:
                logger.warn(f"Unrecognised game state {state!r}; treating as unknown")
                return "unknown"
            logger.write(f"_check_game_state: state={state}")
            return state
        except Exception as e:
            logger.warn(f"_check_game_state failed: {e}")
            return "unknown"

    def _check_game_state_OLD_pixel_scan(self) -> str:
        """LEGACY: pixel-scan fallback (no longer used). Kept here in case the
        state file approach turns out to need a backup."""
        try:
            import numpy as np
            import pyautogui
            shot = pyautogui.screenshot()
            arr = np.array(shot.convert("RGB"))
            H, W = arr.shape[:2]
            R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

            # Locate the buttons by EXACT color match (pyautogui is lossless).
            btn_mask = (
                ((R == 255) & (G ==   0) & (B ==   0)) |  # red
                ((R ==   0) & (G ==   0) & (B == 255)) |  # blue
                ((R == 255) & (G == 255) & (B ==   0)) |  # yellow
                ((R ==   0) & (G == 128) & (B ==   0))    # green (tk 'green')
            )
            ys, xs = np.where(btn_mask)
            if len(xs) == 0:
                logger.write("_check_game_state: no button pixels found")
                return "unknown"
            x0, y0 = int(xs.min()), int(ys.min())
            x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
            if (x1 - x0) > 800 or (y1 - y0) > 800:
                logger.warn(
                    f"_check_game_state: implausible button bbox "
                    f"({x0},{y0})-({x1},{y1})"
                )
                return "unknown"

            # Scan the area above the button grid, restricted to a band
            # centered on the buttons. Going full screen width picks up
            # red pixels from the desktop wallpaper / other windows to the
            # side of the Simon Says window, which causes false game-over
            # detections. info_frame (status label + level label) is
            # centered above the buttons, so ~300px margin each side covers
            # it with plenty of room to spare.
            strip_left = max(0, x0 - 300)
            strip_right = min(W, x1 + 300)
            strip = arr[0:y0, strip_left:strip_right]
            sR = strip[:, :, 0].astype(int)
            sG = strip[:, :, 1].astype(int)
            sB = strip[:, :, 2].astype(int)

            # More permissive red detection. Windows ClearType renders text
            # edges with non-uniform R/G/B blending for subpixel positioning,
            # so a 'red' anti-aliased pixel can be e.g. (180, 100, 50) — not
            # 'red-dominant' by a strict margin. We use a ratio-based check:
            # pixel counts as red if R > 100 AND R > G*1.5 AND R > B*1.5.
            red_mask = (sR > 100) & (sR > sG * 1.5) & (sR > sB * 1.5)
            # Yellow: R and G both high, B much lower
            yellow_mask = (sR > 100) & (sG > 100) & (sR > sB * 1.5) & (sG > sB * 1.5)
            # (green not needed for the decision — it's the fallback)

            red_count = int(red_mask.sum())
            yellow_count = int(yellow_mask.sum())

            # Save the scanned strip to runs/<ts>/state_checks/ so you can
            # inspect exactly what the detector sees. Helpful when a real
            # 'Game Over!' isn't being picked up.
            try:
                dbg_dir = os.path.join(config.work_dir, "state_checks")
                os.makedirs(dbg_dir, exist_ok=True)
                from PIL import Image as _PILImage
                dbg_path = os.path.join(
                    dbg_dir,
                    f"turn_{self.count_turns:03d}_strip_red{red_count}_yel{yellow_count}.png",
                )
                _PILImage.fromarray(strip.astype("uint8")).save(dbg_path)
            except Exception as e:
                logger.warn(f"Could not save state-check strip: {e}")

            logger.write(
                f"_check_game_state scan: bbox=({x0},{y0})-({x1},{y1}), "
                f"strip=({strip_left},0)-({strip_right},{y0}), "
                f"red={red_count}, yellow={yellow_count}"
            )
            # Check red FIRST so game-over wins over any always-present green.
            if red_count > 50:
                return "game_over"
            # Yellow status text means Simon is flashing or game isn't started.
            if yellow_count > 50:
                return "watching"
            # Otherwise the status is green ("Your turn!" or "Correct!").
            return "your_turn"
        except Exception as e:
            logger.warn(f"_check_game_state failed: {e}")
            return "unknown"

    def _list_sequence_screenshots(self) -> List[str]:
        """Return all level_NN_sequence.png paths, sorted by N ascending."""
        try:
            from cradle.utils.file_utils import assemble_project_path
            seq_dir = assemble_project_path(self.SEQUENCE_DIR)
            files = glob.glob(os.path.join(seq_dir, "level_*_sequence.png"))
            def level_of(path):
                m = re.search(r"level_(\d+)_sequence\.png$", os.path.basename(path))
                return int(m.group(1)) if m else 0
            return sorted(files, key=level_of)
        except Exception as e:
            logger.warn(f"_list_sequence_screenshots failed: {e}")
            return []

    def run(self):
        logger.write(f"Starting Simon Says runner | task: {self.task_description}")

        # Wipe any sequence screenshots from a previous game session so the
        # model isn't shown stale colors from a finished game.
        self._clear_sequence_screenshots()

        working_memory = {
            constants.TASK_DESCRIPTION: self.task_description,
            constants.SKILL_LIBRARY: self.skill_library,
            constants.PRE_ACTION: constants.EMPTY_STRING,
            constants.PRE_DECISION_MAKING_REASONING: constants.EMPTY_STRING,
            constants.PRE_SELF_REFLECTION_REASONING: constants.EMPTY_STRING,
            constants.SUMMARIZATION: constants.EMPTY_STRING,
            constants.SKILL_STEPS: [],
            "screen_classification": constants.GENERAL_GAME_INTERFACE,
            "pre_screen_classification": constants.GENERAL_GAME_INTERFACE,
            constants.START_FRAME_ID: 0,
            constants.END_FRAME_ID: 0,
            constants.CUR_SCREENSHOT_PATH: "",
            constants.MOUSE_POSITION: (0, 0),
            "image_description": "",
            "subtask_description": "",
            "subtask_reasoning": "",
            "self_reflection_reasoning": "",
            constants.EXECUTED_SKILLS: [],
        }
        self.memory.update_info_history(working_memory)
        self.memory.add_recent_history_kv(constants.TASK_DESCRIPTION, self.task_description)

        switch_to_environment()

        if config.enable_videocapture:
            self.video_recorder.start_capture()

        while not self.stop_flag:
            try:
                logger.write(f"\n{'='*60}")
                logger.write(f"Turn #{self.count_turns}: {self.task_description}")
                logger.write(f"{'='*60}")

                # If the game hasn't started (no sequence screenshots yet),
                # click START ourselves rather than asking the model — START
                # is the bright-green #00ff00 button, not part of the click()
                # skill's color vocabulary (which only addresses the grid).
                if not self._list_sequence_screenshots():
                    if self._click_start_button():
                        self._wait_for_new_sequence_screenshot(prev_count=0)
                    else:
                        logger.warn("No sequence screenshots and no START button found — retrying")
                        time.sleep(1.0)
                        continue

                # Check the live game state BEFORE doing anything expensive
                # (capturing screenshots, calling the LLM). This avoids both:
                #   - sending a prompt while Simon is still flashing the
                #     sequence (model would act on stale screenshots)
                #   - sending a prompt when the game is already over (would
                #     trigger a useless click round before the timeout-based
                #     restart kicks in).
                state = self._check_game_state()
                if state == "game_over":
                    self._handle_game_over_and_restart()
                    continue
                if state != "your_turn":
                    logger.write(
                        f"State is '{state}' — Simon not done yet, waiting"
                    )
                    time.sleep(0.5)
                    continue

                screenshot_path = self.gm.capture_screen()
                logger.write(f"Screenshot: {screenshot_path}")

                self.memory.add_recent_history_kv(constants.CUR_SCREENSHOT_PATH, screenshot_path)
                self.memory.update_info_history({
                    constants.CUR_SCREENSHOT_PATH: screenshot_path,
                    constants.START_FRAME_ID: self.count_turns,
                    constants.END_FRAME_ID: self.count_turns,
                })

                self.run_information_gathering(screenshot_path)

                if self.use_self_reflection:
                    self.run_self_reflection(screenshot_path)

                if self.use_task_inference:
                    self.run_task_inference(screenshot_path)

                self.run_action_planning(screenshot_path)

                self.execute_actions()

                self.memory.save()
                self.count_turns += 1

                if self.count_turns % config.checkpoint_interval == 0:
                    cp = os.path.join(self.checkpoint_path, f'checkpoint_{self.count_turns:06d}.json')
                    self.memory.save(cp)
                    logger.write(f"Checkpoint: {cp}")

                # NOTE: removed the legacy `count_turns >= max_turn_count`
                # stop. The runner stops based on self.max_failures (failures)
                # instead — see _handle_game_over_and_restart.

                time.sleep(0.5)

            except KeyboardInterrupt:
                logger.write("Ctrl+C detected — shutting down.")
                self.runner_shutdown()
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                self.runner_shutdown()
                raise

        self.runner_shutdown()

    def runner_shutdown(self):
        self.gm.cleanup_io()
        self.video_recorder.finish_capture()
        logger.write("Runner shutdown complete.")

    # ------------------------------------------------------------------
    # Planner stages
    # ------------------------------------------------------------------

    def run_information_gathering(self, screenshot_path: str):
        logger.write("Information gathering...")

        input_map = deepcopy(self.planner.information_gathering_.input_map)
        input_map[constants.TASK_DESCRIPTION] = self.task_description

        if input_map.get("image_introduction"):
            input_map["image_introduction"][0]["path"] = screenshot_path

        input_map["gather_information_configurations"] = {
            "frame_extractor": False,
            "llm_description": True,
            "object_detector": False,
            "icon_replacer": False,
        }

        try:
            data = self.planner.information_gathering_(input=input_map)
            res = data.get("res_dict", {})
            image_description = (
                res.get("description", "")
                or res.get("image_description", "")
                or res.get("game_state", "")
            )
            self.memory.update_info_history({
                "image_description": image_description,
                "gathered_information": res,
            })
            logger.write(f"Scene: {image_description[:120]}...")
        except Exception as e:
            logger.warn(f"Information gathering failed: {e}")

    def run_self_reflection(self, screenshot_path: str):
        logger.write("Self-reflection...")

        params = self.memory.working_area.copy()
        input_map = deepcopy(self.planner.self_reflection_.input_map)

        input_map[constants.TASK_DESCRIPTION] = self.task_description
        input_map["previous_action_call"] = params.get(constants.PRE_ACTION, "")
        input_map["key_reason_of_last_action"] = params.get("key_reason_of_last_action", "")
        input_map["executing_action_error"] = params.get(constants.ACTION_ERROR, "")
        input_map["success_detection"] = ""
        input_map["skill_library"] = json.dumps(self.skill_library, ensure_ascii=False)

        if input_map.get("image_introduction") and len(input_map["image_introduction"]) >= 2:
            prev_path = params.get("prev_screenshot_path", screenshot_path)
            input_map["image_introduction"][0]["path"] = prev_path
            input_map["image_introduction"][1]["path"] = screenshot_path
        elif input_map.get("image_introduction"):
            input_map["image_introduction"][0]["path"] = screenshot_path

        try:
            data = self.planner.self_reflection_(input=input_map)
            res = data.get("res_dict", {})
            reflection = res.get("self_reflection_reasoning", "")
            self.memory.update_info_history({
                constants.PRE_SELF_REFLECTION_REASONING: reflection,
                "self_reflection_reasoning": reflection,
            })
            logger.write(f"Reflection: {reflection[:120]}...")
        except Exception as e:
            logger.warn(f"Self-reflection failed: {e}")

    def run_task_inference(self, screenshot_path: str):
        logger.write("Task inference...")

        params = self.memory.working_area.copy()
        input_map = deepcopy(self.planner.task_inference_.input_map)

        input_map[constants.TASK_DESCRIPTION] = self.task_description
        input_map["subtask_description"] = params.get("subtask_description", "")
        input_map["subtask_reasoning"] = params.get("subtask_reasoning", "")
        input_map["previous_action"] = params.get(constants.PRE_ACTION, "")
        input_map["executing_action_error"] = params.get(constants.ACTION_ERROR, "")
        input_map["previous_reasoning"] = params.get(constants.PRE_DECISION_MAKING_REASONING, "")
        input_map["self_reflection_reasoning"] = params.get("self_reflection_reasoning", "")
        input_map["success_detection"] = ""
        input_map["previous_summarization"] = params.get(constants.SUMMARIZATION, "")

        if input_map.get("image_introduction"):
            input_map["image_introduction"][0]["path"] = screenshot_path

        try:
            data = self.planner.task_inference_(input=input_map)
            res = data.get("res_dict", {})
            subtask = res.get("subtask_description", "")
            summary = res.get("history_summary", "")
            self.memory.update_info_history({
                "subtask_description": subtask,
                "subtask_reasoning": res.get("subtask_reasoning", ""),
                constants.SUMMARIZATION: summary,
            })
            logger.write(f"Subtask: {subtask}")
        except Exception as e:
            logger.warn(f"Task inference failed: {e}")

    def run_action_planning(self, screenshot_path: str):
        logger.write("Action planning...")

        params = self.memory.working_area.copy()
        previous_action = params.get(constants.PRE_ACTION, "")
        if isinstance(previous_action, list):
            previous_action = ", ".join(str(a) for a in previous_action)

        # Pull all per-round screenshots the GAME has written so far.
        # level_01_sequence.png shows the 1st button in the sequence highlighted,
        # level_02_sequence.png shows the 2nd, etc. The model must output a click
        # for each one, in order.
        sequence_files = self._list_sequence_screenshots()
        logger.write(f"Sequence screenshots available: {len(sequence_files)}")

        if not sequence_files:
            logger.warn("Action planner called with 0 sequence screenshots — skipping LLM call (game state likely changed mid-step)")
            self.memory.update_info_history({constants.SKILL_STEPS: []})
            return

        # Folder where we save the EXACT cropped JPEGs we send to the model,
        # so what you inspect on disk matches what the model actually saw.
        dump_dir = os.path.join(config.work_dir, "sequence_screenshots")
        try:
            os.makedirs(dump_dir, exist_ok=True)
            logger.write(f"Sequence dump dir: {os.path.abspath(dump_dir)}")
        except Exception as e:
            logger.error(f"Could not create sequence dump dir {dump_dir}: {e}")
            dump_dir = None

        def _encode_and_save(path: str, save_as: Optional[str], padding: int = 20) -> Optional[str]:
            """Open an image, crop tightly to the colored-buttons region (cuts
            out the dark background that wastes tokens), re-encode as JPEG,
            save those EXACT JPEG bytes to `save_as` (if given), and return
            a data URL ready for the OpenAI API.

            The bytes written to `save_as` are byte-for-byte the bytes sent
            to the model. Crop falls back to the full image if no button
            pixels are found.
            """
            try:
                import base64 as _b64
                import io
                import numpy as np
                from PIL import Image
                img = Image.open(path)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                arr = np.array(img)
                H, W = arr.shape[:2]

                # Pure button fill colors (normal + highlighted) from Game.py
                button_colors = [
                    (255,   0,   0),  # red
                    (  0,   0, 255),  # blue
                    (255, 255,   0),  # yellow
                    (  0, 128,   0),  # green (tk 'green')
                    (255, 102, 102),  # red highlighted
                    (102, 102, 255),  # blue highlighted
                    (255, 255, 102),  # yellow highlighted
                    (102, 255, 102),  # green highlighted
                ]
                mask = np.zeros((H, W), dtype=bool)
                for r, g, b in button_colors:
                    mask |= (
                        (arr[:, :, 0] == r) &
                        (arr[:, :, 1] == g) &
                        (arr[:, :, 2] == b)
                    )

                ys, xs = np.where(mask)
                if len(xs) == 0:
                    cropped = img
                else:
                    x0 = max(0, int(xs.min()) - padding)
                    y0 = max(0, int(ys.min()) - padding)
                    x1 = min(W, int(xs.max()) + padding + 1)
                    y1 = min(H, int(ys.max()) + padding + 1)
                    cropped = img.crop((x0, y0, x1, y1))

                buf = io.BytesIO()
                cropped.save(buf, format="JPEG", quality=90)
                jpeg_bytes = buf.getvalue()
                if save_as:
                    try:
                        with open(save_as, "wb") as f:
                            f.write(jpeg_bytes)
                        logger.write(
                            f"Saved exact image sent to model: "
                            f"{os.path.abspath(save_as)} "
                            f"({len(jpeg_bytes)} bytes, {cropped.size[0]}x{cropped.size[1]})"
                        )
                    except Exception as e:
                        logger.error(f"Could not save {save_as}: {e}")
                b64_str = _b64.b64encode(jpeg_bytes).decode("utf-8")
                return f"data:image/jpeg;base64,{b64_str}"
            except Exception as e:
                logger.warn(f"Could not encode {path}: {e}")
                return None

        # We deliberately do NOT include the live screenshot — only the
        # per-step sequence screenshots Game.py wrote. The live shot was
        # confusing the model (e.g. the title "SIMON SAYS" and other text
        # noise) and isn't needed since restart is handled programmatically.
        sequence_imgs = []
        for path in sequence_files:
            m = re.search(r"level_(\d+)_sequence\.png$", os.path.basename(path))
            step = int(m.group(1)) if m else 0
            save_as = (
                os.path.join(dump_dir, f"turn_{self.count_turns:03d}_step_{step:02d}.jpg")
                if dump_dir else None
            )
            sequence_imgs.append(
                (os.path.basename(path), _encode_and_save(path, save_as=save_as))
            )
        if sequence_files and dump_dir:
            logger.write(
                f"=> {len(sequence_files)} sent image(s) for turn {self.count_turns} "
                f"are in {os.path.abspath(dump_dir)}"
            )

        n_seq = len(sequence_files)
        sequence_block = (
            f"Simon has shown a {n_seq}-step sequence. The screenshots below are labeled "
            f"in order (step 1, step 2, ...). In EACH screenshot, exactly ONE of the four "
            f"colored buttons has a BLACK FILLED CIRCLE (●) drawn on top of it — that is "
            f"the button being pressed for that step. The other three buttons have nothing "
            f"on them. Identify which colored button (red / blue / yellow / green) has the "
            f"black circle in each screenshot, and output ONE click(color=...) per step in "
            f"the SAME order.\n\n"
            f"You MUST output exactly {n_seq} click() calls — no more, no fewer."
        )

        intro_text = (
            f"Task: {self.task_description}\n\n"
            f"Last action: {previous_action or 'None'}\n\n"
            "You are playing Simon Says. Four colored buttons in a 2x2 grid:\n"
            "  red (top-left), blue (top-right), yellow (bottom-left), green (bottom-right).\n\n"
            f"{sequence_block}\n\n"
            "Available skill:\n"
            "  click(color=\"red\"|\"blue\"|\"yellow\"|\"green\")  — press a grid button\n\n"
            "The screenshots showing each highlighted button follow:"
        )

        trailing_text = (
            "\nRespond in this EXACT format:\n\n"
            "Decision_Making_Reasoning:\n"
            "<one sentence describing the sequence you see and what you will click>\n\n"
            "Actions:\n"
            "```python\n"
            "click(color=\"<first>\")\n"
            "click(color=\"<second>\")\n"
            "...\n"
            "```\n\n"
            "Key_reason_of_last_action:\n"
            "<one sentence>"
        )

        messages = [
            {"role": "system", "content": (
                "You are an AI agent playing Simon Says. In each per-step screenshot, "
                "exactly one of the four colored buttons (red, blue, yellow, green) has a "
                "black filled circle (●) drawn on top of it — that is the button to press "
                "for that step. Identify the color of the button beneath the black circle "
                "in each screenshot and output one click(color=...) per step, in order. "
                "Do not invent extra clicks."
            )},
        ]
        user_content: List[Dict[str, Any]] = [{"type": "text", "text": intro_text}]
        for label, img in sequence_imgs:
            if img is None:
                continue
            user_content.append({"type": "text", "text": f"Sequence step from {label}:"})
            user_content.append({"type": "image_url", "image_url": {"url": img}})
        user_content.append({"type": "text", "text": trailing_text})
        messages.append({"role": "user", "content": user_content})

        print("\n" + "="*70)
        print(">>> VLM PROMPT (action planning)")
        print("="*70)
        for msg in messages:
            role = msg["role"].upper()
            content = msg["content"]
            if isinstance(content, str):
                print(f"[{role}]\n{content}")
            else:
                for part in content:
                    if part["type"] == "text":
                        print(f"[{role}]\n{part['text']}")
                    elif part["type"] == "image_url":
                        print(f"[{role}] <image: {len(part['image_url']['url'])} chars>")
        print("="*70 + "\n")

        try:
            raw_response, _info = self.llm_provider.create_completion(messages)
            raw_response = raw_response or ""
            logger.write(f"Raw LLM response: {raw_response[:500]}")

            from cradle.utils.json_utils import parse_semi_formatted_text
            res = parse_semi_formatted_text(raw_response)

            actions_val = res.get("actions", res.get("Actions", ""))
            if isinstance(actions_val, list):
                skill_steps = [
                    ln.strip() for ln in actions_val
                    if ln.strip() and re.match(r"^\w+\(", ln.strip())
                ]
                if not skill_steps:
                    skill_steps = self._parse_skill_steps("", raw_response)
            else:
                skill_steps = self._parse_skill_steps(actions_val, raw_response)

            valid_skills = {"click"}
            valid_colors = {"red", "blue", "yellow", "green"}

            def _skill_name(s):
                m = re.match(r"^(\w+)\(", s.strip())
                return m.group(1) if m else ""

            def _well_formed_click(s):
                """True only if s is a complete click(color="<one of 4>") call.
                Truncated LLM output like click(color="red gets dropped here
                so it can't pass the count check and then silently no-op in
                the executor."""
                m = re.match(
                    r'^\s*click\(\s*color\s*=\s*"(red|blue|yellow|green)"\s*\)\s*$',
                    s,
                )
                return m is not None

            def _action_ok(steps):
                if not steps:
                    return False, "no action produced"
                for p in steps:
                    if _skill_name(p) not in valid_skills:
                        return False, f"'{p}' is not a valid skill (allowed: click)"
                    if not _well_formed_click(p):
                        return False, (
                            f"'{p}' is not a well-formed click — must be exactly "
                            f"click(color=\"red|blue|yellow|green\")"
                        )
                expected = len(self._list_sequence_screenshots())
                click_steps = [p for p in steps if _well_formed_click(p)]
                if expected > 0 and len(click_steps) != expected:
                    return False, (
                        f"expected exactly {expected} click() calls (one per sequence step) "
                        f"but got {len(click_steps)}"
                    )
                return True, ""

            ok, reason = _action_ok(skill_steps)
            for _attempt in range(2):
                if ok:
                    break
                logger.write(f"Action rejected ({reason}) — correction attempt {_attempt+1}")
                messages.append({"role": "assistant", "content": raw_response})
                messages.append({"role": "user", "content": (
                    f"ERROR: {reason}.\n"
                    "Output EXACTLY one of: click(color=\"red|blue|yellow|green\"), "
                    "wait(duration=<seconds>), or screenshot()."
                )})
                raw_response, _ = self.llm_provider.create_completion(messages)
                raw_response = raw_response or ""
                logger.write(f"Retry {_attempt+1} response: {raw_response[:300]}")
                res = parse_semi_formatted_text(raw_response)
                actions_val = res.get("actions", res.get("Actions", ""))
                if isinstance(actions_val, list):
                    skill_steps = [
                        ln.strip() for ln in actions_val
                        if ln.strip() and re.match(r"^\w+\(", ln.strip())
                    ]
                    if not skill_steps:
                        skill_steps = self._parse_skill_steps("", raw_response)
                else:
                    skill_steps = self._parse_skill_steps(actions_val, raw_response)
                ok, reason = _action_ok(skill_steps)

            if not ok:
                # Retries exhausted with malformed/wrong-count steps. Sending
                # a partial sequence (e.g. 10 of 11 expected) leaves the game
                # stuck in your_turn and traps the runner in a START-click
                # loop. Force the empty-action recovery path instead.
                logger.warn(
                    f"Action planning still invalid after retries ({reason}) — "
                    f"discarding steps and letting empty-action recovery handle it"
                )
                skill_steps = []

            reasoning = res.get("decision_making_reasoning", res.get("Decision_Making_Reasoning", ""))
            key_reason = res.get("key_reason_of_last_action", res.get("Key_reason_of_last_action", ""))

            self.memory.update_info_history({
                "action_plan": res,
                constants.SKILL_STEPS: skill_steps,
                constants.PRE_DECISION_MAKING_REASONING: reasoning,
                "key_reason_of_last_action": key_reason,
                "prev_screenshot_path": screenshot_path,
            })
            logger.write(f"Planned steps: {skill_steps}")

        except Exception as e:
            logger.error(f"Action planning failed: {e}")
            self.memory.update_info_history({constants.SKILL_STEPS: []})

    def execute_actions(self):
        logger.write("Executing actions...")
        try:
            pre_count = len(self._list_sequence_screenshots())
            result = self.skill_execute()
            pre_action = result.get(constants.PRE_ACTION, [])
            self.memory.update_info_history({constants.PRE_ACTION: pre_action})
            logger.write(f"Executed: {pre_action}")

            if not pre_action or (isinstance(pre_action, list) and not any(pre_action)):
                self.count_empty_action += 1
                if self.count_empty_action >= self.count_empty_action_threshold:
                    logger.warn(f"Reached empty-action threshold ({self.count_empty_action_threshold}) — recovering instead of shutting down")
                    self.count_empty_action = 0
                    try:
                        state = self._check_game_state()
                        if state != "game_over":
                            # START is disabled while the game is active, so we
                            # need to end the current game first. Click grid
                            # colors until player_click() triggers game_over.
                            self._force_game_over_by_wrong_click()
                        self._handle_game_over_and_restart()
                    except Exception as e:
                        logger.error(f"Recovery after empty-action threshold failed: {e}")
                return

            self.count_empty_action = 0

            click_steps = [
                s for s in (pre_action if isinstance(pre_action, list) else [pre_action])
                if isinstance(s, str) and s.lstrip().startswith("click(")
            ]
            if not click_steps:
                return

            got_new = self._wait_for_new_sequence_screenshot(prev_count=pre_count)
            if not got_new:
                # No new screenshot after a click sequence → game over.
                # Game.py shows "Game Over! Final Level: N" but doesn't
                # save any new level_*.png — the timeout is our signal.
                self._handle_game_over_and_restart()
        except Exception as e:
            logger.error(f"Execution failed: {e}")

    def _wait_for_new_sequence_screenshot(
        self, prev_count: int, timeout: float = None
    ) -> bool:
        """Poll the screenshots/ folder until a new level_NN_sequence.png appears.

        If `timeout` is None, scale it with the level Simon is about to show:
        Game.py spends roughly 0.8s per flashed color (0.3s circle + 0.5s gap),
        plus 0.3s for the final highlight, plus 1.5s inter-round delay. At
        level 13+ a fixed 10s timeout fires before Simon finishes, which
        causes false game-over restarts.

        Returns True if a new screenshot appeared before the timeout.
        """
        if timeout is None:
            next_level = prev_count + 1
            timeout = max(10.0, 1.5 + next_level * 0.8 + 0.3 + 3.0)
            logger.write(
                f"Waiting up to {timeout:.1f}s for level {next_level} screenshot "
                f"(scaled to sequence length)"
            )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self._list_sequence_screenshots()) > prev_count:
                elapsed = timeout - (deadline - time.time())
                logger.write(f"New sequence screenshot detected after {elapsed:.1f}s")
                # Tiny extra pause so the file is fully written by the time we read it.
                time.sleep(0.3)
                return True
            time.sleep(0.2)
        logger.warn(
            f"Timed out after {timeout:.1f}s waiting for new sequence screenshot."
        )
        return False

    def _force_game_over_by_wrong_click(self) -> bool:
        """Click grid colors until Game.py transitions to game_over.

        Used when the model produced 0 valid clicks mid-game: START is disabled
        while game_active=True, so we deliberately submit clicks to make
        player_click() fail and flip the state. Cycles through up to 4 distinct
        colors — the sequence cannot match all 4 in a row at the same position,
        so this is guaranteed to trigger game_over within a few clicks.
        """
        try:
            from cradle.environment.simon_says.skill_registry import BasicClick
        except Exception as e:
            logger.warn(f"_force_game_over_by_wrong_click: cannot import BasicClick: {e}")
            return False

        for color in ("red", "blue", "yellow", "green"):
            try:
                BasicClick.execute(color=color)
            except Exception as e:
                logger.warn(f"_force_game_over_by_wrong_click: click '{color}' failed: {e}")
                continue
            time.sleep(0.3)
            if self._check_game_state() == "game_over":
                logger.write(f"_force_game_over_by_wrong_click: game_over after '{color}'")
                return True
        logger.warn("_force_game_over_by_wrong_click: 4 clicks did not produce game_over")
        return False

    def _handle_game_over_and_restart(self):
        """Detected game over — log details, wipe stale screenshots, restart."""
        self.failures_count += 1
        logger.write(
            "=" * 40 + "\n"
            f"GAME OVER detected (failure {self.failures_count} of {self.max_failures}).\n"
            + "=" * 40
        )
        self._log_failure_to_run_folder()

        if self.failures_count >= self.max_failures:
            logger.write(
                f"Reached max_failures={self.max_failures} — stopping runner. "
                f"See failures.txt for the full record."
            )
            self.stop_flag = True
            return

        logger.write("Wiping stale screenshots and restarting...")
        self._clear_sequence_screenshots()
        # Game.py's start_game() early-returns when game_active=True. If we
        # got here via the screenshot-timeout path (e.g. the executor dropped
        # a malformed click and the game is still mid-your_turn), START will
        # silently do nothing. Force a real game_over first.
        state = self._check_game_state()
        if state != "game_over":
            logger.write(f"Pre-restart state is '{state}' — forcing real game_over before clicking START")
            self._force_game_over_by_wrong_click()
        # Brief pause so Game.py finishes rendering the "Game Over!" overlay
        # and re-enabling the START button before we click it.
        time.sleep(1.0)
        if not self._click_start_button():
            logger.warn("Could not auto-restart: bright-green START not found on screen.")
            return
        # Wait for Simon to flash the first sequence of the new game.
        self._wait_for_new_sequence_screenshot(prev_count=0)

    def _log_failure_to_run_folder(self):
        """Append a structured failure entry to runs/<ts>/failures.txt.

        Reads Game.py's screenshots/last_failure.txt which contains the
        intended sequence and the player's actual clicks at the moment of
        game over, then writes a human-readable record into the run folder.
        """
        try:
            from cradle.utils.file_utils import assemble_project_path
            from datetime import datetime
            src = assemble_project_path(
                os.path.join(self.SEQUENCE_DIR, "last_failure.txt")
            )
            if not os.path.exists(src):
                logger.warn(f"_log_failure: no failure file at {src}")
                return
            with open(src, "r", encoding="utf-8") as f:
                content = f.read().strip()
            info = {}
            for line in content.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k.strip()] = v.strip()

            level = info.get("level", "?")
            intended = [s for s in info.get("intended", "").split(",") if s]
            player = [s for s in info.get("player", "").split(",") if s]

            # Find first divergence
            failed_step = None
            for i, (a, b) in enumerate(zip(intended, player)):
                if a != b:
                    failed_step = i + 1
                    break
            if failed_step is None and len(player) < len(intended):
                # Player gave up partway (we count it as failing on the next expected step)
                failed_step = len(player) + 1

            dst = os.path.join(config.work_dir, "failures.txt")
            with open(dst, "a", encoding="utf-8") as f:
                f.write(f"\n--- Failure at {datetime.now().isoformat(timespec='seconds')} ---\n")
                f.write(f"Level: {level}\n")
                f.write(f"Correct sequence ({len(intended)}): {', '.join(intended)}\n")
                f.write(f"Model's sequence ({len(player)}): {', '.join(player)}\n")
                if failed_step is not None and failed_step <= len(intended):
                    got = player[failed_step - 1] if failed_step - 1 < len(player) else "(none)"
                    want = intended[failed_step - 1]
                    f.write(
                        f"Failed at step {failed_step}: "
                        f"clicked '{got}', should have been '{want}'\n"
                    )
            logger.write(f"Logged failure to {dst}")
        except Exception as e:
            logger.warn(f"Could not log failure: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_skill_steps(self, actions_raw: str, full_response: str = "") -> list:
        known_skills = set(self.skill_registry.skills.keys()) if hasattr(self, 'skill_registry') else set()

        def extract_calls(text):
            text = re.sub(r"```(?:python)?\s*", "", text).replace("```", "").strip()
            steps = []
            for line in text.splitlines():
                line = line.strip()
                if line and re.match(r"^\w+\(", line) and not line.startswith("#"):
                    steps.append(line)
            return steps

        if actions_raw:
            steps = extract_calls(actions_raw)
            if steps:
                return steps

        if full_response:
            for line in full_response.splitlines():
                line = line.strip()
                if re.match(r"^\w+\(", line) and not line.startswith("#"):
                    skill_name = re.match(r"^(\w+)\(", line).group(1)
                    if not known_skills or skill_name in known_skills:
                        return [line]

        return []


def entry(args):
    """Entry point called by runner.py."""
    import platform
    if platform.system() == "Linux":
        os.environ.setdefault("DISPLAY", ":0")

    llm_config_path = args.llmProviderConfig
    embed_config_path = args.embedProviderConfig

    task_description_list = getattr(config, "task_description_list", None) or \
        config.env_config.get("task_description_list", [])
    if task_description_list:
        task_description = task_description_list[0].get(
            constants.TASK_DESCRIPTION, "Play Simon Says"
        )
    else:
        task_description = "Play Simon Says"

    runner = SimonSaysRunner(
        llm_provider_config_path=llm_config_path,
        embed_provider_config_path=embed_config_path,
        task_description=task_description,
        use_self_reflection=getattr(config, "use_self_reflection", False),
        use_task_inference=getattr(config, "use_task_inference", False),
    )
    runner.run()
