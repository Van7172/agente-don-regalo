from types import SimpleNamespace

import pytest

from app.services import product_embedding_worker as worker
from app.services.product_embedding_index import (
    build_embedding_text,
    build_payload,
    content_hash,
)


class FakeQdrant:
    def __init__(self, record=None):
        self.record = record
        self.upserts = []
        self.payloads = []
        self.deletes = []

    def retrieve(self, **_kwargs):
        return [self.record] if self.record else []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def overwrite_payload(self, **kwargs):
        self.payloads.append(kwargs)

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


@pytest.mark.asyncio
async def test_worker_reutiliza_vector_si_el_contenido_no_cambio(monkeypatch):
    product = {
        "id_producto": 58,
        "nombre": "Osito",
        "descripcion": "Peluche para regalar",
        "precio_final": 65,
        "stock": 10,
        "categoria": {"id": 4, "nombre": "Peluches", "url": "peluches"},
        "ocasiones": [],
    }
    monkeypatch.setattr(worker.settings, "embed_model", "text-embedding-3-small")
    monkeypatch.setattr(worker.settings, "embed_dim", 3)
    text = build_embedding_text(product)
    semantic_hash = content_hash(text)
    payload = build_payload(
        product,
        semantic_hash,
        model=worker.settings.embed_model,
        dimensions=3,
    )
    qdrant = FakeQdrant(
        SimpleNamespace(payload=payload, vector=[0.1, 0.2, 0.3])
    )
    completed = []

    async def fake_product(_product_id):
        return product

    async def fake_finish(job_id, **kwargs):
        completed.append((job_id, kwargs))

    async def should_not_embed(_texts):
        raise AssertionError("no debe llamar OpenAI")

    monkeypatch.setattr(worker, "_catalog_product", fake_product)
    monkeypatch.setattr(worker, "get_qdrant", lambda: qdrant)
    monkeypatch.setattr(worker.crm_http, "finish_embedding_job", fake_finish)
    monkeypatch.setattr(worker, "embed", should_not_embed)

    await worker.process_embedding_job({"id_job": 9, "id_producto": 58})

    assert not qdrant.upserts
    assert completed[0][1]["status"] == "done"
    assert completed[0][1]["dimensions"] == 3
    assert completed[0][1]["embedding_base64"]


@pytest.mark.asyncio
async def test_worker_elimina_qdrant_si_producto_ya_no_es_publicable(monkeypatch):
    qdrant = FakeQdrant()
    completed = []

    async def missing(_product_id):
        return None

    async def fake_finish(job_id, **kwargs):
        completed.append((job_id, kwargs))

    monkeypatch.setattr(worker, "_catalog_product", missing)
    monkeypatch.setattr(worker, "get_qdrant", lambda: qdrant)
    monkeypatch.setattr(worker.crm_http, "finish_embedding_job", fake_finish)

    await worker.process_embedding_job({"id_job": 10, "id_producto": 99})

    assert len(qdrant.deletes) == 1
    assert completed == [(10, {"status": "deleted"})]


@pytest.mark.asyncio
async def test_worker_genera_y_persiste_vector_para_producto_nuevo(monkeypatch):
    product = {
        "id_producto": 77,
        "nombre": "Ramo celebración",
        "descripcion": "Flores alegres",
        "precio": 40,
        "stock": 2,
        "categoria": {"id": 2, "nombre": "Flores", "url": "ramos-de-flores"},
    }
    qdrant = FakeQdrant()
    completed = []
    monkeypatch.setattr(worker.settings, "embed_model", "text-embedding-3-small")
    monkeypatch.setattr(worker.settings, "embed_dim", 3)

    async def fake_product(_product_id):
        return product

    async def fake_embed(_texts):
        return [[0.4, 0.5, 0.6]]

    async def fake_finish(job_id, **kwargs):
        completed.append((job_id, kwargs))

    monkeypatch.setattr(worker, "_catalog_product", fake_product)
    monkeypatch.setattr(worker, "embed", fake_embed)
    monkeypatch.setattr(worker, "get_qdrant", lambda: qdrant)
    monkeypatch.setattr(worker.crm_http, "finish_embedding_job", fake_finish)

    await worker.process_embedding_job({"id_job": 11, "id_producto": 77})

    assert len(qdrant.upserts) == 1
    assert completed[0][1]["status"] == "done"
    assert completed[0][1]["dimensions"] == 3
