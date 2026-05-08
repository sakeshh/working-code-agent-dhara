from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional


def _db_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "output")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, "chat_sessions.sqlite3")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS experiences (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          ts REAL NOT NULL,
          user_text TEXT,
          action TEXT,
          success INTEGER,
          notes TEXT,
          FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_experiences_session_ts ON experiences(session_id, ts DESC)")
    return conn


def load_session(session_id: str) -> Dict[str, Any]:
    sid = (session_id or "default").strip() or "default"
    now = time.time()
    conn = _connect()
    try:
        row = conn.execute("SELECT payload_json FROM sessions WHERE session_id = ?", (sid,)).fetchone()
        if not row:
            return {"session_id": sid, "created_at": now, "updated_at": now, "messages": [], "context": {}}
        try:
            data = json.loads(row[0])
            if not isinstance(data, dict):
                raise ValueError("bad session payload")
            data.setdefault("session_id", sid)
            data.setdefault("messages", [])
            data.setdefault("context", {})
            return data
        except Exception:
            return {"session_id": sid, "created_at": now, "updated_at": now, "messages": [], "context": {}}
    finally:
        conn.close()


def save_session(session: Dict[str, Any]) -> None:
    sid = (session.get("session_id") or "default").strip() or "default"
    now = time.time()
    session["session_id"] = sid
    session.setdefault("created_at", now)
    session["updated_at"] = now
    payload = json.dumps(session, ensure_ascii=False, default=str)
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO sessions(session_id, created_at, updated_at, payload_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
              updated_at=excluded.updated_at,
              payload_json=excluded.payload_json
            """,
            (sid, float(session.get("created_at") or now), now, payload),
        )
        conn.commit()
    finally:
        conn.close()


def list_sessions(*, limit: int = 50) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT session_id, created_at, updated_at, payload_json FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for sid, created_at, updated_at, payload_json in rows:
            title: Optional[str] = None
            last: Optional[str] = None
            try:
                payload = json.loads(payload_json) if payload_json else {}
                title = payload.get("title") if isinstance(payload, dict) else None
                msgs = payload.get("messages") if isinstance(payload, dict) else None
                if isinstance(msgs, list) and msgs:
                    # pick last user message as preview
                    for m in reversed(msgs):
                        if isinstance(m, dict) and m.get("role") == "user" and m.get("content"):
                            last = str(m.get("content"))
                            break
            except Exception:
                pass
            out.append(
                {
                    "session_id": sid,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "title": title,
                    "preview": (last[:120] + "…") if last and len(last) > 120 else last,
                }
            )
        return out
    finally:
        conn.close()


def add_experience(
    *,
    session_id: str,
    user_text: Optional[str],
    action: Optional[str],
    success: Optional[bool],
    notes: Optional[str] = None,
) -> None:
    sid = (session_id or "default").strip() or "default"
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO experiences(session_id, ts, user_text, action, success, notes) VALUES(?,?,?,?,?,?)",
            (
                sid,
                time.time(),
                (user_text or "").strip()[:4000] if user_text else None,
                (action or "").strip()[:200] if action else None,
                (1 if success else 0) if success is not None else None,
                (notes or "").strip()[:2000] if notes else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_recent_experiences(*, session_id: str, limit: int = 12) -> List[Dict[str, Any]]:
    sid = (session_id or "default").strip() or "default"
    limit = max(1, min(int(limit or 12), 50))
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT ts, user_text, action, success, notes FROM experiences WHERE session_id=? ORDER BY ts DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for ts, user_text, action, success, notes in rows:
            out.append(
                {
                    "ts": ts,
                    "user_text": user_text,
                    "action": action,
                    "success": (bool(success) if success is not None else None),
                    "notes": notes,
                }
            )
        return out
    finally:
        conn.close()

