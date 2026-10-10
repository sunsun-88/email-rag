from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    mime_bytes BLOB NOT NULL,
    parsed INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(folder, uid)
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id INTEGER NOT NULL REFERENCES raw_emails(id),
    folder TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    recipients TEXT NOT NULL DEFAULT '[]',
    cc TEXT NOT NULL DEFAULT '[]',
    date TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT NOT NULL DEFAULT '',
    "references" TEXT NOT NULL DEFAULT '[]',
    body_text TEXT NOT NULL DEFAULT '',
    body_html TEXT NOT NULL DEFAULT '',
    norm_text TEXT NOT NULL DEFAULT '',
    simhash INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    dedup_status TEXT NOT NULL DEFAULT 'pending',
    duplicate_of INTEGER,
    parse_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(dedup_status);
CREATE INDEX IF NOT EXISTS idx_emails_mid ON emails(message_id);
CREATE INDEX IF NOT EXISTS idx_emails_hash ON emails(content_hash);
CREATE INDEX IF NOT EXISTS idx_raw_parsed ON raw_emails(parsed);
"""

_U64 = (1 << 64) - 1
_I64 = 1 << 63


def _to_signed64(value: int) -> int:
    """无符号 64 位 simhash → SQLite 有符号 64 位（二进制补码，无损）。"""
    v = int(value) & _U64
    return v - (1 << 64) if v >= _I64 else v


def _fix_row(row: sqlite3.Row) -> dict:
    rec = dict(row)
    rec["simhash"] = int(rec["simhash"]) & _U64  # 有符号读回 → 无符号
    return rec


class Store:
    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---- raw ----
    def upsert_raw(self, folder: str, uid: int, mime_bytes: bytes) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO raw_emails(folder, uid, mime_bytes) VALUES (?, ?, ?)",
                (folder, uid, sqlite3.Binary(mime_bytes)),
            )
        return cur.rowcount > 0

    def watermark(self, folder: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(uid) FROM raw_emails WHERE folder = ?", (folder,)
        ).fetchone()
        return int(row[0] or 0)

    def iter_unparsed_raw(self, batch: int = 500) -> Iterator[dict]:
        last_id = 0
        while True:
            rows = self._conn.execute(
                """SELECT id, folder, mime_bytes FROM raw_emails
                   WHERE parsed = 0 AND id > ? ORDER BY id LIMIT ?""",
                (last_id, batch),
            ).fetchall()
            if not rows:
                return
            last_id = rows[-1]["id"]
            for r in rows:
                yield {"id": r["id"], "folder": r["folder"], "mime_bytes": bytes(r["mime_bytes"])}

    # ---- emails ----
    def insert_email(self, raw_id: int, folder: str, parsed: dict) -> int:
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO emails(raw_id, folder, subject, sender, recipients, cc, date,
                       message_id, in_reply_to, "references", body_text, body_html,
                       norm_text, simhash, content_hash, parse_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    raw_id, folder, parsed.get("subject", ""), parsed.get("sender", ""),
                    json.dumps(parsed.get("to", []), ensure_ascii=False),
                    json.dumps(parsed.get("cc", []), ensure_ascii=False),
                    parsed.get("date", ""), parsed.get("message_id", ""),
                    parsed.get("in_reply_to", ""),
                    json.dumps(parsed.get("references", []), ensure_ascii=False),
                    parsed.get("body_text", ""), parsed.get("body_html", ""),
                    parsed.get("norm_text", ""), _to_signed64(parsed.get("simhash", 0)),
                    parsed.get("content_hash", ""), parsed.get("parse_error", ""),
                ),
            )
            self._conn.execute(
                "UPDATE raw_emails SET parsed = 1 WHERE id = ?", (raw_id,)
            )
        return int(cur.lastrowid)

    def get_email(self, email_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        return _fix_row(row) if row else None

    def update_dedup(self, email_id: int, status: str, duplicate_of: int | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE emails SET dedup_status = ?, duplicate_of = ? WHERE id = ?",
                (status, duplicate_of, email_id),
            )

    def iter_dedup_candidates(self) -> Iterator[dict]:
        rows = self._conn.execute("SELECT * FROM emails").fetchall()
        for r in rows:
            yield _fix_row(r)

    def finalize_uniques(self) -> int:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE emails SET dedup_status = 'unique' WHERE dedup_status = 'pending'"
            )
        return cur.rowcount

    def iter_exportable(self) -> Iterator[dict]:
        rows = self._conn.execute(
            """SELECT * FROM emails
               WHERE dedup_status IN ('unique', 'kept') AND parse_error = ''"""
        ).fetchall()
        for r in rows:
            yield _fix_row(r)

    def counts(self) -> dict:
        raw = self._conn.execute("SELECT COUNT(*) FROM raw_emails").fetchone()[0]
        emails = self._conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        by_status = {
            row[0]: row[1]
            for row in self._conn.execute(
                "SELECT dedup_status, COUNT(*) FROM emails GROUP BY dedup_status"
            )
        }
        return {"raw": raw, "emails": emails, "by_status": by_status}
