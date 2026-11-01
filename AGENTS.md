# Instrucciones para agentes

> Si una instrucción de la sección `## graphify` se solapa o contradice otra de este archivo, prevalece lo que indica graphify. Esto no exime de consultar el paper o `docs/IDEA.md` cuando el trabajo lo requiera.

## Objetivo y fuente de verdad

Replicar en Python el modelo matemático del paper oficial de Acuna et al. (2021), *Operational Planning of Energy for Non-Interconnected Zones: A Simulation-Optimization Approach and a Case Study to Tackle Energy Poverty in Colombia*, DOI: https://doi.org/10.3390/en14102789.

- Antes de implementar o modificar el modelo, consultar las ecuaciones, los algoritmos y los supuestos correspondientes en el documento oficial. Si no está disponible, solicitarlo antes de definir el comportamiento matemático.
- Mantener correspondencia entre el paper y los conjuntos, índices, parámetros, variables, función objetivo, restricciones y algoritmos implementados. Referenciar la ecuación o sección cuando ayude a verificar esa correspondencia.
- Consultar `docs/IDEA.md` al trabajar en el alcance experimental o sus adaptaciones. Es una propuesta del proyecto; el paper es la autoridad para la reproducción del modelo original.
- Distinguir explícitamente la reproducción original de las adaptaciones del proyecto. Registrar los supuestos y las diferencias; ante una ambigüedad que cambie el modelo, aclararla antes de implementarla.

## Implementación y verificación

- Priorizar código legible, directo y suficientemente sencillo para explicar el modelo en un contexto académico. El objetivo es una réplica matemática, no una aplicación de producción.
- Usar funciones pequeñas, nombres relacionados con la notación del paper y estructuras de datos simples. Reutilizar el código y las dependencias existentes; añadir capas o dependencias solo cuando sean necesarias para el trabajo solicitado.
- Hacer explícitos las unidades, los dominios, los límites y las condiciones iniciales. Identificar los datos sintéticos y los parámetros asumidos.
- Verificar cada cambio matemático con un caso pequeño comprobable a mano o una prueba pertinente. Comprobar las restricciones y los balances afectados; comparar con los resultados del paper cuando estén disponibles, documentando tolerancias y diferencias.
- Registrar los datos, los parámetros y las semillas necesarios para reproducir los experimentos. Informar qué se verificó y qué queda pendiente, sin presentar supuestos como resultados validados.

## Commits

- Crear un commit por funcionalidad o cambio coherente y verificable; la unidad no tiene que coincidir con una historia de usuario.
- Incluir en el mismo commit el código, las pruebas y la documentación necesarios para ese cambio. Mantener separados los cambios independientes y respetar el trabajo ajeno.
- Usar el formato `tipo: descripción`, con una descripción breve y concreta en español. Elegir el tipo según el cambio: `feat`, `fix`, `docs`, `refactor`, `test` o `chore`.
- Ejemplos: `feat: implementa el balance de energía`, `fix: corrige el límite de carga de la batería`, `docs: documenta los supuestos del modelo`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
