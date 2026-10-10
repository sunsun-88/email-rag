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
