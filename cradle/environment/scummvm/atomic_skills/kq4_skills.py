"""King's Quest IV action skills: move (click) and type parser command."""

import os
import subprocess
import time

from cradle.config import Config
from cradle.log import Logger
from cradle.gameio import IOEnvironment
from cradle.environment import post_skill_wait

config = Config()
logger = Logger()
io_env = IOEnvironment()

_XENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}


def _xdotool(*args):
    """Run xdotool with the correct DISPLAY set."""
    subprocess.run(["xdotool", *[str(a) for a in args]],
                   env=_XENV, capture_output=True)


def _abs(x, y):
    """Convert game-window-relative coords to absolute screen coords."""
    return config.env_window.left + x, config.env_window.top + y


class MoveTo:
    """Move the character by clicking at a position in the game window."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "move_to"

    @staticmethod
    def execute(x: int, y: int):
        """Click to move the character to the specified screen position.

        Args:
            x: X coordinate within the game window (pixels from left edge)
            y: Y coordinate within the game window (pixels from top edge)
        """
        ax, ay = _abs(x, y)
        logger.write(f"move_to: game({x},{y}) → desktop({ax},{ay})")
        _xdotool("mousemove", ax, ay)
        time.sleep(0.1)
        _xdotool("click", "1")
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME * 2)


class TypeCommand:
    """Type a text command into the game's text parser and execute it."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "type_command"

    @staticmethod
    def execute(command: str):
        """Type a parser command (verb [preposition] noun) and execute it.

        Args:
            command: Text command to type, e.g. 'look at rock', 'open door', 'talk to fisherman'
        """
        logger.write(f"Typing command: {command}")
        # Press Enter to activate/clear any pending input
        io_env.key_press('return')
        time.sleep(0.5)
        # Type the command text character by character
        io_env.keys_type(command)
        time.sleep(0.3)
        # Press Enter to execute the command
        io_env.key_press('return')
        # Wait long enough for the game to display its full text response
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME * 3)
