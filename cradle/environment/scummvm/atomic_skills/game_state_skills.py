"""Game state management skills for ScummVM games."""

from cradle.config import Config
from cradle.log import Logger
from cradle.gameio import IOEnvironment
from cradle.environment import post_skill_wait

config = Config()
logger = Logger()
io_env = IOEnvironment()


class Quicksave:
    """Save the game to the quicksave slot."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "quicksave"
        
    @staticmethod
    def execute():
        """Quicksave the game (F7 in most ScummVM games)."""
        io_env.key_press('f7')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class Quickload:
    """Load the game from the quicksave slot."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "quickload"
        
    @staticmethod
    def execute():
        """Quickload the game (F8 in most ScummVM games)."""
        io_env.key_press('f8')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class OpenMenu:
    """Open the game menu."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "open_menu"
        
    @staticmethod
    def execute():
        """Open the game menu (typically Escape key)."""
        io_env.key_press('esc')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class SaveGame:
    """Save the game to a specific save slot."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "save_game"
        
    @staticmethod
    def execute(slot: int = None):
        """Save the game to a save slot.
        
        Args:
            slot: Save slot number (optional, defaults to quicksave)
        """
        # Open menu
        io_env.key_press('esc')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)
        
        # The actual slot selection would be handled by the VLM
        # via menu navigation


class LoadGame:
    """Load the game from a specific save slot."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "load_game"
        
    @staticmethod
    def execute(slot: int = None):
        """Load the game from a save slot.
        
        Args:
            slot: Save slot number (optional, defaults to quickload)
        """
        # Open menu
        io_env.key_press('esc')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)
        
        # The actual slot selection would be handled by the VLM
        # via menu navigation
