import json

from conftest import build_eml
from email_rag.cli import main
from email_rag.store import Store


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "IMAP_HOST=h\nIMAP_USER=u\nIMAP_PASSWORD=p\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "fetch:\n  rate_limit_per_sec: 100\n", encoding="utf-8")
    store = Store(tmp_path / "data" / "emails.db")
    store.init_schema()
    store.upsert_raw("INBOX", 1, build_eml(subject="关于会议的通知",
                                           body="会议改期到明天上午十点。",
                                           message_id="<m1@test.com>"))
    store.upsert_raw("INBOX", 2, build_eml(subject="关于会议的通知",
                                           body="会议改期到明天上午十点。",
                                           message_id="<m2@test.com>"))
    store.upsert_raw("INBOX", 3, build_eml(subject="季度财务报表",
                                           body="财务报表已上传，请查收。",
                                           message_id="<m3@test.com>"))
    store.close()


def test_end_to_end_parse_dedup_export(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)
    assert main(["--env", ".env", "--config", "config.yaml", "parse"]) == 0
    assert main(["--env", ".env", "--config", "config.yaml", "dedup"]) == 0
    assert main(["--env", ".env", "--config", "config.yaml", "export"]) == 0

    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["exported"] == 2  # 1、2 完全重复合并，导出 2 封

    mds = sorted((tmp_path / "output").rglob("*.md"))
    assert len(mds) == 2
    content = mds[0].read_text(encoding="utf-8")
    assert content.startswith("---\n") and "subject:" in content


def test_report_command(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)
    main(["--env", ".env", "--config", "config.yaml", "parse"])
    capsys.readouterr()
    assert main(["--env", ".env", "--config", "config.yaml", "report"]) == 0
    counts = json.loads(capsys.readouterr().out)
    assert counts["raw"] == 3
    assert counts["emails"] == 3


def test_dedup_full_reruns(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)
    main(["--env", ".env", "--config", "config.yaml", "parse"])
    main(["--env", ".env", "--config", "config.yaml", "dedup"])
    capsys.readouterr()
    assert main(["--env", ".env", "--config", "config.yaml", "dedup", "--full"]) == 0
    out = capsys.readouterr().out
    # 全量重跑后状态与首批一致（1、2 合并，3 unique）
    store = Store(tmp_path / "data" / "emails.db")
    counts = store.counts()["by_status"]
    store.close()
    assert counts.get("duplicate") == 1 and counts.get("kept") == 1 and counts.get("unique") == 1


def test_dedup_llm_without_api_key_warns(tmp_path, monkeypatch, capsys):
    """--llm 但未配 LLM_API_KEY：明确提示跳过，而不是静默走人工分支。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMAP_HOST=h\nIMAP_USER=u\nIMAP_PASSWORD=p\n",
                                   encoding="utf-8")
    (tmp_path / "config.yaml").write_text("fetch:\n  rate_limit_per_sec: 100\n",
                                          encoding="utf-8")
    store = Store(tmp_path / "data" / "emails.db")
    store.init_schema()
    store.upsert_raw("INBOX", 1, build_eml(subject="会议通知", message_id="<n1@t>",
                                           body="会议改期到明天上午十点，请准时参加。"))
    store.upsert_raw("INBOX", 2, build_eml(subject="会议通知", message_id="<n2@t>",
                                           body="会议改期到明天上午十点半，请准时参加。"))
    store.close()
    main(["parse"])
    capsys.readouterr()
    assert main(["dedup", "--llm"]) == 0
    out = capsys.readouterr().out
    assert "LLM_API_KEY" in out


def test_export_prune_flag(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)
    main(["parse"])
    main(["dedup"])
    main(["export"])
    capsys.readouterr()
    stale = tmp_path / "output" / "stale.md"
    stale.write_text("x", encoding="utf-8")
    assert main(["--env", ".env", "--config", "config.yaml", "export", "--prune"]) == 0
    assert not stale.exists()
