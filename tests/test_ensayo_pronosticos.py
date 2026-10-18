"""Ensayo T02 con un proveedor pequeño: cronología, artefactos y repetición sin pesos."""

import importlib.metadata
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("astral")
pytest.importorskip("matplotlib")
pytest.importorskip("psutil")

from energy_project import cargar_caso, historial_admisible
from energy_project import pronosticos

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def ensayo(monkeypatch):
    caso = cargar_caso(RAIZ / "scripts/configuracion.json")
    caso.configuracion["pronosticos"] = {
        "revision": "a" * 40, "dispositivo": "cpu", "contexto": 336,
        "cuantiles": [i / 10 for i in range(1, 10)],
    }
    caso.irradiancia_simulacion["ghi_w_m2"] = 10.
    llamadas, contextos = [], []
    pipeline = SimpleNamespace(model=SimpleNamespace(device="cpu", dtype="torch.float32"))

    def cargar(revision, dispositivo):
        llamadas.append((revision, dispositivo))
        return pipeline

    def predecir(modelo, historico, origen, *, contexto, horizonte, cuantiles):
        assert modelo is pipeline
        contexto_real = historial_admisible(historico, origen).tail(contexto)
        contextos.append((origen, contexto_real))
        indice = pd.date_range(origen, periods=horizonte, freq="h", name="inicio_utc")
        return pd.DataFrame({q: np.full(horizonte, 8 + 10 * (q - .5)) for q in cuantiles}, index=indice)

    monkeypatch.setattr(pronosticos, "cargar_chronos", cargar)
    monkeypatch.setattr(pronosticos, "pronosticar_chronos", predecir)
    monkeypatch.setattr(pronosticos, "corregir_cuantiles", lambda tabla, config: (tabla + 1, {"noche": 0}))
    monkeypatch.setattr(pronosticos, "mascara_noche", lambda indice, config: np.asarray(indice.hour < 6))
    monkeypatch.setattr(pronosticos, "_graficar_pronostico", lambda tabla, real, salida: salida.write_bytes(b"PNG"))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda: SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=1234)), virtual_memory=lambda: SimpleNamespace(total=16000)))
    monkeypatch.setattr(importlib.metadata, "version", lambda paquete: "prueba")
    return caso, llamadas, contextos


def test_ensayo_dos_origenes_una_carga_y_repeticion(ensayo, tmp_path):
    caso, llamadas, contextos = ensayo
    salida = tmp_path / "ensayo"
    resumen = pronosticos.ensayo_chronos(caso, salida)
    assert llamadas == [("a" * 40, "cpu")]
    origen = pd.Timestamp(caso.configuracion["periodos"]["desarrollo"]["inicio"]).tz_convert("UTC")
    assert [actual for actual, _ in contextos] == [origen, origen + pd.Timedelta(hours=6), origen]
    for actual, contexto in contextos:
        assert len(contexto) == 336
        assert contexto["fin_utc"].max() == actual
        assert contexto["available_at"].max() <= actual
        assert contexto["inicio_utc"].max() < actual
    csv = pd.read_csv(salida / "pronosticos.csv")
    assert len(csv) == 48
    assert pd.Timestamp(csv["inicio_utc"].iloc[0]) == origen
    assert pd.Timestamp(csv["inicio_utc"].iloc[24]) == origen + pd.Timedelta(hours=6)
    assert csv["crudo_q0.5"].eq(8).all()
    assert csv["q0.5"].eq(9).all()
    assert csv["ghi_real_w_m2"].eq(10).all()
    assert resumen["metricas"]["crudo"]["global"] == {"horas": 48, "mae_w_m2": 2., "rmse_w_m2": 2.}
    assert resumen["metricas"]["corregido"]["diurno"] == {"horas": 36, "mae_w_m2": 1., "rmse_w_m2": 1.}
    assert resumen["repeticion"]["coincide"] is True
    assert len(resumen["rss_muestras_bytes"]) == 5
    assert resumen["dtype"] == "torch.float32"
    assert resumen["entradas_sha256"]["configuracion"]["sha256"]
    assert json.loads((salida / "resumen.json").read_text(encoding="utf-8")) == resumen
    assert (salida / "pronostico.png").read_bytes() == b"PNG"


def test_metricas_manual_y_ausencia_de_horas_diurnas():
    resultado = pronosticos._metricas_pronostico([1, 5], [0, 2], np.array([False, True]))
    assert resultado["global"]["mae_w_m2"] == 2
    assert resultado["global"]["rmse_w_m2"] == pytest.approx(np.sqrt(5))
    assert resultado["diurno"]["mae_w_m2"] == 3
    assert pronosticos._metricas_pronostico([1], [0], np.array([False]))["diurno"] == {
        "horas": 0, "mae_w_m2": None, "rmse_w_m2": None,
    }


def test_ensayo_no_sobrescribe_salida_existente(ensayo, tmp_path):
    caso, llamadas, _ = ensayo
    salida = tmp_path / "existente"
    salida.mkdir()
    anterior = salida / "resumen.json"
    anterior.write_text("conservar", encoding="utf-8")
    with pytest.raises(FileExistsError):
        pronosticos.ensayo_chronos(caso, salida)
    assert anterior.read_text(encoding="utf-8") == "conservar"
    assert llamadas == []


def test_ensayo_no_reemplaza_entrada(ensayo):
    caso, llamadas, _ = ensayo
    ruta = caso.rutas["irradiancia_historica"]
    original = ruta.read_bytes()
    with pytest.raises(ValueError, match="entrada"):
        pronosticos.ensayo_chronos(caso, ruta)
    assert ruta.read_bytes() == original
    assert llamadas == []


@pytest.mark.parametrize("valor, mensaje", [(999., "repetición"), (float("nan"), "no finitos")])
def test_ensayo_rechaza_repeticion_diferente_o_no_finita(ensayo, tmp_path, monkeypatch, valor, mensaje):
    caso, _, contextos = ensayo
    predecir = pronosticos.pronosticar_chronos

    def repeticion(*args, **kwargs):
        tabla = predecir(*args, **kwargs)
        if len(contextos) == 3:
            tabla.iloc[0, 0] = valor
        return tabla

    monkeypatch.setattr(pronosticos, "pronosticar_chronos", repeticion)
    salida = tmp_path / "invalido"
    with pytest.raises(ValueError, match=mensaje):
        pronosticos.ensayo_chronos(caso, salida)
    assert not (salida / "resumen.json").exists()


def test_ensayo_exige_referencia_del_horizonte_completo(ensayo, tmp_path):
    caso, _, _ = ensayo
    caso.irradiancia_simulacion = caso.irradiancia_simulacion.iloc[1:]
    with pytest.raises(ValueError, match="referencia"):
        pronosticos.ensayo_chronos(caso, tmp_path / "incompleto")


@pytest.mark.parametrize("cambio", ["desarrollo", "contexto"])
def test_ensayo_rechaza_informacion_invalida_antes_de_cargar(ensayo, tmp_path, cambio):
    caso, llamadas, _ = ensayo
    if cambio == "desarrollo":
        caso.configuracion["periodos"]["desarrollo"]["fin"] = "2025-01-07T00:00:00-05:00"
    else:
        origen = pd.Timestamp(caso.configuracion["periodos"]["desarrollo"]["inicio"])
        ultima = caso.irradiancia_historica["fin_utc"] == origen
        caso.irradiancia_historica.loc[ultima, "available_at"] = origen + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="desarrollo|contexto"):
        pronosticos.ensayo_chronos(caso, tmp_path / "invalido")
    assert llamadas == []


def test_exportacion_conserva_niveles_cercanos_sin_redondear(ensayo, tmp_path):
    caso, _, _ = ensayo
    caso.configuracion["pronosticos"]["cuantiles"] = [0.1, 0.5, 0.5000001, 0.9]
    pronosticos.ensayo_chronos(caso, tmp_path / "cuantiles")
    tabla = pd.read_csv(tmp_path / "cuantiles" / "pronosticos.csv")
    assert "q0.5" in tabla and "q0.5000001" in tabla
    assert "crudo_q0.5" in tabla and "crudo_q0.5000001" in tabla
