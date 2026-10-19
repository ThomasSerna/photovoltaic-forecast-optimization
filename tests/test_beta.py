"""Referencia beta con ventana común, mezcla cero y fallos explícitos."""

import warnings

import numpy as np
import pandas as pd
import pytest
beta = pytest.importorskip("scipy.stats").beta

from energy_project.pronosticos import pronosticar_beta


ORIGEN = pd.Timestamp("2025-01-06T00:00:00-05:00")
CUANTILES = (0.1, 0.5, 0.9)


@pytest.fixture
def historico():
    indice = pd.date_range(ORIGEN - pd.Timedelta(hours=336), periods=360, freq="h").tz_convert("UTC")
    tabla = pd.DataFrame({"inicio_utc": indice, "fin_utc": indice + pd.Timedelta(hours=1),
                          "available_at": indice + pd.Timedelta(hours=1), "ghi_w_m2": 0.})
    diurnas = (indice.tz_convert("America/Bogota").hour == 6) & (indice < ORIGEN)
    tabla.loc[diurnas, "ghi_w_m2"] = np.arange(1, 15) * 10.
    return tabla


def test_ajuste_real_forma_unidades_y_entradas_intactas(historico):
    original = historico.copy(deep=True)
    resultado = pronosticar_beta(historico, ORIGEN, cuantiles=CUANTILES)
    esperado = pd.date_range(ORIGEN.tz_convert("UTC"), periods=24, freq="h", name="inicio_utc")
    pd.testing.assert_index_equal(resultado.index, esperado)
    assert list(resultado.columns) == list(CUANTILES)
    assert np.isfinite(resultado.to_numpy()).all()
    assert (np.diff(resultado.to_numpy(), axis=1) >= 0).all()
    assert (resultado.iloc[6] > 0).all() and (resultado.iloc[6] < 147.).all()
    assert not resultado.drop(index=esperado[6]).to_numpy().any()
    pd.testing.assert_frame_equal(historico, original)
    pd.testing.assert_frame_equal(pronosticar_beta(historico, ORIGEN.tz_convert("UTC"),
                                                  cuantiles=CUANTILES), resultado)


def test_masa_cero_cuantil_en_umbral_y_positivos_constantes(historico, monkeypatch):
    horas = historico["inicio_utc"].dt.tz_convert("America/Bogota").dt.hour
    filas = historico.index[(horas == 6) & (historico["inicio_utc"] < ORIGEN)]
    historico.loc[filas[:7], "ghi_w_m2"] = 0.
    filas_constantes = historico.index[(horas == 7) & (historico["inicio_utc"] < ORIGEN)]
    historico.loc[filas_constantes[7:], "ghi_w_m2"] = 1230.
    llamadas = []

    def uniforme(valores, *, floc, fscale):
        llamadas.append((valores.copy(), floc, fscale))
        return 1., 1., floc, fscale

    monkeypatch.setattr(beta, "fit", uniforme)
    resultado = pronosticar_beta(historico, ORIGEN, cuantiles=CUANTILES)
    np.testing.assert_allclose(resultado.iloc[6], [0., 0., .8 * 147.])
    np.testing.assert_array_equal(resultado.iloc[7], [0., 0., 1230.])
    assert len(llamadas) == 1
    valores, loc, escala = llamadas[0]
    np.testing.assert_array_equal(valores, np.arange(8, 15) * 10.)
    assert loc == 0 and escala == 147.


def test_no_usa_futuro_ni_observaciones_fuera_de_ventana(historico):
    esperado = pronosticar_beta(historico, ORIGEN, cuantiles=CUANTILES)
    historico.loc[historico["inicio_utc"] >= ORIGEN, "ghi_w_m2"] = 999999.
    anterior = historico.iloc[:24].copy()
    for campo in ("inicio_utc", "fin_utc", "available_at"):
        anterior[campo] -= pd.Timedelta(hours=24)
    anterior["ghi_w_m2"] = 999999.
    alterado = pd.concat([anterior, historico], ignore_index=True)
    pd.testing.assert_frame_equal(pronosticar_beta(alterado, ORIGEN, cuantiles=CUANTILES), esperado)


def test_exige_contexto_disponible_completo(historico):
    historico.loc[335, "available_at"] = ORIGEN + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="contexto"):
        pronosticar_beta(historico, ORIGEN)


def test_hora_sin_observaciones_no_se_sustituye(historico):
    with pytest.raises(ValueError, match="beta: origen .*hora local 0: sin observaciones"):
        pronosticar_beta(historico, ORIGEN, contexto=4)


@pytest.mark.parametrize("fallo", ["excepcion", "aviso", "parametros", "cuantiles"])
def test_fallo_de_ajuste_informa_origen_y_hora_sin_fallback(historico, monkeypatch, fallo):
    if fallo == "excepcion":
        def fallar(*args, **kwargs):
            raise RuntimeError("no converge")
        monkeypatch.setattr(beta, "fit", fallar)
    elif fallo == "aviso":
        def avisar(*args, **kwargs):
            warnings.warn("no converge", RuntimeWarning)
            return 1., 1., 0., 147.
        monkeypatch.setattr(beta, "fit", avisar)
    elif fallo == "parametros":
        monkeypatch.setattr(beta, "fit", lambda *a, **k: (float("nan"), 1., 0., 147.))
    else:
        monkeypatch.setattr(beta, "ppf", lambda q, *a: np.full_like(q, float("nan")))
    with pytest.raises(ValueError, match="beta: origen .*hora local 6:"):
        pronosticar_beta(historico, ORIGEN, cuantiles=CUANTILES)


@pytest.mark.parametrize("parametros", [
    {"horizonte": 0}, {"horizonte": True}, {"cuantiles": (0.1, 0.9)},
    {"contexto": 0}, {"zona_horaria": "No/Existe"},
])
def test_rechaza_parametros_invalidos(historico, parametros):
    with pytest.raises(ValueError):
        pronosticar_beta(historico, ORIGEN, **parametros)
