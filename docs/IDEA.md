# IDEA.md — Proyecto de Optimización 1

## Chronos-2 para planificación energética fotovoltaica en una zona no interconectada

**Caso de estudio:** sistema fotovoltaico aislado *hipotético*, contextualizado en el Centro Educativo Rural El Rayo, Támesis (Antioquia).  
**Naturaleza:** experimento computacional semisintético; no instalación, control real ni diagnóstico energético verificado de la institución.  
**Estado (18 de octubre de 2026):** **T01 y T02 completadas** (datos y pronósticos Chronos-2 verificados en CPU). **T03–T05 pendientes.**
**Criterio de diseño:** **reducir archivos, capas, historias y código; no simplificar las matemáticas ni relajar restricciones físicas.**

## 1. Pregunta, objetivo y contribución

**Pregunta:** bajo las mismas condiciones físicas, demanda y estrategia de decisión, ¿la información predictiva de **Chronos-2** mejora la atención de cargas de un sistema fotovoltaico aislado frente a una referencia probabilística de irradiancia basada en **distribuciones beta**?

**Objetivo general:** integrar Chronos-2, sin ajuste de pesos (*zero-shot*), en un esquema reproducible de simulación y planificación energética inspirado en Acuña et al. (2021), y cuantificar el efecto de cambiar únicamente el modelo de pronóstico.

Objetivos específicos: (1) generar pronósticos solares comparables; (2) implementar el modelo energético con baterías y selección de cargas; (3) construir un controlador anticipatorio común; (4) medir calidad predictiva y servicio energético de ambas alternativas.

| Alternativa | Pronóstico de irradiancia | Resto del experimento |
|---|---|---|
| **A — Referencia beta** | Distribución beta ajustada a la historia admisible | Igual sistema, demanda, escenarios, heurística, MPC y evaluación |
| **B — Chronos-2** | Cuantiles del checkpoint oficial `amazon/chronos-2`, sin entrenamiento | Exactamente los mismos componentes y criterios |

**Hipótesis por evaluar, no resultado anticipado:** Chronos-2 puede reducir el error de irradiancia y mejorar la demanda ponderada atendida; también puede empatar o rendir peor. Un error predictivo menor no garantiza mejores decisiones.

## 2. Fuentes y fidelidad matemática

Referencia principal: **Acuña et al. (2021)**, *Operational Planning of Energy for Non-Interconnected Zones: A Simulation-Optimization Approach and a Case Study to Tackle Energy Poverty in Colombia* ([DOI](https://doi.org/10.3390/en14102789)). Se toman como base las restricciones y la función objetivo (pp. 5–6), los algoritmos de despacho y selección de cargas (p. 8) y el tratamiento estocástico de irradiancia (pp. 6–7 y 12).

Se **preserva el problema** de decidir qué cargas encender, cómo utilizar paneles y baterías y cómo responder a irradiancia incierta. Se **adapta el método** al añadir Chronos-2 y un MPC heurístico compartido. No se afirmará que A reproduce literalmente todos los resultados del artículo: los datos, el emplazamiento, las baterías, la parametrización beta y el criterio de selección difieren. El artículo presenta MPC como posible extensión, no como el controlador de sus experimentos.

Las expresiones impresas (4) y (9) del artículo presentan ambigüedades frente a su explicación física: se implementarán límites consistentes de descarga y un indicador binario de solicitud, documentando el cambio. **Nunca** se relajan límites para hacer que una alternativa produzca mejores resultados.

## 3. Datos existentes: T01 completada

Se **reutiliza** el código, los CSV y la configuración ya publicados; no se reimplementa la ingesta. El comando de preparación `energy-project` valida datos, sin simular energía. El ensayo T02 ejecuta Chronos-2 mediante `python -m energy_project.pronosticos`, con el extra `pronosticos`; todavía no hay simulador energético.

Datos principales en `data/`:

- `irradiancia_historica.csv`: 17.544 horas (2024–2025) de reanálisis ERA5/Open-Meteo, como referencia meteorológica, **no** sensor de la escuela.
- `irradiancia_simulacion.csv`: irradiancia reservada para el caso simulado; sus valores realizados **no** entran a pronósticos emitidos antes de ese instante.
- `demanda_por_carga.csv`: 13 cargas y 84 días/2.016 horas de demanda programada sintética.
- `catalogo_cargas.csv`: potencias, prioridades y horarios supuestos.
- `calendario_simulacion.csv`: alineación entre intervalos y fechas.

Existen CSV adicionales de medidor, demanda diaria y horaria, derivados y verificaciones; **no hay que crear otros por obligación**. Las cinco lecturas transcritas del medidor generan cuatro diferencias provisionales de 7, 5, 9 y 7 kWh, sin fechas ni decimales plenamente verificados. El consumo por equipo es **sintético**, no medición desagregada. La infraestructura y las prioridades son **supuestos de simulación**.

La demanda programada se considera conocida para ambas alternativas durante el horizonte. Este supuesto aísla la incertidumbre solar; **no demuestra** capacidad para pronosticar demanda real. No se presenta el estudio como evaluación operativa de una escuela efectivamente electrificada con esa infraestructura.

Se mantiene la partición temporal existente: historia previa; desarrollo **6 de enero–2 de marzo de 2025**; evaluación **3–30 de marzo de 2025** (intervalos semiabiertos en la configuración). Se ajustan parámetros con desarrollo y se reservan las fechas de evaluación para la comparación final.

## 4. Modelo matemático común (no simplificar)

**Índices:** horas \(t\), dispositivos \(k\), baterías \(b\). Paso \(\Delta t=1\) h. Irradiancia media \(G_t\) en W/m²; energías en Wh. Todas las decisiones respetan unidades y cronología.

### 4.1. Generación fotovoltaica

\[
P_t^{\mathrm{FV}}=\min\!\left(P_{\mathrm{inv,max}},\;P_{\mathrm{STC}}\,\frac{G_t}{1000\;\mathrm{W/m^2}}\,f\right),
\qquad E_t^{\mathrm{FV}}=P_t^{\mathrm{FV}}\Delta t.
\]

\(f\) representa pérdidas agregadas del sistema FV sin duplicar pérdidas de batería. Aproximar irradiancia sobre paneles con GHI implica un **supuesto de plano horizontal equivalente**, idéntico para A y B. El inversor y la potencia del bus deben limitar las cargas conforme a la configuración.

### 4.2. Cargas y función objetivo

\(D_{k,t}\ge0\) es energía solicitada; \(r_{k,t}\in\{0,1\}\) indica solicitud; \(x_{k,t}\) es compromiso planificado y \(z_{k,t}\) carga efectivamente atendida (ambos binarios):

\[
0\le z_{k,t}\le x_{k,t}\le r_{k,t},
\qquad J=\sum_{t,k}p_{k,t}D_{k,t}z_{k,t},
\qquad S_{\mathrm{pond}}=\frac{J}{\sum_{t,k}p_{k,t}D_{k,t}}.
\]

\(p_{k,t}\) es la prioridad. Si el denominador es cero, se informa «no aplica». Se registra por separado energía **solicitada**, **comprometida** y **atendida**. La métrica principal usa atención realmente ejecutada, no promesas del plan.

### 4.3. Conservación de energía y baterías individuales

\(c_{b,t}\) es energía del bus enviada a cargar la batería, \(d_{b,t}\) la entregada desde ella al bus y \(v_t\) el vertido (todas no negativas). \(C_{b,t}\) es energía almacenada al comenzar la hora:

\[
\sum_k D_{k,t}z_{k,t}+\sum_b c_{b,t}+v_t
=E_t^{\mathrm{FV}}+\sum_b d_{b,t},
\qquad C_{b,t+1}=C_{b,t}+\eta_{c,b}c_{b,t}-\frac{d_{b,t}}{\eta_{d,b}}.
\]

\[
C_{b,\min}\le C_{b,t}\le C_{b,\max},\qquad
0\le c_{b,t}\le\min\!\left(P_{c,b,\max}\Delta t,\frac{C_{b,\max}-C_{b,t}}{\eta_{c,b}}\right),
\]

\[
0\le d_{b,t}\le\min\!\left(P_{d,b,\max}\Delta t,\eta_{d,b}(C_{b,t}-C_{b,\min})\right).
\]

No se carga y descarga simultáneamente una batería; la carga usa excedente solar y la descarga cubre déficit. **Cada batería conserva su propio estado, potencia y límites**: agregarlas en una batería virtual podría ocultar descargas inviables. Se verifica también la potencia máxima de suministro a las cargas. Si la energía es insuficiente, se **desatienden cargas** antes que violar restricciones. Los estados finales de un horizonte son los iniciales del siguiente.

### 4.4. Heurística del artículo, interpretada y verificable

En cada hora: calcular energía fotovoltaica disponible; ordenar cargas solicitadas por prioridad descendente, consumo descendente e identificador; atender completamente una carga solo si los flujos son factibles; descargar baterías en orden determinista sin traspasar sus límites; cargar excedentes y contabilizar vertido. La decisión on/off, el despacho y el balance usan **la misma rutina** en A y B.

El orden y los desempates concretan ambigüedades del algoritmo publicado; son una interpretación documentada, no una transcripción literal. Se comprueban casos manuales de noche, excedente, batería al mínimo, batería llena y déficit.

## 5. Pronósticos y escenarios: única diferencia A/B

**A — Beta:** ajustar irradiancia positiva por hora/franja local usando solamente observaciones anteriores al origen de predicción; tratar horas nocturnas y masas de cero por separado. Priorizar una beta de cuatro parámetros si el ajuste resulta estable; si es inestable, fijar **antes del test** una beta escalada de dos parámetros como adaptación explícita. No reutilizar parámetros meteorológicos del artículo como si fueran mediciones de Támesis.

**B — Chronos-2:** usar el checkpoint oficial sin reentrenar ni ajustar pesos; cargarlo una sola vez, utilizar únicamente la historia disponible y emitir cuantiles para el horizonte. Valores iniciales: contexto **336 h**, horizonte **24 h** y cuantiles **0.1 a 0.9**. T02 verificó API, memoria y tiempo mediante dos orígenes de desarrollo y repetición del primero en CPU/FP32. Se fija `chronos-forecasting==2.3.2` y la revisión `29ec3766d36d6f73f0696f85560a422f50e8498c` de `amazon/chronos-2`; configuración, resultados, versiones y hashes se registran en el informe del ensayo. Se exige contexto consecutivo completo, sin interpolar horas no disponibles. Esto no sustituye el backtest de evaluación de T05.

Los dos proveedores entregan **los mismos cuantiles para los mismos tiempos**. Se aplica una corrección común: reordenar cruces, eliminar negativos y poner a cero intervalos completamente nocturnos según amanecer/ocaso calculados con Astral a partir de fecha y coordenadas. Se conservan los intervalos que cruzan esas fronteras y los pronósticos crudos; se reportan correcciones y rangos. Se supone horizonte sin obstáculos y observador a nivel del suelo. **No se impone techo superior artificial de GHI**: 1000 W/m² es referencia STC, no una cota física universal. La mediana sirve como pronóstico puntual. Esta política es una adaptación del proyecto, común a ambas ramas.

**Escenarios temporalmente coherentes:** los cuantiles horarios por sí solos no son trayectorias conjuntas. Para evitar una infraestructura compleja, usar **una sola función compartida** que tome bloques históricos de 24 horas, transforme sus posiciones en rangos empíricos y aplique a ambos proveedores la función cuantil inversa correspondiente. Los mismos bloques, rangos y semillas se usan en A/B. Los extremos de las colas se fijan con una regla común de desarrollo y se documentan. Este muestreo aproxima la dependencia temporal; no equivale a una garantía de calibración conjunta.

La meteorología de evaluación permanece oculta hasta el momento de ejecución simulado. Ninguna alternativa recibe covariables futuras exclusivas, ni parámetros ajustados sobre el test. ERA5 se usa con disponibilidad al cierre de cada hora como idealización declarada; tampoco se afirma que Chronos-2 no haya visto datos semejantes durante su preentrenamiento.

## 6. MPC heurístico sencillo, pero verdaderamente anticipatorio

Un controlador compartido planifica con \(H=24\) horas, ejecuta \(R=6\) y vuelve a calcular a partir de **los estados realizados de cada batería**, sin conocer la irradiancia futura real. Inicialmente: \(N=50\) escenarios y cuatro reservas candidatas \(\rho\in\{0,0.25,0.5,0.75\}\), aplicadas a la capacidad utilizable **de cada batería** solo para cargas no críticas.

Para cada origen de pronóstico:

1. Calcular cuantiles y construir \(N\) escenarios comunes.
2. Proponer cuatro calendarios de cargas con la misma heurística sobre la trayectoria mediana, cambiando solo la reserva operativa \(\rho\).
3. Simular **cada calendario fijo** bajo todos los escenarios, con balances y corrección de ejecución comunes.
4. Seleccionar antes de revelar el futuro el calendario de mayor beneficio esperado:

\[
\mathrm{Score}(a)=\frac1N\sum_{s=1}^N\left[
\sum_{h=1}^{H}\sum_k p_{k,h}D_{k,h}z_{k,h}^{(a,s)}
+\lambda_T\sum_b\eta_{d,b}\big(C_{b,H}^{(a,s)}-C_{b,\min}\big)
\right].
\]

5. Ejecutar los primeros \(R\) compromisos contra la irradiancia real reservada; si falta energía, desconectar cargas por prioridad sin infringir la física. Registrar atención real y actualizar baterías. Repetir.

El término terminal \(\lambda_T\) se fija **con desarrollo** para valorar energía útil restante; **no** forma parte de la métrica final \(J\). Los dos métodos tienen idéntico generador de candidatos, simulador, función de selección y actualización. **Solo cambian sus pronósticos y, legítimamente, las decisiones/estados resultantes.**

**Prueba obligatoria de anticipación:** en un caso artificial de dos horas y batería útil de 1 Wh, sin sol ahora, con carga actual de prioridad 1 y futura de prioridad 3, la decisión presente debe cambiar si el pronóstico futuro pasa de 0 a 1 Wh solar. Repetir una política voraz que ignore el futuro **no** se denominará MPC anticipatorio.

## 7. Protocolo experimental y resultados

**Diseño:** comparar A y B sobre exactamente las mismas fechas, demanda programada, capacidades, estado inicial, información histórica admisible y condiciones meteorológicas realizadas. Durante la simulación **no se igualan artificialmente** sus baterías después de cada decisión. No ajustar parámetros usando resultados finales del test.

**Indicadores principales** (calculados sobre ejecución realizada):

| Tipo | Indicador |
|---|---|
| Pronóstico | MAE y RMSE de irradiancia, especialmente en horas diurnas; *pinball loss* de cuantiles si el tiempo alcanza |
| Servicio | Fracción de energía ponderada atendida \(S_{\mathrm{pond}}\) y energía no atendida (Wh y %) |
| Control | Atención de cargas críticas y estado de carga de las baterías |
| Consistencia | Violaciones físicas (= 0), uso de información futura (= 0) |
| Recursos | Tiempo de inferencia de Chronos-2 y ejecución total |

El resultado principal es la **diferencia pareada B−A** en servicio bajo la misma referencia. Se presentan series de irradiancia real/pronosticada, demanda solicitada/atendida y evolución de baterías, más una tabla final. Con pocos días y demanda sintética, los resultados son exploratorios; no se fuerza una afirmación de significancia ni de beneficios reales en campo.

**Criterio de éxito del trabajo:** ejecutar ambos métodos de extremo a extremo, respetar la física, mostrar resultados reproducibles y explicar si Chronos-2 aporta o no una mejora bajo estos supuestos. **No** es necesario que Chronos-2 gane.

## 8. Implementación compacta: T01 y T02 ya están hechas

Conservar sin reescribir `data/`, `scripts/configuracion.json`, la ingesta/generación existente de `src/energy_project/` y sus pruebas actuales. No crear API, base de datos, interfaz web, framework de servicios ni abstracciones innecesarias.

**Solo cuatro módulos funcionales nuevos, si hacen falta:**

```text
src/energy_project/
├── pronosticos.py   # Chronos-2 y cuantiles implementados; beta pendiente en T04
├── simulador.py     # FV, baterías, despacho y cargas
├── controlador.py   # escenarios y MPC compartido
└── experimento.py   # ejecución A/B, métricas y gráficos
```

La implementación puede reunir funciones cortas en menos archivos si resulta más clara; **la estructura es orientativa**, no un requisito arquitectónico. Una configuración y un comando de experimento son suficientes. Preferir `numpy`, `pandas`, `scipy`, `matplotlib` y dependencias oficiales de Chronos-2 sobre desarrollar librerías propias. Añadir pruebas breves y enfocadas en invariantes matemáticos, tiempo de pronóstico y comparación justa; evitar pruebas extensas de infraestructura que no intervienen en la investigación.

| Tarea / issue de GitHub | Resultado mínimo verificable | Estado |
|---|---|---|
| **T01 — Datos** ([#1](https://github.com/ThomasSerna/proyecto-optimizacion/issues/1)) | CSV, demanda semisintética, configuración y validación reproducibles | **Completada** |
| **T02 — Chronos-2** ([#2](https://github.com/ThomasSerna/proyecto-optimizacion/issues/2)) | Pronosticar 24 h a partir de historia válida y graficar frente a irradiancia real | **Completada** |
| **T03 — Simulación energética** ([#3](https://github.com/ThomasSerna/proyecto-optimizacion/issues/3)) | Reproducir física por batería, heurística y restricciones en casos verificables | Pendiente |
| **T04 — Beta y MPC** ([#4](https://github.com/ThomasSerna/proyecto-optimizacion/issues/4)) | Referencia beta + controlador común que utiliza pronósticos de ambas ramas | Pendiente |
| **T05 — Experimentación** ([#5](https://github.com/ThomasSerna/proyecto-optimizacion/issues/5)) | Comparar A/B, exportar tabla y gráficas, explicar resultados y límites | Pendiente |

Las antiguas issues **#6 y #7** quedan absorbidas por T04 y T05; no se mantienen como trabajos adicionales. T02 está verificada; T03 implementará la física, T04 integrará pronósticos y simulador, y T05 presentará el experimento final.

## 9. Entregables y límites

1. Repositorio ejecutable y reproducible, **sin** aplicación compleja.
2. Tabla y visualizaciones de comparación beta frente a Chronos-2.
3. Artículo del curso: resumen, introducción, literatura, método, resultados, discusión, conclusiones y referencias; más presentación oral. Según los lineamientos, la presentación final es el **4 o 5 de noviembre de 2026**.

**Fuera del alcance:** medición en vivo, instalación física, diseño de electrónica de potencia, optimización económica, entrenamiento de Chronos-2, API, dashboard, pronóstico de demanda, comparación masiva de algoritmos, garantía de operación segura en hardware y reproducción numérica exacta del caso Volcanes.

**Fuentes:** Acuña et al. (2021), [DOI:10.3390/en14102789](https://doi.org/10.3390/en14102789); *Lineamientos del proyecto final* (Angélica M. González, 2026); [Amazon Chronos-2, Hugging Face](https://huggingface.co/amazon/chronos-2). Los supuestos y decisiones experimentales de este documento son **propuesta de implementación**, no hallazgos de esas fuentes.
