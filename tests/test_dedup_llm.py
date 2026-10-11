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
    b = _insert(store, 2, subject="同主题", simhash=BASE ^ 0b11111111)
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


def test_judge_saves_progress_incrementally(store, tmp_path):
    """中途被杀死（KeyboardInterrupt）：已裁定条目必须已落盘，可续跑。"""
    a1 = _insert(store, 1, subject="甲主题", simhash=(1 << 64) - 1)
    b1 = _insert(store, 2, subject="甲主题", simhash=((1 << 64) - 1) ^ 0b11111111)
    c = _insert(store, 3, subject="乙主题", simhash=(1 << 64) - 1)
    d = _insert(store, 4, subject="乙主题", simhash=((1 << 64) - 1) ^ 0b11111111)
    queue = tmp_path / "queue.json"
    write_review_queue(store, near_dedup(store), queue)
    calls = {"n": 0}

    class InterruptingLLM:
        def judge_pair(self, ta, tb):
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyboardInterrupt
            return True

    try:
        judge_pairs_with_llm(store, [], InterruptingLLM(), queue, save_every=1)
    except KeyboardInterrupt:
        pass
    data = json.loads(queue.read_text(encoding="utf-8"))
    verdicts = [p.get("verdict") for p in data["pairs"]]
    assert verdicts[0] == "duplicate"  # 第 1 对已增量落盘
    assert verdicts[1] is None         # 第 2 对未裁定


def test_judge_aborts_after_consecutive_failures(store, tmp_path):
    """LLM 连续失败（如服务器宕机）→ 早停止损，不傻等全部对数。"""
    base = (1 << 64) - 1
    for i in range(1, 13):
        _insert(store, i, subject="同主题", simhash=base if i % 2 else base ^ 0b11111111)
    queue = tmp_path / "queue.json"
    write_review_queue(store, near_dedup(store), queue)

    class FailLLM:
        def __init__(self):
            self.calls = 0

        def judge_pair(self, ta, tb):
            self.calls += 1
            raise RuntimeError("server down")

    llm = FailLLM()
    applied = judge_pairs_with_llm(store, [], llm, queue, save_every=1)
    assert llm.calls == 5  # 第 6 对未尝试（连续失败中止）
    assert applied == 0


def test_llm_band_auto_resolves_confident_ends(store, tmp_path):
    """--llm 时：高置信端规则直判不调用 LLM，只有灰色带送 LLM。"""
    base = (1 << 64) - 1
    # 三对：sim 0.95(≥0.90→直判dup)、0.85(带内→LLM)、0.76(<0.80→直判unique)
    _insert(store, 1, subject="甲主题", simhash=base)
    _insert(store, 2, subject="甲主题", simhash=base ^ 0b101)      # dist2 sim0.969→自动合并区
    # 上面 0.969 会走 auto_merge_threshold(0.95)，改用手动队列更直接：
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"pairs": [
        {"a": 1, "b": 2, "similarity": 0.95, "subject": "s",
         "preview_a": "x", "preview_b": "y"},
        {"a": 1, "b": 2, "similarity": 0.85, "subject": "s",
         "preview_a": "x", "preview_b": "y"},
        {"a": 1, "b": 2, "similarity": 0.76, "subject": "s",
         "preview_a": "x", "preview_b": "y"},
    ]}, ensure_ascii=False), encoding="utf-8")

    class CountingLLM:
        def __init__(self):
            self.calls = 0

        def judge_pair(self, ta, tb):
            self.calls += 1
            return True

    llm = CountingLLM()
    judge_pairs_with_llm(store, [], llm, queue, save_every=1)
    data = json.loads(queue.read_text(encoding="utf-8"))
    verdicts = [p.get("verdict") for p in data["pairs"]]
    assert verdicts == ["duplicate", "duplicate", "unique"]
    assert llm.calls == 1  # 只有 0.85 那一对真正调用了 LLM


def test_llm_batching_chunks_inband_items(store, tmp_path):
    """带内条目按 batch_size 分批调用 judge_batch。"""
    base = (1 << 64) - 1
    for i in range(1, 13):
        _insert(store, i, subject="同主题", simhash=base if i % 2 else base ^ 0b11111111)
    queue = tmp_path / "queue.json"
    write_review_queue(store, near_dedup(store), queue)
    inband = sum(1 for p in json.loads(queue.read_text(encoding="utf-8"))["pairs"]
                 if 0.80 <= p["similarity"] < 0.90)

    class BatchLLM:
        def __init__(self):
            self.batches = []
            self.max_tokens_seen = []

        def judge_batch(self, pairs, max_tokens=None):
            self.batches.append(len(pairs))
            self.max_tokens_seen.append(max_tokens)
            return [True] * len(pairs)

    llm = BatchLLM()
    judge_pairs_with_llm(store, [], llm, queue, save_every=100, batch_size=2)
    assert llm.batches                      # 走了批量路径
    assert sum(llm.batches) == inband       # 只送了带内条目
    assert all(size <= 2 for size in llm.batches)
    assert llm.max_tokens_seen == [200 * size for size in llm.batches]


def test_llm_judges_backlog_and_new_pairs(store, tmp_path):
    # 旧积压 (1,2) 已入队未裁定；本轮产生新对 (3,4)
    a1 = _insert(store, 1, subject="甲主题", simhash=(1 << 64) - 1)
    b1 = _insert(store, 2, subject="甲主题", simhash=((1 << 64) - 1) ^ 0b11111111)
    queue = tmp_path / "q.json"
    write_review_queue(store, near_dedup(store), queue)
    c = _insert(store, 3, subject="乙主题", simhash=(1 << 64) - 1)
    d = _insert(store, 4, subject="乙主题", simhash=((1 << 64) - 1) ^ 0b11111111)
    pairs2 = near_dedup(store)

    class YesLLM:
        def __init__(self):
            self.calls = 0

        def judge_pair(self, text_a, text_b):
            self.calls += 1
            return True

    llm = YesLLM()
    applied = judge_pairs_with_llm(store, pairs2, llm, queue)
    assert llm.calls == 2       # 积压 + 新对都裁定
    assert applied == 2
    data = json.loads(queue.read_text(encoding="utf-8"))
    assert all(p.get("verdict") == "duplicate" for p in data["pairs"])
