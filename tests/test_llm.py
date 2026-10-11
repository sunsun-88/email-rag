import io
import json

import pytest

from email_rag.llm import (
    LLMError,
    NullLLM,
    _extract_verdict,
    create_llm,
    DeepSeekLLM,
)


def test_extract_verdict_pure_json():
    assert _extract_verdict('{"duplicate": true}') is True
    assert _extract_verdict('{"duplicate": false}') is False


def test_extract_verdict_with_prose():
    content = '根据分析，两者是重复内容。\n{"duplicate": true}\n以上。'
    assert _extract_verdict(content) is True


def test_extract_verdict_invalid_returns_none():
    assert _extract_verdict("我无法判断") is None
    assert _extract_verdict('{"other": 1}') is None


def test_null_llm_always_raises():
    with pytest.raises(LLMError):
        NullLLM().judge_pair("a", "b")


def test_create_llm_mapping():
    assert isinstance(create_llm("none", "", "", ""), NullLLM)
    assert isinstance(create_llm("deepseek", "k", "", ""), DeepSeekLLM)
    assert isinstance(create_llm("unknown-provider", "k", "", ""), NullLLM)


def _fake_urlopen(monkeypatch, payload: dict):
    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake(request, timeout=None):
        return FakeResp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake)


def test_deepseek_llm_judge_pair(monkeypatch):
    _fake_urlopen(monkeypatch, {
        "choices": [{"message": {"content": '{"duplicate": true}'}}]
    })
    llm = DeepSeekLLM(api_key="key", model="deepseek-chat")
    assert llm.judge_pair("文本A", "文本B") is True


# ---------- I-4 空 base_url 回退默认值 ----------

def test_create_llm_default_base_urls():
    assert create_llm("deepseek", "k", "", "")._url == "https://api.deepseek.com/chat/completions"
    assert create_llm("openai", "k", "", "")._url == "https://api.openai.com/v1/chat/completions"


def test_create_llm_explicit_base_url_honored():
    llm = create_llm("deepseek", "k", "deepseek-reasoner", "https://proxy.example.com")
    assert llm._url == "https://proxy.example.com/chat/completions"


def test_create_llm_generic_without_provider():
    """只填 api_key + model + base_url、provider 留空 → 通用 OpenAI 兼容客户端。"""
    llm = create_llm("", "k", "my-model", "https://api.example.com")
    assert llm._url == "https://api.example.com/chat/completions"
    assert llm._model == "my-model"


def test_create_llm_generic_requires_base_url():
    """provider 留空且没有 base_url → 无法确定端点，返回 NullLLM。"""
    assert isinstance(create_llm("", "k", "m", ""), NullLLM)
    assert isinstance(create_llm("none", "k", "m", ""), NullLLM)


def test_create_llm_local_server_without_api_key():
    """本地 OpenAI 兼容服务（vLLM 等）通常不需要 key：只给 base_url 即可，
    且请求头不应带 Authorization。"""
    llm = create_llm("", "", "m", "http://192.0.2.10:8080/v1")
    assert llm._url == "http://192.0.2.10:8080/v1/chat/completions"
    assert "Authorization" not in llm._headers


def test_local_llm_judge_pair_sends_no_auth_header(monkeypatch):
    import io
    import json as jsonlib

    captured = {}

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["headers"] = dict(request.header_items())
        payload = jsonlib.dumps(
            {"choices": [{"message": {"content": '{"duplicate": true}'}}]}
        ).encode("utf-8")
        return FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    llm = create_llm("", "", "m", "http://h/v1")
    assert llm.judge_pair("a", "b") is True
    assert captured["headers"].get("Authorization") is None


def _fake_urlopen_capturing(monkeypatch, captured, response_content):
    import io
    import json as jsonlib

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["body"] = jsonlib.loads(request.data.decode("utf-8"))
        payload = jsonlib.dumps(
            {"choices": [{"message": {"content": response_content}}]}
        ).encode("utf-8")
        return FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_judge_batch_success_and_token_cap(monkeypatch):
    """批量裁定：一次调用返回全部 verdict；默认预算=200×组数+512（思考余量）。"""
    from email_rag.llm import _ChatCompletionsLLM

    captured = {}
    _fake_urlopen_capturing(
        monkeypatch, captured,
        '[{"i": 0, "dup": true}, {"i": 1, "dup": false}, {"i": 2, "dup": true}]')
    llm = _ChatCompletionsLLM("k", "m", "http://h/v1")
    result = llm.judge_batch([("a1", "b1"), ("a2", "b2"), ("a3", "b3")])
    assert result == [True, False, True]
    assert captured["body"]["max_tokens"] == 2176  # 384×3 + 1024


def test_judge_batch_reads_reasoning_content(monkeypatch):
    """reasoning 模型的答案可能在 reasoning_content（content 为空）也要解析。"""
    from email_rag.llm import _ChatCompletionsLLM

    captured = {}
    _fake_urlopen_capturing(monkeypatch, captured, "")  # content 为空
    # 手工构造：verdict 藏在 reasoning_content 里
    import io
    import json as jsonlib

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["body"] = jsonlib.loads(request.data.decode("utf-8"))
        payload = jsonlib.dumps({"choices": [{"message": {
            "role": "assistant",
            "content": "",
            "reasoning_content": "分析……最终结论：[{\"i\": 0, \"dup\": true}]",
        }}]}).encode("utf-8")
        return FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    llm = _ChatCompletionsLLM("k", "m", "http://h/v1")
    assert llm.judge_batch([("a", "b")]) == [True]


def test_judge_batch_custom_max_tokens(monkeypatch):
    """max_tokens 可显式覆盖（judge_pairs_with_llm 的 output_tokens_per_pair 用）。"""
    from email_rag.llm import _ChatCompletionsLLM

    captured = {}
    _fake_urlopen_capturing(monkeypatch, captured, '[{"i": 0, "dup": true}]')
    llm = _ChatCompletionsLLM("k", "m", "http://h/v1")
    llm.judge_batch([("a", "b")], max_tokens=42)
    assert captured["body"]["max_tokens"] == 42


def test_judge_batch_partial_and_invalid(monkeypatch):
    """批量响应缺项/非法 → 对应位置为 None，不抛异常。"""
    from email_rag.llm import _ChatCompletionsLLM, _extract_batch_verdicts

    captured = {}
    _fake_urlopen_capturing(
        monkeypatch, captured, '[{"i": 1, "dup": false}]')  # 缺第 0、2 组
    llm = _ChatCompletionsLLM("k", "m", "http://h/v1")
    result = llm.judge_batch([("a", "b"), ("c", "d"), ("e", "f")])
    assert result[1] is False
    assert result[0] is None and result[2] is None

    _fake_urlopen_capturing(monkeypatch, captured, "不是 JSON")
    result2 = llm.judge_batch([("a", "b")])
    assert result2 == [None]

    assert _extract_batch_verdicts("垃圾", 2) == [None, None]
