# Instrucciones para agentes

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
