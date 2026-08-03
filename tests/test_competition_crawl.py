"""El crawl entero, no solo sus piezas.

El incidente (03-08). La pestaña Competencia llevaba días en cero con TODO bien
configurado: migración `016` aplicada, `COMPETITION_CRAWL_ENABLED=1` vivo en el
proceso, watchdog encendido, CRM externo, los tres sitios respondiendo y su
robots.txt permitiendo el path. Y aun así, cero productos.

La causa era una línea:

    record_operation("competition.crawl", "ok", tags={"slug": slug})

`record_operation` no acepta etiquetas — las series se distinguen por el NOMBRE
(`tool.buscar_productos`). Eso levantaba un `TypeError` en cada vuelta del
bucle. Lo grave no es el error, es **dónde estaba la segunda copia**: la misma
llamada se repetía dentro del `except` que debía anotar el fallo, así que el
manejador reventaba también y la excepción salía disparada de `run_crawl`. La
red de seguridad por competidor era justo lo que convertía un fallo recuperable
en fatal: 500 en el trigger manual, y en el tick del watchdog un `log.warning`
que nadie mira.

Los tests que había cubrían `competition_adapters` y `competition_match` —
parseo de precios, robots, matching— y ninguno llamaba a `run_crawl`. Por eso
una función que fallaba SIEMPRE, en su primera línea de métrica, pasó CI y llegó
a producción. Esto cierra ese hueco: lo que se prueba aquí es el pegamento.
"""
from __future__ import annotations

import pytest

from app.services import competition_crawl
from app.services.competition_adapters import ScrapedProduct


def _producto(n: int) -> ScrapedProduct:
    return ScrapedProduct(
        clave_externa=f"ext-{n}",
        nombre=f"Ramo {n}",
        url=f"https://ejemplo.pe/p/{n}",
        precio_sol=99.0 + n,
    )


@pytest.fixture
def crawl_listo(monkeypatch):
    """Todo configurado y el CRM aceptando. Solo cambian los adaptadores."""
    monkeypatch.setattr(
        competition_crawl.settings, "competition_crawl_enabled", True, raising=False
    )
    monkeypatch.setattr(
        competition_crawl.settings, "competition_request_delay_seconds", 0.0, raising=False
    )
    monkeypatch.setattr(competition_crawl.crm_http, "crm_enabled", lambda: True)

    guardados: list[tuple[str, int]] = []

    async def fake_upsert(slug, products, *, crawl_started, mark_missing_inactive=False):
        guardados.append((slug, len(products)))
        return len(products)

    monkeypatch.setattr(
        competition_crawl.crm_http, "upsert_competition_products", fake_upsert
    )

    # `_marcar_hecho` escribe el cooldown en el CRM. Se traga sus propios
    # errores, así que sin stub no rompe el test — solo lo hace tardar mientras
    # httpx agota el timeout contra una URL de verdad. La suite no sale a la red.
    async def fake_put_setting(_key, _value):
        return None

    monkeypatch.setattr(competition_crawl.crm_http, "put_setting", fake_put_setting)

    # El matching pide Qdrant y OpenAI; aquí solo interesa el pegamento del
    # crawl. SIN `raising=False`: si alguien renombra `match_product`, este test
    # tiene que romperse en vez de dejar pasar la llamada real — que es
    # exactamente el despiste que tuve al escribirlo.
    async def sin_match(_nombre, **_kw):
        return competition_match_result()

    monkeypatch.setattr(
        competition_crawl.competition_match, "match_product", sin_match
    )
    return guardados


def competition_match_result():
    """Un `MatchResult` neutro: no hay hueco, no hay vecino."""
    from app.services.competition_match import MatchResult

    return MatchResult(None, None, None, False)


def _adaptadores(monkeypatch, mapping):
    monkeypatch.setattr(competition_crawl, "ADAPTERS", mapping, raising=False)


@pytest.mark.asyncio
async def test_un_crawl_normal_devuelve_resumen(crawl_listo, monkeypatch):
    async def adapter(_fetch, limit):
        return [_producto(1), _producto(2)]

    _adaptadores(monkeypatch, {"magia": adapter})

    summary = await competition_crawl.run_crawl(force=True)

    assert summary["competidores"]["magia"]["scraped"] == 2
    assert summary["errores"] == []


@pytest.mark.asyncio
async def test_un_adaptador_roto_no_tumba_el_crawl(crawl_listo, monkeypatch):
    """El corazón del bug.

    `run_crawl` captura el fallo POR competidor y lo mete en `errores`. Si el
    propio manejador levanta —como hacía— la excepción sale de la función y se
    lleva por delante a los competidores que aún no habían corrido.
    """

    async def roto(_fetch, _limit):
        raise RuntimeError("sitio caído")

    _adaptadores(monkeypatch, {"rosatel": roto})

    summary = await competition_crawl.run_crawl(force=True)

    assert summary["errores"], "el fallo tiene que quedar registrado, no propagarse"
    assert summary["errores"][0]["slug"] == "rosatel"
    assert "sitio caído" in summary["errores"][0]["error"]


@pytest.mark.asyncio
async def test_el_que_falla_no_se_lleva_a_los_demas(crawl_listo, monkeypatch):
    """Con la excepción escapando, un solo sitio caído dejaba la pantalla vacía."""

    async def roto(_fetch, _limit):
        raise RuntimeError("boom")

    async def sano(_fetch, _limit):
        return [_producto(7)]

    # El roto va primero: si propagase, `magia` no llegaría a correr.
    _adaptadores(monkeypatch, {"rosatel": roto, "magia": sano})

    summary = await competition_crawl.run_crawl(force=True)

    assert summary["competidores"]["magia"]["scraped"] == 1
    assert [e["slug"] for e in summary["errores"]] == ["rosatel"]


@pytest.mark.asyncio
async def test_las_metricas_no_llevan_etiquetas(crawl_listo, monkeypatch):
    """`record_operation` distingue series por el nombre, no por `tags=`.

    Se comprueba la llamada real, no la firma: pasarle un kwarg que no existe
    es exactamente el error que se escapó, y una aserción sobre el nombre lo
    habría cazado igual.
    """
    vistas: list[tuple] = []

    def espia(operation, outcome="ok", **kwargs):
        assert not kwargs, f"record_operation no acepta {sorted(kwargs)}"
        vistas.append((operation, outcome))

    monkeypatch.setattr(competition_crawl, "record_operation", espia)

    async def adapter(_fetch, _limit):
        return [_producto(1)]

    _adaptadores(monkeypatch, {"magia": adapter})

    await competition_crawl.run_crawl(force=True)

    assert ("competition.crawl.magia", "ok") in vistas


@pytest.mark.asyncio
async def test_sin_crm_no_se_intenta(monkeypatch):
    """Sin CRM externo no hay dónde guardar: se sale antes de tocar ningún sitio."""
    monkeypatch.setattr(
        competition_crawl.settings, "competition_crawl_enabled", True, raising=False
    )
    monkeypatch.setattr(competition_crawl.crm_http, "crm_enabled", lambda: False)

    summary = await competition_crawl.run_crawl(force=True)

    assert summary == {"skipped": True, "reason": "crm_disabled"}


@pytest.mark.asyncio
async def test_maybe_run_crawl_no_propaga_al_watchdog(crawl_listo, monkeypatch):
    """El tick llama aquí. Lo que salga de esta función acaba en un log y nada más.

    Por eso el fallo era invisible: `check_competition` lo tragaba entero.
    """

    async def roto(_fetch, _limit):
        raise RuntimeError("boom")

    _adaptadores(monkeypatch, {"rosatel": roto})
    monkeypatch.setattr(competition_crawl, "_en_cooldown", lambda: _falso())

    async def _falso():
        return False

    summary = await competition_crawl.maybe_run_crawl()

    assert summary is not None
    assert summary["errores"]
