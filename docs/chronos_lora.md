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
