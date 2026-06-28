# Agente IA para WhatsApp — Documento de Producto y Modelo de Negocio

> Documento de referencia para posicionar, vender y escalar el servicio de Agente IA para
> tiendas y negocios que venden por WhatsApp. Redactado para toma de decisiones comerciales
> y técnicas.

---

## 1. ¿Qué es el Agente IA para WhatsApp?

Es un asistente de ventas con inteligencia artificial que atiende a tus clientes por WhatsApp
**las 24 horas, los 7 días de la semana**, como si fuera un vendedor real de tu tienda.

No es un chatbot de menús ni botones. Es un agente que **lee**, **entiende** y **responde**
en lenguaje natural, exactamente como lo haría un humano capacitado — pero sin descansar,
sin olvidar y sin cometer errores de precio.

### ¿Qué lo hace diferente a un chatbot tradicional?

| Chatbot tradicional | Agente IA |
|---|---|
| Menús de opciones (1, 2, 3…) | Entiende mensajes en lenguaje libre |
| Respuestas pregrabadas | Genera respuestas contextuales únicas |
| No conoce el catálogo real | Conectado en tiempo real a tu base de productos |
| El cliente tiene que "adivinar" cómo preguntar | El cliente habla normal y el agente entiende |
| No aprende | Aprende de tus vendedores humanos |

---

## 2. Funciones que cumple el agente

### 2.1 Atención y ventas

| Función | Descripción |
|---|---|
| **Saludo y derivación** | Recibe al cliente, identifica su necesidad y lo guía sin hacer preguntas innecesarias |
| **Búsqueda semántica de productos** | Entiende intenciones ("algo romántico para mi novia", "un detalle para felicitar a mi jefe") y devuelve los productos más relevantes del catálogo real |
| **Filtros por ocasión y presupuesto** | Filtra automáticamente por Cumpleaños, Aniversario, Nacimiento, etc., y respeta el presupuesto del cliente si lo menciona |
| **Productos similares** | Si al cliente le gustó un producto pero quiere ver alternativas, el agente las encuentra instantáneamente |
| **Mostrar imágenes con descripciones** | Envía la foto del producto junto con su precio en ambas monedas y una descripción breve |
| **Información de delivery** | Verifica cobertura por distrito y tarifa de envío en tiempo real |
| **Métodos de pago** | Informa sobre todos los métodos disponibles (Yape, tarjeta, transferencia, etc.) |
| **Rastreo de pedidos** | Consulta el estado del pedido del cliente con su email y código |
| **Conversión de moneda** | Muestra precios en USD y Soles usando el tipo de cambio actual |

### 2.2 Personalización y memoria

| Función | Descripción |
|---|---|
| **Memoria de corto plazo** | Recuerda todo lo que el cliente dijo en la conversación actual (hasta 30 mensajes, 24 horas) |
| **Memoria de largo plazo** | Guarda el nombre, distrito habitual y preferencias del cliente para recordarlos en futuras conversaciones |
| **Búsquedas personalizadas** | Si sabe que al cliente le gustan los girasoles, prioriza esos productos en la siguiente búsqueda |
| **Tono adaptado** | Detecta el contexto (celebración, condolencias, negocios) y ajusta el tono de la respuesta |

### 2.3 Comportamiento inteligente (diferenciales clave)

| Función | Descripción |
|---|---|
| **Búsqueda híbrida** | Combina comprensión semántica (IA) + coincidencia de términos clave para resultados más precisos. Ej: "rosas BLANCAS" devuelve rosas blancas, no rojas |
| **Campañas temporales como catálogo curado** | Fechas como Día del Padre, Día de la Madre, Navidad o San Valentín se resuelven desde la categoría curada del sitio, no desde búsqueda semántica libre |
| **Honestidad ante stock limitado** | Si no tiene exactamente lo que el cliente pide, lo dice claramente y ofrece la alternativa más cercana |
| **Lógica de contexto fúnebre** | Muestra arreglos de condolencias SOLO cuando el cliente lo pide explícitamente; los excluye en todos los demás casos automáticamente |
| **Agrupación de mensajes (debounce 6s)** | Si el cliente envía varios mensajes seguidos, los agrupa y procesa juntos — evita respuestas parciales o duplicadas |
| **Mensajes "ya voy"** | Mientras busca productos (1-3 seg de latencia), envía un mensaje cálido para que el cliente sepa que está procesando |
| **Indicador de escritura** | Simula que escribe, con delay proporcional al largo del mensaje — se siente humano |
| **Aprendizaje de vendedores humanos (Nivel B)** | Cuando un vendedor humano resuelve un caso, el agente extrae el conocimiento y lo indexa. La próxima vez que llegue la misma pregunta, responde como lo hizo el vendedor |
| **Detección de mensajes citados** | Cuando el cliente cita un producto en WhatsApp, el agente detecta el nombre del producto citado y distingue si el cliente pide más info o quiere comprarlo — sin volver a buscar |
| **Flujo de cierre secuencial** | Al confirmar compra, recoge datos en este orden exacto: (1) distrito → verifica cobertura + tarifa, (2) fecha, (3) horario (5 opciones enumeradas), (4) mensaje de tarjeta, (5) resumen, (6) deriva a pago. Una pregunta por turno |
| **Búsqueda secuencial sin duplicados** | `buscar_semantico` y `catalogo_categoria` se llaman siempre en secuencia, nunca en paralelo — evita que el mismo producto aparezca dos veces |
| **Normalización de slugs en catálogo** | Los productos de subcategorías (ej: `desayunos-tematicos`) se almacenan bajo el slug padre (`desayunos`) para que los filtros del agente sean consistentes |
| **Escalación a un asesor humano** | Si el cliente pide hablar con una persona o se frustra, el agente lo deriva: envía un mensaje de espera, etiqueta la conversación y se aparta para que el equipo intervenga. Mientras tanto no interrumpe, y puede avisar al equipo por Slack/webhook |
| **Nunca repite opciones** | Cuando el cliente pide "más opciones", excluye lo ya mostrado y trae productos realmente nuevos, en vez de repetir los mismos |
| **Productos siempre vigentes** | Verifica en tiempo real contra el catálogo que cada producto sugerido siga activo, aunque el índice de búsqueda se haya desactualizado entre sincronizaciones |
| **Nunca deja al cliente sin respuesta** | Ante cualquier error interno, en lugar de quedar mudo envía un mensaje y escala a una persona |
| **Privacidad y límites de seguridad** | No comparte datos de otros clientes, no inventa precios ni descuentos, no pide datos de tarjeta e ignora intentos de manipularlo. Los datos personales se filtran antes de entrar a su memoria de conocimiento |

### 2.4 Herramientas del agente (visión técnica)

```
buscar_semantico          → búsqueda vectorial en Qdrant (285+ productos indexados)
listar_categorias         → categorías reales del sitio, incluyendo campañas temporales activas
listar_ocasiones          → ocasiones permanentes (Cumpleaños, Aniversario, etc.)
buscar_productos          → búsqueda textual exacta (respaldo)
catalogo_categoria        → listado por categoría (arreglos, desayunos, peluches…)
productos_por_ocasion     → filtro por ocasión (Cumpleaños, Aniversario…)
productos_destacados      → lo más vendido / recomendado
productos_oferta          → productos con descuento
productos_similares       → vecinos en el espacio vectorial del producto seleccionado
detalle_producto          → ficha completa del producto
distritos_cobertura       → cobertura y tarifa de delivery por distrito
metodos_pago              → métodos de pago disponibles
tipo_cambio               → USD → Soles en tiempo real
rastrear_pedido           → estado del pedido por email + código
guardar_datos_cliente     → memoria de largo plazo en perfil de contacto Chatwoot
buscar_conocimiento_equipo → base de conocimiento aprendida de vendedores humanos
escalar_a_humano          → deriva la conversación a un asesor (mensaje de espera + etiqueta + alerta al equipo)
```

---

## 3. Arquitectura técnica (resumen)

```
WhatsApp ──► Canal de mensajería ──► Chatwoot (bandeja omnicanal)
                                          │
                                    Webhook (HTTP POST)
                                          │
                                   Agente IA (FastAPI)
                                     ┌────┴─────┐
                               OpenAI GPT-4o   Qdrant
                               (razonamiento)  (catálogo
                                               vectorial)
                                          │
                                  API del negocio
                                  (productos, pedidos,
                                   distritos, pagos)
```

### Componentes clave

| Componente | Descripción |
|---|---|
| **FastAPI** | Servidor del agente (Python), expone webhook para Chatwoot |
| **Chatwoot** | Plataforma omnicanal open-source; gestiona conversaciones y agentes humanos |
| **OpenAI GPT-4o-mini / GPT-4o** | LLM para razonamiento, selección de herramientas y generación de respuestas |
| **Qdrant** | Base de datos vectorial para búsqueda semántica del catálogo y base de conocimiento |
| **OpenAI Embeddings** | `text-embedding-3-small` (1536 dim) para vectorizar productos y preguntas |
| **Evolution API** | Puente no oficial WhatsApp ↔ Chatwoot (para Pymes, ver §6) |
| **Meta Cloud API** | API oficial de WhatsApp Business (para empresas medianas y grandes, ver §6) |
| **EasyPanel** | PaaS para desplegar todos los servicios en VPS propio del cliente |

---

## 4. Utilidad — ¿Por qué una tienda necesita esto?

### El problema real del comercio por WhatsApp

La mayoría de tiendas pequeñas y medianas en Latinoamérica venden principalmente por
WhatsApp. El problema: **responder WhatsApp es una labor manual, repetitiva y continua**.

- Un vendedor responde las mismas preguntas decenas de veces al día
- Fuera del horario de atención, los clientes se quedan sin respuesta — y compran en otro lado
- En temporadas altas (Navidad, Día de la Madre, San Valentín), el volumen colapsa al equipo
- Cada vendedor tiene su estilo; no hay consistencia en la atención

### Lo que el agente resuelve

- **Atención 24/7** sin contratar personal nocturno
- **Respuesta inmediata** — 0 tiempo de espera para el cliente
- **Consistencia** — siempre el mismo tono, siempre la información correcta de precios
- **Escalabilidad** — atiende 1 o 100 conversaciones simultáneas sin degradarse
- **El vendedor humano se concentra** en cerrar ventas complejas, no en repetir "¿Cuánto cuesta?"

---

## 5. Portabilidad — ¿A qué negocios se puede adaptar?

El agente es **agnóstico al negocio**. Lo que se personaliza por cliente es:

1. **El catálogo** — conectado vía API o sincronizado en Qdrant
2. **Las herramientas** — se agregan/quitan según lo que vende el negocio
3. **El prompt del sistema** — personalidad, nombre, reglas de negocio, horarios, políticas
4. **Las integraciones** — CRM, sistema de pedidos, ERP, pasarelas de pago

### Categorías de negocios donde aplica directamente

| Categoría | Ejemplo de herramientas a adaptar |
|---|---|
| **Flores y regalos** (caso actual) | catálogo + ocasiones + delivery + pedidos |
| **Restaurantes y delivery de comida** | menú + zonas de delivery + pedidos en tiempo real |
| **Ropa y moda** | catálogo por talla/color + disponibilidad + pasarela de pago |
| **Joyería** | catálogo + personalización + seguimiento de pedido |
| **Tiendas de mascotas** | productos + servicios (grooming) + agendar citas |
| **Clínicas y consultorios** | disponibilidad de citas + doctores + precios |
| **Inmobiliarias** | propiedades + filtros (zona, precio, m²) + agendamiento de visitas |
| **Agencias de viaje** | paquetes + disponibilidad + cotización |
| **Supermercados / minimarkets** | catálogo + stock en tiempo real + delivery |

### Lo que NO cambia entre clientes

- El motor de IA (OpenAI)
- La base de datos vectorial (Qdrant)
- La plataforma de mensajería (Chatwoot)
- El servidor del agente (FastAPI)
- El mecanismo de memoria y aprendizaje

---

## 6. Canal de WhatsApp — dos versiones según el tamaño del negocio

Este es uno de los aspectos más importantes para definir el plan adecuado para cada cliente.

### Versión A — Evolution API (para Pymes y negocios pequeños)

| Característica | Detalle |
|---|---|
| **Qué es** | Puente no oficial que conecta WhatsApp Business App con Chatwoot vía QR |
| **Costo del canal** | Gratuito (sin costo por mensaje de WhatsApp) |
| **Límite de mensajes** | ~1.000 conversaciones/mes en la práctica sin riesgo de ban |
| **Tiempo de setup** | 30 minutos (escanear QR, configurar webhook) |
| **Riesgo** | Meta puede banear el número si detecta automatización — no es API oficial |
| **Para quién** | Negocios con volumen bajo-medio, que recién empiezan con IA en WhatsApp |
| **Soporte de imágenes** | Sí, vía Evolution directamente a WhatsApp |
| **Requiere número dedicado** | Sí (número de WhatsApp Business exclusivo para el bot) |

> **Importante:** Evolution API viola los Términos de Servicio de Meta. Es ampliamente usada
> en Latinoamérica para Pymes por su costo cero, pero el riesgo de ban existe y el cliente
> debe ser informado.

### Versión B — Meta Cloud API (para empresas medianas y grandes)

| Característica | Detalle |
|---|---|
| **Qué es** | API oficial de WhatsApp Business de Meta |
| **Costo del canal** | Cobrado por Meta según conversaciones (aprox. $0.0488–$0.1108 por conv. en LatAm) |
| **Límite de mensajes** | Sin límite práctico (escala con el nivel de negocio verificado) |
| **Tiempo de setup** | 3-7 días hábiles (verificación de empresa en Meta Business Suite) |
| **Riesgo** | Ninguno — es el canal oficial |
| **Para quién** | Empresas medianas/grandes, marcas reconocidas, alto volumen de conversaciones |
| **Soporte de imágenes** | Sí, nativo |
| **Requiere número dedicado** | Sí + número verificado por Meta |
| **Ventajas adicionales** | WhatsApp Flows, plantillas aprobadas, estadísticas oficiales, green badge |

---

## 7. Planes de servicio

Los tres planes están diseñados para crecer con el negocio. El código base es el mismo;
lo que varía es la capacidad, las integraciones y el nivel de soporte.

---

### Plan Iniciante — Para negocios que dan el primer paso

**Precio sugerido: $99–$149 USD/mes**

> Ideal para: tiendas con 1-2 vendedores, volumen de 50–300 conversaciones/mes, que
> quieren automatizar las preguntas más frecuentes sin inversión alta.

#### Qué incluye

- Canal vía **Evolution API** (WhatsApp no oficial)
- Agente IA con **prompt base personalizado** al negocio (nombre, productos, horarios, tono)
- **Búsqueda de productos** conectada al catálogo (hasta 500 productos en Qdrant)
- Respuestas a preguntas frecuentes: horarios, precios, delivery, métodos de pago
- **Memoria de corto plazo** (conversación actual)
- Indicador de escritura y mensajes de espera
- Agrupación de mensajes (debounce)
- Chatwoot para ver todas las conversaciones en una sola bandeja
- **1 número de WhatsApp** atendido por el bot
- Setup inicial incluido

#### Limitaciones

- Sin memoria de largo plazo (el agente no recuerda al cliente en la próxima conversación)
- Sin búsqueda semántica avanzada (búsqueda por texto exacto, no por intención)
- Sin aprendizaje de vendedores humanos (Nivel B)
- Sin personalización del ranking de productos por preferencias del cliente
- Máximo **500 productos** en catálogo vectorial
- Máximo **2 agentes humanos** en Chatwoot
- Soporte por email (48h de respuesta)
- **Sin SLA** de disponibilidad garantizado
- El número puede ser baneado por Meta (riesgo inherente de Evolution API)

#### Lo que el cliente ve

```
Cliente: "Quiero algo para el cumpleaños de mi mamá, tiene 60 años"
Regalito: "¡Qué lindo! 🎂 Déjame buscar algo especial..."
           [muestra 4-5 productos con imagen y precio]
           "¿Te llama la atención alguno? 😊"
```

---

### Plan Negocio — Para tiendas que ya venden bien por WhatsApp

**Precio sugerido: $249–$399 USD/mes**

> Ideal para: tiendas con 3-8 vendedores, 300–2.000 conversaciones/mes, que necesitan
> que el bot sea un vendedor completo, no solo un contestador.

#### Qué incluye (todo lo del plan Iniciante, más):

- Canal vía **Evolution API** (puede migrar a Meta API por costo adicional)
- **Búsqueda semántica** (Qdrant + embeddings): entiende intención, estilo, sentimiento
- **Filtros avanzados**: por ocasión, presupuesto, categoría, atributos (color, tipo de flor)
- **Productos similares**: el cliente ve alternativas del producto que le interesó
- **Memoria de largo plazo**: el agente recuerda nombre, distrito y preferencias del cliente en futuras conversaciones
- **Personalización de búsquedas**: el ranking de productos se adapta a los gustos del cliente
- **Aprendizaje de vendedores (Nivel B)**: el agente aprende de las conversaciones resueltas por humanos
- **Base de conocimiento del equipo**: el agente responde preguntas complejas como lo haría tu mejor vendedor
- **Lógica de contexto inteligente** (ej: no mostrar arreglos fúnebres en cumpleaños)
- **Honestidad ante stock limitado**: si no tiene lo que pide, lo dice y ofrece alternativas
- Hasta **2.000 productos** en catálogo vectorial
- Hasta **8 agentes humanos** en Chatwoot
- **2 números de WhatsApp** atendidos
- Dashboard de conversaciones en Chatwoot (etiquetas, asignaciones, estadísticas básicas)
- Soporte por WhatsApp (respuesta en 24h)
- Uptime **99%** (alojado en VPS del proveedor)

#### Limitaciones

- Canal Evolution API: riesgo de ban de Meta (informado al cliente)
- Sin integración con sistema de pedidos propio (solo consulta de estado via API existente)
- Sin CRM propio ni integración con terceros (HubSpot, Salesforce, etc.)
- Sin plantillas de WhatsApp outbound (no puede iniciar conversaciones — solo responde)
- Sin reportería avanzada ni métricas de conversión
- Máximo 2.000 productos en Qdrant
- Soporte en horario de oficina

#### Lo que el cliente ve

```
[Segunda visita del cliente]
Cliente: "Hola, soy María, quiero algo para regalar"
Regalito: "¡Hola María! 😊 La última vez te interesaron los arreglos con girasoles.
           ¿Sigo buscando algo en esa línea o es para otra ocasión?"

[Pregunta no frecuente, pero resuelta antes por un vendedor]
Cliente: "¿Puedo pedir con un día de anticipación y recoger yo?"
Regalito: "Claro, puedes coordinar el recojo en nuestra tienda. Te recomiendo 
           confirmar con el equipo: WhatsApp +51 977 174 485 para coordinar la hora."
           [Esta respuesta fue aprendida de una conversación anterior del equipo]
```

---

### Plan Empresa — Para empresas con volumen alto y marca que proteger

**Precio sugerido: $799–$1.499 USD/mes + costos Meta API**

> Ideal para: empresas medianas-grandes, cadenas de tiendas, marcas con múltiples sucursales,
> volumen superior a 2.000 conversaciones/mes, que necesitan la API oficial de Meta para
> operar sin riesgos.

#### Qué incluye (todo lo del plan Negocio, más):

- Canal vía **Meta Cloud API (oficial)** — sin riesgo de ban, escala sin límite
- **Plantillas de WhatsApp aprobadas** para mensajes outbound (el bot puede iniciar la conversación: confirmaciones, seguimientos, promociones)
- **WhatsApp Business green badge** (verificación de empresa ante Meta)
- **Multiagente escalable**: hasta 20+ agentes humanos en Chatwoot
- **Múltiples números / sucursales**: cada sucursal puede tener su número propio, todos conectados a la misma plataforma
- **Catálogo ilimitado** en Qdrant (sin límite de productos)
- **Integración con ERP / CRM** (WooCommerce, Shopify, HubSpot, Salesforce, etc.) — desarrollo a medida
- **Integración con sistema de pedidos propio** (crear, modificar, cancelar pedidos desde el chat)
- **Reportería avanzada**: tasa de resolución por bot, escalaciones a humano, productos más consultados, tiempo de respuesta, conversaciones por canal
- **A/B testing de prompts**: probar dos versiones del agente y medir cuál convierte mejor
- **Segmentación de clientes**: el bot puede detectar cliente VIP, nuevo, recurrente y adaptar el trato
- **Escalación inteligente**: el bot detecta cuándo el cliente está listo para comprar y transfiere a un vendedor humano con el contexto completo
- **SLA 99.9%** con monitoreo activo
- **Actualizaciones del catálogo en tiempo real** (sincronización automática con la tienda)
- Soporte **prioritario 24/7** con canal dedicado
- **Onboarding personalizado** (2 semanas de capacitación al equipo)
- Infraestructura en VPS exclusivo del cliente (no compartida)

#### Diferencias clave vs Plan Negocio

| Aspecto | Plan Negocio | Plan Empresa |
|---|---|---|
| Canal WhatsApp | Evolution API (no oficial) | Meta Cloud API (oficial) |
| Riesgo de ban | Sí | Ninguno |
| Mensajes outbound | No | Sí (con plantillas aprobadas) |
| Integraciones externas | No | Sí (CRM, ERP, pedidos) |
| Número de agentes | Hasta 8 | Ilimitado |
| Productos en catálogo | Hasta 2.000 | Ilimitado |
| Reportería | Básica (Chatwoot) | Avanzada con métricas de negocio |
| SLA | 99% | 99.9% |
| Infraestructura | Compartida | Exclusiva |

#### Lo que el cliente ve

```
[Plantilla outbound — 24h después de que el cliente consultó sin comprar]
Regalito: "Hola María 🌸 Ayer consultaste por arreglos florales para aniversario.
           ¿Te ayudo a cerrar el pedido? Puedo separar el de rosas rojas que te
           mostré si me confirmas el distrito de entrega."

[Escalación inteligente — cliente listo para pagar]
Cliente: "Sí, me gustaría ese, ¿cómo lo pido?"
Regalito: "Perfecto 🎁 Te transfiero con Carla de nuestro equipo para que 
           te confirme los detalles. [Contexto: cliente quiere el Arreglo Rosas 
           Eternas XL ($35), entrega en Miraflores, tiene Yape disponible]"
           → El vendedor recibe el chat con el resumen ya redactado
```

---

## 8. Tabla comparativa de los planes

| Característica | Iniciante | Negocio | Empresa |
|---|---|---|---|
| **Precio referencial** | $99–$149/mes | $249–$399/mes | $799–$1.499/mes |
| Canal WhatsApp | Evolution API | Evolution API | Meta Cloud API |
| Riesgo de ban Meta | Sí (informado) | Sí (informado) | No |
| Mensajes outbound | No | No | Sí |
| Búsqueda semántica | No | Sí | Sí |
| Productos similares | No | Sí | Sí |
| Memoria corto plazo | Sí | Sí | Sí |
| Memoria largo plazo | No | Sí | Sí |
| Personalización búsquedas | No | Sí | Sí |
| Aprendizaje de vendedores | No | Sí | Sí |
| Integración ERP/CRM | No | No | Sí |
| Productos en catálogo | 500 | 2.000 | Ilimitado |
| Agentes humanos | 2 | 8 | Ilimitado |
| Números de WhatsApp | 1 | 2 | Ilimitado |
| Reportería | Básica | Media | Avanzada |
| SLA | Sin garantía | 99% | 99.9% |
| Soporte | Email 48h | WhatsApp 24h | Dedicado 24/7 |
| Infraestructura | Compartida | Compartida | Exclusiva |
| Setup | Incluido | Incluido | Onboarding 2 semanas |

---

## 9. Casos de uso reales

### Caso 1 — Floristería / Tiendas de regalos (caso Don Regalo)
**Plan recomendado:** Negocio → Empresa
- El agente recibe pedidos en horario no laboral (madrugada, domingos)
- Sugiere productos para la ocasión exacta con fotos y precios en soles
- Detecta si el cliente es nuevo o recurrente y adapta el trato
- Aprende del equipo de vendedores cómo manejar objeciones de precio

### Caso 2 — Restaurante con delivery propio
**Plan recomendado:** Iniciante → Negocio
- Atiende pedidos automáticamente, verifica zona de cobertura
- Informa el menú del día con fotos
- En temporadas altas (fines de semana) gestiona el volumen sin colapsar al equipo
- Escala a humano para pedidos con personalización especial

### Caso 3 — Tienda de ropa online
**Plan recomendado:** Negocio
- Filtra por talla, color, estilo ("algo casual para salir a comer")
- Recuerda las tallas del cliente en futuras conversaciones
- Verifica disponibilidad de stock en tiempo real
- Propone combos o productos que combinan con lo que el cliente eligió

### Caso 4 — Clínica o consultorio médico
**Plan recomendado:** Negocio → Empresa
- Atiende consultas sobre especialidades y médicos disponibles
- Verifica disponibilidad de citas y las agenda (con integración de calendario)
- Recuerda el historial de visitas del paciente
- Escala a recepcionista para casos urgentes

### Caso 5 — Cadena de tiendas / Franquicia
**Plan recomendado:** Empresa
- Cada sucursal tiene su número propio con el mismo agente personalizado
- El bot sabe en cuál sucursal está el cliente por el número que contactó
- Reportería por sucursal: cuál tiene más consultas, qué productos se consultan más
- Integración con el ERP central para stock unificado

### Caso 6 — Empresa de logística o courier
**Plan recomendado:** Empresa
- El cliente escribe su número de tracking y recibe el estado en tiempo real
- Atiende reclamos y escala a un agente humano con el contexto del envío
- Envía notificaciones outbound cuando el paquete está por llegar (Meta API)

---

## 10. Preguntas frecuentes del comprador

**¿En cuánto tiempo está listo?**
- Plan Iniciante: 3–5 días hábiles
- Plan Negocio: 7–14 días hábiles
- Plan Empresa: 3–6 semanas (incluye integración con sistemas del cliente)

**¿El agente puede responder en el mismo número que ya uso?**
Se recomienda un número dedicado para el bot. El número humano y el bot pueden coexistir
si se usa un número secundario, o se puede configurar para que el bot responda primero y
un humano tome el control cuando quiera.

**¿Mis vendedores pierden el trabajo?**
No. El agente resuelve las preguntas repetitivas. Los vendedores se enfocan en las
conversaciones de mayor valor: negociaciones, clientes VIP, casos especiales.
En la práctica, los negocios que adoptan el agente suelen **aumentar** su equipo de
ventas porque el volumen de leads calificados sube.

**¿Funciona en otros idiomas?**
Sí. El agente funciona en el idioma en que el cliente escribe. Si el cliente escribe en
inglés, responde en inglés. La configuración base es en español.

**¿Qué pasa si el agente no sabe algo?**
Primero consulta su base de conocimiento aprendida del equipo. Si tampoco sabe, deriva
al equipo humano con el contexto de la conversación. Nunca inventa información.

**¿Puedo ver todas las conversaciones?**
Sí. Chatwoot es la bandeja de entrada omnicanal donde el equipo ve todas las
conversaciones en tiempo real, puede tomar el control manualmente y responder como humano.

**¿El agente puede crear pedidos?**
- Plan Iniciante y Negocio: informa sobre productos y dirige al cliente a la web/pago,
  pero no crea el pedido en el sistema.
- Plan Empresa: con integración personalizada, sí puede crear y modificar pedidos
  directamente en el sistema del cliente.

---

## 11. Costos de infraestructura (referencia para el proveedor)

Para calcular el margen real del servicio, estos son los costos operativos estimados
por cliente:

| Componente | Plan Iniciante | Plan Negocio | Plan Empresa |
|---|---|---|---|
| VPS (EasyPanel) | ~$15–20/mes (compartido) | ~$20–30/mes (compartido) | ~$60–100/mes (exclusivo) |
| OpenAI GPT-4o-mini | ~$5–15/mes | ~$15–40/mes | ~$50–200/mes |
| OpenAI Embeddings | ~$1–3/mes | ~$2–5/mes | ~$5–15/mes |
| Qdrant (vector DB) | ~$0 (plan free) | ~$0–10/mes | ~$20–50/mes |
| Meta API (mensajes) | N/A | N/A | Variable ($0.05–0.11/conv) |
| Chatwoot (self-hosted) | ~$0 | ~$0 | ~$0 (o plan cloud $50+) |
| **Total aprox.** | **$21–38/mes** | **$37–85/mes** | **$135–365/mes + Meta** |

> Los costos de OpenAI varían según el volumen de conversaciones y el modelo usado.
> GPT-4o-mini es significativamente más económico que GPT-4o con calidad aceptable para
> la mayoría de negocios. GPT-4o se recomienda para Empresa donde la precisión es crítica.

---

## 12. Estrategia comercial sugerida

### Posicionamiento
No vendas "un chatbot de WhatsApp". Vende **un vendedor IA que trabaja 24/7 sin sueldo fijo**.

El argumento más poderoso: **un vendedor en Lima cuesta S/1.800–3.500/mes**.
El Plan Negocio cuesta menos que contratar media persona — y no se enferma, no falta
y no comete errores de precio.

### Canales de adquisición
1. **Agencias de marketing digital** — como servicio complementario a sus clientes
2. **Cámaras de comercio y asociaciones de Pymes**
3. **Proveedores de ERP/e-commerce** — como módulo de conversación
4. **Referidos** — el modelo de negocio de WhatsApp es viral: cuando un cliente ve la
   calidad de atención, pregunta "¿cómo lo hiciste?"

### Modelo de ingresos
- **Setup fee**: $200–$800 por implementación inicial (no recurrente)
- **Mensualidad**: los planes descritos arriba
- **Upsell**: actualizar de Iniciante a Negocio a Empresa según crece el cliente
- **Integraciones a medida**: cobro por hora o por proyecto ($50–150/h)
- **Mantenimiento del catálogo**: si el cliente no tiene API, cobrar la sincronización manual

### Retención
- El agente **aprende** con el tiempo (base de conocimiento que crece)
- El catálogo vectorial **es valioso** y costoso de migrar
- La memoria de largo plazo de los clientes está alojada en el sistema
- El cliente no quiere empezar de cero con otro proveedor

---

## 13. Hoja de ruta tecnológica (para el proveedor)

### Ya implementado (v1.1)
- [x] Motor IA (OpenAI function calling)
- [x] Búsqueda semántica + híbrida (Qdrant)
- [x] Filtro por `categoria_slug` (padre normalizado) en búsquedas semánticas
- [x] Normalización de slugs subcategoría → padre en sync_qdrant.py
- [x] Campañas temporales como categorías curadas: Día del Padre, Día de la Madre, Navidad, etc. se resuelven con `listar_categorias` → `catalogo_categoria`
- [x] Bloqueo de `buscar_semantico` libre cuando el usuario pide una campaña temporal sin `categoria_slug`
- [x] Búsqueda secuencial obligatoria (`buscar_semantico` → `catalogo_categoria`) — sin duplicados
- [x] Fallback categoría-específico (nunca mezcla categorías en el fallback)
- [x] Detección de mensajes citados de WhatsApp vía Evolution API webhook
- [x] Distinción cita = detalle vs cita = intención de compra
- [x] Flujo de cierre en 6 pasos secuenciales (una pregunta por turno)
- [x] Franja horaria presentada en lista numerada (5 opciones)
- [x] `distritos_cobertura` llamado una sola vez por pedido
- [x] Catálogo general sin mostrar categoría fúnebre proactivamente
- [x] Memoria corto y largo plazo
- [x] Personalización por preferencias del cliente
- [x] Aprendizaje de vendedores humanos (Nivel B)
- [x] Lógica de contexto (fúnebre, ocasiones, honestidad de stock)
- [x] Canal WhatsApp vía Evolution API
- [x] Envío de imágenes de productos por Evolution API directo, con conversión WebP → JPEG antes de enviar media
- [x] Plataforma Chatwoot (bandeja omnicanal)
- [x] Mensajes de espera y typing humano
- [x] Debounce de mensajes a 6 segundos (agrupa mensajes rápidos del usuario)
- [x] Validación de productos activos en tiempo real (descarta los desactivados aunque sigan en el índice)
- [x] Sin productos repetidos en "más opciones" (exclusión de los ya mostrados)
- [x] Escalación a un asesor humano (a pedido del cliente, por frustración o ante fallo): mensaje de espera + etiqueta + bloqueo del bot mientras atiende el equipo
- [x] Alerta opcional al equipo (Slack/webhook) en escalaciones y fallos
- [x] Nunca queda sin responder: fallback ante errores internos
- [x] Restricciones de seguridad y privacidad en el prompt (datos de terceros, precios, pagos, anti-manipulación, alcance)
- [x] Filtrado de datos personales (PII) antes de indexar conocimiento del equipo
- [x] Mensajes no repetitivos (saludo y "ya voy" no se repiten textualmente)
- [x] Tests unitarios de la lógica crítica
- [x] Credenciales fuera del código (archivos de secretos / variables de entorno)

### Próximas versiones
- [ ] Panel de administración web para que el cliente gestione su prompt y catálogo sin intervención técnica
- [ ] Integración Meta Cloud API (para Plan Empresa)
- [ ] Reportería con métricas de conversión
- [ ] Conector genérico para WooCommerce y Shopify
- [ ] Soporte multi-idioma configurable
- [ ] Resumen automático del contexto para el asesor al escalar a humano (el handoff básico ya está implementado)
- [ ] WhatsApp Flows (formularios nativos de WhatsApp para captura de pedidos)
- [ ] Dashboard de conocimiento: ver qué aprendió el agente de los vendedores

---

*Documento generado el 14 de junio de 2026 — Proyecto Agente Regalito / Don Regalo*
*Versión 1.3 — Actualizado el 27 de junio de 2026: escalación a un asesor humano, validación de productos activos, sin opciones repetidas, restricciones de seguridad y privacidad, filtrado de datos personales, alertas al equipo y tests*
*Para uso interno y propuestas comerciales*
