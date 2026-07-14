"""¿El producto que el cliente eligió sigue existiendo?

El estado de la conversación guarda ids que el cliente vio hace horas o días, y
Qdrant se sincroniza cada cierto tiempo. Sin esta comprobación, el bot puede cerrar
un pedido entero —distrito, fecha, horario— de un producto que la tienda ya dio de
baja: el asesor entra al chat verde a cobrar y descubre que no existe.

`/productos/activos` es la única fuente que lo sabe. Se consulta en el único punto
donde equivocarse cuesta dinero: justo antes de arrancar el cierre.
"""
from __future__ import annotations

import logging

import httpx

from app.tools.catalog import productos_activos

log = logging.getLogger(__name__)


async def is_available(product_id: int | None) -> bool | None:
    """`True` / `False` / `None` si la API no pudo confirmarlo.

    `None` no es "no disponible": ante un fallo de la API dejamos pasar la venta.
    Bloquear un pedido sano por un timeout es peor que el riesgo que evitamos.
    """
    if product_id is None:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            activos = await productos_activos(client, [int(product_id)])
    except Exception as err:
        log.warning("[stock] no se pudo verificar %s: %s", product_id, err)
        return None

    if activos is None:
        return None
    return int(product_id) in activos


def unavailable_message(nombre: str = "") -> str:
    producto = f"*{nombre}*" if nombre else "ese producto"
    return (
        f"Justo {producto} ya no está disponible 😔 "
        "¿Te muestro otras opciones parecidas?"
    )
