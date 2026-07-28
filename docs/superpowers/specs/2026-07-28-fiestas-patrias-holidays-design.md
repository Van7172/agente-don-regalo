# Fiestas Patrias 28–29 julio 2026

## Contexto

El 28 y 29 de julio 2026 no hay personal humano. El bot sigue atendiendo y
puede programar pedidos **a partir del 30/07/2026**.

## Comportamiento

1. **Checkout:** no acepta entrega en `2026-07-28` ni `2026-07-29`. Repregunta
   orientando al 30/07.
2. **Handoff:** no cede el chat (no HUMAN, no cola AYUDA). Avisa el feriado y
   ofrece seguir con el bot / pedidos desde el 30.
3. **FACTS:** nota temporal en delivery.

## Archivos

- `app/harness/holidays.py` — fechas y textos
- `app/harness/checkout.py` — bloqueo de fecha
- `app/services/agent.py` — `perform_handoff` no cede en feriado
- `app/prompts/facts.py`
- tests + espejo `sandbox/`
