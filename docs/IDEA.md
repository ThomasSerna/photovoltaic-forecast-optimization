# IDEA.md — Proyecto de Optimización 1

## Chronos-2 para planificación energética fotovoltaica en una zona no interconectada

**Caso de estudio:** sistema fotovoltaico aislado *hipotético*, contextualizado en el Centro Educativo Rural El Rayo, Támesis (Antioquia).  
**Naturaleza:** experimento computacional semisintético; no instalación, control real ni diagnóstico energético verificado de la institución.  
**Estado:** la simulación, los pronósticos y los dos modos de MPC están implementados y se han evaluado en CPU. El modo original se evaluó en marzo; el modo CVaR se calibró con datos de desarrollo. El borrador `docs/articulo.md` presenta la evaluación del modo original.
**Criterio de diseño:** **mantener una implementación sencilla sin simplificar las matemáticas ni relajar restricciones físicas.**

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

## 3. Datos existentes

Se **reutiliza** el código, los CSV y la configuración ya publicados; no se reimplementa la ingesta. El comando de preparación `energy-project` valida datos, sin simular energía. El ensayo de pronóstico ejecuta Chronos-2 mediante `python -m src.pronosticos`, con el extra `pronosticos`; el simulador expone `src.simulador.simular` desde Python.

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

### 4.5. Correspondencia con el artículo y diferencias

Se consultaron §2.2 (pp. 5–6) y los algoritmos 1–2 (p. 8) del [PDF oficial](https://mdpi-res.com/d_attachment/energies/energies-14-02789/article_deploy/energies-14-02789.pdf). Los índices día/periodo del artículo se reúnen en la secuencia horaria \(t\).

| Referencia | Interpretación física implementada |
|---|---|
| (1)–(3), (7) | Límites individuales explícitos y descarga limitada por energía útil, sin variable auxiliar \(Q\) ni big-M. Con \(c_{\min}>0\) y \(Q\) binaria, (1)–(2) imprimen \(Q\le C/c_{\min}\le Q+1\), imponiendo inadvertidamente \(C\le2c_{\min}\); no se incorpora esa cota ajena a la capacidad configurada. |
| (4) | Para \(E=Q=1\), la desigualdad impresa exige \(BD\ge C-c_{\min}\), frente al máximo descrito en el texto. Además, (5) define \(C\) después del flujo del mismo periodo. Se limita la descarga con el estado **anterior**, \(d\le\eta_d(C_t-C_{\min})\). |
| (5)–(6) | Continuidad por batería y conservación en el bus. Se añaden eficiencias individuales y vertido explícito; no se crea energía ni se descuenta una carga rechazada. |
| (8)–(9) | Servicio completo y solicitudes binarias: \(z\le x\le r\). La (9) impresa compara una decisión binaria con demanda en Wh; se usa el indicador \(r\), conservando la energía en el balance. |
| (10) | Beneficio ponderado de las cargas efectivamente atendidas. La selección voraz busca ese criterio sin certificar un máximo global. |
| Algoritmos 1–2 | El primero despacha usando un orden por consumo y el segundo selecciona según prioridad. Para evitar descontar energía de una selección provisional distinta, el simulador selecciona primero y despacha una sola vez. Prioridad, consumo, desempate por identificador y reparto por batería quedan fijados explícitamente. |

La conversión FV, el paso horario, las eficiencias, los límites de potencia y la topología común son supuestos físicos del caso semisintético. Estas decisiones concretan una adaptación consistente; no validan numéricamente el caso original de Volcanes ni el comportamiento eléctrico dentro de una hora.

### 4.6. Interfaz reproducible del simulador

`simular(demanda, irradiancia, configuracion, *, estado_inicial=None, compromisos=None, reserva=0)` recibe la demanda existente, una tabla explícita `t, ghi_w_m2` y la configuración validada. El estado opcional es `{bateria_id: energia_wh}`; los compromisos opcionales son `t, carga_id, x` para todas las filas de demanda. Se validan horas consecutivas, claves, dominios y límites individuales, sin modificar entradas ni buscar meteorología por cuenta propia. El parámetro `reserva` en [0, 1] se usa solo para generar candidatos (§6); cero conserva el comportamiento sin reserva y una reserva no nula se rechaza junto con compromisos fijos.

Devuelve tablas `cargas` (solicitud, compromiso y servicio), `horas` (energías y vertido), `baterias` (estados y flujos individuales), el diccionario `estado_final` y un `resumen` de energías, \(J\) y \(S_{\mathrm{pond}}\). No escribe archivos. Las pruebas `test_continuidad_entre_horizontes_y_entradas_sin_mutacion` y `test_compromiso_fijo_con_deficit_y_sin_sustituciones` de `tests/test_simulador.py` muestran la continuación mediante estados y la ejecución de un calendario fijo. La rutina es determinista y no introduce semillas nuevas; los datos sintéticos conservan sus semillas declaradas. Las pruebas incluyen casos manuales, heterogeneidad y recorrido completo de desarrollo; la comparación de control y pronósticos se describe en §7.

## 5. Pronósticos y escenarios: única diferencia A/B

**A — Beta:** se usa una beta escalada de dos parámetros por hora local: masa de cero empírica y ajuste por máxima verosimilitud de los positivos con `loc=0` y `scale=1.05*max(positivos)`. Se usan las mismas **336 horas consecutivas, terminadas y disponibles** que recibe Chronos. Si todos los valores son cero se devuelve cero; si los positivos son constantes se usa una masa puntual mezclada con cero. Un ajuste inválido o contexto incompleto produce error, sin cambiar de familia durante evaluación. La escala es un soporte estadístico aprendido, no un máximo físico de GHI.

Se descartó el ajuste libre de cuatro parámetros tras un piloto de desarrollo con los orígenes locales 6 de enero de 2025 a las 00:00 y 12:00, y 13 de enero a las 00:00: 36 ajustes de las horas 06–17, cada uno con 14 positivos. Cuatro produjeron soporte negativo y once emitieron advertencias, aunque todos devolvieron cuantiles 0.1, 0.5 y 0.9 finitos. Los 36 ajustes escalados fueron válidos y no emitieron advertencias. Entorno: Python 3.12.14, SciPy 1.18.1, NumPy 2.5.4 y pandas 3.0.6. Esto justifica la adaptación prevista antes del test; no valida el ajuste estadístico en todos los orígenes. El caso original ajusta beta4 con datos distintos (§5 del paper); no se trasladan sus parámetros a Támesis. SciPy [documenta el problema no acotado del ajuste libre por máxima verosimilitud](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.beta.html).

**B — Chronos-2:** usar el checkpoint oficial sin reentrenar ni ajustar pesos; cargarlo una sola vez, utilizar únicamente la historia disponible y emitir cuantiles para el horizonte. Valores iniciales: contexto **336 h**, horizonte **24 h** y cuantiles **0.1 a 0.9**. Un ensayo verificó API, memoria y tiempo mediante dos orígenes de desarrollo y repetición del primero en CPU/FP32. Se fija `chronos-forecasting==2.3.2` y la revisión `29ec3766d36d6f73f0696f85560a422f50e8498c` de `amazon/chronos-2`; configuración, resultados, versiones y hashes se registran en el informe del ensayo. Se exige contexto consecutivo completo, sin interpolar horas no disponibles. Esto no sustituye la evaluación retrospectiva descrita en §7.

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

### 6.1. Modo original

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

`planificar_mpc(...)` devuelve los cuatro calendarios, sus puntuaciones y el compromiso elegido; los empates favorecen la menor reserva. Los calendarios idénticos reutilizan su evaluación. `ejecutar_mpc(caso, pronosticar, *, inicio, fin, estado_inicial=None)` recibe un callback `(historia_admisible, origen_utc, horizonte)` que devuelve cuantiles crudos; aplica las correcciones y escenarios comunes y entrega las tablas del simulador, decisiones y pronósticos con sus diagnósticos. El calendario validado alinea UTC y `t`. Al terminar el periodo, \(H_{\mathrm{ef}}=\min(H,\text{horas restantes})\) y se ejecutan como máximo \(R\) horas restantes. Se concatenan solo resultados realizados y se recalcula \(S_{\mathrm{pond}}\) sobre sus totales. `src/experimento.py` carga Chronos una sola vez y `controlador.proveedores` crea los callbacks de beta y Chronos que recibe `ejecutar_mpc`.

**Prueba de anticipación implementada:** en un caso artificial de dos horas y batería útil de 1 Wh, sin sol ahora, con carga actual de prioridad 1 y futura de prioridad 3, la decisión presente cambia si el pronóstico futuro —mediana y escenarios— pasa de 0 a 1 Wh solar. Las pruebas también cubren compromisos fijos, continuidad realizada, reservas heterogéneas, igualdad con proveedores idénticos y ausencia de información futura. Esto verifica la lógica anticipatoria del MPC, sin certificar optimalidad. El artículo selecciona soluciones frecuentes y propone MPC como extensión (§§3 y 6); este controlador y su puntuación son adaptaciones del proyecto.

### 6.2. Modo sensible al riesgo

El modo sensible al riesgo ofrece una selección alternativa, común a beta y Chronos-2. Este MPC y el uso de CVaR son adaptaciones del proyecto: el artículo original no los implementa en sus experimentos. Para cada origen, genera hasta 12 calendarios con la heurística de §4.4, combinando los cuantiles horarios 0.1, 0.5 y 0.9 con las cuatro reservas anteriores. Calendarios idénticos se evalúan una sola vez; se conserva como representante el de menor reserva y, después, menor cuantil. Todos se simulan como compromisos fijos sobre los mismos 50 escenarios, con idéntico estado inicial individual. La reserva se usa únicamente para proponer el calendario: la evaluación y la ejecución mantienen los balances, la topología y los límites físicos de §4.

Para un calendario \(a\), un escenario \(s\) y todo el horizonte efectivo \(H_{\mathrm{ef}}\), la pérdida de servicio es:

\[
L_s(a)=\sum_{h=1}^{H_{\mathrm{ef}}}\sum_k p_{k,h}D_{k,h}\bigl(1-z_{k,h}^{(a,s)}\bigr).
\]

Incluye toda la demanda solicitada no atendida, también la que no se comprometió en el calendario, y se expresa en Wh·p, igual que \(J\). El modo riesgo elige el mayor valor de:

\[
\mathrm{Score}_{\mathrm{CVaR}}(a)=\frac{1}{N}\sum_{s=1}^{N}J_s(a)-\lambda_R\,\mathrm{CVaR}_{\alpha}(L(a)).
\]

Este modo **no** incluye el término terminal \(\lambda_T\); el modo original conserva su puntuación y sus cuatro filas de reservas. Con empates de puntuación se elige la menor reserva y, después, el menor cuantil. Un peso \(\lambda_R=0\) en modo CVaR no equivale necesariamente al original: el conjunto de candidatos es más amplio.

El CVaR empírico promedia la peor fracción \(1-\alpha\) de las pérdidas. Sean \(\ell_1\ge\cdots\ge\ell_N\) las pérdidas ordenadas, \(m=N(1-\alpha)\) y \(k=\lfloor m\rfloor\):

\[
\mathrm{CVaR}_{\alpha}=\frac{\sum_{i=1}^{k}\ell_i+(m-k)\ell_{k+1}}{m}.
\]

La fracción \(m-k\) pesa el escenario en la frontera en vez de redondear la cantidad de escenarios; véase [Rockafellar y Uryasev](https://sites.math.washington.edu/~rtr/papers/rtr187-CVaR2.pdf). Con \(N=50\) y el valor inicial \(\alpha=0.9\) se promedian los cinco peores. Con \(\alpha=0.95\) se usan los dos peores y la mitad del tercero. El CVaR describe esta muestra: 50 escenarios no garantizan calibración de las colas ni protección fuera de muestra.

La configuración acepta `modo_mpc` igual a `original` o `cvar`; si se omite, usa `original`. En modo CVaR, `lambda_riesgo` debe proporcionarse explícitamente, ser finito y no negativo; `alpha_cvar` es opcional, con valor inicial 0.9 y dominio \(0<\alpha<1\). `lambda_terminal` se sigue usando en el modo original. Una corrida usa el mismo modo y parámetros para beta y Chronos-2. El calibrador del peso terminal solo acepta el modo original; la calibración del riesgo usa desarrollo, sin usar los resultados reservados de marzo.

`candidatos.csv` registra \(J\) esperado, pérdida media, CVaR, puntuación, cuantil y reserva de cada calendario único; `planes_elegidos.csv` conserva el calendario completo seleccionado, incluidas filas con \(x=0\) y horas aún no ejecutadas. Origen, política e identificador enlazan ambos registros. `decisiones.csv` resume el elegido en cada origen y `resumen.json` conserva el modo y parámetros. Las tablas físicas siguen reflejando únicamente la ejecución realizada; al final se usa \(H_{\mathrm{ef}}=\min(H,\text{horas restantes})\).

**Caso manual de aceptación:** con una batería de 1 Wh inicialmente llena, sin generación presente, demanda presente de 0.30 Wh con prioridad 1 y futura de 1 Wh con prioridad 3, se consideran 49 escenarios con 1 Wh de generación futura y uno sin generación. El plan A compromete ambas cargas y obtiene \(E[J]=3.24\), \(\mathrm{CVaR}_{0.9}=0.60\); el plan B compromete solo la futura y obtiene \(E[J]=3.00\), \(\mathrm{CVaR}_{0.9}=0.30\). Con \(\lambda_R=0\) gana A y con \(\lambda_R=1\) gana B (puntuaciones 2.64 y 2.70). Es una comprobación de sensibilidad, no una validación de los parámetros para el caso real.

## 7. Protocolo experimental y resultados

**Diseño:** comparar A y B sobre exactamente las mismas fechas, demanda programada, capacidades, estado inicial, información histórica admisible y condiciones meteorológicas realizadas. Durante la simulación **no se igualan artificialmente** sus baterías después de cada decisión. No ajustar parámetros usando resultados finales del test.

**Calibración del peso terminal:** usar únicamente 6–12 de enero de 2025, con fin exclusivo el 13. Probar \(\lambda_T\in\{0,0.25,0.5,0.75\}\), reutilizando pronósticos y rangos por origen entre pesos. Las ocho combinaciones `(proveedor, peso)` parten de idéntica inicialización y conservan estados continuos independientes. Elegir el peso que maximiza el promedio del **J realizado** de A y B; desempatar por el menor peso. `calibrar_mpc(caso, pipeline=None)` devuelve ocho resultados, sus tablas y pronósticos, fechas y peso elegido, sin modificar entradas. El comando `uv run --locked --extra pronosticos python -m src.controlador --salida results/calibracion_terminal` verifica balances y límites con tolerancia de \(10^{-6}\) Wh y comprueba que entradas y código no cambiaron. Solo después prepara ambos archivos temporales, publica `resumen.json` y reemplaza la configuración con el peso elegido como último paso; si falla el reemplazo, retira el informe y conserva la configuración original. Se limpian los temporales tanto al terminar como ante un error. Registra fechas, puntuaciones, semillas, bloques, cuantiles crudos/corregidos, diagnósticos, versiones y hashes. Incluye tanto la configuración de entrada como la final; el hash del archivo de configuración describe la entrada anterior a su actualización. La salida debe ser nueva.

**Ensayo CPU completado:** 28 orígenes por proveedor, 50 escenarios por origen y ocho recorridos de 168 h, en 1266.3 s con pesos en caché. Los cuatro pesos empataron: en beta, \(J=73192.486644\), \(S_{\mathrm{pond}}=0.9540504745\); en Chronos, \(J=73784.143168\), \(S_{\mathrm{pond}}=0.9617626074\). El promedio pareado fue 73488.314906 y el desempate fijó **\(\lambda_T=0\)** en la configuración común. No hubo violaciones físicas; errores máximos observados del bus \(2.8422\times10^{-14}\) Wh y de batería \(2.2738\times10^{-13}\) Wh (cotas redondeadas hacia arriba), menores que \(10^{-6}\) Wh. Se comprobaron los mismos bloques/semillas en A/B, cuantiles finitos, hashes de código y CSV y coherencia de la configuración final. En esa verificación pasaron **260 pruebas** con el entorno bloqueado. Registro local regenerable: `results/calibracion_terminal_verificada/resumen.json`. Esto documenta calibración, **no** la evaluación retrospectiva de marzo ni una conclusión general sobre la utilidad del término terminal o la superioridad de Chronos.

### Calibración y validación del riesgo

**Campaña completada y verificada en CPU.** Las fechas se expresan en `America/Bogota` y los intervalos incluyen el inicio y excluyen el fin.

- **Ajuste:** del 6 de enero al 17 de febrero de 2025. Para ambos proveedores se fija \(\alpha=0.9\) y se prueban los pesos \(\lambda_R\in\{0,0.25,0.5,1,2\}\). En cada origen se reutilizan entre pesos los pronósticos de cada proveedor y los rangos y semillas comunes; cada combinación conserva su propia trayectoria y estados individuales de baterías.
- **Selección común:** se admite un peso si el \(J\) realizado de cada proveedor alcanza al menos el 99 % del obtenido con \(\lambda_R=0\). Entre los admitidos se minimiza el promedio entre proveedores del CVaR diario de la pérdida **realizada** y ponderada por prioridad. En empate se elige el mayor \(J\) promedio y, si persiste, el menor peso. El peso se elige solo con ajuste y se comparte entre beta y Chronos.
- **Validación:** del 17 de febrero al 3 de marzo de 2025 se comparan el peso elegido, CVaR con \(\lambda_R=0\) y el modo original. Cada recorrido continúa con sus propios estados de batería al final del ajuste. Se respalda el peso si, frente a CVaR con peso cero, cada proveedor conserva al menos el 99 % de \(J\) y el promedio entre proveedores del CVaR diario no empeora. Si no se cumplen ambas condiciones, no se recomienda el peso ni se reajusta con validación.

La pérdida diaria suma la demanda no atendida multiplicada por su prioridad, en Wh·p. Su CVaR empírico aplica la fórmula de §6.2 a los días locales realizados: es distinto del CVaR de los 50 escenarios pronosticados que usa el MPC al elegir cada plan. En los 14 días de validación, la cola al 90 % equivale a 1.4 días: el peor completo y el 40 % del segundo. Esta muestra pequeña limita la interpretación y no garantiza protección fuera de muestra. El MPC y esta calibración son adaptaciones del proyecto, no resultados del artículo de Acuña et al.

La campaña verifica balances y límites con tolerancia de \(10^{-6}\) Wh, continuidad individual, ausencia de información futura y repetición de las primeras 48 horas de cada fase; conserva la interfaz y los pesos de los pronosticadores. En esta campaña, marzo se utilizó exclusivamente para una regresión del modo original contra la versión previa al modo CVaR (`9bc279c7caa2aa81eab10567d48741e93fd10f9f`), con los mismos pronósticos congelados, y no para elegir o validar el peso de riesgo.

```powershell
uv run --locked --extra pronosticos python -m src.controlador --modo cvar --salida results/calibracion_riesgo
uv run --locked --extra pronosticos python -m scripts.verificar_regresion_original --salida results/regresion_original
```

Las carpetas de salida deben ser nuevas. La calibración guarda `resumen.json` y `configuracion_cvar.json` por separado, con rutas a datos válidas sin alterar la configuración original. El informe registra parámetros, periodos, criterios, métricas por proveedor y peso, validación, semillas, versiones y hashes de entradas, código y archivos generados. La configuración se produce aun cuando la validación falle; en ese caso el informe indica que el peso no se recomienda.

**Resultado del ajuste (42 días):** los cinco pesos cumplieron el umbral de servicio de ambos proveedores. Las medias de las dos ramas fueron:

| \(\lambda_R\) | \(J\) medio (Wh·p) | CVaR diario medio (Wh·p) |
|---:|---:|---:|
| 0 | 447641.358 | 3083.645 |
| 0.25 | 447367.453 | 3083.645 |
| 0.5 | 447367.453 | 3083.645 |
| 1 | 447688.355 | 3103.842 |
| 2 | 446864.562 | 3126.470 |

Los pesos 0, 0.25 y 0.5 empataron en el menor CVaR diario medio; el mayor \(J\) entre ellos seleccionó **\(\lambda_R=0\)**. Aunque el peso 1 obtuvo un \(J\) medio mayor, su CVaR diario fue más alto y no ganó el criterio principal. El servicio crítico durante el ajuste fue 99.7311 % en todas las trayectorias.

**Validación posterior (14 días):** la tabla compara el modo CVaR seleccionado con el modo original; la pérdida de cola es el CVaR diario realizado, no el riesgo pronosticado por el controlador.

| Proveedor | Modo | \(J\) (Wh·p) | \(S_{\mathrm{pond}}\) | CVaR diario (Wh·p) |
|---|---|---:|---:|---:|
| Beta | CVaR, \(\lambda_R=0\) | 150935.735 | 0.9390092330 | 2809.385 |
| Beta | Original | 150036.190 | 0.9334129375 | 3226.657 |
| Chronos-2 | CVaR, \(\lambda_R=0\) | 150794.594 | 0.9381311592 | 2910.200 |
| Chronos-2 | Original | 150012.166 | 0.9332634817 | 3465.659 |

El informe marca el peso como recomendado según la regla prefijada. Como el elegido es cero, la comparación de validación contra CVaR con peso cero es una comparación consigo mismo y cumple la regla por construcción; no demuestra beneficio de una penalización positiva. Frente al modo original, el CVaR diario realizado disminuyó 12.93 % para beta y 16.03 % para Chronos, con aumentos de \(J\) de 0.60 % y 0.52 %, respectivamente. El modo CVaR con peso cero considera más candidatos que el original: esas diferencias no se atribuyen a aversión al riesgo. La atención crítica fue del 100 % en todas las trayectorias de validación.

**Verificación y artefactos:** la campaña tardó 2643.9 s en CPU; registró 448 pronósticos únicos en el recorrido principal (224 orígenes por proveedor), con 920 evaluaciones y 1320 reutilizaciones de candidatos. No hubo violaciones físicas ni filtraciones; los errores máximos observados fueron \(1.14\times10^{-13}\) Wh en el bus y \(2.28\times10^{-13}\) Wh en baterías. La repetición comprendió 64 bloques y 32 pares origen–proveedor: la diferencia máxima de cuantiles fue cero y las tablas y decisiones coincidieron dentro de \(10^{-6}\). Se aprobaron 379 pruebas automatizadas. El registro local regenerable `results/calibracion_riesgo/` contiene el resumen, la configuración CVaR, pronósticos, selección, métricas de ajuste y validación, diario, decisiones y candidatos; `resumen.json` incluye sus hashes y los de las entradas, el código y `uv.lock`.

**Regresión del modo original verificada en CPU:** `results/regresion_original/resumen.json` registra la comparación de las 672 horas y 112 orígenes por proveedor con el controlador anterior al modo CVaR, usando los mismos pronósticos congelados. Coincidieron las tablas físicas y decisiones dentro de la tolerancia de \(10^{-6}\), y los indicadores seleccionados coincidieron con los publicados en la evaluación original según su precisión. La repetición de 30 horas y cinco orígenes dio diferencias máximas de cero; no se detectaron violaciones físicas ni filtraciones. Los mayores errores observados fueron \(1.14\times10^{-13}\) Wh en el bus y \(2.85\times10^{-13}\) Wh en baterías. La ejecución tardó 1284.4 s; el informe conserva semillas, versiones y hashes de entradas, código, guion y salidas. Estos resultados solo verifican el modo original, no calibran ni validan CVaR.

Para reproducir esta regresión debe estar disponible en el historial Git local el objeto `9bc279c7caa2aa81eab10567d48741e93fd10f9f`. El guion lee únicamente el controlador de esa revisión y no modifica el checkout. En la revisión de implementación `d1ca484`, la suite completa aprobó 379 pruebas en 137.74 s.

**Evaluación pareada del modo original:** `uv run --locked --extra pronosticos python -m src.experimento --salida results/evaluacion` (`src/experimento.py`).

- **Diseño.** Ejecuta A y B en el periodo reservado (3 al 31 de marzo de 2025, fin exclusivo; 112 orígenes por rama), con los callbacks comunes de `controlador.proveedores` y el mismo estado inicial configurado. Después conserva estados independientes.
- **Referencia reactiva.** Añade la heurística de §4.4 ejecutada con la GHI realizada de cada hora. Es una adaptación del proyecto con información perfecta de la hora en curso: sirve para interpretar, no es una alternativa admisible y no entra en B − A.
- **Verificaciones.** Física de §4, incluida la dirección del despacho: carga agregada ≤ excedente FV y descarga agregada ≤ déficit. Mismo hash de contexto, bloques, rangos y semilla por origen. Contexto y bloques nunca posteriores al origen. Repetición exacta de las primeras horas con horizonte completo.
- **Exportaciones.** Tabla, CSV, cuatro figuras y `resumen.json`. No modifica la configuración.

**Ensayo CPU verificado** (`results/evaluacion_verificada/resumen.json`, regenerable; 306 pruebas aprobadas):

- **Pronóstico.** Chronos-2 mejora el pronóstico diurno: MAE 64.20 frente a 66.33 W/m², pinball 25.13 frente a 25.94 W/m² y cobertura 0.1–0.9 de 80.95 % frente a 76.98 % (nominal 80 %). En las seis primeras horas de cada origen, el MAE baja de 66.97 a 60.77 W/m².
- **Servicio.** La mejora es pequeña: \(J_B-J_A=+505.599\) Wh·p (+0.18 %) y \(S_{\mathrm{pond}}\) 0.9328742419 frente a 0.9312263476. La diferencia se concentra en 4 de 28 días (+969.44, −488.86, −54.98 y +80.00 Wh·p); los otros 24 días son idénticos.
- **Cargas críticas y nivel de servicio.** El servicio crítico es del 100 % y el nivel de servicio horario, del 93.01 % en las tres políticas.
- **Reservas.** Beta eligió \(\rho=0\) en todos los orígenes y Chronos eligió \(\rho=0.25\) en dos.
- **Referencia reactiva.** Obtiene \(J=285240.845\): menor que A y B, aunque deja menos energía sin atender. Atiende más cargas de prioridad 1 (73.3 %) y menos de prioridad 2 (90.6 %). En la semana de calibración había superado a ambas ramas, así que no acota al MPC.
- **Consistencia.** Cero violaciones físicas: error del bus ≤ 1.2e−13 Wh y de batería ≤ 2.9e−13 Wh. Cero filtraciones.
- **Reproducibilidad y tiempos.** Dos ejecuciones completas produjeron CSV de resultados idénticos; en `tabla_ab.csv` solo cambian los tiempos medidos. Las figuras del artículo son copias de esta ejecución en `docs/figuras/`. La ejecución duró 266.2 s en CPU, con inferencias medias de 0.010 s (beta) y 0.028 s (Chronos) por origen.

**Lectura del resultado.** Un pronóstico mejor no se traduce automáticamente en más servicio. Con calendario fijo (\(z\le x\)), una mediana pesimista deja cargas sin comprometer y una optimista compromete cargas que luego se desconectan: Chronos registró 35 compromisos incumplidos y beta, 29. El efecto neto depende de pocos días nublados. Estas cifras son exploratorias y no permiten afirmar significancia.

**Correspondencia con los indicadores del artículo (§4.2).** El artículo mide el *service level* (porcentaje de réplicas en que se cubre la demanda) y el *missing of fulfilment* (porcentaje medio no cubierto). La evaluación calcula sus análogos sobre la trayectoria realizada: la proporción de horas con demanda atendida por completo y la fracción media no atendida en las horas con déficit. La diferencia está en la base: horas de una sola realización, no réplicas. Los bloques y rangos comunes de A y B cumplen el papel de los números aleatorios comunes que usa el artículo (§3.1) para comparar soluciones. No se compara numéricamente con el caso Volcanes, porque los datos, el sistema y la demanda difieren.

### Comparación posterior de los MPC con Chronos-2

**Comparación completada y verificada:** después de calibrar el riesgo se compararon los dos controladores con Chronos-2 del 3 al 30 de marzo de 2025. Esta evaluación es posterior a la selección del peso y no modifica ni vuelve a ajustar \(\alpha=0.9\) o \(\lambda_R=0\). La campaña de calibración descrita arriba no evaluó el modo CVaR en marzo. Como marzo ya fue analizado en la evaluación original, la comparación posterior es exploratoria y no constituye una validación independiente nueva.

El diseño reutiliza los 112 pronósticos crudos guardados en `results/regresion_original/` para ejecutar el modo CVaR con la configuración generada por la calibración del riesgo; el MPC original se toma de la regresión verificada. Ambas trayectorias usan las mismas fechas, demanda, datos físicos, pronósticos y estado inicial, y luego mantienen estados de batería independientes. La comparación no vuelve a cargar ni ajustar Chronos-2.

```powershell
uv run --locked --extra pronosticos python -m scripts.comparar_mpc_chronos --salida results/comparacion_mpc_chronos
```

La salida incluye las figuras `servicio_diario`, `diferencias_servicio`, `baterias` y `resumen_metricas`, en PNG y SVG dentro de `results/comparacion_mpc_chronos/figuras/`; también contiene `diario.csv`, `metricas.csv`, las tablas del recorrido nuevo y `resumen.json`. Estos archivos son locales y regenerables: `results/` está ignorado por Git. Las figuras del artículo permanecen asociadas a la evaluación original.

| Indicador | MPC original | MPC nuevo | Diferencia nuevo − original |
|---|---:|---:|---:|
| \(J\) realizado (Wh·p) | 286220.023 | 286406.573 | +186.549 |
| \(S_{\mathrm{pond}}\) (%) | 93.2874 | 93.3482 | +0.0608 puntos porcentuales |
| Energía no atendida (Wh) | 16352.931 | 16339.657 | −13.275 |
| CVaR diario de pérdida (Wh·p) | 3949.084 | 3887.228 | −61.855 |
| Servicio crítico (%) | 100 | 100 | 0 |
| Nivel de servicio horario (%) | 93.0060 | 93.0060 | 0 |
| Compromisos incumplidos (carga·h) | 35 | 45 | +10 |

El aumento de \(J\) fue 0.0652 % y se concentró en el 4 y el 5 de marzo (+51.259 y +135.290 Wh·p); en los otros 26 días \(J\) fue igual. El CVaR diario realizado disminuyó 1.57 %, pero las solicitudes desatendidas aumentaron de 229 a 230. La muestra comprende 28 días y la cola al 90 % representa 2.8 días efectivos; estas cifras no prueban significancia ni un beneficio por penalización de riesgo, ya que \(\lambda_R=0\) y el conjunto de candidatos del modo nuevo es más amplio.

La comparación usó 672 horas y 112 orígenes de Chronos-2. Los cuantiles crudos y corregidos coincidieron exactamente; no se detectaron filtraciones ni violaciones físicas, con errores máximos de \(5.69\times10^{-14}\) Wh en el bus y \(2.28\times10^{-13}\) Wh en baterías. Pasaron seis pruebas automatizadas de la comparación. La ejecución tardó 370.6 s sin inferencia de Chronos; este tiempo no es comparable como mejora de velocidad frente a la evaluación original. El informe registra hashes de entradas, código y archivos generados.

### Comparación de beta sin MPC frente a Chronos con MPC

**Comparación completada y verificada:** se contrastó una planificación diaria con beta frente a la trayectoria ya calculada de Chronos-2 con el modo CVaR calibrado (\(\alpha=0.9\), \(\lambda_R=0\), horizonte de 24 h y replanificación cada 6 h). Ambas usan las mismas fechas del 3 al 30 de marzo de 2025, demanda, realización solar, sistema físico y estado inicial; después mantienen sus propios estados de batería. La trayectoria de Chronos procede de `results/comparacion_mpc_chronos/` y se verificó mediante sus hashes.

La alternativa beta es una **adaptación** de la selección por frecuencia de [Acuña et al. (2021), §3.1](https://doi.org/10.3390/en14102789). Cada día a las 00:00 locales genera el pronóstico beta existente a partir de 336 h disponibles y construye 50 escenarios. Para cada escenario aplica la heurística física con reserva cero desde el mismo estado inicial de ese día. Dos calendarios se consideran vecinos directos si al menos el 90 % de las cargas tienen el mismo perfil encendido/apagado durante **las 24 horas completas**, no si coincide el 90 % de las celdas. Se elige el calendario con más vecinos directos, incluido él mismo; en empate, el del primer escenario. La vecindad no se cierra transitivamente.

El calendario se fija antes de conocer la irradiancia realizada y se ejecuta durante las 24 h sin replanificación intradía; si falta energía se respeta la física y no se sustituyen compromisos. Por tanto, beta sin MPC no es el MPC de horizonte deslizante ni la referencia reactiva que conoce la irradiancia de la hora. Usa el pronóstico beta de dos parámetros escalado y corregido, la física actual y 50 escenarios fijos sin parada adaptativa; el paper presenta beta de cuatro parámetros para su caso y considera la estabilidad y el número de iteraciones. No es una reproducción literal ni un resultado del caso Volcanes.

La comparación cambia a la vez el pronosticador y la estrategia de control, por lo que cualquier diferencia no puede atribuirse por separado a uno de ellos. Marzo ya fue examinado en la evaluación original: el análisis es exploratorio. Para reproducirlo se requiere la trayectoria previa de Chronos y una carpeta de salida nueva:

```powershell
uv run --locked --extra pronosticos python -m scripts.comparar_beta_sin_mpc --salida results/beta_sin_mpc_final
```

La salida `results/beta_sin_mpc_final/` contiene tablas físicas de beta, resultados diarios, métricas, un informe y cuatro gráficas en PNG y SVG; es local y regenerable. El `README.md` de esa carpeta reúne los resultados y las gráficas.

| Indicador | Beta sin MPC | Chronos-2 + MPC | Diferencia Chronos − beta |
|---|---:|---:|---:|
| \(J\) realizado (Wh·p) | 285762.630 | 286406.573 | +643.943 |
| \(S_{\mathrm{pond}}\) (%) | 93.1383 | 93.3482 | +0.2099 puntos porcentuales |
| Energía no atendida (Wh) | 16374.138 | 16339.657 | −34.481 |
| CVaR diario de pérdida (Wh·p) | 4146.254 | 3887.228 | −259.026 |
| Servicio crítico (%) | 100 | 100 | 0 |
| Nivel de servicio horario (%) | 92.8571 | 93.0060 | +0.1488 puntos porcentuales |
| Compromisos incumplidos (carga·h) | 33 | 45 | +12 |
| Solicitudes desatendidas (carga·h) | 220 | 230 | +10 |

El aumento de \(J\) de Chronos frente a beta fue 0.2253 %. La diferencia diaria ocurrió solo el 4, 5 y 6 de marzo: +1285.972, −700.874 y +58.845 Wh·p, respectivamente; los otros 25 días tuvieron igual \(J\). El CVaR diario realizado disminuyó 6.2472 %, calculado sobre 28 días cuya cola del 10 % representa 2.8 días efectivos. El mayor número de compromisos incumplidos y solicitudes desatendidas limita la lectura del aumento de \(J\).

La verificación física no detectó violaciones en ninguna trayectoria; los errores máximos fueron \(5.69\times10^{-14}\) Wh en el bus y \(2.28\times10^{-13}\) Wh en baterías. Pasaron diez pruebas relacionadas (cuatro de beta sin MPC y seis de la comparación previa). La ejecución final registró 32.5 s e incluyó solo el nuevo recorrido beta y la reutilización de Chronos; no es una comparación de velocidad entre ambos métodos.

### Calibración experimental orientada al servicio

**Repetición completada y verificada:** la campaña seleccionó el peso de riesgo que maximiza el \(J\) realizado, es decir, la demanda atendida ponderada por prioridad. Se compararon dos reglas: maximizar \(J\) de Chronos-2 o maximizar el promedio de \(J\) de beta y Chronos-2. En empate se elige el menor peso. Esta selección cambia el criterio de calibración del riesgo descrito antes; durante cada decisión el MPC conserva la puntuación por escenarios de §6.2, con \(\alpha=0.9\) y la rejilla \(\lambda_R\in\{0,0.25,0.5,1,2\}\).

Se reutilizan las trayectorias diarias de ajuste de la calibración del riesgo sin resimularlas. Para estudiar sensibilidad temporal se entrenan ambas reglas en ventanas expansivas de tres, cuatro y cinco semanas desde el 6 de enero de 2025 y se evalúa cada peso elegido durante la semana siguiente. Los estados de cada peso son continuos desde el inicio, sin reiniciar baterías en cada corte. Las seis semanas completas de ajuste finalizan el 17 de febrero y fijan los pesos antes de la comparación de marzo.

Se evalúan en Chronos los pesos seleccionados en la validación de desarrollo del 17 de febrero al 3 de marzo y en marzo. La validación continúa con el estado individual de batería correspondiente al peso al final de las seis semanas; marzo parte de la configuración inicial usada en la comparación anterior. Se reutiliza el peso cero ya calculado y se ejecuta el otro peso seleccionado con pronósticos guardados. Esta comparación solo evalúa las consecuencias en Chronos: no demuestra qué ocurre con beta bajo la regla común. Marzo ya fue examinado y se usa de forma exploratoria, sin volver a elegir parámetros.

```powershell
uv run --locked --extra pronosticos python -m scripts.calibrar_servicio --salida results/calibracion_servicio_verificada
```

La salida canónica `results/calibracion_servicio_verificada/` registra las estrategias, cortes temporales, comparaciones y tablas físicas. La repetición verificó los manifiestos, parámetros, fechas y condiciones de las fuentes de marzo antes y después de la ejecución; sus 11 archivos registrados coinciden byte a byte con la primera campaña, conservada en `results/calibracion_servicio/`. La infraestructura, los pronosticadores, los candidatos, los horizontes y la configuración predeterminada no se modificaron; la búsqueda no certifica un óptimo global.

**Pesos seleccionados:** los tres cortes expansivos de ambas reglas eligieron \(\lambda_R=0\). Con las seis semanas completas, el criterio de Chronos también eligió cero por desempate: los pesos 0, 0.25, 0.5 y 1 empataron en su \(J\). La regla común eligió \(\lambda_R=1\): frente a cero, mejoró el promedio de ajuste de ambas ramas en 46.997 Wh·p por una mejora de beta. La evaluación posterior de ese peso se realizó solo con Chronos y no mide su efecto posterior en beta.

En la validación de desarrollo del 17 de febrero al 2 de marzo, ambos pesos produjeron para Chronos \(J=150794.593561\) Wh·p, \(S_{\mathrm{pond}}=0.9381311592\) y atención crítica del 100 %. Los contadores de incumplimientos de la referencia de peso cero no figuran en esa comparación y no se comparan.

**Comparación exploratoria de Chronos en marzo:**

| Indicador | \(\lambda_R=0\) | \(\lambda_R=1\) | Diferencia 1 − 0 |
|---|---:|---:|---:|
| \(J\) realizado (Wh·p) | 286406.573 | 286813.424 | +406.852 (+0.1421 %) |
| \(S_{\mathrm{pond}}\) (%) | 93.3482 | 93.4808 | +0.1326 puntos porcentuales |
| Energía no atendida (Wh) | 16339.657 | 16482.651 | +142.994 |
| Servicio crítico (%) | 100 | 100 | 0 |
| Nivel de servicio horario (%) | 93.0060 | 92.4107 | −0.5952 puntos porcentuales |
| Compromisos incumplidos (carga·h) | 45 | 12 | −33 |
| Solicitudes desatendidas (carga·h) | 230 | 238 | +8 |
| Servicio de prioridad 1 (%) | 69.8616 | 68.1599 | −1.7017 puntos porcentuales |
| Servicio de prioridad 2 (%) | 92.8662 | 93.8302 | +0.9640 puntos porcentuales |

Con peso 1 aumenta \(J\), pero también aumenta la energía no atendida y disminuye la proporción de horas con servicio completo; se atiende una mayor fracción de prioridad 2 y una menor de prioridad 1. El CVaR diario de pérdida para peso 1 no figura en el informe de esta campaña y no se compara. Frente a beta sin MPC de la comparación anterior, el \(J\) de Chronos con peso 1 es 0.3677 % mayor; esa diferencia también combina cambios de pronosticador y control. No se concluye que exista una mejora general ni que el peso 1 sea óptimo fuera de esta muestra.

El informe canónico registra las verificaciones físicas y hashes de entradas, código, fuentes de marzo y archivos generados. La ejecución verificada tardó 487.7 s sin inferencias nuevas del modelo; no mide una mejora de velocidad. Pasaron 392 pruebas en 142.47 s, incluidas tres de selección, cortes temporales e integridad de fuentes. Los datos y el diseño mantienen el carácter exploratorio del resultado.

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

## 8. Implementación y estado

Conservar `data/`, la ingesta/generación existente de `src/` y sus pruebas actuales. El modo original usa un peso terminal compartido; los parámetros físicos conservan sus supuestos. No crear API, base de datos, interfaz web, framework de servicios ni abstracciones innecesarias.

**Módulos funcionales, sin capas adicionales:**

```text
src/
├── pronosticos.py   # Chronos-2, beta y correcciones comunes
├── simulador.py     # FV, baterías, despacho y cargas
├── controlador.py   # escenarios y MPC compartido
└── experimento.py   # evaluación pareada, métricas, tabla y gráficos
```

La estructura presenta las responsabilidades de los módulos principales. Una configuración y un comando de experimento son suficientes. Preferir `numpy`, `pandas`, `scipy`, `matplotlib` y dependencias oficiales de Chronos-2 sobre desarrollar librerías propias. Añadir pruebas breves y enfocadas en invariantes matemáticos, tiempo de pronóstico y comparación justa; evitar pruebas extensas de infraestructura que no intervienen en la investigación.

La ingesta valida los datos y la configuración; el simulador aplica la física y la heurística descritas en §4. Los pronósticos, el MPC compartido y la evaluación pareada están implementados. El modo sensible al riesgo cuenta con una calibración y validación en desarrollo (§7); sus resultados, como los del caso de marzo, son exploratorios y no equivalen a una validación del caso original de Volcanes.

## 9. Entregables y límites

1. Repositorio ejecutable y reproducible, **sin** aplicación compleja.
2. Tabla y visualizaciones de comparación beta frente a Chronos-2.
3. Artículo del curso: resumen, introducción, literatura, método, resultados, discusión, conclusiones y referencias; más presentación oral.

**Fuera del alcance:** medición en vivo, instalación física, diseño de electrónica de potencia, optimización económica, entrenamiento de Chronos-2, API, dashboard, pronóstico de demanda, comparación masiva de algoritmos, garantía de operación segura en hardware y reproducción numérica exacta del caso Volcanes.

**Fuentes:** Acuña et al. (2021), [DOI:10.3390/en14102789](https://doi.org/10.3390/en14102789); *Lineamientos del proyecto final* (Angélica M. González, 2026); [Amazon Chronos-2, Hugging Face](https://huggingface.co/amazon/chronos-2). Los supuestos y decisiones experimentales de este documento son **propuesta de implementación**, no hallazgos de esas fuentes.
