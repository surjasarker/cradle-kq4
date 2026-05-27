"""Skill registry for Simon Says game."""

import inspect
import base64
import os
import time as _time
import json
from typing import Dict, Any

import pyautogui

from cradle import constants
from cradle.config.config import Config
from cradle.log import Logger
from cradle.gameio import IOEnvironment
from cradle.environment import SkillRegistry
from cradle.environment import Skill
from cradle.utils.singleton import Singleton

config = Config()
logger = Logger()
io_env = IOEnvironment()

# Calibrated absolute button centers, populated by calibrate_buttons() or by
# reading conf/simon_says_buttons.json. The defaults are placeholders only —
# run test_simon_says_skills.py to write a real calibration file.
_BUTTON_POSITIONS: Dict[str, tuple] = {
    "red": (245, 420),
    "blue": (475, 420),
    "yellow": (245, 580),
    "green": (475, 580),
}

_CALIB_PATH = os.path.join("conf", "simon_says_buttons.json")


def _load_calibration() -> None:
    """Load button centers from disk if a calibration file is present."""
    global _BUTTON_POSITIONS
    try:
        from cradle.utils.file_utils import assemble_project_path
        path = assemble_project_path(_CALIB_PATH)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            _BUTTON_POSITIONS = {k: tuple(v) for k, v in data.items()}
            logger.write(f"Loaded Simon Says button calibration: {_BUTTON_POSITIONS}")
    except Exception as e:
        logger.warn(f"Failed to load button calibration: {e}")


_load_calibration()


# Pure tkinter fill colors used by Game.py for the four buttons. These are the
# resting/unhighlighted colors — Game.py only highlights one button at a time
# (and briefly), so at click-time the other three are always at these exact
# RGB values. We do a live screen-scan rather than trust a cached calibration:
# the calibration approach was brittle (median centroid skewed by stray pixels
# of the same color elsewhere on screen).
_COLOR_RGB = {
    "red":    (255,   0,   0),
    "blue":   (  0,   0, 255),
    "yellow": (255, 255,   0),
    "green":  (  0, 128,   0),   # tk 'green' = #008000, NOT (0,255,0) which is START
}


def _find_button_center_live(color: str):
    """Take a screenshot now and return the (x, y) center of the largest
    blob of pixels matching the button color, or None if not found.

    Uses cv2 connected-components so stray pixels elsewhere (e.g. one red
    pixel in a window decoration) can't pull the centroid off the real button.
    Restricts the scan to the upper 70% of the screen to exclude the START
    button area.
    """
    try:
        import numpy as np
        import cv2
    except Exception as e:
        logger.warn(f"_find_button_center_live: numpy/cv2 missing: {e}")
        return None

    rgb = _COLOR_RGB.get(color)
    if rgb is None:
        return None

    shot = pyautogui.screenshot()
    arr = np.array(shot)  # HxWx3 RGB
    H, W = arr.shape[:2]
    scan_h = int(H * 0.70)

    r, g, b = rgb
    mask = (
        (arr[:scan_h, :, 0] == r) &
        (arr[:scan_h, :, 1] == g) &
        (arr[:scan_h, :, 2] == b)
    ).astype("uint8") * 255

    num, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num <= 1:
        logger.warn(f"_find_button_center_live: no {color} blob found on screen")
        return None

    # stats[0] is the background; find the largest foreground component
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest = int(areas.argmax()) + 1
    cx, cy = centroids[largest]
    area = int(stats[largest, cv2.CC_STAT_AREA])
    logger.write(f"Found {color} button: center=({int(cx)},{int(cy)}), area={area}px")
    return int(cx), int(cy)


class BasicClick(Skill):
    """Click on a colored button in Simon Says."""

    @staticmethod
    def execute(color: str):
        """Left-click the named colored button.

        Locates the button by color on-screen at click-time, so the click
        always lands on the right button regardless of window position.
        Falls back to the calibration file if the live scan fails.
        """
        if color not in _COLOR_RGB:
            logger.error(f"Unknown color: {color}")
            return False

        # Live find first
        live = _find_button_center_live(color)
        if live is not None:
            x, y = live
            source = "live"
        elif color in _BUTTON_POSITIONS:
            x, y = _BUTTON_POSITIONS[color]
            source = "fallback calibration"
            logger.warn(f"Using {source} for {color} (live scan failed)")
        else:
            logger.error(f"Could not locate {color} button")
            return False

        # Use pyautogui directly — AHK's stdin pipe breaks after clicks into
        # tkinter windows on Windows (OSError [Errno 22] on flush).
        pyautogui.moveTo(x, y, duration=0.1)
        pyautogui.click(x, y)
        # Game.py's highlight_button() blocks the UI for ~300 ms per click.
        _time.sleep(0.5)
        logger.write(f"Clicked {color} button at ({x}, {y}) [{source}]")
        return True


SIMON_SAYS_SKILLS = {
    "click": BasicClick,
}


class SimonSaysSkillRegistry(SkillRegistry, metaclass=Singleton):
    """Skill registry for Simon Says game."""

    def __init__(self,
                 *args,
                 skill_configs: Dict[str, Any] = None,
                 embedding_provider=None,
                 **kwargs):

        if skill_configs is None:
            skill_configs = config.skill_configs

        skill_configs[constants.SKILL_CONFIG_REGISTERED_SKILLS] = SIMON_SAYS_SKILLS

        self.skill_configs = skill_configs
        self.embedding_provider = embedding_provider
        self.skills = {}

        self._init_skills()

    def _init_skills(self):
        """Register all Simon Says skills."""
        import numpy as np
        for skill_name, skill_class in SIMON_SAYS_SKILLS.items():
            try:
                skill_obj = Skill(
                    skill_name=skill_name,
                    skill_function=skill_class.execute,
                    skill_embedding=np.array([]),
                    skill_code=inspect.getsource(skill_class),
                    skill_code_base64=base64.b64encode(
                        inspect.getsource(skill_class).encode('utf-8')
                    ).decode('utf-8')
                )
                self.skills[skill_name] = skill_obj
                logger.debug(f"Registered skill: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to register skill {skill_name}: {e}")

    def retrieve_skills(self,
                        query_task: str = "",
                        skill_num: int = 10,
                        screen_type: str = "general game interface",
                        **kwargs) -> list:
        """Return all registered skill names."""
        return list(self.skills.keys())

    def get_from_skill_library(self,
                               skill_name: str,
                               skill_library_with_code: bool = False) -> Dict:
        """Get a skill's signature and description."""
        if skill_name not in self.skills:
            return {}

        skill = self.skills[skill_name]
        func = skill.skill_function

        try:
            sig = inspect.signature(func)
            param_names = list(sig.parameters.keys())
            function_expression = (
                f"{skill_name}({', '.join(param_names)})" if param_names else f"{skill_name}()"
            )
        except Exception:
            function_expression = f"{skill_name}()"

        description = inspect.getdoc(func) or ""

        result = {
            "function_expression": function_expression,
            "description": description,
        }

        if skill_library_with_code:
            result["skill_code"] = skill.skill_code

        return result
