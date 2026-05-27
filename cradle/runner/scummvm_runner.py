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
        self.action_history: List[str] = []             # rolling global history
        self.action_response_history: List[tuple] = []  # (action, game_response) — correctly paired
        self.pinned_screenshots: List[tuple] = []       # (path, action, reason, scene_desc) — VLM-saved
        self.prev_scene_screenshot_path: Optional[str] = None
        self.is_stuck: bool = False
        self.stuck_count: int = 0
        self.last_executed_action: str = ""             # action from previous turn for correct pairing

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

        # Apply hardcoded game window bounds if specified in env config
        gw = config.env_config.get("game_window")
        if gw:
            config.env_window.left   = gw["left"]
            config.env_window.top    = gw["top"]
            config.env_window.width  = gw["width"]
            config.env_window.height = gw["height"]
            print(f"  [window] overridden: left={gw['left']} top={gw['top']} "
                  f"width={gw['width']} height={gw['height']}")

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

                # Stuck detection: compare current scene to previous scene after a move action
                params_pre = self.memory.working_area.copy()
                last_action_pre = params_pre.get(constants.PRE_ACTION, "")
                if isinstance(last_action_pre, list):
                    last_action_pre = ", ".join(str(a) for a in last_action_pre)
                self.is_stuck = self._detect_stuck(
                    self.prev_scene_screenshot_path, scene_screenshot_path, last_action_pre
                )
                if self.is_stuck:
                    self.stuck_count += 1
                    print(f"  [STUCK] Scene unchanged after move (stuck_count={self.stuck_count})")
                else:
                    self.stuck_count = 0

                print(f"\n  ▶ Turn: {self.count_turns}")

                # 2. Information gathering: read the text response screenshot
                self.run_information_gathering(text_screenshot_path)

                # Pair the game's response to the action that caused it (last turn's action).
                # We do this right after info gathering so image_description is fresh.
                if self.last_executed_action:
                    params_ig = self.memory.working_area.copy()
                    game_response = params_ig.get("image_description", "").strip()
                    if game_response:
                        self.action_response_history.append((self.last_executed_action, game_response))
                        self.action_response_history = self.action_response_history[-12:]
                        logger.write(f"History: '{self.last_executed_action}' → '{game_response[:80]}...'")

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

                # Record this turn's action so next turn can pair it with the game's response
                params = self.memory.working_area.copy()
                executed = params.get(constants.PRE_ACTION, "")
                if isinstance(executed, list):
                    executed = ", ".join(str(a) for a in executed)
                self.last_executed_action = executed

                self.prev_scene_screenshot_path = scene_screenshot_path
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

        movement_mode = config.env_config.get("movement_mode", "click")


        # Build recent history block — action paired with the game's actual response
        if self.action_response_history:
            history_lines = []
            for i, (act, response) in enumerate(self.action_response_history, 1):
                history_lines.append(f"  [{i}] You did: {act}")
                if response:
                    # Keep up to 200 chars of the game response so nothing important is lost
                    clipped = response[:200].rstrip()
                    if len(response) > 200:
                        clipped += "…"
                    history_lines.append(f"       Game said: {clipped}")
            history_block = (
                "\n=== WHAT YOU DID AND WHAT THE GAME SAID ===\n"
                + "\n".join(history_lines)
                + "\n============================================\n"
            )
        else:
            history_block = ""

        movement_mode = config.env_config.get("movement_mode", "click")

        # Detect "too far" / "not close enough" in the game's last text response
        too_far_keywords = ["too far", "not close enough", "can't reach", "cannot reach",
                            "not near enough", "closer", "walk up to", "approach"]
        game_said_too_far = any(kw in image_description.lower() for kw in too_far_keywords)

        if movement_mode == "keyboard":
            move_section = (
                "2. MOVE — hold an arrow key to walk in a direction:\n"
                "   move_direction(direction=\"DIRECTION\", duration=SECONDS)\n"
                "   DIRECTION: left, right, up, down  (matching what you see on screen)\n"
                "   DURATION: number of seconds to walk (0.1 to 5.0)\n"
            )
            valid_move_skills = {"move_direction", "type_command"}
        elif movement_mode == "sequence":
            move_section = (
                "2. MOVE — plan a path as a sequence of directional steps:\n"
                "   move_sequence(moves=\"DIRECTION DURATION, DIRECTION DURATION, ...\")\n\n"
                "   COORDINATE SYSTEM (very important — do not confuse these):\n"
                "     X increases left → right across the image.  x=0 is the LEFT edge.\n"
                "     Y increases top → bottom down the image.    y=0 is the TOP edge.\n"
                "     'right' means increasing X (toward the right side of the image).\n"
                "     'left'  means decreasing X (toward the left  side of the image).\n"
                "     'down'  means increasing Y (toward the bottom of the image).\n"
                "     'up'    means decreasing Y (toward the top    of the image).\n\n"
                f"   The game window is {win_w} px wide and {win_h} px tall.\n"
                "   Full width = 5.0s. Full height = 5.0s.\n"
                "   DURATION = (pixel distance in that axis / axis size) × 5.0\n\n"
                "   SCREEN EDGES — walking off an edge moves Rosella to a new area:\n"
                "     Left edge  (x ≈ 0)   → new area to the west\n"
                "     Right edge (x ≈ {win_w}) → new area to the east\n"
                "     Top edge   (y ≈ 0)   → new area to the north\n"
                "     Bottom edge (y ≈ {win_h}) → new area to the south\n"
                "   To enter a new area, move Rosella all the way to the relevant edge.\n"
            ).format(win_w=win_w, win_h=win_h)
            valid_move_skills = {"move_sequence", "type_command"}
        else:
            move_section = (
                "2. MOVE — click anywhere in the game window to walk Rosella there:\n"
                "   move_to(x=X, y=Y)\n"
                "   X and Y are pixel coordinates within the game image (0–{win_w}, 0–{win_h}).\n"
                "   IMPORTANT: Rosella must be VERY CLOSE (within ~30 pixels) to an object before\n"
                "   the game will let her interact with it. If you want to interact with something,\n"
                "   click DIRECTLY ON the object/person/door first to walk right up to it.\n"
                "   Do NOT click a random direction and hope she ends up close enough.\n"
            ).format(win_w=win_w, win_h=win_h)
            valid_move_skills = {"move_to", "type_command"}

        if movement_mode == "keyboard":
            format_example = (
                '# Choose ONE of:\n'
                'move_direction(direction="left"|"right"|"up"|"down", duration=<float>)\n'
                '# OR\n'
                'type_command(command="verb noun")'
            )
        elif movement_mode == "sequence":
            format_example = (
                '# Fill in your planned steps (any number of steps, any durations):\n'
                '# DIRECTION must be: left, right, up, or down\n'
                '# Duration is a plain number — NO "s" suffix. e.g. 2.0 not 2.0s\n'
                'move_sequence(moves="<left/right/up/down> <float>, <left/right/up/down> <float>, ...")\n'
                '# OR, if interacting with something:\n'
                'type_command(command="verb noun")'
            )
        else:
            format_example = (
                '# To MOVE: click directly on the target object/person/door\n'
                'move_to(x=X, y=Y)\n'
                '# OR to INTERACT (only when already very close to the target):\n'
                'type_command(command="verb noun")'
            )

        # Build "too far" alert if applicable
        too_far_alert = ""
        if game_said_too_far:
            too_far_alert = (
                "\n⚠️  GAME SAID YOU ARE TOO FAR AWAY from the target.\n"
                "You MUST move closer before interacting. Click directly ON the object/person/door "
                "to walk right up to it, then try the command again next turn.\n"
            )

        # Build stuck alert if the scene did not change after a move
        stuck_alert = ""
        if self.is_stuck:
            if self.stuck_count >= 2:
                stuck_alert = (
                    f"\n🚧  ROSELLA IS STUCK (happened {self.stuck_count} turns in a row).\n"
                    "Her position on screen has NOT changed despite your move commands.\n"
                    "She is blocked by a wall, tree, rock, or other obstacle.\n"
                    "You MUST navigate AROUND the obstacle:\n"
                    "  - Try moving in a perpendicular direction first (e.g. if going right, try up or down first).\n"
                    "  - Take a short step sideways to clear the obstacle, then resume toward your target.\n"
                    "  - Do NOT keep issuing the same move — it will not work.\n"
                )
            else:
                stuck_alert = (
                    "\n🚧  ROSELLA DID NOT MOVE — she may be stuck on a wall or object.\n"
                    "If your intended direction is blocked, try a slightly different angle or "
                    "step sideways first to get around the obstacle.\n"
                )

        if movement_mode == "sequence":
            response_format = (
                "DECISION RULE:\n"
                "- Is there something RIGHT NEXT TO Rosella you haven't interacted with? → type_command\n"
                "- Otherwise → move_sequence toward your target, using the pixel-math below.\n"
                "- After arriving near a door: type_command(command=\"open door\")\n"
                "- After arriving near a person: type_command(command=\"talk to person\")\n"
                "- After arriving near an item: type_command(command=\"take item\")\n\n"
                "Respond in this EXACT format:\n\n"
                "Decision_Making_Reasoning:\n"
                "<What do you see? What is your goal this turn — move or interact?>\n\n"
                "Path_Plan (fill in if moving; skip entirely if using type_command):\n"
                f"  Screen size: {win_w} px wide, {win_h} px tall. Full axis = 5.0s.\n"
                "  Rosella position:  x=<px>, y=<px>  (estimate from the image)\n"
                "  Target position:   x=<px>, y=<px>  (<what it is>)\n"
                "  dx = target_x - rosella_x = <value>\n"
                "    dx positive → target is to the RIGHT  → horizontal direction: right\n"
                "    dx negative → target is to the LEFT   → horizontal direction: left\n"
                "    → horizontal direction I will use: <right/left>  duration: abs(dx)/{win_w} × 5 = <sec>s\n"
                "  dy = target_y - rosella_y = <value>\n"
                "    dy positive → target is BELOW         → vertical direction: down\n"
                "    dy negative → target is ABOVE         → vertical direction: up\n"
                "    → vertical direction I will use: <up/down>  duration: abs(dy)/{win_h} × 5 = <sec>s\n"
                "  (skip an axis if abs gap < 30 px)\n"
                "  Final moves string: \"<h-dir> <h-sec>, <v-dir> <v-sec>\"  ← copy this exactly into the action\n\n"
                "Actions:\n"
                "```python\n"
                f"{format_example}\n"
                "```\n\n"
                "Key_reason_of_last_action:\n"
                "<one sentence: goal of this action>\n\n"
                "Save_Screenshot: yes — <one sentence: why this screenshot is useful to remember> "
                "| omit this line entirely if the screenshot is not worth keeping"
            )
        else:
            response_format = (
                "HOW INTERACTION WORKS IN THIS GAME:\n"
                "  Step 1 — MOVE: Click directly on the object/NPC/door you want to interact with.\n"
                "           Rosella will walk to that exact pixel. She must be RIGHT NEXT TO it.\n"
                "  Step 2 — INTERACT: On the NEXT turn, once she is close, use type_command.\n"
                "  If the game says 'too far' or 'not close enough', you must click EVEN CLOSER\n"
                "  to the target — click directly on the object's sprite, not near it.\n\n"
                "DECISION RULE THIS TURN:\n"
                "  - Is there something nearby you have NOT interacted with yet? → type_command\n"
                "  - Is there something you want to reach that is not right next to Rosella? → move_to (click ON the target)\n"
                "  - Did the game just say you're too far? → move_to clicking DIRECTLY ON the object\n\n"
                "Respond in this EXACT format:\n\n"
                "Decision_Making_Reasoning:\n"
                "<Where is Rosella on screen? What is your target? Is she close enough to interact, "
                "or does she need to walk closer first?>\n\n"
                "Target_Location (fill in only if moving):\n"
                "- Target object/person/door: <describe it>\n"
                "- Its position in the image: approximately (X, Y) pixels\n"
                "- I will click at: (X, Y) — directly on the target sprite\n\n"
                "Actions:\n"
                "```python\n"
                f"{format_example}\n"
                "```\n\n"
                "Key_reason_of_last_action:\n"
                "<one sentence: goal of this action>\n\n"
                "Save_Screenshot: yes — <one sentence: why this screenshot is useful to remember> "
                "| omit this line entirely if the screenshot is not worth keeping"
            )

        user_text = (
            f"Task: {self.task_description}\n\n"
            f"=== GAME RESPONSE TO LAST ACTION ===\n{image_description}\n"
            f"====================================={too_far_alert}{stuck_alert}{history_block}\n"
            f"Last action taken: {previous_action or 'None'}\n"
            f"Game window: {win_w}x{win_h} pixels. "
            f"(0,0)=TOP-LEFT, ({win_w},{win_h})=BOTTOM-RIGHT.\n\n"
            "You have TWO types of actions:\n\n"
            "1. TYPE COMMAND — use a verb with a noun you see on screen:\n"
            "   type_command(command=\"VERB NOUN\")\n"
            "   ONLY use this when Rosella is already right next to the target.\n\n"
            f"   Valid verbs: {VERB_LIST}\n\n"
            "   Choose the noun from what you actually see in the screenshot "
            "(objects, characters, exits, items). Do NOT reuse a command you have already tried.\n\n"
            f"{move_section}\n"
            "Do not repeat the same action over and over — always try something new.\n\n"
            f"{response_format}"
        )

        # Encode screenshot directly to base64
        import base64 as _b64
        try:
            with open(screenshot_path, "rb") as f:
                raw = f.read()
            b64_str = _b64.b64encode(raw).decode("utf-8")
            ext = screenshot_path.rsplit(".", 1)[-1].lower()
            image_url = f"data:image/{ext};base64,{b64_str}"
            logger.write(f"Action planning screenshot encoded: {len(raw)} bytes from {screenshot_path}")
        except Exception as e:
            logger.warn(f"Could not encode screenshot {screenshot_path}: {e}")
            image_url = None

        if movement_mode == "keyboard":
            action_list_str = "move_direction(direction, duration) or type_command(command)"
        elif movement_mode == "sequence":
            action_list_str = "move_sequence(moves) or type_command(command)"
        else:
            action_list_str = "move_to(x, y) or type_command(command)"

        # Build messages directly — guaranteed to include the image
        messages = [
            {"role": "system", "content": (
                "You are an AI agent playing King's Quest IV, a text-parser adventure game in ScummVM. "
                "You control Rosella — the young woman in the red dress with blonde hair on screen. "
                f"Each turn output exactly ONE action: {action_list_str}. "
                "DIRECTION RULE: in the image, X increases left-to-right and Y increases top-to-bottom. "
                "If the target has a HIGHER X than Rosella, you MUST use 'right'. "
                "If the target has a LOWER X than Rosella, you MUST use 'left'. "
                "The direction in your action must always match the sign of dx/dy you computed — never contradict your own reasoning. "
                "INTERACTION REQUIRES TWO STEPS: "
                "(1) First use move_to to click DIRECTLY ON the target object/NPC/door — Rosella will walk right up to it. "
                "(2) On the next turn, once she is standing right next to it, use type_command to interact. "
                "If the game says 'too far' or 'not close enough', you are not close enough — "
                "click even closer, directly on the object's sprite, not beside it. "
                "Do not repeat the same action over and over — vary your actions and make progress. "
                "At the end of your response you may optionally write 'Save_Screenshot: yes — <reason>' "
                "to pin the CURRENT screenshot for future reference. Only do this when the scene contains "
                "something genuinely useful to remember (a new location, key item, NPC, puzzle state). "
                "Do NOT save every turn."
            )},
        ]
        # Log and build screenshot context — pinned screenshots only, then current
        print("\n" + "="*70)
        print(f">>> SCREENSHOTS SENT TO VLM  (turn {self.count_turns + 1})")
        print("="*70)

        user_content = []

        if self.pinned_screenshots:
            user_content.append({"type": "text",
                "text": f"=== YOUR SAVED SCREENSHOTS ({len(self.pinned_screenshots)}) — "
                        "scenes you marked as important in previous turns ==="})
            for pin_path, pin_action, pin_reason, pin_desc in self.pinned_screenshots:
                try:
                    with open(pin_path, "rb") as f:
                        pin_raw = f.read()
                    pin_b64 = _b64.b64encode(pin_raw).decode("utf-8")
                    pin_ext = pin_path.rsplit(".", 1)[-1].lower()
                    pin_url = f"data:image/{pin_ext};base64,{pin_b64}"
                    short_desc = pin_desc.split(".")[0].strip() if pin_desc else ""
                    user_content.append({"type": "text",
                        "text": (
                            f"[SAVED] Why kept: {pin_reason}\n"
                            f"Action taken after: {pin_action}\n"
                            f"Scene at the time: {short_desc}"
                        )})
                    user_content.append({"type": "image_url", "image_url": {"url": pin_url}})
                    print(f"  [SAVED] {os.path.basename(pin_path)} — {pin_reason}")
                except Exception as e:
                    logger.debug(f"Could not load pinned screenshot {pin_path}: {e}")
            user_content.append({"type": "text", "text": "=== END SAVED SCREENSHOTS ==="})
        else:
            print("  (no saved screenshots yet)")

        print(f"  [NOW]   {os.path.basename(screenshot_path)}  <- CURRENT (newest)")
        print("="*70)

        user_content.append({"type": "text",
            "text": f">>> CURRENT SCREENSHOT (newest, turn {self.count_turns + 1}) — use this to decide your action:"})
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

            # Retry if the model produced no valid action or used a placeholder
            def _skill_name(s):
                m = re.match(r"^(\w+)\(", s.strip())
                return m.group(1) if m else ""

            def _is_placeholder(s):
                return "VERB NOUN" in s or 'command="X"' in s or "<direction>" in s or "<float>" in s

            def _action_ok(steps):
                if not steps:
                    return False, "no action produced"
                p = steps[0]
                if _skill_name(p) not in valid_move_skills:
                    return False, f"'{p}' is not a valid skill — use one of: {', '.join(sorted(valid_move_skills))}"
                if _is_placeholder(p):
                    return False, f"'{p}' contains placeholder text — fill in real values"
                return True, ""

            ok, reason = _action_ok(skill_steps)
            for _attempt in range(3):
                if ok:
                    break
                logger.write(f"Action rejected ({reason}) — correction attempt {_attempt+1}")
                # Use a fresh minimal conversation for the retry to avoid hitting context limits
                valid_names = " or ".join(sorted(valid_move_skills))
                retry_messages = [
                    {"role": "system", "content": (
                        "You are an AI agent playing King's Quest IV. "
                        f"Output exactly ONE valid action: {valid_names}."
                    )},
                    {"role": "user", "content": (
                        f"Your last response was rejected: {reason}.\n"
                        f"Output ONE valid action in a python code block. No other text."
                    )},
                ]
                raw_response, _ = self.llm_provider.create_completion(retry_messages)
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

            # Check if the VLM wants to pin this screenshot
            save_field = res.get("save_screenshot", res.get("Save_Screenshot", ""))
            if not save_field:
                # Also scan raw response for the field (parser may miss it)
                m = re.search(r"Save_Screenshot\s*:\s*yes\s*[—–-]\s*(.+)", raw_response, re.IGNORECASE)
                if m:
                    save_field = "yes — " + m.group(1).strip()
            if save_field and str(save_field).lower().startswith("yes"):
                pin_reason = re.sub(r"^yes\s*[—–-]\s*", "", str(save_field), flags=re.IGNORECASE).strip()
                last_action_str = skill_steps[0] if skill_steps else "none"
                self.pinned_screenshots.append((screenshot_path, last_action_str, pin_reason, image_description))
                self.pinned_screenshots = self.pinned_screenshots[-5:]  # keep at most 5 pinned
                logger.write(f"Screenshot PINNED: {pin_reason}")
                print(f"  [PINNED] {pin_reason}")

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
                    self.action_history.append(actions_str)
                    self.action_history = self.action_history[-20:]
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

        # Last resort: if the model output a bare parser command (e.g. "examine tree",
        # "look around") without wrapping it in type_command(), auto-wrap it.
        if full_response:
            code_blocks = re.findall(r"```(?:python)?\s*(.*?)```", full_response, re.DOTALL)
            for block in code_blocks:
                for line in block.strip().splitlines():
                    line = line.strip()
                    if (line
                            and not line.startswith("#")
                            and not re.match(r"^\w+\(", line)
                            and re.match(r"^[a-zA-Z][\w ]*$", line)):
                        logger.write(f"Auto-wrapping bare command: '{line}'")
                        return [f'type_command(command="{line}")']

        return []

    def _detect_stuck(self, prev_path: Optional[str], curr_path: str, last_action: str) -> bool:
        """Return True if the scene did not change after a move action (Rosella is stuck)."""
        if not prev_path or not curr_path:
            return False
        # Only check after move commands — not after type_command
        if not any(m in last_action for m in ("move_to", "move_sequence", "move_direction")):
            return False
        try:
            from PIL import Image
            import numpy as np
            img1 = np.array(Image.open(prev_path).convert("RGB"), dtype=float)
            img2 = np.array(Image.open(curr_path).convert("RGB"), dtype=float)
            if img1.shape != img2.shape:
                return False
            diff = float(np.mean(np.abs(img1 - img2)))
            logger.write(f"Scene diff after move: {diff:.2f} (stuck threshold: 0.5)")
            print(f"  [stuck-check] scene diff = {diff:.2f}")
            return diff < 0.5
        except Exception as e:
            logger.warn(f"Stuck detection failed: {e}")
            return False


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
