"""Skill registry for King's Quest IV via ScummVM."""

import inspect
import base64
from typing import Dict, Any

from cradle import constants
from cradle.config.config import Config
from cradle.log import Logger
from cradle.environment import SkillRegistry
from cradle.environment import Skill
from cradle.utils.singleton import Singleton

from cradle.environment.scummvm.atomic_skills.kq4_skills import MoveTo, TypeCommand
from cradle.environment.scummvm.atomic_skills.game_state_skills import Quicksave, Quickload

config = Config()
logger = Logger()

KQ4_SKILLS = {
    "move_to": MoveTo,
    "type_command": TypeCommand,
    "quicksave": Quicksave,
    "quickload": Quickload,
}


class ScummVMSkillRegistry(SkillRegistry, metaclass=Singleton):
    """Skill registry for King's Quest IV (ScummVM text-parser adventure)."""

    def __init__(self,
                 *args,
                 skill_configs: Dict[str, Any] = None,
                 embedding_provider=None,
                 **kwargs):

        if skill_configs is None:
            skill_configs = config.skill_configs

        skill_configs[constants.SKILL_CONFIG_REGISTERED_SKILLS] = KQ4_SKILLS

        self.skill_configs = skill_configs
        self.embedding_provider = embedding_provider
        self.skills = {}

        self._init_skills()

    def _init_skills(self):
        """Register all KQ4 skills."""
        import numpy as np
        for skill_name, skill_class in KQ4_SKILLS.items():
            try:
                skill_obj = Skill(
                    skill_name=skill_name,
                    skill_function=skill_class.execute,
                    skill_embedding=np.array([]),
                    skill_code=inspect.getsource(skill_class),
                    skill_code_base64=base64.b64encode(
                        inspect.getsource(skill_class).encode('utf-8')
                    ).decode('utf-8')
                )
                self.skills[skill_name] = skill_obj
                logger.debug(f"Registered skill: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to register skill {skill_name}: {e}")

    def retrieve_skills(self,
                        query_task: str = "",
                        skill_num: int = 10,
                        screen_type: str = "general game interface",
                        **kwargs) -> list:
        """Return all registered skill names."""
        return list(self.skills.keys())

    def get_from_skill_library(self,
                               skill_name: str,
                               skill_library_with_code: bool = False) -> Dict:
        """Get a skill's signature and description."""
        if skill_name not in self.skills:
            return {}

        skill = self.skills[skill_name]
        func = skill.skill_function

        try:
            sig = inspect.signature(func)
            param_names = list(sig.parameters.keys())
            function_expression = (
                f"{skill_name}({', '.join(param_names)})" if param_names else f"{skill_name}()"
            )
        except Exception:
            function_expression = f"{skill_name}()"

        description = inspect.getdoc(func) or ""

        result = {
            "function_expression": function_expression,
            "description": description,
        }

        if skill_library_with_code:
            result["code"] = skill.skill_code

        return result

    def add_new_skill(self, skill_code: str, overwrite: bool = True) -> bool:
        return False

    def delete_skill(self, skill_name: str) -> bool:
        return False
