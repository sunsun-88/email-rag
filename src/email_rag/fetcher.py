from __future__ import annotations

import logging
import time
from typing import Callable

import imapclient

from .config import FetchConfig, ImapConfig
from .store import Store

log = logging.getLogger(__name__)

_BATCH = 200  # 每批拉取封数：小批次让进度更细腻、内存更省；总限速时长不变


class FetchError(Exception):
    """folder 重连耗尽后抛出。"""


class Fetcher:
    def __init__(self, imap_cfg: ImapConfig, fetch_cfg: FetchConfig,
                 store: Store, client_factory: Callable | None = None):
        self._imap = imap_cfg
        self._cfg = fetch_cfg
        self._store = store
        self._factory = client_factory or self._default_factory

    def _default_factory(self):
        return imapclient.IMAPClient(
            self._imap.host, port=self._imap.port, ssl=True, timeout=60
        )

    def _login(self, client):
        client.login(self._imap.user, self._imap.password)
        return client

    # ---- folders ----
    def list_folders(self) -> list[str]:
        client = self._login(self._factory())
        try:
            names = []
            for flags, _delim, name in client.list_folders():
                if b"\\Noselect" in flags:
                    continue
                names.append(name)
            return names
        finally:
            client.logout()

    def resolve_folders(self) -> list[str]:
        available = self.list_folders()
        if self._cfg.folders == "all":
            excluded = set(self._cfg.exclude)
            return [f for f in available if f not in excluded]
        wanted = [f.strip() for f in self._cfg.folders.split(",") if f.strip()]
        return [f for f in wanted if f in available]

    # ---- fetch ----
    def fetch_all(self) -> dict[str, int]:
        folders = self.resolve_folders()
        log.info("将处理以下文件夹：%s", ", ".join(folders) or "（无）")
        result = {}
        for folder in folders:
            result[folder] = self.fetch_folder(folder)
        return result

    def fetch_folder(self, folder: str) -> int:
        delay, last_err = 1.0, None
        for attempt in range(self._cfg.reconnect_max):
            try:
                return self._fetch_folder_once(folder)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                log.warning("fetch %s attempt %d/%d failed: %s",
                            folder, attempt + 1, self._cfg.reconnect_max, exc)
                time.sleep(delay)
                delay *= 2
        raise FetchError(
            f"folder {folder} failed after {self._cfg.reconnect_max} attempts: {last_err}"
        )

    def _fetch_folder_once(self, folder: str) -> int:
        client = self._login(self._factory())
        fetched = 0
        try:
            client.select_folder(folder, readonly=True)
            watermark = self._store.watermark(folder)
            uids = sorted(u for u in client.search() if u > watermark)
            total = len(uids)
            log.info("开始拉取 %s：待拉取 %d 封", folder, total)
            done = 0
            for start in range(0, total, _BATCH):
                batch = uids[start: start + _BATCH]
                data = client.fetch(batch, ["BODY.PEEK[]"])
                for uid in sorted(data):
                    entry = data[uid]
                    mime = entry.get(b"BODY[]") or entry.get("BODY[]")
                    if mime is None:
                        continue
                    if self._store.upsert_raw(folder, int(uid), bytes(mime)):
                        fetched += 1
                done += len(batch)
                log.info("%s：进度 %d/%d（累计新写入 %d 封）",
                         folder, done, total, fetched)
                if self._cfg.rate_limit_per_sec > 0:
                    wait = len(batch) / self._cfg.rate_limit_per_sec
                    log.info("%s：限速等待 %.0f 秒...", folder, wait)
                    time.sleep(wait)
            log.info("%s：完成", folder)
            return fetched
        finally:
            client.logout()
