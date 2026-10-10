from __future__ import annotations

import email
import email.policy
import hashlib
import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

import html2text

from .simhash import simhash64, tokenize

SIGNATURE_RE = re.compile(r"^\s*--\s*$", re.MULTILINE)
SUBJECT_PREFIX_RE = re.compile(
    r"^(re|fw|fwd|回复|答复|转发)\s*[:：]\s*", re.IGNORECASE
)


def decode_str(value: str | None) -> str:
    """解码 RFC 2047 encoded-word 主题/地址头。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def decode_bytes(data: bytes, charset: str | None) -> str:
    candidates = []
    if charset:
        candidates.append(charset)
    candidates += ["utf-8", "gb18030", "latin-1"]
    for cs in candidates:
        try:
            return data.decode(cs)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def normalize_text(text: str) -> str:
    """去签名块、压缩空白、小写，供去重比较。"""
    sig = SIGNATURE_RE.search(text)
    if sig:
        text = text[: sig.start()]
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def normalize_subject(subject: str) -> str:
    s = decode_str(subject).strip().lower()
    prev = None
    while prev != s:
        prev = s
        s = SUBJECT_PREFIX_RE.sub("", s).strip()
    return s


def _html_to_text(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = True
    return converter.handle(html).strip()


def _iter_parts(container: Message):
    """遍历 multipart 子部件；message/*（转发附件）子树不下钻。"""
    if not container.is_multipart():
        return
    for part in container.get_payload():
        if not isinstance(part, Message):
            continue
        yield part
        if part.get_content_type().startswith("message/"):
            continue
        yield from _iter_parts(part)


def _extract_body(msg: Message) -> tuple[str, str]:
    text_part: Message | None = None
    html_part: Message | None = None
    if msg.is_multipart():
        if not msg.get_content_type().startswith("message/"):
            for part in _iter_parts(msg):
                disposition = str(part.get("Content-Disposition", "")).lower()
                if "attachment" in disposition:
                    continue  # 附件永不进入正文
                ctype = part.get_content_type()
                if ctype == "text/plain" and text_part is None:
                    text_part = part
                elif ctype == "text/html" and html_part is None:
                    html_part = part
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            text_part = msg
        elif ctype == "text/html":
            html_part = msg

    body_text, body_html = "", ""
    if text_part is not None:
        payload = text_part.get_payload(decode=True)
        if payload:
            body_text = decode_bytes(payload, text_part.get_content_charset())
    if html_part is not None:
        payload = html_part.get_payload(decode=True)
        if payload:
            body_html = decode_bytes(payload, html_part.get_content_charset())
    if not body_text.strip() and body_html:
        body_text = _html_to_text(body_html)
    return body_text.strip(), body_html.strip()


def parse_mime(mime_bytes: bytes, folder: str) -> dict:
    msg = email.message_from_bytes(mime_bytes, policy=email.policy.default)
    body_text, body_html = _extract_body(msg)

    date_str = ""
    raw_date = msg.get("Date")
    if raw_date:
        try:
            date_str = parsedate_to_datetime(str(raw_date)).isoformat()
        except Exception:
            date_str = str(raw_date)

    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
    references_raw = msg.get("References", "")
    references = references_raw.split() if isinstance(references_raw, str) else []

    return {
        "folder": folder,
        "subject": decode_str(msg.get("Subject", "")),
        "sender": decode_str(msg.get("From", "")),
        "to": [decode_str(x) for x in str(msg.get("To", "")).split(",") if x.strip()],
        "cc": [decode_str(x) for x in str(msg.get("Cc", "")).split(",") if x.strip()],
        "date": date_str,
        "message_id": message_id,
        "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
        "references": references,
        "body_text": body_text,
        "body_html": body_html,
    }


def parse_raw(mime_bytes: bytes, folder: str) -> dict:
    """解析 + 规范化 + 哈希。永不抛异常。"""
    empty = {
        "subject": "", "sender": "", "to": [], "cc": [], "date": "",
        "message_id": "", "in_reply_to": "", "references": [],
        "body_text": "", "body_html": "", "norm_text": "",
        "simhash": 0, "content_hash": "", "parse_error": "",
    }
    try:
        parsed = parse_mime(mime_bytes, folder)
    except Exception as exc:  # noqa: BLE001
        return {**empty, "parse_error": f"{type(exc).__name__}: {exc}"}

    norm = normalize_text(parsed["body_text"] or parsed["subject"])
    return {
        **parsed,
        "norm_text": norm,
        "simhash": simhash64(tokenize(norm)),
        # 空 norm 不计算哈希：sha256("") 是常量，否则不同邮件会被当完全重复
        "content_hash": hashlib.sha256(norm.encode("utf-8")).hexdigest() if norm else "",
        "parse_error": "",
    }
