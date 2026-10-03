"""Validación de trabajos y cola SQLite."""

from __future__ import annotations

import base64
import time

import pytest

from labelbridge.models import JobError, parse_job, parse_jobs_payload


def test_parse_job_sources():
    assert parse_job({"id": "a", "url": "https://x.com/l.pdf"}).url == "https://x.com/l.pdf"
    job = parse_job({"id": "b", "content": "^XA^FDhola^FS^XZ"})
    assert job.format == "zpl" and job.content.startswith(b"^XA")
    job = parse_job({"id": "c", "content_base64": "data:application/pdf;base64," + base64.b64encode(b"%PDF").decode()})
    assert job.content == b"%PDF"
    job = parse_job({"id": "d", "order": {"name": "#1001"}})
    assert job.format == "order" and job.title == "#1001"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"url": "https://x.com/a.pdf"}, "id"),
        ({"id": "x"}, "exactamente uno"),
        ({"id": "x", "url": "https://a", "content": "b"}, "exactamente uno"),
        ({"id": "x", "url": "ftp://a/b"}, "http"),
        ({"id": "x", "url": "https://a/b", "copies": 0}, "copies"),
        ({"id": "x", "url": "https://a/b", "format": "docx"}, "Formato"),
        ({"id": "x", "content_base64": "@@@"}, "base64"),
    ],
)
def test_parse_job_rejects_invalid(payload, message):
    with pytest.raises(JobError, match=message):
        parse_job(payload)


def test_parse_jobs_payload_shapes():
    assert len(parse_jobs_payload({"jobs": [{"id": "1", "content": "x"}, {"id": "2", "content": "y"}]}, require_id=True)) == 2
    assert len(parse_jobs_payload([{"id": "1", "content": "x"}], require_id=True)) == 1
    generated = parse_jobs_payload({"content": "x"}, require_id=False)[0]
    assert generated.external_id


def test_store_dedupes_and_retries(core):
    store = core.store
    req = parse_job({"id": "pedido-1", "content": "^XA^XZ"})
    job, created = store.add(req, "mqtt")
    again, created_again = store.add(req, "mqtt")
    assert created and not created_again and again["id"] == job["id"]
    assert job["ack_pending"] == 1  # el canal en tiempo real confirma a n8n

    store.set_printing(job["id"])
    store.set_retry(job["id"], "sin papel", delay=60)
    assert store.next_ready() is None  # espera el reintento
    assert store.next_ready(time.time() + 61)["id"] == job["id"]

    store.set_printing(job["id"])
    failed = store.set_failed(job["id"], "error final")
    assert failed["status"] == "failed" and failed["attempts"] == 2 and failed["ack_pending"] == 1

    store.mark_acked(job["id"], "failed", failed["updated_at"])
    assert store.pending_acks("mqtt") == []

    retried = store.retry(job["id"])
    assert retried["status"] == "queued" and retried["attempts"] == 0

    copy = store.reprint(job["id"])
    assert copy["source"] == "manual" and copy["id"] != job["id"]
    assert open(copy["payload_path"], "rb").read() == b"^XA^XZ"


def test_store_recovers_printing_jobs_after_crash(core):
    from labelbridge import paths
    from labelbridge.jobstore import JobStore

    job, _ = core.store.add(parse_job({"id": "x", "content": "^XA^XZ"}), "api")
    core.store.set_printing(job["id"])
    reopened = JobStore(paths.db_path(), paths.spool_dir())
    assert reopened.get(job["id"])["status"] == "queued"
    reopened.close()
