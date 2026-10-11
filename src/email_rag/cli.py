from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import AppConfig, load_config
from .dedup import (
    apply_review,
    exact_dedup,
    judge_pairs_with_llm,
    near_dedup,
    write_review_queue,
)
from .exporter import export_all
from .fetcher import Fetcher
from .llm import NullLLM, create_llm
from .parser import parse_raw
from .store import Store

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="email-rag",
                                     description="IMAP 邮件拉取、去重、导出 Markdown")
    parser.add_argument("--env", default=".env", help=".env 路径")
    parser.add_argument("--config", default="config.yaml", help="config.yaml 路径")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="IMAP 拉取邮件到 SQLite")
    sub.add_parser("parse", help="解析 raw 邮件为结构化记录")
    dedup_parser = sub.add_parser("dedup", help="去重；可带队列文件导入裁定")
    dedup_parser.add_argument("queue", nargs="?", default=None,
                              help="复核队列 JSON 文件（导入人工/LLM 裁定）")
    dedup_parser.add_argument("--llm", action="store_true", help="用 LLM 自动裁定复核队列")
    dedup_parser.add_argument("--full", action="store_true",
                              help="全量重去重：先把所有邮件重置为 pending 再执行")
    export_parser = sub.add_parser("export", help="导出 Markdown")
    export_parser.add_argument("--prune", action="store_true",
                               help="清理 output 目录中不在本次导出集合的 *.md（dedup --full 后建议）")
    sub.add_parser("report", help="统计报告")
    return parser


def _resolve_data_paths(cfg: AppConfig, base: Path) -> None:
    """把 data/output 等相对路径固定到 config.yaml 所在目录（项目根）。

    否则在别的目录执行命令时，会在该目录新建一个空库，造成
    "拉过邮件却像重新开始"的错觉。
    """
    for attr in ("db_path", "review_queue_path", "export"):
        target = getattr(cfg, attr)
        if attr == "export":
            out = Path(target.output_dir)
            if not out.is_absolute():
                target.output_dir = str(base / out)
            continue
        p = Path(target)
        if not p.is_absolute():
            setattr(cfg, attr, str(base / p))


def run_parse(store: Store) -> int:
    count = 0
    for raw in store.iter_unparsed_raw():
        parsed = parse_raw(raw["mime_bytes"], raw["folder"])
        store.insert_email(raw["id"], raw["folder"], parsed)
        count += 1
    return count


def _queue_has_unjudged(path: str | Path) -> bool:
    """队列文件是否存在且含有未裁定（无 verdict）条目。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    items = data.get("pairs") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return False
    return any(isinstance(item, dict) and not item.get("verdict")
               for item in items)


def run_dedup(store: Store, cfg: AppConfig, use_llm: bool = False,
              full: bool = False) -> None:
    if full:
        n = store.reset_dedup()
        print(f"已重置 {n} 封为待去重")
        print("提示：全量重去重可能改变保留代表，旧导出文件可能残留，建议用 export --prune 清理")
    exact = exact_dedup(store)
    pairs = near_dedup(store, cfg.dedup.auto_merge_threshold,
                       cfg.dedup.review_threshold)
    print(f"完全重复合并 {exact} 封；近似重复复核队列 {len(pairs)} 对")
    if pairs:
        write_review_queue(store, pairs, cfg.review_queue_path)
        print(f"复核队列已写入 {cfg.review_queue_path}")
        if not use_llm:
            print("人工裁定后运行：email-rag dedup data/review_queue.json")
    if use_llm:
        # 既裁本轮新对，也补裁队列文件里往轮积压的未裁定条目（增量场景）
        if not (cfg.llm.api_key or cfg.llm.base_url):
            print("提示：已指定 --llm 但未配置 LLM"
                  "（需要 LLM_BASE_URL 或 LLM_API_KEY），跳过自动裁定")
        elif not pairs and not _queue_has_unjudged(cfg.review_queue_path):
            print("提示：复核队列无待裁定条目，无需 LLM")
        else:
            llm = create_llm(cfg.llm.provider, cfg.llm.api_key,
                             cfg.llm.model, cfg.llm.base_url)
            if isinstance(llm, NullLLM):
                print("提示：无法确定 LLM 服务端点，请配置 LLM_BASE_URL"
                      "（或 LLM_PROVIDER=deepseek/openai 使用默认端点）")
            else:
                applied = judge_pairs_with_llm(
                    store, pairs, llm, cfg.review_queue_path,
                    batch_size=cfg.dedup.llm_batch_size,
                    auto_dup_threshold=cfg.dedup.llm_auto_dup_threshold,
                    auto_unique_threshold=cfg.dedup.llm_auto_unique_threshold)
                print(f"已裁定 {applied} 对（高置信端规则直判 + LLM 灰色带）")
    finalized = store.finalize_uniques()
    print(f"标记 unique {finalized} 封")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.env, args.config)
    _resolve_data_paths(cfg, Path(args.config).resolve().parent)

    if args.command == "fetch":
        if not (cfg.imap.host and cfg.imap.user and cfg.imap.password):
            raise SystemExit("IMAP_HOST / IMAP_USER / IMAP_PASSWORD 未配置（见 .env.example）")
        store = Store(cfg.db_path)
        store.init_schema()
        print(f"正在连接 {cfg.imap.host}:{cfg.imap.port} ...")
        try:
            fetcher = Fetcher(cfg.imap, cfg.fetch, store)
            for folder, n in fetcher.fetch_all().items():
                print(f"{folder}: 新拉取 {n} 封")
        finally:
            store.close()
        return 0

    if args.command == "dedup" and args.queue:
        store = Store(cfg.db_path)
        store.init_schema()
        try:
            applied = apply_review(store, args.queue)
            print(f"已导入裁定 {applied} 对")
            print(f"标记 unique {store.finalize_uniques()} 封")
        finally:
            store.close()
        return 0

    store = Store(cfg.db_path)
    store.init_schema()
    try:
        if args.command == "parse":
            print(f"解析完成：{run_parse(store)} 封")
        elif args.command == "dedup":
            run_dedup(store, cfg, use_llm=args.llm, full=args.full)
        elif args.command == "export":
            print(json.dumps(export_all(store, cfg.export.output_dir,
                                        prune=args.prune),
                             ensure_ascii=False))
        elif args.command == "report":
            print(json.dumps(store.counts(), ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
