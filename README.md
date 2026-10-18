# Proyecto de optimización 1

Preparación reproducible de un caso fotovoltaico semisintético para el Centro
Educativo Rural El Rayo, Támesis. **HU-01 implementada:** ingesta, demanda,
configuración y validación. La física, el despacho, los pronósticos y el MPC
corresponden a las siguientes historias; este comando aún no simula su operación.

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
redondeo. Las pruebas no validan todavía balances físicos ni resultados de control.
Consultar `docs/datos.md` para datos/unidades y `docs/IDEA.md` para el protocolo
experimental y las adaptaciones al [paper de Acuna et al.](https://doi.org/10.3390/en14102789).

## Integrantes
- Thomas Serna
- Juan Esteban Palacio
- Jeronimo Gutierrez
