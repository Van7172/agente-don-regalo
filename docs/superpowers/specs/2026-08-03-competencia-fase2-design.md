# Fase 2 — Competencia (diseño aprobado)

**Fecha:** 2026-08-03  
**Estado:** aprobado (Opción A: scrape `httpx` propio)

## Competidores

1. https://www.rosatel.pe/ (VTEX)
2. https://magia.pe/ (Shopify)
3. https://www.sorprendelima.pe/ (Shopify)

## Arquitectura

- Crawl + matching en el agente FastAPI (tick del watchdog, cooldown largo).
- CRM PHP: tablas, upsert autenticado, panel de lectura.
- Solo datos públicos: nombre, precio, URL, promo tachada si aparece.
- Matching: embedding del nombre → Qdrant; bajo umbral = hueco candidato.
- Sin cifras inventadas (ventas, márgenes, stock ajeno).

## Fuentes de crawl

| Dominio | Fuente | Nota |
|---|---|---|
| magia.pe | `/products.json` | robots Allow catálogo |
| sorprendelima.pe | `/products.json` | robots Allow catálogo |
| rosatel.pe | VTEX `catalog_system/pub/products/search` en host comercial | No usar `www.rosatel.pe/api` (Disallow) |

## Fuera de alcance (este corte)

- Fase 3 (cruce con `crm_demanda_no_cubierta`)
- BrightData / Semrush
- CAC / Marketing API
