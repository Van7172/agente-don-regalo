"""Compatibilidad: los toolsets ahora viven en `registry.AgentSpec.tool_names`.

Estaban sueltos aquí, en un archivo distinto al de los prompts, sin nada que
obligara a que un playbook citara solo tools de su propio toolset. `registry` ata
ambos; esto queda como fachada para los call sites viejos.
"""
from __future__ import annotations

from app.harness.registry import tools_for

__all__ = ["tools_for"]
