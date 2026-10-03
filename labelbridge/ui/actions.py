"""Acciones compartidas entre pantallas y bandeja."""

from __future__ import annotations

from datetime import datetime

from ..labels.order import sample_order
from ..models import JobRequest
from .context import AppContext


def enqueue_test_label(ctx: AppContext, printer: str = "") -> dict:
    """Pone en cola la etiqueta de ejemplo (recorre el mismo camino que un pedido real)."""
    req = JobRequest(
        external_id=f"prueba-{datetime.now():%Y%m%d%H%M%S%f}",
        title="Etiqueta de prueba",
        printer=printer,
        format="order",
        order=sample_order(),
    )
    job, _ = ctx.store.add(req, "test")
    ctx.events.emit("job", job)
    ctx.engine.wake()
    return job
