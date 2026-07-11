# Opción C — CRM MySQL + Agente Implementation Plan

> **For agentic workers:** Implement task-by-task. Checkboxes track progress.

**Goal:** Separar `crm/` (Next.js + MySQL `donregalo_bd`) del `sandbox/` (agente Cloud API + tools + watchdog).

**Architecture:** CRM expone HTTP sobre tablas `crm_*` en MySQL. Sandbox deja de usar SQLite embebido como fuente de verdad del inbox y consume esas APIs. Watchdog corre en sandbox y alerta vía Cloud API.

**Tech Stack:** Next.js 15+, mysql2, TypeScript, FastAPI (sandbox existente), MySQL XAMPP `donregalo_bd` (root / sin password).

---

## Bloque 1 — CRM + schema MySQL

- [x] SQL `crm/sql/001_crm_schema.sql` con tablas `crm_*`
- [x] Aplicar schema en `donregalo_bd` (MySQL puerto **3307**)
- [x] Scaffold `crm/` Next.js (sin Baileys/Airtable/Supabase)
- [x] APIs: conversations, messages, mode, leads, memory, settings, outbox, watchdog/unanswered, health
- [x] Panel inbox mínimo (`http://127.0.0.1:3100`)

## Bloque 2 — Sandbox + watchdog

- [x] Cliente HTTP `CRM_BASE_URL` en sandbox (`app/crm/http_client.py`)
- [x] Buffer dual `CRM_MODE=local|external` + handoff/memoria vía CRM
- [x] Watchdog Python (mute, balance OpenRouter, fallback spike, daily audit)
- [x] Endpoint interno `/internal/outbox/send`
- [x] E2E con Meta Cloud API real + auth panel CRM
- [x] Auth panel CRM contra tabla `usuarios` (+ cookie JWT)
- [ ] E2E Meta Cloud API real
- [ ] Docs README raíz + checklist APIs faltantes del cliente
