# Harness Engineering: documentación, buenas prácticas, técnicas y reglas

## Resumen ejecutivo

Harness engineering es la disciplina de diseñar el entorno completo alrededor de un modelo o agente de IA para que funcione como un sistema de entrega de software fiable, repetible y verificable, en lugar de ser solo un modelo que responde prompts aislados. Este entorno (el "harness") incluye prompts de sistema, archivos de configuración y documentación, herramientas, límites de permisos, workflows, pruebas automáticas, observabilidad y bucles de feedback que convierten la inteligencia del modelo en resultados productivos consistentes.[^1][^2][^3]

Este informe sintetiza la literatura reciente y cursos prácticos sobre harness engineering para agentes de programación y otros agentes de IA, cubriendo anatomía del harness, mejores prácticas de documentación, controles clave (contexto, skills, límites, verificación, aprendizaje), reglas operativas para producción y recomendaciones accionables para proyectos de SaaS, e‑commerce y automatización.[^4][^2][^3]

## 1. Definición y objetivos del harness engineering

### 1.1 ¿Qué es harness engineering?

Harness engineering se define como la disciplina de diseñar los sistemas, restricciones arquitectónicas, entornos de ejecución y feedback loops que rodean a los agentes de IA para que estos puedan operar como componentes confiables dentro de aplicaciones y pipelines de software reales. La ingeniería de harnesses se centra en la arquitectura del sistema alrededor del modelo: reglas, contexto, herramientas, seguridad, observabilidad y mecanismos de verificación.[^5][^6][^7]

En el contexto de agentes de programación (AI coding agents), el harness incluye mensajes de sistema, archivos de roles (`CLAUDE.md`, `AGENTS.md`), descripciones de tools, servidores MCP, sandboxes de ejecución, políticas de contexto, hooks de verificación, CI, documentación y dashboards de monitoreo.[^1][^4]

### 1.2 Diferencia con el "prompt engineering"

El prompt engineering tradicional se centra en redactar instrucciones para el modelo en forma de texto, mientras que harness engineering considera el modelo como solo un componente dentro de un sistema mayor de entrega de software. En vez de optimizar una única conversación, harness engineering diseña workflows completos que incluyen planificación, ejecución con herramientas, verificación automatizada, revisión humana donde haga falta y aprendizaje continuo a partir de errores.[^7][^3][^8]

Un harness bien diseñado reduce la dependencia de prompts enormes e inestables y se apoya en documentación versionada, pruebas, límites de permisos y procesos reproducibles como fuentes de verdad.[^2][^3]

### 1.3 Objetivos principales

Los principales objetivos del harness engineering son:[^3][^8]

- Convertir agentes de IA en sistemas de entrega confiables, medidos por calidad de output, estabilidad y velocidad de ciclo, no por métricas superficiales como número de PRs o líneas de código generadas.[^8][^3]
- Minimizar errores graves y comportamientos fuera de control mediante límites de permisos, reglas claras y verificación determinista sistemática.[^6][^8]
- Hacer que el sistema sea legible tanto para humanos como para agentes: que fronteras de servicios, contratos de datos, restricciones y planes estén explícitos y versionados.[^2][^3]
- Capturar aprendizaje continuo de errores: cada fallo recurrente debe traducirse en nueva regla, test, skill o mejora de documentación.[^9][^3]

## 2. Anatomía del harness para agentes de IA

### 2.1 Componentes básicos

Fuentes de referencia sobre harness engineering para agentes de programación describen una anatomía típica que incluye los siguientes elementos:[^4][^3][^1]

- **Modelo/agent core**: el modelo de lenguaje o agente principal (por ejemplo, Claude, GPT, etc.) al que se le da acceso controlado a herramientas y contexto.
- **Mensajes de sistema y archivos de rol**: archivos como `CLAUDE.md` y `AGENTS.md` que definen misión, límites, estilo de comunicación, roles de subagentes y reglas de coordinación.[^1][^4]
- **Skill files**: documentos que encapsulan procedimientos reutilizables (por ejemplo, migración de base de datos, refactorización de módulo) con pasos, criterios de aceptación y herramientas permitidas.[^10][^4]
- **Tools y servidores MCP**: definición de herramientas (APIs, comandos CLI, acceso a repos, sandbox de navegador, bases de datos) expuestas mediante protocolos como MCP.[^3][^1]
- **Orquestador de agentes**: lógica que decide cuándo crear subagentes, cómo hacer handoffs entre ellos, qué modelo usar para cada tarea y cómo gestionar el contexto compartido.[^2][^1]
- **Hooks y middleware**: componentes que comprimen contexto, implementan auto‑verificación, ejecutan tests, linters y escáneres de seguridad antes de aceptar cambios.[^8][^1]
- **Observabilidad**: logging estructurado, métricas, trazas y dashboards que permiten inspeccionar lo que el agente hizo, cuándo, con qué inputs y outputs.[^3][^1]

### 2.2 Artefactos de documentación

Cursos dedicados a harness engineering para agentes de código proponen un conjunto de artefactos de documentación versionados en el repositorio de código:[^10][^4]

| Artefacto               | Contenido típico                                                                 | Objetivo principal                                                      |
|-------------------------|----------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| `CLAUDE.md` (system)    | Objetivos globales, límites, estilo, valores y reglas de trabajo del agente.    | Proveer un entry‑point breve y estable para el agente.                 |
| `AGENTS.md`             | Lista de agentes/subagentes, roles, responsabilidades y handoffs.                | Documentar arquitectura de agentes y ruteo de tareas.                  |
| Skill files (`*.md`)    | Procedimientos detallados para tareas repetibles con herramientas específicas.  | Encapsular práctica experta en unidades reutilizables.                 |
| `feature_list.json`     | Listado de servicios, endpoints, contratos de datos y ownership.                 | Hacer legible la topología y capacidades del sistema.                  |
| Runbooks/playbooks      | Guías operativas para incidentes, migraciones y despliegues.                     | Reducir errores en operaciones críticas y apoyar agentes y humanos.    |

Estos archivos se mantienen como "fuente de verdad" para agentes y humanos, evitando que reglas críticas estén únicamente en mensajes de chat o documentación aislada.[^8][^2]

### 2.3 Harness para plataformas de software

Artículos sobre harness engineering también lo vinculan con plataformas unificadas de entrega de software, donde diferentes agentes (DevOps, testing, AppSec, optimización de costes) operan dentro de un mismo harness. En estos casos, el harness se integra con pipelines de CI/CD, sistemas de tickets, repositorios de código y herramientas de observabilidad para ofrecer una vista unificada de la actividad de los agentes.[^11][^3]

## 3. Controles clave del harness: contexto, skills, límites, verificación y aprendizaje

Varios autores describen un marco de "cinco controles" que un harness efectivo debe aplicar de forma sistemática: contexto, skills, límites, verificación y aprendizaje.[^7][^3]

### 3.1 Contexto: hacer el sistema legible

El contexto describe toda la información que permite a un agente entender qué puede hacer y qué no dentro de un sistema: servicios, módulos, contratos de datos, restricciones, planes actuales y estándares de calidad. La recomendación es evitar prompts gigantes y en su lugar proporcionar un entry‑point corto (como `CLAUDE.md`) que apunte a documentación estructurada, versionada y accesible, considerada fuente de verdad.[^2][^3]

Buenas prácticas de contexto:[^3][^2]

- Mapear servicios y módulos con descripciones mínimas, ownership explícito y enlaces a documentación ampliada.
- Documentar restricciones arquitectónicas (por ejemplo, qué módulos no pueden llamar directamente a qué bases de datos o servicios externos).
- Mantener planes, TODOs y roadmaps en archivos legibles para agentes (por ejemplo, `PLAN.md`), no solo en herramientas de gestión con APIs difíciles de integrar.

### 3.2 Skills: convertir práctica experta en capacidades reutilizables

Los skills encapsulan procedural knowledge: pasos, criterios de aceptación, herramientas y contexto relevante para tareas específicas como migraciones, cambios de API, investigación de incidentes o revisión de costes en cloud. Un buen skill exige evidencia concreta al final (por ejemplo, pruebas pasadas, plan de rollback, checklist de observabilidad) en vez de aceptar outputs vagos.[^4][^8][^3]

Buenas prácticas de diseño de skills:[^4][^3]

- Definir claramente scope del skill (en qué tipo de tareas se usa) y sus precondiciones.
- Especificar herramientas permitidas y scripts que el agente debe usar, reduciendo improvisación peligrosa.
- Incluir criterios de aceptación explícitos y checklist de verificación ligada a tests automáticos.

### 3.3 Límites: suministrar poder útil pero acotado

Harness engineering aplica el principio de mínimo privilegio a agentes de IA: el objetivo es darles los permisos justos y necesarios para tareas específicas, no acceso ilimitado a sistemas sensibles. Esto incluye límites de lectura/escritura, restricciones de despliegue, control de acceso a repositorios y reglas sobre cuándo es obligatorio pedir aprobación humana.[^7][^8][^3]

Buenas prácticas de límites:[^8][^3]

- Definir modos de operación: modo "audit" o "read‑only" para análisis, y modo "change" o "write" limitado a áreas específicas del repositorio.
- Restringir acceso a secretos, entornos de producción y recursos financieros, usando herramientas especializadas para que el agente nunca manipule credenciales directamente.
- Documentar fronteras de responsabilidad para que el agente no intente resolver problemas fuera de su dominio (por ejemplo, un agente de código no debe gestionar políticas de negocio sin supervisión).

### 3.4 Verificación: priorizar checks deterministas

Los checks deterministas —tests unitarios y de integración, type checking, linters, escáneres de seguridad, reglas de arquitectura y políticas codificadas— son rápidos y repetibles, por lo que deberían ejecutarse en cada cambio generado por un agente. La verificación con IA se utiliza para aspectos menos estructurables, como coherencia de diseño, claridad de documentación o valoración de trade‑offs.[^2][^3][^8]

Buenas prácticas de verificación:[^3][^8]

- Integrar pipelines de CI que se ejecuten automáticamente cada vez que el agente crea un PR o commit.
- Bloquear merges si fallan tests, linters o reglas de seguridad/arquitectura definidas en policy‑as‑code.
- Diseñar self‑verification en skills: el agente debe revisar su propio output contra criterios explícitos antes de marcar la tarea como completada.

### 3.5 Aprendizaje: convertir fallos repetidos en mejoras de sistema

Cada error recurrente del agente —como usar mal un API, violar una restricción de módulo o desencadenar despliegues arriesgados— debería registrarse y dar lugar a mejoras del harness (nuevos tests, reglas, skills o documentación). El objetivo es que el sistema aprenda de forma acumulativa, reduciendo la probabilidad de repetir el mismo tipo de fallo a lo largo del tiempo.[^9][^7][^3]

Buenas prácticas de aprendizaje:[^8][^3]

- Mantener un registro de "fallos recurrentes" y asociar cada uno con una acción correctiva concreta.
- Versionar cambios en workflows de agentes y reglas del harness junto al código de la aplicación.
- Ejecutar agentes especializados de "doc‑gardening" que escanean documentación obsoleta y abren PRs para alinearla con el comportamiento real del sistema.[^9][^8]

## 4. Buenas prácticas de documentación para harnesses

### 4.1 Principios generales

La documentación en harness engineering busca que humanos y agentes tengan la misma visión compartida del sistema, incluyendo capacidades, límites y procesos. Los principios clave incluyen:[^2][^3]

- **Fuentes de verdad únicas**: evitar copias divergentes de reglas críticas; todo debe estar en archivos versionados en el repositorio principal.[^10][^2]
- **Entry‑points breves**: comenzar con documentos cortos que dirigen a secciones más detalladas, en vez de prompts monolíticos difíciles de mantener.[^3][^2]
- **Legibilidad para agentes**: usar estructuras y formatos que agentes puedan parsear fácilmente, como JSON, tablas y listas claras.[^4][^3]

### 4.2 Plantillas recomendadas

Recursos educativos sobre harness engineering para agentes de programación proporcionan plantillas listas para usar, incluyendo `CLAUDE.md`, `AGENTS.md`, archivos de skills y `feature_list.json`. Estas plantillas siguen convenciones de estructura que facilitan la vida a los agentes y permiten que distintos proyectos mantengan consistencia.[^10][^4][^2]

Elementos comunes de las plantillas:

- Secciones claras de objetivo, scope, reglas, herramientas permitidas y criterios de aceptación.
- Listas de skills con descripciones breves y enlaces a documentación extendida.
- Esquemas de datos (`feature_list.json`) que describen endpoints, parámetros y contratos.[^4][^3]

### 4.3 Documentación como código

Una línea de trabajo importante es tratar la documentación del harness como código: se versiona, se revisa en PRs, se prueba (por ejemplo, validando esquemas JSON) y se mantiene bajo estándares de calidad. Algunos sistemas incluyen agentes automáticos que detectan documentación desactualizada y generan PRs de actualización, integrando "doc‑gardening" en el ciclo de vida del software.[^9][^8][^2]

## 5. Técnicas de diseño de harness para agentes de código

### 5.1 Diseño orientado a comportamientos deseados

Una recomendación central es que cada componente del harness debe existir para asegurar un comportamiento concreto del agente; si no puede nombrarse ese comportamiento, el componente es probablemente innecesario. Este enfoque evita configuración decorativa y promueve un diseño más minimalista y efectivo.[^1][^2][^3]

Ejemplos de comportamientos y componentes:

- "El agente nunca despliega sin tests verdes" → hook de CI obligatorio antes del merge.
- "El agente no cruza límites de módulos" → reglas de arquitectura y linter que detectan dependencias prohibidas.
- "El agente siempre deja evidencia" → requisitos de logs y registros de artefactos.

### 5.2 Progressive disclosure de herramientas y contexto

Para evitar sobrecargar al agente con demasiada información y opciones, se recomienda usar progressive disclosure: solo se exponen herramientas y bloques de contexto cuando la tarea los requiere. Esto se logra mediante orquestadores que administran qué skill se activa y qué documentos se anexan en cada paso de un workflow.[^1][^2][^3]

Esta técnica mejora la precisión de los agentes y reduce la probabilidad de que usen herramientas incorrectas o información irrelevante.[^8][^3]

### 5.3 Orquestación de subagentes

En sistemas más complejos, el harness define múltiples subagentes especializados (por ejemplo, uno para refactorización, otro para testing, otro para documentación), coordinados mediante reglas en `AGENTS.md` y lógica de orquestador. Cada subagente tiene su propio scope, herramientas y criterios de éxito, lo que permite dividir tareas grandes en etapas más manejables y controlables.[^1][^4][^3]

### 5.4 Integración con CI/CD y sistemas de tickets

Harness engineering se conecta naturalmente con pipelines de CI/CD, herramientas de issue tracking y sistemas de revisión de código. El agente puede abrir issues, crear PRs, lanzar builds y ejecutar tests, pero siempre bajo reglas estrictas definidas por el harness.[^11][^3][^8]

Técnicas comunes incluyen:

- Agents que actúan como "pair programmers" generando propuestas que luego son revisadas.
- Agents que automatizan tareas repetitivas de mantenimiento (actualización de dependencias, refactorizaciones mecánicas) bajo verificación fuerte.
- Agents que monitorizan documentación y estados de sistemas para sugerir mejoras o alertar sobre incoherencias.[^9][^8]

## 6. Reglas operativas para harnesses en producción

### 6.1 Políticas de cambio y despliegue

Artículos sobre harness engineering enfatizan que ningún cambio generado por agentes debería llegar a producción sin pasar por pruebas automáticas y al menos una verificación humana en puntos críticos. Las reglas operativas típicas incluyen:[^3][^8]

- Requerir tests verdes y linters sin errores antes del merge de PRs generados por agentes.
- Aplicar políticas de seguridad y arquitectura codificadas que bloqueen cambios peligrosos.[^8][^3]
- Establecer niveles de aprobación humana según el riesgo del cambio (por ejemplo, cambios en lógica de pagos requieren revisión de un experto de negocio).

### 6.2 Observabilidad y trazabilidad

Cada acción del agente debe dejar evidencia trazable: logs estructurados, diffs, PRs, resultados de pruebas y artefactos de documentación generados. Esto permite auditar comportamiento, investigar incidentes y entender cómo el agente llegó a ciertas decisiones.[^6][^1][^3][^8]

Sistemas avanzados de harness engineering integran dashboards que muestran actividad de agentes, latencia, coste, tasa de éxito y tipos de errores más comunes, facilitando la mejora iterativa.[^11][^3]

### 6.3 Gestión de estándares de calidad

Los estándares de calidad deben estar escritos, versionados y accesibles para todo el equipo, incluyendo los agentes. Esto abarca guías de estilo de código, requisitos de documentación, políticas de seguridad, objetivos de rendimiento y criterios de diseño.[^2][^8]

El harness usa estos estándares como referencia para verificación: tests, linters, policies y evaluaciones de IA alineadas con estos documentos.[^3][^8]

### 6.4 Revisión y limpieza continua del harness

Dado que los modelos de IA y los sistemas evolucionan, el harness no puede ser estático; debe revisarse periódicamente para eliminar componentes que ya no aportan valor o que generan fricción innecesaria. Esta "limpieza" evita acumulación de reglas obsoletas, prompts excesivos y documentos redundantes.[^1][^3]

## 7. Aplicaciones prácticas y recomendaciones accionables

### 7.1 Proyectos de SaaS y APIs

Para aplicaciones SaaS con fuerte componente de API y automatización, harness engineering puede enfocarse en:[^2][^3]

- Documentar contratos de APIs en formatos legibles para agentes (OpenAPI, JSON, tablas) y enlazarlos desde skill files.
- Definir skills para cambios de endpoints, gestión de versiones y análisis de impacto.
- Integrar agentes en procesos de mantenimiento rutinario como actualización de dependencias y refactorización de módulos de autenticación.

### 7.2 Plataformas de e‑commerce

En plataformas de e‑commerce, el harness puede incluir skills específicos para gestión de catálogo, pricing, promociones, análisis de conversión y monitoreo de integraciones con pasarelas de pago. Los límites de permisos son especialmente importantes para evitar que agentes afecten inventario, precios o datos de clientes sin control adecuado.[^6][^8][^3]

### 7.3 Automatización de negocio y workflows

Para workflows de negocio (por ejemplo, CRM, soporte, backoffice financiero), el harness describe procesos de punta a punta: qué eventos disparan agentes, qué datos pueden leer, qué acciones pueden ejecutar y qué evidencia deben registrar. Skills bien definidos permiten que agentes manejen tareas repetitivas con baja variabilidad, mientras que decisiones complejas quedan reservadas para humanos.[^7][^8][^3]

### 7.4 Recomendaciones prácticas generales

Basado en cursos y artículos sobre harness engineering, un plan práctico inicial incluye:[^4][^2][^3]

1. Crear `CLAUDE.md` y `AGENTS.md` con roles, límites y objetivos claros.
2. Definir 3–5 skills críticos para tareas frecuentes y de alto impacto (por ejemplo, "modificar API", "nueva feature pequeña", "investigar bug").
3. Configurar herramientas mínimas pero bien definidas (repos, tests, logs, APIs clave) y exponerlas mediante servidores MCP.
4. Montar CI que ejecute tests y linters obligatorios en cada PR generado por agentes.
5. Registrar errores recurrentes y traducirlos en mejoras del harness.

## 8. Conclusiones

Harness engineering representa un cambio de enfoque respecto al uso tradicional de modelos de IA: ya no se trata de "hacer prompts mejores", sino de diseñar sistemas completos de soporte que conviertan la capacidad del modelo en entrega de software y operaciones de negocio confiables. La disciplina se apoya en principios de arquitectura de software, seguridad, observabilidad y aprendizaje continuo, aplicados específicamente a agentes de IA.[^6][^7][^3]

La documentación estructurada (por ejemplo, `CLAUDE.md`, `AGENTS.md`, skill files, `feature_list.json`), límites claros de permisos, verificación determinista sistemática y mecanismos de aprendizaje son los pilares que permiten que agentes de IA trabajen en producción sin convertir el sistema en una caja negra incontrolable. Adoptar estas prácticas en proyectos de SaaS, e‑commerce y automatización proporciona una base sólida para escalar el uso de agentes de IA manteniendo calidad, seguridad y trazabilidad.[^4][^8][^2]

---

## References

1. [Ingeniería de arneses de agentes](https://addyosmani.com/blog/agent-harness-engineering/) - La anatomía de un arnés de agente. El modelo se sitúa en el centro;. Concretamente, un arnés incluye...

2. [Harness Engineering — Índice - Tutoriales de Tecnologías](https://siemprelisto.cl/tecnologias/harness-engineering/00-indice/) - Tutorial sobre harness engineering: la disciplina que estudia cómo construir la infraestructura de s...

3. [Harness Engineering Is the Operating System for AI ...](https://hackernoon.com/harness-engineering-is-the-operating-system-for-ai-software-delivery) - Harness engineering turns AI coding agents into reliable software delivery systems through context, ...

4. [Bienvenido a Learn Harness Engineering](https://walkinglabs.github.io/learn-harness-engineering/es/) - Learn Harness Engineering es un curso dedicado a la ingeniería de agentes de programación con IA. He...

5. [Harness Engineering para agentes IA en producción](https://newsletter.arquitecturasoftware.com/p/harness-engineering-ia) - El harness engineering es la disciplina de diseñar los sistemas, restricciones arquitectónicas, ento...

6. [Harness-Engineering: la arquitectura de sistemas que ...](https://morethandigital.info/es/harness-engineering-la-arquitectura-de-sistemas-que-potencia-la-eficacia-de-los-agentes-de-ia/) - La ingeniería de harnesses define las reglas, el contexto y la retroalimentación para los agentes de...

7. [Qué es el AI harness y el harness engineering](https://www.webreactiva.com/blog/ai-harness) - Harness engineering (2026): diseñar el entorno completo donde los agentes trabajan, con restriccione...

8. [Ingeniería de arnés: ¿Qué significa para el control de ...](https://testcollab.com/blog/harness-engineering) - Aquí se presenta un marco práctico para aplicar los principios de ingeniería de arnés a su proceso d...

9. [Harness engineering: leveraging Codex in an agent-first ...](https://openai.com/index/harness-engineering/) - A recurring “doc-gardening” agent scans for stale or obsolete documentation that does not reflect th...

10. [walkinglabs/learn-harness-engineering](https://github.com/walkinglabs/learn-harness-engineering) - Learn Harness Engineering is a course dedicated to the engineering of AI coding agents. We have deep...

11. [Harness: AI for DevOps, Testing, AppSec, and Cost Optimization](https://www.harness.io/) - Harness is a unified, end-to-end AI software delivery platform to manage the SDLC using purpose-buil...

