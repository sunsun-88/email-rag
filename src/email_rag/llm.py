from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)

_MAX_TEXT_LEN = 2000  # 每封仅发送截断后的规范化文本片段（spec §12 最小化原则）
_SINGLE_MAX_TOKENS = 256      # 单对裁定的输出上限
_DEFAULT_TOKENS_PER_PAIR = 200  # 批量裁定每对的输出预算（实测模型约 167/对）

_PROMPT_TEMPLATE = """判断两段邮件正文是否为重复内容。
判定为重复：同一内容的不同版本、转发/回复导致的变化、格式差异。
判定为不重复：主题相关但实质内容不同。
仅输出 JSON：{{"duplicate": true}} 或 {{"duplicate": false}}

文本A：
{a}

文本B：
{b}"""

_BATCH_PROMPT_TEMPLATE = """判断下面 {n} 组邮件正文各自是否为重复内容。
判定为重复：同一内容的不同版本、转发/回复导致的变化、格式差异。
判定为不重复：主题相关但实质内容不同。
不要分析过程，直接输出结果。仅输出 JSON 数组，形如：[{{"i": 0, "dup": true}}, {{"i": 1, "dup": false}}]，不要输出任何其他内容。

{groups}"""


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


def _extract_batch_verdicts(content: str, expected: int) -> list[bool | None]:
    """从批量响应中提取判定数组；缺失/非法项为 None。长度恒等于 expected。"""
    start = content.find("[")
    end = content.rfind("]")
    results: list[bool | None] = [None] * expected
    if start == -1 or end == -1 or end <= start:
        return results
    try:
        data = json.loads(content[start: end + 1])
    except json.JSONDecodeError:
        return results
    if not isinstance(data, list):
        return results
    for item in data:
        if isinstance(item, dict):
            idx, dup = item.get("i"), item.get("dup")
            if isinstance(idx, int) and isinstance(dup, bool) and 0 <= idx < expected:
                results[idx] = dup
    return results


class NullLLM:
    def judge_pair(self, text_a: str, text_b: str) -> bool:
        raise LLMError("LLM 未配置：请填写 LLM_API_KEY + LLM_BASE_URL"
                       "（或 LLM_PROVIDER=deepseek/openai 使用默认端点）")


class _ChatCompletionsLLM:
    """共享的 /chat/completions 调用骨架（stdlib urllib，无 SDK 依赖）。"""

    def __init__(self, api_key: str, model: str, base_url: str):
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Content-Type": "application/json"}
        if api_key:  # 本地服务（vLLM 等）可不带密钥
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._model = model

    def judge_pair(self, text_a: str, text_b: str) -> bool:
        prompt = _PROMPT_TEMPLATE.format(
            a=text_a[:_MAX_TEXT_LEN], b=text_b[:_MAX_TEXT_LEN]
        )
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": _SINGLE_MAX_TOKENS,
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

    def judge_batch(self, text_pairs: list[tuple[str, str]],
                    max_tokens: int | None = None) -> list[bool | None]:
        """批量裁定：一次调用判多组，显著减少请求次数。

        返回与输入等长的判定列表（None=未判定）。输出上限默认
        _DEFAULT_TOKENS_PER_PAIR × 组数（截断会导致 JSON 残缺解析失败），
        可用 max_tokens 显式覆盖；生成耗时随上限增长，超时放宽到 300 秒。
        """
        if not text_pairs:
            return []
        groups = "\n\n".join(
            f"【第{i}组】\n文本A：\n{a[:_MAX_TEXT_LEN]}\n文本B：\n{b[:_MAX_TEXT_LEN]}"
            for i, (a, b) in enumerate(text_pairs)
        )
        prompt = _BATCH_PROMPT_TEMPLATE.format(n=len(text_pairs), groups=groups)
        budget = max_tokens or _DEFAULT_TOKENS_PER_PAIR * len(text_pairs)
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": budget,
        }
        request = urllib.request.Request(
            self._url, data=json.dumps(payload).encode("utf-8"),
            headers=self._headers,
        )
        with urllib.request.urlopen(request, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return _extract_batch_verdicts(content, len(text_pairs))


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
    """创建 LLM 客户端。provider 仅用于缺省端点/模型，显式给出 base_url 时无需填写。

    - 通用填法（推荐）：LLM_API_KEY + LLM_MODEL + LLM_BASE_URL，provider 留空，
      走 OpenAI 兼容的 /chat/completions 协议（DeepSeek/本地 vLLM/Ollama 等均兼容）。
      本地服务无需密钥时，LLM_API_KEY 可留空，只填地址和模型。
    - 偷懒填法：LLM_PROVIDER=deepseek 或 openai，只填 LLM_API_KEY，端点/模型用默认。
    - 两者都缺端点信息（无 base_url 且无认识的 provider）→ NullLLM。
    """
    if provider == "deepseek":
        return DeepSeekLLM(api_key, model, base_url)
    if provider == "openai":
        return OpenAILLM(api_key, model, base_url)
    if base_url:
        return _ChatCompletionsLLM(api_key, model, base_url)
    return NullLLM()
