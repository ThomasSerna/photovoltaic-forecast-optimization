"""Cuantiles solares sin fuga temporal; el pipeline doble no descarga pesos."""

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.pronosticos import (
    cargar_chronos, corregir_cuantiles, mascara_noche, pronosticar_chronos,
)


ORIGEN = pd.Timestamp("2025-01-06T00:00:00-05:00")
CUANTILES = (0.1, 0.5, 0.9)


class PipelineFalso:
    def __init__(self, salida=None):
        self.llamadas = []
        self.salida = salida

    def predict_quantiles(self, inputs, prediction_length, quantile_levels, **kwargs):
        self.llamadas.append((np.asarray(inputs).reshape(-1).copy(), prediction_length,
                              tuple(quantile_levels)))
        valores = np.arange(prediction_length * len(quantile_levels), dtype=float)
        valores = valores.reshape(1, prediction_length, len(quantile_levels)) + 100
        if self.salida is not None:
            valores = self.salida
        return [valores], [np.full((1, prediction_length), -999.)]


@pytest.fixture
def historico():
    inicios = pd.date_range(ORIGEN - pd.Timedelta(hours=6), periods=12, freq="h").tz_convert("UTC")
    fines = inicios + pd.Timedelta(hours=1)
    return pd.DataFrame({"inicio_utc": inicios, "fin_utc": fines, "available_at": fines,
                         "ghi_w_m2": np.arange(12, dtype=float) * 10})


@pytest.fixture
def config():
    ruta = Path(__file__).resolve().parents[1] / "scripts" / "configuracion.json"
    return json.loads(ruta.read_text(encoding="utf-8"))


def pronosticar(pipeline, historico, origen=ORIGEN, **kwargs):
    parametros = {"contexto": 4, "horizonte": 3, "cuantiles": CUANTILES, **kwargs}
    return pronosticar_chronos(pipeline, historico, origen, **parametros)


def test_ventana_tiempos_cuantiles_y_unidades(historico):
    pipeline = PipelineFalso()
    original = historico.copy(deep=True)
    resultado = pronosticar(pipeline, historico)
    pd.testing.assert_index_equal(
        resultado.index, pd.date_range(ORIGEN.tz_convert("UTC"), periods=3, freq="h", name="inicio_utc"),
    )
    assert list(resultado.columns) == list(CUANTILES)
    np.testing.assert_array_equal(resultado.to_numpy(), np.arange(9).reshape(3, 3) + 100)
    contexto, horizonte, cuantiles = pipeline.llamadas[0]
    np.testing.assert_array_equal(contexto, [20., 30., 40., 50.])
    assert horizonte == 3 and cuantiles == CUANTILES
    pd.testing.assert_frame_equal(historico, original)


def test_futuro_y_reserva_no_afectan_prediccion(historico):
    pipeline = PipelineFalso()
    esperado = pronosticar(pipeline, historico)
    alterado = historico.copy(deep=True)
    alterado.loc[alterado["inicio_utc"] >= ORIGEN, "ghi_w_m2"] = 999999.
    obtenido = pronosticar(pipeline, alterado, ORIGEN.tz_convert("UTC"))
    pd.testing.assert_frame_equal(obtenido, esperado)
    assert len(pipeline.llamadas) == 2
    np.testing.assert_array_equal(pipeline.llamadas[0][0], pipeline.llamadas[1][0])


@pytest.mark.parametrize("alteracion", ["disponibilidad", "hueco", "duplicado", "fin", "desorden"])
def test_contexto_debe_cubrir_rejilla_completa(historico, alteracion):
    if alteracion == "disponibilidad":
        historico.loc[5, "available_at"] = ORIGEN + pd.Timedelta(hours=1)
    elif alteracion == "hueco":
        historico = historico.drop(index=4)
    elif alteracion == "duplicado":
        historico = pd.concat([historico, historico.iloc[[4]]], ignore_index=True)
    elif alteracion == "fin":
        historico.loc[4, "fin_utc"] += pd.Timedelta(minutes=1)
    else:
        historico = historico.iloc[[0, 1, 3, 2, 4, 5, 6, 7, 8, 9, 10, 11]]
    pipeline = PipelineFalso()
    with pytest.raises(ValueError):
        pronosticar(pipeline, historico)
    assert not pipeline.llamadas


@pytest.mark.parametrize("origen", ["2025-01-06", "inválido", None, ORIGEN + pd.Timedelta(minutes=30)])
def test_origen_debe_tener_zona_y_hora_entera(historico, origen):
    with pytest.raises(ValueError):
        pronosticar(PipelineFalso(), historico, origen)


@pytest.mark.parametrize("parametros", [
    {"contexto": 0}, {"contexto": True}, {"contexto": 8193},
    {"horizonte": 0}, {"horizonte": 1.5}, {"horizonte": 1025},
    {"cuantiles": (0.1, 0.9)}, {"cuantiles": (0.1, 0.5, 0.5)},
    {"cuantiles": (0.5, 0.1)}, {"cuantiles": (0., 0.5, 0.9)},
    {"cuantiles": (0.1, 0.5, 1.)}, {"cuantiles": (0.1, 0.5, float("nan"))},
])
def test_rechaza_parametros_invalidos(historico, parametros):
    pipeline = PipelineFalso()
    with pytest.raises(ValueError):
        pronosticar(pipeline, historico, **parametros)
    assert not pipeline.llamadas


def test_contexto_horizonte_y_cuantiles_son_ajustables(historico):
    pipeline = PipelineFalso()
    cuantiles = (0.01, 0.5, 0.99)
    resultado = pronosticar(pipeline, historico, contexto=2, horizonte=2, cuantiles=cuantiles)
    assert resultado.shape == (2, 3)
    assert list(resultado.columns) == list(cuantiles)
    np.testing.assert_array_equal(pipeline.llamadas[0][0], [40., 50.])


def test_convierte_tensor_del_modelo_a_tabla(historico):
    class TensorFalso:
        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.ones((1, 3, 3))

    resultado = pronosticar(PipelineFalso(TensorFalso()), historico)
    np.testing.assert_array_equal(resultado.to_numpy(), np.ones((3, 3)))


@pytest.mark.parametrize("valor", [float("nan"), float("inf"), -1.])
def test_rechaza_contexto_no_fisico(historico, valor):
    historico.loc[5, "ghi_w_m2"] = valor
    with pytest.raises(ValueError):
        pronosticar(PipelineFalso(), historico)


@pytest.mark.parametrize("salida", [
    np.zeros((3, 3)), np.zeros((2, 3, 3)), np.zeros((1, 2, 3)),
    np.zeros((1, 3, 2)), np.full((1, 3, 3), float("nan")),
    np.full((1, 3, 3), float("inf")),
])
def test_rechaza_salida_invalida_del_modelo(historico, salida):
    with pytest.raises(ValueError):
        pronosticar(PipelineFalso(salida), historico)


def test_cargar_usa_checkpoint_oficial_revision_y_dispositivo(monkeypatch):
    llamadas = []
    pipeline = PipelineFalso()

    def cargar(modelo, **kwargs):
        llamadas.append((modelo, kwargs))
        return pipeline

    monkeypatch.setitem(sys.modules, "chronos", SimpleNamespace(
        Chronos2Pipeline=SimpleNamespace(from_pretrained=cargar),
    ))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(float32="float32"))
    revision = "a" * 40
    assert cargar_chronos(revision, dispositivo="cpu") is pipeline
    assert len(llamadas) == 1
    modelo, parametros = llamadas[0]
    assert modelo == "amazon/chronos-2"
    assert parametros["revision"] == revision
    assert parametros["device_map"] == "cpu"


def test_noche_considera_intervalo_completo_y_no_solo_inicio(config):
    pytest.importorskip("astral")
    indice = pd.DatetimeIndex([f"2025-01-06T{hora}:00:00-05:00" for hora in ("05", "06", "12", "18", "19")])
    esperado = [True, False, False, False, True]
    np.testing.assert_array_equal(mascara_noche(indice, config), esperado)
    np.testing.assert_array_equal(mascara_noche(indice.tz_convert("UTC"), config), esperado)


def test_correcciones_fisicas_compartidas_sin_recorte_superior(config):
    pytest.importorskip("astral")
    indice = pd.DatetimeIndex([f"2025-01-06T{hora}:00:00-05:00" for hora in ("05", "06", "12", "13", "19")])
    tabla = pd.DataFrame([[-2., 3., 1.], [-3., 5., 4.], [10., 20., 15.],
                          [1200., 1300., 1400.], [0., 0., 0.]], index=indice, columns=CUANTILES)
    original, configuracion = tabla.copy(deep=True), deepcopy(config)
    corregida, diagnostico = corregir_cuantiles(tabla, config)
    esperado = pd.DataFrame([[0., 0., 0.], [0., 4., 5.], [10., 15., 20.],
                             [1200., 1300., 1400.], [0., 0., 0.]], index=indice, columns=CUANTILES)
    pd.testing.assert_frame_equal(corregida, esperado)
    assert diagnostico["filas_cruzadas"] == 3
    assert diagnostico["valores_negativos"] == 2
    assert diagnostico["valores_nocturnos_corregidos"] == 2
    assert diagnostico["valores_corregidos"] == 8
    pd.testing.assert_frame_equal(tabla, original)
    assert config == configuracion


@pytest.mark.parametrize("valor", [float("nan"), float("inf")])
def test_correccion_no_oculta_cuantiles_no_finitos(config, valor):
    tabla = pd.DataFrame([[0., valor, 1.]], columns=CUANTILES,
                         index=pd.DatetimeIndex([ORIGEN.tz_convert("UTC")]))
    with pytest.raises(ValueError):
        corregir_cuantiles(tabla, config)
