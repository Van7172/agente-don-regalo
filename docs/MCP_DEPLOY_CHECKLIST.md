# Despliegue MCP Don Regalo

## Responsabilidad

El despliegue en cPanel, el smoke test contra producción y la activación en
EasyPanel los realiza el propietario de la infraestructura. No es necesario
compartir credenciales ni el token MCP.

## Paquete PHP

Archivo preparado:

```text
artifacts/donregalo-mcp-php-2026-07-24.zip
```

```text
Tamaño: 28355 bytes
SHA-256: DFC030063AB368A8EFABC26F98395977138AAD73A85A633D3815726C89B13A02
```

Los archivos desplegables mantienen compatibilidad con PHP 7.3 o superior.

Subir conservando la ruta relativa dentro de `public_html/`:

```text
clienteApiApp/
  handlers/
    metodos_pago.php
    pedidos.php
  src/
    Mcp/
      Auth.php
      Config.php
      Server.php
      Tools.php
    Service/
      PagoService.php
      PedidoService.php
      ProductoService.php
```

No requiere SQL. Antes de sobrescribir, conservar una copia recuperable de esos archivos.

## Orden

1. Subir el paquete PHP.
2. Confirmar que `config/secrets.php` conserva `mcp_token` y `mcp_base_url`.
3. Desde la raíz de `agente-don-regalo`, ejecutar el smoke sin escribir el
   token en el comando.

   PowerShell:

   ```powershell
   $tokenSeguro = Read-Host "Token MCP" -AsSecureString
   $env:DONREGALO_MCP_TOKEN = [System.Net.NetworkCredential]::new("", $tokenSeguro).Password
   python scripts/smoke_mcp.py
   Remove-Item Env:DONREGALO_MCP_TOKEN
   Remove-Variable tokenSeguro
   ```

   Bash:

   ```bash
   read -s -p "Token MCP: " DONREGALO_MCP_TOKEN
   export DONREGALO_MCP_TOKEN
   python scripts/smoke_mcp.py
   unset DONREGALO_MCP_TOKEN
   ```

4. Para cubrir también el rastreo:

   ```powershell
   $tokenSeguro = Read-Host "Token MCP" -AsSecureString
   $env:DONREGALO_MCP_TOKEN = [System.Net.NetworkCredential]::new("", $tokenSeguro).Password
   python scripts/smoke_mcp.py --tracking-email cliente@example.com --tracking-code CODIGO
   Remove-Item Env:DONREGALO_MCP_TOKEN
   Remove-Variable tokenSeguro
   ```

5. Solo si el smoke queda verde, configurar en EasyPanel:

   ```env
   DONREGALO_MCP_TOKEN=...
   DONREGALO_USE_MCP=1
   ```

6. Redeploy del agente y comprobar `/health`: debe mostrar
   `donregalo_mcp_enabled: true` y `donregalo_mcp_configured: true`.

## Resultado esperado

- El smoke termina con `OK: lifecycle, 9 tools, catálogo, detalle, cobertura, ofertas, pagos e imagen`.
- Si no se proporcionan datos reales de rastreo, muestra un aviso; ese control
  continúa pendiente.
- `/health` confirma que MCP está habilitado y configurado, sin exponer el token.
- Ante cualquier fallo, mantener o volver a `DONREGALO_USE_MCP=0`.

## Confirmación

Cuando los tres pasos terminen correctamente, marcar en
`docs/PLAN_MCP_DONREGALO.md`:

- despliegue PHP al hosting;
- smoke test en producción;
- activación opt-in en EasyPanel.

## Rollback

1. Poner `DONREGALO_USE_MCP=0` y redeploy del agente.
2. Si el endpoint PHP también falla, restaurar la copia previa de los archivos del paquete.
