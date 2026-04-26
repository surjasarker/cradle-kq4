"""Interaction skills for ScummVM games (walk, talk, look at objects)."""

from cradle.config import Config
from cradle.log import Logger
from cradle.gameio import IOEnvironment
from cradle.environment import post_skill_wait

config = Config()
logger = Logger()
io_env = IOEnvironment()


class Look:
    """Use look cursor on an object or location."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "look"

    @staticmethod
    def execute(x: int, y: int):
        """Look at the specified position (coordinates relative to game window).

        Args:
            x: X coordinate within the game window
            y: Y coordinate within the game window
        """
        abs_x = config.env_window.left + x
        abs_y = config.env_window.top + y
        io_env.mouse_move(abs_x, abs_y, relative=False)
        io_env.mouse_click_button('right')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class Talk:
    """Talk to an NPC at the specified position."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "talk"

    @staticmethod
    def execute(x: int, y: int):
        """Talk to NPC/character at the specified position (coordinates relative to game window).

        Args:
            x: X coordinate within the game window
            y: Y coordinate within the game window
        """
        abs_x = config.env_window.left + x
        abs_y = config.env_window.top + y
        io_env.mouse_move(abs_x, abs_y, relative=False)
        io_env.mouse_click_button('left')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class WalkTo:
    """Walk the character to a specific location on screen."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "walk_to"

    @staticmethod
    def execute(x: int, y: int):
        """Walk to the specified screen position (coordinates relative to game window).

        Args:
            x: X coordinate within the game window
            y: Y coordinate within the game window
        """
        abs_x = config.env_window.left + x
        abs_y = config.env_window.top + y
        io_env.mouse_move(abs_x, abs_y, relative=False)
        io_env.mouse_click_button('left')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME * 2)
