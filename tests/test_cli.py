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
