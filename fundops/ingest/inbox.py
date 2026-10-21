"""Load the inbox folder into Message records.

Each message file is a small RFC-822-ish text: header lines (From / To / Date /
Subject / Attachment), a blank line, then the body. The loader is deliberately
tolerant — a missing header becomes an empty field, not a crash — because a
real inbox is never tidy.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from ..config import INBOX_DIR
from ..models import Message

_HEADER_RE = re.compile(r"^([A-Za-z-]+):\s*(.*)$")


def _parse_date(s: str) -> date:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {s!r}")


def parse_message_file(path: Path) -> Message:
    text = path.read_text(encoding="utf-8")
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False
    for line in text.splitlines():
        if not in_body:
            if line.strip() == "":
                in_body = True
                continue
            m = _HEADER_RE.match(line)
            if m:
                headers[m.group(1).lower()] = m.group(2).strip()
                continue
            in_body = True  # a non-header line before the blank: treat as body
        body_lines.append(line)

    msg_id = path.name.split("_", 1)[0]
    sender = headers.get("from", "")
    domain = sender.split("@", 1)[1].lower() if "@" in sender else ""
    sent_str = headers.get("date") or path.name.split("_")[1]
    return Message(
        msg_id=msg_id,
        filename=path.name,
        sender=sender,
        sender_domain=domain,
        to=headers.get("to", ""),
        sent=_parse_date(sent_str),
        subject=headers.get("subject", ""),
        attachment=headers.get("attachment") or None,
        body="\n".join(body_lines),
    )


def load_inbox(inbox_dir: Path = INBOX_DIR) -> list[Message]:
    files = sorted(p for p in inbox_dir.iterdir() if p.suffix == ".txt")
    return [parse_message_file(p) for p in files]
