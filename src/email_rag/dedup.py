from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path

from .parser import normalize_subject
from .simhash import similarity
from .store import Store

log = logging.getLogger(__name__)

_PREVIEW_LEN = 300
_SENDER_WINDOW_DAYS = 7
_MAX_PAIRS_PER_GROUP = 100_000  # 单组 O(n²) 组合上限，超出退化为相邻对窗口


@dataclass
class ReviewPair:
    a: int
    b: int
    similarity: float
    subject: str


# ---------- 完全重复 ----------

def exact_dedup(store: Store) -> int:
    """pending 邮件的完全重复判定。

    同 message_id 与同 content_hash 是两条独立通道，必须按连通分量
    统一裁决（并查集），否则后一通道的裁决会覆盖前一通道。
    """
    pending = [r for r in store.iter_dedup_candidates()
               if r["dedup_status"] == "pending"]  # 只判新邮件，避免翻旧账
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_key: dict[tuple, list[int]] = {}
    for rec in pending:
        parent.setdefault(rec["id"], rec["id"])
        mid = (rec["message_id"] or "").strip().lower()
        if mid:
            by_key.setdefault(("mid", mid), []).append(rec["id"])
        # 空正文（norm_text 为空）的常量哈希不构成重复证据
        if rec["content_hash"] and rec["norm_text"].strip():
            by_key.setdefault(("hash", rec["content_hash"]), []).append(rec["id"])

    for ids in by_key.values():
        for other in ids[1:]:
            union(ids[0], other)

    components: dict[int, list[dict]] = {}
    for rec in pending:
        components.setdefault(find(rec["id"]), []).append(rec)

    marked = 0
    for group in components.values():
        if len(group) < 2:
            continue
        keeper = max(group, key=lambda r: (len(r["body_text"]), -r["id"]))
        for rec in group:
            if rec["id"] == keeper["id"]:
                store.update_dedup(rec["id"], "kept")
            else:
                store.update_dedup(rec["id"], "duplicate", keeper["id"])
                marked += 1
    return marked
# ---------- 近似重复 ----------

def _parse_date(value: str):
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _days_between_dt(da, db) -> float:
    if da is None or db is None:
        return float("inf")
    try:
        return abs((da - db).total_seconds()) / 86400.0
    except (TypeError, ValueError):
        return float("inf")  # 时区混合（aware/naive）无法相减 → 视为超出时间窗


def _days_between(a: str, b: str) -> float:
    return _days_between_dt(_parse_date(a), _parse_date(b))


def _candidate_pairs(records: list[dict]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    by_subject: dict[str, list[dict]] = {}
    by_sender: dict[str, list[dict]] = {}
    for r in records:
        subject = normalize_subject(r["subject"])
        if subject:  # 空规范化主题不聚合：无关邮件聚成一组会退化成 O(n²)
            by_subject.setdefault(subject, []).append(r)
        by_sender.setdefault(r["sender"], []).append(r)

    for subject, group in by_subject.items():
        if len(group) * (len(group) - 1) // 2 > _MAX_PAIRS_PER_GROUP:
            log.warning("主题组 %r 含 %d 封，候选对超上限 %d，退化为相邻对窗口",
                        subject[:80], len(group), _MAX_PAIRS_PER_GROUP)
            ordered = sorted(group, key=lambda r: (r["date"], r["id"]))
            for a, b in zip(ordered, ordered[1:]):
                pairs.add((min(a["id"], b["id"]), max(a["id"], b["id"])))
            continue
        for a, b in combinations(group, 2):
            pairs.add((min(a["id"], b["id"]), max(a["id"], b["id"])))

    date_cache: dict[int, object] = {r["id"]: _parse_date(r["date"]) for r in records}
    for group in by_sender.values():
        group.sort(key=lambda r: (r["date"], r["id"]))
        made = 0
        stop = False
        for i, a in enumerate(group):
            if stop:
                break
            for b in group[i + 1:]:
                if _days_between_dt(date_cache[a["id"]],
                                    date_cache[b["id"]]) > _SENDER_WINDOW_DAYS:
                    break
                if made >= _MAX_PAIRS_PER_GROUP:
                    log.warning("发件人组 %r 候选对达到上限 %d，停止扩展",
                                a["sender"], _MAX_PAIRS_PER_GROUP)
                    stop = True
                    break
                pairs.add((min(a["id"], b["id"]), max(a["id"], b["id"])))
                made += 1
    return pairs


def near_dedup(store: Store, auto_threshold: float = 0.95,
               review_threshold: float = 0.75) -> list[ReviewPair]:
    pending = [r for r in store.iter_dedup_candidates()
               if r["dedup_status"] == "pending"  # 只判新邮件；review/kept 等不翻旧账
               and r["norm_text"].strip() and r["simhash"]]
    by_id = {r["id"]: r for r in pending}
    pairs = _candidate_pairs(pending)

    judged = []
    for ida, idb in pairs:
        sim = similarity(by_id[ida]["simhash"], by_id[idb]["simhash"])
        judged.append((ida, idb, sim))
    # 按 id 升序处理，不用相似度排序：高相似度排序会让 (b,c) 这类
    # 彼此极近的交叉对抢先占用成员，使边界用例的复核对无法成立。
    judged.sort(key=lambda t: (t[0], t[1]))

    spent: set[int] = set()
    review_pairs: list[ReviewPair] = []
    for ida, idb, sim in judged:
        if ida in spent or idb in spent:
            continue
        if sim >= auto_threshold:
            ra, rb = by_id[ida], by_id[idb]
            keeper = ra if len(ra["body_text"]) >= len(rb["body_text"]) else rb
            other = rb if keeper is ra else ra
            store.update_dedup(keeper["id"], "kept")
            store.update_dedup(other["id"], "duplicate", keeper["id"])
            spent.add(ida)
            spent.add(idb)
        elif sim >= review_threshold:
            ra, rb = by_id[ida], by_id[idb]
            store.update_dedup(ida, "review")
            store.update_dedup(idb, "review")
            spent.add(ida)
            spent.add(idb)
            review_pairs.append(ReviewPair(
                a=ida, b=idb, similarity=round(sim, 4),
                subject=(ra["subject"] or rb["subject"])[:200],
            ))
    return review_pairs


# ---------- 复核队列 ----------

def write_review_queue(store: Store, pairs: list[ReviewPair], path: str | Path) -> None:
    items = []
    for p in pairs:
        ra, rb = store.get_email(p.a), store.get_email(p.b)
        items.append({
            **asdict(p),
            "preview_a": (ra["norm_text"] if ra else "")[:_PREVIEW_LEN],
            "preview_b": (rb["norm_text"] if rb else "")[:_PREVIEW_LEN],
        })
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"pairs": items}, ensure_ascii=False, indent=2),
                   encoding="utf-8")


def apply_review(store: Store, queue_path: str | Path) -> int:
    """读取队列中人工/LLM 写入的 verdict：duplicate → 合并，unique → 各自保留。"""
    data = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    applied = 0
    for item in data.get("pairs", []):
        verdict = item.get("verdict")
        if verdict not in ("duplicate", "unique"):
            continue
        a, b = store.get_email(item["a"]), store.get_email(item["b"])
        if not a or not b:
            continue
        if verdict == "unique":
            store.update_dedup(a["id"], "unique")
            store.update_dedup(b["id"], "unique")
        else:
            keeper = a if len(a["body_text"]) > len(b["body_text"]) else b
            other = b if keeper is a else a
            store.update_dedup(keeper["id"], "kept")
            store.update_dedup(other["id"], "duplicate", keeper["id"])
        applied += 1
    return applied


# ---------- LLM 复核 ----------

def judge_pairs_with_llm(store: Store, pairs: list[ReviewPair], llm,
                         queue_path: str | Path) -> int:
    """逐对 LLM 裁定并写回 verdict；失败的 pair 保持 review 留待人工。"""
    path = Path(queue_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for item, pair in zip(data.get("pairs", []), pairs):
        if item.get("verdict"):
            continue
        try:
            verdict = llm.judge_pair(item.get("preview_a", ""),
                                     item.get("preview_b", ""))
            item["verdict"] = "duplicate" if verdict else "unique"
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM 裁定失败 pair(%s, %s): %s", pair.a, pair.b, exc)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return apply_review(store, path)
