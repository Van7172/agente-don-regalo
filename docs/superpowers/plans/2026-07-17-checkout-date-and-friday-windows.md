# Checkout Date and Friday Windows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guardar fechas concretas en Lima y generar opciones de horario correctas para viernes.

**Architecture:** `orders.normalize_fecha` será la única normalización; el FSM la usa al capturar la fecha. `delivery_windows` expondrá opciones estructuradas por fecha y el prompt recibirá un bloque temporal Lima independiente del LLM.

**Tech Stack:** Python 3.11, `datetime`, `zoneinfo`, pytest.

---

### Task 1: Fecha canónica en el FSM

**Files:**
- Modify: `app/harness/orders.py`
- Modify: `app/harness/checkout.py`
- Modify: `tests/test_pedido_temporal.py`

- [ ] **Step 1: Add failing FSM tests**

Con fecha controlada `2026-07-17`, probar que “mañana” guarda `2026-07-18`,
una fecha pasada o ilegible repregunta sin avanzar y el resumen presenta
`18/07/26`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_pedido_temporal.py -q`

Expected: FAIL porque el FSM guarda el texto literal.

- [ ] **Step 3: Implement normalization at capture time**

Permitir que `advance_checkout` reciba un `today` opcional para pruebas, llamar
`normalize_fecha`, rechazar fechas anteriores y añadir:

```python
def display_fecha(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%y")
```

Usar el formato visible en resumen y ficha, manteniendo ISO en estado/API.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_pedido_temporal.py -q`

Expected: PASS.

### Task 2: Opciones dinámicas de viernes

**Files:**
- Modify: `app/delivery_windows.py`
- Modify: `app/harness/checkout.py`
- Modify: `tests/test_pedido_temporal.py`

- [ ] **Step 1: Add failing schedule tests**

Probar `schedule_options_for(date(2026, 7, 17))` sin franja temprana y numerada
1–4; probar sábado con 1–5; comprobar que `parse_schedule("1", delivery_date)`
elige 09:00–11:00 el viernes y 07:00–09:00 otro día.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_pedido_temporal.py -q`

Expected: FAIL porque las opciones y mapas son globales.

- [ ] **Step 3: Implement one structured source**

Definir una estructura de franjas con `label`, `display` y `api_hour`.
Implementar:

```python
def windows_for(delivery_date: date | str) -> tuple[DeliveryWindow, ...]: ...
def schedule_options_for(delivery_date: date | str) -> str: ...
def parse_schedule_choice(text: str, delivery_date: date | str) -> str | None: ...
```

Excluir la primera franja cuando `weekday() == 4` y mapear siempre a las horas
permitidas por `API.md`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_pedido_temporal.py -q`

Expected: PASS.

### Task 3: Contexto actual de Lima

**Files:**
- Modify: `app/prompts/compose.py`
- Modify: `tests/test_prompts_architecture.py`

- [ ] **Step 1: Add failing prompt test**

Inyectar un `datetime` controlado y comprobar que todos los agentes
`customer_facing` contienen `America/Lima`, día, fecha y hora; el orquestador no.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_prompts_architecture.py -q`

Expected: FAIL por ausencia del bloque temporal.

- [ ] **Step 3: Add deterministic temporal block**

Crear `render_current_time(now=None)` usando `ZoneInfo("America/Lima")` y
añadirlo desde `build_system` solo para `customer_facing`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_prompts_architecture.py -q`

Expected: PASS.

### Task 4: Sincronizar y verificar

- [ ] Sincronizar raíz → `sandbox/app`, `sandbox/tests`, `sandbox/evals`.
- [ ] Confirmar espejo con comparación recursiva.
- [ ] Ejecutar `python -m pytest tests/ -q`.
- [ ] Ejecutar `python -m evals.runner`.
