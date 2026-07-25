# Cola durable y coordinación distribuida

## Alcance implementado

El webhook puede usar Redis Streams mediante `INBOUND_QUEUE_BACKEND=redis`.
La implementación ofrece:

- persistencia de mensajes entre reinicios;
- consumer group compartido por todas las réplicas;
- deduplicación distribuida por `wa_message_id`;
- lease renovable por conversación, usando una clave derivada y no el teléfono;
- ACK solamente después del flush completo del agente;
- reintentos exponenciales y Dead Letter Stream;
- recuperación de entregas abandonadas con `XAUTOCLAIM`;
- métricas y auditoría de aceptación, reintentos, recuperación y DLQ.

El estado funcional del checkout continúa persistido en el CRM. Redis aporta la
exclusión por conversación que impide que dos réplicas hagan simultáneamente el
ciclo cargar → modificar → guardar. No se crea una segunda copia permanente de
los datos personales del checkout. El trabajo sí contiene temporalmente el
teléfono, nombre y texto necesarios para procesarlo; se omite el webhook `raw`.
Redis debe tener acceso privado, autenticación, cifrado en tránsito cuando
corresponda y una política explícita de retención/borrado para la DLQ y backups.

## Activación en producción

Crear un servicio Redis persistente y configurar en todas las réplicas:

```dotenv
INBOUND_QUEUE_BACKEND=redis
CRM_MODE=external
REDIS_URL=redis://usuario:password@redis:6379/0
REDIS_STREAM_KEY=donregalo:inbound
REDIS_CONSUMER_GROUP=donregalo-agents
REDIS_DLQ_STREAM=donregalo:inbound:dlq
INBOUND_QUEUE_WORKERS=2
INBOUND_MAX_RETRIES=3
```

Usar la URL privada del proveedor. Si el tráfico sale de la red privada, usar
`rediss://` y exigir TLS. Redis debe tener persistencia AOF habilitada y política
de memoria que no expulse arbitrariamente las claves de la cola.

La aplicación falla al arrancar si se selecciona Redis sin `REDIS_URL` o si no
puede conectarse. No existe fallback silencioso a memoria porque rompería el
orden y la deduplicación entre réplicas.

## Semántica y operación

La entrega es **al menos una vez**. La deduplicación evita que una redelivery
normal de Meta vuelva a ejecutar el mensaje durante 24 horas. Si un proceso cae,
otro consumidor reclama su entrada pendiente después de `REDIS_CLAIM_IDLE_MS`.

Después de `INBOUND_MAX_RETRIES`, el trabajo se mueve a
`donregalo:inbound:dlq`. La DLQ no debe borrarse automáticamente: debe generar
una alerta y revisarse antes de reinyectar un mensaje.

Valores que deben vigilarse desde `/health`, `/metrics` y Redis:

- longitud del stream y pendientes del consumer group;
- `inbound.worker:retry` y `inbound.worker:dead_letter`;
- `inbound.recovery:claimed`;
- contención de `inbound.conversation_lock:busy`;
- antigüedad del mensaje pendiente más antiguo.

## Despliegue gradual

1. Desplegar Redis con AOF y respaldo.
2. Desplegar una réplica del agente con backend Redis.
3. Verificar webhook, respuesta, ACK, deduplicación y DLQ.
4. Reiniciar el agente con un trabajo pendiente y confirmar la recuperación.
5. Aumentar a dos réplicas y probar varios mensajes sobre la misma conversación.
6. Crear alertas operacionales antes de aumentar más réplicas.

Para desarrollo sin Redis se mantiene `INBOUND_QUEUE_BACKEND=local`.
