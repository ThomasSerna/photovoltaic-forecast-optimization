# Planificación fotovoltaica con Chronos-2 y LoRA

Este proyecto estudia cómo los pronósticos de irradiancia influyen en el servicio
de un sistema fotovoltaico aislado con baterías, en un caso semisintético del
Centro Educativo Rural El Rayo, Támesis, Antioquia.

Esta rama prepara el ajuste de Chronos-2 mediante adaptadores LoRA para comparar
su pronóstico y el servicio energético con el modelo base y una referencia beta.
El experimento adapta el trabajo de
[Acuña et al. (2021)](https://doi.org/10.3390/en14102789); el ajuste LoRA y el
controlador MPC son extensiones del proyecto.

## Cómo se hace

- Irradiancia horaria ERA5 y demanda sintética por carga, con prioridades y
  parámetros físicos explícitos.
- Entrenamiento con datos de 2015–2024 y selección del adaptador con validación
  del 6 de enero al 2 de marzo de 2025.
- Pronósticos con 336 horas de contexto y horizonte de 24 horas; el mismo MPC
  replanifica cada 6 horas con 50 escenarios y conserva los estados de baterías.
- Comparación de pronóstico y energía en marzo de 2025; evaluación adicional
  exclusivamente predictiva entre abril y diciembre, sin volver a ajustar pesos.

Los datos están incluidos. La [guía de LoRA](docs/chronos_lora.md) detalla las
particiones, la configuración, los requisitos de GPU y los informes generados.
La metodología común se describe en [docs/IDEA.md](docs/IDEA.md).

## Resultados

Aún no hay resultados.

## Ejecución

Se necesita [uv](https://docs.astral.sh/uv/getting-started/installation/).
Desde la raíz del repositorio, para validar los datos en CPU:

```powershell
uv sync --locked
uv run --locked energy-project --config scripts/configuracion.json
```

Para entrenar y comparar en la máquina Windows con GPU NVIDIA compatible
(configuración prevista para RTX 5070):

```powershell
uv sync --locked --extra entrenamiento
uv run --locked --extra entrenamiento python -m src.ajuste_lora verificar
uv run --locked --extra entrenamiento python -m src.ajuste_lora entrenar
uv run --locked --extra entrenamiento python -m src.ajuste_lora comparar
```

El extra `entrenamiento` incluye pronósticos y PEFT; es excluyente con
`pronosticos`. La primera ejecución descarga los pesos públicos. Las salidas se
guardan en `results/chronos_lora/`; para repetir se requiere una carpeta nueva
mediante `--salida`. Consultar la guía para la prueba breve de dos pasos.

## Pruebas

En CPU, sin entrenar ni descargar pesos:

```powershell
uv run --locked --extra pronosticos pytest -q
```

Las pruebas cubren datos, particiones temporales, física, MPC y el flujo LoRA con
dobles. La instalación básica permite `uv run --locked pytest -q` y omite las
pruebas que requieren dependencias opcionales. Estas comprobaciones no validan
el entrenamiento ni el rendimiento en una GPU real.

## Integrantes

- Thomas Serna
- Juan Esteban Palacio
- Jeronimo Gutierrez
