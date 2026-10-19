"""Conversión de la hora precedente y trazabilidad sin consultas de red."""

from copy import deepcopy
import hashlib
import io
import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest

from src.descarga import construir_consulta, descargar_irradiancia, normalizar_respuesta


@pytest.fixture
def config_solar():
    cortes = ["2024-01-01", "2025-01-06", "2025-03-03", "2025-03-31", "2026-01-01"]
    nombres = ["historial_previo", "desarrollo", "evaluacion", "reserva_no_usada"]
    return {
        "zona_horaria": "America/Bogota", "latitud": 5.5610705, "longitud": -75.8092448,
        "periodos": {nombre: {"inicio": a + "T00:00:00-05:00", "fin": b + "T00:00:00-05:00"}
                     for nombre, a, b in zip(nombres, cortes, cortes[1:])},
    }


def respuesta_solar(inicio="2025-01-06", fin="2025-01-07"):
    desde = pd.Timestamp(inicio, tz="America/Bogota").tz_convert("UTC").floor("D")
    hasta = pd.Timestamp(fin, tz="America/Bogota").tz_convert("UTC").ceil("D")
    tiempos = pd.date_range(desde, hasta, freq="h", inclusive="left")
    return {
        "latitude": 5.5, "longitude": -75.75, "elevation": 1828.,
        "timezone": "GMT", "utc_offset_seconds": 0,
        "hourly_units": {"time": "unixtime", "shortwave_radiation": "W/m²"},
        "hourly": {"time": [int(t.timestamp()) for t in tiempos],
                   "shortwave_radiation": [float(i * 100) for i in range(len(tiempos))]},
    }


def test_desplaza_solo_crudo_y_no_recorta_ghi(config_solar):
    datos = normalizar_respuesta(respuesta_solar(), config_solar, "2025-01-06", "2025-01-07")
    assert len(datos) == 24
    assert datos.iloc[0]["inicio_intervalo"] == "2025-01-06T00:00:00-05:00"
    assert datos.iloc[0]["fin_utc"] == "2025-01-06T06:00:00+00:00"
    assert datos.iloc[0]["ghi_w_m2"] == 600.
    assert datos.iloc[-1]["fin_intervalo"] == "2025-01-07T00:00:00-05:00"
    assert datos["ghi_w_m2"].max() > 1000
    assert datos["ghi_w_m2"].equals(datos["irradiacion_wh_m2"])
    assert datos["particion"].eq("desarrollo").all()


def test_incluye_fin_ultimo_intervalo_2025(config_solar):
    parametros = parse_qs(urlsplit(construir_consulta(config_solar, "2025-12-31", "2026-01-01")).query)
    assert parametros["end_date"] == ["2026-01-01"]
    assert parametros["models"] == ["era5"]
    assert parametros["timezone"] == ["GMT"]
    assert parametros["timeformat"] == ["unixtime"]
    datos = normalizar_respuesta(respuesta_solar("2025-12-31", "2026-01-01"), config_solar,
                                 "2025-12-31", "2026-01-01")
    assert len(datos) == 24
    assert datos.iloc[-1]["fin_utc"] == "2026-01-01T05:00:00+00:00"
    assert datos["particion"].eq("reserva_no_usada").all()


@pytest.mark.parametrize("campo,valor,mensaje", [
    ("latitude", None, "latitude"), ("longitude", float("inf"), "longitude"),
    ("latitude", 91, "latitude"), ("utc_offset_seconds", -18000, "utc_offset_seconds"),
    ("hourly_units", {"time": "unixtime", "shortwave_radiation": "Wh/m²"}, "hourly_units"),
    ("hourly", {}, "listas no vacías"),
])
def test_rechaza_esquema_y_metadatos(config_solar, campo, valor, mensaje):
    respuesta = respuesta_solar()
    respuesta[campo] = valor
    with pytest.raises(ValueError, match=mensaje):
        normalizar_respuesta(respuesta, config_solar, "2025-01-06", "2025-01-07")


@pytest.mark.parametrize("valor", [None, -1, float("inf"), float("nan"), "5", True])
def test_rechaza_ghi_invalida(config_solar, valor):
    respuesta = respuesta_solar()
    respuesta["hourly"]["shortwave_radiation"][6] = valor
    with pytest.raises(ValueError, match=r"shortwave_radiation\[6\]"):
        normalizar_respuesta(respuesta, config_solar, "2025-01-06", "2025-01-07")


def test_rechaza_duplicados_y_cobertura_incompleta(config_solar):
    respuesta = respuesta_solar()
    duplicada = deepcopy(respuesta)
    duplicada["hourly"]["time"][6] = duplicada["hourly"]["time"][5]
    with pytest.raises(ValueError, match="duplicados"):
        normalizar_respuesta(duplicada, config_solar, "2025-01-06", "2025-01-07")
    respuesta["hourly"] = {clave: valores[:29] for clave, valores in respuesta["hourly"].items()}
    with pytest.raises(ValueError, match="cobertura incompleta"):
        normalizar_respuesta(respuesta, config_solar, "2025-01-06", "2025-01-07")


@pytest.mark.parametrize("inicio,fin", [("2025-01-07", "2025-01-06"),
                                        ("2023-01-01", "2025-01-06"),
                                        ("2025-01-06T00:30:00-05:00", "2025-01-07")])
def test_rechaza_ventana_invalida(config_solar, inicio, fin):
    with pytest.raises(ValueError, match="Límite|Ventana"):
        construir_consulta(config_solar, inicio, fin)


def test_guarda_nueva_adquisicion_y_hashes(tmp_path, monkeypatch, config_solar):
    ruta_config = tmp_path / "config.json"
    ruta_config.write_text(json.dumps(config_solar), encoding="utf-8")
    monkeypatch.setattr("src.descarga.cargar_configuracion", lambda ruta: config_solar)
    cruda = json.dumps(respuesta_solar("2025-01-07", "2025-01-08")).encode()
    monkeypatch.setattr("src.descarga.urlopen", lambda *args, **kwargs: io.BytesIO(cruda))
    salida = tmp_path / "nueva"
    assert descargar_irradiancia(ruta_config, salida, "2025-01-07", "2025-01-08") == salida
    assert (salida / "respuesta.json").read_bytes() == cruda
    metadatos = json.loads((salida / "metadatos.json").read_text(encoding="utf-8"))
    assert metadatos["conteos"] == {"historico": 24, "simulacion": 24}
    assert metadatos["celda_devuelta"] == {"latitude": 5.5, "longitude": -75.75}
    assert "no reconstruye" in metadatos["procedencia"]
    for nombre, sha in metadatos["sha256"].items():
        assert hashlib.sha256((salida / nombre).read_bytes()).hexdigest() == sha
    solar = pd.read_csv(salida / "irradiancia_simulacion.csv")
    assert solar["t"].tolist() == list(range(24, 48))
    with pytest.raises(FileExistsError):
        descargar_irradiancia(ruta_config, salida, "2025-01-07", "2025-01-08")
    assert (salida / "respuesta.json").read_bytes() == cruda


def test_error_de_red_no_produce_csv(tmp_path, monkeypatch, config_solar):
    ruta_config = tmp_path / "config.json"
    ruta_config.write_text(json.dumps(config_solar), encoding="utf-8")
    monkeypatch.setattr("src.descarga.cargar_configuracion", lambda ruta: config_solar)
    def fallar(*args, **kwargs):
        raise URLError("sin conexión")
    monkeypatch.setattr("src.descarga.urlopen", fallar)
    salida = tmp_path / "fallida"
    with pytest.raises(ValueError, match="Open-Meteo"):
        descargar_irradiancia(ruta_config, salida, "2025-01-06", "2025-01-07")
    assert (salida / "consulta.json").exists()
    assert not list(salida.glob("*.csv"))
