# Pronóstico solar y planificación energética

Proyecto académico que estudia cómo el pronóstico de irradiancia afecta el
servicio de un sistema fotovoltaico aislado con baterías. El caso semisintético
se contextualiza en el Centro Educativo Rural El Rayo, Támesis (Antioquia).
Esta rama compara una referencia beta con **Chronos-2, TimesFM 3.0 y t0-gamma**,
sin entrenamiento adicional de los modelos.

## Cómo se hace

Se combinan irradiancia horaria de reanálisis ERA5 y demanda sintética de
13 cargas. Los pronosticadores reciben las mismas 336 horas de historia y
comparten correcciones, escenarios y un controlador predictivo heurístico
(MPC): horizonte de 24 h, replanificación cada 6 h y 50 escenarios por origen.
La comparación conserva la misma física, demanda y estado inicial; después,
cada alternativa mantiene sus propios estados de batería.

El simulador adapta el modelo de [Acuña et al. (2021)](https://doi.org/10.3390/en14102789).
El caso, los pronosticadores y el MPC son adaptaciones del proyecto; no constituyen
una reproducción numérica del caso original. El [protocolo y los supuestos](docs/IDEA.md)
y la [descripción de los datos](docs/datos.md) documentan los detalles.

## Resultados

Evaluación en CPU del **3 al 30 de marzo de 2025**: 672 horas y 112 orígenes
por pronosticador. Cifras de los ensayos documentados en el
[protocolo experimental](docs/IDEA.md), con la extensión t0-gamma verificada el
21 de octubre de 2026.

| Indicador | Beta | Chronos-2 | TimesFM 3.0 | t0-gamma |
|---|---:|---:|---:|---:|
| MAE diurno (W/m²) | 66,33 | 64,20 | 64,90 | **62,72** |
| Pinball diurno (W/m²) | 25,94 | 25,13 | 25,59 | **24,49** |
| Energía ponderada atendida, J (Wh·p) | 285 714,42 | **286 220,02** | 285 418,07 | 285 477,94 |
| Fracción ponderada atendida, S_pond | 93,12 % | **93,29 %** | 93,03 % | 93,05 % |
| Energía no atendida (Wh) | 16 418,24 | **16 352,93** | 16 360,97 | 16 403,11 |
| Servicio de cargas críticas | 100 % | 100 % | 100 % | 100 % |

J pondera la energía atendida por la prioridad de cada carga. **Un mejor
pronóstico no garantiza más servicio:** t0-gamma obtiene el menor MAE, mientras
Chronos-2 logra el mayor J (+0,18 % frente a beta). TimesFM y t0-gamma quedan
ligeramente por debajo de beta en J. Los ensayos registraron cero violaciones
físicas y cero usos de información futura en la ejecución del experimento.

Son resultados exploratorios de 28 días, una realización meteorológica y demanda
sintética; no se aplicaron pruebas de significancia. El [artículo](docs/articulo.md)
y sus [figuras](docs/figuras/) desarrollan la comparación inicial beta–Chronos-2.

## Ejecutar el experimento

Se necesita [uv](https://docs.astral.sh/uv/getting-started/installation/).
Desde la raíz del repositorio:

```powershell
uv sync --locked --extra pronosticos
uv run --locked --extra pronosticos energy-project --config scripts/configuracion.json
uv run --locked --extra pronosticos python -m src.experimento --salida results/comparacion
```

La carpeta de salida debe ser nueva. Se generan tablas, CSV, figuras y un
`resumen.json` final con métricas, verificaciones, semillas, versiones y hashes.
Los resultados completos están excluidos de Git y se regeneran con ese comando.
La primera instalación y ejecución requieren internet para dependencias y pesos
públicos; no se necesitan claves de API. Se usa Python 3.12 y CPU por defecto.
Los pesos de TimesFM 3.0 tienen [licencia no comercial](https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/LICENSE);
los de t0-gamma se distribuyen bajo Apache-2.0.

## Ejecutar las pruebas

```powershell
uv run --locked --extra pronosticos pytest -q
```

Las pruebas usan modelos simulados, sin descargar pesos ni consultar la red.
Comprueban datos, pronósticos, balances, límites de baterías y comparación pareada.
Para la instalación básica: `uv run --locked pytest -q`; las pruebas con
dependencias opcionales se omiten.

## Integrantes

Thomas Serna · Juan Esteban Palacio · Jeronimo Gutierrez
