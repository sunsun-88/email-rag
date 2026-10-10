import base64

from conftest import build_eml
from email_rag.parser import (
    decode_bytes,
    normalize_subject,
    normalize_text,
    parse_raw,
)


def test_simple_chinese_plaintext():
    result = parse_raw(build_eml(subject="关于会议的通知",
                                 body="会议改期到明天上午十点。"), "INBOX")
    assert result["parse_error"] == ""
    assert result["subject"] == "关于会议的通知"
    assert result["sender"] == "Alice <alice@example.com>"
    assert result["to"] == ["bob@example.com"]
    assert result["message_id"] == "<msg-001@test.com>"
    assert "会议改期" in result["body_text"]
    assert result["date"].startswith("2024-03-15")


def test_html_only_body_degraded_to_text():
    result = parse_raw(build_eml(html="<html><body><p>季度报告已发布</p></body></html>",
                                 message_id="<html-001@test.com>", body=""),
                       "INBOX")
    assert "季度报告" in result["body_text"]
    assert "<p>" in result["body_html"]


def test_multipart_alternative_prefers_plain():
    raw = build_eml(subject="双版本", body="纯文本版本正文。",
                    html="<html><body><p>HTML版本正文</p></body></html>")
    result = parse_raw(raw, "INBOX")
    assert "纯文本版本" in result["body_text"]
    assert "HTML版本" in result["body_html"]


def test_gb18030_charset():
    enc = base64.b64encode("季度报告".encode("gb18030")).decode("ascii")
    raw = (
        f"From: =?GB18030?B?{enc}?= <zhang@test.com>\r\n"
        f"To: li@test.com\r\n"
        f"Subject: =?GB18030?B?{enc}?=\r\n"
        "Date: Mon, 15 Mar 2024 09:30:00 +0800\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=GB18030\r\n"
        "\r\n"
    ).encode("ascii") + "关于会议改期的通知内容".encode("gb18030") + b"\r\n"
    result = parse_raw(raw, "INBOX")
    assert result["subject"] == "季度报告"
    assert "会议改期" in result["body_text"]


def test_missing_message_id():
    result = parse_raw(build_eml(message_id=""), "INBOX")
    assert result["message_id"] == ""


def test_references_and_in_reply_to():
    result = parse_raw(build_eml(in_reply_to="<parent@test.com>",
                                 references="<root@test.com> <parent@test.com>"), "INBOX")
    assert result["in_reply_to"] == "<parent@test.com>"
    assert result["references"] == ["<root@test.com>", "<parent@test.com>"]


def test_attachment_ignored():
    result = parse_raw(build_eml(body="", attachments=[("report.txt", "附件内容不应出现")]),
                       "INBOX")
    assert result["body_text"] == ""
    assert "附件内容" not in result["body_text"]


def test_malformed_bytes_never_raise():
    result = parse_raw(b"\xff\xfe\x00 not a mime at all \x80", "INBOX")
    assert {"subject", "body_text", "parse_error", "simhash", "content_hash"} <= set(result)


def test_parse_error_captured(monkeypatch):
    from email_rag import parser as parser_module

    def _boom(*a, **k):
        raise ValueError("bad mime")

    monkeypatch.setattr(parser_module, "parse_mime", _boom)
    result = parse_raw(b"x", "INBOX")
    assert result["parse_error"].startswith("ValueError")
    assert result["body_text"] == ""
    assert result["simhash"] == 0


def test_normalize_text_strips_signature_and_whitespace():
    text = "Hello World\n\n-- \n张三\n发送自我的手机"
    assert normalize_text(text) == "hello world"


def test_normalize_subject_strips_reply_prefixes():
    assert normalize_subject("Re: Fwd: 回复：转发：季度报告") == "季度报告"
    assert normalize_subject("RE: [list] 会议通知") == "[list] 会议通知"


def test_decode_bytes_fallback_chain():
    assert decode_bytes("中文".encode("gb18030"), None) == "中文"
    assert decode_bytes(b"hello", "unknown-charset") == "hello"
    assert decode_bytes(b"\xff\xfe", "utf-8") != ""  # 不抛异常


def test_parse_raw_computes_norm_simhash_hash():
    result = parse_raw(build_eml(body="关于会议改期的通知"), "INBOX")
    assert result["norm_text"] == normalize_text("关于会议改期的通知")
    assert result["simhash"] != 0
    assert len(result["content_hash"]) == 64  # sha256 hex
