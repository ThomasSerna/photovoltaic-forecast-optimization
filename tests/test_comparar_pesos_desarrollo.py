"""Calibración orientada al servicio: selección y validación comprobables a mano."""

from pathlib import Path
import sys

import pandas as pd
import pytest

pytest.importorskip("astral")
pytest.importorskip("matplotlib")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `scripts` no es un paquete instalado

from scripts.comparar_pesos_desarrollo import cotejar_t07, seleccionar_servicio
from src.controlador import _validar_riesgo


def _tabla(filas):
    return pd.DataFrame([dict(proveedor=p, modo_mpc="cvar", lambda_riesgo=peso, J=j, CVaR_diario=cvar)
                         for p, peso, j, cvar in filas])


def test_seleccion_usa_el_j_medio_entre_proveedores():
    # Chronos pierde con peso 1, pero el promedio sube: (100 + 90) / 2 < (120 + 80) / 2.
    ajuste = _tabla([("beta", 0., 100., 1.), ("chronos", 0., 90., 1.),
                     ("beta", 1., 120., 1.), ("chronos", 1., 80., 1.)])
    assert seleccionar_servicio(ajuste) == 1.


def test_seleccion_elige_el_menor_peso_si_empatan():
    ajuste = _tabla([("beta", 0., 100., 1.), ("chronos", 0., 90., 1.),
                     ("beta", 1., 110., 1.), ("chronos", 1., 80., 1.)])
    assert seleccionar_servicio(ajuste) == 0.


def test_cotejo_detecta_diferencias_con_las_cifras_de_t07():
    ajuste = _tabla([("beta", 0., 447641.358, 3083.645), ("chronos", 0., 447641.358, 3083.645),
                     ("beta", 1., 447688.355, 3103.842), ("chronos", 1., 447688.355, 3103.842)])
    validacion = _tabla([("beta", 0., 150935.735, 2809.385), ("chronos", 0., 150794.594, 2910.200)])
    assert all(fila["coincide"] for fila in cotejar_t07(ajuste, validacion).values())
    validacion.loc[0, "J"] += .01
    assert not cotejar_t07(ajuste, validacion)["J_validacion_beta"]["coincide"]


@pytest.mark.parametrize("j_beta, cvar_beta, servicio, cola", [
    (99., 10., True, True),  # conserva el 99 % y el CVaR medio no sube
    (98.9, 10., False, True),  # beta pierde más del 1 %
    (100., 12., True, False),  # el CVaR medio sube de 10 a 11
])
def test_validacion_exige_servicio_y_cola_en_ambos_proveedores(j_beta, cvar_beta, servicio, cola):
    validacion = _tabla([("beta", 0., 100., 10.), ("chronos", 0., 100., 10.),
                         ("beta", 1., j_beta, cvar_beta), ("chronos", 1., 100., 10.)])
    veredicto = _validar_riesgo(validacion, 1.)
    assert veredicto == dict(servicio_admisible=servicio, cola_no_empeora=cola, recomendado=servicio and cola)
