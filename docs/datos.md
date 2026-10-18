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