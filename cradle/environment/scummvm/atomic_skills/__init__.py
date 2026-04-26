"""Atomic skills for ScummVM games."""

from .click_skills import Click, DoubleClick, RightClick
from .interaction_skills import Look, Talk, WalkTo
from .inventory_skills import OpenInventory, CloseInventory, UseItem, ExamineItem
from .game_state_skills import Quicksave, Quickload, OpenMenu, SaveGame, LoadGame
from .diagnostic_skills import GetHotspots, GetInventoryItems

__all__ = [
    # Click skills
    "Click",
    "DoubleClick", 
    "RightClick",
    # Interaction skills
    "Look",
    "Talk",
    "WalkTo",
    # Inventory skills
    "OpenInventory",
    "CloseInventory",
    "UseItem",
    "ExamineItem",
    # Game state skills
    "Quicksave",
    "Quickload",
    "OpenMenu",
    "SaveGame",
    "LoadGame",
    # Diagnostic skills
    "GetHotspots",
    "GetInventoryItems",
]
