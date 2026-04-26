from .base import BaseProvider
from .base.base_provider import BaseModuleProvider
from .base.base_embedding import EmbeddingProvider
from .base.base_llm import LLMProvider

from .llm.openai import OpenAIProvider
from .llm.claude import ClaudeProvider
from .llm.restful_claude import RestfulClaudeProvider

from .circle_detector import CircleDetectProvider
from .sam_provider import SamProvider
from .object_detect.gd_provider import GdProvider

from .video.video_ocr_extractor import VideoOCRExtractorProvider
from .video.video_recorder import VideoRecordProvider
from .video.video_frame_extractor import VideoFrameExtractorProvider
from .video.video_clip import VideoClipProvider

from .process.action_planning import (ActionPlanningPreprocessProvider,
                                      ActionPlanningPostprocessProvider)

from .process.information_gathering import (InformationGatheringPreprocessProvider,
                                            InformationGatheringPostprocessProvider)

from .process.self_reflection import (SelfReflectionPreprocessProvider,
                                      SelfReflectionPostprocessProvider)

from .process.task_inference import (TaskInferencePreprocessProvider,
                                     TaskInferencePostprocessProvider)

from .module.information_gathering import InformationGatheringProvider
from .module.self_reflection import SelfReflectionProvider
from .module.action_planning import ActionPlanningProvider
from .module.task_inference import TaskInferenceProvider
from .module.skill_curation import SkillCurationProvider

from .execute.skill_execute import SkillExecuteProvider

from .augment.augment import AugmentProvider

from .others.coordinates import CoordinatesProvider
from .others.task_guidance import TaskGuidanceProvider


__all__ = [
    # Base provider
    "BaseProvider",

    # LLM providers
    "LLMProvider",
    "EmbeddingProvider",
    "OpenAIProvider",
    "ClaudeProvider",
    "RestfulClaudeProvider",

    # Object detection provider
    "GdProvider",

    # Video provider
    "VideoOCRExtractorProvider",
    "VideoRecordProvider",
    "VideoFrameExtractorProvider",
    "VideoClipProvider"

    # Augmentation providers
    "AugmentProvider",

    # Others
    "CoordinatesProvider",
    "TaskGuidanceProvider",

    # ???
    "CircleDetectProvider",
    "SamProvider",

    # Process provider
    "SkillExecuteProvider",
    "ActionPlanningPreprocessProvider",
    "ActionPlanningPostprocessProvider",
    "InformationGatheringPreprocessProvider",
    "InformationGatheringPostprocessProvider",
    "SelfReflectionPreprocessProvider",
    "SelfReflectionPostprocessProvider",
    "TaskInferencePreprocessProvider",
    "TaskInferencePostprocessProvider",

    # Module provider
    "InformationGatheringProvider",
    "SelfReflectionProvider",
    "ActionPlanningProvider",
    "TaskInferenceProvider",
    "SkillCurationProvider",
]
