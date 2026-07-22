-- De qué anuncio viene el lead.
--
-- Varios clientes abren con "¡Hola! Quiero más información." y el asesor no sabe
-- por qué: no lo escribieron ellos, es el *mensaje predefinido* de un anuncio de
-- Click-to-WhatsApp. En Ads Manager hay una campaña (DESAYUNOS | VENTAS | MC)
-- con siete anuncios —PORTADA FAMILIA, PORTADA ELLA, PORTADA EL…— y **todos
-- comparten el mismo texto predefinido**, así que por el mensaje es imposible
-- distinguirlos: el asesor abre el chat a ciegas y pregunta lo que el anuncio ya
-- había respondido.
--
-- Meta sí lo dice: adjunta un objeto `referral` al PRIMER mensaje de esa
-- conversación (titular del anuncio, cuerpo, enlace y su id). Llegaba al webhook
-- y se tiraba entero: el agente en modo `external` ni siquiera mandaba `raw`, y
-- el endpoint de ingesta tampoco lo guardaba. O sea que `crm_messages.raw_message`
-- está en NULL y ese dato no se puede recuperar hacia atrás — solo desde el
-- despliegue en adelante.
--
-- Va en la conversación y no en el mensaje porque es la pregunta que se hace el
-- asesor al ABRIR el chat, y porque así se puede contar leads por anuncio sin
-- rebuscar dentro de un JSON.

ALTER TABLE crm_conversations
  ADD COLUMN ad_source_type VARCHAR(32)   NULL COMMENT 'referral.source_type: ad | post',
  ADD COLUMN ad_source_id   VARCHAR(64)   NULL COMMENT 'referral.source_id: id del anuncio en Meta',
  ADD COLUMN ad_headline    VARCHAR(512)  NULL COMMENT 'referral.headline: titular del anuncio',
  ADD COLUMN ad_body        TEXT          NULL COMMENT 'referral.body: cuerpo del anuncio',
  ADD COLUMN ad_source_url  VARCHAR(1024) NULL COMMENT 'referral.source_url: enlace al anuncio',
  ADD COLUMN ad_ctwa_clid   VARCHAR(255)  NULL COMMENT 'referral.ctwa_clid: click id, para atribución',
  ADD COLUMN ad_captured_at DATETIME      NULL COMMENT 'cuándo se capturó (el referral solo llega una vez)';

-- Para "cuántos leads trajo cada anuncio" sin escanear la tabla entera.
ALTER TABLE crm_conversations
  ADD KEY idx_crm_conv_ad (ad_source_id);
