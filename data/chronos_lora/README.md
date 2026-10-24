# Historia adicional para LoRA

Adquisición de Open-Meteo Historical Weather API / ERA5 realizada el 24 de
octubre de 2026 para el sitio de la configuración publicada. Son nueve años
horarios (2015–2023); los datos de 2024–2025 se leen del snapshot existente.
GHI es reanálisis en W/m², no medición de la escuela.

Cada año conserva la consulta, la respuesta original comprimida, el CSV
normalizado comprimido y los metadatos de adquisición. `manifiesto.json`
registra los hashes y la separación temporal del experimento. Los hashes de
JSON/CSV normalizan CRLF a LF; los archivos gzip conservan sus bytes exactos.
Los metadatos conservan además los hashes originales anteriores a comprimir.

Desde la raíz, para verificar y reutilizar los años descargados sin entrenar:

```powershell
uv run --locked --extra pronosticos python -m src.datos_lora
```

El comando descarga únicamente años ausentes y rechaza archivos inconsistentes.
No sobrescribe el snapshot publicado. Fuente y definición de la variable:
[Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api).
