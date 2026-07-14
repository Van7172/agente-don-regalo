"""Evals que SÍ llaman a OpenAI. No corren en CI por defecto: cuestan dinero.

    RUN_LLM_EVALS=1 pytest tests/test_evals_llm.py -q

Miden el único punto donde el clasificador LLM decide: los mensajes que las reglas
no saben clasificar y que antes caían en el catálogo en silencio.
"""
import os

import pytest

from evals.runner import run_routing_llm

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LLM_EVALS") != "1",
    reason="Llama a OpenAI. Actívalo con RUN_LLM_EVALS=1.",
)


@pytest.mark.asyncio
async def test_el_llm_clasifica_lo_que_las_reglas_no_saben():
    resultados = await run_routing_llm()
    fallos = [r for r in resultados if not r.passed]

    assert resultados, "el corpus de routing_llm está vacío"
    assert not fallos, "\n".join(f"{r.case_id}: {r.detail}" for r in fallos)
