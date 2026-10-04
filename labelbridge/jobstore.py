"""Cola e historial de trabajos en SQLite (sobrevive a reinicios y cortes de luz)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import JobRequest

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    printer TEXT NOT NULL DEFAULT '',
    copies INTEGER NOT NULL DEFAULT 1,
    format TEXT NOT NULL DEFAULT 'auto',
    url TEXT NOT NULL DEFAULT '',
    headers TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}',
    payload_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    printer_used TEXT NOT NULL DEFAULT '',
    next_attempt_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    printed_at REAL,
    ack_pending INTEGER NOT NULL DEFAULT 0,
    ack_sent TEXT NOT NULL DEFAULT '',
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, created_at REAL NOT NULL);
"""

ACK_SOURCES = {"mqtt"}
FINAL_STATUSES = {"done", "failed", "canceled"}


def _start_of_today() -> float:
    now = datetime.now()
    return datetime(now.year, now.month, now.day).timestamp()


class JobStore:
    def __init__(self, db_path: Path, spool_dir: Path) -> None:
        self.spool_dir = spool_dir
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            # Recuperación tras cierre inesperado: lo que estaba imprimiéndose vuelve a la cola.
            self._conn.execute(
                "UPDATE jobs SET status='queued', updated_at=? WHERE status='printing'", (time.time(),)
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- utilidades -------------------------------------------------------
    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        for key in ("headers", "meta"):
            try:
                job[key] = json.loads(job[key] or "{}")
            except json.JSONDecodeError:
                job[key] = {}
        return job

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(sql, params).fetchone())

    def _update(self, job_id: str, **values: Any) -> dict[str, Any] | None:
        values["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in values)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*values.values(), job_id))
            return self.get(job_id)

    # --- alta ---------------------------------------------------------------
    def add(self, req: JobRequest, source: str) -> tuple[dict[str, Any], bool]:
        """Agrega un trabajo. Si ya existe (mismo origen + id externo) devuelve el existente."""
        with self._lock:
            existing = self.get_by_external(source, req.external_id)
            if existing:
                return existing, False
            job_id = uuid.uuid4().hex
            payload_path = ""
            if req.content is not None:
                path = self.spool_dir / f"{job_id}.bin"
                path.write_bytes(req.content)
                payload_path = str(path)
            elif req.order is not None:
                path = self.spool_dir / f"{job_id}.json"
                path.write_text(json.dumps(req.order, ensure_ascii=False), encoding="utf-8")
                payload_path = str(path)
            now = time.time()
            self._conn.execute(
                """INSERT INTO jobs (id, source, external_id, title, printer, copies, format, url,
                       headers, meta, payload_path, status, next_attempt_at, created_at, updated_at,
                       ack_pending)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?)""",
                (
                    job_id,
                    source,
                    req.external_id,
                    req.title,
                    req.printer,
                    req.copies,
                    req.format,
                    req.url,
                    json.dumps(req.headers),
                    json.dumps(req.meta, ensure_ascii=False, default=str),
                    payload_path,
                    now,
                    now,
                    now,
                    1 if source in ACK_SOURCES else 0,
                ),
            )
            job = self.get(job_id)
            assert job is not None
            return job, True

    # --- consulta -------------------------------------------------------------
    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def get_by_external(self, source: str, external_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM jobs WHERE source=? AND external_id=?", (source, external_id))

    def find(self, any_id: str) -> dict[str, Any] | None:
        return self.get(any_id) or self._one(
            "SELECT * FROM jobs WHERE external_id=? ORDER BY created_at DESC LIMIT 1", (any_id,)
        )

    def next_ready(self, now: float | None = None) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM jobs WHERE status='queued' AND next_attempt_at<=? ORDER BY created_at LIMIT 1",
            (now if now is not None else time.time(),),
        )

    def needing_download(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status='queued' AND url!='' AND payload_path='' "
                "ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def list(self, limit: int = 300, status: str | None = None, search: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if search:
            where.append("(title LIKE ? OR external_id LIKE ? OR printer_used LIKE ? OR error LIKE ?)")
            params.extend([f"%{search}%"] * 4)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def counts(self) -> dict[str, int]:
        today = _start_of_today()
        with self._lock:
            q = self._conn.execute
            return {
                "queued": q("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','printing')").fetchone()[0],
                "done_today": q(
                    "SELECT COUNT(*) FROM jobs WHERE status='done' AND printed_at>=?", (today,)
                ).fetchone()[0],
                "failed_today": q(
                    "SELECT COUNT(*) FROM jobs WHERE status='failed' AND updated_at>=?", (today,)
                ).fetchone()[0],
                "total": q("SELECT COUNT(*) FROM jobs").fetchone()[0],
            }

    def count_done(self, source: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM jobs WHERE status='done'"
        params: tuple = ()
        if source:
            sql += " AND source=?"
            params = (source,)
        with self._lock:
            return self._conn.execute(sql, params).fetchone()[0]

    def next_retry_at(self) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(next_attempt_at) FROM jobs WHERE status='queued'"
            ).fetchone()
        return row[0] if row and row[0] is not None else None

    # --- transiciones -----------------------------------------------------------
    def set_payload(self, job_id: str, path: str, fmt: str | None = None) -> dict[str, Any] | None:
        values: dict[str, Any] = {"payload_path": path}
        if fmt:
            values["format"] = fmt
        return self._update(job_id, **values)

    def set_printing(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='printing', attempts=attempts+1, updated_at=? WHERE id=?",
                (time.time(), job_id),
            )
            return self.get(job_id)

    def set_done(self, job_id: str, printer_used: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        return self._update(
            job_id,
            status="done",
            error="",
            printer_used=printer_used,
            printed_at=time.time(),
            ack_pending=1 if job and job["source"] in ACK_SOURCES else 0,
        )

    def set_retry(self, job_id: str, error: str, delay: float, printer_used: str = "") -> dict[str, Any] | None:
        return self._update(
            job_id,
            status="queued",
            error=error,
            printer_used=printer_used,
            next_attempt_at=time.time() + delay,
        )

    def set_failed(self, job_id: str, error: str, printer_used: str = "") -> dict[str, Any] | None:
        job = self.get(job_id)
        return self._update(
            job_id,
            status="failed",
            error=error,
            printer_used=printer_used,
            ack_pending=1 if job and job["source"] in ACK_SOURCES else 0,
        )

    def retry(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job or job["status"] == "printing":
            return job
        return self._update(job_id, status="queued", attempts=0, error="", next_attempt_at=0)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job or job["status"] != "queued":
            return job
        return self._update(
            job_id,
            status="canceled",
            error="Cancelado por el usuario",
            ack_pending=1 if job["source"] in ACK_SOURCES else 0,
        )

    def reprint(self, job_id: str, printer: str | None = None) -> dict[str, Any] | None:
        """Crea un trabajo nuevo con el mismo contenido (origen 'manual')."""
        job = self.get(job_id)
        if not job:
            return None
        req = JobRequest(
            external_id=f"reimpresion-{uuid.uuid4().hex[:12]}",
            title=f"{job['title']} (reimpresión)",
            printer=job["printer"] if printer is None else printer,
            copies=job["copies"],
            format=job["format"],
            url=job["url"] if not job["payload_path"] else "",
            headers=job["headers"],
            meta=job["meta"],
        )
        if job["payload_path"]:
            path = Path(job["payload_path"])
            if not path.exists():
                return None
            if path.suffix == ".json":
                req.order = json.loads(path.read_text(encoding="utf-8"))
            else:
                req.content = path.read_bytes()
        new_job, _ = self.add(req, "manual")
        return new_job

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            return
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self._remove_payload(job)

    def _remove_payload(self, job: dict[str, Any]) -> None:
        if job.get("payload_path"):
            try:
                Path(job["payload_path"]).unlink()
            except OSError:
                pass

    def cleanup(self, retention_days: int) -> int:
        cutoff = time.time() - max(1, retention_days) * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE created_at<? AND status IN ('done','failed','canceled')"
                " AND ack_pending=0",
                (cutoff,),
            ).fetchall()
            self._conn.execute(
                "DELETE FROM jobs WHERE created_at<? AND status IN ('done','failed','canceled')"
                " AND ack_pending=0",
                (cutoff,),
            )
            self._conn.execute("DELETE FROM seen WHERE created_at<?", (cutoff - 60 * 86400,))
        for row in rows:
            self._remove_payload(dict(row))
        return len(rows)

    # --- acks (confirmaciones hacia n8n) --------------------------------------
    def pending_acks(self, source: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE source=? AND ack_pending=1 ORDER BY updated_at LIMIT ?",
                (source, limit),
            ).fetchall()
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def mark_acked(self, job_id: str, status_sent: str, expected_updated_at: float) -> None:
        # Solo se limpia si el trabajo no cambió mientras se enviaba la confirmación.
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET ack_pending=0, ack_sent=? WHERE id=? AND updated_at=?",
                (status_sent, job_id, expected_updated_at),
            )

    def request_ack(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE jobs SET ack_pending=1 WHERE id=?", (job_id,))

    # --- estado de conectores ----------------------------------------------------
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def seen(self, key: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None

    def mark_seen(self, key: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO seen(key, created_at) VALUES(?, ?)", (key, time.time())
            )

    def unmark_seen(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM seen WHERE key=?", (key,))

    def count_seen(self, prefix: str, since: float) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM seen WHERE key LIKE ? AND created_at>=?", (f"{prefix}%", since)
            ).fetchone()[0]

    @staticmethod
    def start_of_today() -> float:
        return _start_of_today()
