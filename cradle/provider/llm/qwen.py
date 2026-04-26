"""Qwen VLM provider using the OpenAI-compatible API.

Supports both cloud (DashScope) and local (vLLM / Ollama) endpoints.

Cloud config example (conf/qwen_config.json):
    {
        "key_var": "DASHSCOPE_API_KEY",
        "comp_model": "qwen-vl-max",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    }

Local config example:
    {
        "key_var": "EMPTY",
        "comp_model": "Qwen/Qwen2-VL-7B-Instruct",
        "api_base": "http://localhost:8000/v1"
    }
"""

import os
import re
import json
import base64
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Tuple, Union

import backoff
from openai import OpenAI, AsyncOpenAI, RateLimitError, APIError, APITimeoutError

from cradle import constants
from cradle.provider.base import LLMProvider
from cradle.config import Config
from cradle.log import Logger
from cradle.utils.json_utils import load_json
from cradle.utils.file_utils import assemble_project_path
from cradle.utils.encoding_utils import encode_data_to_base64_path

config = Config()
logger = Logger()

PROVIDER_SETTING_KEY_VAR = "key_var"
PROVIDER_SETTING_COMP_MODEL = "comp_model"
PROVIDER_SETTING_API_BASE = "api_base"

DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class QwenProvider(LLMProvider):
    """Qwen VLM provider via OpenAI-compatible API."""

    client: Any = None
    async_client: Any = None
    llm_model: str = ""

    def __init__(self) -> None:
        self.retries = 5

    def init_provider(self, provider_cfg) -> None:
        self.provider_cfg = self._parse_config(provider_cfg)

    def _parse_config(self, provider_cfg) -> dict:
        if isinstance(provider_cfg, dict):
            conf_dict = provider_cfg
        else:
            path = assemble_project_path(provider_cfg)
            conf_dict = load_json(path)

        key_var_name = conf_dict[PROVIDER_SETTING_KEY_VAR]
        api_key = os.getenv(key_var_name, "EMPTY")
        api_base = conf_dict.get(PROVIDER_SETTING_API_BASE, DEFAULT_API_BASE)

        self.client = OpenAI(api_key=api_key, base_url=api_base)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        self.llm_model = conf_dict[PROVIDER_SETTING_COMP_MODEL]

        logger.write(f"QwenProvider initialized: model={self.llm_model}, base={api_base}")
        return conf_dict

    def create_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str | None = None,
        temperature: float = config.temperature,
        seed: int = config.seed,
        max_tokens: int = config.max_tokens,
    ) -> Tuple[str, Dict[str, int]]:

        if model is None:
            model = self.llm_model

        logger.write(f"Requesting {model} completion...")

        @backoff.on_exception(
            backoff.constant,
            (APIError, RateLimitError, APITimeoutError),
            max_tries=self.retries,
            interval=10,
        )
        def _generate():
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = response.choices[0].message.content
            info = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
            logger.write(f"Response received from {model}.")
            return text, info

        return _generate()

    async def create_completion_async(
        self,
        messages: List[Dict[str, Any]],
        model: str | None = None,
        temperature: float = config.temperature,
        seed: int = config.seed,
        max_tokens: int = config.max_tokens,
    ) -> Tuple[str, Dict[str, int]]:

        if model is None:
            model = self.llm_model

        response = await self.async_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content
        info = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
        }
        return text, info

    def embed_query(self, query: str) -> list:
        raise NotImplementedError(
            "QwenProvider does not support text embeddings. "
            "ScummVM skill retrieval uses a fixed skill list and does not require embeddings."
        )

    def assemble_prompt(
        self, template_str: str = None, params: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        if config.DEFAULT_MESSAGE_CONSTRUCTION_MODE == constants.MESSAGE_CONSTRUCTION_MODE_TRIPART:
            return self._assemble_prompt_tripartite(template_str=template_str, params=params)
        return self._assemble_prompt_tripartite(template_str=template_str, params=params)

    def _assemble_prompt_tripartite(
        self, template_str: str = None, params: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """Build an OpenAI-format message list from a tripartite template.

        Structure:
          - First double-newline-separated paragraph → system message
          - Sections before <$image_introduction$> → user text part 1
          - <$image_introduction$> → image messages injected here
          - Sections after <$image_introduction$> → user text part 2
        """
        pattern = re.compile(r"(.+?)(?=\n\n|$)", re.DOTALL)
        paragraphs = [p for p in re.findall(pattern, template_str) if p.strip()]

        system_content = paragraphs[0]
        system_message = {"role": "system", "content": system_content}

        img_tag_idx = None
        for i, p in enumerate(paragraphs):
            if constants.IMAGES_INPUT_TAG in p:
                img_tag_idx = i
                break

        part1_paragraphs = paragraphs[1:img_tag_idx] if img_tag_idx is not None else paragraphs[1:]
        part2_paragraphs = paragraphs[img_tag_idx + 1:] if img_tag_idx is not None else []

        def fill_paragraphs(paras):
            filled = []
            ph_re = re.compile(r"<\$([^\$]+)\$>")
            for para in paras:
                m = ph_re.search(para)
                if not m:
                    filled.append(para)
                    continue
                key = m.group(1)
                val = params.get(key, None) if params else None
                if val is None or val == "" or val == []:
                    continue
                if isinstance(val, str):
                    filled.append(para.replace(m.group(0), val))
                elif isinstance(val, list):
                    filled.append(para.replace(m.group(0), json.dumps(val)))
                else:
                    filled.append(para.replace(m.group(0), str(val)))
            return "\n\n".join(filled)

        part1_text = fill_paragraphs(part1_paragraphs)
        part2_text = fill_paragraphs(part2_paragraphs)

        image_messages = self._build_image_messages(paragraphs, img_tag_idx, params)

        messages: List[Dict[str, Any]] = [system_message]
        if part1_text.strip():
            messages.append({"role": "user", "content": part1_text})
        messages.extend(image_messages)
        if part2_text.strip():
            messages.append({"role": "user", "content": part2_text})

        return self._merge_messages(messages)

    def _build_image_messages(self, paragraphs, img_tag_idx, params):
        if img_tag_idx is None or params is None:
            return []

        image_intro_paragraph = paragraphs[img_tag_idx]
        pre_text = image_intro_paragraph.replace(constants.IMAGES_INPUT_TAG, "").strip()

        image_items = params.get(constants.IMAGES_INPUT_TAG_NAME, [])
        if not image_items:
            return []

        msgs = []
        if pre_text:
            msgs.append({"role": "user", "content": pre_text})

        for item in image_items:
            intro = item.get(constants.IMAGE_INTRO_TAG_NAME, "")
            path = item.get(constants.IMAGE_PATH_TAG_NAME, "")
            assistant = item.get(constants.IMAGE_ASSISTANT_TAG_NAME, "")

            if intro:
                content: List[Dict[str, Any]] = [{"type": "text", "text": intro}]

                if path:
                    encoded_images = encode_data_to_base64_path(path)
                    for b64_data in encoded_images:
                        # encode_data_to_base64_path already returns the full data URL
                        url = b64_data if b64_data.startswith("data:") else f"data:image/jpeg;base64,{b64_data}"
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": url},
                        })

                msgs.append({"role": "user", "content": content})

            if assistant:
                msgs.append({"role": "assistant", "content": assistant})

        return msgs

    def _merge_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge consecutive messages with the same role."""
        merged = []
        for msg in messages:
            if not merged or msg["role"] != merged[-1]["role"]:
                merged.append({"role": msg["role"], "content": msg["content"]})
            else:
                prev = merged[-1]
                if isinstance(prev["content"], str) and isinstance(msg["content"], str):
                    prev["content"] = prev["content"] + "\n\n" + msg["content"]
                elif isinstance(prev["content"], list) and isinstance(msg["content"], list):
                    prev["content"] = prev["content"] + msg["content"]
                elif isinstance(prev["content"], str) and isinstance(msg["content"], list):
                    prev["content"] = [{"type": "text", "text": prev["content"]}] + msg["content"]
                elif isinstance(prev["content"], list) and isinstance(msg["content"], str):
                    prev["content"] = prev["content"] + [{"type": "text", "text": msg["content"]}]
        return merged
