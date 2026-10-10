from __future__ import annotations

import argparse
import json
import logging

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
from .llm import create_llm
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
    sub.add_parser("export", help="导出 Markdown")
    sub.add_parser("report", help="统计报告")
    return parser


def run_parse(store: Store) -> int:
    count = 0
    for raw in store.iter_unparsed_raw():
        parsed = parse_raw(raw["mime_bytes"], raw["folder"])
        store.insert_email(raw["id"], raw["folder"], parsed)
        count += 1
    return count


def run_dedup(store: Store, cfg: AppConfig, use_llm: bool = False) -> None:
    exact = exact_dedup(store)
    pairs = near_dedup(store, cfg.dedup.auto_merge_threshold,
                       cfg.dedup.review_threshold)
    print(f"完全重复合并 {exact} 封；近似重复复核队列 {len(pairs)} 对")
    if pairs:
        write_review_queue(store, pairs, cfg.review_queue_path)
        print(f"复核队列已写入 {cfg.review_queue_path}")
        if use_llm and cfg.llm.provider != "none":
            llm = create_llm(cfg.llm.provider, cfg.llm.api_key,
                             cfg.llm.model, cfg.llm.base_url)
            applied = judge_pairs_with_llm(store, pairs, llm, cfg.review_queue_path)
            print(f"LLM 已裁定 {applied} 对")
        else:
            print("人工裁定后运行：email-rag dedup data/review_queue.json")
    finalized = store.finalize_uniques()
    print(f"标记 unique {finalized} 封")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.env, args.config)

    if args.command == "fetch":
        if not (cfg.imap.host and cfg.imap.user and cfg.imap.password):
            raise SystemExit("IMAP_HOST / IMAP_USER / IMAP_PASSWORD 未配置（见 .env.example）")
        store = Store(cfg.db_path)
        store.init_schema()
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
            run_dedup(store, cfg, use_llm=args.llm)
        elif args.command == "export":
            print(json.dumps(export_all(store, cfg.export.output_dir),
                             ensure_ascii=False))
        elif args.command == "report":
            print(json.dumps(store.counts(), ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
