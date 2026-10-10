import json

from email_rag import dedup as dedup_module
from email_rag.dedup import (
    ReviewPair,
    apply_review,
    exact_dedup,
    near_dedup,
    write_review_queue,
)


def _insert(store, uid, subject="主题", sender="a@b.c", body="正文内容",
            message_id="", content_hash="", simhash=0, date="2024-03-15T09:30:00+08:00",
            folder="INBOX", norm_text=None):
    raw_id = store.upsert_raw(folder, uid, b"x")
    parsed = {
        "subject": subject, "sender": sender, "to": [], "cc": [], "date": date,
        "message_id": message_id, "in_reply_to": "", "references": [],
        "body_text": body, "body_html": "",
        "norm_text": body if norm_text is None else norm_text,
        "simhash": simhash, "content_hash": content_hash, "parse_error": "",
    }
    return store.insert_email(raw_id, folder, parsed)


BASE = (1 << 64) - 1  # 全 1 的 simhash


def _bits(n):
    """低 n 位翻转，构造与 BASE 距离恰为 n 的 simhash。"""
    return BASE ^ ((1 << n) - 1)


def test_exact_duplicate_by_message_id(store):
    a = _insert(store, 1, message_id="<dup@t>", body="较短")
    b = _insert(store, 2, message_id="<DUP@t>", body="这是更长的正文版本")  # 大小写不敏感
    c = _insert(store, 3, message_id="<other@t>")
    assert exact_dedup(store) == 1
    rows = {r["id"]: r for r in store.iter_dedup_candidates()}
    assert rows[a]["dedup_status"] == "duplicate"
    assert rows[a]["duplicate_of"] == b  # 保留长者
    assert rows[b]["dedup_status"] == "kept"
    assert rows[c]["dedup_status"] == "pending"


def test_exact_duplicate_by_content_hash(store):
    a = _insert(store, 1, content_hash="h1", message_id="<a@t>")
    b = _insert(store, 2, content_hash="h1", message_id="<b@t>")
    assert exact_dedup(store) == 1
    statuses = sorted(r["dedup_status"] for r in store.iter_dedup_candidates())
    assert statuses == ["duplicate", "kept"]
    assert a != b


def test_near_dup_auto_merged(store):
    a = _insert(store, 1, subject="会议通知", simhash=BASE, body="正文版本一，比较短")
    b = _insert(store, 2, subject="Re: 会议通知", simhash=_bits(3), body="正文版本一，比较长的一版")
    pairs = near_dedup(store, auto_threshold=0.95, review_threshold=0.75)
    assert pairs == []
    rows = {r["id"]: r for r in store.iter_dedup_candidates()}
    assert rows[a]["dedup_status"] == "duplicate"
    assert rows[a]["duplicate_of"] == b
    assert rows[b]["dedup_status"] == "kept"


def test_near_dup_review_band(store, tmp_path):
    a = _insert(store, 1, subject="会议通知", simhash=BASE)
    b = _insert(store, 2, subject="会议通知", simhash=_bits(4))  # sim=1-4/64≈0.9375
    pairs = near_dedup(store)
    assert len(pairs) == 1
    assert isinstance(pairs[0], ReviewPair)
    assert 0.75 <= pairs[0].similarity < 0.95
    rows = {r["id"]: r for r in store.iter_dedup_candidates()}
    assert rows[a]["dedup_status"] == "review"
    assert rows[b]["dedup_status"] == "review"

    queue = tmp_path / "queue.json"
    write_review_queue(store, pairs, queue)
    data = json.loads(queue.read_text(encoding="utf-8"))
    assert data["pairs"][0]["a"] == a
    assert "preview_a" in data["pairs"][0]


def test_review_boundary_dist16_goes_review_dist17_unique(store):
    a = _insert(store, 1, subject="同主题", simhash=BASE)
    b = _insert(store, 2, subject="同主题", simhash=_bits(16))  # sim=0.75 恰在边界
    c = _insert(store, 3, subject="同主题", simhash=_bits(17))  # sim<0.75 各自保留
    pairs = near_dedup(store)
    assert [p.b for p in pairs] == [b]  # 只有 (a,b) 落入复核带
    store.finalize_uniques()
    rows = {r["id"]: r for r in store.iter_exportable()}
    assert rows[c]["dedup_status"] == "unique"


def test_different_subjects_same_sender_within_7_days(store):
    a = _insert(store, 1, subject="一月刊", sender="news@x.com", date="2024-03-01T08:00:00+08:00", simhash=BASE)
    b = _insert(store, 2, subject="二月刊", sender="news@x.com", date="2024-03-03T08:00:00+08:00", simhash=_bits(3))
    pairs = near_dedup(store)
    assert pairs == []  # 自动合并（同发件人 2 天内）


def test_same_sender_far_apart_not_compared(store):
    a = _insert(store, 1, subject="A刊", sender="news@x.com", date="2024-01-01T08:00:00+08:00", simhash=BASE)
    b = _insert(store, 2, subject="B刊", sender="news@x.com", date="2024-03-01T08:00:00+08:00", simhash=_bits(3))
    near_dedup(store)
    store.finalize_uniques()
    rows = list(store.iter_exportable())
    assert all(r["dedup_status"] == "unique" for r in rows)


def test_near_dedup_mixed_timezone_dates(store):
    """aware/naive Date 混合不得让 dedup 崩溃；无法比较即视为超出时间窗。"""
    a = _insert(store, 1, subject="A刊", sender="news@x.com",
                date="2024-03-01T08:00:00+08:00", simhash=BASE)
    b = _insert(store, 2, subject="B刊", sender="news@x.com",
                date="2024-03-02T08:00:00", simhash=_bits(3))  # 无时区
    pairs = near_dedup(store)  # 不抛 TypeError
    assert pairs == []
    store.finalize_uniques()
    rows = {r["id"]: r for r in store.iter_exportable()}
    assert rows[a]["dedup_status"] == "unique"
    assert rows[b]["dedup_status"] == "unique"


def test_apply_review_duplicate_and_unique(store, tmp_path):
    a = _insert(store, 1, subject="同主题", simhash=BASE, body="短")
    b = _insert(store, 2, subject="同主题", simhash=_bits(4), body="更长的一封")
    c = _insert(store, 3, subject="另一对", simhash=BASE ^ 0b101)
    # 原 brief 数据 BASE^0b111 与 c 只差 1 位（sim=0.9844≥auto 会被自动合并），
    # 与"入复核带"意图数学上不相容；改为 _bits(6)：dist(c,d)=4 → sim=0.9375 复核带。
    d = _insert(store, 4, subject="另一对", simhash=_bits(6))
    pairs = near_dedup(store)
    queue = tmp_path / "queue.json"
    write_review_queue(store, pairs, queue)
    data = json.loads(queue.read_text(encoding="utf-8"))
    data["pairs"][0]["verdict"] = "duplicate"
    data["pairs"][1]["verdict"] = "unique"
    queue.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    applied = apply_review(store, queue)
    assert applied == 2
    rows = {r["id"]: store.get_email(r["id"]) for r in store.iter_dedup_candidates()}
    assert rows[a]["dedup_status"] == "duplicate" and rows[a]["duplicate_of"] == b
    assert rows[b]["dedup_status"] == "kept"
    assert rows[c]["dedup_status"] == "unique"
    assert rows[d]["dedup_status"] == "unique"


def test_apply_review_skips_pairs_without_verdict(store, tmp_path):
    a = _insert(store, 1, subject="同主题", simhash=BASE)
    b = _insert(store, 2, subject="同主题", simhash=_bits(4))
    pairs = near_dedup(store)
    queue = tmp_path / "queue.json"
    write_review_queue(store, pairs, queue)
    assert apply_review(store, queue) == 0  # 无 verdict 不动作
    assert store.get_email(a)["dedup_status"] == "review"


def test_empty_norm_bypasses_near_dedup(store):
    a = _insert(store, 1, body="")
    store.finalize_uniques()
    assert store.get_email(a)["dedup_status"] == "unique"


# ---------- I-1 exact 双通道并查集 ----------

def test_exact_duplicate_transitive_chain(store):
    w = _insert(store, 1, message_id="<m1@t>", body="这是明显更长的正文内容版本一", content_hash="hw")
    x = _insert(store, 2, message_id="<M1@t>", body="短文本", content_hash="hx")
    y = _insert(store, 3, message_id="<m2@t>", body="短文本", content_hash="hx")
    assert exact_dedup(store) == 2
    rows = {r["id"]: r for r in store.iter_dedup_candidates()}
    assert rows[w]["dedup_status"] == "kept"
    assert rows[x]["dedup_status"] == "duplicate" and rows[x]["duplicate_of"] == w
    assert rows[y]["dedup_status"] == "duplicate" and rows[y]["duplicate_of"] == w
    assert len(list(store.iter_exportable())) == 1


# ---------- I-2 空 norm 的常量哈希防御 ----------

def test_exact_dedup_skips_empty_norm_hash_group(store):
    import hashlib
    empty_hash = hashlib.sha256(b"").hexdigest()
    _insert(store, 1, subject="甲", content_hash=empty_hash, body="-- \n张三", norm_text="")
    _insert(store, 2, subject="乙", content_hash=empty_hash, body="-- \n李四", norm_text="")
    assert exact_dedup(store) == 0


# ---------- I-5 候选对规模保护 ----------

def test_empty_subject_not_subject_grouped(store):
    _insert(store, 1, subject="RE: ", sender="a@x.com", date="2024-01-01T08:00:00+08:00",
            simhash=(1 << 64) - 1, body="内容甲")
    _insert(store, 2, subject="回复：", sender="b@x.com", date="2024-02-01T08:00:00+08:00",
            simhash=(1 << 64) - 1, body="内容乙")
    assert near_dedup(store) == []
    # 空主题不得聚合：两封同为全 1 simhash，若被聚成一组会被自动合并
    assert [r["dedup_status"] for r in store.iter_dedup_candidates()] == ["pending", "pending"]


def test_oversized_group_window_still_merges_adjacent(store, monkeypatch):
    monkeypatch.setattr(dedup_module, "_MAX_PAIRS_PER_GROUP", 1)
    base, far = (1 << 64) - 1, 0x5555555555555555
    ids = []
    for i, (sh, day) in enumerate([(base, "01"), (base, "02"), (far, "03"), (far, "04")]):
        ids.append(_insert(store, i + 1, subject="每日摘要", sender=f"u{i}@x.com",
                           date=f"2024-01-{day}T08:00:00+08:00", simhash=sh, body=f"内容{i}"))
    near_dedup(store)
    assert store.get_email(ids[0])["dedup_status"] == "kept"
    assert store.get_email(ids[1])["dedup_status"] == "duplicate"
    assert store.get_email(ids[2])["dedup_status"] == "kept"
    assert store.get_email(ids[3])["dedup_status"] == "duplicate"


def test_oversized_group_window_skips_non_adjacent(store, monkeypatch):
    monkeypatch.setattr(dedup_module, "_MAX_PAIRS_PER_GROUP", 1)
    base, far = (1 << 64) - 1, 0x5555555555555555
    ids = []
    for i, (sh, day) in enumerate([(base, "01"), (far, "02"), (base, "03")]):
        ids.append(_insert(store, i + 1, subject="同主题", sender=f"u{i}@x.com",
                           date=f"2024-01-{day}T08:00:00+08:00", simhash=sh, body=f"内容{i}"))
    assert near_dedup(store) == []
    store.finalize_uniques()
    for eid in ids:
        assert store.get_email(eid)["dedup_status"] == "unique"
