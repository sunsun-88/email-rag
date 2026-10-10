import pytest

from email_rag.store import Store


def _parsed(**kw):
    base = {
        "subject": "s", "sender": "a@b.c", "to": ["x@y.z"], "cc": [],
        "date": "2024-03-15T09:30:00+08:00", "message_id": "<m1@t>",
        "in_reply_to": "", "references": ["<r1@t>"],
        "body_text": "hello", "body_html": "", "norm_text": "hello",
        "simhash": 7, "content_hash": "abc", "parse_error": "",
    }
    base.update(kw)
    return base


def test_upsert_raw_idempotent(store):
    assert store.upsert_raw("INBOX", 1, b"raw-bytes") is True
    assert store.upsert_raw("INBOX", 1, b"raw-bytes") is False
    assert store.counts()["raw"] == 1


def test_watermark(store):
    assert store.watermark("INBOX") == 0
    for uid in (1, 2, 5):
        store.upsert_raw("INBOX", uid, b"x")
    assert store.watermark("INBOX") == 5
    assert store.watermark("Other") == 0


def test_insert_and_get_email(store):
    raw_id = store.upsert_raw("INBOX", 1, b"x")
    eid = store.insert_email(raw_id, "INBOX", _parsed())
    row = store.get_email(eid)
    assert row["subject"] == "s"
    assert row["recipients"] == '["x@y.z"]'  # list → JSON
    assert row["references"] == '["<r1@t>"]'
    assert row["dedup_status"] == "pending"


def test_iter_unparsed_raw_excludes_parsed(store):
    r1 = store.upsert_raw("INBOX", 1, b"aaa")
    store.upsert_raw("INBOX", 2, b"bbb")
    store.insert_email(r1, "INBOX", _parsed())  # insert_email 同事务标记 raw 已解析
    remaining = list(store.iter_unparsed_raw())
    assert len(remaining) == 1
    assert remaining[0]["mime_bytes"] == b"bbb"
    store.insert_email(remaining[0]["id"], "INBOX", _parsed())  # 处理剩余 raw，同事务置位
    assert list(store.iter_unparsed_raw()) == []  # 幂等


def test_iter_unparsed_raw_keyset_no_skip(store):
    """消费中置位 parsed=1 时 OFFSET 翻页会漏行；keyset 分页须一轮收齐。"""
    for uid in range(1, 6):
        store.upsert_raw("INBOX", uid, f"raw-{uid}".encode())
    processed = 0
    for row in store.iter_unparsed_raw(batch=2):  # batch 小于总行数，边消费边置位
        store.insert_email(row["id"], row["folder"], _parsed())
        processed += 1
    assert processed == 5
    assert list(store.iter_unparsed_raw()) == []  # 一轮后无剩余


def test_dedup_status_flow(store):
    r = store.upsert_raw("INBOX", 1, b"x")
    eid = store.insert_email(r, "INBOX", _parsed())
    assert len(list(store.iter_dedup_candidates())) == 1
    store.update_dedup(eid, "kept")
    assert store.finalize_uniques() == 0
    assert store.get_email(eid)["dedup_status"] == "kept"
    assert list(store.iter_exportable())[0]["id"] == eid


def test_finalize_uniques(store):
    r1 = store.upsert_raw("INBOX", 1, b"x")
    r2 = store.upsert_raw("INBOX", 2, b"y")
    store.insert_email(r1, "INBOX", _parsed(message_id="<a@t>"))
    store.insert_email(r2, "INBOX", _parsed(message_id="<b@t>"))
    assert store.finalize_uniques() == 2
    counts = store.counts()
    assert counts["by_status"]["unique"] == 2


def test_parse_error_rows_not_exportable(store):
    r = store.upsert_raw("INBOX", 1, b"x")
    eid = store.insert_email(r, "INBOX", _parsed(parse_error="HeaderParseError"))
    store.update_dedup(eid, "unique")
    assert list(store.iter_exportable()) == []


def test_reset_dedup(store):
    r1 = store.upsert_raw("INBOX", 1, b"x")
    e1 = store.insert_email(r1, "INBOX", _parsed())
    store.update_dedup(e1, "kept")
    assert store.reset_dedup() == 1
    row = store.get_email(e1)
    assert row["dedup_status"] == "pending"
    assert row["duplicate_of"] is None
