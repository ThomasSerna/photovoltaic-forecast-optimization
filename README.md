# Proyecto de optimización 1

Investigación computacional de planificación fotovoltaica aislada en un caso
semisintético contextualizado en el Centro Educativo Rural El Rayo, Támesis:
**referencia beta vs. pronósticos de Chronos-2**, con el mismo simulador físico
y controlador MPC heurístico.

**T01–T03 completadas:** datos, pronósticos Chronos-2 y simulador energético.
El comando de preparación descrito abajo todavía **no ejecuta el simulador**
ni los pronósticos. El alcance matemático y el experimento se explican en
[docs/IDEA.md](docs/IDEA.md).

## Tareas del proyecto

- **T01 — Preparación de datos:** completada ([#1](https://github.com/ThomasSerna/proyecto-optimizacion/issues/1)).
- **T02 — Chronos-2:** completada y verificada en CPU ([#2](https://github.com/ThomasSerna/proyecto-optimizacion/issues/2)).
- **T03 — Simulador energético y heurística:** completada ([#3](https://github.com/ThomasSerna/proyecto-optimizacion/issues/3)).
- **T04 — Referencia beta y MPC compartido:** pendiente ([#4](https://github.com/ThomasSerna/proyecto-optimizacion/issues/4)).
- **T05 — Comparación y resultados:** pendiente ([#5](https://github.com/ThomasSerna/proyecto-optimizacion/issues/5)).

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
from energy_project import cargar_caso
from energy_project.pronosticos import cargar_chronos, pronosticar_chronos, corregir_cuantiles

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

La interfaz compartida con la futura beta es una tabla de cuantiles en W/m²:
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
uv run --locked --extra pronosticos python -m energy_project.pronosticos
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
compromisos=None)` recorre horas consecutivas con FV, selección binaria de cargas
y estado individual de cada batería. Usa la formulación física corregida de
IDEA.md §4, con eficiencias, límites de energía/potencia y vertido explícitos.
No garantiza el óptimo global ni representa la operación eléctrica dentro de la hora.

Ejemplo reproducible de las primeras 48 horas de desarrollo, desde la raíz:

```python
from energy_project import cargar_caso
from energy_project.simulador import simular

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
preparación permanece igual. Beta, escenarios, reservas y MPC siguen en T04.

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
Los datos y la semilla deben concordar: si cambian los supuestos de demanda,
hay que regenerar explícitamente los derivados antes de validarlos.

## Descarga solar opcional

Para adquirir de nuevo la ventana completa de 2024–2025, sin reemplazar el
snapshot publicado:

```powershell
uv run python -m energy_project.descarga --config scripts/configuracion.json
```

Se crea una carpeta nueva en `data/adquisiciones/<instanteUTC>/`, con
`consulta.json`, `respuesta.json`, `metadatos.json` y los dos CSV solares.
Los metadatos incluyen la URL exacta, fecha de adquisición, coordenadas solicitadas
y devueltas, unidades y hashes. Se fija ERA5; no se usa Best Match. Una adquisición
nueva no recupera la respuesta original ausente de los CSV heredados.

Para un ensayo breve, con fin exclusivo y directorio de salida nuevo:

```powershell
uv run python -m energy_project.descarga --inicio 2025-01-06 --fin 2025-01-07 --salida results/solar_un_dia
```

Si la carpeta ya existe, el descargador falla sin sobrescribirla. Los intervalos
normalizados conservan sus índices `t` respecto al inicio de la simulación; una
descarga parcial es un ensayo y no sustituye la cobertura completa del caso.
Los errores de red no producen CSV y las respuestas inválidas se conservan para
diagnóstico. El comando de validación habitual sigue funcionando sin red.

## Uso desde Python

```python
from energy_project import cargar_caso, historial_admisible

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
redondeo. T03 verifica los balances físicos horarios; los resultados de control
y la comparación beta/Chronos-2 siguen pendientes.
Consultar `docs/datos.md` para datos/unidades y `docs/IDEA.md` para el protocolo
experimental y las adaptaciones al [paper de Acuna et al.](https://doi.org/10.3390/en14102789).

## Integrantes
- Thomas Serna
- Juan Esteban Palacio
- Jeronimo Gutierrez
