"""VLM runner for pygame games — screenshot every turn, VLM decides which key to press."""

import base64
import os
import re
import subprocess
import time
from typing import Optional

from cradle.config import Config
from cradle.log import Logger
from cradle.provider.llm.llm_factory import LLMFactory
from cradle.environment.skill_registry_factory import SkillRegistryFactory
from cradle.environment.ui_control_factory import UIControlFactory
from cradle.gameio.game_manager import GameManager
from cradle.provider.video.video_recorder import VideoRecordProvider
from cradle.module.executor import Executor

config = Config()
logger = Logger()

_XENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}


def _xdotool(*args):
    subprocess.run(["xdotool", *[str(a) for a in args]],
                   env=_XENV, capture_output=True)


class PyGameRunner:
    """Generic VLM runner for pygame games.

    Each turn:
      1. Take a screenshot of the game window.
      2. Ask the VLM what action to take.
      3. Execute the action.
    """

    def __init__(self,
                 llm_provider_config_path: str,
                 embed_provider_config_path: str,
                 task_description: str):

        self.llm_provider_config_path = llm_provider_config_path
        self.embed_provider_config_path = embed_provider_config_path
        self.task_description = task_description

        self.stop_flag = False
        self.count_turns = 0
        self.score = 0
        self.action_history = []   # last N (action, outcome_description) pairs

        self._init_components()

    def _init_components(self):
        lf = LLMFactory()
        self.llm_provider, self.embed_provider = lf.create(
            self.llm_provider_config_path,
            self.embed_provider_config_path,
        )
        logger.write("LLM provider initialised")

        srf = SkillRegistryFactory()
        srf.register_builder(config.env_short_name, config.skill_registry_name)
        self.skill_registry = srf.create(
            config.env_short_name,
            skill_configs=config.skill_configs,
            embedding_provider=self.embed_provider,
        )

        ucf = UIControlFactory()
        ucf.register_builder(config.env_short_name, config.ui_control_name)
        self.ui_control = ucf.create(config.env_short_name)

        self.gm = GameManager(
            env_name=config.env_name,
            embedding_provider=self.embed_provider,
            llm_provider=self.llm_provider,
            skill_registry=self.skill_registry,
            ui_control=self.ui_control,
        )

        self.video_recorder = VideoRecordProvider(
            os.path.join(config.work_dir, "video.mp4")
        )
        self.executor = Executor(env_manager=self.gm)

        # Apply game_window override from env config
        gw = config.env_config.get("game_window")
        if gw:
            config.env_window.left   = gw["left"]
            config.env_window.top    = gw["top"]
            config.env_window.width  = gw["width"]
            config.env_window.height = gw["height"]

        # Override max_turn_count from env config if provided
        max_turns = config.env_config.get("max_turn_count")
        if max_turns is not None:
            config.max_turn_count = int(max_turns)

        # How long to wait after pressing a key (lets the game render the new heart)
        self._post_action_delay = float(config.env_config.get("post_action_delay", 0.5))
        # Minimum wall-clock seconds per turn (safety floor)
        self._min_turn_time = float(config.env_config.get("min_turn_time", 1.0))

        # Clear the per-session score log so we only count this run's games
        self._score_log = os.path.join(
            config.env_config.get("game_path", ""), "other-files", "score-log.txt"
        )
        try:
            open(self._score_log, "w").close()
        except Exception:
            pass

        logger.write("PyGameRunner ready")

    # ------------------------------------------------------------------

    def run(self):
        logger.write(f"Starting pygame runner | task: {self.task_description}")

        if config.enable_videocapture:
            self.video_recorder.start_capture()

        while not self.stop_flag:
            try:
                turn_start = time.time()

                print(f"\n{'='*60}")
                print(f"  Turn {self.count_turns}")
                print(f"{'='*60}")

                screenshot_path = self.gm.capture_screen()
                logger.write(f"Screenshot: {screenshot_path}")

                action = self._plan_action(screenshot_path)
                self._execute(action)

                # Let the game render the new state before screenshotting
                time.sleep(self._post_action_delay)

                self.count_turns += 1
                if self.count_turns >= config.max_turn_count:
                    self.stop_flag = True
                    logger.warn(f"Max turns reached: {config.max_turn_count}")

                # Never loop faster than min_turn_time even if VLM fails
                elapsed = time.time() - turn_start
                if elapsed < self._min_turn_time:
                    time.sleep(self._min_turn_time - elapsed)

            except KeyboardInterrupt:
                logger.write("Ctrl+C — shutting down.")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                raise

        self.video_recorder.finish_capture()
        logger.write("Runner shutdown.")
        self._report_score()

    # ------------------------------------------------------------------

    def _report_score(self):
        """Read and display the final high score and per-session average."""
        game_path = config.env_config.get("game_path", "")

        # High score from the game's persistent save file
        save_file = os.path.join(game_path, "other-files", "save-data.txt")
        try:
            with open(save_file) as f:
                high_score = f.read().strip()
        except Exception:
            high_score = "unknown"

        # Per-session scores written by lives_gone() during this run
        scores = []
        try:
            with open(self._score_log) as f:
                scores = [int(l.strip()) for l in f if l.strip()]
        except Exception:
            pass

        lines = [
            "",
            "=" * 60,
            f"  Run complete — {self.count_turns} turns",
            f"  HIGH SCORE : {high_score}",
        ]
        if scores:
            avg = sum(scores) / len(scores)
            lines += [
                f"  Games played : {len(scores)}",
                f"  Scores       : {scores}",
                f"  Average score: {avg:.1f}",
            ]
        else:
            lines.append("  (no completed games in this run)")
        lines.append("=" * 60)

        msg = "\n".join(lines)
        print(msg)
        logger.write(msg)

    def _plan_action(self, screenshot_path: str) -> str:
        """Ask the VLM what to do and return a skill call string."""

        # Encode screenshot
        try:
            with open(screenshot_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = screenshot_path.rsplit(".", 1)[-1].lower()
            image_url = f"data:image/{ext};base64,{b64}"
        except Exception as e:
            logger.warn(f"Could not encode screenshot: {e}")
            image_url = None

        user_text = (
            "Look at the screenshot carefully.\n\n"
            "There are TWO possible screens:\n\n"
            "MENU SCREEN — you are on the menu if you see:\n"
            "  - A large green heart logo with 'I'LL COP YOUR HEART' text\n"
            "  - Text saying 'Press the up arrow to play'\n"
            "  - A score like 'Most Hearts Copped: X'\n"
            "  → Call press_start()\n\n"
            "GAMEPLAY SCREEN — you are in-game if you see:\n"
            "  - A character in the centre with four hearts around it (above, below, left, right)\n"
            "  - One heart is RED (the one to steal), the others are white/pink\n"
            "  - A live counter and score at the top\n"
            "  → Call press_key with the direction of the RED heart:\n"
            "      press_key(key=\"up\")    — red heart is ABOVE\n"
            "      press_key(key=\"down\")  — red heart is BELOW\n"
            "      press_key(key=\"left\")  — red heart is LEFT\n"
            "      press_key(key=\"right\") — red heart is RIGHT\n\n"
            "Before answering, work through these steps:\n"
            "Step 1: Is this the menu screen or the gameplay screen?\n"
            "Step 2: If gameplay — what colour is the heart ABOVE? BELOW? LEFT? RIGHT?\n"
            "Step 3: Which heart is RED?\n"
            "Step 4: What is the correct action?\n\n"
            "Respond in this format:\n"
            "Step 1: <menu or gameplay>\n"
            "Step 2: If menu → N/A. If gameplay → above=<colour>, below=<colour>, left=<colour>, right=<colour>\n"
            "Step 3: If menu → N/A. If gameplay → <direction> heart is red\n"
            "Step 4: (choose exactly one)\n"
            "  If menu screen:\n"
            "```python\n"
            "press_start()\n"
            "```\n"
            "  If gameplay screen:\n"
            "```python\n"
            "press_key(key=\"???\")  # fill in: up, down, left, or right — based on Step 3\n"
            "```\n\n"
            "IMPORTANT: On the menu screen call press_start(), never press_key(). "
            "On the gameplay screen replace ??? with the actual direction from Step 3."
        )

        user_content = []
        if image_url:
            user_content.append({"type": "image_url", "image_url": {"url": image_url}})
        user_content.append({"type": "text", "text": user_text})

        messages = [
            {"role": "system", "content": (
                "You are an AI agent playing a reaction game called 'I'll Cop Your Heart'. "
                "Each turn a single heart turns red — press the arrow key matching its position "
                "(up/down/left/right relative to the central character). "
                "The red heart can be in ANY of the four positions each turn. "
                "Look carefully at the screenshot to determine which one is red THIS turn. "
                "Output exactly ONE action."
            )},
            {"role": "user", "content": user_content},
        ]

        # Print prompt
        print("\n>>> VLM PROMPT")
        print(user_text)
        print("<<<\n")

        try:
            raw, _ = self.llm_provider.create_completion(messages)
            raw = raw or ""
            logger.write(f"VLM response: {raw[:300]}")
            print(f"VLM: {raw[:300]}")
            action = self._parse_action(raw)
        except Exception as e:
            logger.error(f"VLM call failed: {e}")
            action = "press_start()"

        return action

    def _parse_action(self, raw: str) -> str:
        """Extract the skill call from raw VLM output."""
        # Look inside a code block first
        blocks = re.findall(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
        for block in blocks:
            for line in block.strip().splitlines():
                line = line.strip()
                if re.match(r"^(press_key|press_start)\(", line) and not line.startswith("#"):
                    return line

        # Fallback: scan all lines, match anywhere on the line
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            m = re.search(r"(press_key\(key=[\"']\w+[\"']\)|press_start\(\))", line)
            if m:
                return m.group(1)

        logger.warn("No valid action parsed — defaulting to press_start()")
        return "press_start()"

    def _execute(self, action: str):
        """Execute a skill call string directly."""
        logger.write(f"Executing: {action}")
        print(f"  ACTION: {action}")

        # Match press_key(key="...")
        m = re.match(r'press_key\(key=["\'](\w+)["\']\)', action)
        if m:
            key = m.group(1).lower()
            from cradle.environment.pygame_hearts.atomic_skills.hearts_skills import PressKey
            PressKey.execute(key)
            self.action_history.append(action)
            self.action_history = self.action_history[-12:]
            return

        # Match press_start()
        if re.match(r"press_start\(\)", action):
            from cradle.environment.pygame_hearts.atomic_skills.hearts_skills import PressStart
            PressStart.execute()
            self.action_history.append(action)
            self.action_history = self.action_history[-12:]
            return

        logger.warn(f"Unknown action: {action}")


# ------------------------------------------------------------------

def entry(args):
    """Entry point called by runner.py."""
    os.environ.setdefault("DISPLAY", ":0")

    task_description_list = config.env_config.get("task_description_list", [])
    task_description = (
        task_description_list[0].get("task_description", "Play the game")
        if task_description_list else "Play the game"
    )

    runner = PyGameRunner(
        llm_provider_config_path=args.llmProviderConfig,
        embed_provider_config_path=args.embedProviderConfig,
        task_description=task_description,
    )
    runner.run()
