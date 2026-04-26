"""ScummVM game runner for VLM-based game playing."""

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
import cradle.environment.scummvm

config = Config()
logger = Logger()
io_env = IOEnvironment()


class ScummVMRunner:
    """Main runner for ScummVM games."""

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
        self.action_history: List[str] = []        # rolling global history (last 10)
        self.room_action_history: Dict[str, List[str]] = {}  # per-room history
        self.current_room: Optional[str] = None    # room identifier from debugger

        self.set_internal_params()

    def set_internal_params(self):
        """Initialize all internal components."""
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
            use_task_inference=self.use_task_inference
        )
        logger.write("Planner initialized")

        skills = self.gm.retrieve_skills(
            query_task=self.task_description,
            skill_num=config.skill_configs[constants.SKILL_CONFIG_MAX_COUNT],
            screen_type=constants.GENERAL_GAME_INTERFACE
        )
        self.skill_library = self.gm.get_skill_information(
            skills,
            skill_library_with_code=config.skill_library_with_code
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

    def run(self):
        """Main execution loop."""
        logger.write(f"Starting ScummVM runner | task: {self.task_description}")

        working_memory = {
            constants.TASK_DESCRIPTION: self.task_description,
            constants.SKILL_LIBRARY: self.skill_library,
            constants.PRE_ACTION: constants.EMPTY_STRING,
            constants.PRE_DECISION_MAKING_REASONING: constants.EMPTY_STRING,
            constants.PRE_SELF_REFLECTION_REASONING: constants.EMPTY_STRING,
            constants.SUMMARIZATION: constants.EMPTY_STRING,
            # initialise keys expected by executor
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

                # 1a. Screenshot to capture any text response from the last action
                text_screenshot_path = self.gm.capture_screen()
                logger.write(f"Text screenshot: {text_screenshot_path}")

                # 1b. Dismiss any full-screen text overlay (press Enter/Space),
                #     then take a second screenshot showing the graphical scene.
                io_env.key_press('return')
                time.sleep(1.5)
                scene_screenshot_path = self.gm.capture_screen()
                logger.write(f"Scene screenshot: {scene_screenshot_path}")

                self.memory.add_recent_history_kv(constants.CUR_SCREENSHOT_PATH, scene_screenshot_path)
                self.memory.update_info_history({
                    constants.CUR_SCREENSHOT_PATH: scene_screenshot_path,
                    constants.START_FRAME_ID: self.count_turns,
                    constants.END_FRAME_ID: self.count_turns,
                })

                # 1c. Query ScummVM debugger for current room number
                room = self._get_current_room()
                if room is not None:
                    self.current_room = str(room)
                    logger.write(f"Current room: {self.current_room}")

                # 2. Information gathering: read the text response screenshot
                self.run_information_gathering(text_screenshot_path)

                # 3. Self-reflection (optional)
                if self.use_self_reflection:
                    self.run_self_reflection(scene_screenshot_path)

                # 4. Task inference (optional)
                if self.use_task_inference:
                    self.run_task_inference(scene_screenshot_path)

                # 5. Action planning: use the scene screenshot (graphics visible)
                self.run_action_planning(scene_screenshot_path)

                # 6. Execute
                self.execute_actions()

                self.memory.save()
                self.count_turns += 1

                if self.count_turns % config.checkpoint_interval == 0:
                    cp = os.path.join(self.checkpoint_path, f'checkpoint_{self.count_turns:06d}.json')
                    self.memory.save(cp)
                    logger.write(f"Checkpoint: {cp}")

                if self.count_turns >= config.max_turn_count:
                    self.stop_flag = True
                    logger.warn(f"Max turns reached: {config.max_turn_count}")

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

        # Set current screenshot in the image_introduction list
        if input_map.get("image_introduction"):
            input_map["image_introduction"][0]["path"] = screenshot_path

        # Tell the planner to use LLM description only (no video extractor, no DINO)
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

        # Two images: previous + current
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
        win_w = getattr(config.env_window, "width", 640)
        win_h = getattr(config.env_window, "height", 480)

        image_description = params.get("image_description", "")
        previous_action = params.get(constants.PRE_ACTION, "")
        if isinstance(previous_action, list):
            previous_action = ", ".join(str(a) for a in previous_action)

        VERB_LIST = (
            "Bait, Blow, Bounce, Break, Bridle, Calm, Call, Cast, Catch, Clean, "
            "Climb, Close, Command, Cross, Cure, Cut, Dance, Detach, Dig, Dismount, "
            "Dive, Dock, Drink, Drop, Eat, Enter, Exit, Feed, Find, Fish, Flip, "
            "Follow, Force, Free, Frighten, Give, Go, Help, Hide, Hit, Hug, Jump, "
            "Kill, Kiss, Knock, Lay, Lead, Leave, Lie, Lift, Light, Lock, Look, "
            "Make, Move, Open, Pet, Play, Polish, Pry, Pull, Push, Put, Raise, "
            "Read, Remove, Ride, Rock, Say, Save, Shake, Shine, Shoot, Shout, "
            "Sing, Sit, Sleep, Smell, Speak, Stand, Start, Steal, Sweep, Swim, "
            "Swing, Take, Talk, Tempt, Throw, Tickle, Turn, Undress, Unlock, "
            "Untie, Use, Wade, Wake, Wave, Wear"
        )

        # Build forbidden-action block from room + global history
        room_key = self.current_room or "unknown"
        room_history = self.room_action_history.get(room_key, [])

        # Merge room history + full global history, deduplicated, preserving order
        seen = set()
        forbidden_lines = []
        for a in room_history + self.action_history:
            if a not in seen:
                seen.add(a)
                forbidden_lines.append(a)

        if forbidden_lines:
            forbidden_list_str = "\n".join(f"  • {a}" for a in forbidden_lines)
            forbidden_block = (
                f"\n⛔ ALREADY TRIED — do NOT repeat these:\n{forbidden_list_str}\n"
            )
            # Closing reminder placed right before the model's output slot
            forbidden_reminder = (
                f"\n⛔ REMINDER: The following actions are BANNED — outputting any of them "
                f"is an error. You MUST choose something not in this list:\n"
                f"{forbidden_list_str}\n"
            )
        else:
            forbidden_block = ""
            forbidden_reminder = ""

        logger.write(f"Forbidden actions ({len(forbidden_lines)}): {forbidden_lines}")

        user_text = (
            f"Task: {self.task_description}\n\n"
            f"=== GAME RESPONSE / CURRENT SCENE ===\n{image_description}\n"
            f"====================================={forbidden_block}\n"
            f"Last action taken: {previous_action or 'None'}\n"
            f"Game window: {win_w}x{win_h} pixels. "
            f"(0,0)=TOP-LEFT, ({win_w},{win_h})=BOTTOM-RIGHT.\n\n"
            "You have TWO types of actions:\n\n"
            "1. TYPE COMMAND — use a verb from the list with a noun you see on screen:\n"
            "   type_command(command=\"VERB NOUN\")\n\n"
            f"   Valid verbs: {VERB_LIST}\n\n"
            "   Choose the noun from what you actually see in the screenshot "
            "(objects, characters, exits, items). Do NOT reuse a command you have already tried.\n\n"
            "2. MOVE — click a specific pixel in the screenshot to walk there:\n"
            "   move_to(x=X, y=Y)\n"
            "   Replace X and Y with actual pixel coordinates of something you can see "
            "in the image. Do NOT use placeholder values.\n\n"
            f"{forbidden_reminder}"
            "Respond in this EXACT format:\n\n"
            "Decision_Making_Reasoning:\n"
            "<one sentence: what you see and what NEW action you will take>\n\n"
            "Actions:\n"
            "```python\n"
            "type_command(command=\"verb noun\")\n"
            "```\n\n"
            "Key_reason_of_last_action:\n"
            "<one sentence: goal of this action>"
        )

        # Encode screenshot directly to base64
        try:
            import base64 as _b64
            with open(screenshot_path, "rb") as f:
                raw = f.read()
            b64_str = _b64.b64encode(raw).decode("utf-8")
            ext = screenshot_path.rsplit(".", 1)[-1].lower()
            image_url = f"data:image/{ext};base64,{b64_str}"
            logger.write(f"Action planning screenshot encoded: {len(raw)} bytes from {screenshot_path}")
        except Exception as e:
            logger.warn(f"Could not encode screenshot {screenshot_path}: {e}")
            image_url = None

        # Build messages directly — guaranteed to include the image
        messages = [
            {"role": "system", "content": (
                "You are an AI agent playing King's Quest IV, a text-parser adventure game in ScummVM. "
                "You control Rosella — the young woman in the red dress with blonde hair on screen. "
                "Each turn output exactly ONE action: move_to(x,y) or type_command(command=\"verb noun\"). "
                "ABSOLUTE RULE: if the user message contains ⛔ lines listing banned actions, "
                "you MUST NOT output any action that matches those lines exactly. "
                "Violating this rule by repeating a banned action is always wrong. "
                "When 'look around' has been tried, use specific examine commands instead: "
                "'look at <object>', 'examine <object>', 'go north/south/east/west', 'take <item>'."
            )},
        ]
        user_content = [
            {"type": "text", "text": "Here is the current game screenshot:"},
        ]
        if image_url:
            user_content.append({"type": "image_url", "image_url": {"url": image_url}})
        user_content.append({"type": "text", "text": user_text})
        messages.append({"role": "user", "content": user_content})

        # Print full prompt to console so the user can inspect it
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
            raw_response, info = self.llm_provider.create_completion(messages)
            raw_response = raw_response or ""
            logger.write(f"Raw LLM response: {raw_response[:500]}")

            from cradle.utils.json_utils import parse_semi_formatted_text
            res = parse_semi_formatted_text(raw_response)

            # parse_semi_formatted_text already converts "actions" to a list of lines
            actions_val = res.get("actions", res.get("Actions", ""))
            if isinstance(actions_val, list):
                # Filter to lines that look like skill calls
                skill_steps = [
                    ln.strip() for ln in actions_val
                    if ln.strip() and re.match(r"^\w+\(", ln.strip())
                ]
                if not skill_steps:
                    skill_steps = self._parse_skill_steps("", raw_response)
            else:
                skill_steps = self._parse_skill_steps(actions_val, raw_response)

            # Code-level enforcement: reject banned or invalid actions and retry
            forbidden_set = set(forbidden_lines)
            valid_skills = {"move_to", "type_command"}

            def _norm_action(s):
                return re.sub(
                    r"move_to\(x=(\d+),\s*y=(\d+)\)",
                    lambda m: f"move_to(x={round(int(m.group(1))/50)*50}, y={round(int(m.group(2))/50)*50})",
                    s,
                )

            def _skill_name(s):
                m = re.match(r"^(\w+)\(", s.strip())
                return m.group(1) if m else ""

            def _is_placeholder(s):
                # Reject if the model output the template literally
                return "VERB NOUN" in s or 'command="X"' in s

            def _action_ok(steps):
                if not steps:
                    return False, "no action produced"
                p = steps[0]
                if _skill_name(p) not in valid_skills:
                    return False, f"'{p}' is not a valid skill (only type_command or move_to allowed)"
                if _is_placeholder(p):
                    return False, f"'{p}' is a placeholder — fill in real values"
                if _norm_action(p) in forbidden_set:
                    return False, f"'{p}' is banned"
                return True, ""

            ok, reason = _action_ok(skill_steps)
            for _attempt in range(3):
                if ok:
                    break
                logger.write(f"Action rejected ({reason}) — correction attempt {_attempt+1}")
                messages.append({"role": "assistant", "content": raw_response})
                messages.append({"role": "user", "content": (
                    f"ERROR: {reason}.\n"
                    f"You MUST output exactly ONE of these two action types:\n"
                    f"  type_command(command=\"verb noun\")  — where verb is from the list and noun is something you see\n"
                    f"  move_to(x=X, y=Y)  — where X and Y are real pixel coordinates from the screenshot\n"
                    f"Banned actions (do not repeat): {forbidden_lines}\n"
                    f"Output a valid, NEW action now."
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
        """Execute planned actions via the Executor (reads skill_steps from memory)."""
        logger.write("Executing actions...")
        try:
            result = self.skill_execute()
            pre_action = result.get(constants.PRE_ACTION, [])
            self.memory.update_info_history({constants.PRE_ACTION: pre_action})
            logger.write(f"Executed: {pre_action}")
            if pre_action:
                actions_str = pre_action if isinstance(pre_action, str) else ", ".join(str(a) for a in pre_action)
                if actions_str.strip():
                    # Normalize move_to coordinates to 50-pixel grid so nearby
                    # clicks are treated as the same action and get blocked.
                    normalized = re.sub(
                        r"move_to\(x=(\d+),\s*y=(\d+)\)",
                        lambda m: f"move_to(x={round(int(m.group(1))/50)*50}, y={round(int(m.group(2))/50)*50})",
                        actions_str,
                    )
                    self.action_history.append(normalized)
                    self.action_history = self.action_history[-20:]
                    # Per-room history (unbounded within a room)
                    room_key = self.current_room or "unknown"
                    self.room_action_history.setdefault(room_key, []).append(normalized)
        except Exception as e:
            logger.error(f"Execution failed: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_skill_steps(self, actions_raw: str, full_response: str = "") -> list:
        """Extract skill call strings from LLM output."""
        known_skills = set(self.skill_registry.skills.keys()) if hasattr(self, 'skill_registry') else set()

        def extract_calls(text):
            text = re.sub(r"```(?:python)?\s*", "", text).replace("```", "").strip()
            steps = []
            for line in text.splitlines():
                line = line.strip()
                if line and re.match(r"^\w+\(", line) and not line.startswith("#"):
                    steps.append(line)
            return steps

        # Try the dedicated actions field first
        if actions_raw:
            steps = extract_calls(actions_raw)
            if steps:
                return steps

        # Fallback: scan the full response for any known skill call
        if full_response:
            for line in full_response.splitlines():
                line = line.strip()
                if re.match(r"^\w+\(", line) and not line.startswith("#"):
                    skill_name = re.match(r"^(\w+)\(", line).group(1)
                    if not known_skills or skill_name in known_skills:
                        return [line]

        return []

    def _get_current_room(self) -> Optional[int]:
        """Read the current SCI room number from ScummVM's Room debug log.

        ScummVM must be started with:
            scummvm --debugflags=Room -d 1 ... 2>/tmp/scummvm_debug.log
        The path is set by scummvm_debug_log in the env config.

        Scans the log file in reverse for the most recent "room N" mention
        (ScummVM SCI logs lines like "Entering room N (was M)").
        Returns None silently if the log is unavailable or contains no room line.
        """
        log_path = getattr(config, "scummvm_debug_log",
                           config.env_config.get("scummvm_debug_log", ""))
        if not log_path or not os.path.exists(log_path):
            return None
        try:
            with open(log_path, "rb") as f:
                # Read up to the last 8 KB — enough for many recent log lines
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8192))
                tail = f.read().decode("utf-8", errors="replace")
            # Walk lines in reverse, find the latest room mention
            for line in reversed(tail.splitlines()):
                # Matches: "room 100", "entering room 100", "room: 100", "room=100"
                m = re.search(r"\broom[:\s=]+(\d+)", line, re.IGNORECASE)
                if m:
                    return int(m.group(1))
        except Exception as exc:
            logger.debug(f"Could not read ScummVM debug log '{log_path}': {exc}")
        return None


def entry(args):
    """Entry point called by runner.py."""
    import os
    os.environ.setdefault("DISPLAY", ":0")

    llm_config_path = args.llmProviderConfig
    embed_config_path = args.embedProviderConfig

    task_description_list = getattr(config, "task_description_list", None) or \
        config.env_config.get("task_description_list", [])
    if task_description_list:
        task_description = task_description_list[0].get(
            constants.TASK_DESCRIPTION, "Play the ScummVM game"
        )
    else:
        task_description = "Play the ScummVM game"

    runner = ScummVMRunner(
        llm_provider_config_path=llm_config_path,
        embed_provider_config_path=embed_config_path,
        task_description=task_description,
        use_self_reflection=getattr(config, "use_self_reflection", False),
        use_task_inference=getattr(config, "use_task_inference", False),
    )
    runner.run()
