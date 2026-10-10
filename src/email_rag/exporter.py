from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .store import Store


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[^\w一-鿿]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return (text[:max_len].strip("-") or "untitled")


def render_markdown(record: dict) -> str:
    frontmatter = {
        "subject": record["subject"],
        "from": record["sender"],
        "to": json.loads(record["recipients"] or "[]"),
        "cc": json.loads(record["cc"] or "[]"),
        "date": record["date"],
        "folder": record["folder"],
        "message_id": record["message_id"],
        "labels": [],
        "dedup": record["dedup_status"],
        "content_hash": record["content_hash"][:8],
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    lines.append("")
    lines.append(record["body_text"])
    return "\n".join(lines) + "\n"


def export_all(store: Store, output_dir: str | Path) -> dict:
    out = Path(output_dir)
    count = 0
    for rec in store.iter_exportable():
        date = rec["date"] or ""
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", date):  # ISO 白名单，防路径穿越
            ym = date[:7]
            day = date[:10].replace("-", "")
        else:
            ym, day = "unknown-date", "00000000"
        folder_dir = out / slugify(rec["folder"], max_len=20) / ym
        folder_dir.mkdir(parents=True, exist_ok=True)
        # 空哈希（空正文）回退为 id：同日同主题邮件不得互相覆盖
        hash_part = rec["content_hash"][:8] or f"x{rec['id']:07d}"
        name = f"{day}-{slugify(rec['subject'])}-{hash_part}.md"
        (folder_dir / name).write_text(render_markdown(rec), encoding="utf-8")
        count += 1
    return {"exported": count, "output_dir": str(out)}
