"""Endurecimiento del webhook (C), versionado de prompts (B6), fallback de
proveedor (B5) y el juez de calidad (B3).

Lo que une a los cuatro: ninguno cambia lo que ve el cliente cuando todo va
bien. Se notan justo cuando algo falla — un webhook falsificado, un proveedor
caído, un prompt que alguien reescribió, una respuesta que cumple todas las
invariantes y aun así no resuelve nada.
"""
from __future__ import annotations

import httpx
import pytest

from app.channels.whatsapp import webhook as webhook_mod
from app.config import settings
from app.harness.registry import spec_for
from app.observability import metrics_snapshot, reset_observability
from app.prompts.compose import prompt_version
from app.services import agent as agent_mod


@pytest.fixture(autouse=True)
def entorno_limpio():
    webhook_mod.reset_rate_limit()
    reset_observability()
    yield
    webhook_mod.reset_rate_limit()
    reset_observability()


# ── C · firma del webhook ───────────────────────────────────────────────────


def test_sin_secreto_se_acepta_por_defecto(monkeypatch):
    """Encender el rechazo por defecto dejaría sin WhatsApp a quien no lo tenga.

    Es una decisión consciente, no un descuido: un apagón total del canal de
    ventas es peor que el riesgo, y el operador lo cierra en un minuto.
    """
    monkeypatch.setattr(settings, "whatsapp_app_secret", "", raising=False)
    monkeypatch.setattr(settings, "whatsapp_require_signature", False, raising=False)
    assert webhook_mod._valid_signature(b"{}", None) is True


def test_sin_secreto_pero_exigiendo_firma_se_rechaza_todo(monkeypatch):
    """`WHATSAPP_REQUIRE_SIGNATURE=1` convierte "acepto todo" en "no acepto nada".

    Es el único efecto de la bandera, y solo aplica sin secreto: con secreto la
    firma ya se verifica siempre.
    """
    monkeypatch.setattr(settings, "whatsapp_app_secret", "", raising=False)
    monkeypatch.setattr(settings, "whatsapp_require_signature", True, raising=False)
    assert webhook_mod._valid_signature(b"{}", None) is False


def test_con_secreto_una_firma_invalida_se_rechaza(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cr3t", raising=False)
    assert webhook_mod._valid_signature(b"{}", "sha256=falsa") is False
    assert webhook_mod._valid_signature(b"{}", None) is False


def test_con_secreto_la_firma_correcta_pasa(monkeypatch):
    import hashlib
    import hmac

    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cr3t", raising=False)
    cuerpo = b'{"hola":1}'
    firma = hmac.new(b"s3cr3t", cuerpo, hashlib.sha256).hexdigest()
    assert webhook_mod._valid_signature(cuerpo, f"sha256={firma}") is True


# ── C · rate limit ──────────────────────────────────────────────────────────


def test_el_rate_limit_corta_al_superar_el_techo(monkeypatch):
    monkeypatch.setattr(settings, "webhook_rate_limit_per_minute", 3, raising=False)
    assert [webhook_mod._rate_limited() for _ in range(3)] == [False] * 3
    assert webhook_mod._rate_limited() is True


def test_el_rate_limit_se_puede_desactivar(monkeypatch):
    monkeypatch.setattr(settings, "webhook_rate_limit_per_minute", 0, raising=False)
    assert all(webhook_mod._rate_limited() is False for _ in range(50))


def test_la_ventana_del_rate_limit_se_desliza(monkeypatch):
    """Un pico de hace un minuto no puede seguir bloqueando ahora."""
    reloj = {"t": 1000.0}
    monkeypatch.setattr(webhook_mod.time, "monotonic", lambda: reloj["t"])
    monkeypatch.setattr(settings, "webhook_rate_limit_per_minute", 2, raising=False)

    assert webhook_mod._rate_limited() is False
    assert webhook_mod._rate_limited() is False
    assert webhook_mod._rate_limited() is True

    reloj["t"] += 61.0
    assert webhook_mod._rate_limited() is False


# ── B6 · versionado de prompts ──────────────────────────────────────────────


def test_cada_agente_tiene_su_huella():
    catalogo = prompt_version(spec_for("catalog_search"))
    concierge = prompt_version(spec_for("greet"))
    assert catalogo and concierge
    assert catalogo != concierge


def test_la_huella_es_estable_entre_llamadas():
    """Si cambiara sola no serviría para agrupar nada."""
    spec = spec_for("catalog_search")
    assert prompt_version(spec) == prompt_version(spec)


def test_la_huella_cambia_si_cambia_el_playbook():
    """Es lo único que tiene que hacer: delatar que alguien tocó el texto."""
    import dataclasses

    spec = spec_for("catalog_search")
    retocado = dataclasses.replace(spec, playbook=spec.playbook + "\nUna línea nueva.")
    assert prompt_version(retocado) != prompt_version(spec)


def test_la_huella_no_depende_de_la_hora_ni_del_estado():
    """Con la hora dentro sería distinta en cada turno: inútil para comparar."""
    from datetime import datetime

    from app.harness.state import ConversationState
    from app.prompts.compose import build_system

    spec = spec_for("catalog_search")
    huella = prompt_version(spec)
    build_system(spec, ConversationState(district="Surco"), now=datetime(2026, 1, 1))
    assert prompt_version(spec) == huella


def test_la_traza_lleva_la_huella():
    from app.harness.trace import Trace

    trace = Trace(agent="catalog", prompt_version="abc123")
    assert trace.to_dict()["prompt_version"] == "abc123"


# ── B5 · fallback de proveedor ──────────────────────────────────────────────


def _respuesta_ok(texto: str = "hola") -> dict:
    return {
        "choices": [{"message": {"content": texto}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.fixture
def respaldo(monkeypatch):
    monkeypatch.setattr(settings, "llm_fallback_base_url", "https://respaldo/v1", raising=False)
    monkeypatch.setattr(settings, "llm_fallback_api_key", "clave-respaldo", raising=False)
    monkeypatch.setattr(settings, "llm_fallback_model", "modelo-respaldo", raising=False)


@pytest.mark.asyncio
async def test_sin_respaldo_configurado_el_error_sube(monkeypatch):
    """Nada cambia para quien no lo configure."""
    monkeypatch.setattr(settings, "llm_fallback_base_url", "", raising=False)
    monkeypatch.setattr(settings, "llm_fallback_api_key", "", raising=False)

    async def revienta(*_a, **_k):
        raise httpx.ConnectError("openai caído")

    monkeypatch.setattr(agent_mod, "_chat_completion_unprotected", revienta)
    with pytest.raises(httpx.ConnectError):
        await agent_mod._chat_completion(httpx.AsyncClient(), {"model": "gpt", "messages": []})


@pytest.mark.asyncio
async def test_el_respaldo_responde_cuando_el_primario_cae(respaldo, monkeypatch):
    llamadas: list[dict] = []

    async def enrutado(_client, payload, *, url=agent_mod._OPENAI_URL, api_key="", label="specialist"):
        llamadas.append({"url": url, "model": payload.get("model"), "label": label})
        if label == "specialist":
            raise httpx.ConnectError("openai caído")
        return _respuesta_ok("desde el respaldo")

    monkeypatch.setattr(agent_mod, "_chat_completion_unprotected", enrutado)
    data = await agent_mod._chat_completion(
        httpx.AsyncClient(), {"model": "gpt-4o-mini", "messages": []}
    )

    assert data["choices"][0]["message"]["content"] == "desde el respaldo"
    assert llamadas[1]["url"] == "https://respaldo/v1/chat/completions"
    assert llamadas[1]["model"] == "modelo-respaldo", "debe usar el modelo del respaldo"
    # Queda constancia de que se usó: si no, un proveedor caído durante horas
    # sería invisible mientras el respaldo aguanta.
    assert "openai.provider:fallback" in metrics_snapshot()["operation_series"]


@pytest.mark.asyncio
async def test_si_el_respaldo_tambien_cae_gana_el_error_del_primario(respaldo, monkeypatch):
    """El error que se propaga tiene que ser el del proveedor de verdad.

    Enseñar el fallo del respaldo mandaría a depurar el proveedor equivocado.
    """
    async def todo_mal(_client, payload, *, url=agent_mod._OPENAI_URL, api_key="", label="specialist"):
        if label == "specialist":
            raise httpx.ConnectError("openai caído")
        raise httpx.ReadTimeout("respaldo caído")

    monkeypatch.setattr(agent_mod, "_chat_completion_unprotected", todo_mal)
    with pytest.raises(httpx.ConnectError):
        await agent_mod._chat_completion(httpx.AsyncClient(), {"model": "gpt", "messages": []})


@pytest.mark.asyncio
async def test_el_primario_sano_no_toca_el_respaldo(respaldo, monkeypatch):
    llamadas: list[str] = []

    async def solo_primario(_client, payload, *, url=agent_mod._OPENAI_URL, api_key="", label="specialist"):
        llamadas.append(label)
        return _respuesta_ok()

    monkeypatch.setattr(agent_mod, "_chat_completion_unprotected", solo_primario)
    await agent_mod._chat_completion(httpx.AsyncClient(), {"model": "gpt", "messages": []})
    assert llamadas == ["specialist"]


def test_el_respaldo_exige_url_y_clave(monkeypatch):
    """Media configuración es peor que ninguna: fallaría en el peor momento."""
    monkeypatch.setattr(settings, "llm_fallback_base_url", "https://x/v1", raising=False)
    monkeypatch.setattr(settings, "llm_fallback_api_key", "", raising=False)
    assert agent_mod._fallback_configured() is False


# ── B3 · juez de calidad ────────────────────────────────────────────────────


def test_el_corpus_de_calidad_existe_y_tiene_casos():
    from evals.judge import load_cases

    casos = load_cases()
    assert len(casos) >= 5
    for caso in casos:
        assert caso.get("id"), "cada caso necesita id"
        assert caso.get("respuesta"), "hay que evaluar ALGO"


def test_el_veredicto_se_parsea_y_puntua():
    from evals.judge import parse_verdict

    v = parse_verdict("x", '{"resolvio":2,"tono":2,"sin_inventar":2,"motivo":"bien"}')
    assert v.score == 1.0
    assert not v.error


def test_una_nota_fuera_de_rango_se_acota():
    from evals.judge import parse_verdict

    v = parse_verdict("x", '{"resolvio":9,"tono":-3,"sin_inventar":1}')
    assert v.resolvio == 2 and v.tono == 0


@pytest.mark.parametrize("basura", ["no soy json", "[1,2,3]", "", "null"])
def test_un_juez_que_devuelve_basura_no_tumba_la_evaluacion(basura):
    from evals.judge import parse_verdict

    v = parse_verdict("x", basura)
    assert v.error, "debe anotarse como error, no reventar"
    assert v.score == 0.0


def test_la_media_ignora_los_casos_que_fallaron():
    """Un error del juez no es un cero de calidad: es una medición que no hubo."""
    from evals.judge import JudgeReport, Verdict

    report = JudgeReport(
        verdicts=[
            Verdict("a", resolvio=2, tono=2, sin_inventar=2),
            Verdict("b", error="Timeout"),
        ]
    )
    assert report.average == 1.0
    assert len(report.scored) == 1


def test_el_juez_no_entra_en_el_gate_determinista():
    """Llama a OpenAI: en CI pondría el gate rojo por un mal minuto del proveedor."""
    from evals.runner import run_all

    assert all(not r.kind.startswith("judge") for r in run_all())
