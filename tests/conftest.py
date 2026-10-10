from email.message import EmailMessage

import pytest

from email_rag.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.init_schema()
    yield s
    s.close()


def build_eml(subject="测试主题", sender="Alice <alice@example.com>", to="bob@example.com",
              cc="", body="这是正文内容。", html=None, charset="utf-8",
              message_id="<msg-001@test.com>", in_reply_to="", references="",
              attachments=None, date="Mon, 15 Mar 2024 09:30:00 +0800"):
    """用 stdlib email 构造测试邮件，返回 bytes。"""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = date
    if message_id:
        msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body, charset=charset)
    if html is not None:
        msg.add_alternative(html, subtype="html", charset=charset)
    for name, data in (attachments or []):
        msg.add_attachment(data.encode("utf-8"), maintype="text",
                           subtype="plain", filename=name)
    return msg.as_bytes()
