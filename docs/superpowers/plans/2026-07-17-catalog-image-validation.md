# Catalog Image Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Omitir productos cuya URL no contiene una imagen real y rellenar el listado con candidatos válidos sin mostrar errores al cliente.

**Architecture:** Un verificador asíncrono compartido valida status, MIME y bytes con Pillow. Las tools solicitan un pool mayor, filtran antes de truncar y devuelven solo productos válidos; el renderer sigue siendo determinista.

**Tech Stack:** Python 3.11, httpx, Pillow, pytest/pytest-asyncio.

---

### Task 1: Verificador de imágenes

**Files:**
- Create: `app/tools/image_validation.py`
- Test: `tests/test_image_validation.py`

- [ ] **Step 1: Write the failing tests**

Cubrir una imagen PNG/JPEG válida, HTTP 200 con HTML, MIME no gráfico, bytes
corruptos, timeout y filtrado que continúa hasta alcanzar el límite. Inyectar un
`httpx.MockTransport` para que la suite no use red.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_image_validation.py -q`

Expected: FAIL porque `app.tools.image_validation` aún no existe.

- [ ] **Step 3: Implement the minimal verifier**

Crear:

```python
async def is_valid_image(client: httpx.AsyncClient, product: dict) -> bool: ...

async def valid_products(
    client: httpx.AsyncClient,
    products: list[dict],
    *,
    limit: int,
) -> list[dict]: ...
```

Usar `GET`, `raise_for_status`, MIME `image/*`, límite de bytes y
`PIL.Image.verify()`. Registrar con `log.warning` ID y URL al rechazar; nunca
devolver el detalle técnico al consumidor.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_image_validation.py -q`

Expected: PASS.

### Task 2: Integrar validación y reposición en catálogo/Qdrant

**Files:**
- Modify: `app/tools/catalog.py`
- Modify: `app/tools/search.py`
- Test: `tests/test_image_validation.py`

- [ ] **Step 1: Add failing integration tests**

Probar que una respuesta con candidatos `[válido, HTML, válido]` devuelve los dos
válidos en orden y que la búsqueda solicita candidatos adicionales antes de
truncar al límite visible.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_image_validation.py -q`

Expected: FAIL porque las tools aún dejan pasar el candidato HTML.

- [ ] **Step 3: Integrate candidate pools**

Solicitar un pool mayor que `DEFAULT_PER_PAGE`, normalizar con adapters, llamar
`valid_products(..., limit=DEFAULT_PER_PAGE)` y solo entonces devolver el
payload. En Qdrant, validar el pool ya filtrado por producto activo antes del
slice final.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `python -m pytest tests/test_image_validation.py tests/test_render_productos.py -q`

Expected: PASS.

### Task 3: Sincronizar el espejo

**Files:**
- Create: `sandbox/app/tools/image_validation.py`
- Modify: `sandbox/app/tools/catalog.py`
- Modify: `sandbox/app/tools/search.py`
- Create: `sandbox/tests/test_image_validation.py`

- [ ] Reemplazar `sandbox/app`, `sandbox/tests` y `sandbox/evals` por los árboles
de raíz, según `CLAUDE.md`.
- [ ] Ejecutar comparación recursiva excluyendo `__pycache__`.
- [ ] Ejecutar `python -m pytest tests/ -q` y `python -m evals.runner`.
