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
