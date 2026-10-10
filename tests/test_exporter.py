import json

import yaml

from email_rag.exporter import export_all, render_markdown, slugify


def _insert(store, uid, subject, body, status="unique", content_hash="",
            folder="INBOX", date="2024-03-15T09:30:00+08:00"):
    raw_id = store.upsert_raw(folder, uid, b"x")
    eid = store.insert_email(raw_id, folder, {
        "subject": subject, "sender": "张三 <zhang@example.com>",
        "to": ["li@example.com"], "cc": [], "date": date, "message_id": f"<m{uid}@t>",
        "in_reply_to": "", "references": [], "body_text": body, "body_html": "",
        "norm_text": body, "simhash": 1, "content_hash": content_hash or f"hash{uid}",
        "parse_error": "",
    })
    if status != "pending":
        store.update_dedup(eid, status)
    return eid


def test_slugify_cjk_and_symbols():
    assert slugify("关于会议的通知！") == "关于会议的通知"
    assert slugify("") == "untitled"
    assert slugify("Re: [list] Hello World") == "re-list-hello-world"
    assert len(slugify("长" * 100, max_len=40)) <= 40


def test_render_markdown_frontmatter():
    record = {
        "subject": "主题", "sender": "张三 <zhang@example.com>",
        "recipients": '["li@example.com"]', "cc": "[]",
        "date": "2024-03-15T09:30:00+08:00", "folder": "INBOX",
        "message_id": "<m1@t>", "dedup_status": "kept",
        "content_hash": "abcdef0123456789", "body_text": "正文内容",
    }
    md = render_markdown(record)
    assert md.startswith("---\n")
    assert 'subject: "主题"' in md
    assert 'from: "张三 <zhang@example.com>"' in md
    assert 'to: ["li@example.com"]' in md
    assert 'dedup: "kept"' in md
    assert 'content_hash: "abcdef01"' in md  # 只取前 8 位
    assert md.rstrip().endswith("正文内容")
    # frontmatter 可被 yaml 解析
    front = md.split("---\n")[1]
    parsed = yaml.safe_load(front)
    assert parsed["subject"] == "主题"
    assert parsed["to"] == ["li@example.com"]


def test_export_creates_directory_structure(store, tmp_path):
    _insert(store, 1, "关于会议的通知", "正文一", status="kept",
            content_hash="a" * 64, date="2024-03-15T09:30:00+08:00")
    report = export_all(store, tmp_path / "out")
    assert report["exported"] == 1
    files = list((tmp_path / "out").rglob("*.md"))
    assert len(files) == 1
    assert files[0].parent.name == "2024-03"  # 月份目录
    assert files[0].name.startswith("20240315-关于会议的通知-aaaaaaaa")
    assert files[0].parent.parent.name == "inbox"  # 文件夹 slug 小写


def test_duplicates_and_errors_not_exported(store, tmp_path):
    _insert(store, 1, "唯一", "x", status="unique")
    _insert(store, 2, "重复", "x", status="duplicate")
    _insert(store, 3, "待复核", "x", status="review")
    report = export_all(store, tmp_path / "out")
    assert report["exported"] == 1


def test_export_idempotent_overwrite(store, tmp_path):
    _insert(store, 1, "同名主题", "x", status="unique", content_hash="b" * 64)
    export_all(store, tmp_path / "out")
    export_all(store, tmp_path / "out")  # 第二次导出
    files = list((tmp_path / "out").rglob("*.md"))
    assert len(files) == 1  # 同名覆盖，不产生重复文件
