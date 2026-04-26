"""Point and click interaction skills for ScummVM games."""

from cradle.config import Config
from cradle.log import Logger
from cradle.gameio import IOEnvironment
from cradle.environment import post_skill_wait

config = Config()
logger = Logger()
io_env = IOEnvironment()


def _abs(x, y):
    """Convert game-window-relative coords to absolute screen coords."""
    return config.env_window.left + x, config.env_window.top + y


class Click:
    """Click at a position within the game window."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "click"

    @staticmethod
    def execute(x: int, y: int):
        """Left-click at (x, y) relative to the game window.

        Args:
            x: X coordinate within the game window
            y: Y coordinate within the game window
        """
        ax, ay = _abs(x, y)
        io_env.mouse_move(ax, ay, relative=False)
        io_env.mouse_click_button('left')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class DoubleClick:
    """Double-click at a position within the game window."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "double_click"

    @staticmethod
    def execute(x: int, y: int):
        """Double-click at (x, y) relative to the game window.

        Args:
            x: X coordinate within the game window
            y: Y coordinate within the game window
        """
        ax, ay = _abs(x, y)
        io_env.mouse_move(ax, ay, relative=False)
        io_env.mouse_click_button('left', duration=0.01)
        io_env.mouse_click_button('left', duration=0.01)
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class RightClick:
    """Right-click at a position within the game window."""

    def __init__(self, registry):
        self.registry = registry
        self.name = "right_click"

    @staticmethod
    def execute(x: int, y: int):
        """Right-click at (x, y) relative to the game window.

        Args:
            x: X coordinate within the game window
            y: Y coordinate within the game window
        """
        ax, ay = _abs(x, y)
        io_env.mouse_move(ax, ay, relative=False)
        io_env.mouse_click_button('right')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)
