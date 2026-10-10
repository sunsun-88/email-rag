import json

from email_rag.dedup import judge_pairs_with_llm, near_dedup, write_review_queue


def _insert(store, uid, subject="主题", body="正文", simhash=0):
    raw_id = store.upsert_raw("INBOX", uid, b"x")
    return store.insert_email(raw_id, "INBOX", {
        "subject": subject, "sender": "a@b.c", "to": [], "cc": [],
        "date": "2024-03-15T09:30:00+08:00", "message_id": "",
        "in_reply_to": "", "references": [], "body_text": body, "body_html": "",
        "norm_text": body, "simhash": simhash, "content_hash": "", "parse_error": "",
    })


class FakeLLM:
    def __init__(self, verdict: bool):
        self._verdict = verdict
        self.calls = 0

    def judge_pair(self, text_a, text_b):
        self.calls += 1
        return self._verdict


class FlakyLLM:
    """第一对抛异常，第二对返回 True。"""

    def __init__(self):
        self.calls = 0

    def judge_pair(self, text_a, text_b):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("api down")
        return True


BASE = (1 << 64) - 1


def _make_review_pair(store):
    a = _insert(store, 1, subject="同主题", simhash=BASE)
    b = _insert(store, 2, subject="同主题", simhash=BASE ^ 0b1111)
    return near_dedup(store), a, b


def test_llm_judges_and_applies(store, tmp_path):
    pairs, a, b = _make_review_pair(store)
    queue = tmp_path / "queue.json"
    write_review_queue(store, pairs, queue)

    llm = FakeLLM(verdict=True)
    applied = judge_pairs_with_llm(store, pairs, llm, queue)
    assert applied == 1
    assert llm.calls == 1
    rows = {r["id"]: r for r in store.iter_dedup_candidates()}
    assert rows[a]["dedup_status"] == "duplicate"
    assert rows[b]["dedup_status"] == "kept"
    # verdict 已写回队列文件
    data = json.loads(queue.read_text(encoding="utf-8"))
    assert data["pairs"][0]["verdict"] == "duplicate"


def test_llm_failure_pair_left_for_human(store, tmp_path):
    pairs, a, b = _make_review_pair(store)
    queue = tmp_path / "queue.json"
    write_review_queue(store, pairs, queue)

    applied = judge_pairs_with_llm(store, pairs, FlakyLLM(), queue)
    assert applied == 0
    assert store.get_email(a)["dedup_status"] == "review"  # 保持待复核
