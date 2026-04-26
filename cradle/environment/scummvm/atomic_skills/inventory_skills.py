"""Inventory management skills for ScummVM games."""

from cradle.config import Config
from cradle.log import Logger
from cradle.gameio import IOEnvironment
from cradle.environment import post_skill_wait

config = Config()
logger = Logger()
io_env = IOEnvironment()


class OpenInventory:
    """Open the inventory screen."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "open_inventory"
        
    @staticmethod
    def execute():
        """Open the inventory screen (typically 'i' key or menu access)."""
        # Standard in most ScummVM games
        io_env.key_press('i')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class CloseInventory:
    """Close the inventory screen."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "close_inventory"
        
    @staticmethod
    def execute():
        """Close the inventory screen (typically Escape key)."""
        io_env.key_press('esc')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class UseItem:
    """Use an item from inventory on a location or object."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "use_item"
        
    @staticmethod
    def execute(item_name: str, x: int = None, y: int = None):
        """Use an inventory item.
        
        Args:
            item_name: Name of the item to use
            x: Optional X coordinate to use item on (pixel position)
            y: Optional Y coordinate to use item on (pixel position)
        """
        # Open inventory
        io_env.key_press('i')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)
        
        # The actual selection of the item would need to be done by the VLM
        # via coordinate-based clicking or text search. This is a placeholder
        # that assumes the VLM will handle item selection after opening inventory.
        
        if x is not None and y is not None:
            # Close inventory and click on target location
            io_env.key_press('esc')
            post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)
            io_env.mouse_move(x, y, relative=False)
            io_env.mouse_click_button('left')
            post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)


class ExamineItem:
    """Examine an item in inventory for more information."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "examine_item"
        
    @staticmethod
    def execute(item_name: str):
        """Examine an inventory item.
        
        Args:
            item_name: Name of the item to examine
        """
        # Open inventory
        io_env.key_press('i')
        post_skill_wait(config.DEFAULT_POST_ACTION_WAIT_TIME)
        
        # The actual examination would be done by VLM clicking on the item
        # or selecting it from a list. This is a placeholder.
