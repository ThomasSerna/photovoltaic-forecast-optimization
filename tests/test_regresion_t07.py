"""Regresión T07: detecta cambios físicos y contrasta cifras históricas de T05."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("astral")
pytest.importorskip("matplotlib")

from src.controlador import ejecutar_mpc
from test_controlador import ORIGEN, _caso, _cuantiles

SPEC = spec_from_file_location("regresion_t07", Path(__file__).resolve().parents[1] /
                               "scripts/verificar_regresion_t07.py")
regresion = module_from_spec(SPEC)
SPEC.loader.exec_module(regresion)


def test_original_coincide_con_revision_pre_t06_y_detecta_cambios():
    caso = _caso(real=(0., 0.), prioridades=(1, 3))
    pronosticar = lambda historia, origen, horizonte: _cuantiles([0.] * horizonte, origen)
    fin = ORIGEN + pd.Timedelta(hours=2)
    anterior, huella = regresion.cargar_referencia()
    actual = ejecutar_mpc(caso, pronosticar, inicio=ORIGEN, fin=fin)
    previo = anterior.ejecutar_mpc(caso, pronosticar, inicio=ORIGEN, fin=fin)
    assert len(huella) == 64
    assert regresion.comparar_resultados(actual, previo)["coincide"]
    alterado = deepcopy(actual)
    alterado["baterias"].loc[0, "final_wh"] += 1e-4
    with pytest.raises(ValueError, match="cambió baterias"):
        regresion.comparar_resultados(alterado, previo)


def test_cifras_publicadas_respetan_su_precision_y_rechazan_cambios():
    def rama(servicio, J, mae):
        return dict(servicio=dict(S_pond=servicio, J=J),
                    pronostico=dict(diurno=dict(corregido=dict(mae_w_m2=mae))))

    resumen = dict(metricas=dict(beta=rama(.9312263476, 1000., 66.33),
                                chronos=rama(.9328742419, 1505.599, 64.20),
                                reactiva=dict(servicio=dict(J=285240.845))))
    assert all(f["coincide"] for f in regresion.contrastar_publicados(resumen).values())
    resumen["metricas"]["beta"]["servicio"]["S_pond"] += 1e-6
    with pytest.raises(ValueError, match="cifras publicadas"):
        regresion.contrastar_publicados(resumen)
