# IDEA.md — Proyecto de Optimización 1

## Chronos-2 para planificación energética fotovoltaica en una zona no interconectada

**Caso de estudio:** sistema fotovoltaico aislado *hipotético*, contextualizado en el Centro Educativo Rural El Rayo, Támesis (Antioquia).  
**Naturaleza:** experimento computacional semisintético; no instalación, control real ni diagnóstico energético verificado de la institución.  
**Estado (19 de octubre de 2026):** **T01–T05 completadas** (datos, pronósticos Chronos-2, simulador, MPC pareado y evaluación final A/B verificados en CPU; borrador del artículo en `docs/articulo.md`). En la rama `experiment/seleccion-mochila`, **regla opcional de selección exacta (mochila)**, desactivada por defecto y evaluada en desarrollo y marzo (§§4.4 y 7).
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

La formulación física de este documento **corrige inconsistencias impresas** y explicita supuestos; no es una transcripción literal de las ecuaciones del artículo. La correspondencia se detalla en §4.5. **Nunca** se relajan límites para hacer que una alternativa produzca mejores resultados.

## 3. Datos existentes: T01 completada

Se **reutiliza** el código, los CSV y la configuración ya publicados; no se reimplementa la ingesta. El comando de preparación `energy-project` valida datos, sin simular energía. El ensayo T02 ejecuta Chronos-2 mediante `python -m src.pronosticos`, con el extra `pronosticos`; T03 expone `src.simulador.simular` desde Python.

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

\(f\) representa pérdidas agregadas del sistema FV sin duplicar pérdidas de batería. Aproximar irradiancia sobre paneles con GHI implica un **supuesto de plano horizontal equivalente**, idéntico para A y B. No se recorta GHI a 1000 W/m²: es la referencia STC, no un máximo. El recorte de generación FV por el inversor y el límite de potencia nominal de las cargas son comprobaciones distintas.

### 4.2. Cargas y función objetivo

\(D_{k,t}\ge0\) es energía solicitada; \(r_{k,t}\in\{0,1\}\) indica solicitud; \(x_{k,t}\) es compromiso planificado y \(z_{k,t}\) carga efectivamente atendida (ambos binarios):

\[
0\le z_{k,t}\le x_{k,t}\le r_{k,t},
\qquad J=\sum_{t,k}p_{k,t}D_{k,t}z_{k,t},
\qquad S_{\mathrm{pond}}=\frac{J}{\sum_{t,k}p_{k,t}D_{k,t}}.
\]

\(p_{k,t}\) es la prioridad. Si el denominador es cero, se informa «no aplica». Se registra por separado energía **solicitada**, **comprometida** y **atendida**. La métrica principal usa atención realmente ejecutada, no promesas del plan.

Se supone un **inversor común** para el suministro FV y desde baterías. Con \(P_k\) como potencia nominal del dispositivo, se exige además:

\[
\sum_k P_k z_{k,t}\le\min(P_{\mathrm{inv,max}},P_{\mathrm{bus,max}}).
\]

Esta restricción usa potencias nominales; no se sustituye por la energía horaria dividida entre \(\Delta t\), pues el tiempo programado de uso puede ser menor que una hora. La topología y este límite son supuestos del proyecto, no un modelo eléctrico dentro del intervalo.

### 4.3. Conservación de energía y baterías individuales

\(c_{b,t}\) es energía del bus enviada a cargar la batería, \(d_{b,t}\) la entregada desde ella al bus y \(v_t\) el vertido (todas no negativas). Las eficiencias individuales \(0<\eta_{c,b},\eta_{d,b}\le1\) convierten esos flujos del bus a cambios de energía almacenada; no se vuelven a aplicar a la energía FV. \(C_{b,t}\) es energía almacenada **al comenzar la hora**:

\[
\sum_k D_{k,t}z_{k,t}+\sum_b c_{b,t}+v_t
=E_t^{\mathrm{FV}}+\sum_b d_{b,t},
\qquad C_{b,t+1}=C_{b,t}+\eta_{c,b}c_{b,t}-\frac{d_{b,t}}{\eta_{d,b}}.
\]

\[
C_{b,\min}\le C_{b,t},C_{b,t+1}\le C_{b,\max},\qquad
0\le c_{b,t}\le\min\!\left(P_{c,b,\max}\Delta t,\frac{C_{b,\max}-C_{b,t}}{\eta_{c,b}}\right),
\]

\[
0\le d_{b,t}\le\min\!\left(P_{d,b,\max}\Delta t,\eta_{d,b}(C_{b,t}-C_{b,\min})\right),\qquad c_{b,t}d_{b,t}=0.
\]

Los límites de ambos flujos se calculan con el estado previo \(C_{b,t}\). La carga usa excedente solar y la descarga cubre déficit. **Cada batería conserva su propio estado, potencia y límites**: agregarlas en una batería virtual podría ocultar descargas inviables. Las eficiencias, los límites de potencia y el reparto por identificador son extensiones explícitas del proyecto. Si la energía es insuficiente, se **desatienden cargas** antes que violar restricciones. Los estados finales de un horizonte son los iniciales del siguiente; los valores configurados solo inicializan la primera llamada que no recibe estado.

### 4.4. Heurística del artículo, interpretada y verificable

En cada hora, calcular la energía FV y sumar los límites de descarga individuales para conocer el presupuesto energético. Ordenar las solicitudes activas por prioridad descendente, energía demandada descendente e identificador ascendente. Aceptar una carga completa si cabe en la energía y potencia nominal restantes; si se rechaza, continuar con la siguiente. Seleccionar primero todas las cargas y ejecutar **un único despacho sobre la selección final**: FV para las cargas, descarga para el déficit, carga con excedente solar y vertido del resto. Recorrer baterías por identificador ascendente. Una carga rechazada no consume batería.

Sin calendario externo se fija \(x=z\). Con compromisos fijos solo son candidatas las cargas con \(x=1\): ante un déficit se conserva el compromiso y puede resultar \(z=0\), sin activar cargas fuera del calendario. La decisión on/off, el despacho y el balance usan **la misma rutina** en A y B; la heurística no garantiza el óptimo global de \(J\).

No se redondean flujos ni estados intermedios. Se verifican balances y límites con tolerancia absoluta de \(10^{-6}\) Wh, sin añadir esa tolerancia a los presupuestos de selección. Los casos manuales incluyen noche, excedente, batería al mínimo, batería llena, déficit y baterías heterogéneas.

**Adaptación opcional: selección exacta (mochila).** La clave `seleccion_cargas` de la configuración vale `"prioridad"` (regla anterior, predeterminada también si falta la clave) o `"mochila"`. `scripts/configuracion.json` no la define, de modo que T03–T05 no cambian. Con `"mochila"`, la regla solo cambia en las horas **limitadas por potencia**, cuando la potencia nominal de las candidatas supera \(\min(P_{\mathrm{inv,max}},P_{\mathrm{bus,max}})\). Las críticas (\(p\ge3\)) se siguen eligiendo primero con la regla de prioridad. Las no críticas se eligen enumerando todos los subconjuntos factibles y maximizando \(\sum_k p_kD_k\), con los mismos presupuestos de energía (o límites de reserva de §6) y de potencia, sin tolerancia añadida. Los empates se resuelven por menor energía y después por el orden \((-p,-D,\text{id})\). Hay a lo sumo \(2^{12}\) subconjuntos por hora. El despacho único, el calendario fijo y la verificación física no cambian, y la regla elegida se aplica igual a todas las políticas, a los candidatos del MPC y a la referencia reactiva. La regla optimiza el beneficio horario de la ecuación (10), pero deja de respetar el orden estricto de prioridad del algoritmo 2: una carga de prioridad 2 puede ceder su potencia a varias de prioridad 1 con mayor \(\sum p_kD_k\). Sigue siendo una heurística horaria y no certifica el óptimo del periodo. Los resultados están en §7.

### 4.5. Correspondencia con el artículo y diferencias

Se consultaron §2.2 (pp. 5–6) y los algoritmos 1–2 (p. 8) del [PDF oficial](https://mdpi-res.com/d_attachment/energies/energies-14-02789/article_deploy/energies-14-02789.pdf). Los índices día/periodo del artículo se reúnen en la secuencia horaria \(t\).

| Referencia | Interpretación física adoptada en T03 |
|---|---|
| (1)–(3), (7) | Límites individuales explícitos y descarga limitada por energía útil, sin variable auxiliar \(Q\) ni big-M. Con \(c_{\min}>0\) y \(Q\) binaria, (1)–(2) imprimen \(Q\le C/c_{\min}\le Q+1\), imponiendo inadvertidamente \(C\le2c_{\min}\); no se incorpora esa cota ajena a la capacidad configurada. |
| (4) | Para \(E=Q=1\), la desigualdad impresa exige \(BD\ge C-c_{\min}\), frente al máximo descrito en el texto. Además, (5) define \(C\) después del flujo del mismo periodo. Se limita la descarga con el estado **anterior**, \(d\le\eta_d(C_t-C_{\min})\). |
| (5)–(6) | Continuidad por batería y conservación en el bus. Se añaden eficiencias individuales y vertido explícito; no se crea energía ni se descuenta una carga rechazada. |
| (8)–(9) | Servicio completo y solicitudes binarias: \(z\le x\le r\). La (9) impresa compara una decisión binaria con demanda en Wh; se usa el indicador \(r\), conservando la energía en el balance. |
| (10) | Beneficio ponderado de las cargas efectivamente atendidas. La selección voraz busca ese criterio sin certificar un máximo global. |
| Algoritmos 1–2 | El primero despacha usando un orden por consumo y el segundo selecciona según prioridad. Para evitar descontar energía de una selección provisional distinta, T03 selecciona primero y despacha una sola vez. Prioridad, consumo, desempate por identificador y reparto por batería quedan fijados explícitamente. La opción `seleccion_cargas="mochila"` (§4.4) sustituye el orden voraz de las no críticas por la maximización exacta de (10) solo en horas limitadas por potencia; es una adaptación del proyecto, desactivada por defecto. |

La conversión FV, el paso horario, las eficiencias, los límites de potencia y la topología común son supuestos físicos del caso semisintético. Estas decisiones concretan una adaptación consistente; no validan numéricamente el caso original de Volcanes ni el comportamiento eléctrico dentro de una hora.

### 4.6. Interfaz reproducible de T03

`simular(demanda, irradiancia, configuracion, *, estado_inicial=None, compromisos=None, reserva=0)` recibe la demanda existente, una tabla explícita `t, ghi_w_m2` y la configuración validada de T01. El estado opcional es `{bateria_id: energia_wh}`; los compromisos opcionales son `t, carga_id, x` para todas las filas de demanda. Se validan horas consecutivas, claves, dominios y límites individuales, sin modificar entradas ni buscar meteorología por cuenta propia. La clave opcional `configuracion["seleccion_cargas"]` elige la regla de §4.4. T04 añade `reserva` en [0, 1] solo para generar candidatos (§6); cero conserva el comportamiento de T03 y una reserva no nula se rechaza junto con compromisos fijos.

Devuelve tablas `cargas` (solicitud, compromiso y servicio), `horas` (energías y vertido), `baterias` (estados y flujos individuales), el diccionario `estado_final` y un `resumen` de energías, \(J\) y \(S_{\mathrm{pond}}\). No escribe archivos. Las pruebas `test_continuidad_entre_horizontes_y_entradas_sin_mutacion` y `test_compromiso_fijo_con_deficit_y_sin_sustituciones` de `tests/test_simulador.py` muestran la continuación mediante estados y la ejecución de un calendario fijo. La rutina es determinista y no introduce semillas nuevas; los datos sintéticos conservan las semillas de T01. Las pruebas incluyen casos manuales, heterogeneidad y recorrido completo de desarrollo; la comparación de control y pronósticos pertenece a T04–T05.

## 5. Pronósticos y escenarios: única diferencia A/B

**A — Beta:** T04 fija una beta escalada de dos parámetros por hora local: masa de cero empírica y ajuste por máxima verosimilitud de los positivos con `loc=0` y `scale=1.05*max(positivos)`. Se usan las mismas **336 horas consecutivas, terminadas y disponibles** que recibe Chronos. Si todos los valores son cero se devuelve cero; si los positivos son constantes se usa una masa puntual mezclada con cero. Un ajuste inválido o contexto incompleto produce error, sin cambiar de familia durante evaluación. La escala es un soporte estadístico aprendido, no un máximo físico de GHI.

Se descartó el ajuste libre de cuatro parámetros tras un piloto de desarrollo con los orígenes locales 6 de enero de 2025 a las 00:00 y 12:00, y 13 de enero a las 00:00: 36 ajustes de las horas 06–17, cada uno con 14 positivos. Cuatro produjeron soporte negativo y once emitieron advertencias, aunque todos devolvieron cuantiles 0.1, 0.5 y 0.9 finitos. Los 36 ajustes escalados fueron válidos y no emitieron advertencias. Entorno: Python 3.12.14, SciPy 1.18.1, NumPy 2.5.4 y pandas 3.0.6. Esto justifica la adaptación prevista antes del test; no valida el ajuste estadístico en todos los orígenes. El caso original ajusta beta4 con datos distintos (§5 del paper); no se trasladan sus parámetros a Támesis. SciPy [documenta el problema no acotado del ajuste libre por máxima verosimilitud](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.beta.html).

**B — Chronos-2:** usar el checkpoint oficial sin reentrenar ni ajustar pesos; cargarlo una sola vez, utilizar únicamente la historia disponible y emitir cuantiles para el horizonte. Valores iniciales: contexto **336 h**, horizonte **24 h** y cuantiles **0.1 a 0.9**. T02 verificó API, memoria y tiempo mediante dos orígenes de desarrollo y repetición del primero en CPU/FP32. Se fija `chronos-forecasting==2.3.2` y la revisión `29ec3766d36d6f73f0696f85560a422f50e8498c` de `amazon/chronos-2`; configuración, resultados, versiones y hashes se registran en el informe del ensayo. Se exige contexto consecutivo completo, sin interpolar horas no disponibles. Esto no sustituye el backtest de evaluación de T05.

Los dos proveedores entregan **los mismos cuantiles para los mismos tiempos**. Se aplica una corrección común: reordenar cruces, eliminar negativos y poner a cero intervalos completamente nocturnos según amanecer/ocaso calculados con Astral a partir de fecha y coordenadas. Se conservan los intervalos que cruzan esas fronteras y los pronósticos crudos; se reportan correcciones y rangos. Se supone horizonte sin obstáculos y observador a nivel del suelo. **No se impone techo superior artificial de GHI**: 1000 W/m² es referencia STC, no una cota física universal. La mediana sirve como pronóstico puntual. Esta política es una adaptación del proyecto, común a ambas ramas.

**Escenarios temporalmente coherentes:** `generar_escenarios(cuantiles, historico, origen, configuracion)` toma bloques históricos completos de 24 horas, alineados con la hora local del origen dentro de las mismas 336 horas admisibles. Calcula rangos entre bloques **por columna del horizonte**, con empates promedio y \(U=(r-0.5)/M\). Muestrea 50 bloques con reemplazo, mediante una semilla que combina `semilla_escenarios` y la hora UTC del origen; el orden de ejecución A/B no afecta al muestreo. Interpola esos mismos rangos sobre los cuantiles corregidos de cada proveedor y prolonga los cuantiles extremos fuera de 0.1–0.9. Devuelve matrices N×H `escenarios` y `rangos`, fechas UTC `bloques` y `semilla`. Empates y colas truncadas son aproximaciones: no reproducen exactamente las marginales ni garantizan calibración conjunta.

La meteorología de evaluación permanece oculta hasta el momento de ejecución simulado. Ninguna alternativa recibe covariables futuras exclusivas, ni parámetros ajustados sobre el test. ERA5 se usa con disponibilidad al cierre de cada hora como idealización declarada; tampoco se afirma que Chronos-2 no haya visto datos semejantes durante su preentrenamiento.

## 6. MPC heurístico sencillo, pero verdaderamente anticipatorio

Un controlador compartido planifica con \(H=24\) horas, ejecuta \(R=6\) y vuelve a calcular a partir de **los estados realizados de cada batería**, sin conocer la irradiancia futura real. Se usan \(N=50\) escenarios y cuatro reservas candidatas \(\rho\in\{0,0.25,0.5,0.75\}\). Son críticas las solicitudes de prioridad \(p\ge3\); las demás protegen la capacidad utilizable **de cada batería**:

\[
C_{\mathrm{res},b}=C_{b,\min}+\rho(C_{b,\max}-C_{b,\min}).
\]

Sea \(L_b\) el límite físico de descarga de §4.3 y \(a_b\) la descarga necesaria para las cargas críticas seleccionadas, repartida por identificador. Para las no críticas, la descarga adicional admisible es:

\[
e_b=\min\!\left(L_b-a_b,\;\eta_{d,b}\max\!\left(0,C_{b,t}-\frac{a_b}{\eta_{d,b}}-C_{\mathrm{res},b}\right)\right).
\]

El único despacho final respeta \(d_b\le a_b+e_b\) **individualmente**, además de las restricciones de §4. No basta limitar la energía agregada, pues podría consumir la reserva de otra batería. Las críticas pueden descargar hasta el mínimo físico. La reserva solo genera candidatos; la evaluación y ejecución de compromisos fijos usan los mínimos físicos originales, sin reserva adicional.

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

`planificar_mpc(...)` devuelve los cuatro calendarios, sus puntuaciones y el compromiso elegido; los empates favorecen la menor reserva. Los calendarios idénticos reutilizan su evaluación. `ejecutar_mpc(caso, pronosticar, *, inicio, fin, estado_inicial=None)` recibe un callback `(historia_admisible, origen_utc, horizonte)` que devuelve cuantiles crudos; aplica las correcciones y escenarios comunes y entrega las tablas de T03, decisiones y pronósticos con sus diagnósticos. El calendario validado alinea UTC y `t`. Al terminar el periodo, \(H_{\mathrm{ef}}=\min(H,\text{horas restantes})\) y se ejecutan como máximo \(R\) horas restantes. Se concatenan solo resultados realizados y se recalcula \(S_{\mathrm{pond}}\) sobre sus totales. `src/experimento.py` carga Chronos una sola vez y `controlador.proveedores` crea los callbacks de beta y Chronos que recibe `ejecutar_mpc`.

**Prueba de anticipación implementada:** en un caso artificial de dos horas y batería útil de 1 Wh, sin sol ahora, con carga actual de prioridad 1 y futura de prioridad 3, la decisión presente cambia si el pronóstico futuro —mediana y escenarios— pasa de 0 a 1 Wh solar. Las pruebas también cubren compromisos fijos, continuidad realizada, reservas heterogéneas, igualdad con proveedores idénticos y ausencia de información futura. Esto verifica la lógica anticipatoria del MPC, sin certificar optimalidad. El artículo selecciona soluciones frecuentes y propone MPC como extensión (§§3 y 6); este controlador y su puntuación son adaptaciones del proyecto.

## 7. Protocolo experimental y resultados

**Diseño:** comparar A y B sobre exactamente las mismas fechas, demanda programada, capacidades, estado inicial, información histórica admisible y condiciones meteorológicas realizadas. Durante la simulación **no se igualan artificialmente** sus baterías después de cada decisión. No ajustar parámetros usando resultados finales del test.

**Calibración T04:** usar únicamente 6–12 de enero de 2025, con fin exclusivo el 13. Probar \(\lambda_T\in\{0,0.25,0.5,0.75\}\), reutilizando pronósticos y rangos por origen entre pesos. Las ocho combinaciones `(proveedor, peso)` parten de idéntica inicialización y conservan estados continuos independientes. Elegir el peso que maximiza el promedio del **J realizado** de A y B; desempatar por el menor peso. `calibrar_mpc(caso, pipeline=None)` devuelve ocho resultados, sus tablas y pronósticos, fechas y peso elegido, sin modificar entradas. El comando `uv run --locked --extra pronosticos python -m src.controlador --salida results/t04_calibracion` verifica balances y límites con tolerancia de \(10^{-6}\) Wh y comprueba que entradas y código no cambiaron. Solo después prepara ambos archivos temporales, publica `resumen.json` y reemplaza la configuración con el peso elegido como último paso; si falla el reemplazo, retira el informe y conserva la configuración original. Se limpian los temporales tanto al terminar como ante un error. Registra fechas, puntuaciones, semillas, bloques, cuantiles crudos/corregidos, diagnósticos, versiones y hashes. Incluye tanto la configuración de entrada como la final; el hash del archivo de configuración describe la entrada anterior a su actualización. La salida debe ser nueva.

**Ensayo CPU completado:** 28 orígenes por proveedor, 50 escenarios por origen y ocho recorridos de 168 h, en 1266.3 s con pesos en caché. Los cuatro pesos empataron: en beta, \(J=73192.486644\), \(S_{\mathrm{pond}}=0.9540504745\); en Chronos, \(J=73784.143168\), \(S_{\mathrm{pond}}=0.9617626074\). El promedio pareado fue 73488.314906 y el desempate fijó **\(\lambda_T=0\)** en la configuración común. No hubo violaciones físicas; errores máximos observados del bus \(2.8422\times10^{-14}\) Wh y de batería \(2.2738\times10^{-13}\) Wh (cotas redondeadas hacia arriba), menores que \(10^{-6}\) Wh. Se comprobaron los mismos bloques/semillas en A/B, cuantiles finitos, hashes de código y CSV y coherencia de la configuración final. En esa verificación pasaron **260 pruebas** con el entorno bloqueado. Registro local regenerable: `results/t04_calibracion_verificada/resumen.json`. Esto documenta calibración, **no** el backtest de T05 ni una conclusión general sobre la utilidad del término terminal o la superioridad de Chronos.

**Evaluación T05 completada:** `uv run --locked --extra pronosticos python -m src.experimento --salida results/t05_evaluacion` (`src/experimento.py`).

- **Diseño.** Ejecuta A y B en el periodo reservado (3 al 31 de marzo de 2025, fin exclusivo; 112 orígenes por rama), con los callbacks comunes de `controlador.proveedores` y el mismo estado inicial configurado. Después conserva estados independientes.
- **Referencia reactiva.** Añade la heurística de §4.4 ejecutada con la GHI realizada de cada hora. Es una adaptación del proyecto con información perfecta de la hora en curso: sirve para interpretar, no es una alternativa admisible y no entra en B − A.
- **Verificaciones.** Física de §4, incluida la dirección del despacho: carga agregada ≤ excedente FV y descarga agregada ≤ déficit. Mismo hash de contexto, bloques, rangos y semilla por origen. Contexto y bloques nunca posteriores al origen. Repetición exacta de las primeras horas con horizonte completo.
- **Exportaciones.** Tabla, CSV, cuatro figuras y `resumen.json`. No modifica la configuración.

**Ensayo CPU verificado** (`results/t05_evaluacion_verificada/resumen.json`, regenerable; 306 pruebas aprobadas):

- **Pronóstico.** Chronos-2 mejora el pronóstico diurno: MAE 64.20 frente a 66.33 W/m², pinball 25.13 frente a 25.94 W/m² y cobertura 0.1–0.9 de 80.95 % frente a 76.98 % (nominal 80 %). En las seis primeras horas de cada origen, el MAE baja de 66.97 a 60.77 W/m².
- **Servicio.** La mejora es pequeña: \(J_B-J_A=+505.599\) Wh·p (+0.18 %) y \(S_{\mathrm{pond}}\) 0.9328742419 frente a 0.9312263476. La diferencia se concentra en 4 de 28 días (+969.44, −488.86, −54.98 y +80.00 Wh·p); los otros 24 días son idénticos.
- **Cargas críticas y nivel de servicio.** El servicio crítico es del 100 % y el nivel de servicio horario, del 93.01 % en las tres políticas.
- **Reservas.** Beta eligió \(\rho=0\) en todos los orígenes y Chronos eligió \(\rho=0.25\) en dos.
- **Referencia reactiva.** Obtiene \(J=285240.845\): menor que A y B, aunque deja menos energía sin atender. Atiende más cargas de prioridad 1 (73.3 %) y menos de prioridad 2 (90.6 %). En la semana de calibración había superado a ambas ramas, así que no acota al MPC.
- **Consistencia.** Cero violaciones físicas: error del bus ≤ 1.2e−13 Wh y de batería ≤ 2.9e−13 Wh. Cero filtraciones.
- **Reproducibilidad y tiempos.** Dos ejecuciones completas produjeron CSV de resultados idénticos; en `tabla_ab.csv` solo cambian los tiempos medidos. Las figuras del artículo son copias de esta ejecución en `docs/figuras/`. La ejecución duró 266.2 s en CPU, con inferencias medias de 0.010 s (beta) y 0.028 s (Chronos) por origen.

**Lectura del resultado.** Un pronóstico mejor no se traduce automáticamente en más servicio. Con calendario fijo (\(z\le x\)), una mediana pesimista deja cargas sin comprometer y una optimista compromete cargas que luego se desconectan: Chronos registró 35 compromisos incumplidos y beta, 29. El efecto neto depende de pocos días nublados. Estas cifras son exploratorias y no permiten afirmar significancia.

**Análisis adicional: selección exacta (mochila)** (rama `experiment/seleccion-mochila`; regla de §4.4, adaptación del proyecto).

- **Motivación.** Un diagnóstico exploratorio de marzo, fuera del repositorio, comparó el MPC con un óptimo mensual con información perfecta (MILP con la física de §4). Mostró que cerca de la mitad de la distancia al óptimo se pierde a las 10:00 de los días lectivos: con 2000 W, la regla de prioridad enciende la licuadora (p=2, 600 W) y deja fuera seis ventiladores y un televisor, que suman más \(\sum p_kD_k\). Como la regla salió de mirar marzo, se decide con desarrollo y marzo solo confirma.
- **Desarrollo** (`uv run --locked --extra pronosticos python scripts/comparar_seleccion.py --salida results/seleccion_desarrollo`; 6 de enero–2 de marzo, 224 orígenes, λ_T y semillas de la configuración):

  | Política | J prioridad (Wh·p) | J mochila (Wh·p) | Diferencia |
  |---|---:|---:|---:|
  | Beta | 596763.723 | 599615.028 | +2851.305 (+0.48 %) |
  | Chronos-2 | 596883.430 | 599552.182 | +2668.752 (+0.45 %) |
  | Reactiva | 597696.046 | 601259.043 | +3562.997 (+0.60 %) |

  La energía no atendida baja de 33831.6 a 27442.6 Wh (beta) y de 33431.0 a 26427.5 Wh (Chronos-2). El servicio de prioridad 1 sube de 68.50 % a 79.94 % (beta) y de 69.28 % a 82.35 % (Chronos-2); el de prioridad 2 baja de 94.91 % a 92.07 % y de 94.68 % a 91.20 %. En las dos ramas MPC, la energía crítica no atendida pasa de 203.2 a 212.4 Wh, de 100820.8 Wh: la mochila gasta antes energía no crítica. En la reactiva no cambia.
- **Marzo** (`uv run --locked --extra pronosticos python -m src.experimento --seleccion mochila --salida results/t05_mochila`): \(J_A=288165.342\) (+2450.918, +0.86 %), \(J_B=288650.041\) (+2430.017, +0.85 %) y reactiva 287915.094 (+2674.250, +0.94 %) Wh·p. \(J_B-J_A=+484.699\) Wh·p, frente a +505.599 con la regla de prioridad: la regla pesa unas cinco veces más que el cambio de pronóstico. La energía no atendida baja de 16418.2/16352.9 a 12300.4/12245.0 Wh (A/B). La prioridad 1 sube a 85.38 %/84.47 % y la 2 baja a 88.87 %/89.62 %. El servicio crítico sigue en 100 %, el nivel de servicio horario en 93.01 % y los compromisos incumplidos pasan de 29/35 a 30/39.
- **Verificaciones.** Cero violaciones físicas y cero filtraciones; pareo de los 112 orígenes y repetición interna exacta (30 h, 5 orígenes, 0 Wh). Con la regla predeterminada, `python -m src.experimento` reproduce T05: CSV de resultados idénticos byte a byte y, en `tabla_ab.csv`, solo cambian los tiempos medidos. En marzo, la ejecución con mochila duró 281.6 s en CPU.
- **Lectura y límites.** La mejora no depende del pronóstico y aparece en las tres políticas y en ambos periodos. Su costo es un cambio de criterio: maximiza (10) en lugar del orden estricto de prioridad del algoritmo 2, por lo que baja el servicio de prioridad 2. Adoptarla es una decisión de modelado, no un resultado validado. Los resultados son exploratorios, con demanda sintética y una sola realización.

**Correspondencia con los indicadores del artículo (§4.2).** El artículo mide el *service level* (porcentaje de réplicas en que se cubre la demanda) y el *missing of fulfilment* (porcentaje medio no cubierto). T05 calcula sus análogos sobre la trayectoria realizada: la proporción de horas con demanda atendida por completo y la fracción media no atendida en las horas con déficit. La diferencia está en la base: horas de una sola realización, no réplicas. Los bloques y rangos comunes de A y B cumplen el papel de los números aleatorios comunes que usa el artículo (§3.1) para comparar soluciones. No se compara numéricamente con el caso Volcanes, porque los datos, el sistema y la demanda difieren.

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

## 8. Implementación compacta: T01–T05 implementadas

Conservar `data/`, la ingesta/generación existente de `src/` y sus pruebas actuales. T04 añade únicamente el peso terminal compartido a la configuración; los parámetros físicos conservan sus supuestos. No crear API, base de datos, interfaz web, framework de servicios ni abstracciones innecesarias.

**Módulos funcionales, sin capas adicionales:**

```text
src/
├── pronosticos.py   # Chronos-2, beta y correcciones comunes
├── simulador.py     # FV, baterías, despacho y cargas
├── controlador.py   # escenarios y MPC compartido
└── experimento.py   # T05: evaluación pareada, métricas, tabla y gráficos
```

La implementación puede reunir funciones cortas en menos archivos si resulta más clara; **la estructura es orientativa**, no un requisito arquitectónico. Una configuración y un comando de experimento son suficientes. Preferir `numpy`, `pandas`, `scipy`, `matplotlib` y dependencias oficiales de Chronos-2 sobre desarrollar librerías propias. Añadir pruebas breves y enfocadas en invariantes matemáticos, tiempo de pronóstico y comparación justa; evitar pruebas extensas de infraestructura que no intervienen en la investigación.

| Tarea / issue de GitHub | Resultado mínimo verificable | Estado |
|---|---|---|
| **T01 — Datos** ([#1](https://github.com/ThomasSerna/photovoltaic-forecast-optimization/issues/1)) | CSV, demanda semisintética, configuración y validación reproducibles | **Completada** |
| **T02 — Chronos-2** ([#2](https://github.com/ThomasSerna/photovoltaic-forecast-optimization/issues/2)) | Pronosticar 24 h a partir de historia válida y graficar frente a irradiancia real | **Completada** |
| **T03 — Simulación energética** ([#3](https://github.com/ThomasSerna/photovoltaic-forecast-optimization/issues/3)) | Física corregida por batería, heurística y restricciones verificables | **Completada** |
| **T04 — Beta y MPC** ([#4](https://github.com/ThomasSerna/photovoltaic-forecast-optimization/issues/4)) | Referencia beta + controlador común que utiliza pronósticos de ambas ramas | **Completada y verificada en CPU** |
| **T05 — Experimentación** ([#5](https://github.com/ThomasSerna/photovoltaic-forecast-optimization/issues/5)) | Comparar A/B, exportar tabla y gráficas, explicar resultados y límites | **Completada y verificada en CPU** |
| **Selección exacta (mochila)** (adaptación, rama `experiment/seleccion-mochila`) | Opción `seleccion_cargas` desactivada por defecto; T05 sin cambios; comparación en desarrollo con `scripts/comparar_seleccion.py` | **Evaluada en CPU (desarrollo y marzo)** |

Las antiguas issues **#6 y #7** quedan absorbidas por T04 y T05; no se mantienen como trabajos adicionales. T03 incorpora la física; T04 integra pronósticos y simulador, y T05 presentará el experimento final.

## 9. Entregables y límites

1. Repositorio ejecutable y reproducible, **sin** aplicación compleja.
2. Tabla y visualizaciones de comparación beta frente a Chronos-2.
3. Artículo del curso: resumen, introducción, literatura, método, resultados, discusión, conclusiones y referencias; más presentación oral. Según los lineamientos, la presentación final es el **4 o 5 de noviembre de 2026**.

**Fuera del alcance:** medición en vivo, instalación física, diseño de electrónica de potencia, optimización económica, entrenamiento de Chronos-2, API, dashboard, pronóstico de demanda, comparación masiva de algoritmos, garantía de operación segura en hardware y reproducción numérica exacta del caso Volcanes.

**Fuentes:** Acuña et al. (2021), [DOI:10.3390/en14102789](https://doi.org/10.3390/en14102789); *Lineamientos del proyecto final* (Angélica M. González, 2026); [Amazon Chronos-2, Hugging Face](https://huggingface.co/amazon/chronos-2). Los supuestos y decisiones experimentales de este documento son **propuesta de implementación**, no hallazgos de esas fuentes.
