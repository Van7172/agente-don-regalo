"""FACTS: datos del dominio, inyectados solo al agente que los necesita.

El especialista de cobertura no necesita los ids de ocasión; el de catálogo no
necesita la política de devoluciones. Separar los datos del procedimiento evita
que cada playbook crezca hasta volver a ser el monolito.
"""
from __future__ import annotations

from app.delivery_windows import SCHEDULE_OPTIONS

CATALOG_TAXONOMY = """## CATEGORÍAS REALES (slugs para `catalogo_categoria`)
Permanentes:
- **arreglos-florales** → arreglos-florales-variados, en-canasta,
  arreglos-florales-con-peluche, cajas, corporativos, ramos-de-flores, floreros
- **desayunos** → desayunos-criollos, desayunos-de-amor, desayunos-light,
  desayunos-tematicos
- **peluches**
- **arreglos-funebres** → cruces-funebres, lagrimas-funebres,
  coronas-para-difuntos, mantos-funebres
- **regalo-para-bebe**
- **cestas**
- **plantas** → terrarios, orquideas, suculentas

Las categorías de campaña (Día del Padre, Navidad, San Valentín…) rotan durante
el año y NO se listan aquí: `listar_categorias` marca cada campaña vigente con
`es_temporal: true`.

## OCASIONES REALES (ids para `productos_por_ocasion`)
1 Cumpleaños · 2 Aniversario · 3 Felicitación · 4 Nacimiento
5 Agradecimiento · 6 Negocios · 7 Otros"""

PRICING = """## PRECIOS Y MONEDA
- Cada producto llega ya con `precio_sol` y `precio_usd`. **Cópialos tal cual.**
- Formato exacto: **S/XX.XX ($XX.XX)** — ejemplo: "S/64.60 ($19.00)".
- **Nunca calcules un precio ni conviertas monedas tú.** Si un precio no viene en
  el resultado de la tool, no lo inventes: no lo menciones.
- Las tarifas de envío llegan igual, en ambas monedas (`tarifa_sol`, `tarifa_usd`)."""

DELIVERY = f"""## HORARIOS Y DELIVERY
- Atención: lunes a viernes 7:00 am – 10:00 pm; sábados 7:00 am – 8:00 pm (hora Lima).
  Pedidos web 24/7.
- Entregas de lunes a domingo (excepto feriados).
- Pedido el mismo día: solo con coordinación previa.
- **Desayunos sorpresa: se piden con 1 día de anticipación** y solo en los rangos 1 y 2.
- Don Regalo entrega **solo dentro de Lima Metropolitana**.
- **Sede / punto de despacho:** Calle La Habana 595, San Isidro, Lima.
  Si preguntan dónde estamos, nuestra dirección o dónde se preparan/despachan los
  pedidos, usa este dato. No confundas la sede con la dirección de entrega del cliente.

## RANGOS HORARIOS DE ENTREGA
{SCHEDULE_OPTIONS}"""

PAYMENT = """## MÉTODOS DE PAGO (confírmalos con `metodos_pago`)
- Tarjeta de crédito/débito vía PayPal o Payu (Visa, Mastercard, Amex, Discover).
- Depósito/transferencia: BCP, Scotiabank, Interbank, BBVA.
- Yape / Plin al 943 113 807.
- Transferencia internacional: Western Union, Xoom, Money Gram.
- Pagos desde provincia: comisión adicional de S/7.50.
- Los comprobantes van a los canales oficiales del equipo, NO a este chat.
  Tú no puedes verlos ni confirmarlos → `escalar_a_humano`."""

RETURNS = """## DEVOLUCIONES Y CANCELACIONES
- Cambios dentro de las primeras 5 horas tras la entrega (con justificación).
- Devolución por depósito en cuenta, nunca en efectivo. Tarjeta: ~2 días hábiles.
- Cancelación: mínimo 1 día antes, avisando por WhatsApp (+51) 977174485 y a
  ventas@donregalo.pe.
- Cambios para el pedido del día siguiente: hasta las 4:00 pm.
- Cambios para el pedido del lunes: hasta el sábado 11:00 am."""

CONTACT = """## CONTACTO
- 📱 Este chat (WhatsApp API): (+51) 923149666 — **solo mensajes**.
  Cloud API no admite llamadas de voz: NUNCA digas "llama al 923149666".
- 📱 WhatsApp del equipo humano y 📞 llamadas: (+51) 977174485
- 📧 ventas@donregalo.pe · 🌐 donregalo.pe
- 📍 Sede despacho: Calle La Habana 595, San Isidro, Lima.
- Si el cliente quiere hablar por teléfono → indícale el 977174485.
  Si prefiere seguir por escrito con una persona en este chat → `escalar_a_humano`."""

FACTS: dict[str, str] = {
    "catalog_taxonomy": CATALOG_TAXONOMY,
    "pricing": PRICING,
    "delivery": DELIVERY,
    "payment": PAYMENT,
    "returns": RETURNS,
    "contact": CONTACT,
}


def render_facts(names: tuple[str, ...] | list[str]) -> str:
    return "\n\n".join(FACTS[n] for n in names if n in FACTS)
