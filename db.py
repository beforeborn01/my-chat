"""SQLite persistence layer for users, conversations, and messages.

The DB file lives at ``$DATA_DIR/chat.db`` (default ``./data/chat.db``).
Connections are short-lived and per-call to keep things simple — the
write volume here is tiny.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "chat.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '新会话',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (username) REFERENCES users(username)
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(username, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role            TEXT NOT NULL,        -- 'user' | 'assistant'
    intent          TEXT,                 -- 'text' | 'image' (assistant only)
    content         TEXT NOT NULL DEFAULT '',
    image_path      TEXT,                 -- /images/<user>/<conv>/<file>
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def now() -> int:
    return int(time.time())


# ---------- users ----------

def upsert_user(username: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO users(username, created_at) VALUES (?, ?)",
            (username, now()),
        )


# ---------- conversations ----------

def list_conversations(username: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE username=? ORDER BY updated_at DESC",
            (username,),
        ).fetchall()
    return [dict(r) for r in rows]


def create_conversation(username: str, title: str = "新会话") -> int:
    t = now()
    with connect() as c:
        cur = c.execute(
            "INSERT INTO conversations(username, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (username, title, t, t),
        )
        return cur.lastrowid


def get_conversation(conv_id: int, username: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT id, username, title, created_at, updated_at FROM conversations "
            "WHERE id=? AND username=?",
            (conv_id, username),
        ).fetchone()
    return dict(row) if row else None


def rename_conversation(conv_id: int, username: str, title: str) -> None:
    with connect() as c:
        c.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=? AND username=?",
            (title[:120], now(), conv_id, username),
        )


def touch_conversation(conv_id: int) -> None:
    with connect() as c:
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id))


def delete_conversation(conv_id: int, username: str) -> None:
    with connect() as c:
        c.execute(
            "DELETE FROM conversations WHERE id=? AND username=?",
            (conv_id, username),
        )


# ---------- messages ----------

def list_messages(conv_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, role, intent, content, image_path, created_at "
            "FROM messages WHERE conversation_id=? ORDER BY id ASC",
            (conv_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(
    conv_id: int,
    role: str,
    content: str = "",
    intent: str | None = None,
    image_path: str | None = None,
) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO messages(conversation_id, role, intent, content, image_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, role, intent, content, image_path, now()),
        )
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id))
        return cur.lastrowid


def text_history_for_llm(conv_id: int, limit: int = 20) -> list[dict]:
    """Return only text turns as [{role, content}] for re-feeding the LLM.
    Image turns are skipped — they don't help text continuity and inflate context.
    """
    with connect() as c:
        rows = c.execute(
            "SELECT role, content, intent FROM messages "
            "WHERE conversation_id=? AND (image_path IS NULL OR image_path = '') "
            "ORDER BY id ASC",
            (conv_id,),
        ).fetchall()
    msgs = [{"role": r["role"], "content": r["content"]} for r in rows if r["content"]]
    return msgs[-limit:]
