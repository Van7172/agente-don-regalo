import sync_qdrant


def test_documento_canonico_limpia_html_y_no_embebe_datos_operativos():
    product = {
        "nombre": "Desayuno Amor",
        "categoria": "Desayunos",
        "ocasiones": ["Aniversario", "Cumpleaños"],
        "descripcion_corta": "Sorprende <br> con cariño",
        "descripcion": "<p>Incluye taza y chocolates.</p>",
        "tags": "romántico, pareja",
        "precio": 99.90,
        "stock": 4,
    }

    text = sync_qdrant.build_embedding_text(product)

    assert "Producto: Desayuno Amor" in text
    assert "Ocasiones: Aniversario, Cumpleaños" in text
    assert "<br>" not in text
    assert "<p>" not in text
    assert "99.90" not in text
    assert "stock" not in text.lower()


def test_precio_y_stock_no_cambian_el_hash_semantico():
    base = {
        "nombre": "Ramo",
        "categoria": "Flores",
        "descripcion_corta": "Rosas para celebrar",
        "precio": 20,
        "stock": 1,
    }
    changed = {**base, "precio": 30, "stock": 99}

    assert sync_qdrant.content_hash(
        sync_qdrant.build_embedding_text(base)
    ) == sync_qdrant.content_hash(sync_qdrant.build_embedding_text(changed))


def test_cambio_de_descripcion_fuerza_embedding_nuevo(monkeypatch):
    monkeypatch.setattr(sync_qdrant, "EMBED_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(sync_qdrant, "EMBED_DIM", 1536)
    existing = {
        "content_hash": "anterior",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
        "index_schema_version": sync_qdrant.INDEX_SCHEMA_VERSION,
    }

    assert sync_qdrant.needs_embedding(existing, "nuevo")


def test_mismo_contenido_modelo_y_esquema_reutiliza_vector(monkeypatch):
    monkeypatch.setattr(sync_qdrant, "EMBED_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(sync_qdrant, "EMBED_DIM", 1536)
    existing = {
        "content_hash": "igual",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
        "index_schema_version": sync_qdrant.INDEX_SCHEMA_VERSION,
    }

    assert not sync_qdrant.needs_embedding(existing, "igual")


def test_export_url_categoria_alimenta_slug_qdrant():
    product = {
        "id_producto": 7,
        "nombre": "Desayuno",
        "categoria": "Desayunos criollos",
        "url_categoria": "desayunos-criollos",
    }

    payload = sync_qdrant.build_payload(product, "abc")

    assert payload["categoria_slug"] == "desayunos"
    assert payload["content_hash"] == "abc"
