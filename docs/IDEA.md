# IDEA.md - Propuesta para Optimización 1

## Valor de los pronósticos de Chronos-2 en la planificación energética fotovoltaica con MPC heurístico

**Caso:** simulación de un sistema fotovoltaico aislado hipotético, contextualizado en una escuela rural de Támesis, Antioquia.  
**Versión:** 2, revisión del 17 de octubre de 2026, hora de Colombia.  
**Estado:** protocolo recomendado para implementar. No contiene resultados experimentales ni certifica que los datos escolares hayan sido verificados.  
**Alcance:** investigación computacional; no instalación física, estudio económico ni diagnóstico de electrificación de la escuela.

**Pregunta:** manteniendo el mismo sistema físico, demanda, generador de trayectorias y controlador, ¿cambia la satisfacción ponderada de demanda cuando las distribuciones de irradiancia basadas en beta se sustituyen por pronósticos de Chronos-2?

La comparación principal tiene exactamente dos alternativas:

| Alternativa | Componente diferente | Componentes compartidos |
|---|---|---|
| A: referencia del paper adaptada a MPC | Distribuciones beta condicionadas por hora | Demanda, física, construcción de trayectorias, heurística, selección de planes, MPC y evaluación |
| B: híbrido Chronos-2 + MPC | Pronósticos de cuantiles de Chronos-2 | Los mismos componentes de A |

**Cambio metodológico relevante frente a la primera versión:** se mantiene el núcleo constructivo de despacho y selección de cargas, con interpretaciones físicas documentadas. Se incorpora a ambas ramas una búsqueda pequeña de reservas de batería y una selección por desempeño esperado. Esto permite aprovechar el horizonte futuro. Reemplaza, en el experimento principal, la selección modal del paper. Es una adaptación explícita; A no es una reproducción literal del artículo.

## 1. Problema, contribución y objetivos

El artículo de Acuna et al. [1] combina simulación de radiación solar con una heurística de despacho y encendido de cargas para la escuela Volcanes, en Caparrapí, Cundinamarca. La propuesta conserva ese problema operativo y lo contextualiza en Támesis.

La contribución consiste en evaluar el valor de la información predictiva para tomar decisiones energéticas. Chronos-2 pronostica irradiancia; el controlador decide cómo utilizar la energía. La novedad propuesta es la integración y su evaluación controlada, sin afirmar que sea la primera aplicación de este tipo.

**Objetivo general:** evaluar el efecto de sustituir una referencia probabilística beta por Chronos-2 zero-shot en un sistema compartido de planificación fotovoltaica y MPC heurístico, bajo demanda programada y condiciones meteorológicas históricas de referencia.

**Objetivos específicos:**

1. Auditar los registros diarios de consumo y construir una serie meteorológica trazable.
2. Construir perfiles de demanda por hora y carga, identificando todos los supuestos.
3. Implementar la física del sistema y una interpretación verificable de los algoritmos del paper.
4. Implementar dos proveedores de distribuciones de irradiancia con una interfaz común.
5. Integrarlos con el mismo generador de trayectorias y controlador de horizonte móvil.
6. Comparar precisión, energía atendida, cargas críticas y costo de cómputo en un test reservado.
7. Entregar código reproducible, artículo y exposición conforme a los lineamientos [2].

**Hipótesis:** B puede mejorar el pronóstico y la satisfacción ponderada respecto a A. Son resultados distintos: mejorar MAE no implica mejorar decisiones. También puede no haber mejora o existir deterioro. La factibilidad física es un requisito de ambos métodos, no un beneficio que se atribuirá a Chronos-2.

## 2. Datos reales, simulados y disponibles al decidir

| Elemento | Condición en este protocolo |
|---|---|
| Consumo diario escolar | La propuesta original declara disponer de registros de pocas semanas; no se adjuntaron en esta revisión y deben auditarse |
| Demanda horaria y por dispositivo | Sintética, construida con plantillas y un catálogo hipotético |
| Irradiancia histórica | Por obtener; será referencia de un producto meteorológico, no medición en la escuela |
| Infraestructura, prioridades y estado inicial | Supuestos de simulación |
| Pronósticos | Salidas de los dos métodos; nunca sustituyen la referencia usada para evaluar |
| Resultados operativos reales | No disponibles |

### 2.1. Demanda conocida como supuesto del experimento

El experimento principal considera **demanda programada conocida durante el horizonte**. Se crea una agenda sintética de solicitudes antes del backtest, utilizando el conjunto de registros escolares destinado a construir perfiles. Se entrega la misma agenda a A y B.

Esta elección aísla la incertidumbre solar. No implica que el consumo real de mañana sea conocido hoy. Si se utilizan totales históricos de los días de prueba para reconstruir su demanda y luego se entregan completos al planificador, el resultado debe describirse como evaluación **condicionada a conocimiento perfecto de demanda**, no como operación realista sin información futura.

Si los registros escolares y la meteorología corresponden a periodos diferentes, se construye un caso semisintético: no se atribuye una medición escolar a una fecha en la que no existió. La ausencia de registros impide afirmar que el caso está anclado en consumo real; solo permitiría un prototipo sintético claramente identificado.

### 2.2. Desagregación conservando energía

Para un total diario D_d en Wh:

\[
w_{d,t}\geq0,\qquad \sum_{t\in d}w_{d,t}=1,\qquad
D_{d,t}=D_dw_{d,t}.
\]

Se usan plantillas lectivas y no lectivas. El catálogo por dispositivos debe conciliar con ese total. Si la combinación de equipos binarios y horas de uso no permite reproducirlo, se informa el residuo o se añade una carga de fondo explícita. No se cambian arbitrariamente las potencias nominales para aparentar mediciones por equipo.

El consumo medido es energía efectivamente utilizada, y no identifica por sí solo necesidades no atendidas, demanda latente ni aparatos. El proyecto lo adopta como **proxy de demanda**, con esa limitación.

## 3. Fidelidad al paper y registro de adaptaciones

| Elemento de [1] | Tratamiento |
|---|---|
| Despacho y selección on/off, algoritmos 1 y 2, p. 8 | Núcleo heurístico compartido; se documentan ordenamiento, acumuladores y desempates |
| Objetivo ponderado, ecuación (10), p. 6 | Se conserva para medir servicio; se distingue del criterio interno del MPC |
| Restricciones, pp. 5-6 | Se reemplazan ambigüedades por una especificación física consistente |
| Beta4 y Monte Carlo, pp. 6 y 12 | Se reestiman con datos de Támesis; no se copian parámetros de Volcanes |
| Solución más frecuente y similitud del 90 %, p. 6 | Referencia metodológica; no selector principal de la versión reformulada |
| MPC, p. 14 | Ampliación propuesta por los autores, no implementada en sus experimentos |
| Resultados de servicio, pp. 10 y 13 | No son metas garantizadas ni comparables directamente con otra definición de servicio |

**Correcciones que deben quedar en el registro de implementación:**

- En la ecuación (4), al fijar E=Q=1, la expresión impresa implica BD ≥ C-c_min, mientras que el texto describe una cota superior de descarga. Implementaremos la cota física de la sección 7; no copiaremos esa desigualdad.
- La ecuación (9), X≤N, mezcla la decisión binaria con una demanda energética. Utilizaremos un indicador de solicitud r y X≤r.
- El estado disponible antes de actuar será C_t; el estado después será C_(t+1). Se mantiene continuidad entre días.
- Los algoritmos usan símbolos de oferta y criterios de ordenamiento que requieren interpretación. Se registrará cada interpretación y se contrastará con ejemplos manuales.
- El paper incluye valores de radiación expresados en Wh y una cifra de área de panel ambigua. No se tomarán como GHI en W/m² ni como especificaciones de equipos de Támesis.

La comparación del paper con un método exacto no certifica óptimos: su sección 4.1 informa soluciones enteras obtenidas bajo límite de tiempo. No se afirmará que una heurística superó el óptimo global.

## 4. Diseño experimental y alcance de la atribución

Solo se cambia el proveedor de distribuciones marginales de irradiancia. Ambos métodos comparten:

- Historial admisible, fechas, unidades, calendario y reglas de disponibilidad.
- Demanda, infraestructura, prioridades y estado inicial de cada episodio.
- Biblioteca de dependencia temporal, números aleatorios subyacentes y cantidad de escenarios.
- Generación de candidatos, criterio de selección, corrección física, horizonte y replanificación.
- Datos de evaluación y reglas de cómputo de métricas.

**Los estados posteriores pueden diferir:** cada método conserva la batería resultante de sus propias decisiones. Igualar nuevamente los estados en cada replanificación borraría parte del efecto que se quiere medir.

El resultado estima el efecto de cambiar el **módulo de pronóstico dentro de este controlador y estos supuestos**. No separa todos los componentes internos de Chronos-2 ni cuantifica por separado el beneficio de añadir MPC. Con dos ramas que ya tienen MPC, no se puede afirmar cuánto mejora MPC frente a operar sin él.

El experimento principal no dará a B covariables meteorológicas adicionales que A no recibe. Se comienza con GHI histórica y calendario. Una ampliación con pronósticos meteorológicos externos se declararía como cambio del paquete informativo.

## 5. Arquitectura e interfaces

| Módulo | Responsabilidad principal |
|---|---|
| data | Ingesta, unidades, intervalos, disponibilidad, procedencia y cortes temporales |
| demand | Catálogo, prioridades y agenda sintética |
| forecasting | Proveedores beta y Chronos-2 con el mismo contrato |
| scenarios | Distribuciones marginales más una biblioteca temporal compartida |
| physics | Generación FV, batería, ejecución y balance |
| heuristics | Construcción de candidatos con el núcleo del paper |
| mpc | Evaluación de candidatos, selección y avance temporal |
| evaluation | Backtest pareado, métricas e intervalos de confianza |
| plots | Figuras para el artículo y la exposición |

Se recomienda un paquete Python bajo src/energy_project, configuraciones en configs, pruebas en tests, resultados en results y decisiones metodológicas en docs.

**Contratos mínimos:**

- Forecast: origen, intervalos objetivo, cuantiles, unidades, modelo/revisión, ventana de contexto y duración.
- ScenarioSet: matriz N×H, pesos 1/N, origen, identificadores de plantillas y semilla.
- Plan: matriz H×K de compromisos binarios, candidato/reserva y flujos previstos.
- Execution: compromisos, cargas atendidas, flujos realizados, batería, faltantes y motivo de corrección.
- Run: hashes de datos/configuración/código, versiones, hardware, semilla y estado final.

No se necesita webapp, base de datos, Django ni LangGraph. Un comando reproducible que produzca tablas y gráficos cubre el propósito académico.

## 6. Meteorología, tiempo y validación

**Fuente inicial:** Open-Meteo Historical Weather API [6], seleccionando y fijando el producto meteorológico. NASA POWER [7] queda como alternativa si la cobertura es insuficiente.

La documentación de Open-Meteo describe datos de reanálisis y shortwave_radiation como promedio de la hora precedente. NASA POWER ofrece UTC y hora solar local; se solicitará UTC para evitar confundir esta última con America/Bogota. Se conservarán unidad y metadatos originales [6,7].

### 6.1. Convención temporal

Cada registro normalizado tiene interval_start, interval_end y available_at, además de valor, unidad, fuente y bandera de calidad. Una observación puede entrar al contexto solo cuando su intervalo terminó y su fecha de disponibilidad no supera el origen del pronóstico.

Cuando no exista información de publicación histórica, se declara **disponibilidad idealizada de la observación pasada**. Un reanálisis retrospectivo puede incorporar información y revisiones posteriores; usarlo como referencia no demuestra desempeño de un sistema en tiempo real.

Se almacenan tiempos inequívocos en UTC y se interpretan calendario escolar y franjas solares en America/Bogota. La máscara nocturna usa el intervalo completo: una hora que contiene amanecer no es enteramente nocturna.

### 6.2. Validaciones

Unidades explícitas; intervalos sin duplicados; cobertura; faltantes; no negatividad; consistencia solar; separación entre GHI y POA; ausencia de interpolación que use información futura en el contexto. Los límites solares no se fijan automáticamente en 1000 W/m²: ese valor es referencia STC, no una cota universal de irradiancia.

### 6.3. Separación temporal

1. Historia inicial para ajustar beta y alimentar Chronos-2.
2. Calibración/desarrollo para elegir ventanas, evaluar cobertura, construir dependencia y fijar parámetros.
3. Test final reservado, continuo y común.

Se buscará al menos un año de historia y varias semanas de desarrollo y test si la fuente lo permite. Las fechas se fijan después de auditar cobertura, antes de examinar comparaciones finales. Con menos datos se reduce el alcance inferencial.

En el test se permite añadir al contexto los valores pasados que ya terminaron. No se permite retocar hiperparámetros, perfiles, tamaños FV o la biblioteca de calibración al observar quién gana.

## 7. Formulación física y objetivo

### 7.1. Variables y unidades

Se propone un banco agregado de baterías para el MVP. Su equivalencia es una simplificación de un conjunto homogéneo; no modela desbalances entre baterías.

| Símbolo | Significado | Unidad |
|---|---|---|
| Δt | Duración del intervalo | h |
| D_(k,t) | Energía solicitada por una carga | Wh |
| r_(k,t) | Solicitud de uso | 0/1 |
| x_(k,t) | Compromiso planificado | 0/1 |
| z_(k,t) | Atención realizada del intervalo en el modelo agregado | 0/1 |
| p_(k,t) | Peso de prioridad | Adimensional |
| G_t | Irradiancia media | W/m² |
| E_t^PV | Energía FV disponible en el bus de cargas | Wh |
| c_t, d_t, v_t | Carga de batería, descarga al bus y vertido | Wh |
| C_t | Energía almacenada al inicio | Wh |

Se exige 0≤z_(k,t)≤x_(k,t)≤r_(k,t). La energía atendida es D_(k,t)z_(k,t). No se confunde energía comprometida con energía efectivamente servida.

### 7.2. Generación FV

Para el MVP se adopta un arreglo horizontal equivalente, por lo que POA=GHI como **supuesto geométrico explícito**, con factor de desempeño constante f:

\[
P_t^{PV}=\min\left(P_{\mathrm{inv,max}},
P_{\mathrm{STC}}\frac{G_t}{1000\ {\rm W/m^2}}f\right),\qquad
E_t^{PV}=P_t^{PV}\Delta t.
\]

La potencia nominal ya representa conversión eléctrica; no se multiplica otra vez por la eficiencia nominal del módulo. El factor f incluye pérdidas FV/inversor definidas en configuración, sin duplicar las pérdidas de la batería.

Una versión con inclinación, transposición y temperatura puede usar pvlib [8]. Sería común a A y B; no se necesita para el primer experimento.

### 7.3. Balance del bus y batería

\[
\sum_k D_{k,t}z_{k,t}+c_t+v_t=E_t^{PV}+d_t,
\qquad
C_{t+1}=C_t+\eta_c c_t-\frac{d_t}{\eta_d}.
\]

\[
C_{\min}\le C_t\le C_{\max},\quad c_t,d_t,v_t\ge0,
\]

\[
c_t\le\min\left(P_{c,\max}\Delta t,
\frac{C_{\max}-C_t}{\eta_c}\right),\qquad
d_t\le\min\left(P_{d,\max}\Delta t,
\eta_d(C_t-C_{\min})\right).
\]

No se permite carga y descarga simultánea del banco. Se especifica si los límites de potencia están medidos en el bus o en la batería; estas ecuaciones usan límites en el bus. También se impone la capacidad de suministro a cargas P_bus,max·Δt. Se empieza con flujos de un bus equivalente; no es un diseño eléctrico detallado.

### 7.4. Objetivo energético

\[
J=\sum_{t,k}p_{k,t}D_{k,t}z_{k,t},
\qquad
S_{\mathrm{pond}}=\frac{J}{\sum_{t,k}p_{k,t}D_{k,t}}.
\]

Si el denominador es cero se informa no aplicable. Se usan prioridades iniciales 1, 2 y 3. Un peso 3 no garantiza prioridad absoluta frente a cualquier cantidad de energía de peso 1: el objetivo pondera energía. Por eso se mide por separado el servicio de cargas declaradas críticas.

No se exige C_final=C_inicial como restricción dura: en episodios de escasez podría ser inviable. Se reporta el estado final y se incluye el tratamiento terminal de la sección 11.

## 8. Heurística compartida y corrección de ejecución

**Orden propuesto:** prioridad descendente, energía solicitada descendente e identificador ascendente para desempatar. Es una interpretación documentada de la descripción del algoritmo 2, no una transcripción inequívoca.

Para cada intervalo de un candidato:

1. Calcular generación prevista y energía útil de batería.
2. Ordenar únicamente las cargas solicitadas.
3. Admitir cada carga si cabe completa en el presupuesto y respeta los límites.
4. Aplicar, para cargas no críticas, la reserva operativa del candidato.
5. Calcular los flujos a partir de las cargas efectivamente seleccionadas.
6. Cargar excedentes y contabilizar vertido; actualizar batería una sola vez.

La prioridad 3 puede utilizar energía por encima del mínimo físico aunque exista una reserva operativa para las otras cargas. La reserva no representa energía adicional ni permite violar C_min.

**Ejecución común:** dado un compromiso fijo y la generación realizada, se atienden las cargas comprometidas por el mismo orden mientras sea físicamente posible; se desconectan las restantes. No se añaden nuevas cargas fuera del plan antes de replanificar. La batería se actualiza con lo realmente atendido.

El replay horario utiliza una corrección ideal dentro del intervalo. Es una aproximación agregada de protección y corte de cargas; no demuestra continuidad de servicio segundo a segundo, transitorios, arranques ni tiempos reales de desconexión. La irradiancia realizada nunca entra a la función que construye el compromiso anterior.

Se registra energía solicitada, comprometida y atendida; violaciones de planes contra sus propios escenarios; incompatibilidades al ejecutarlos con otra realización; y errores físicos después de la corrección. Estos últimos deben ser cero dentro de tolerancia.

## 9. Modelo A: referencia beta

Se ajustan distribuciones a irradiancia pasada, condicionando inicialmente por hora local. La noche completa es una masa en cero, no una beta continua. Los ceros diurnos pueden representarse como una masa adicional más una beta para valores positivos.

**Preferencia de fidelidad:** beta de cuatro parámetros, con dos parámetros de forma, localización y escala; límites admisibles, convergencia y tamaño de muestra registrados. El ajuste no puede usar la irradiancia futura ni parámetros copiados de Cundinamarca.

Se propone una ventana de hasta 365 días y un mínimo inicial de 30 observaciones útiles por grupo. Ambos son parámetros de desarrollo. Si falta muestra, se agrupan franjas mediante una regla fijada de antemano. Una serie constante requiere tratamiento degenerado explícito.

Si el ajuste beta4 es inestable, puede adoptarse una beta escalada con soporte fijado y dos formas estimadas. Se denominará **beta escalada de dos formas**, no ajuste beta4 libre. Esta decisión se toma antes del test y se registra en el nombre/configuración del experimento. Un fallo no puede sustituirse silenciosamente por otro método.

El proveedor devuelve cuantiles; no contiene un muestreador temporal propio. De esta forma la dependencia entre horas se construye en el módulo común.

## 10. Modelo B: Chronos-2 y escenarios compartidos

### 10.1. Pronóstico

Se utilizará el checkpoint oficial amazon/chronos-2, sin ajustar pesos. La documentación confirma pronósticos por cuantiles, tareas univariadas/con covariables e inferencia en CPU o GPU [3,4]. No se requiere contratar un servicio de AWS para la inferencia local.

El objetivo inicial es GHI horaria. Se comienza sin variables meteorológicas futuras. Los hiperparámetros propuestos son contexto de 336 horas, horizonte de 24 horas y cuantiles 0.1, 0.2, ..., 0.9; se validan antes de congelar el experimento.

El modelo se carga una vez y se ejecuta una vez por origen. Se fija la revisión del checkpoint y las versiones instaladas. Zero-shot significa que el proyecto no ajusta sus pesos; no prueba que ninguna serie similar estuviera en el preentrenamiento. Se informa esa limitación y se prefieren, si hay cobertura, fechas posteriores a la publicación del checkpoint.

Se conservan salidas originales y procesadas. La corrección común impone no negatividad, máscara nocturna y monotonía cuantílica; registra cuánto cambia cada salida. La mediana se usa como predicción puntual para ambos métodos.

### 10.2. Dependencia temporal común

Los cuantiles por hora no constituyen automáticamente trayectorias conjuntas. La versión principal utilizará **la misma biblioteca de rangos históricos** para A y B, inspirada en métodos de reordenamiento por dependencia empírica [10].

1. Construir M bloques históricos completos de longitud H en desarrollo/calibración, alineados con la hora de origen. Fijar su selección antes del test.
2. Para cada posición h, obtener el rango de cada bloque entre los M valores. Resolver empates de forma reproducible.
3. Convertir rangos en U_(j,h)=(rango_(j,h)-0.5)/M, con rangos entre 1 y M.
4. Muestrear N índices de bloques con una semilla por origen. Compartir **la misma matriz U** entre A y B.
5. Para cada modelo m, aplicar G_(m,s,h)=Q_(m,h)(U_(s,h)), donde Q es su función cuantil interpolada.

La matriz U aporta una dependencia empírica común. Las magnitudes dependen del predictor. No garantiza meteorología perfecta; se revisan rampas, correlaciones y energía solar diaria.

Para el MVP probabilístico se interpolan linealmente los cuantiles interiores. Las colas se completan explícitamente con extremos operativos 0 y G_cap, común a ambos; G_cap se fija con información de desarrollo y una revisión de plausibilidad. Es una aproximación adicional de las colas, no una predicción emitida por Chronos ni una beta4 exacta tras discretización. Se registran clipping y sensibilidad a ese límite si afecta decisiones.

La misma regla de interpolación, tratamiento de noche y validación se aplica a ambas ramas. Se medirá cobertura antes y después de construir escenarios. No se afirmará calibración estadística solo por haber generado muestras. Una recalibración marginal posterior, si se necesita, debe usar la misma familia de procedimiento en ambos modelos y únicamente desarrollo.

Las curvas p10/p50/p90 pueden servir para depurar interfaces, pero no son tres realizaciones equiprobables ni sustituyen este protocolo en los resultados principales.

## 11. MPC heurístico con una decisión anticipatoria explícita

El MPC planifica un horizonte, ejecuta una parte y vuelve a planificar con el estado realizado [9]. No exige necesariamente resolver un MILP, pero aquí debe existir una búsqueda que utilice el efecto futuro de las decisiones.

### 11.1. Parámetros iniciales

| Parámetro | Propuesta |
|---|---|
| Intervalo Δt | 1 hora |
| Horizonte H | 24 intervalos |
| Replanificación R | 6 intervalos |
| Escenarios N | 50 |
| Reservas candidatas ρ | 0, 0.25, 0.50 y 0.75 de la capacidad utilizable |
| Reserva operativa | C_min + ρ(C_max-C_min), solo para cargas no críticas |
| Valor terminal λ_T | Inicialmente 0.5 veces la prioridad mínima; fijar en desarrollo |

R=6 es control por bloques, con corrección física horaria. R=1 queda como sensibilidad si el costo de inferencia lo permite. Estos valores son propuestas, no parámetros estimados.

### 11.2. Construcción y selección

En cada origen:

1. Obtener historia admisible y estado actual de la rama.
2. Obtener los cuantiles y N escenarios con el módulo común.
3. Construir cuatro planes completos mediante la heurística sobre la trayectoria mediana, uno por reserva candidata.
4. Reproducir **cada mismo plan** bajo todos los escenarios, utilizando la misma física y corrección.
5. Seleccionar el candidato de mayor valor esperado; desempatar por menor reserva y luego identificador estable.
6. Ejecutar únicamente los primeros R compromisos contra la referencia meteorológica reservada.
7. Conservar el estado realizado y repetir.

El criterio interno es:

\[
\mathrm{Score}(a)=\frac{1}{N}\sum_{s=1}^{N}
\left[\sum_{h,k}p_{k,h}D_{k,h}z^{(s,a)}_{k,h}
+\lambda_T\eta_d\big(C_H^{(s,a)}-C_{\min}\big)\right].
\]

El valor terminal evita tratar toda energía restante como inútil al cerrar el horizonte. Es una extensión al objetivo del paper y no una garantía de estabilidad. Se reporta separado de la métrica principal J, que mide solo energía atendida. No se interpreta Score como ahorro económico.

Cada candidato mantiene los mismos compromisos en todos los escenarios; solo cambian las consecuencias de la corrección. Se selecciona un único candidato **antes** de revelar la realización. No se elige retrospectivamente el plan que habría convenido para cada futuro.

La búsqueda es pequeña y aproximada: no pretende optimizar todos los calendarios binarios ni resolver el MPC estocástico global. Su costo de evaluación crece aproximadamente como candidatos×N×H×cargas.

### 11.3. Prueba de anticipación

Caso artificial de dos horas: batería útil de 1 Wh, sin sol en la primera hora, una carga actual de 1 Wh con prioridad 1 y una carga de 1 Wh con prioridad 3 en la siguiente.

- Si la próxima hora no tendrá sol, reservar batería permite obtener servicio ponderado 3 en vez de 1.
- Si tendrá 1 Wh solar, atender la carga actual permite obtener 4 en vez de 3.

La primera decisión debe poder cambiar cuando cambia solo el pronóstico de la segunda hora. Es una prueba manual de mecanismo, no un resultado de Támesis. Una heurística puramente voraz que siempre atienda el presente puede fallar esta prueba aunque se la ejecute repetidamente.

### 11.4. Fidelidad y alcance

La reserva y la selección por Score se aplican idénticamente en A y B. Mantienen el núcleo heurístico, pero amplían el procedimiento de [1]. Si se decide conservar literalmente la selección modal y ninguna decisión de reserva, debe denominarse replanificación de una heurística secuencial y reducirse la afirmación de anticipación.

No se afirma factibilidad robusta para todo futuro ni garantía de servicio de cargas críticas. La seguridad del modelo proviene de límites físicos y de permitir demanda no atendida.

## 12. Evaluación

### 12.1. Pronóstico

MAE y RMSE de la mediana; pinball loss en la misma malla de cuantiles; cobertura y amplitud de p10-p90; correcciones físicas. Separar horas diurnas y conjunto completo para que las noches no oculten errores.

Para horizontes solapados se evalúa por adelanto y origen. Los intervalos objetivo deben ser los mismos en A y B. No se mezclan errores de diferentes adelantos sin indicar la agregación.

### 12.2. Servicio realizado

| Indicador | Definición |
|---|---|
| Principal | S_pond: energía solicitada efectivamente atendida, ponderada por prioridad |
| Energía no atendida | Σ D_(k,t)(1-z_(k,t)), Wh y fracción del total solicitado |
| Servicio energético | Σ D·z / Σ D |
| Servicio completo por intervalo | Fracción de intervalos con demanda positiva en que todas las solicitudes se atienden |
| Atención crítica | Σ D·z de cargas críticas / Σ D de esas cargas |
| Incumplimiento del compromiso | Σ D(x-z) / Σ Dx, cuando se comprometió energía |
| Batería y vertido | Trayectoria, mínimo, estado final y excedente descartado |
| Integridad | Cero errores físicos ejecutados; correcciones y fallos del plan se muestran por separado |

No se presentan horas sin demanda como éxitos. Si un denominador es cero, el indicador no aplica. Se distingue incumplir el plan de no atender toda la demanda.

El nivel de servicio del paper se estima sobre réplicas de simulación. Sus porcentajes no se comparan directamente con nuestros porcentajes horarios ni garantizan que toda la demanda original estuviera atendida. La traducción de missing fulfilment se documenta con nuestra fórmula explícita; no se atribuye al paper una fórmula que no especifica.

### 12.3. Comparación pareada

Se calculan diferencias B-A sobre las mismas fechas, configuración y realizaciones de demanda. Una diferencia de S_pond se expresa en puntos porcentuales; una reducción relativa de déficit requiere denominador positivo.

Se reportan efecto, dispersión e intervalo de confianza mediante remuestreo pareado de bloques temporales, respetando persistencia meteorológica y de batería. Las semillas y los escenarios Monte Carlo no se cuentan como nuevas observaciones escolares independientes. Si el test es demasiado corto, la comparación es exploratoria y no se fuerzan conclusiones de significancia.

El test principal recorre una secuencia continua y no reinicia la batería cada día. Se reserva una inicialización/calientamiento previo si procede, definido antes del test. Los episodios independientes usados en sensibilidad se documentan como tales.

### 12.4. Recursos y sensibilidad

Medir carga del modelo, inferencia caliente, muestreo, planificación, tiempo total y memoria máxima. La factibilidad en CPU con 16 GB se verifica con un ensayo corto; no se presupone.

Cachear pronósticos por origen, historial, configuración y revisión evita repetir inferencia entre tamaños FV o semillas de demanda. Se informa el costo original de generarlos y el costo con caché.

Primero se completa una configuración intermedia. Después, si alcanza el tiempo, se estudian escasez/holgura, otra plantilla de demanda, N y R. Se fijan con desarrollo, sin buscar configuraciones donde gane Chronos después de ver el test.

## 13. Implementación por etapas y calendario

Plan orientativo ajustado a la exposición del 4 o 5 de noviembre de 2026 [2]:

| Fechas propuestas | Resultado verificable |
|---|---|
| 18-20 octubre | Datos auditados, supuestos, entorno y primera física |
| 21-24 octubre | Demanda, heurística, proveedores y benchmark de Chronos |
| 25-28 octubre | Trayectorias comunes, MPC completo y protocolo congelado |
| 29-31 octubre | Comparación final, tablas y borrador de artículo |
| 1-3 noviembre | Revisión, reproducibilidad, diapositivas y ensayo |
| 4-5 noviembre | Exposición según turno asignado |

La redacción de método y literatura empieza durante la implementación. El ensayo de Chronos se hace temprano para detectar incompatibilidades, aunque todavía no sea la base del simulador.

Si hay retrasos, se recortan covariables, interfaz y sensibilidades; se conserva una comparación completa de A y B, física, trazabilidad temporal y resultados honestos.

## 14. Historias de usuario

El archivo HISTORIAS_USUARIO.md contiene 18 historias propuestas, con actor, prioridad, dificultad, dependencias y criterios verificables. Son historias para investigadores y desarrolladores del simulador; no presuponen usuarios escolares usando una aplicación.

Los primeros cierres útiles son entorno reproducible, auditoría de consumo, meteorología, protocolo temporal, demanda y física. El backlog queda listo para trasladar a GitHub una vez identificado el repositorio y ajustado el reparto del equipo.

## 15. Dependencias y reproducibilidad

Base propuesta: Python 3.12, numpy, pandas, scipy, matplotlib y pytest. Añadir chronos-forecasting y torch para inferencia, y pyarrow si se usa Parquet. pvlib es opcional en la versión física más detallada.

La instalación se verifica antes de fijar versiones; la especificación del protocolo no certifica una combinación de versiones no probada. Se guarda un archivo de dependencias bloqueadas, revisión exacta del modelo, configuración, semillas, hardware y hashes de entradas.

Se recomienda separar semillas de demanda y escenarios y generar explícitamente los números aleatorios comunes. Usar el mismo entero como semilla en dos rutinas distintas no garantiza por sí solo números aleatorios comunes.

No hacen falta solver MILP, entrenamiento del modelo ni infraestructura en la nube para el alcance principal.

## 16. Riesgos y respuestas

| Riesgo | Respuesta |
|---|---|
| Sin registros escolares verificables | Prototipo sintético etiquetado; bloquear afirmaciones de datos reales |
| Pocas semanas de consumo | Limitar generalización y variar plantillas con mesura |
| Reanálisis con sesgo local | Tratarlo como referencia modelada, no sensor escolar |
| Beta inestable o poco estacional | Revisar agrupación y soporte antes de abrir el test |
| Cuantiles mal calibrados | Informar cobertura; cualquier recalibración se define antes del test |
| Dependencia temporal insuficiente | Diagnósticos y biblioteca común; reconocer alcance exploratorio |
| Corrección horaria idealizada | No afirmar continuidad física intrahoraria ni validación de campo |
| Control sin efecto anticipatorio | Prueba de dos horas y selección por horizonte |
| Resultado saturado | Dimensionar con desarrollo; conservar los casos sin diferencia |
| Latencia excesiva | Contexto compacto, modelo residente y caché; medir sin ocultar costos |
| Doble conteo de energía | Actualizar el estado desde flujos realizados una sola vez |
| Sobrecarga del proyecto | Priorizar una configuración completa y los entregables académicos |

## 17. Decisiones y pendientes

**Definido en esta versión propuesta:** dos ramas; GHI como única variable pronosticada inicial; demanda programada; Chronos sin ajuste de pesos; física, trayectorias y MPC compartidos; reserva anticipatoria y selección esperada; sin MILP; simulación horaria.

**Debe resolverse al recibir/auditar los datos:** archivo y unidades de consumo; ubicación meteorológica; periodos; catálogo; potencia FV; capacidad y límites de batería; estado inicial; soporte beta; límites de irradiancia; ventanas; tolerancias y hardware.

El protocolo registra quién fijó cada parámetro, con qué datos y antes de qué ejecución. Los valores iniciales recomendados no se presentan como calibración terminada.

## 18. Entregables y definición de éxito

Código modular; configuración y datos reproducibles o instrucciones de obtención; registro de adaptaciones al paper; pruebas físicas y temporales; dos backtests comparables; tablas y figuras; artículo y exposición.

El artículo incluye resumen, introducción, revisión de literatura, método, resultados, conclusiones y referencias. Los lineamientos admiten hasta tres personas por grupo y fijan una exposición final de máximo 20 minutos [2].

Éxito significa contestar la pregunta con evidencia reproducible. No exige que gane Chronos-2. No permite afirmar ahorro monetario, autonomía real, electrificación efectiva ni validez general para todas las escuelas.

## 19. Fuentes

Consultadas para esta revisión el 17 de octubre de 2026, hora de Colombia.

1. Acuna, M. et al. (2021). Operational Planning of Energy for Non-Interconnected Zones: A Simulation-Optimization Approach and a Case Study to Tackle Energy Poverty in Colombia. Energies, 14(10), 2789. [DOI](https://doi.org/10.3390/en14102789). Se revisó el PDF aportado, especialmente pp. 5-8 y 12-14.
3. Amazon Science. [Ficha oficial de amazon/chronos-2](https://huggingface.co/amazon/chronos-2).
4. Amazon Science. [Implementación oficial de Chronos](https://github.com/amazon-science/chronos-forecasting).
5. Ansari, A. F. et al. (2025). [Chronos-2: From Univariate to Universal Forecasting](https://arxiv.org/abs/2510.15821).
6. Open-Meteo. [Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api). La [Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api) se reserva para posibles ampliaciones con pronósticos archivados.
7. NASA POWER. [Hourly API](https://power.larc.nasa.gov/docs/services/api/temporal/hourly/).
8. pvlib-python. [Documentación](https://pvlib-python.readthedocs.io/en/stable/).
9. Rawlings, J. B., Mayne, D. Q. y Diehl, M. Model Predictive Control: Theory, Computation, and Design, segunda edición. [Sitio de los autores](https://sites.engineering.ucsb.edu/~jbraw/mpc/).
10. Schefzik, R., Thorarinsdottir, T. L. y Gneiting, T. (2013). [Uncertainty Quantification in Complex Simulation Models Using Ensemble Copula Coupling](https://arxiv.org/abs/1302.7149). Sustenta la distinción entre marginales y dependencia; la variante concreta descrita aquí usa rangos históricos compartidos.

Las reservas, la selección de cuatro candidatos, el supuesto de demanda programada y el protocolo común son propuestas de este proyecto, no resultados demostrados por estas fuentes.

## 20. Próxima acción concreta

Comenzar con HU-01 a HU-04 y el ensayo corto de compatibilidad de Chronos-2. Incorporar el archivo de consumo diario para validar sus magnitudes y seleccionar el punto meteorológico. Con esos datos se cierra la configuración física; mientras tanto solo se usan ejemplos sintéticos identificados.
