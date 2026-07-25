"""
Sincroniza el catálogo de donregalo.pe a Qdrant (base vectorial).

Flujo:
  1. Crea la colección en Qdrant si no existe.
  2. Pagina el endpoint /api/productos/export.
  3. Embebe cada producto (nombre + categoría + descripción + ocasiones + tags).
  4. Upsert de los puntos con su payload (para filtros y para devolver al agente).

Uso:
  python sync_qdrant.py

Programar (ej. nightly) con cron / scheduler de EasyPanel.
"""
import os
import sys
import httpx
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from app.services.product_embedding_index import (
    INDEX_SCHEMA_VERSION,
    build_embedding_text,
    build_payload as _build_payload,
    content_hash,
    needs_embedding as _needs_embedding,
)

load_dotenv()

API_BASE        = os.getenv("DONREGALO_API_BASE", "https://donregalo.pe/clienteApiApp/api")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
EMBED_MODEL     = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM       = int(os.getenv("EMBED_DIM", "1536"))

QDRANT_URL      = os.getenv("QDRANT_URL", "").rstrip("/")
QDRANT_API_KEY  = os.getenv("QDRANT_API_KEY", "")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "productos")

EXPORT_PER_PAGE = 100   # tamaño de página del endpoint export
EMBED_BATCH     = 64    # cuántos textos embeber por llamada a OpenAI
def build_payload(p: dict, semantic_hash: str) -> dict:
    return _build_payload(
        p,
        semantic_hash,
        model=EMBED_MODEL,
        dimensions=EMBED_DIM,
    )


def needs_embedding(existing_payload: dict | None, semantic_hash: str) -> bool:
    return _needs_embedding(
        existing_payload,
        semantic_hash,
        model=EMBED_MODEL,
        dimensions=EMBED_DIM,
    )


def fetch_all_products() -> list[dict]:
    """Trae todo el catálogo activo paginando el endpoint export."""
    productos: list[dict] = []
    page = 1
    with httpx.Client(timeout=30.0) as client:
        while True:
            r = client.get(
                f"{API_BASE}/productos/export",
                params={"page": page, "per_page": EXPORT_PER_PAGE},
            )
            r.raise_for_status()
            body = r.json()
            data = body.get("data", [])
            productos.extend(data)

            pag = body.get("pagination", {})
            last = pag.get("last_page", page)
            print(f"  página {page}/{last} — {len(data)} productos")
            if page >= last or not data:
                break
            page += 1
    return productos


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embebe una lista de textos con OpenAI (en lotes)."""
    vectors: list[list[float]] = []
    with httpx.Client(timeout=60.0) as client:
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i:i + EMBED_BATCH]
            r = client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": EMBED_MODEL, "input": batch},
            )
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            vectors.extend(d["embedding"] for d in data)
            print(f"  embebidos {min(i + EMBED_BATCH, len(texts))}/{len(texts)}")
    return vectors


def ensure_collection(qc: QdrantClient) -> None:
    """Crea la colección si no existe."""
    existing = [c.name for c in qc.get_collections().collections]
    if QDRANT_COLLECTION not in existing:
        print(f"Creando colección '{QDRANT_COLLECTION}'...")
        qc.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
    else:
        print(f"Colección '{QDRANT_COLLECTION}' ya existe.")


def fetch_qdrant_payloads(qc: QdrantClient) -> dict[int, dict]:
    """Carga solo metadatos para decidir altas, cambios y eliminaciones."""
    existing: dict[int, dict] = {}
    offset = None
    while True:
        records, offset = qc.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records:
            existing[int(record.id)] = dict(record.payload or {})
        if offset is None:
            break
    return existing


_SLUG_PARENT: dict[str, str] = {
    # Desayunos
    "desayunos-criollos":        "desayunos",
    "desayunos-de-amor":         "desayunos",
    "desayunos-light":           "desayunos",
    "desayunos-tematicos":       "desayunos",
    # Arreglos florales
    "arreglos-florales-variados":       "arreglos-florales",
    "en-canasta":                       "arreglos-florales",
    "arreglos-florales-con-peluche":    "arreglos-florales",
    "cajas":                            "arreglos-florales",
    "corporativos":                     "arreglos-florales",
    "ramos-de-flores":                  "arreglos-florales",
    "floreros":                         "arreglos-florales",
    "arreglos-florales-de-navidad":     "arreglos-florales",
    # Arreglos fúnebres
    "cruces-funebres":      "arreglos-funebres",
    "lagrimas-funebres":    "arreglos-funebres",
    "coronas-para-difuntos":"arreglos-funebres",
    "mantos-funebres":      "arreglos-funebres",
    # Plantas
    "terrarios":    "plantas",
    "orquideas":    "plantas",
    "suculentas":   "plantas",
    # Otros ya son padres o no tienen sub
    "regalos-corporativos": "cestas",
}


def _parent_slug(slug: str) -> str:
    """Normaliza el slug de subcategoría al slug padre para filtros consistentes."""
    return _SLUG_PARENT.get(slug, slug)


def main() -> int:
    if not OPENAI_API_KEY or not QDRANT_URL:
        print("ERROR: faltan OPENAI_API_KEY o QDRANT_URL en el entorno.")
        return 1

    from urllib.parse import urlparse
    _parsed = urlparse(QDRANT_URL)
    qc = QdrantClient(
        host=_parsed.hostname,
        port=_parsed.port or (443 if _parsed.scheme == "https" else 80),
        https=(_parsed.scheme == "https"),
        api_key=QDRANT_API_KEY or None,
        prefer_grpc=False,
        timeout=30,
        check_compatibility=False,
    )
    ensure_collection(qc)

    print("Descargando catálogo...")
    productos = fetch_all_products()
    print(f"Total productos: {len(productos)}")
    if not productos:
        print("No hay productos para indexar.")
        return 0

    print("Leyendo estado actual de Qdrant...")
    existing = fetch_qdrant_payloads(qc)

    prepared = []
    for product in productos:
        text = build_embedding_text(product)
        semantic_hash = content_hash(text)
        payload = build_payload(product, semantic_hash)
        prepared.append((product, text, payload))

    changed = [
        item for item in prepared
        if needs_embedding(existing.get(int(item[0]["id_producto"])), item[2]["content_hash"])
    ]
    payload_only = [
        item for item in prepared
        if not needs_embedding(
            existing.get(int(item[0]["id_producto"])),
            item[2]["content_hash"],
        )
        and existing[int(item[0]["id_producto"])].get("payload_hash")
        != item[2]["payload_hash"]
    ]

    print(
        "Cambios detectados: "
        f"{len(changed)} vector(es), {len(payload_only)} payload(s), "
        f"{len(prepared) - len(changed) - len(payload_only)} sin cambios."
    )

    points = []
    if changed:
        print("Generando embeddings solo para contenido nuevo o modificado...")
        vectors = embed_texts([text for _, text, _ in changed])
        for (product, _text, payload), vector in zip(changed, vectors):
            points.append(PointStruct(
                id=int(product["id_producto"]),
                vector=vector,
                payload=payload,
            ))

    # Upsert en lotes
    for i in range(0, len(points), 100):
        qc.upsert(collection_name=QDRANT_COLLECTION, points=points[i:i + 100])
        print(f"  upsert {min(i + 100, len(points))}/{len(points)}")

    # Los cambios operativos no requieren pagar otro embedding. Se reemplaza el
    # payload completo para que no sobrevivan claves de esquemas anteriores.
    for index, (product, _text, payload) in enumerate(payload_only, start=1):
        qc.overwrite_payload(
            collection_name=QDRANT_COLLECTION,
            payload=payload,
            points=[int(product["id_producto"])],
        )
        if index % 100 == 0 or index == len(payload_only):
            print(f"  payload {index}/{len(payload_only)}")

    # Limpieza: eliminar de Qdrant los productos que ya no existen en la API
    print("Verificando productos obsoletos en Qdrant...")
    api_ids: set[int] = {p["id_producto"] for p in productos}
    stale_ids = set(existing) - api_ids
    if stale_ids:
        from qdrant_client.models import PointIdsList
        print(f"Eliminando {len(stale_ids)} productos obsoletos: {sorted(stale_ids)}")
        qc.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=PointIdsList(points=list(stale_ids)),
        )
    else:
        print("Sin productos obsoletos.")

    print(
        f"[OK] Listo. {len(points)} vector(es) y {len(payload_only)} payload(s) "
        f"actualizados en '{QDRANT_COLLECTION}'."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
