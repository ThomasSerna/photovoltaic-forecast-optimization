"""Comparación pareada de controladores con pronósticos congelados de Chronos."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("astral")
pytest.importorskip("matplotlib")

from src.controlador import calcular_cvar, ejecutar_mpc
from src.experimento import metricas_servicio, tabla_pronosticos
from test_controlador import ORIGEN, _caso, _cuantiles, _historia

SPEC = spec_from_file_location("comparacion_mpc", Path(__file__).resolve().parents[1] / "scripts/comparar_mpc_chronos.py")
comparacion = module_from_spec(SPEC)
SPEC.loader.exec_module(comparacion)


@pytest.fixture
def caso_pareado():
    caso = _caso()
    fin = ORIGEN + pd.Timedelta(hours=2)
    caso.configuracion["periodos"] = {"evaluacion": dict(inicio=ORIGEN.isoformat(), fin=fin.isoformat())}
    original = ejecutar_mpc(caso, lambda h, o, H: _cuantiles([0.] * H, o), inicio=ORIGEN, fin=fin)
    informe = dict(inicio_utc=ORIGEN.isoformat(), fin_utc=fin.isoformat(), configuracion=deepcopy(caso.configuracion),
                   metricas=dict(chronos=dict(servicio=metricas_servicio(original, caso.configuracion))))
    real = caso.irradiancia_simulacion.set_index("inicio_utc").ghi_w_m2
    fuente = tabla_pronosticos(original["pronosticos"], real).assign(proveedor="chronos")
    caso.configuracion.update(modo_mpc="cvar", alpha_cvar=.9, lambda_riesgo=0.)
    return caso, informe, original, fuente


def test_pronostico_congelado_rechaza_horizonte_y_duplicados(caso_pareado):
    caso, _, _, fuente = caso_pareado
    callback = comparacion.pronostico_congelado(fuente, caso.configuracion)
    historia = _historia(np.zeros(336))
    with pytest.raises(ValueError, match="horizonte"):
        callback(historia, ORIGEN, 1)
    duplicados = pd.concat([fuente, fuente.iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicadas"):
        comparacion.pronostico_congelado(duplicados, caso.configuracion)
    discontinuo = fuente.copy()
    discontinuo.loc[1, "anticipacion_h"] = 3
    with pytest.raises(ValueError, match="anticipaciones"):
        comparacion.pronostico_congelado(discontinuo, caso.configuracion)


def test_pronostico_congelado_rechaza_historia_futura_y_no_usa_observaciones(caso_pareado):
    caso, _, _, fuente = caso_pareado
    historia = _historia(np.zeros(336))
    callback = comparacion.pronostico_congelado(fuente, caso.configuracion)
    esperado = callback(historia, ORIGEN, 2)
    fuente["ghi_real_w_m2"] = 1e10
    observado_alterado = comparacion.pronostico_congelado(fuente, caso.configuracion)(historia, ORIGEN, 2)
    pd.testing.assert_frame_equal(esperado, observado_alterado)
    historia.loc[335, "available_at"] = ORIGEN + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="futura"):
        callback(historia, ORIGEN, 2)


def test_diario_comparativo_y_cvar_son_comprobables_a_mano():
    fechas = pd.to_datetime(["2025-03-03T23:00:00-05:00", "2025-03-04T00:00:00-05:00"])
    calendario = pd.DataFrame(dict(t=[0, 1], inicio_intervalo=fechas))

    def resultado(atendida):
        cargas = pd.DataFrame(dict(t=[0, 1], demanda_wh=[10., 10.], prioridad_supuesta=[1, 3], atendida_wh=atendida))
        cargas["no_atendida_wh"] = cargas.demanda_wh - cargas.atendida_wh
        return dict(cargas=cargas)

    diario = comparacion.diario_comparativo(dict(anterior=resultado([5., 6.]), nuevo=resultado([8., 5.])), calendario)
    assert diario.J_anterior.tolist() == [5., 18.]
    assert diario.J_nuevo.tolist() == [8., 15.]
    assert diario.perdida_ponderada_anterior.tolist() == [5., 12.]
    assert diario.perdida_ponderada_nuevo.tolist() == [2., 15.]
    assert diario.delta_J.tolist() == [3., -3.]
    assert diario.delta_acumulado_J.tolist() == [3., 0.]
    assert calcular_cvar(diario.perdida_ponderada_anterior, .9) == pytest.approx(12.)
    assert calcular_cvar(diario.perdida_ponderada_nuevo, .9) == pytest.approx(15.)


def test_comparar_preserva_fisica_estados_y_pronosticos(caso_pareado):
    caso, informe, anterior, fuente = caso_pareado
    resultado = comparacion.comparar(caso, informe, anterior, fuente)
    assert resultado["verificaciones"]["pronosticos_identicos"]
    assert resultado["verificaciones"]["filtraciones"] == 0
    assert resultado["verificaciones"]["max_diferencia_pronostico"] == 0.
    assert all(d["violaciones_fisicas"] == 0 for d in resultado["verificaciones"]["fisica"].values())
    fuente.loc[0, "q0.5"] += 1.
    with pytest.raises(ValueError, match="corregidos"):
        comparacion.comparar(caso, informe, anterior, fuente)


def test_cargar_anterior_preserva_floats_y_selecciona_chronos(tmp_path):
    (tmp_path / "resumen.json").write_text(json.dumps(dict(metricas=dict(chronos=dict(servicio={})))), encoding="utf-8")
    (tmp_path / "cargas.csv").write_text("politica,t,valor\nchronos,0,0.10101010101010101\nbeta,0,99\n", encoding="utf-8")
    (tmp_path / "horas.csv").write_text("politica,t\nchronos,0\n", encoding="utf-8")
    (tmp_path / "baterias.csv").write_text("politica,t,bateria_id,final_wh\nchronos,0,b,0.10101010101010101\n", encoding="utf-8")
    _, resultado = comparacion.cargar_anterior(tmp_path)
    assert len(resultado["cargas"]) == 1
    assert resultado["cargas"].valor.iloc[0] == float("0.10101010101010101")
    assert resultado["estado_final"] == {"b": float("0.10101010101010101")}


def test_exporta_cuatro_figuras_png_y_svg(caso_pareado, tmp_path):
    caso, informe, anterior, fuente = caso_pareado
    resultado = comparacion.comparar(caso, informe, anterior, fuente)
    comparacion.graficar(tmp_path, caso, resultado)
    for nombre in ("servicio_diario", "diferencias_servicio", "baterias", "resumen_metricas"):
        for extension in ("png", "svg"):
            assert (tmp_path / f"{nombre}.{extension}").stat().st_size > 5000
