import pytest

from email_rag import fetcher as fetcher_module
from email_rag.config import FetchConfig, ImapConfig
from email_rag.fetcher import FetchError, Fetcher
from email_rag.store import Store


class FakeClient:
    def __init__(self, folders=None, uids=None, fail_times=0):
        # folders: list of (flags, delimiter, name)
        self.folders = folders or [([b"\\HasNoChildren"], b"/", "INBOX")]
        self.uids = uids or {}
        self.fail_times = fail_times  # select_folder 连续失败的次数
        self.selected = []
        self.logouts = 0

    def login(self, user, password):
        assert password  # 凭证非空（这里不校验内容）

    def logout(self):
        self.logouts += 1

    def list_folders(self):
        return self.folders

    def select_folder(self, name, readonly=True):
        self.selected.append(name)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("connection reset")

    def search(self):
        return list(self.uids.keys())

    def fetch(self, batch, items):
        return {uid: {b"BODY[]": self.uids[uid]} for uid in batch}


def _make_store(tmp_path):
    store = Store(tmp_path / "t.db")
    store.init_schema()
    return store


def _fetcher(store, fake, fetch_cfg=None):
    return Fetcher(
        ImapConfig(host="h", user="u", password="p"),
        fetch_cfg or FetchConfig(rate_limit_per_sec=0),
        store,
        client_factory=lambda: fake,
    )


def test_fetch_all_inserts_raw(tmp_path):
    store = _make_store(tmp_path)
    fake = FakeClient(uids={1: b"raw-1", 2: b"raw-2"})
    result = _fetcher(store, fake).fetch_all()
    assert result == {"INBOX": 2}
    assert store.counts()["raw"] == 2
    store.close()


def test_watermark_resume_no_refetch(tmp_path):
    store = _make_store(tmp_path)
    store.upsert_raw("INBOX", 1, b"old")
    store.upsert_raw("INBOX", 2, b"old2")
    fake = FakeClient(uids={1: b"old", 2: b"old2", 3: b"new-3"})
    result = _fetcher(store, fake).fetch_all()
    assert result == {"INBOX": 1}
    assert store.watermark("INBOX") == 3
    store.close()


def test_retry_on_connection_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda s: None)
    store = _make_store(tmp_path)
    fake = FakeClient(uids={1: b"r1"}, fail_times=1)
    result = _fetcher(store, fake).fetch_all()
    assert result == {"INBOX": 1}
    assert fake.selected == ["INBOX", "INBOX"]  # 第二次重连成功
    store.close()


def test_retry_exhausted_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda s: None)
    store = _make_store(tmp_path)
    fake = FakeClient(uids={1: b"r1"}, fail_times=99)
    with pytest.raises(FetchError):
        _fetcher(store, fake).fetch_all()
    store.close()


def test_fetch_logs_progress(store, caplog):
    """拉取过程输出进度与限速日志，避免"看起来像卡死"。"""
    import logging

    fake = FakeClient(uids={1: b"r1", 2: b"r2", 3: b"r3"})
    fetcher = Fetcher(
        ImapConfig(host="h", user="u", password="p"),
        FetchConfig(rate_limit_per_sec=100),
        store,
        client_factory=lambda: fake,
    )
    with caplog.at_level(logging.INFO):
        fetcher.fetch_all()
    messages = [r.getMessage() for r in caplog.records]
    assert any("进度" in m and "3/3" in m for m in messages)
    assert any("限速等待" in m for m in messages)
    store.close()


def test_resolve_folders_excludes_junk_and_trash(tmp_path):
    store = _make_store(tmp_path)
    fake = FakeClient(folders=[
        ([b"\\HasNoChildren"], b"/", "INBOX"),
        ([b"\\HasNoChildren"], b"/", "Junk"),
        ([b"\\HasNoChildren"], b"/", "Trash"),
        ([b"\\Noselect"], b"/", "[Gmail]"),
    ])
    folders = _fetcher(store, fake).resolve_folders()
    assert folders == ["INBOX"]  # 排除 exclude 列表与 Noselect
    store.close()
