"""Skill registry for I'll Cop Your Heart (PyWeek-31 pygame game)."""

import inspect
import base64
from typing import Dict, Any

from cradle import constants
from cradle.config.config import Config
from cradle.log import Logger
from cradle.environment import SkillRegistry, Skill
from cradle.utils.singleton import Singleton

from cradle.environment.pygame_hearts.atomic_skills.hearts_skills import PressKey, PressStart

config = Config()
logger = Logger()

HEARTS_SKILLS = {
    "press_key": PressKey,
    "press_start": PressStart,
}


class PyGameHeartsSkillRegistry(SkillRegistry, metaclass=Singleton):
    """Skill registry for I'll Cop Your Heart."""

    def __init__(self, *args, skill_configs: Dict[str, Any] = None,
                 embedding_provider=None, **kwargs):
        if skill_configs is None:
            skill_configs = config.skill_configs
        skill_configs[constants.SKILL_CONFIG_REGISTERED_SKILLS] = HEARTS_SKILLS
        self.skill_configs = skill_configs
        self.embedding_provider = embedding_provider
        self.skills = {}
        self._init_skills()

    def _init_skills(self):
        import numpy as np
        for name, cls in HEARTS_SKILLS.items():
            try:
                skill_obj = Skill(
                    skill_name=name,
                    skill_function=cls.execute,
                    skill_embedding=np.array([]),
                    skill_code=inspect.getsource(cls),
                    skill_code_base64=base64.b64encode(
                        inspect.getsource(cls).encode("utf-8")
                    ).decode("utf-8"),
                )
                self.skills[name] = skill_obj
                logger.debug(f"Registered skill: {name}")
            except Exception as e:
                logger.error(f"Failed to register skill {name}: {e}")

    def retrieve_skills(self, query_task="", skill_num=10,
                        screen_type="general game interface", **kwargs):
        return list(self.skills.keys())

    def get_from_skill_library(self, skill_name: str,
                               skill_library_with_code: bool = False) -> Dict:
        if skill_name not in self.skills:
            return {}
        skill = self.skills[skill_name]
        func = skill.skill_function
        try:
            sig = inspect.signature(func)
            param_names = list(sig.parameters.keys())
            function_expression = (
                f"{skill_name}({', '.join(param_names)})"
                if param_names else f"{skill_name}()"
            )
        except Exception:
            function_expression = f"{skill_name}()"
        result = {
            "function_expression": function_expression,
            "description": inspect.getdoc(func) or "",
        }
        if skill_library_with_code:
            result["code"] = skill.skill_code
        return result

    def add_new_skill(self, skill_code: str, overwrite: bool = True) -> bool:
        return False

    def delete_skill(self, skill_name: str) -> bool:
        return False
