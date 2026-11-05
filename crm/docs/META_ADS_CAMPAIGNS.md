# Meta Ads en Campañas

El panel une dos fuentes por el **ID del anuncio**: el referral que llegó al chat
(`crm_conversations.ad_source_id`) y la lectura de Meta Ads. No une por nombre,
porque los nombres pueden repetirse o cambiar.

## Puesta en marcha

1. Aplicar `crm/sql/020_meta_ads_snapshot.sql` en la misma base del CRM antes de
   publicar el PHP nuevo.
2. Configurar `meta_ads_account_id` en `crm/config.php`. Para Don Regalo - Ads:
   `1149788199387012`.
3. Publicar `crm/src/MetaAdsSnapshot.php`, `crm/scripts/import_meta_ads.php`,
   `crm/public/campaigns.php`, `crm/views/campaigns.php`, `crm/public/assets/campaigns.js`,
   `crm/public/assets/app.css`, `crm/public/api/index.php`, `crm/src/Repository.php`
   y `crm/bootstrap.php`.
4. Importar una lectura completa del conector con una de las dos vías:

   - CLI en el servidor: `php crm/scripts/import_meta_ads.php lectura.json`.
   - API servidor a servidor: `POST /crm/public/api/campaigns/meta-snapshot`, JSON
     y cabecera `X-CRM-Token` con el token interno del CRM.

La lectura debe tener `account_id`, `from`, `to`, `complete: true` y `rows` con
filas de `ads_get_ad_entities` a nivel **ad**, `time_increment: "1"` y campos
`id`, `name`, `campaign_id`, `campaign_name`, `adset_id`, `adset_name`,
`amount_spent`, `impressions`, `clicks`. Si hay paginación, reunir **todas** las
páginas antes de marcar `complete: true`. Se pueden omitir días completamente
en cero para reducir el archivo; conviene conservar una fila reciente por anuncio
para poder mostrar su nombre y campaña. El importe debe venir en PEN.

La importación reemplaza atómicamente la lectura anterior de esa cuenta. La
interfaz muestra cuándo se importó y qué fechas cubre. El archivo local
`tmp/meta_ads_snapshot.json` contiene una lectura verificada del 7 de agosto
al 4 de noviembre de 2026 y está excluido de Git; hay que copiarlo expresamente
al servidor si se quiere usar como primera carga.

## Alcance de la conexión

El MCP está autorizado en este chat, pero su sesión OAuth no se comparte con
PHP. Por eso el CRM lee una copia guardada, nunca el MCP desde el navegador ni
el token de Meta. Para actualización automática sin esta conversación hace falta
una credencial de servicio de Meta con acceso de lectura a la cuenta o un proceso
externo autorizado que envíe las lecturas al endpoint. El panel no afirma que
una lectura guardada sea tiempo real.

Meta agrupa gasto por fecha de impresión. El CRM agrupa compradores por fecha
de llegada del chat y cuenta personas con al menos una venta; el monto solo suma
ventas con importe conocido. Las dos fuentes comparten anuncio, pero no la misma
regla temporal. Por eso el panel no calcula ROAS ni costo por adquisición como
si las dos series fueran directamente equivalentes.
