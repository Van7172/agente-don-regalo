# CI con evaluaciones obligatorias

## Regla

Una revisión, merge, imagen o despliegue solo es elegible para producción si
terminan correctamente, en este orden:

1. espejo `sandbox/`;
2. snapshot del contrato proveedor/consumidor MCP;
3. suite completa de tests;
4. corpus determinista de evaluaciones.

El comando canónico es:

```bash
python scripts/quality_gate.py
```

El CRM PHP tiene un gate complementario:

```bash
python scripts/check_crm.py
```

Valida sintaxis PHP, JavaScript y todos los contratos estáticos de `crm/tests/`.

Devuelve un código distinto de cero en la primera regresión y no ejecuta las
etapas posteriores. Con `--report artifacts/quality-gate.json` deja evidencia
estructurada de las etapas ejecutadas.

## Contrato MCP obligatorio

`tests/fixtures/mcp/provider_contract_snapshot.json` representa la superficie
PHP publicada que consume el agente. El test obligatorio comprueba que:

- todas las tools consumidas están declaradas;
- los parámetros del agente son un subconjunto de las claves publicadas;
- todos los objetos cierran `additionalProperties`;
- requeridos, contenedores y campos de salida coinciden con el contrato del
  consumidor.

Cuando el repositorio hermano `donregalo/` está disponible, el test cruzado
adicional inspecciona directamente `Tools::definiciones()`. En CI, el snapshot
es obligatorio y nunca puede omitirse por falta de PHP.

Al modificar `clienteApiApp/src/Mcp/Tools.php`, se debe actualizar el snapshot
en el mismo cambio coordinado del agente. Una diferencia bloquea el gate.

## GitHub Actions

El workflow `.github/workflows/ci.yml` publica tres checks:

- `crm / php-contracts`;
- `quality-gate / production`;
- `production-image / gated`.

La imagen depende de los dos gates. Configura ambos gates como **required status checks**
en la regla de protección de `main`, desactiva pushes directos y exige pull
request.

Esta configuración de GitHub es una acción administrativa externa al
repositorio: el YAML no puede activarla por sí solo.

## Docker y EasyPanel

El `Dockerfile` contiene una etapa `quality-gate`. La imagen `runtime` copia un
marcador producido exclusivamente por esa etapa, por lo que `docker build .`
vuelve a ejecutar el gate y falla si aparece una regresión.

Para evitar una carrera entre CI y EasyPanel:

1. no desplegar automáticamente cada push sin esperar checks;
2. aceptar en `main` únicamente commits con los dos checks verdes;
3. desplegar por SHA/commit aprobado;
4. verificar `/health` después del despliegue.

No se almacenan credenciales de producción en Actions. Los evals obligatorios
son deterministas y no llaman a OpenAI. Los evals LLM continúan siendo una
comprobación manual con `RUN_LLM_EVALS=1`.

## Diagnóstico

El nombre de la etapa fallida queda en consola y en
`artifacts/quality-gate.json`. No se debe reintentar un despliegue ignorando el
fallo; primero se corrige la regresión y se vuelve a ejecutar el gate completo.
