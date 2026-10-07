"""Cola e historial de trabajos en SQLite (sobrevive a reinicios y cortes de luz).

Con `protector` los datos de clientes van cifrados en disco: el contenido de cada trabajo (pedido,
etiqueta PDF/ZPL) y las columnas url, headers y meta. Lo que ya estaba guardado sin cifrar se cifra al abrir.
"""

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

from .i18n import tr
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


ENCRYPTED_COLUMNS = ("url", "headers", "meta")
CRYPTO_FLAG = "crypto:jobs-v1"


class PayloadUnavailable(Exception):
    """El contenido del trabajo ya no existe o no se puede descifrar."""


class JobStore:
    def __init__(self, db_path: Path, spool_dir: Path, protector=None) -> None:
        self.spool_dir = spool_dir
        self.protector = protector
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
        if protector is not None and self.kv_get(CRYPTO_FLAG) != "1":
            self._encrypt_existing()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- cifrado ------------------------------------------------------------
    def _seal(self, column: str, job_id: str, value: str) -> str:
        if self.protector is None or not value:
            return value
        return self.protector.encrypt_text(value, f"jobs.{column}:{job_id}")

    def _open(self, column: str, job_id: str, value: str) -> str:
        if self.protector is None or not value:
            return value
        from .security.crypto import CryptoError

        try:
            return self.protector.decrypt_text(value, f"jobs.{column}:{job_id}")
        except CryptoError:
            log.warning("No se pudo descifrar %s del trabajo %s", column, job_id)
            return ""

    def _write_payload(self, job_id: str, suffix: str, data: bytes) -> str:
        if self.protector is not None:
            data = self.protector.encrypt(data, f"spool:{job_id}")
        path = self.spool_dir / f"{job_id}.{suffix}"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return str(path)

    def read_payload(self, job: dict[str, Any]) -> bytes:
        """Contenido guardado del trabajo (descifrado). Lanza PayloadUnavailable si ya no está."""
        path = Path(job.get("payload_path") or "")
        if not job.get("payload_path") or not path.exists():
            raise PayloadUnavailable(tr("El contenido de este trabajo ya no está disponible"))
        data = path.read_bytes()
        if self.protector is not None:
            from .security.crypto import CryptoError

            try:
                data = self.protector.decrypt(data, f"spool:{job['id']}")
            except CryptoError:
                raise PayloadUnavailable(tr("El contenido de este trabajo está dañado o es de otra instalación")) from None
        return data

    def read_order(self, job: dict[str, Any]) -> dict[str, Any]:
        return json.loads(self.read_payload(job).decode("utf-8"))

    def save_payload(self, job_id: str, data: bytes, fmt: str | None = None) -> dict[str, Any] | None:
        """Guarda (cifrado) el archivo descargado de un trabajo con URL."""
        values: dict[str, Any] = {"payload_path": self._write_payload(job_id, "bin", data)}
        if fmt:
            values["format"] = fmt
        return self._update(job_id, **values)

    def _encrypt_existing(self) -> None:
        """Migración: cifra lo guardado por versiones anteriores (una sola vez)."""
        with self._lock:
            rows = self._conn.execute("SELECT id, url, headers, meta, payload_path FROM jobs").fetchall()
            for row in rows:
                values = {
                    column: self._seal(column, row["id"], row[column] or "")
                    for column in ENCRYPTED_COLUMNS
                    if row[column] and not str(row[column]).startswith("lbx1:")
                }
                if values:
                    cols = ", ".join(f"{k}=?" for k in values)
                    self._conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*values.values(), row["id"]))
                path = Path(row["payload_path"] or "")
                if row["payload_path"] and path.exists():
                    data = path.read_bytes()
                    if not self.protector.is_encrypted(data):
                        tmp = path.with_suffix(".tmp")
                        tmp.write_bytes(self.protector.encrypt(data, f"spool:{row['id']}"))
                        tmp.replace(path)
        self.kv_set(CRYPTO_FLAG, "1")
        if rows:
            log.info("Datos de %d trabajo(s) cifrados en disco", len(rows))

    # --- utilidades -------------------------------------------------------
    def _row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        job["url"] = self._open("url", job["id"], job.get("url") or "")
        for key in ("headers", "meta"):
            try:
                job[key] = json.loads(self._open(key, job["id"], job[key] or "") or "{}")
            except json.JSONDecodeError as exc:  # el trabajo sigue (p. ej. sin el tamaño de la guía), pero queda escrito
                log.warning("Trabajo %s: «%s» ilegible (%s); se usa vacío", job["id"], key, exc)
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
                payload_path = self._write_payload(job_id, "bin", req.content)
            elif req.order is not None:
                payload_path = self._write_payload(job_id, "json", json.dumps(req.order, ensure_ascii=False).encode("utf-8"))
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
                    self._seal("url", job_id, req.url),
                    self._seal("headers", job_id, json.dumps(req.headers)),
                    self._seal("meta", job_id, json.dumps(req.meta, ensure_ascii=False, default=str)),
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

    def working_now(self, now: float | None = None) -> int:
        """Trabajos imprimiéndose o listos para imprimirse ya (los que esperan un reintento más tarde no cuentan)."""
        now = time.time() if now is None else now
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='printing' OR (status='queued' AND next_attempt_at<=?)",
                (now + 5,),
            ).fetchone()[0]

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
    def set_format(self, job_id: str, fmt: str) -> dict[str, Any] | None:
        return self._update(job_id, format=fmt)

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
            error=tr("Cancelado por el usuario"),
            ack_pending=1 if job["source"] in ACK_SOURCES else 0,
        )

    def reprint(self, job_id: str, printer: str | None = None) -> dict[str, Any] | None:
        """Crea un trabajo nuevo con el mismo contenido (origen 'manual')."""
        job = self.get(job_id)
        if not job:
            return None
        req = JobRequest(
            external_id=f"reimpresion-{uuid.uuid4().hex[:12]}",
            title=tr("{0} (reimpresión)", job['title']),
            printer=job["printer"] if printer is None else printer,
            copies=job["copies"],
            format=job["format"],
            url=job["url"] if not job["payload_path"] else "",
            headers=job["headers"],
            meta=job["meta"],
        )
        if job["payload_path"]:
            try:
                data = self.read_payload(job)
            except PayloadUnavailable:
                return None
            if job["payload_path"].endswith(".json"):
                req.order = json.loads(data.decode("utf-8"))
            else:
                req.content = data
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
            except FileNotFoundError:
                pass  # ya no estaba
            except OSError as exc:  # queda en la carpeta de la cola (cifrado): se anota para limpiarlo
                log.warning("No se pudo borrar el archivo del trabajo %s: %s", job.get("id"), exc)

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

    def kv_items(self, prefix: str) -> dict[str, str]:
        """Todas las claves que empiezan por `prefix` (con su valor)."""
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM kv WHERE key LIKE ? ESCAPE '\\'", (escaped + "%",)
            ).fetchall()
        return {key: value for key, value in rows}

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
