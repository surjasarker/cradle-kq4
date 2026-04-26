"""Diagnostic skills for analyzing ScummVM game state."""

from cradle.config import Config
from cradle.log import Logger
from cradle.gameio import IOEnvironment
from cradle.environment import post_skill_wait

config = Config()
logger = Logger()
io_env = IOEnvironment()


class GetHotspots:
    """Get interactive hotspots from the current screen."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "get_hotspots"
        
    @staticmethod
    def execute():
        """Get list of interactive hotspots on screen.
        
        Returns:
            List of hotspot information (to be filled by VLM analysis)
        """
        # This is a diagnostic skill that would typically use the VLM
        # to analyze the screenshot and identify interactive elements.
        # The actual implementation would rely on the VLM's vision capabilities.
        logger.debug("GetHotspots skill called - analysis handled by VLM")
        return []


class GetInventoryItems:
    """Get list of items currently in inventory."""
    
    def __init__(self, registry):
        self.registry = registry
        self.name = "get_inventory_items"
        
    @staticmethod
    def execute():
        """Get list of items in inventory.
        
        Returns:
            List of inventory items (to be filled by VLM analysis)
        """
        # This is a diagnostic skill that would typically use the VLM
        # to analyze the inventory screen and identify available items.
        # The actual implementation would rely on the VLM's vision capabilities.
        logger.debug("GetInventoryItems skill called - analysis handled by VLM")
        return []
