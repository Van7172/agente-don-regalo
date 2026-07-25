"""Compatibilidad: las políticas públicas ahora viven en `app.guardrails`.

Los imports nuevos deben apuntar a `app.guardrails`; este módulo evita romper
extensiones y tests existentes durante la migración.
"""

from app.guardrails.conversation import *  # noqa: F401,F403
