from cradle.provider.llm.openai import OpenAIProvider
from cradle.provider.llm.restful_claude import RestfulClaudeProvider
from cradle.provider.llm.qwen import QwenProvider
from cradle.utils import Singleton


class LLMFactory(metaclass=Singleton):

    def __init__(self):
        self._builders = {}


    def create(self, llm_provider_config_path, embed_provider_config_path, **kwargs):

        llm_provider = None
        embed_provider = None

        key = llm_provider_config_path

        if "openai" in key:
            llm_provider = OpenAIProvider()
            llm_provider.init_provider(llm_provider_config_path)
            embed_provider = llm_provider
        elif "claude" in key:
            llm_provider = RestfulClaudeProvider()
            llm_provider.init_provider(llm_provider_config_path)
            embed_provider = OpenAIProvider()
            embed_provider.init_provider(embed_provider_config_path)
        elif "qwen" in key:
            llm_provider = QwenProvider()
            llm_provider.init_provider(llm_provider_config_path)
            # ScummVM does not use embeddings for skill retrieval; re-use provider as placeholder
            embed_provider = llm_provider

        if not llm_provider or not embed_provider:
            raise ValueError(f"Unknown LLM provider config: {key}")

        return llm_provider, embed_provider
