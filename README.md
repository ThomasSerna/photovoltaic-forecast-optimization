# Pronóstico solar y planificación energética

Este proyecto estudia si mejores pronósticos de irradiancia permiten atender más
cargas en un sistema fotovoltaico aislado. El caso semisintético se contextualiza
en el Centro Educativo Rural El Rayo, Támesis, Colombia. Esta rama compara una
referencia **beta**, **Chronos-2** y **TimesFM 3.0**, sin entrenar los modelos.

## Cómo se hace

- Reanálisis horario ERA5 y demanda sintética por carga, con prioridades y horarios.
- Pronósticos con 336 horas de historia, horizonte de 24 horas y cuantiles 0,1–0,9.
- El mismo simulador de paneles y baterías, 50 escenarios y controlador MPC
  heurístico para los tres métodos; se ejecutan 6 horas antes de replanificar.
- Comparación con iguales datos, semillas y estado inicial; cada método conserva
  después sus propios estados de batería, sin consultar irradiancia futura.

La base es el [modelo de Acuña et al. (2021)](https://doi.org/10.3390/en14102789).
El caso, los pronósticos y el MPC son adaptaciones del proyecto; no constituyen
una reproducción numérica del caso original ni garantizan el óptimo global.
El protocolo y las diferencias se describen en [docs/IDEA.md](docs/IDEA.md).

## Resultados

Ensayo documentado en CPU sobre **672 horas, del 3 al 30 de marzo de 2025**
(112 orígenes por método). TimesFM añade una comparación al experimento beta
frente a Chronos-2, conservando exactamente los resultados de ambos.

| Indicador | Beta | Chronos-2 | TimesFM 3.0 |
|---|---:|---:|---:|
| MAE diurno (W/m²) | 66,33 | 64,20 | 64,90 |
| RMSE diurno (W/m²) | 101,17 | 98,92 | 98,38 |
| Cobertura del intervalo 0,1–0,9 (nominal: 80 %) | 76,98 % | 80,95 % | 83,31 % |
| Energía atendida ponderada por prioridad, J (Wh·p) | 285 714,42 | 286 220,02 | 285 418,07 |
| Fracción ponderada atendida, S_pond | 93,12 % | 93,29 % | 93,03 % |
| Energía no atendida (Wh) | 16 418,24 | 16 352,93 | 16 360,97 |
| Atención de cargas críticas | 100 % | 100 % | 100 % |

Chronos-2 aumenta J un **0,18 %** frente a beta; TimesFM lo reduce un **0,10 %**,
aunque mejora su pronóstico. La mejora predictiva no garantiza más servicio.
Las diferencias de J se concentran en 4 de los 28 días. El ensayo registró cero
violaciones físicas y cero filtraciones de información futura.

Son resultados exploratorios: demanda sintética, parámetros físicos supuestos,
una sola realización meteorológica y sin pruebas de significancia. El detalle
de los tres métodos está en [el protocolo y sus resultados](docs/IDEA.md);
el [artículo](docs/articulo.md) desarrolla la comparación beta–Chronos-2.

## Ejecutar el experimento

Se necesita [uv](https://docs.astral.sh/uv/getting-started/installation/).
Desde la raíz del repositorio (Python 3.12, con versiones fijadas en `uv.lock`):

```powershell
uv sync --locked --extra pronosticos
uv run --locked --extra pronosticos energy-project --config scripts/configuracion.json
uv run --locked --extra pronosticos python -m src.experimento --salida results/comparacion_timesfm
```

El primer comando instala dependencias; `energy-project` valida los datos.
El último ejecuta los tres métodos y exporta tablas, CSV, gráficas y `resumen.json`.
La carpeta de salida debe ser nueva. La primera ejecución descarga los pesos
públicos de ambos modelos; las siguientes usan la caché. Los pesos de TimesFM 3.0
tienen licencia no comercial y se utilizan aquí con fines académicos
([licencia y revisiones](docs/IDEA.md)).

## Ejecutar las pruebas

```powershell
uv run --locked --extra pronosticos pytest -q
```

Las pruebas de pronóstico usan dobles sin descargar pesos. Para probar únicamente
la instalación básica: `uv sync --locked` y `uv run --locked pytest -q`; las
pruebas que requieren dependencias opcionales se omiten.
Los datos, unidades y supuestos se documentan en [docs/datos.md](docs/datos.md).

## Integrantes

Thomas Serna · Juan Esteban Palacio · Jeronimo Gutierrez
