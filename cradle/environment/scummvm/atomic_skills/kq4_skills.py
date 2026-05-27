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
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


_DIRECTION_KEYS = {
    "north": "Up",  "up": "Up",
    "south": "Down", "down": "Down",
    "east":  "Right", "right": "Right",
    "west":  "Left",  "left": "Left",
}


class MoveDirection:
    """Move the character by holding an arrow key for a given duration."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "move_direction"

    @staticmethod
    def execute(direction: str, duration: float):
        """Hold an arrow key to walk in a direction.

        Args:
            direction: One of north, south, east, west (or up/down/left/right)
            duration:  Seconds to walk in that direction (0.1 to 5.0)
        """
        key = _DIRECTION_KEYS.get(direction.lower())
        if key is None:
            logger.write(f"move_direction: unknown direction '{direction}', skipping")
            return
        duration = max(0.1, min(float(duration), 5.0))
        logger.write(f"move_direction: {direction} ({key}) for {duration}s")
        _xdotool("key", key)   # press once to start moving
        time.sleep(duration)
        _xdotool("key", key)   # press again to stop
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class MoveSequence:
    """Execute a planned sequence of directional moves in one action."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "move_sequence"

    @staticmethod
    def execute(moves: str):
        """Execute a sequence of directional moves.

        Args:
            moves: Comma-separated "direction duration" pairs,
                   e.g. "east 1.0, north 2.0, east 0.5"
        """
        steps = []
        for part in moves.split(","):
            part = part.strip()
            if not part:
                continue
            tokens = part.split()
            if len(tokens) >= 2:
                direction = tokens[0].lower()
                try:
                    duration = float(tokens[1].rstrip("s"))
                    steps.append((direction, duration))
                except ValueError:
                    logger.write(f"move_sequence: bad duration in '{part}', skipping")

        if not steps:
            logger.write("move_sequence: no valid steps parsed")
            return

        for direction, duration in steps:
            key = _DIRECTION_KEYS.get(direction)
            if key is None:
                logger.write(f"move_sequence: unknown direction '{direction}', skipping")
                continue
            duration = max(0.1, min(duration, 5.0))
            logger.write(f"move_sequence step: {direction} ({key}) for {duration}s")
            _xdotool("key", key)
            time.sleep(duration)
            _xdotool("key", key)
            time.sleep(0.2)

        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


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
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME * 2)
