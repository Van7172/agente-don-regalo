"""
Tests de lógica pura del agente (sin red ni dependencias externas en runtime).

Ejecutar:
    pip install -r requirements.txt -r requirements-dev.txt
    pytest
"""
from app.services.knowledge import _scrub_pii
from app.services.messenger import split_reply
from app.tools.search import _exclude_ids, _build_filter


class TestScrubPII:
    """El filtro de PII redacta datos de clientes, conserva los del negocio."""

    def test_redacta_telefono_de_cliente(self):
        out = _scrub_pii("su numero es 987 654 321")
        assert "987" not in out
        assert "[dato omitido]" in out

    def test_redacta_email_y_dni_de_cliente(self):
        out = _scrub_pii("correo cliente@gmail.com DNI 45678912")
        assert "cliente@gmail.com" not in out
        assert "45678912" not in out

    def test_conserva_contactos_del_negocio(self):
        out = _scrub_pii("WhatsApp 977174485 o Yape 943113807")
        assert "977174485" in out
        assert "943113807" in out

    def test_conserva_email_del_negocio(self):
        assert "ventas@donregalo.pe" in _scrub_pii("escribe a ventas@donregalo.pe")

    def test_no_toca_precios_ni_horas(self):
        txt = "cuesta S/149.60 ($44.00), entrega 07:00 AM"
        assert _scrub_pii(txt) == txt


class TestSplitReply:
    """La respuesta se parte en segmentos de imagen y texto."""

    def test_separa_imagen_y_texto(self):
        reply = "https://x.com/a.jpg\n• Producto A\n\nhttps://x.com/b.png\n• Producto B"
        segs = split_reply(reply)
        assert segs[0]["type"] == "image"
        assert segs[0]["url"] == "https://x.com/a.jpg"
        assert "Producto A" in segs[0]["caption"]

    def test_solo_texto_un_segmento(self):
        segs = split_reply("Hola, ¿en qué te ayudo?")
        assert len(segs) == 1
        assert segs[0]["type"] == "text"


class TestExcludeIds:
    """Saneo de los ids a excluir en 'más opciones'."""

    def test_sanea_a_enteros(self):
        assert _exclude_ids({"excluir_ids": [1, "2", "x", None, 3]}) == [1, 2, 3]

    def test_vacio(self):
        assert _exclude_ids({}) == []


class TestBuildFilter:
    """El filtro de Qdrant excluye productos ya mostrados."""

    def test_excluir_ids_genera_must_not(self):
        f = _build_filter({"excluir_ids": [10, 20], "incluir_funebre": True})
        assert f is not None
        assert f.must_not  # tiene condiciones de exclusión

    def test_sin_condiciones_es_none(self):
        assert _build_filter({"incluir_funebre": True}) is None
