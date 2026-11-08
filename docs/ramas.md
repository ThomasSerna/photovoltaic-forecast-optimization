# Experimentos en ramas

Además de la evaluación principal de `main`, el equipo probó cuatro extensiones en
ramas separadas del repositorio. Usan el mismo caso semisintético de Támesis, la misma
demanda, el mismo sistema físico y el mismo periodo de evaluación (3 al 30 de marzo de
2025, 672 horas y 112 orígenes por método). No se fusionaron en `main` y no cambian los
resultados del [README](../README.md) ni del [artículo](articulo.md).

Todos son análisis **exploratorios**: una sola realización meteorológica, demanda
sintética, marzo ya inspeccionado en la evaluación principal y ninguna prueba de
significancia. Cada sección resume la pregunta, el diseño, los resultados, la
verificación y los límites. También enlaza la rama y su documento de resultados en
un commit fijo, que no cambia aunque la rama avance.

En las ramas, los pronósticos se nombran con letras (A beta, B Chronos-2, C TimesFM y D
t0-gamma). Esas letras no corresponden a las celdas del experimento 2×2 de `main`; por
eso aquí se usan los nombres de los modelos.

## Resumen

| Rama | Qué prueba | Resultado principal | Resultados |
|---|---|---|---|
| [`experiment/timesfm`][timesfm-rama] | TimesFM 3.0 como tercer pronosticador, sin entrenamiento | Pronostica mejor que beta (MAE 64,90 frente a 66,33 W/m²), pero J baja un 0,10 % frente a beta | [Protocolo §7][timesfm-doc] |
| [`experiment/t0gamma`][t0gamma-rama] | t0-gamma como cuarto pronosticador; incluye TimesFM | El menor MAE (62,72 W/m²), pero J baja un 0,08 % frente a beta; Chronos-2 sigue con el mayor J | [README][t0gamma-readme] · [Protocolo §7][t0gamma-doc] |
| [`experiment/chronos-2-fine-tunning-lora`][lora-rama] | Chronos-2 ajustado con adaptadores LoRA en GPU | MAE −1,2 % en marzo y −3,2 % de abril a diciembre; J −0,02 % frente al modelo base | [Resultados §5][lora-doc] |
| [`experimental/beta-comparation`][beta-rama] | Beta con planificación diaria sin MPC frente a Chronos-2 con MPC; calibración de λ_R por servicio | Chronos-2 con MPC obtiene un 0,23 % más de J; la regla común elige λ_R = 1 | [Beta sin MPC][beta-doc-mpc] · [Calibración][beta-doc-cal] |

Las tres ramas de pronóstico repiten la conclusión principal: **un pronóstico con menos
error no se traduce en más servicio con este controlador**. Las diferencias de J se
concentran en los mismos días nublados (4, 5 y 6 de marzo) y el resto de los días
coincide.

## TimesFM 3.0

**Pregunta.** ¿Otro modelo fundacional sin entrenamiento cambia la comparación entre
beta y Chronos-2?

**Qué cambia.** Solo el pronosticador: el checkpoint oficial
`google/timesfm-3.0-pytorch` con revisión fijada y `timesfm[torch]==3.0.2`. Recibe la
misma historia de 336 h, emite los cuantiles 0,1–0,9 y pasa por la misma corrección
física común; se desactivan sus ajustes internos de salida. El MPC original, λ_T = 0,
los escenarios, las semillas y el periodo son los de la evaluación principal. λ_T no
se recalibró para TimesFM.

| Indicador | Beta | Chronos-2 | TimesFM 3.0 |
|---|---:|---:|---:|
| MAE diurno (W/m²) | 66,33 | 64,20 | 64,90 |
| RMSE diurno (W/m²) | 101,17 | 98,92 | **98,38** |
| Pinball diurno (W/m²) | 25,94 | **25,13** | 25,59 |
| Cobertura 0,1–0,9 (nominal 80 %) | 76,98 % | 80,95 % | 83,31 % |
| J (Wh·p) | 285 714,42 | **286 220,02** | 285 418,07 |
| Energía no atendida (Wh) | 16 418,24 | 16 352,93 | 16 360,97 |
| Compromisos incumplidos (carga·h) | 29 | 35 | 20 |

**Lectura.** TimesFM mejora a beta en todas las métricas de pronóstico y tiene el menor
RMSE, pero su intervalo es algo ancho. En servicio obtiene el menor J: −296,36 Wh·p
(−0,10 %) frente a beta y −801,95 Wh·p frente a Chronos-2. La diferencia con beta se
reparte en cuatro días (4, 5, 6 y 17 de marzo); los otros 24 son idénticos. Incumple
menos compromisos, pero atiende más cargas de prioridad 1 y menos de prioridad 2, y
eso reduce J.

**Verificación.**
- La ejecución reproduce exactamente beta, Chronos-2 y la referencia reactiva de la
  evaluación principal.
- No hubo violaciones físicas ni uso de información futura.
- Una segunda ejecución completa produjo resultados idénticos byte a byte, salvo los
  tiempos medidos.
- La inferencia tarda 0,18 s por origen, unas 7 veces la de Chronos-2.

**Límites.**
- Los pesos tienen la licencia *TimesFM Non-Commercial License v1.0*: el proyecto no
  los redistribuye y se descargan al ejecutar (1,32 GB).
- No hay una cifra publicada de TimesFM para este caso con la que contrastar.

**Cómo reproducirlo:**

```powershell
git switch experiment/timesfm
uv sync --locked --extra pronosticos
uv run --locked --extra pronosticos python -m src.experimento --salida results/comparacion_timesfm
```

## t0-gamma

**Pregunta.** ¿Un cuarto modelo fundacional, con licencia abierta, cambia la lectura? La rama parte de la de TimesFM y compara los cuatro pronósticos.

**Qué cambia.** El pronosticador es el checkpoint oficial
`theforecastingcompany/t0-gamma`, con revisión fijada y `tfc-t0[torch]==0.6.0`, bajo
licencia Apache-2.0. Emite 21 cuantiles nativos, de los que se toman 0,1–0,9. Se
desactivan los cuatro ajustes de salida de la biblioteca, porque redondearían y
recortarían los cuantiles crudos. El resto del protocolo no cambia y λ_T tampoco se
recalibró.

| Indicador | Beta | Chronos-2 | TimesFM 3.0 | t0-gamma |
|---|---:|---:|---:|---:|
| MAE diurno (W/m²) | 66,33 | 64,20 | 64,90 | **62,72** |
| MAE de las primeras 6 h (W/m²) | 66,97 | 60,77 | 61,59 | **60,40** |
| Pinball diurno (W/m²) | 25,94 | 25,13 | 25,59 | **24,49** |
| Cobertura 0,1–0,9 (nominal 80 %) | 76,98 % | 80,95 % | 83,31 % | 83,31 % |
| J (Wh·p) | 285 714,42 | **286 220,02** | 285 418,07 | 285 477,94 |
| Fracción ponderada atendida, S_pond | 93,12 % | **93,29 %** | 93,03 % | 93,05 % |
| Compromisos incumplidos (carga·h) | 29 | 35 | 20 | 19 |

**Lectura.** t0-gamma es el mejor pronosticador por MAE y pinball, también en las seis
primeras horas, que son las que se ejecutan. Aun así, no obtiene el mayor J:
−236,48 Wh·p (−0,08 %) frente a beta y −742,08 Wh·p frente a Chronos-2. La diferencia
con beta se reparte en cinco días (3, 4, 5, 6 y 17 de marzo). Es la alternativa con
menos compromisos incumplidos, pero también la que deja más solicitudes sin
comprometer.

**Verificación.**
- La ejecución reproduce exactamente beta, Chronos-2, TimesFM y la referencia reactiva.
- No hubo violaciones físicas ni uso de información futura, y la biblioteca no emitió
  avisos de valores no finitos.
- Una segunda ejecución completa produjo resultados idénticos.
- La inferencia tarda 0,050 s por origen: 1,4 veces Chronos-2 y una cuarta parte de
  TimesFM.

**Límites.** El corpus de preentrenamiento de t0-gamma no está documentado, así que no
puede descartarse que incluyera series semejantes.

**Cómo reproducirlo:**

```powershell
git switch experiment/t0gamma
uv sync --locked --extra pronosticos
uv run --locked --extra pronosticos python -m src.experimento --salida results/comparacion_t0gamma
```

## Chronos-2 ajustado con LoRA

**Pregunta.** ¿Ajustar Chronos-2 a la irradiancia del sitio mejora el pronóstico y el
servicio?

**Qué cambia.**
- **Qué se entrena.** Solo adaptadores LoRA: 1 206 912 de 120 684 576 parámetros, el
  1,0 %.
- **Datos.** Irradiancia ERA5 de 2015 a 2024. La rama incluye los años 2015–2023 en
  `data/chronos_lora/`.
- **Selección.** El checkpoint se eligió con la validación de enero y febrero de 2025,
  sin usar marzo.
- **Lo que no cambia.** El contexto de 336 h, el horizonte de 24 h, los nueve deciles,
  las correcciones y el MPC original.
- **Entrenamiento.** Se ejecutó en una RTX 5070 Laptop de 8 GB: 1000 pasos en 432 s.
  Se eligió el paso 600 por su menor pérdida de validación.

| Indicador (marzo) | Chronos-2 base | Chronos-2 con LoRA | Diferencia |
|---|---:|---:|---:|
| MAE diurno (W/m²) | 64,20 | 63,41 | −0,80 (−1,2 %) |
| RMSE diurno (W/m²) | 98,92 | 95,94 | −2,98 (−3,0 %) |
| Pinball diurno (W/m²) | 25,13 | 24,79 | −0,33 (−1,3 %) |
| Cobertura 0,1–0,9 (nominal 80 %) | 80,95 % | 78,30 % | −2,64 p.p. |
| J (Wh·p) | 286 220,02 | 286 153,23 | −66,79 (−0,02 %) |
| Compromisos incumplidos (carga·h) | 35 | 22 | −13 |

De abril a diciembre de 2025, un periodo que no intervino en la selección, el MAE
diurno bajó de 74,09 a 71,70 W/m² (−3,2 %) y la cobertura, de 82,12 % a 77,96 %.

**Lectura.** LoRA reduce el error en los dos periodos, pero estrecha los intervalos y
deja la cobertura por debajo del nominal. En servicio, el cambio es prácticamente
nulo: 25 de los 28 días son idénticos. Frente a beta, LoRA gana +438,81 Wh·p, algo
menos que el modelo base (+505,60).

**Verificación.**
- El modelo base en GPU reproduce las cifras de la evaluación principal en CPU
  (J = 286 220,02 Wh·p y MAE de 64,20 W/m²).
- La recarga del adaptador desde disco difiere como máximo 0,00018 W/m².
- No hubo violaciones físicas ni uso de información futura.

**Límites.** El entrenamiento requiere una GPU NVIDIA con CUDA y el extra
`entrenamiento`, que no se combina con el extra `pronosticos` en el mismo entorno.

**Cómo reproducirlo**, en una máquina con GPU:

```powershell
git switch experiment/chronos-2-fine-tunning-lora
uv sync --locked --extra entrenamiento
uv run --locked --extra entrenamiento python -m src.ajuste_lora verificar
uv run --locked --extra entrenamiento python -m src.ajuste_lora entrenar
uv run --locked --extra entrenamiento python -m src.ajuste_lora comparar
```

## Beta sin MPC y calibración orientada al servicio

La rama `experimental/beta-comparation` contiene dos análisis.

**1. Beta sin MPC frente a Chronos-2 con MPC.**
- **Beta.** Planifica una vez al día, a las 00:00, con una adaptación de la selección
  por frecuencia de Acuña et al. (2021, §3.1). Genera 50 escenarios del pronóstico beta
  y aplica la heurística física a cada uno. Después elige el calendario con más
  vecinos, es decir, calendarios en los que al menos el 90 % de las cargas tiene el
  mismo perfil de 24 h. Lo ejecuta sin replanificar.
- **Chronos-2.** Usa el MPC sensible al riesgo calibrado (α = 0,9 y λ_R = 0), que
  replanifica cada 6 h.

| Indicador | Beta sin MPC | Chronos-2 con MPC | Diferencia |
|---|---:|---:|---:|
| J (Wh·p) | 285 762,63 | 286 406,57 | +643,94 (+0,23 %) |
| S_pond | 93,14 % | 93,35 % | +0,21 p.p. |
| Energía no atendida (Wh) | 16 374,14 | 16 339,66 | −34,48 |
| CVaR diario de pérdida (Wh·p) | 4146,25 | 3887,23 | −259,03 (−6,2 %) |
| Compromisos incumplidos (carga·h) | 33 | 45 | +12 |
| Atención de cargas críticas | 100 % | 100 % | 0 |

Las diferencias aparecen solo el 4, el 5 y el 6 de marzo. La comparación cambia a la
vez el pronóstico y el control, de modo que la diferencia no puede atribuirse a uno
solo de ellos. Chronos-2 incumple más compromisos, y eso limita la lectura del aumento
de J.

**2. Calibración de λ_R orientada al servicio.**
- **Criterio.** Elige el peso de riesgo que maximiza el J realizado en desarrollo, en
  lugar del criterio de la calibración principal.
- **Reglas.** Compara dos: el J de Chronos-2 o el J medio de beta y Chronos-2.
- **Búsqueda.** Usa la rejilla λ_R ∈ {0; 0,25; 0,5; 1; 2} y ventanas de ajuste de tres
  a seis semanas desde el 6 de enero de 2025.

**Resultados.**
- Las ventanas de tres, cuatro y cinco semanas eligieron λ_R = 0 con ambas reglas.
- Con las seis semanas, la regla de Chronos-2 también eligió 0, por desempate.
- La regla común eligió λ_R = 1, que mejoró el J medio en 47,00 Wh·p gracias a beta.
- En marzo, con Chronos-2, ese peso subió J de 286 406,57 a 286 813,42 Wh·p
  (+406,85, +0,14 %) y bajó los compromisos incumplidos de 45 a 12.
- A cambio, aumentó la energía no atendida (+143 Wh) y bajó el servicio de las cargas
  de prioridad 1.

`main` incorpora parte de este trabajo: la sensibilidad a λ_R = 1 del
[protocolo](IDEA.md#sensibilidad-al-peso-de-riesgo) y del README usa el criterio de
esta rama. La comparación de beta sin MPC y la campaña de calibración original solo
están en la rama.

**Cómo reproducirlo.** Ambos comandos reutilizan resultados locales previos: la
calibración del riesgo y la comparación de los MPC. El protocolo de la rama indica
cuáles necesita cada uno.

```powershell
git switch experimental/beta-comparation
uv run --locked --extra pronosticos python -m scripts.comparar_beta_sin_mpc --salida results/beta_sin_mpc
uv run --locked --extra pronosticos python -m scripts.calibrar_servicio --salida results/calibracion_servicio
```

[timesfm-rama]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/tree/experiment/timesfm
[timesfm-doc]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/blob/e49d538a50fb894e0ba76f3cf68b4379132e16e1/docs/IDEA.md#7-protocolo-experimental-y-resultados
[t0gamma-rama]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/tree/experiment/t0gamma
[t0gamma-readme]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/blob/2875d21abdd0b4029f4388ad3f7434c1abf47772/README.md#resultados
[t0gamma-doc]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/blob/2875d21abdd0b4029f4388ad3f7434c1abf47772/docs/IDEA.md#7-protocolo-experimental-y-resultados
[lora-rama]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/tree/experiment/chronos-2-fine-tunning-lora
[lora-doc]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/blob/4f3dc4636326416b2718260e4a3ef46b677e46fd/docs/chronos_lora.md#5-resultados-verificados-25-de-octubre-de-2026
[beta-rama]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/tree/experimental/beta-comparation
[beta-doc-mpc]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/blob/2ac816a65da687962cc8989f5252ea8e5754b5ef/docs/IDEA.md#comparación-de-beta-sin-mpc-frente-a-chronos-con-mpc
[beta-doc-cal]: https://github.com/ThomasSerna/photovoltaic-forecast-optimization/blob/2ac816a65da687962cc8989f5252ea8e5754b5ef/docs/IDEA.md#calibración-experimental-orientada-al-servicio
