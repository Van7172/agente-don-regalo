-- La cita de una imagen se veía como "[image]".
--
-- El asesor le manda al lead una foto (un ramo de girasoles con galletas), el
-- lead responde CITÁNDOLA — "podría optar por esta opción?" — y en el panel la
-- cita salía como el literal `[image]`. O sea que justo cuando el cliente señala
-- un producto concreto, el asesor es el único que no puede ver cuál: tiene que
-- subir por el hilo a adivinar de qué foto hablaba, y si mandó varias seguidas
-- ya no hay forma de saberlo.
--
-- El motivo: al citar solo se guardaba TEXTO (`quoted_text`), y el texto de un
-- mensaje de imagen es el marcador `[image]` que pone el agente cuando no hay
-- caption. La foto estaba en `media_url`, pero de la cita no se copiaba.
--
-- Se guarda junto al texto, y no como referencia al mensaje original, por lo
-- mismo que `quoted_text`: la cita es una foto del pasado. Si el mensaje
-- original se borra, la cita debe seguir mostrando lo que el cliente vio.

ALTER TABLE crm_messages
  ADD COLUMN quoted_media_url VARCHAR(1024) NULL
    COMMENT 'media_url del mensaje citado, para que la cita muestre la miniatura';
