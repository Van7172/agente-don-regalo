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


def build_embedding_text(p: dict) -> str:
    """Texto que se convierte en vector. Incluye el contexto semántico clave:
    nombre, categoría, ocasiones y descripción — para que la búsqueda capte la
    intención (ej: distinguir 'rosas blancas' de nacimiento vs fúnebre)."""
    partes = [
        p.get("nombre", ""),
        f"Categoría: {p.get('categoria', '')}",
    ]
    ocasiones = p.get("ocasiones") or []
    if ocasiones:
        partes.append("Ocasiones: " + ", ".join(ocasiones))
    if p.get("descripcion_corta"):
        partes.append(p["descripcion_corta"])
    elif p.get("descripcion"):
        partes.append(p["descripcion"])
    if p.get("tags"):
        partes.append("Tags: " + p["tags"])
    return "\n".join(x for x in partes if x and x.strip())


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

    print("Generando embeddings...")
    texts   = [build_embedding_text(p) for p in productos]
    vectors = embed_texts(texts)

    print("Subiendo a Qdrant...")
    points = []
    for p, vec in zip(productos, vectors):
        points.append(PointStruct(
            id=p["id_producto"],
            vector=vec,
            payload={
                "id_producto":   p["id_producto"],
                "nombre":        p.get("nombre", ""),
                "precio":        p.get("precio", 0),
                "categoria":     p.get("categoria", ""),
                "categoria_slug": _parent_slug(p.get("categoria_slug", "")),
                "ocasiones_ids": p.get("ocasiones_ids", []),
                "es_funebre":    bool(p.get("es_funebre", False)),
                "stock":         p.get("stock", 0),
                "descripcion_corta": p.get("descripcion_corta", ""),
                "imagen_url":    p.get("imagen_url"),
                "url":           p.get("url", ""),
            },
        ))

    # Upsert en lotes
    for i in range(0, len(points), 100):
        qc.upsert(collection_name=QDRANT_COLLECTION, points=points[i:i + 100])
        print(f"  upsert {min(i + 100, len(points))}/{len(points)}")

    print(f"[OK] Listo. {len(points)} productos indexados en '{QDRANT_COLLECTION}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
