from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)

_MAX_TEXT_LEN = 2000  # 每封仅发送截断后的规范化文本片段（spec §12 最小化原则）

_PROMPT_TEMPLATE = """判断两段邮件正文是否为重复内容。
判定为重复：同一内容的不同版本、转发/回复导致的变化、格式差异。
判定为不重复：主题相关但实质内容不同。
仅输出 JSON：{{"duplicate": true}} 或 {{"duplicate": false}}

文本A：
{a}

文本B：
{b}"""


class LLMError(Exception):
    """LLM 不可用或响应无法解析。"""


def _extract_verdict(content: str) -> bool | None:
    """从响应中提取 {"duplicate": bool}；失败返回 None。"""
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(content[start: end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("duplicate"), bool):
        return data["duplicate"]
    return None


class NullLLM:
    def judge_pair(self, text_a: str, text_b: str) -> bool:
        raise LLMError("LLM_PROVIDER 未配置（none）")


class _ChatCompletionsLLM:
    """共享的 /chat/completions 调用骨架（stdlib urllib，无 SDK 依赖）。"""

    def __init__(self, api_key: str, model: str, base_url: str):
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._model = model

    def judge_pair(self, text_a: str, text_b: str) -> bool:
        prompt = _PROMPT_TEMPLATE.format(
            a=text_a[:_MAX_TEXT_LEN], b=text_b[:_MAX_TEXT_LEN]
        )
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 60,
        }
        request = urllib.request.Request(
            self._url, data=json.dumps(payload).encode("utf-8"),
            headers=self._headers,
        )
        with urllib.request.urlopen(request, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        verdict = _extract_verdict(content)
        if verdict is None:
            raise LLMError(f"无法解析 LLM 响应: {content[:120]}")
        return verdict


class DeepSeekLLM(_ChatCompletionsLLM):
    def __init__(self, api_key: str, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com"):
        super().__init__(api_key, model or "deepseek-chat",
                         base_url or "https://api.deepseek.com")


class OpenAILLM(_ChatCompletionsLLM):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 base_url: str = "https://api.openai.com/v1"):
        super().__init__(api_key, model or "gpt-4o-mini",
                         base_url or "https://api.openai.com/v1")


def create_llm(provider: str, api_key: str, model: str, base_url: str):
    if provider == "deepseek":
        return DeepSeekLLM(api_key, model, base_url)
    if provider == "openai":
        return OpenAILLM(api_key, model, base_url)
    return NullLLM()
