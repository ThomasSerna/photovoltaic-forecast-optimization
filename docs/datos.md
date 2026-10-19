# Datos del proyecto

La simulación representa al Centro Educativo Rural El Rayo, en Támesis,
Antioquia, ubicado aproximadamente en las coordenadas
5.5610705, -75.8092448.

## Archivos principales

Los siguientes archivos se encuentran en `data/`:

| Archivo | Contenido |
|---|---|
| `catalogo_cargas.csv` | Los 13 equipos individuales, sus potencias, horarios y prioridades. |
| `demanda_por_carga.csv` | La energía solicitada por cada equipo en cada intervalo horario, en Wh. |
| `irradiancia_historica.csv` | 17.544 registros horarios de irradiancia de 2024 y 2025, utilizados como historial para beta y Chronos-2. |
| `irradiancia_simulacion.csv` | Subconjunto de 2.016 horas de irradiancia para las 12 semanas de simulación. |
| `calendario_simulacion.csv` | Correspondencia entre el índice horario `t` de la demanda y las fechas de simulación. |
| `demanda_horaria.csv` | Suma horaria de las cargas, con energía de nevera y actividad escolar, en Wh. |
| `demanda_diaria.csv` | Objetivo diario, referencia sorteada, multiplicador y total conciliado, en Wh. |
| `lecturas_medidor.csv` | Partes enteras transcritas y cotas; fechas, horas y décimas vacías porque no se conocen. |
| `intervalos_medidor.csv` | Cuatro diferencias provisionales y cotas abiertas; duración de 24 h supuesta. |

`validacion.json` y `sha256.json` son salidas reproducibles del generador. Las
fechas vacías de las lecturas y la duración real desconocida de sus intervalos
son ausencias intencionales; no se rellenan con fechas de simulación.
El manifiesto registra los bytes presentes al generar; Git puede convertir
saltos de línea entre plataformas. La ingesta calcula los hashes actuales y la
prueba de preservación compara contenido CSV, no sus saltos de línea.

## Origen y supuestos

La demanda es **sintética**, basada en el inventario escolar y cinco
fotografías del medidor. Estas proporcionan cuatro diferencias aproximadas
de 7, 5, 9 y 7 kWh. Las horas, fechas y décimas de las lecturas están
pendientes de confirmación.

Se consideran clases de lunes a viernes, de 07:00 a 12:30. Fuera de ese
horario y los fines de semana solo se incluye la nevera, con un consumo
supuesto de 1,8 kWh/día y variación de ±10%. Las potencias, prioridades y
ventanas de uso son supuestos de simulación, no mediciones verificadas.

Se utilizó la hoja del inventario fechada en julio de 2026, denominada
`ANTERIOR`, sin sumarla a la versión de 2023. Se excluyeron los portátiles
reportados como dañados. La iluminación y otras posibles cargas no
inventariadas están pendientes de confirmación.

La irradiancia proviene de **ERA5 mediante Open-Meteo**. Es una referencia
de reanálisis de la celda cercana a la escuela, no una medición local.

## Modelo de la demanda sintética

La demanda sintética es una adaptación del proyecto (IDEA.md, sección 3), no una ecuación
del paper. Su código está en `src/energy_project/demanda.py`. Las ecuaciones se numeran (D1)–(D5)
para no confundirlas con las (1)–(10) del paper. Notación: `d` es el día desde 0 (lunes), `h` la
hora local, `t = 24d + h`, `K` el conjunto de las 13 cargas, `nev` la nevera (la única permanente)
y `K' = K∖{nev}`. De cada carga `k` se usan la potencia `P_k` (W), la ventana de uso
`[a_k, b_k)` (h) y la ocupación base `o_k`; `L_j` son las cinco lecturas enteras del medidor (kWh).

\[
\text{(D1)}\quad D^{\mathrm{ref}}_i = 1000\,(L_{i+1}-L_i)\ \text{Wh},\ i=1,\dots,4;\qquad
D_d=\begin{cases}D^{\mathrm{ref}}_{i_d}\,m_d & \text{día lectivo},\\ D^{\mathrm{nev}}_d & \text{fin de semana,}\end{cases}
\]

con `i_d` uniforme entre 1 y 4 (con reemplazo) y `m_d ~ U[0,95; 1,05]` (`variacion_lectiva`).

\[
\text{(D2)}\quad D^{\mathrm{nev}}_d=1800\,u_d,\ u_d\sim U[0{,}9;\,1{,}1];\qquad
D_{\mathrm{nev},t}=D^{\mathrm{nev}}_d\,\frac{g_h}{\sum_{j=0}^{23}g_j},\quad g_h=\nu_h\,\varepsilon_{d,h},
\]

con `ε ~ U[0,9; 1,1]` y `ν_h = 0,8` si `h < 7` o `h ≥ 19`, y `1` en otro caso. Con 2160 Wh/día y
sin ruido, cada hora nocturna recibe 80 Wh y cada hora diurna, 100 Wh.

\[
\text{(D3)}\quad \ell_{k,h}=\max\{0,\ \min(h+1,b_k)-\max(h,a_k)\},\qquad
D^{\max}_{k,h}=P_k\,\ell_{k,h},\qquad \omega_{k,h}=o_k\,\xi_{d,k,h},
\]

con `ξ ~ U[0,85; 1,15]`. Solo cuentan las franjas `(k, h)` de cargas no permanentes con `ℓ > 0`, y
solo en días lectivos; `ℓ` es el número de horas de la franja `[h, h+1)` dentro de la ventana de uso.

\[
\text{(D4)}\quad D_{k,h}(\kappa)=D^{\max}_{k,h}\,\min\{1,\ \kappa\,\omega_{k,h}\},\qquad
\sum_{k\in K',\,h}D_{k,h}(\kappa_d)=D_d-D^{\mathrm{nev}}_d .
\]

La suma crece con `κ`, así que `κ_d` se busca por bisección con 90 iteraciones. Se conserva
`[0, 100]` si alcanza el objetivo; si no, se duplica la cota superior hasta cubrirlo.
Una cota que exceda el rango numérico produce un error. Si el objetivo es cero, `κ_d = 0`.
Existe solución si `0 ≤ D_d − D^nev_d ≤ Σ D^max`; si no, el generador falla: no recorta la demanda
ni añade cargas ocultas. En fin de semana solo hay nevera y `κ_d = 0`.

\[
\text{(D5)}\quad 0\le D_{k,t}\le P_k\,\ell^{\mathrm{disp}}_{k,t}+10^{-6}\ \text{Wh},\qquad
\Big|\sum_{t\in d}\sum_{k}D_{k,t}-D_d\Big|\le 0{,}001\ \text{Wh}.
\]

`ℓ^disp` vale 1 para la nevera, 0 para las demás cargas en fin de semana y `ℓ_{k,h}` en días
lectivos. La energía de cada carga y hora se redondea a 6 decimales; el total de cada hora, también,
antes de acumularse en el del día.

| Ecuación | Función |
|---|---|
| (D1) `D^ref` | `referencias_del_medidor` |
| (D1) `i_d`, `m_d`, `D_d`; (D2) `u_d`, `D^nev_d` | `sortear_objetivo` (devuelve `ObjetivoDia`) |
| (D2) `D_nev,t` | `perfil_nevera` |
| (D3) `ℓ` | `horas_solapadas`, `horas_disponibles` |
| (D3) `D^max`, `ω` | `franjas_de_uso` (devuelve `Franja`) |
| (D4) `D_{k,h}(κ)` | `energia_franja` |
| (D4) `κ_d` | `factor_de_asignacion` |
| Reparto de un día | `energia_del_dia` |
| (D5) | `comprobar_limites`, `comprobar_conservacion` |
| Filas de los CSV | `filas_carga_hora`, `filas_del_dia`, `fila_diaria`, `generar_demanda` |

Los sorteos de cada día siguen siempre este orden: `i_d`, `m_d` (solo días lectivos), `u_d`,
`ε_{d,0..23}` y `ξ_{d,k,h}` (solo días lectivos; cargas en el orden del catálogo y `h` creciente).
Cambiarlo altera todos los datos de cada semilla. Los supuestos fijos del código (0,8, el horario
nocturno, los rangos de ruido, la cota y las iteraciones de la bisección, las tolerancias y los
decimales) están nombrados en la sección «Supuestos fijos del modelo» de `demanda.py`; los demás
parámetros vienen de `scripts/configuracion.json`. `tests/test_demanda.py` comprueba a mano (D1)–(D4)
y el orden de los sorteos con casos pequeños.

## Periodo y alineación

La simulación comprende del **6 de enero al 30 de marzo de 2025**:
ocho semanas de desarrollo y cuatro de evaluación. Estas fechas fueron
asignadas a la demanda sintética; no corresponden a las fotografías.
El calendario simplificado no contempla festivos ni vacaciones.

Los archivos de simulación se relacionan mediante `t`. El catálogo y la
demanda se relacionan mediante `id` y `carga_id`, respectivamente.

Los intervalos tienen una hora y se expresan en `America/Bogota`.
`ghi_w_m2` representa irradiancia media en W/m², no energía eléctrica.
Los tiempos ya están ajustados para representar el inicio y fin del
intervalo correspondiente.

## Condiciones de comparación

- Ambos modelos utilizan la misma demanda, prioridades y restricciones.
- La demanda sintética se considera una programación conocida por el MPC.
- Beta y Chronos-2 solo pueden consultar irradiancia cuyo intervalo haya
  terminado antes o en el instante de predicción.
- La irradiancia futura se utiliza únicamente para simular y evaluar.
- Se idealiza la disponibilidad del reanálisis al cierre de cada hora;
  el producto real se publica con retraso.
- `irradiancia_simulacion.csv` ya está contenido en el histórico:
  no deben concatenarse.
- Al unir irradiancia con múltiples cargas, no debe sumarse la radiación
  repetida por cada equipo.

La evaluación utiliza consumo sintético y no constituye una validación
independiente con mediciones reales de la escuela.

## Ingesta ejecutable y configuración provisional (HU-01)

`uv run energy-project --config scripts/configuracion.json` valida los archivos
contra su configuración y semilla, sin escribirlos. Comprueba números finitos,
claves/rejilla carga-hora, cobertura, horarios, unidades, metadatos, conservación
diaria y que la serie solar de simulación coincide con el histórico. Rechaza
errores con excepciones explícitas, incluso al ejecutar Python con `-O`.

El catálogo, los resúmenes y la demanda deben reproducir el generador declarado:
no se aceptan cambios manuales que conserven únicamente el total diario pero no
correspondan a su semilla. Los CSV solares ya representan intervalos corregidos;
la ingesta no resta otra hora, interpola ni recorta GHI a 1000 W/m².

Los periodos locales se fijan con extremos derechos exclusivos:

| Partición | Inicio | Fin | Horas |
|---|---|---|---:|
| Historial previo | 2024-01-01 | 2025-01-06 | 8904 |
| Desarrollo | 2025-01-06 | 2025-03-03 | 1344 |
| Evaluación | 2025-03-03 | 2025-03-31 | 672 |
| Reserva no usada | 2025-03-31 | 2026-01-01 | 6624 |

La configuración inicial incluye cuatro paneles de 500 W (2000 W nominales),
arreglo horizontal equivalente, factor de desempeño 0,85, inversor de 2000 W y
bus con límite de 3000 W; el inversor común limita el suministro a cargas a 2000 W.
Incluye dos baterías individuales de
2400 Wh: mínimo 480, máximo 2400 e inicial 1440 Wh por batería, límites de carga
y descarga de 600 W medidos en el bus y eficiencias de 0,95. Son **supuestos del
proyecto sin calibración**, no equipos medidos ni valores atribuidos al paper.
No se agregan estados ni límites de las baterías en la ingesta.

Intervalo: 1 h; horizonte: 24 intervalos; replanificación: 6; escenarios: 50.
Semilla de demanda: 20261018; semilla de escenarios: 20261019.
T04 implementa el muestreo con los mismos bloques y rangos para beta y Chronos-2
(IDEA.md §5). El estado inicial se refiere al inicio de la simulación;
los cortes desarrollo/evaluación no prescriben reinicios de batería.

La correspondencia con ecuaciones (1)–(9), objetivo (10) y algoritmos del paper
se documenta en IDEA.md §4.5, con las correcciones y adaptaciones implementadas
en T03–T04. El comando de preparación de T01 verifica datos y dominios de
parámetros; los balances FV/baterías y el servicio realizado se comprueban en
el simulador y la calibración. La evaluación final corresponde a T05.

## Nuevas adquisiciones solares

El descargador opcional `python -m energy_project.descarga`, ejecutado mediante
uv, consulta [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api)
con `models=era5`, `hourly=shortwave_radiation`, `timezone=GMT` y
`timeformat=unixtime`. La radiación es el promedio de la **hora precedente**:
el sello crudo indica su fin y se resta una hora para obtener el inicio. La
consulta cubre el sello final necesario, incluido 2026-01-01 05:00 UTC para la
última hora local de 2025. Esa conversión no se vuelve a aplicar al leer CSV.

Cada adquisición escribe en un directorio nuevo: consulta exacta, bytes de la
respuesta, metadatos de celda/procedencia y hashes, histórico normalizado y
subconjunto de simulación. Las carpetas de adquisiciones y los informes se
excluyen de Git por defecto; conservarlos si se usan en un experimento y registrar
sus hashes. Cambiar de adquisición es una decisión explícita: no se sustituyen
automáticamente las rutas del caso. Una descarga parcial no cumple la cobertura
completa exigida por `cargar_caso`.

La respuesta cruda original del snapshot heredado sigue sin estar disponible.
No se exige que una consulta posterior produzca valores idénticos: se conserva
como una nueva adquisición identificada. Las pruebas del descargador usan
respuestas controladas sin red; además se verificó una descarga real de 24 horas
del 6 de enero de 2025 en una carpeta temporal, sin tocar los CSV publicados.
