from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class ImapConfig:
    host: str = ""
    port: int = 993
    user: str = ""
    password: str = ""


@dataclass
class LLMConfig:
    provider: str = "none"  # none | deepseek | openai
    api_key: str = ""
    model: str = ""
    base_url: str = ""


@dataclass
class FetchConfig:
    folders: str = "all"  # "all" 或逗号分隔列表
    exclude: list = field(default_factory=lambda: ["Junk", "Trash", "垃圾邮件", "已删除"])
    rate_limit_per_sec: float = 2.0
    reconnect_max: int = 5


@dataclass
class DedupConfig:
    auto_merge_threshold: float = 0.95
    review_threshold: float = 0.75
    llm_batch_size: int = 20          # 每批送 LLM 的对数（输出 token 上限=16×批大小）
    llm_auto_dup_threshold: float = 0.90    # --llm 时相似度≥此值直判重复，不调 LLM
    llm_auto_unique_threshold: float = 0.80  # --llm 时相似度<此值直判不重复，不调 LLM
    llm_output_tokens_per_pair: int = 384  # 每对输出 token 预算（reasoning 模型实测约 300/对，太小会截断）


@dataclass
class ExportConfig:
    output_dir: str = "output"


@dataclass
class AppConfig:
    imap: ImapConfig
    llm: LLMConfig
    fetch: FetchConfig = field(default_factory=FetchConfig)
    dedup: DedupConfig = field(default_factory=DedupConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    db_path: str = "data/emails.db"
    review_queue_path: str = "data/review_queue.json"


def _override(instance, data: dict):
    """把 dict 中属于该 dataclass 的字段覆盖到实例上。"""
    names = set(instance.__dataclass_fields__)
    for key, value in (data or {}).items():
        if key in names:
            setattr(instance, key, value)
    return instance


def load_config(env_path: str | Path = ".env", config_path: str | Path = "config.yaml") -> AppConfig:
    load_dotenv(dotenv_path=env_path)

    imap = ImapConfig(
        host=os.environ.get("IMAP_HOST", ""),
        port=int(os.environ.get("IMAP_PORT", "993")),
        user=os.environ.get("IMAP_USER", ""),
        password=os.environ.get("IMAP_PASSWORD", ""),
    )
    llm = LLMConfig(
        provider=os.environ.get("LLM_PROVIDER", "none"),
        api_key=os.environ.get("LLM_API_KEY", ""),
        model=os.environ.get("LLM_MODEL", ""),
        base_url=os.environ.get("LLM_BASE_URL", ""),
    )

    fetch, dedup, export = FetchConfig(), DedupConfig(), ExportConfig()
    cfg_file = Path(config_path)
    if cfg_file.exists():
        data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        _override(fetch, data.get("fetch"))
        _override(dedup, data.get("dedup"))
        _override(export, data.get("export"))

    return AppConfig(imap=imap, llm=llm, fetch=fetch, dedup=dedup, export=export)
