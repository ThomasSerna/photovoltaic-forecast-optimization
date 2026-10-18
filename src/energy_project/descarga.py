"""Adquisiciones nuevas y trazables de GHI horaria de Open-Meteo/ERA5."""

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from .configuracion import PERIODOS, cargar_configuracion
from .datos import particion_por_periodo


ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
DOCUMENTACION = "https://open-meteo.com/en/docs/historical-weather-api"
FUENTE = "Open-Meteo"
MODELO = "ERA5"
SEGUNDOS_POR_HORA = 3600
ARCHIVOS_ADQUIRIDOS = ("consulta.json", "respuesta.json", "irradiancia_historica.csv", "irradiancia_simulacion.csv")
CONVENCIONES = {
    "conversion_temporal": "sello Unix UTC = fin de la hora precedente; inicio = sello - 1 h; aplicada una sola vez",
    "disponibilidad": "available_at = fin_intervalo; supuesto idealizado, no disponibilidad operativa de ERA5",
    "procedencia": "Nueva adquisición; no reconstruye la respuesta cruda original de los CSV publicados",
}


# --- Ventana y consulta ---

def _limite(valor, zona):
    try:
        instante = pd.Timestamp(valor)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Límite {valor!r}: fecha inválida") from error
    if instante.tzinfo is None:
        if not isinstance(valor, str) or len(valor) != 10:
            raise ValueError(f"Límite {valor!r}: usar fecha ISO o instante con zona horaria")
        try:
            date.fromisoformat(valor)
        except ValueError as error:
            raise ValueError(f"Límite {valor!r}: se requiere fecha ISO YYYY-MM-DD") from error
        instante = instante.tz_localize(zona)
    instante = instante.tz_convert(zona)
    if instante != instante.floor("h"):
        raise ValueError(f"Límite {valor!r}: debe coincidir con una hora completa")
    return instante


def _ventana(config, inicio=None, fin=None):
    zona = config["zona_horaria"]
    primero = _limite(config["periodos"][PERIODOS[0]]["inicio"], zona)
    ultimo = _limite(config["periodos"][PERIODOS[-1]]["fin"], zona)
    inicio = _limite(inicio, zona) if inicio is not None else primero
    fin = _limite(fin, zona) if fin is not None else ultimo
    if not primero <= inicio < fin <= ultimo:
        raise ValueError(f"Ventana [{inicio}, {fin}): debe estar dentro de [{primero}, {ultimo})")
    return inicio, fin


def construir_consulta(config, inicio=None, fin=None):
    """URL exacta; incluye el sello de fin del último intervalo solicitado."""
    inicio, fin = _ventana(config, inicio, fin)
    parametros = {
        "latitude": config["latitud"],
        "longitude": config["longitud"],
        "start_date": inicio.tz_convert("UTC").date().isoformat(),
        "end_date": fin.tz_convert("UTC").date().isoformat(),
        "hourly": "shortwave_radiation",
        "models": "era5",
        "timezone": "GMT",
        "timeformat": "unixtime",
    }
    return ENDPOINT + "?" + urlencode(parametros)


# --- Normalización de la respuesta cruda ---

def _validar_celda_y_unidades(respuesta):
    """La respuesta es un objeto sin error, con una celda válida, en GMT y con las unidades esperadas."""
    if not isinstance(respuesta, dict) or respuesta.get("error"):
        raise ValueError(f"respuesta.json: respuesta inválida de Open-Meteo: {respuesta!r}")
    for campo, limite in (("latitude", 90), ("longitude", 180)):
        valor = respuesta.get(campo)
        if (isinstance(valor, bool) or not isinstance(valor, (int, float))
                or not math.isfinite(valor) or abs(valor) > limite):
            raise ValueError(f"respuesta.json: {campo} inválida: {valor!r}")
    if respuesta.get("utc_offset_seconds") != 0:
        raise ValueError("respuesta.json: utc_offset_seconds debe ser 0 para la consulta GMT")
    unidades = respuesta.get("hourly_units", {})
    if not isinstance(unidades, dict) or unidades.get("shortwave_radiation") != "W/m²" or unidades.get("time") != "unixtime":
        raise ValueError(f"respuesta.json: hourly_units incompatibles: {unidades!r}")


def _leer_serie_horaria(respuesta):
    """Sellos Unix (fin de cada hora) y GHI (W/m²): enteros horarios, valores finitos no negativos y sin saltos."""
    hourly = respuesta.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("respuesta.json: falta el objeto hourly")
    tiempos, valores = hourly.get("time"), hourly.get("shortwave_radiation")
    if not isinstance(tiempos, list) or not isinstance(valores, list) or not tiempos or len(tiempos) != len(valores):
        raise ValueError("respuesta.json: hourly.time y hourly.shortwave_radiation deben ser listas no vacías de igual longitud")
    for i, (tiempo, valor) in enumerate(zip(tiempos, valores)):
        if isinstance(tiempo, bool) or not isinstance(tiempo, int) or tiempo % SEGUNDOS_POR_HORA:
            raise ValueError(f"respuesta.json: hourly.time[{i}] no es un segundo Unix horario: {tiempo!r}")
        if (isinstance(valor, bool) or not isinstance(valor, (int, float))
                or not math.isfinite(valor) or valor < 0):
            raise ValueError(f"respuesta.json: hourly.shortwave_radiation[{i}] inválida: {valor!r}")
    if any(b - a != SEGUNDOS_POR_HORA for a, b in zip(tiempos, tiempos[1:])):
        raise ValueError("respuesta.json: hourly.time contiene duplicados, desorden o discontinuidad horaria")
    return tiempos, valores


def _horas_de_la_ventana(tiempos, valores, inicio, fin):
    """Inicios y fines (UTC) y GHI de las horas de [inicio, fin); la respuesta debe cubrirlas todas."""
    try:
        fines = pd.to_datetime(tiempos, unit="s", utc=True)
    except (ValueError, OverflowError) as error:
        raise ValueError("respuesta.json: hourly.time contiene fechas fuera de rango") from error
    inicios = fines - pd.Timedelta(hours=1)
    mascara = (inicios >= inicio.tz_convert("UTC")) & (inicios < fin.tz_convert("UTC"))
    inicios, fines = inicios[mascara], fines[mascara]
    esperados = pd.date_range(inicio.tz_convert("UTC"), fin.tz_convert("UTC"), freq="h", inclusive="left")
    if not inicios.equals(esperados):
        raise ValueError(f"respuesta.json: cobertura incompleta de [{inicio}, {fin}); se esperaban {len(esperados)} horas y hay {len(inicios)}")
    ghi = [float(valor) for valor, incluir in zip(valores, mascara) if incluir]
    return inicios, fines, ghi


def _particiones(inicios, config):
    """Periodo de config["periodos"] de cada hora (inicio inclusivo, fin exclusivo); deben cubrirlas todas."""
    particiones = particion_por_periodo(pd.Series(inicios), config["periodos"])
    if (particiones == "").any():
        raise ValueError("configuracion: los periodos no cubren todas las horas de la adquisición")
    return particiones


def normalizar_respuesta(respuesta, config, inicio=None, fin=None):
    """Convierte sellos crudos de FIN de hora a los diez campos del histórico.

    La hora se resta únicamente aquí; los CSV resultantes ya están alineados.
    Fuente: documentación oficial, definición de ``shortwave_radiation``.
    """
    inicio, fin = _ventana(config, inicio, fin)
    _validar_celda_y_unidades(respuesta)
    tiempos, valores = _leer_serie_horaria(respuesta)
    inicios, fines, ghi = _horas_de_la_ventana(tiempos, valores, inicio, fin)
    particiones = _particiones(inicios, config)
    return pd.DataFrame({
        "inicio_intervalo": [valor.isoformat() for valor in inicios.tz_convert(config["zona_horaria"])],
        "fin_intervalo": [valor.isoformat() for valor in fines.tz_convert(config["zona_horaria"])],
        "inicio_utc": [valor.isoformat() for valor in inicios],
        "fin_utc": [valor.isoformat() for valor in fines],
        "duracion_h": 1,
        "ghi_w_m2": ghi,
        "irradiacion_wh_m2": ghi,
        "fuente": FUENTE,
        "modelo": MODELO,
        "particion": particiones,
    })


# --- Descarga y archivos de la adquisición ---

def _guardar_json(ruta, contenido):
    ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(ruta):
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _carpeta_nueva(ruta_config, config, salida, consultado):
    """Crea, sin sobrescribir, la carpeta de la adquisición; por defecto data/adquisiciones/<instanteUTC>."""
    if salida is None:
        carpeta_datos = (ruta_config.parent / config["datos"]["irradiancia_historica"]).resolve().parent
        salida = carpeta_datos / "adquisiciones" / consultado.strftime("%Y%m%dT%H%M%S%fZ")
    salida = Path(salida).resolve()
    salida.mkdir(parents=True, exist_ok=False)
    return salida


def _descargar(url):
    """Bytes de la respuesta de Open-Meteo; un fallo de red se informa como ValueError."""
    try:
        with urlopen(url, timeout=60) as conexion:
            return conexion.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ValueError(f"Open-Meteo: no se pudo descargar {url}: {error}") from error


def _leer_json(cruda, ruta):
    try:
        return json.loads(cruda)
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{ruta}: respuesta JSON inválida") from error


def _tabla_de_simulacion(historico, config):
    """Horas de desarrollo y evaluación del histórico, con su índice t respecto al inicio de la simulación."""
    simulacion = historico[historico["particion"].isin(["desarrollo", "evaluacion"])].copy()
    origen = pd.Timestamp(config["periodos"]["desarrollo"]["inicio"]).tz_convert("UTC")
    indices = ((pd.to_datetime(simulacion["inicio_utc"], utc=True) - origen) / pd.Timedelta(hours=1)).astype(int)
    simulacion.insert(0, "t", indices)
    return simulacion


def _metadatos(salida, ruta_config, config, consulta, respuesta, historico, simulacion):
    """Procedencia de la adquisición: consulta, celda devuelta, convenciones temporales, conteos y hashes."""
    return {
        **consulta,
        "documentacion": DOCUMENTACION,
        "fuente": FUENTE,
        "modelo": MODELO,
        "variable_api": "shortwave_radiation",
        "unidad": "W/m²",
        "coordenadas_solicitadas": {"latitud": config["latitud"], "longitud": config["longitud"]},
        "celda_devuelta": {clave: respuesta[clave] for clave in ("latitude", "longitude")},
        "elevation_devuelta_m": respuesta.get("elevation"),
        "zona_horaria_calendario": config["zona_horaria"],
        "periodos": config["periodos"],
        **CONVENCIONES,
        "configuracion_sha256": _sha256(ruta_config),
        "conteos": {"historico": len(historico), "simulacion": len(simulacion)},
        "sha256": {nombre: _sha256(salida / nombre) for nombre in ARCHIVOS_ADQUIRIDOS},
    }


def descargar_irradiancia(ruta_config, salida=None, inicio=None, fin=None):
    """Guarda una adquisición nueva; nunca sustituye los CSV existentes.

    Ante una respuesta inválida conserva consulta y respuesta para diagnosticar.
    """
    ruta_config = Path(ruta_config).resolve()
    config = cargar_configuracion(ruta_config)
    inicio, fin = _ventana(config, inicio, fin)
    url = construir_consulta(config, inicio, fin)
    consultado = datetime.now(timezone.utc)
    salida = _carpeta_nueva(ruta_config, config, salida, consultado)
    consulta = {
        "url": url,
        "consultado_utc": consultado.isoformat(),
        "inicio_intervalo": inicio.isoformat(),
        "fin_exclusivo": fin.isoformat(),
    }
    _guardar_json(salida / "consulta.json", consulta)
    cruda = _descargar(url)
    (salida / "respuesta.json").write_bytes(cruda)
    respuesta = _leer_json(cruda, salida / "respuesta.json")
    historico = normalizar_respuesta(respuesta, config, inicio, fin)
    historico.to_csv(salida / "irradiancia_historica.csv", index=False, lineterminator="\n")
    simulacion = _tabla_de_simulacion(historico, config)
    simulacion.to_csv(salida / "irradiancia_simulacion.csv", index=False, lineterminator="\n")
    _guardar_json(salida / "metadatos.json", _metadatos(salida, ruta_config, config, consulta, respuesta, historico, simulacion))
    return salida


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="scripts/configuracion.json", type=Path)
    parser.add_argument("--salida", type=Path, help="Directorio nuevo; por defecto data/adquisiciones/<instanteUTC>")
    parser.add_argument("--inicio", help="Fecha local ISO inclusiva; por defecto inicio del histórico")
    parser.add_argument("--fin", help="Fecha local ISO exclusiva; por defecto fin de la reserva")
    args = parser.parse_args()
    try:
        salida = descargar_irradiancia(args.config, args.salida, args.inicio, args.fin)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(salida)


if __name__ == "__main__":
    main()
