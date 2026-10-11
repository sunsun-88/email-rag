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

def _anchor_rank(rec: dict) -> tuple:
    """同 key 多锚择一：kept 优先，其次 id 最小。"""
    return (0 if rec["dedup_status"] == "kept" else 1, rec["id"])


def _build_anchor_indexes(records: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    anchors_mid: dict[str, dict] = {}
    anchors_hash: dict[str, dict] = {}
    for rec in records:
        if rec["dedup_status"] not in ("kept", "unique"):  # 只有定稿行是历史锚点
            continue
        mid = (rec["message_id"] or "").strip().lower()
        if mid:
            cur = anchors_mid.get(mid)
            if cur is None or _anchor_rank(rec) < _anchor_rank(cur):
                anchors_mid[mid] = rec
        # 空正文（norm_text 为空）的常量哈希不构成重复证据
        if rec["content_hash"] and rec["norm_text"].strip():
            cur = anchors_hash.get(rec["content_hash"])
            if cur is None or _anchor_rank(rec) < _anchor_rank(cur):
                anchors_hash[rec["content_hash"]] = rec
    return anchors_mid, anchors_hash


def _member_anchor(rec: dict, anchors_mid: dict[str, dict],
                   anchors_hash: dict[str, dict]) -> dict | None:
    """成员命中锚点：message_id 通道优先，其次 content_hash 通道。"""
    mid = (rec["message_id"] or "").strip().lower()
    anchor = anchors_mid.get(mid) if mid else None
    if anchor is None and rec["content_hash"] and rec["norm_text"].strip():
        anchor = anchors_hash.get(rec["content_hash"])
    return anchor


def exact_dedup(store: Store) -> int:
    """pending 邮件的完全重复判定，并与历史锚点（kept/unique）比对。

    同 message_id 与同 content_hash 是两条独立通道，必须按连通分量
    统一裁决（并查集），否则后一通道的裁决会覆盖前一通道。
    pending 分量命中历史锚点时整组解析到锚点，不再组内选 keeper。
    """
    records = list(store.iter_dedup_candidates())
    pending = [r for r in records if r["dedup_status"] == "pending"]
    anchors_mid, anchors_hash = _build_anchor_indexes(records)
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
        hits = [a for a in (_member_anchor(rec, anchors_mid, anchors_hash)
                            for rec in group) if a is not None]
        if hits:
            # 组内多成员命中不同锚 → 取锚 id 最小者（确定性）
            anchor = min(hits, key=lambda r: r["id"])
            for rec in group:
                store.update_dedup(rec["id"], "duplicate", anchor["id"])
                marked += 1
            if anchor["dedup_status"] == "unique":  # unique 锚升级为 kept
                store.update_dedup(anchor["id"], "kept")
            continue
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


def _candidate_pairs(records: list[dict],
                     pending_ids: set[int] | None = None) -> set[tuple[int, int]]:
    if pending_ids is None:  # 兼容旧语义：未指定时视为全部记录都是待比对邮件
        pending_ids = {r["id"] for r in records}
    pairs: set[tuple[int, int]] = set()

    def _add(a_id: int, b_id: int) -> bool:
        key = (min(a_id, b_id), max(a_id, b_id))
        if key in pairs:
            return False
        if key[0] not in pending_ids and key[1] not in pending_ids:
            return False  # 锚-锚对不比对：候选对至少一方是本轮待比对邮件
        pairs.add(key)
        return True

    by_subject: dict[str, list[dict]] = {}
    by_sender: dict[str, list[dict]] = {}
    for r in records:
        subject = normalize_subject(r["subject"])
        if subject:  # 空规范化主题不聚合：无关邮件聚成一组会退化成 O(n²)
            by_subject.setdefault(subject, []).append(r)
        by_sender.setdefault(r["sender"], []).append(r)

    for subject, group in by_subject.items():
        if not any(r["id"] in pending_ids for r in group):
            continue  # 无 pending 的纯锚组整组跳过（核心优化）
        if len(group) * (len(group) - 1) // 2 > _MAX_PAIRS_PER_GROUP:
            log.warning("主题组 %r 含 %d 封，候选对超上限 %d，退化为相邻对窗口",
                        subject[:80], len(group), _MAX_PAIRS_PER_GROUP)
            ordered = sorted(group, key=lambda r: (r["date"], r["id"]))
            for a, b in zip(ordered, ordered[1:]):
                _add(a["id"], b["id"])
            continue
        for a, b in combinations(group, 2):
            _add(a["id"], b["id"])

    dates: dict[int, object] = {r["id"]: _parse_date(r["date"]) for r in records}
    for group in by_sender.values():
        group.sort(key=lambda r: (r["date"], r["id"]))
        group_pending = [r for r in group if r["id"] in pending_ids]
        if not group_pending:
            continue
        index = {r["id"]: i for i, r in enumerate(group)}
        made = 0
        capped = False
        for p in group_pending:
            ip = index[p["id"]]
            cp = dates[p["id"]]
            for step in (1, -1):  # 先向后（日期更晚）再向前
                j = ip + step
                while 0 <= j < len(group):
                    if _days_between_dt(cp, dates[group[j]["id"]]) > _SENDER_WINDOW_DAYS:
                        break
                    _add(p["id"], group[j]["id"])
                    made += 1
                    if made >= _MAX_PAIRS_PER_GROUP:
                        capped = True
                        break
                    j += step
                if capped:
                    break
            if capped:
                break
        if capped:
            log.warning("发件人组过大，候选对截断至 %d（按待处理邮件逐封扩展窗口）",
                        _MAX_PAIRS_PER_GROUP)
    return pairs


def near_dedup(store: Store, auto_threshold: float = 0.95,
               review_threshold: float = 0.75) -> list[ReviewPair]:
    pool = [r for r in store.iter_dedup_candidates()
            if r["dedup_status"] in ("pending", "kept", "unique")  # review 不参与池
            and r["norm_text"].strip() and r["simhash"]]
    pending_ids = {r["id"] for r in pool if r["dedup_status"] == "pending"}
    by_id = {r["id"]: r for r in pool}
    # 生成期已保证候选对至少一方是 pending（锚-锚对不再产生）
    pairs = _candidate_pairs(pool, pending_ids)

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
        a_anchor, b_anchor = ida not in pending_ids, idb not in pending_ids
        if a_anchor and b_anchor:
            continue
        if sim >= auto_threshold:
            ra, rb = by_id[ida], by_id[idb]
            if a_anchor or b_anchor:
                anchor = ra if a_anchor else rb
                newbie = rb if a_anchor else ra
                # 历史锚已导出且 export 不删旧文件：即使新邮件正文更长也不换
                # keeper（换了会留下旧文件残留 + 新文件重复）。
                if anchor["dedup_status"] == "unique":
                    store.update_dedup(anchor["id"], "kept")
                store.update_dedup(newbie["id"], "duplicate", anchor["id"])
                spent.add(newbie["id"])  # 锚永不 spent：一个锚可挂多个新邮件
            else:
                keeper = ra if len(ra["body_text"]) >= len(rb["body_text"]) else rb
                other = rb if keeper is ra else ra
                store.update_dedup(keeper["id"], "kept")
                store.update_dedup(other["id"], "duplicate", keeper["id"])
                spent.add(ida)
                spent.add(idb)
        elif sim >= review_threshold:
            ra, rb = by_id[ida], by_id[idb]
            if a_anchor or b_anchor:
                anchor = ra if a_anchor else rb
                newbie = rb if a_anchor else ra
                store.update_dedup(newbie["id"], "review")  # 锚保持原状
                spent.add(newbie["id"])
                review_pairs.append(ReviewPair(
                    a=anchor["id"], b=newbie["id"], similarity=round(sim, 4),
                    subject=(ra["subject"] or rb["subject"])[:200],
                ))
            else:
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

def _queue_key(item: dict) -> tuple:
    return (item.get("a"), item.get("b"))


def _load_queue_items(path: Path) -> list[dict]:
    """读取已存在的队列条目；文件缺失/损坏视为无旧条目（新建覆盖）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("pairs")
    return items if isinstance(items, list) else []


def write_review_queue(store: Store, pairs: list[ReviewPair], path: str | Path) -> None:
    """合并写队列：保留旧条目中无 verdict 者，追加新 pairs（按 (a,b) 去重）。

    apply_review 不回写队列文件，已裁定条目会在下一次 write 时自然清出；
    这样未裁定的旧条目不会因新一批 pairs 写入而丢失。
    """
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
    merged: list[dict] = []
    seen: set[tuple] = set()
    for item in _load_queue_items(out):
        if (not isinstance(item, dict) or item.get("verdict")
                or not isinstance(item.get("a"), int)
                or not isinstance(item.get("b"), int)):
            continue  # 已裁定条目清出；a/b 非 int 的畸形条目一并丢弃
        key = _queue_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    for item in items:
        key = _queue_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    out.write_text(json.dumps({"pairs": merged}, ensure_ascii=False, indent=2),
                   encoding="utf-8")


def apply_review(store: Store, queue_path: str | Path) -> int:
    """读取队列中人工/LLM 写入的 verdict：duplicate → 合并，unique → 各自保留。

    锚感知：定稿（kept/unique）成员保持原状/作为 keeper（unique→kept 升级），
    绝不会被降级为 duplicate。
    """
    data = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    applied = 0
    for item in data.get("pairs", []):
        if not isinstance(item, dict):
            continue
        verdict = item.get("verdict")
        if verdict not in ("duplicate", "unique"):
            continue
        if not isinstance(item.get("a"), int) or not isinstance(item.get("b"), int):
            continue  # 畸形条目不参与裁定
        a, b = store.get_email(item["a"]), store.get_email(item["b"])
        if not a or not b:
            continue
        if verdict == "unique":
            for rec in (a, b):
                if rec["dedup_status"] not in ("kept", "unique"):
                    store.update_dedup(rec["id"], "unique")
        else:
            finalized = [r for r in (a, b)
                         if r["dedup_status"] in ("kept", "unique")]
            if finalized:
                keeper = min(finalized, key=lambda r: r["id"])
                store.update_dedup(keeper["id"], "kept")  # unique → kept 升级
                for rec in (a, b):
                    if (rec["id"] != keeper["id"]
                            and rec["dedup_status"] not in ("kept", "unique")):
                        store.update_dedup(rec["id"], "duplicate", keeper["id"])
            else:
                keeper = a if len(a["body_text"]) > len(b["body_text"]) else b
                other = b if keeper is a else a
                store.update_dedup(keeper["id"], "kept")
                store.update_dedup(other["id"], "duplicate", keeper["id"])
        applied += 1
    return applied


# ---------- LLM 复核 ----------

_MAX_CONSECUTIVE_FAILURES = 5  # LLM 连续失败上限：早停止损（如服务器宕机）


def _save_queue(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def judge_pairs_with_llm(store: Store, pairs: list[ReviewPair], llm,
                         queue_path: str | Path, save_every: int = 20,
                         batch_size: int = 20,
                         auto_dup_threshold: float = 0.90,
                         auto_unique_threshold: float = 0.80) -> int:
    """逐条/逐批 LLM 裁定并写回 verdict；失败条目保持无 verdict 留待人工。

    成本控制（用户的合理要求：不遍历全量、控制输出 token）：
    - 高置信端规则直判，不进 LLN：similarity ≥ auto_dup_threshold 判 duplicate，
      < auto_unique_threshold 判 unique；
    - 仅灰色带 [auto_unique_threshold, auto_dup_threshold) 送 LLM，
      支持 judge_batch 的客户端按 batch_size 对一批次调用（输出上限 16×组数）；
    - 增量落盘（中途被杀不丢进度）；连续失败 _MAX_CONSECUTIVE_FAILURES 次即
      中止（服务器宕机不逐对傻等），修好后重跑 dedup --llm 续裁剩余。
    """
    path = Path(queue_path)
    if pairs:
        write_review_queue(store, pairs, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    items = [it for it in data.get("pairs", []) if isinstance(it, dict)]

    # 第一遍：高置信端规则直判
    inband: list[dict] = []
    auto_decided = 0
    for item in items:
        if item.get("verdict"):
            continue
        sim = item.get("similarity") or 0
        if sim >= auto_dup_threshold:
            item["verdict"] = "duplicate"
            auto_decided += 1
        elif sim < auto_unique_threshold:
            item["verdict"] = "unique"
            auto_decided += 1
        else:
            inband.append(item)
    if auto_decided:
        log.info("规则直判 %d 对（≥%.2f 判重复、<%.2f 判不重复），送 LLM %d 对",
                 auto_decided, auto_dup_threshold, auto_unique_threshold,
                 len(inband))

    consecutive_failures = 0
    since_save = 0
    pending = list(inband)
    while pending and consecutive_failures < _MAX_CONSECUTIVE_FAILURES:
        if hasattr(llm, "judge_batch"):
            chunk, pending = pending[:batch_size], pending[batch_size:]
            texts = [(it.get("preview_a", ""), it.get("preview_b", ""))
                     for it in chunk]
            try:
                verdicts = llm.judge_batch(texts)
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                log.warning("LLM 批量裁定失败（%d 对）: %s", len(chunk), exc)
            else:
                if all(v is None for v in verdicts):
                    consecutive_failures += 1
                    log.warning("LLM 批量裁定无有效结果（%d 对）", len(chunk))
                else:
                    consecutive_failures = 0
                for it, v in zip(chunk, verdicts):
                    if v is not None:
                        it["verdict"] = "duplicate" if v else "unique"
            since_save += len(chunk)
        else:
            item = pending.pop(0)
            try:
                v = llm.judge_pair(item.get("preview_a", ""),
                                   item.get("preview_b", ""))
                item["verdict"] = "duplicate" if v else "unique"
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                log.warning("LLM 裁定失败 pair(%s, %s): %s",
                            item.get("a"), item.get("b"), exc)
            since_save += 1
        if since_save >= save_every:
            _save_queue(data, path)
            since_save = 0
    if pending and consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
        log.error("LLM 连续失败 %d 次，中止裁定；已完成进度已保存，"
                  "修复服务后重跑 dedup --llm 续裁剩余 %d 对",
                  _MAX_CONSECUTIVE_FAILURES, len(pending))
    _save_queue(data, path)
    return apply_review(store, path)
