"""Preparación trazable sin red y ventanas temporales sin etiquetas fuera de corte."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import datos_lora


RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "scripts/configuracion.json"


@pytest.fixture
def historia():
    inicios = pd.date_range("2025-01-06T00:00:00-05:00", periods=12, freq="h").tz_convert("UTC")
    return pd.DataFrame({"inicio_utc": inicios, "fin_utc": inicios + pd.Timedelta(hours=1),
                         "available_at": inicios + pd.Timedelta(hours=1), "ghi_w_m2": np.arange(12, dtype=float)})


def test_ventanas_solo_contexto_pasado_y_objetivos_en_particion(historia):
    inicio = historia.inicio_utc.iloc[4]
    fin = historia.fin_utc.iloc[-1]
    antes = historia.copy()
    ventanas = datos_lora.ventanas_validacion(historia, inicio, fin, contexto=4, horizonte=3, paso=2)
    assert len(ventanas) == 3
    for ventana, primero in zip(ventanas, (0, 2, 4)):
        assert ventana.dtype == np.float32
        np.testing.assert_array_equal(ventana, np.arange(primero, primero + 7))
    # Se excluye el origen 10: sus tres etiquetas cruzarían el fin exclusivo 12.
    assert ventanas[-1][-1] == 10
    pd.testing.assert_frame_equal(historia, antes)


@pytest.mark.parametrize("fallo", ["hueco", "duplicado", "negativo", "no_disponible"])
def test_ventanas_rechazan_entradas_invalidas(historia, fallo):
    inicio = historia.inicio_utc.iloc[4]
    fin = historia.fin_utc.iloc[-1]
    if fallo == "hueco":
        historia = historia.drop(index=5)
    elif fallo == "duplicado":
        historia.loc[5, "inicio_utc"] = historia.loc[4, "inicio_utc"]
    elif fallo == "negativo":
        historia.loc[5, "ghi_w_m2"] = -1.
    else:
        historia.loc[3, "available_at"] = inicio + pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        datos_lora.ventanas_validacion(historia, inicio, fin, contexto=4, horizonte=3)


@pytest.mark.parametrize("cambio", [{"contexto": 0}, {"paso": True}, {"horizonte": 0}])
def test_parametros_de_ventanas(historia, cambio):
    with pytest.raises(ValueError):
        datos_lora.ventanas_validacion(historia, historia.inicio_utc.iloc[4], historia.fin_utc.iloc[-1], **cambio)


def test_preparacion_reutiliza_solo_adquisicion_integra_y_no_toca_publicados(tmp_path, monkeypatch):
    monkeypatch.setattr(datos_lora, "ANIOS", (2023,))
    periodos = deepcopy(datos_lora.PERIODOS_LORA)
    periodos["entrenamiento"]["inicio"] = "2023-01-01T00:00:00-05:00"
    monkeypatch.setattr(datos_lora, "PERIODOS_LORA", periodos)
    tiempos = pd.date_range("2023-01-01", "2024-01-02", freq="h", inclusive="left", tz="UTC")
    cruda = json.dumps({"latitude": 5.5, "longitude": -75.75, "elevation": 1828., "utc_offset_seconds": 0,
                       "hourly_units": {"time": "unixtime", "shortwave_radiation": "W/m²"},
                       "hourly": {"time": [int(t.timestamp()) for t in tiempos],
                                  "shortwave_radiation": [100.] * len(tiempos)}}).encode()
    llamadas = []

    def descargar(url):
        llamadas.append(url)
        return cruda

    monkeypatch.setattr("src.descarga._descargar", descargar)
    fuente = RAIZ / "data/irradiancia_historica.csv"
    antes = hashlib.sha256(fuente.read_bytes()).hexdigest()
    salida = tmp_path / "datos"
    manifiesto = datos_lora.preparar_datos(CONFIG, salida)
    assert len(llamadas) == 1 and "start_date=2023-01-01" in llamadas[0]
    assert manifiesto["horas"] == 26304
    assert manifiesto["horas_por_periodo"]["entrenamiento"] == 17544
    assert manifiesto["horas_por_periodo"]["validacion"] == 1344
    assert manifiesto["horas_por_periodo"]["evaluacion_nueva"] == 6600
    assert datos_lora.preparar_datos(CONFIG, salida) == manifiesto
    assert len(llamadas) == 1
    historia = datos_lora.cargar_datos(CONFIG, salida)
    assert list(historia.columns) == datos_lora.COLUMNAS
    assert historia.iloc[0].inicio_utc == pd.Timestamp("2023-01-01T05:00:00Z")
    assert historia.iloc[0].fin_utc == pd.Timestamp("2023-01-01T06:00:00Z")
    assert hashlib.sha256(fuente.read_bytes()).hexdigest() == antes
    # El checkout Windows/Linux puede cambiar finales de línea de JSON/CSV.
    metadatos = salida / "2023/metadatos.json"
    metadatos.write_bytes(metadatos.read_bytes().replace(b"\r\n", b"\n"))
    assert len(datos_lora.cargar_datos(CONFIG, salida)) == 26304
    (salida / "2023/respuesta.json.gz").write_bytes(b"alterado")
    with pytest.raises(ValueError, match="SHA256"):
        datos_lora.preparar_datos(CONFIG, salida)
    assert len(llamadas) == 1


def test_carpeta_parcial_no_se_sobrescribe(tmp_path, monkeypatch):
    monkeypatch.setattr(datos_lora, "ANIOS", (2023,))
    (tmp_path / "2023").mkdir()
    original = b"conservar evidencia"
    (tmp_path / "2023/respuesta.json.gz").write_bytes(original)
    with pytest.raises(ValueError, match="JSON ausente"):
        datos_lora.preparar_datos(CONFIG, tmp_path)
    assert (tmp_path / "2023/respuesta.json.gz").read_bytes() == original
