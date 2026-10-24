# Pronóstico solar y planificación de energía en un sistema aislado

Este proyecto evalúa si los pronósticos de **Chronos-2**, sin entrenamiento
adicional, mejoran el servicio energético frente a una referencia probabilística
**beta**. El caso es semisintético y está contextualizado en el Centro Educativo
Rural El Rayo, Támesis (Antioquia): combina irradiancia ERA5 y demanda sintética
con un sistema fotovoltaico y baterías hipotéticos.

## Cómo se hace

Se adapta el modelo de [Acuña et al. (2021)](https://doi.org/10.3390/en14102789)
y se comparan ambos pronósticos con el mismo simulador físico y controlador
predictivo heurístico (MPC). Cada método recibe 336 horas de historia, pronostica
24 horas y replanifica cada 6 horas con 50 escenarios comunes. Comparten demanda,
parámetros y estado inicial; después conservan sus propios estados de batería.
La calibración usa enero y la evaluación reservada, del 3 al 30 de marzo de 2025.

El MPC y el caso de Támesis son adaptaciones del proyecto; no constituyen una
reproducción numérica del caso del artículo. El [protocolo](docs/IDEA.md) detalla
estas diferencias y [docs/datos.md](docs/datos.md) describe datos y unidades.

## Resultados

Evaluación en CPU sobre **28 días, 672 horas y 112 orígenes por método**:

| Indicador | Beta | Chronos-2 |
|---|---:|---:|
| MAE diurno de irradiancia (W/m²) | 66,33 | 64,20 |
| Cobertura diurna del intervalo 0,1–0,9 (nominal: 80 %) | 76,98 % | 80,95 % |
| Demanda atendida ponderada por prioridad, J (Wh·p) | 285 714,42 | 286 220,02 |
| Fracción ponderada atendida, S_pond | 93,12 % | 93,29 % |
| Energía no atendida (Wh) | 16 418,24 | 16 352,93 |
| Atención de cargas críticas | 100 % | 100 % |

Chronos-2 reduce el MAE diurno un **3,2 %**, pero la mejora en J es de solo
**0,18 %** (+505,60 Wh·p), concentrada en 4 de los 28 días. Ambas alternativas
respetaron los balances y límites físicos y las verificaciones no detectaron
uso de información futura. Un pronóstico mejor no garantiza más servicio.

![Diferencia diaria de servicio ponderado entre Chronos-2 y beta](docs/figuras/diferencias_diarias.png)

El [artículo del proyecto](docs/articulo.md) presenta las tablas, figuras y
análisis completo. Los resultados son exploratorios: usan demanda sintética,
meteorología de reanálisis y una sola realización; no prueban significancia ni
beneficios en una instalación real.

## Ejecutar el experimento

Se necesita [uv](https://docs.astral.sh/uv/getting-started/installation/).
Desde la raíz del repositorio:

```powershell
uv sync --locked --extra pronosticos
uv run --locked --extra pronosticos energy-project --config scripts/configuracion.json
uv run --locked --extra pronosticos python -m src.experimento --salida results/evaluacion
```

`uv` prepara Python 3.12 y el entorno bloqueado. El primer comando de ejecución
valida los datos; el segundo compara los métodos y exporta tablas, CSV, figuras
y `resumen.json`. La carpeta de salida debe ser nueva. La primera inferencia
descarga los pesos públicos de Chronos-2; no requiere claves de API y funciona
en CPU. Los datos del caso ya están incluidos.

## Ejecutar las pruebas

```powershell
uv run --locked --extra pronosticos pytest -q
```

Las pruebas comprueban datos, cronología, balances, límites de baterías,
controlador y comparación pareada. Usan dobles de los modelos, sin descargar
pesos ni ejecutar el experimento completo.
Para la instalación básica: `uv run --locked pytest -q`; las pruebas con
dependencias opcionales se omiten.

## Integrantes

Thomas Serna · Juan Esteban Palacio · Jeronimo Gutierrez
