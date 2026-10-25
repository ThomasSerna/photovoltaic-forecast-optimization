# Chronos-2 con LoRA: entrenamiento en Windows con RTX 5070

Esta extensión compara el **mismo Chronos-2 antes y después de ajustar únicamente
adaptadores LoRA**. Conserva el pronóstico univariado de GHI, el contexto de 336 h,
el horizonte de 24 h, los nueve deciles, las correcciones físicas y el MPC de T05.
Beta y la referencia reactiva permanecen en la comparación de marzo. El ajuste es
una adaptación experimental del proyecto; no pertenece al modelo original de
[Acuña et al. (2021)](https://doi.org/10.3390/en14102789).

El código y los datos se prepararon sin entrenar en el PC de desarrollo. Las
pruebas automatizadas usan dobles y operaciones pequeñas en CPU; no acreditan
rendimiento, memoria disponible ni mejora predictiva en una GPU real.

Preparación verificada el **24 de octubre de 2026**: 360 pruebas aprobadas con
el extra de pronósticos, datos completos reutilizados sin red y perfiles de
instalación comprobados mediante `uv sync --dry-run`. El PC conserva PyTorch
`2.14.1+cpu`; el comando `verificar` rechaza este entorno sin CUDA. No se han
descargado nuevos pesos ni ejecutado actualizaciones reales de un modelo.
La instalación básica aislada también pasa: 269 pruebas aprobadas y 12
omisiones por dependencias opcionales (`uv run --isolated --locked pytest -q`).

Ejecución verificada el **25 de octubre de 2026** en una RTX 5070 Laptop de 8 GB
con el commit `ce7b85d`: 361 pruebas aprobadas con el extra `entrenamiento`,
entrenamiento completo y comparación. Las cifras y su lectura están en §5.

## 1. Datos incluidos y separación temporal

El repositorio incluye **2015–2023** descargados de Open-Meteo/ERA5 en
`data/chronos_lora/`, junto con consultas, respuestas originales y CSV comprimidos,
metadatos por año y un manifiesto. Ocupan aproximadamente 1,3 MB. Se combinan con
el snapshot de 2024–2025 ya publicado, sin modificarlo: **96.432 horas continuas**.
No es necesario descargar meteorología en la máquina que entrenará.

| Uso | Inicio local inclusivo | Fin local exclusivo | Horas objetivo |
|---|---|---|---:|
| Entrenamiento | 2015-01-01 | 2025-01-01 | 87.672 |
| Validación | 2025-01-06 | 2025-03-03 | 1.344 |
| Comparación de pronóstico y energía | 2025-03-03 | 2025-03-31 | 672 |
| Prueba adicional de pronóstico | 2025-04-01 | 2026-01-01 | 6.600 |

Todas las fechas usan `America/Bogota`. Del 1 al 5 de enero y el 31 de marzo de
2025 no se usan objetivos para optimizar ni seleccionar el modelo. Sí pueden
servir como contexto cuando ya han terminado y están disponibles.

La validación contiene **221 ventanas** de 336+24 valores, con origen cada 6 h;
las etiquetas quedan íntegramente dentro de validación. Chronos evalúa únicamente
las últimas H horas de cada entrada, por eso no se entrega una única serie larga
para validar. El entrenamiento recibe una serie `float32` y Chronos muestrea sus
cortes; `min_past=336` exige contexto completo. No hay escalado externo,
interpolación, covariables ni recorte superior de GHI.

El manifiesto verifica hashes, continuidad, unidad W/m², valores finitos no
negativos, procedencia y particiones. Los hashes de JSON/CSV normalizan CRLF a LF
para permitir clones con distintas terminaciones de línea; los gzip y los hashes
de las respuestas originales conservan los bytes exactos. `available_at=fin_utc`
sigue siendo la idealización de disponibilidad del reanálisis usada en T01.

Para verificar o reconstruir los datos, desde la raíz y en el PC de preparación:

```powershell
uv run --locked --extra pronosticos python -m src.ajuste_lora preparar
```

Si existen adquisiciones completas, el comando las verifica y reutiliza sin red.
Los años ausentes se descargan mediante el código existente; un año inconsistente
produce un error. No sobrescribe el snapshot publicado. La convención solar de
fin de hora se convierte una sola vez, conforme a la
[API oficial de Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api).

## 2. Preparar la máquina con GPU

Clonar o copiar **toda la rama** `experiment/chronos-2-fine-tunning-lora`, incluidos
`data/`, `scripts/`, `src/` y `uv.lock`. Abrir PowerShell en la raíz del proyecto.
Se necesita Windows x64, uv, acceso a internet para instalar paquetes y descargar
los pesos públicos de Hugging Face, y un controlador NVIDIA actualizado.
No se necesitan claves de API ni una instalación manual del CUDA Toolkit.

La RTX 5070 tiene arquitectura `sm_120`, según
[NVIDIA](https://developer.nvidia.com/cuda/gpus). El entorno fija PyTorch
`2.14.1+cu130`; CUDA 13 requiere controlador de la familia R580 o posterior según
la [tabla oficial de compatibilidad](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).

```powershell
uv sync --locked --extra entrenamiento
uv run --locked --extra entrenamiento python -m src.ajuste_lora verificar
```

El segundo comando valida los datos y comprueba CUDA, la arquitectura incluida
en PyTorch, BF16 y PEFT mediante una pequeña multiplicación en GPU. No carga el
modelo ni entrena. Si falla, corregir el entorno antes de continuar.

**Usar solo `--extra entrenamiento` en esta máquina.** Incluye las dependencias
de pronóstico y PEFT. Los extras `pronosticos` y `entrenamiento` son excluyentes
para que uv no sustituya el PyTorch de CPU al resolver ambos perfiles. El comando
habitual de CPU `uv sync --locked --extra pronosticos` se conserva; ejecutarlo en
el entorno GPU lo devolvería al perfil de CPU. No mezclar instalaciones manuales
de pip con este lock ni usar `--all-extras`.

## 3. Prueba breve y entrenamiento completo

Primero, probar dos actualizaciones en una carpeta independiente:

```powershell
uv run --locked --extra entrenamiento python -m src.ajuste_lora entrenar --pasos 2 --salida results/chronos_lora/prueba_gpu
```

Esta prueba sí entrena y debe ejecutarse **solo en la máquina con GPU**. Incluye
validación, guardado y recarga; no produce un modelo admisible para la comparación
final. La primera carga descarga `amazon/chronos-2`; las siguientes usan su caché.
Un `resumen.json` final con `ENTRENADO_Y_RECARGADO` acredita éxito.

Ejecutar el entrenamiento completo:

```powershell
uv run --locked --extra entrenamiento python -m src.ajuste_lora entrenar
```

Valores listos en `scripts/configuracion_lora.json`:

| Parámetro | Valor |
|---|---|
| Modelo base | `amazon/chronos-2` |
| Revisión base | `29ec3766d36d6f73f0696f85560a422f50e8498c` |
| Chronos / PEFT | `2.3.2` / `0.21.2` |
| Rango / alpha / dropout | 8 / 16 / 0 |
| Módulos | q, k, v, o de atención y capa de salida de parches |
| Aprendizaje / pasos | `1e-5` / 1.000 |
| Batch / acumulación | 8 / 4; batch efectivo de 32 series |
| Precisión | Pesos FP32 con precisión mixta BF16; sin FP16 |
| Semilla de modelo y datos | `20261024` |
| Validación, guardado y registro | Cada 100 pasos y al final |
| Selección | Menor `eval_loss`, exclusivamente en validación |

Se usa `Chronos2Pipeline.fit(finetune_mode="lora")`, según el
[código oficial de la versión fijada](https://github.com/amazon-science/chronos-forecasting/blob/v2.3.2/src/chronos/chronos2/pipeline.py).
Antes de actualizar pesos, un callback exige que solo haya parámetros `lora_`
entrenables. Esto también bloquea el fallback de Chronos a ajuste completo si
PEFT falta. No se utiliza gradient checkpointing, cuantización ni FlashAttention.

El resultado predeterminado queda en `results/chronos_lora/entrenamiento/`:

- `finetuned-ckpt/`: adaptador y configuración con revisión base fijada; no duplica
  los pesos completos del modelo base.
- `checkpoint-*/`: checkpoints conservados por el entrenador, incluido el mejor.
- `resumen.json`: configuración efectiva, periodos, semillas, parámetros
  entrenables, pérdidas, mejor checkpoint, versiones, hashes, tiempos y máximo de
  memoria GPU asignada observado por PyTorch.

Se exige una pérdida de validación finita, completar los pasos solicitados y que
los tensores exportados coincidan con el mejor checkpoint. Después se recarga el
adaptador desde disco y se contrasta su pronóstico con el anterior: `rtol=1e-4`,
`atol=1e-3 W/m²`, admitiendo el redondeo al fusionar LoRA con el modelo base.
Ambos pronósticos usan FP32: antes de contrastarlos se retira el autocast BF16 que
accelerate deja en el modelo ajustado y se restaura `torch.backends.fp32_precision`,
que transformers cambia a TF32. Sin ello difieren unos 3 W/m² sin que cambien los pesos.

Las carpetas de resultados deben ser nuevas. Para repetir, usar `--salida` con
otra ruta. Una salida sin resumen final está incompleta. No hay reanudación
exacta del optimizador: esta versión guarda solo el modelo. Si falta memoria,
reducir `batch_size` a 2 y aumentar `acumulacion` a 16 en el JSON; iniciar una
ejecución nueva y conservar su configuración. No se cambia automáticamente a CPU
ni se promete duración o consumo de VRAM sin medir la tarjeta real.

## 4. Comparar y revisar resultados

Mantener el mismo checkout, datos, configuración y lock con los que se entrenó:

```powershell
uv run --locked --extra entrenamiento python -m src.ajuste_lora comparar
```

Para un entrenamiento guardado en otra carpeta:

```powershell
uv run --locked --extra entrenamiento python -m src.ajuste_lora comparar --adaptador results/otro_entrenamiento/finetuned-ckpt --salida results/otra_comparacion
```

La comparación no entrena ni recalibra el MPC. Base y LoRA usan el mismo
dispositivo, FP32, contextos de 336 h, orígenes cada 6 h, horizonte, cuantiles y
correcciones. Los tiempos CUDA se miden con sincronización. Cada política conserva
su propio estado realizado de baterías después del estado inicial común.

**Marzo:** beta, base y LoRA comparten demanda, parámetros físicos, MPC,
`lambda_terminal=0`, semillas y rangos de escenarios; la referencia reactiva se
conserva. Se verifican pareo, ausencia de futuro, balances físicos y repetición.
Se exportan errores de pronóstico, J, S_pond, servicio crítico, energía no atendida,
compromisos incumplidos y diferencias diarias LoRA−base.

**Abril–diciembre:** se congelan los pesos y se evalúa únicamente pronóstico.
Son 1.097 orígenes por modelo, todos con objetivo completo de 24 h. Las
observaciones anteriores a cada origen, incluso del propio test, son contexto
admisible; no se utilizan para actualizar pesos ni elegir otro checkpoint.

En ambos periodos se conserva irradiancia cruda y corregida. Se calcula MAE,
RMSE, pinball y cobertura 0.1–0.9 en global, diurno y primeras seis horas diurnas.
Los horizontes solapados cuentan como pares origen–anticipación distintos.

La salida predeterminada es `results/chronos_lora/comparacion/`:

- `informe.md` y `resumen.json`: resultados de marzo y reserva separados,
  verificaciones y trazabilidad del adaptador.
- `tabla_ab.csv/.md`: contraste original beta–base.
- `tabla_lora.csv/.md`: base, LoRA y diferencias LoRA−base de marzo.
- `tabla_reserva.csv` y `pronosticos_reserva.csv`: diferencias y cuantiles de la
  prueba adicional, con figura de la primera semana de abril.
- CSV de cargas, horas, baterías, decisiones, pronósticos y resultados diarios;
  figuras de irradiancia, servicio, baterías y diferencias diarias.

Menor MAE/RMSE/pinball representa menor error. Mayor J/S_pond representa más
servicio; la cobertura se interpreta respecto al nominal 0.8. Son conclusiones
distintas: un modelo puede mejorar el pronóstico y empeorar la decisión.

Marzo ya fue inspeccionado en T05 y sigue siendo exploratorio. La reserva
adicional no interviene en el ajuste LoRA, pero no se garantiza ausencia de datos
semejantes en el preentrenamiento de Chronos-2. La demanda y el sistema son
semisintéticos; estos resultados no acreditan beneficios reales en la escuela
ni significancia estadística. No cambiar hiperparámetros mirando cualquiera de
los tests y seguir llamándolos conjuntos reservados.

## 5. Resultados verificados (25 de octubre de 2026)

**Entorno.** RTX 5070 Laptop GPU de 8 GB (`sm_120`, controlador 621.01), Windows 11,
Python 3.12.15, PyTorch `2.14.1+cu130` (CUDA 13.0), Chronos `2.3.2`, PEFT `0.21.2`,
transformers `5.20.1` y accelerate `1.15.0`. Se usaron el commit `ce7b85d`, la
configuración predeterminada de `scripts/configuracion_lora.json` y la semilla
`20261024`. La primera prueba de 2 pasos falló en la comprobación de recarga
(3,38 W/m²) por el autocast BF16 descrito en §3; con la corrección de ese commit,
la diferencia bajó a 0,00024 W/m².

**Entrenamiento** (`results/chronos_lora/entrenamiento/`, regenerable):

- 1.000 pasos en 432 s (2,3 pasos/s y 74 series/s, con batch efectivo de 32).
  VRAM máxima asignada: 1,42 GB, así que no hizo falta reducir `batch_size`.
- Parámetros entrenables: 1.206.912 de 120.684.576 (1,0 %), todos `lora_`.
- Pérdida de validación (`eval_loss` interna de Chronos; no está en W/m²):

| Paso | 100 | 200 | 300 | 400 | 500 | **600** | 700 | 800 | 900 | 1.000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `eval_loss` | 1,0732 | 1,0679 | 1,0623 | 1,0625 | 1,0575 | **1,0552** | 1,0585 | 1,0575 | 1,0570 | 1,0562 |

  Se eligió `checkpoint-600`. Como referencia, la prueba de 2 pasos registró 1,0861.
  La pérdida media de entrenamiento fue 1,1429.
- La recarga desde disco difiere como máximo 0,00018 W/m², dentro de la tolerancia.
  SHA-256 de `adapter_model.safetensors`:
  `bcf90cd1b95c3ede6a68b5512cc2be8b205448ca885ff0f4ac0430d7e42f9b5e`.

**Marzo** (`results/chronos_lora/comparacion/`; unos 8 min en CUDA y FP32; 28 días
y 112 orígenes por política):

| Indicador | Base | LoRA | LoRA − base |
|---|---:|---:|---:|
| MAE diurno (W/m²) | 64,20 | 63,41 | −0,80 (−1,2 %) |
| RMSE diurno (W/m²) | 98,92 | 95,94 | −2,98 (−3,0 %) |
| Pinball diurno (W/m²) | 25,13 | 24,79 | −0,33 (−1,3 %) |
| Cobertura diurna 0,1–0,9 (nominal 80 %) | 80,95 % | 78,30 % | −2,64 p.p. |
| MAE diurno, primeras 6 h de cada origen (W/m²) | 60,77 | 60,16 | −0,61 |
| J (Wh·p) | 286.220,02 | 286.153,23 | −66,79 (−0,02 %) |
| S_pond | 93,29 % | 93,27 % | −0,02 p.p. |
| Energía no atendida (Wh) | 16.352,93 | 16.358,84 | +5,91 |
| Compromisos incumplidos (carga·h) | 35 | 22 | −13 |
| Solicitudes no comprometidas (carga·h) | 194 | 207 | +13 |

- 25 de los 28 días son idénticos. LoRA − base vale −201,52 Wh·p el 4 de marzo,
  +193,57 el 5 y −58,85 el 6: tres de los cuatro días en que base y beta ya
  diferían en T05.
- Frente a beta, LoRA suma +438,81 Wh·p (+0,15 %), algo menos que la base (+505,60).
- LoRA es más conservador: compromete 13 carga·h menos e incumple 13 menos, con las
  mismas 229 solicitudes desatendidas. Eligió ρ = 0,25 en un origen; la base, en dos.
- El servicio crítico es del 100 % y el nivel de servicio horario, del 93,01 %, en
  las tres políticas.
- Verificaciones: cero violaciones físicas (error del bus ≤ 5,7·10⁻¹⁴ Wh y de batería
  ≤ 2,3·10⁻¹³ Wh), cero filtraciones, entradas comunes en los 112 orígenes y
  repetición exacta (30 h, 5 orígenes, 0 Wh). La base en GPU reproduce las cifras de
  T05 en CPU: J = 286.220,02 Wh·p y MAE diurno de 64,20 W/m².
- Inferencia media por origen: 0,070 s la base y 0,056 s LoRA.

**Abril–diciembre** (1.097 orígenes por modelo, cuantiles corregidos, inferencia
media de 0,025 s por origen en ambos):

| Subconjunto | Métrica | Base | LoRA | LoRA − base |
|---|---|---:|---:|---:|
| Diurno | MAE (W/m²) | 74,09 | 71,70 | −2,39 (−3,2 %) |
| Diurno | RMSE (W/m²) | 112,41 | 107,92 | −4,49 (−4,0 %) |
| Diurno | Pinball (W/m²) | 28,38 | 27,58 | −0,79 (−2,8 %) |
| Diurno | Cobertura 0,1–0,9 | 82,12 % | 77,96 % | −4,16 p.p. |
| Diurno, primeras 6 h | MAE (W/m²) | 67,76 | 66,00 | −1,77 (−2,6 %) |
| Diurno, primeras 6 h | Cobertura 0,1–0,9 | 79,84 % | 76,95 % | −2,89 p.p. |
| Global | MAE (W/m²) | 41,74 | 40,40 | −1,35 (−3,2 %) |

`tabla_reserva.csv` incluye también RMSE, pinball y cobertura globales y los
cuantiles crudos.

**Lectura.** LoRA reduce el error en los dos periodos, también en la reserva, que
no intervino en la selección del checkpoint. A cambio, estrecha los intervalos: su
cobertura diurna queda por debajo del nominal, mientras la de la base quedaba por
encima. En servicio, el cambio es prácticamente nulo (−0,02 % de J, concentrado en
tres días). Se repite la conclusión de T05: un pronóstico mejor no garantiza más
servicio con este controlador. Hay una sola realización, la demanda es sintética y
marzo ya estaba inspeccionado, así que los resultados son exploratorios y no se
aplican pruebas de significancia.
