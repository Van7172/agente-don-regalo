# CRM Sales History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Registrar ventas cerradas, permitir marcarlas entregadas y retirar su ficha activa sin perder auditoría.

**Architecture:** MySQL conserva registros normalizados en `crm_ventas_historiales`; `sale_*` sigue representando la ficha activa. El API del CRM archiva anuncios y confirma entregas de forma idempotente. Una página PHP de servidor consulta el historial.

**Tech Stack:** PHP 7.4+, PDO/MySQL, JavaScript sin framework, CSS.

---

### Task 1: Migración de historial

**Files:**
- Create: `crm-php/sql/004_sales_history.sql`

- [ ] Crear `crm_ventas_historiales` con PK independiente, FKs, índices por
tenant/estado/fecha y UNIQUE `(id_tenant, id_conversation,
cerrada_en_venta_historial)`.
- [ ] Añadir `INSERT IGNORE ... SELECT` desde claves `sale_%`, extrayendo el
snapshot JSON y dejando estado `pendiente`.
- [ ] Verificar sintaxis MySQL y documentar ejecución en
`crm-php/docs/DEPLOY.md`.

### Task 2: Repositorio y API

**Files:**
- Modify: `crm-php/src/Repository.php`
- Modify: `crm-php/public/api/index.php`
- Test: `crm-php/tests/sales_history_contract.php`

- [ ] **Step 1: Add failing contract tests**

El test estático verificará rutas, parámetros enlazados, aislamiento por tenant,
idempotencia y orden “archivar antes de borrar setting”.

- [ ] **Step 2: Verify RED**

Run: `php crm-php/tests/sales_history_contract.php`

Expected: FAIL por métodos/rutas ausentes.

- [ ] **Step 3: Implement repository methods**

Añadir métodos:

```php
Repository::archiveSale(int $conversationId, array $sale): array
Repository::markSaleDelivered(int $conversationId, int $userId): array
Repository::listSalesHistory(?string $from, ?string $to, string $status, string $query): array
```

Usar transacción PDO en la confirmación; tomar el snapshot activo, hacer upsert,
marcar entregado y borrar `sale_*` después.

- [ ] **Step 4: Implement API routes**

- `PATCH /conversations/{id}/sale/delivered`: sesión obligatoria.
- Interceptar `PUT /settings` para que una clave `sale_<id>` válida archive el
snapshot además de conservar el setting.

- [ ] **Step 5: Verify GREEN**

Run: `php crm-php/tests/sales_history_contract.php`

Expected: PASS.

### Task 3: Interacción del inbox

**Files:**
- Modify: `crm-php/public/assets/inbox.js`
- Modify: `crm-php/public/assets/app.css`
- Test: `crm-php/tests/sales_history_contract.php`

- [ ] Añadir el test que exige botón, confirmación, endpoint y tratamiento de
éxito/error; verificar RED.
- [ ] Añadir **Marcar como entregado** dentro de la ficha.
- [ ] Usar `window.confirm` con el texto aprobado.
- [ ] Al confirmar, hacer PATCH; limpiar `lastThread.conv.sale`, repintar ficha y
lista. Si falla, mantener la ficha y mostrar error.
- [ ] Verificar GREEN y ejecutar `node --check crm-php/public/assets/inbox.js`.

### Task 4: Módulo Historial de ventas

**Files:**
- Create: `crm-php/public/sales-history.php`
- Create: `crm-php/views/sales-history.php`
- Modify: `crm-php/views/layout.php`
- Modify: `crm-php/public/assets/app.css`
- Test: `crm-php/tests/sales_history_contract.php`

- [ ] Añadir pruebas contractuales de autenticación, filtros, escape y enlace de
navegación; verificar RED.
- [ ] Crear controlador que valida `from`, `to`, `status`, `q` y llama al
repositorio.
- [ ] Crear vista con filtros y tabla de estado, cliente, WhatsApp, producto,
distrito, entrega, pedido temporal, cierre y confirmación.
- [ ] Añadir enlace **Historial de ventas** y estilos responsive.
- [ ] Verificar GREEN y ejecutar `php -l` sobre todos los PHP modificados.

### Task 5: Verificación y despliegue

- [ ] Ejecutar el test contractual PHP y `node --check`.
- [ ] Ejecutar `php -l` para Repository, API, controlador y vista.
- [ ] Documentar que `004_sales_history.sql` se ejecuta antes de publicar la UI.
- [ ] Verificar manualmente el flujo en navegador si hay un CRM local y BD
disponibles; si no, informar esa limitación sin afirmar una prueba E2E.
