"""Contrato arquitectónico del módulo independiente de guardrails."""

from pathlib import Path

from app.guardrails import (
    check_reply,
    guard_reply,
    handoff_policy,
    sanitize_reply,
)
from app.harness.contracts import Product
from app.harness.invariants import check_reply as legacy_check_reply
from app.harness.master import _degrade_unsafe_reply
from app.harness.policies import handoff_policy as legacy_handoff_policy


ROOT = Path(__file__).resolve().parents[1]


def test_wrappers_legacy_delegan_a_la_fuente_unica():
    assert legacy_check_reply is check_reply
    assert legacy_handoff_policy is handoff_policy
    assert _degrade_unsafe_reply is sanitize_reply


def test_guard_reply_bloquea_precio_inventado_y_conserva_producto_real():
    product = Product(
        id_producto=58,
        nombre="Terrario Familia Panditas",
        precio_sol=149.60,
        precio_usd=44.0,
        imagen_url="https://donregalo.pe/panditas.webp",
    )

    result = guard_reply("Te cuesta S/10.00", artifacts=[product])

    assert result.blocked is True
    assert any(item.rule == "prices_are_sourced" for item in result.violations)
    assert "S/10" not in result.reply
    assert "149.60" in result.reply


def test_runtime_importa_guardrails_y_no_los_wrappers_legacy():
    production_files = [
        ROOT / "app" / "services" / "agent.py",
        ROOT / "app" / "harness" / "master.py",
        ROOT / "app" / "harness" / "router.py",
        ROOT / "app" / "harness" / "checkout.py",
    ]
    for path in production_files:
        source = path.read_text(encoding="utf-8")
        assert "app.harness.policies" not in source
        assert "app.harness.invariants" not in source
        assert "app.guardrails" in source
