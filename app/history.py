"""Persistent task/run history in SQLite (DB-GPT-style task list, kept tiny).

A task is one analysis conversation (multiple Q&A rounds); each run is one
question answered inside a task.
"""

import sqlite3
import time
from pathlib import Path

from .config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "history.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            title TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT,
            ok INTEGER NOT NULL,
            plan TEXT,
            code TEXT,
            run_dir TEXT
        );
        """
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
    if "task_id" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN task_id INTEGER")
    if "followups" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN followups TEXT")
    if "stdout" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN stdout TEXT")
    if "stderr" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN stderr TEXT")
    if "spec" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN spec TEXT")
    if "pinned" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN pinned INTEGER DEFAULT 0")
    conn.commit()


def create_task(title: str = "新任务") -> int:
    with _conn() as conn:
        _init(conn)
        cur = conn.execute(
            "INSERT INTO tasks (ts, title) VALUES (?, ?)",
            (time.strftime("%Y-%m-%d %H:%M"), title),
        )
        return int(cur.lastrowid)


def rename_task(task_id: int, title: str) -> None:
    with _conn() as conn:
        _init(conn)
        conn.execute("UPDATE tasks SET title = ? WHERE id = ?", (title, task_id))
        conn.commit()


def list_tasks(limit: int = 30) -> list[dict]:
    with _conn() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT id, ts, title FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def save_run(entry: dict) -> int:
    import json

    with _conn() as conn:
        _init(conn)
        cur = conn.execute(
            "INSERT INTO runs (ts, question, answer, ok, plan, code, run_dir, task_id, "
            "followups, stdout, stderr, spec, pinned) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                entry.get("ts") or time.strftime("%Y-%m-%d %H:%M"),
                entry.get("question", ""),
                entry.get("answer", ""),
                1 if entry.get("ok") else 0,
                entry.get("plan", ""),
                entry.get("code", ""),
                entry.get("run_dir", ""),
                entry.get("task_id"),
                json.dumps(entry.get("followups", []), ensure_ascii=False),
                (entry.get("stdout") or "")[-4000:],
                (entry.get("stderr") or "")[-4000:],
                json.dumps(entry.get("spec", {}), ensure_ascii=False),
            ),
        )
        return int(cur.lastrowid)


def toggle_pin(run_id: int) -> bool:
    with _conn() as conn:
        _init(conn)
        cur = conn.execute("SELECT pinned FROM runs WHERE id = ?", (run_id,))
        row = cur.fetchone()
        if not row:
            return False
        new_val = 0 if row["pinned"] else 1
        conn.execute("UPDATE runs SET pinned = ? WHERE id = ?", (new_val, run_id))
        conn.commit()
        return bool(new_val)


def list_pinned() -> list[dict]:
    import json

    with _conn() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT * FROM runs WHERE pinned = 1 ORDER BY id DESC"
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["followups"] = json.loads(item.get("followups") or "[]")
            except Exception:
                item["followups"] = []
            out.append(item)
        return out


def get_task_runs(task_id: int) -> list[dict]:
    import json

    with _conn() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT * FROM runs WHERE task_id = ? ORDER BY id ASC", (task_id,)
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["followups"] = json.loads(item.get("followups") or "[]")
            except Exception:
                item["followups"] = []
            try:
                item["spec"] = json.loads(item.get("spec") or "{}")
            except Exception:
                item["spec"] = {}
            out.append(item)
        return out


def history_context(task_id: int, limit: int = 3) -> list[dict[str, str]]:
    """Recent Q&A pairs of THIS task for the agent's follow-up memory."""
    with _conn() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT question, answer FROM runs WHERE task_id = ? AND ok = 1 "
            "ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [{"question": r["question"], "answer": r["answer"]} for r in reversed(rows)]


# ---- backwards-compatible helpers -------------------------------------------

def list_runs(limit: int = 30) -> list[dict]:
    with _conn() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT id, ts, question, ok FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_run(run_id: int) -> dict | None:
    with _conn() as conn:
        _init(conn)
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None
