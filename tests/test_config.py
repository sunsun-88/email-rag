import pytest

from email_rag.config import AppConfig, FetchConfig, load_config


def test_defaults_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("IMAP_HOST", raising=False)
    monkeypatch.delenv("IMAP_USER", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    cfg = load_config(env_path=tmp_path / "missing.env", config_path=tmp_path / "missing.yaml")
    assert isinstance(cfg, AppConfig)
    assert cfg.imap.host == ""
    assert cfg.imap.port == 993
    assert cfg.fetch.folders == "all"
    assert "Junk" in cfg.fetch.exclude
    assert cfg.dedup.auto_merge_threshold == 0.95
    assert cfg.db_path == "data/emails.db"


def test_env_loading(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "IMAP_HOST=imap.test.com\nIMAP_USER=a@b.c\nIMAP_PASSWORD=secret\n",
        encoding="utf-8",
    )
    for var in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(env_path=tmp_path / ".env", config_path=tmp_path / "missing.yaml")
    assert cfg.imap.host == "imap.test.com"
    assert cfg.imap.user == "a@b.c"
    assert cfg.imap.password == "secret"


def test_yaml_overrides(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "fetch:\n  rate_limit_per_sec: 10\n  exclude: [Spam]\n"
        "dedup:\n  auto_merge_threshold: 0.9\n",
        encoding="utf-8",
    )
    cfg = load_config(env_path=tmp_path / "missing.env", config_path=tmp_path / "config.yaml")
    assert cfg.fetch.rate_limit_per_sec == 10
    assert cfg.fetch.exclude == ["Spam"]
    assert cfg.dedup.auto_merge_threshold == 0.9
    # 未覆盖的保持默认
    assert cfg.dedup.review_threshold == 0.75
