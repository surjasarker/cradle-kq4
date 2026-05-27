"""Skills for I'll Cop Your Heart: press arrow keys to steal hearts."""

import os
import subprocess
import time

from cradle.config import Config
from cradle.log import Logger

config = Config()
logger = Logger()

_XENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}

# The actual pygame window title (as set by pygame.display.set_caption)
WINDOW_TITLE = "I'll Cop Your Heart"

_KEY_MAP = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}


def _focus_game():
    """Raise and focus the game window so xdotool key events land there."""
    try:
        subprocess.run(
            ["wmctrl", "-a", WINDOW_TITLE],
            capture_output=True, timeout=3, env=_XENV,
        )
        time.sleep(0.08)
    except Exception:
        pass


def _xdotool(*args):
    subprocess.run(["xdotool", *[str(a) for a in args]],
                   env=_XENV, capture_output=True)


class PressKey:
    """Press an arrow key to steal the active heart."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "press_key"

    @staticmethod
    def execute(key: str):
        """Press an arrow key matching the direction of the active (red) heart.

        Args:
            key: Direction of the active heart — 'up', 'down', 'left', or 'right'
        """
        key = key.lower().strip()
        xkey = _KEY_MAP.get(key)
        if xkey is None:
            logger.write(f"press_key: invalid key '{key}' — must be up/down/left/right")
            return
        logger.write(f"press_key: {key} ({xkey})")
        _focus_game()
        _xdotool("key", "--clearmodifiers", xkey)
        time.sleep(0.05)


class PressStart:
    """Press Up arrow to start or restart the game from the menu."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "press_start"

    @staticmethod
    def execute():
        """Press the Up arrow key to start the game from the main menu."""
        logger.write("press_start: pressing Up to start game")
        _focus_game()
        _xdotool("key", "--clearmodifiers", "Up")
        time.sleep(0.2)
