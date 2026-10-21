# Proyecto de optimización 1

Investigación computacional de planificación fotovoltaica aislada en un caso
semisintético contextualizado en el Centro Educativo Rural El Rayo, Támesis:
**referencia beta vs. pronósticos de Chronos-2**, con el mismo simulador físico
y controlador MPC heurístico.

**T01–T03 completadas:** datos, pronósticos Chronos-2 y simulador energético.
**T04 completada:** referencia beta, escenarios y MPC; ensayo pareado verificado en CPU.
**T05 completada:** evaluación pareada A/B en el periodo reservado, tabla, gráficas y
borrador del artículo ([docs/articulo.md](docs/articulo.md)).
**Análisis adicional C (rama `t05-timesfm`):** TimesFM 3.0 zero-shot con el mismo
protocolo, verificado en CPU (ver «Análisis adicional: alternativa C»).
**Análisis adicional D (rama `t05-t0gamma`, que incluye C):** t0-gamma zero-shot con
el mismo protocolo, verificado en CPU (ver «Análisis adicional: alternativa D»).
El comando de preparación descrito abajo todavía **no ejecuta el simulador**
ni los pronósticos. El alcance matemático y el experimento se explican en
[docs/IDEA.md](docs/IDEA.md).

## Tareas del proyecto

- **T01 — Preparación de datos:** completada ([#1](https://github.com/ThomasSerna/proyecto-optimizacion/issues/1)).
- **T02 — Chronos-2:** completada y verificada en CPU ([#2](https://github.com/ThomasSerna/proyecto-optimizacion/issues/2)).
- **T03 — Simulador energético y heurística:** completada ([#3](https://github.com/ThomasSerna/proyecto-optimizacion/issues/3)).
- **T04 — Referencia beta y MPC compartido:** completada y verificada en CPU ([#4](https://github.com/ThomasSerna/proyecto-optimizacion/issues/4)).
- **T05 — Comparación y resultados:** completada y verificada en CPU ([#5](https://github.com/ThomasSerna/proyecto-optimizacion/issues/5)).

El objetivo es simplificar el código y la arquitectura, **sin simplificar
las ecuaciones ni las restricciones físicas del modelo**.

## Instalación y ejecución

Se necesita [uv](https://docs.astral.sh/uv/getting-started/installation/).
Desde la raíz del repositorio, en PowerShell o una terminal equivalente:

```powershell
uv sync --locked
uv run energy-project --config scripts/configuracion.json
uv run pytest
```

uv instala Python 3.12 si hace falta y crea `.venv/` con las versiones de
`uv.lock`. No hacen falta credenciales, solver ni descargas meteorológicas para
validar el caso publicado. La primera instalación necesita acceso a internet.

Los módulos están directamente en `src/`; se importan, por ejemplo, con
`from src import cargar_caso` y se ejecutan con `python -m src.pronosticos`.

El comando comprueba los nueve CSV y muestra un resumen; además escribe
`results/preparacion_hu01.json`. `--salida otra/ruta.json` permite elegir el
informe. Incluye conteos, periodos, semillas, supuestos, configuración física,
versiones y hashes de entradas y código. Una inconsistencia produce un error con
archivo/campo y código de salida 1; la ingesta no repara ni sobrescribe entradas.

## Núcleo de pronóstico (T02)

Las dependencias de Chronos son opcionales; la preparación de T01 conserva su
instalación básica. Para usar el pronóstico local:

```powershell
uv sync --locked --extra pronosticos
```

```python
from src import cargar_caso
from src.pronosticos import cargar_chronos, pronosticar_chronos, corregir_cuantiles

caso = cargar_caso("scripts/configuracion.json")
opciones = caso.configuracion["pronosticos"]
pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])
crudos = pronosticar_chronos(
    pipeline, caso.irradiancia_historica,
    caso.configuracion["periodos"]["desarrollo"]["inicio"],
    contexto=opciones["contexto"], horizonte=caso.configuracion["horizonte"],
    cuantiles=opciones["cuantiles"],
)
cuantiles, correcciones = corregir_cuantiles(crudos, caso.configuracion)
```

Se usa `amazon/chronos-2`, sin ajuste de pesos, fijado al commit
`29ec3766d36d6f73f0696f85560a422f50e8498c`, con CPU y FP32 por defecto.
El pipeline se carga una sola vez y se reutiliza entre orígenes. La primera
carga necesita descargar pesos públicos; las siguientes utilizan la caché de
Hugging Face. No hacen falta claves de API.

**Alternativa C (análisis adicional, rama `t05-timesfm`).** El mismo extra instala
`timesfm[torch]==3.0.2`, que no cambia las versiones de torch ni de transformers
de `uv.lock`. Para cargar TimesFM 3.0 y pronosticar:

- `cargar_timesfm(opciones["revision_timesfm"], opciones["dispositivo"])` carga
  `google/timesfm-3.0-pytorch` en CPU/FP32, fijado al commit
  `43046b85ec22d584a13f8098c2ed39c889e129c2`.
- `pronosticar_timesfm(modelo, …)` tiene la misma firma y salida que
  `pronosticar_chronos`.

La primera carga descarga 1,32 GB de pesos. **Licencia:** los pesos de TimesFM 3.0
se distribuyen bajo la [TimesFM Non-Commercial License v1.0](https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/LICENSE),
que solo permite uso no comercial ni productivo; aquí se usan con fines académicos y
el repositorio no los redistribuye. El código de `timesfm` es Apache-2.0. Detalles y
supuestos en IDEA §5.

**Alternativa D (análisis adicional, rama `t05-t0gamma`).** El mismo extra instala
`tfc-t0[torch]==0.6.0`. En `uv.lock` solo añade `tfc-t0`, `jaxtyping`,
`rotary-embedding-torch` y `wadler-lindig`; no cambia torch, transformers,
huggingface-hub, numpy, Chronos ni TimesFM. Para cargar t0-gamma y pronosticar:

- `cargar_t0(opciones["revision_t0"], opciones["dispositivo"])` carga
  `theforecastingcompany/t0-gamma` en CPU/FP32, fijado al commit
  `e08aee083778f1f32da4974c0a34276776a35c52`, con `strict=True` y en modo evaluación.
- `pronosticar_t0(modelo, …)` tiene la misma firma y salida que
  `pronosticar_chronos`. Pide los 21 cuantiles nativos, elige 0.1–0.9 sin interpolar
  y desactiva los cuatro ajustes internos de `predict`; la GHI histórica es entera y
  `snap_integers` redondearía los crudos.

La primera carga descarga 1,02 GB de pesos. **Licencia:** el código y los pesos de
t0-gamma son Apache-2.0. Detalles y supuestos en IDEA §5.

La interfaz compartida con beta es una tabla de cuantiles en W/m²:
índice horario `inicio_utc` con zona UTC y columnas numéricas de probabilidad.
La columna `0.5` es la mediana. Se exige contexto completo, terminado y disponible;
no se interpolan huecos. El primer intervalo pronosticado empieza en el origen.
Los límites son contexto 1–8192 h, horizonte 1–1024 h y cuantiles crecientes
en [0.01, 0.99], incluyendo 0.5. Otros niveles pueden interpolarse por la
biblioteca oficial; los nueve deciles iniciales son nativos.

La corrección común conserva los crudos: reordena cruces, elimina negativos y
pone a cero solo horas completamente fuera de amanecer–ocaso calculados con
Astral. Conserva las horas de transición, usa un horizonte sin obstáculos y no
consulta irradiancia futura. No se aplica techo superior, ni siquiera 1000 W/m².
Son adaptaciones del proyecto (IDEA.md §5); Chronos no reproduce las ecuaciones
físicas del artículo ni las modifica. T03 implementa la física; el MPC pertenece a T04.

El ensayo reproducible se ejecuta con:

```powershell
uv run --locked --extra pronosticos python -m src.pronosticos
uv run --locked --extra pronosticos pytest
```

`--config` permite elegir la configuración y `--salida` una **carpeta nueva**
(por defecto `results/chronos_t02`). No sobrescribe resultados; para repetir,
elegir por ejemplo `--salida results/chronos_t02_repeticion`. Una ejecución fallida
puede dejar una carpeta incompleta: solo un `resumen.json` final acredita éxito.

El ensayo emite dos horizontes desde el inicio de desarrollo y seis horas
después (la separación usa `replanificacion`), y repite el primero con el mismo
pipeline. Exige coincidencia con `rtol=1e-5`, `atol=1e-6`. Exporta:

- `pronosticos.csv`: origen, intervalos UTC, referencia y todos los cuantiles
  crudos (`crudo_q0.5`, etc.) y corregidos (`q0.5`, etc.), en W/m².
- `pronostico.png`: primer horizonte, referencia ERA5, mediana y banda entre
  cuantiles extremos; horas mostradas en America/Bogota.
- `resumen.json`: MAE/RMSE global y diurno antes/después de corregir, tiempos,
  muestras RSS en bytes, contexto usado, correcciones, rangos, configuración,
  revisión, versiones, hardware y hashes de entradas, código y lock.

La carga puede incluir la descarga inicial; el tiempo de cada inferencia
incluye preparar y validar el contexto, pero excluye correcciones y gráficos.
RSS se observa antes/después de cargar y tras cada una de las tres inferencias;
su máximo observado **no es el pico continuo**. Los horizontes solapados cuentan
como pronósticos distintos en las métricas. Las horas diurnas son las que no
están completamente en noche astronómica; no se usa un umbral sobre la verdad.
Si no hay horas diurnas, sus métricas son `null`.

Este ensayo no toca la evaluación final ni demuestra mejora frente a beta.
Se mantiene `available_at = fin_intervalo` como idealización de ERA5; tampoco
se garantiza que el preentrenamiento no incluyera datos semejantes. Las pruebas
usan dobles sin pesos ni red; las que requieren extras se omiten en la
instalación básica de T01.

Ensayo real verificado el **18 de octubre de 2026**, con el lock publicado:
dos orígenes de desarrollo, 48 pronósticos horarios (26 diurnos), repetición
coincidente y 160 pruebas aprobadas. MAE global crudo **35,30 W/m²**, corregido
**34,07 W/m²**; MAE diurno corregido **62,91 W/m²**. Inferencias de **0,13–0,34 s**,
carga inicial de **237,37 s** (incluye descarga/importación) y RSS máxima observada
de **839,09 MiB**, en CPU AMD64/FP32 con 16 procesadores lógicos. Son cifras del
ensayo corto de esta máquina, no rendimiento general ni comparación con beta.
Los artefactos quedan en `results/chronos_t02/`, excluidos de Git y regenerables.

## Simulador energético y heurística (T03)

`simular(demanda, irradiancia, configuracion, *, estado_inicial=None,
compromisos=None, reserva=0)` recorre horas consecutivas con FV, selección binaria de cargas
y estado individual de cada batería. Usa la formulación física corregida de
IDEA.md §4, con eficiencias, límites de energía/potencia y vertido explícitos.
No garantiza el óptimo global ni representa la operación eléctrica dentro de la hora.

Ejemplo reproducible de las primeras 48 horas de desarrollo, desde la raíz:

```python
from src import cargar_caso
from src.simulador import simular

caso = cargar_caso("scripts/configuracion.json")
demanda = caso.demanda.loc[caso.demanda["t"] < 48]
solar = caso.irradiancia_simulacion.loc[
    caso.irradiancia_simulacion["t"] < 48, ["t", "ghi_w_m2"]
]
resultado = simular(demanda, solar, caso.configuracion)
print(resultado["resumen"])

# La continuación conserva estados y puede ejecutar un calendario ya decidido.
primero = simular(demanda[demanda["t"] < 24], solar[solar["t"] < 24], caso.configuracion)
continuacion = simular(
    demanda[demanda["t"] >= 24], solar[solar["t"] >= 24], caso.configuracion,
    estado_inicial=primero["estado_final"],
    compromisos=resultado["cargas"].loc[
        resultado["cargas"]["t"] >= 24, ["t", "carga_id", "x"]
    ],
)
assert continuacion["estado_final"] == resultado["estado_final"]
```

La demanda conserva las columnas existentes: `t`, `carga_id`, `demanda_wh`,
`solicitada`, `prioridad_supuesta` y `potencia_w_supuesta`. La irradiancia se suministra
explícitamente con `t` y `ghi_w_m2`; no se consulta meteorología desde el simulador.
La configuración es la validada por `cargar_caso`. Se rechazan horas faltantes,
claves repetidas, valores inválidos y estados fuera de los límites individuales.

`estado_inicial` es un diccionario `{bateria_id: energia_wh}`; si falta, se usan
los estados configurados una sola vez al iniciar la llamada. `compromisos` debe
cubrir exactamente todas las filas de demanda con `t`, `carga_id`, `x` binaria y
`x <= solicitada`. Sin calendario se fija `x = z`; con calendario, un déficit
puede dejar `z = 0` conservando `x`, sin activar cargas adicionales.
`reserva` admite una fracción entre 0 y 1 de la capacidad utilizable de cada
batería, protegida solo ante prioridades menores que 3. Su valor predeterminado
conserva T03; una reserva no nula se rechaza con compromisos fijos.

El resultado es un diccionario de tablas y valores en memoria:

- `cargas`: columnas originales, `x`, `z`, `comprometida_wh`, `atendida_wh` y `no_atendida_wh`.
- `horas`: `t`, `fv_wh`, `solicitada_wh`, `comprometida_wh`, `atendida_wh`, `no_atendida_wh`, `potencia_atendida_w` y `vertido_wh`.
- `baterias`: `t`, `bateria_id`, `inicial_wh`, `carga_wh`, `descarga_wh` y `final_wh`.
- `estado_final`: diccionario de estados por batería para continuar la trayectoria.
- `resumen`: energías totales, beneficio realizado `J` y fracción ponderada `S_pond` (`None` sin demanda ponderada).

La selección ordena por prioridad y energía descendentes, con desempate por
identificador ascendente. Se acepta una carga completa si cabe en energía y
potencia nominal; después se despacha una sola vez, recorriendo baterías por
identificador. Los presupuestos no se amplían por tolerancia. Las pruebas comprueban
balances con tolerancia absoluta de `1e-6` Wh y límites individuales, incluidos
casos manuales, baterías heterogéneas y el periodo completo de desarrollo.

El simulador es determinista: reutiliza los datos, parámetros y semillas de T01
sin introducir otra semilla. No escribe archivos ni añade un comando; la
preparación permanece igual. El controlador de T04 reutiliza esta misma física.

## Referencia beta (T04)

`pronosticar_beta` devuelve los mismos cuantiles crudos, tiempos UTC y unidades
que Chronos. Ambos reciben las últimas 336 horas completas y disponibles.
La beta se ajusta por hora local, separando ceros y positivos; se fijan `loc=0`
y `scale=1.05*max(positivos)` y se estiman las dos formas con SciPy. Los positivos
constantes se representan como una masa puntual; sin positivos se devuelve cero.
Un contexto incompleto o ajuste inválido produce error con origen y hora.

```python
from src.pronosticos import pronosticar_beta, corregir_cuantiles

crudos_beta = pronosticar_beta(
    caso.irradiancia_historica, "2025-01-06T00:00:00-05:00",
    contexto=336, horizonte=24,
)
beta, correcciones_beta = corregir_cuantiles(crudos_beta, caso.configuracion)
```

La escala aprendida no recorta GHI a 1000 W/m². La noche, los valores negativos
y los cruces de cuantiles pasan por la misma corrección que Chronos. SciPy está
incluido en el extra `pronosticos`; usar `uv sync --locked --extra pronosticos`.

El piloto con tres orígenes de desarrollo descartó beta libre de cuatro
parámetros: 4 de 36 ajustes tuvieron soporte negativo y 11 emitieron advertencias;
los 36 ajustes escalados fueron válidos. Fechas, versiones y límites de esa
comprobación quedan en IDEA §5. No constituye una validación estadística general
ni una reproducción numérica de beta4 del artículo.

## Escenarios, MPC y calibración (T04)

`generar_escenarios(cuantiles, historico, origen, configuracion)` devuelve
`escenarios` y `rangos` como matrices N×H, `bloques` como fechas UTC seleccionadas
y `semilla`. Usa bloques completos de 24 h alineados con la hora local del origen
dentro del contexto admisible. Calcula rangos por columna del horizonte, con
empates promedio y `U=(r-0.5)/M`; muestrea bloques con reemplazo mediante una
semilla derivada del origen UTC y `semilla_escenarios`. Ambos proveedores reciben
los mismos bloques y rangos, independientemente del orden A/B. La interpolación
prolonga los cuantiles extremos fuera de 0.1–0.9. Empates y colas truncadas
aproximan la dependencia; no garantizan marginales exactas ni calibración conjunta.

`planificar_mpc(demanda, calendario, cuantiles, escenarios, configuracion,
*, estado_inicial=None)` genera cuatro calendarios sobre la mediana con reservas
0, 0.25, 0.5 y 0.75. Devuelve `compromisos`, `puntuaciones`, la `reserva` elegida
y los `planes`. Protege la reserva por batería después de cubrir cargas críticas
(prioridad ≥3), conservando potencias y eficiencias individuales. Evalúa los
calendarios fijos con los mínimos físicos originales, sin reserva adicional,
y elige por beneficio esperado más energía útil terminal ponderada; desempata
por menor reserva. Los calendarios idénticos comparten su evaluación.

`ejecutar_mpc(caso, pronosticar, *, inicio, fin, estado_inicial=None)` recibe un
callback `(historia_admisible, origen_utc, horizonte)` que devuelve cuantiles
crudos. Aplica correcciones y escenarios comunes, ejecuta R horas contra la
realización y continúa desde cada estado realmente alcanzado. Devuelve las
tablas de T03, `decisiones` y `pronosticos` con crudos, corregidos y muestreo.
Al cerrar el periodo reduce H y R a las horas restantes. El `resumen` cuenta
solo ejecución realizada, recalcula `S_pond` sobre sus totales y excluye el término
terminal de `J`. Este MPC es una adaptación del proyecto; no es el algoritmo
de selección de soluciones frecuentes del artículo ni garantiza optimalidad.

La calibración común se ejecuta con:

```powershell
uv run --locked --extra pronosticos python -m src.controlador --salida results/t04_calibracion
```

Usa exclusivamente 6–12 de enero de 2025 (fin exclusivo el 13), H=24, R=6 y
N=50; prueba `lambda_terminal` en 0, 0.25, 0.5 y 0.75 para ambas ramas. Reutiliza
pronósticos por origen y mantiene ocho recorridos con estados independientes.
Elige el peso con mayor promedio de `J` realizado A/B; empata por el menor peso.
`calibrar_mpc(caso, pipeline=None)` devuelve la tabla de ocho resultados, sus
recorridos, pronósticos, fechas y peso elegido, sin modificar la configuración.
El comando verifica balances y límites de los ocho recorridos y comprueba que
entradas y código siguen iguales. Prepara ambos archivos temporales, publica
`resumen.json` y reemplaza la configuración común con `lambda_terminal` elegido
como último paso. Si falla ese reemplazo, retira el informe; los fallos de escritura
conservan la configuración original y se limpian los temporales. El temporal de
configuración es exclusivo y conserva cualquier archivo preexistente. El
informe conserva semillas, bloques, cuantiles, diagnósticos, versiones y hashes.
La carpeta debe ser nueva; una salida incompleta no acredita éxito. El informe
conserva la configuración de entrada y la configuración con el peso elegido;
el hash de la entrada corresponde al archivo anterior a esa actualización.

**Ensayo CPU verificado:** 28 orígenes por proveedor y ocho recorridos de 168 h;
`lambda_terminal=0` por empate de los cuatro pesos. En esta semana de calibración,
beta obtuvo `J=73192.486644`, `S_pond=0.9540504745`; Chronos obtuvo
`J=73784.143168`, `S_pond=0.9617626074`, para todos los pesos. El promedio pareado
fue `73488.314906`. Hubo cero violaciones físicas: error máximo del bus
`2.85e-14` Wh y de batería `2.28e-13` Wh (cotas redondeadas hacia arriba).
Duración observada: 1266.3 s en CPU, con pesos en caché. El registro local está en
`results/t04_calibracion_verificada/resumen.json`, excluido de Git y regenerable
con el comando anterior usando una carpeta nueva. En esa verificación pasaron 260 pruebas con
`uv run --locked --extra pronosticos pytest -q`. Estos valores son de calibración;
la evaluación reservada y sus conclusiones corresponden a T05.

Tras calibrar, este ejemplo ejecuta un día de desarrollo con el mismo peso y
carga Chronos una sola vez:

```python
from src import cargar_caso
from src.controlador import ejecutar_mpc
from src.pronosticos import cargar_chronos, pronosticar_beta, pronosticar_chronos

caso = cargar_caso("scripts/configuracion.json")
opciones = caso.configuracion["pronosticos"]
pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])

def con_beta(historia, origen, horizonte):
    return pronosticar_beta(
        historia, origen, contexto=opciones["contexto"], horizonte=horizonte,
        cuantiles=opciones["cuantiles"], zona_horaria=caso.configuracion["zona_horaria"],
    )

def con_chronos(historia, origen, horizonte):
    return pronosticar_chronos(
        pipeline, historia, origen, contexto=opciones["contexto"], horizonte=horizonte,
        cuantiles=opciones["cuantiles"],
    )

periodo = dict(inicio="2025-01-06T00:00:00-05:00", fin="2025-01-07T00:00:00-05:00")
a = ejecutar_mpc(caso, con_beta, **periodo)
b = ejecutar_mpc(caso, con_chronos, **periodo)
```

Las pruebas incluyen anticipación comprobable a mano, planes fijos, reservas
heterogéneas, estados realizados, ausencia de información futura y calibración
pareada. La evaluación reservada y sus gráficos corresponden a T05.

## Revisión previa a T05

Verificada el **19 de octubre de 2026**: **290 pruebas aprobadas** con
`uv run --locked --extra pronosticos pytest -q`. Las regresiones incluyen
ocupaciones pequeñas y heterogéneas, colisiones de destinos y fallos al publicar
la calibración. Se conservan las interfaces, las ecuaciones y las dependencias.

Los nueve CSV regenerados coinciden byte por byte con los publicados. El recorrido
beta del 6 al 8 de enero de 2025 (fin exclusivo), con contexto 336 h, H=24, R=6 y
N=50, conserva exactamente decisiones, pronósticos, flujos, estados y métricas
anteriores al refactor, con cero violaciones físicas. El registro local con
semillas, versiones y hashes está en `results/revision_pre_t05/resumen_48h.json`,
excluido de Git.
Esta revisión no repitió inferencia real de Chronos ni ejecutó la evaluación de T05.

## Evaluación final A/B (T05)

```powershell
uv run --locked --extra pronosticos python -m src.experimento --salida results/t05_evaluacion
```

Evalúa el periodo reservado del 3 al 31 de marzo de 2025 (fin exclusivo): 672 h y
112 orígenes por rama, con H=24, R=6, N=50 y `lambda_terminal=0` calibrado en T04.
A (beta) y B (Chronos-2, cargado una sola vez) usan los callbacks comunes de
`controlador.proveedores`. Parten de 1440 Wh por batería y reciben la misma demanda,
el mismo contexto de 336 h y los mismos bloques, rangos y semillas por origen.
Después, cada rama conserva sus propios estados. La **referencia reactiva** aplica
la heurística de T03 con la GHI realizada de cada hora. Tiene información perfecta
de la hora en curso: no es una alternativa admisible y no entra en la diferencia B − A.

En la rama `t05-timesfm` el mismo comando carga también TimesFM 3.0 y añade la
alternativa C como análisis adicional. En la rama `t05-t0gamma` carga además
t0-gamma y añade la alternativa D. Ambas se describen en las secciones siguientes.
Con solo A y B, las columnas y el formato de la tabla A/B son los de esta sección.

El comando verifica:

- balances y cotas de todas las trayectorias (tolerancia de 1e-6 Wh), incluida la
  dirección de carga y descarga;
- el mismo hash de contexto, bloques, rangos y semilla para cada rama y A en cada origen;
- que ningún contexto ni bloque sea posterior al origen;
- la repetición de las primeras 48 h de cada rama, exigiendo decisiones idénticas en
  las 30 h con horizonte completo;
- que entradas y código no cambien durante la ejecución.

No modifica la configuración. Exige una carpeta nueva y publica `resumen.json` al
final como marcador de éxito. Exporta:

- `tabla_ab.csv` y `tabla_ab.md`: A, B, B − A y referencia por indicador; con C,
  además `timesfm` y `diferencia_c_menos_a`, y con D, `t0` y `diferencia_d_menos_a`.
- `horas.csv`, `cargas.csv`, `baterias.csv` y `decisiones.csv`, con columna `politica`.
- `pronosticos.csv`: crudos y corregidos por origen y anticipación, frente a ERA5.
- `diario.csv`: J, S_pond y energía no atendida por día local, y ΔJ de cada
  alternativa frente a A (`delta_J_chronos`, `delta_J_timesfm`, `delta_J_t0`).
- `figuras/`: `irradiancia.png`, `demanda_atendida.png`, `baterias.png` y
  `diferencias_diarias.png`.
- `resumen.json`: verificaciones, métricas, reservas elegidas, semillas, bloques,
  tiempos, versiones, hashes y supuestos.

Desde Python, `evaluar(caso, pronosticadores)` ejecuta y verifica A, las alternativas
presentes y la referencia.
`resumir` calcula las métricas y `verificar_reproducibilidad` repite el tramo inicial.

**Ensayo CPU verificado el 19 de octubre de 2026** (`results/t05_evaluacion_verificada/`,
excluido de Git y regenerable). Las métricas de pronóstico usan la mediana y los
cuantiles corregidos sobre 1438 pares diurnos (origen, anticipación):

| Indicador | A — beta | B — Chronos-2 | B − A | Reactiva |
|---|---:|---:|---:|---:|
| MAE diurno (W/m²) | 66.33 | 64.20 | −2.13 | — |
| MAE diurno, primeras 6 h (W/m²) | 66.97 | 60.77 | −6.21 | — |
| Pinball diurno (W/m²) | 25.94 | 25.13 | −0.81 | — |
| Cobertura 0.1–0.9 diurna (nominal 80 %) | 76.98 % | 80.95 % | +3.96 p.p. | — |
| J realizado (Wh·p) | 285714.424364 | 286220.023421 | +505.599057 | 285240.844736 |
| S_pond | 0.9312263476 | 0.9328742419 | +0.0016478943 | 0.9296828140 |
| Energía no atendida (Wh) | 16418.24 (11.06 %) | 16352.93 (11.02 %) | −65.31 | 16218.53 (10.93 %) |
| Servicio crítico (p ≥ 3) | 100 % | 100 % | 0 | 100 % |
| Horas con demanda atendida por completo | 93.01 % | 93.01 % | 0 | 93.01 % |

**Pronóstico.** Chronos-2 mejora todas las métricas de pronóstico. Su intervalo
0.1–0.9 queda cerca de la cobertura nominal, mientras que beta se queda por debajo.

**Servicio.** La ventaja en servicio es pequeña: +0.18 % de J, concentrada en 4 de
28 días. Los otros 24 días son idénticos.

| Día | ΔJ (Wh·p) |
|---|---:|
| 4 de marzo | +969.44 |
| 5 de marzo | −488.86 |
| 6 de marzo | −54.98 |
| 17 de marzo | +80.00 |

La media diaria es +18.06 Wh·p y la mediana, 0.

**Reservas.** Beta eligió ρ=0 en los 112 orígenes. Chronos eligió ρ=0.25 en dos
orígenes nublados (3 de marzo a las 18:00 y 4 de marzo a las 00:00, hora local).

**Referencia reactiva.** Deja menos energía sin atender, pero obtiene menor J. Al
atender más cargas de prioridad 1 temprano, deja menos energía para las de
prioridad 2:

| Prioridad | Beta | Chronos | Reactiva |
|---|---:|---:|---:|
| p=1 | 71.2 % | 70.3 % | 73.3 % |
| p=2 | 91.8 % | 92.6 % | 90.6 % |

En la semana de calibración ocurrió lo contrario (reactiva 73864.14 frente a 73192.49
y 73784.14). La referencia no acota al MPC en ningún sentido.

**Verificación.** Hubo cero violaciones físicas: error máximo del bus 1.2e-13 Wh y de
batería 2.9e-13 Wh, cotas redondeadas hacia arriba. Hubo cero filtraciones y las
repeticiones fueron exactas.

**Recursos y reproducibilidad.** La ejecución duró 266.2 s en CPU, con los pesos de
Chronos en caché (carga de 8.4 s). Cada rama tardó unos 117 s, sobre todo en
simulaciones del MPC. La inferencia media por origen fue de 0.010 s con beta y
0.028 s con Chronos. Una segunda ejecución completa produjo CSV de resultados idénticos
byte a byte; en `tabla_ab.csv` solo cambian las filas de tiempos medidos. Las cuatro
figuras de esta ejecución se copiaron a `docs/figuras/` para el artículo.
Con 28 días, una sola realización y demanda sintética, estos resultados son
exploratorios. No se aplican pruebas de significancia.

## Análisis adicional: alternativa C (TimesFM 3.0)

Es una adaptación del proyecto en la rama `t05-timesfm`; la pregunta principal sigue
siendo A frente a B. C cambia solo el pronosticador por `google/timesfm-3.0-pytorch`:
zero-shot, en CPU/FP32, con la misma historia de 336 h y los mismos cuantiles 0.1–0.9,
corrección común, escenarios, MPC y λ_T=0. El λ_T se calibró en T04 solo con A y B y
no se recalibró. Versión, revisión, banderas y licencia están en IDEA §5. El comando de
T05 es el mismo y exporta las tres ramas.

**Ensayo CPU verificado el 19 de octubre de 2026** (`results/t05_timesfm/`, excluido
de Git y regenerable). Con C presente, A, B y la referencia coinciden **exactamente**
con `results/t05_evaluacion_verificada/`: los mismos CSV por política, los mismos J,
S_pond, métricas, reservas y ΔJ, y las mismas filas de la tabla sin tiempos.

| Indicador | A — beta | B — Chronos-2 | C — TimesFM 3.0 | C − A |
|---|---:|---:|---:|---:|
| MAE diurno (W/m²) | 66.33 | 64.20 | 64.90 | −1.43 |
| RMSE diurno (W/m²) | 101.17 | 98.92 | 98.38 | −2.79 |
| Pinball diurno (W/m²) | 25.94 | 25.13 | 25.59 | −0.34 |
| Cobertura 0.1–0.9 diurna (nominal 80 %) | 76.98 % | 80.95 % | 83.31 % | +6.33 p.p. |
| MAE diurno, primeras 6 h (W/m²) | 66.97 | 60.77 | 61.59 | −5.39 |
| J realizado (Wh·p) | 285714.424364 | 286220.023421 | 285418.068982 | −296.355382 |
| S_pond | 0.9312263476 | 0.9328742419 | 0.9302604393 | −0.0009659083 |
| Energía no atendida (Wh) | 16418.24 (11.06 %) | 16352.93 (11.02 %) | 16360.97 (11.02 %) | −57.27 |
| Servicio crítico (p ≥ 3) | 100 % | 100 % | 100 % | 0 |
| Horas con demanda atendida por completo | 93.01 % | 93.01 % | 93.15 % | +0.15 p.p. |
| Compromisos incumplidos (carga·h) | 29 | 35 | 20 | −9 |

**Pronóstico.** C mejora a beta en todas las métricas de pronóstico y queda cerca de
Chronos-2; tiene el menor RMSE. Su banda 0.1–0.9 cubre algo más que el nivel nominal.
Sus crudos traen más negativos y cruces que los de Chronos (6410 frente a 1616 valores
negativos y 483 frente a 305 filas cruzadas, en 2652 filas), pero la corrección común
apenas cambia su MAE diurno: de 64.92 a 64.90 W/m².

**Servicio.** C obtiene el menor J: −0.10 % frente a A y −801.95 Wh·p frente a B. La
diferencia con A se concentra en los mismos cuatro días que la de B:

| Día | C − A (Wh·p) |
|---|---:|
| 4 de marzo | −225.27 |
| 5 de marzo | −819.13 |
| 6 de marzo | +668.05 |
| 17 de marzo | +80.00 |

Los otros 24 días son idénticos. C eligió ρ=0 en los 112 orígenes e incumplió menos
compromisos, pero dejó más solicitudes sin comprometer (195, frente a 191 y 194).
Atendió más cargas de prioridad 1 (72.2 %) y menos de prioridad 2 (91.2 %). Se repite la
lectura de T05: un pronóstico mejor que beta no garantiza más servicio.

**Verificación.** Cero violaciones físicas: error máximo del bus 5.7e-14 Wh y de
batería 2.3e-13 Wh. Hubo cero filtraciones y la repetición de las primeras 48 h fue exacta.
Una segunda ejecución completa produjo CSV de resultados idénticos byte a byte; en
`tabla_ab.csv` solo cambian los tiempos medidos.

**Costo computacional.** La ejecución completa duró 426.3 s, frente a 266.2 s sin C.
La rama C tardó 137.1 s; su inferencia media fue de 0.179 s por origen, unas 7 veces
la de Chronos-2 (0.026 s). La carga con pesos en caché fue de 1.2 s. La primera
descarga, de 1.32 GB, tardó 17.4 s en esta red, y una prueba de humo aislada midió
1.51 GB de RSS tras cargar C.

Son resultados exploratorios, con una sola realización y sin pruebas de significancia.
No hay cifras publicadas de TimesFM para este caso con las que contrastarlos.

## Análisis adicional: alternativa D (t0-gamma)

Es una adaptación del proyecto en la rama `t05-t0gamma`, que parte de `t05-timesfm` e
incluye C; la pregunta principal sigue siendo A frente a B. D cambia solo el
pronosticador por `theforecastingcompany/t0-gamma`: zero-shot, en CPU/FP32, con la
misma historia de 336 h y los mismos cuantiles 0.1–0.9, corrección común, escenarios,
MPC y λ_T=0. Como C, hereda λ_T de T04 sin recalibrarlo. Versión, revisión, banderas
y licencia están en IDEA §5. El comando de T05 es el mismo y exporta las cuatro ramas.

**Ensayo CPU verificado el 21 de octubre de 2026** (`results/t05_t0gamma/`, excluido
de Git y regenerable). Con D presente, A, B, C y la referencia coinciden **exactamente**
con `results/t05_timesfm/`: los mismos CSV por política, los mismos J, S_pond, métricas,
reservas, ΔJ, semillas, bloques y hashes de contexto, y las mismas filas de la tabla
sin tiempos.

| Indicador | A — beta | B — Chronos-2 | C — TimesFM 3.0 | D — t0-gamma | D − A |
|---|---:|---:|---:|---:|---:|
| MAE diurno (W/m²) | 66.33 | 64.20 | 64.90 | 62.72 | −3.61 |
| RMSE diurno (W/m²) | 101.17 | 98.92 | 98.38 | 98.98 | −2.19 |
| Pinball diurno (W/m²) | 25.94 | 25.13 | 25.59 | 24.49 | −1.44 |
| Cobertura 0.1–0.9 diurna (nominal 80 %) | 76.98 % | 80.95 % | 83.31 % | 83.31 % | +6.33 p.p. |
| MAE diurno, primeras 6 h (W/m²) | 66.97 | 60.77 | 61.59 | 60.40 | −6.57 |
| J realizado (Wh·p) | 285714.424364 | 286220.023421 | 285418.068982 | 285477.943030 | −236.481334 |
| S_pond | 0.9312263476 | 0.9328742419 | 0.9302604393 | 0.9304555863 | −0.0007707614 |
| Energía no atendida (Wh) | 16418.24 (11.06 %) | 16352.93 (11.02 %) | 16360.97 (11.02 %) | 16403.11 (11.05 %) | −15.14 |
| Servicio crítico (p ≥ 3) | 100 % | 100 % | 100 % | 100 % | 0 |
| Horas con demanda atendida por completo | 93.01 % | 93.01 % | 93.15 % | 93.15 % | +0.15 p.p. |
| Compromisos incumplidos (carga·h) | 29 | 35 | 20 | 19 | −10 |
| Solicitudes no comprometidas (carga·h) | 191 | 194 | 195 | 198 | +7 |

**Pronóstico.** D tiene el menor MAE y el menor pinball de las cuatro ramas, también
en las primeras 6 h. Su RMSE queda cerca del de Chronos-2 y por encima del de TimesFM,
y su banda 0.1–0.9 cubre algo más que el nivel nominal, igual que C. Sus crudos no
traen filas cruzadas (305 en B y 483 en C), pero sí 6629 valores negativos (1616 en B y
6410 en C), en 2652 filas. La corrección común cambia su MAE diurno de 62.74 a 62.72 W/m².

**Servicio.** D queda −0.08 % frente a A, −742.08 Wh·p frente a B y +59.87 Wh·p
frente a C. La diferencia con A se concentra en los cuatro días de B y C, más el 3 de
marzo:

| Día | D − A (Wh·p) |
|---|---:|
| 3 de marzo | −62.36 |
| 4 de marzo | −225.27 |
| 5 de marzo | −395.31 |
| 6 de marzo | +366.46 |
| 17 de marzo | +80.00 |

Los otros 23 días son idénticos. D eligió ρ=0 en los 112 orígenes. Es la rama con menos
compromisos incumplidos, pero también la que deja más solicitudes sin comprometer. Es
el mejor pronosticador por MAE y pinball, pero el mayor J lo obtiene B: se refuerza la
lectura de T05, un pronóstico mejor no garantiza más servicio.

**Verificación.** Cero violaciones físicas: error máximo del bus 5.7e-14 Wh y de
batería 2.3e-13 Wh. Hubo cero filtraciones, ningún aviso de valores no finitos en
stderr y la repetición de las primeras 48 h fue exacta. Una segunda ejecución completa
(`results/t05_t0gamma_repeticion/`) produjo CSV de resultados idénticos byte a byte; en
`tabla_ab.csv` solo cambian los tiempos medidos.

**Costo computacional.** Las dos ejecuciones completas duraron 645.6 y 583.8 s, frente
a 426.3 s con tres ramas; el tiempo de todas las ramas varía con la carga de la máquina,
así que D se compara dentro de cada ejecución. La rama D tardó 138.5 y 127.5 s, en el
rango de A, B y C (120–148 s): el MPC domina. Su inferencia media fue de 0.050 s por
origen, unas 1.4 veces la de Chronos-2 (0.035–0.037 s) y una cuarta parte de la de
TimesFM 3.0 (0.202 s). La carga con pesos en caché tomó 1.0 s; la descarga única, de
1.02 GB, no se midió por separado. Una prueba de humo aislada midió 1.33 GB de RSS tras
cargar D, frente a 1.51 GB con C.

Son resultados exploratorios, con una sola realización y sin pruebas de significancia.
No hay cifras publicadas de t0-gamma para este caso con las que contrastarlos, y su
corpus de preentrenamiento no está documentado.

## Configuración y regeneración

`scripts/configuracion.json` es la configuración única. Las rutas de `datos`
se resuelven respecto a ese JSON, no respecto al directorio de ejecución.
Define las lecturas provisionales, cargas, prioridades, ventanas, fechas,
semillas, FV y cada batería por separado. Todos los parámetros físicos son
supuestos editables sin calibrar. El intervalo admitido es de una hora; horizonte,
replanificación y escenarios son enteros positivos, con replanificación ≤ horizonte.

```powershell
uv run python scripts/generar_datos.py --config scripts/configuracion.json
```

Esto regenera los derivados en `data/`, conserva el histórico solar y publica
también las lecturas transcritas, intervalos de consumo y resúmenes diario/horario.
Para verificar otra generación sin reemplazar los archivos publicados:

```powershell
uv run python scripts/generar_datos.py --config scripts/configuracion.json --salida results/regeneracion
```

El generador reutiliza el histórico local para calendario y solar de simulación;
no necesita red. Rechaza una demanda que no cabe en los horarios y potencias.
Antes de escribir comprueba los destinos, incluido `--salida`: ninguna tabla
puede reemplazar la configuración, otra entrada ni los manifiestos
`validacion.json` y `sha256.json`. Se permite regenerar cada tabla en su propia ruta.
Los datos y la semilla deben concordar: si cambian los supuestos de demanda,
hay que regenerar explícitamente los derivados antes de validarlos.

## Descarga solar opcional

Para adquirir de nuevo la ventana completa de 2024–2025, sin reemplazar el
snapshot publicado:

```powershell
uv run python -m src.descarga --config scripts/configuracion.json
```

Se crea una carpeta nueva en `data/adquisiciones/<instanteUTC>/`, con
`consulta.json`, `respuesta.json`, `metadatos.json` y los dos CSV solares.
Los metadatos incluyen la URL exacta, fecha de adquisición, coordenadas solicitadas
y devueltas, unidades y hashes. Se fija ERA5; no se usa Best Match. Una adquisición
nueva no recupera la respuesta original ausente de los CSV heredados.

Para un ensayo breve, con fin exclusivo y directorio de salida nuevo:

```powershell
uv run python -m src.descarga --inicio 2025-01-06 --fin 2025-01-07 --salida results/solar_un_dia
```

Si la carpeta ya existe, el descargador falla sin sobrescribirla. Los intervalos
normalizados conservan sus índices `t` respecto al inicio de la simulación; una
descarga parcial es un ensayo y no sustituye la cobertura completa del caso.
Los errores de red no producen CSV y las respuestas inválidas se conservan para
diagnóstico. El comando de validación habitual sigue funcionando sin red.

## Uso desde Python

```python
from src import cargar_caso, historial_admisible

caso = cargar_caso("scripts/configuracion.json")
contexto = historial_admisible(
    caso.irradiancia_historica, "2025-01-06T00:00:00-05:00"
)
demanda_comun = caso.demanda  # La misma programación para A y B.
```

`CasoDatos` entrega configuración y tablas separadas de catálogo, demanda por
carga, diaria, horaria, lecturas, intervalos, calendario y las dos series solares.
En memoria las particiones de demanda se normalizan a `desarrollo`/`evaluacion`;
los CSV conservan sus nombres originales. El calendario mantiene America/Bogota
y añade tiempos UTC. Las series solares incluyen `available_at` en UTC.
La consulta de contexto exige a la vez intervalo terminado y observación disponible.

## Alcance y supuestos

La demanda horaria es **sintética**; las cuatro diferencias del medidor (7, 5, 9 y
7 kWh) no son cuatro días confirmados. Las fechas del medidor, sus horas y décimas
no están verificadas. La meteorología es reanálisis ERA5, no un sensor escolar.
Se idealiza su disponibilidad al fin de cada hora; esto no representa el retraso
real de publicación de ERA5. El snapshot solar heredado no tiene respuesta cruda
original en el repositorio.

Los cinco CSV originales conservan su contenido. Se verifica conservación diaria
con tolerancia de 0,001 Wh y capacidad por solicitud con holgura de 1e-6 Wh por
redondeo. T03 verifica los balances físicos horarios y T04 verifica el ensayo
pareado de calibración en CPU. T05 verifica la evaluación reservada (sección anterior).
Los pesos de TimesFM 3.0 (alternativa C) tienen licencia no comercial: el proyecto los
usa con fines académicos, los descarga al ejecutar y no los redistribuye. El código y
los pesos de t0-gamma (alternativa D) son Apache-2.0; también se descargan al ejecutar.
Consultar `docs/datos.md` para datos/unidades y `docs/IDEA.md` para el protocolo
experimental y las adaptaciones al [paper de Acuna et al.](https://doi.org/10.3390/en14102789).

## Integrantes
- Thomas Serna
- Juan Esteban Palacio
- Jeronimo Gutierrez
