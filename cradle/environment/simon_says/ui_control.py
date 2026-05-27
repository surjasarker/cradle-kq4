"""UI Control for Simon Says game."""

import time
from cradle.config import Config
from cradle.log import Logger
from cradle.gameio.io_env import IOEnvironment
from cradle.gameio.lifecycle.ui_control import take_screenshot
from cradle.environment import UIControl
from cradle import constants

config = Config()
logger = Logger()
io_env = IOEnvironment()


class SimonSaysUIControl(UIControl):
    """UI Control for Simon Says game.
    
    Handles pause/resume and window switching for the Simon Says game.
    Since Simon Says doesn't support pause/resume like ScummVM, these operations
    are no-ops but follow the same interface for compatibility.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pause_timeout = 5.0
        self._window_found = False

    def pause_game(self, env_name: str, ide_name: str) -> None:
        """No-op: tkinter Simon Says has no pause hook."""
        pass

    def unpause_game(self, env_name: str, ide_name: str) -> None:
        """No-op: tkinter Simon Says has no pause hook."""
        pass

    def switch_to_game(self, env_name: str, ide_name: str) -> None:
        """Switch focus to the Simon Says game window.
        
        Args:
            env_name: Simon Says game window name
            ide_name: IDE window name
        """
        try:
            # Try to find the game window
            named_windows = io_env.get_windows_by_name(env_name)
            if len(named_windows) > 0:
                target_window = named_windows[0]
                target_window.activate()
                target_window.show()
                logger.debug(f"Switched to game window: {env_name}")
                self._window_found = True
                time.sleep(0.5)
            else:
                if not self._window_found:
                    logger.warning(f"Game window '{env_name}' not found. Make sure Game.py is running.")
                else:
                    logger.debug(f"Game window '{env_name}' not visible, may be minimized")
        except Exception as e:
            logger.error(f"Error switching to game window: {e}")

    def switch_to_ide(self, env_name: str, ide_name: str) -> None:
        """Switch focus to the IDE window.

        Args:
            env_name: Simon Says game window name (not used)
            ide_name: IDE window name
        """
        try:
            if ide_name:
                named_windows = io_env.get_windows_by_name(ide_name)
                if len(named_windows) > 0:
                    target_window = named_windows[0]
                    target_window.activate()
                    target_window.show()
                    logger.debug(f"Switched to IDE window: {ide_name}")
                    time.sleep(0.5)
        except Exception as e:
            logger.error(f"Error switching to IDE window: {e}")

    def exit_back_to_pause(self, env_name: str, ide_name: str) -> None:
        pass

    def exit_back_to_game(self, env_name: str, ide_name: str) -> None:
        pass

    def take_screenshot(self,
                        tid: float,
                        screen_region: tuple[int, int, int, int] = None) -> str:
        screenshot, _ = take_screenshot(tid, screen_region=screen_region)
        return screenshot
