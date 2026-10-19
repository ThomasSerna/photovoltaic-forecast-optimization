"""Reservas operativas de IDEA.md §6; no modifican los mínimos físicos de T03."""

from copy import deepcopy

import pandas as pd
import pytest

from src.simulador import simular


def _bateria(id_="a", **cambios):
    bateria = dict(id=id_, capacidad_wh=10., minimo_wh=0., maximo_wh=10., inicial_wh=10.,
                   potencia_carga_max_w=10., potencia_descarga_max_w=10.,
                   eficiencia_carga=0.8, eficiencia_descarga=1., limites_potencia="bus")
    return dict(bateria, **cambios)


def _caso(*cargas, baterias=None, fv_wh=0.):
    demanda = pd.DataFrame(cargas, columns=["carga_id", "demanda_wh", "prioridad_supuesta"])
    demanda = demanda.assign(t=0, solicitada=lambda d: (d.demanda_wh > 0).astype(int),
                             potencia_w_supuesta=lambda d: d.demanda_wh)
    solar = pd.DataFrame({"t": [0], "ghi_w_m2": [fv_wh]})
    sistema = dict(resolucion_h=1, potencia_bus_max_w=1000.,
                   pv=dict(potencia_nominal_w=1000., inversor_max_w=1000., factor_desempeno=1.),
                   baterias=baterias or [_bateria()])
    return demanda, solar, sistema


def test_reserva_cero_conserva_resultado_y_no_modifica_entradas():
    demanda, solar, sistema = _caso(("critica", 3., 3), ("otra", 5., 1))
    antes = deepcopy(sistema)
    normal = simular(demanda, solar, sistema)
    cero = simular(demanda, solar, sistema, reserva=0.)
    for nombre in ("cargas", "horas", "baterias"):
        pd.testing.assert_frame_equal(normal[nombre], cero[nombre])
    assert normal["resumen"] == cero["resumen"]
    assert normal["estado_final"] == cero["estado_final"] == {"a": 2.}
    assert sistema == antes


def test_reserva_individual_y_potencia_de_baterias_heterogeneas():
    # A está en su reserva (6 Wh); B tiene 8 Wh útiles sobre ella, limitados a 4 Wh/h.
    baterias = [_bateria(minimo_wh=2., inicial_wh=6., eficiencia_descarga=0.5,
                          potencia_descarga_max_w=4.),
                _bateria("b", capacidad_wh=30., minimo_wh=10., maximo_wh=30., inicial_wh=30.,
                          eficiencia_descarga=0.8, potencia_descarga_max_w=4.)]
    caso = _caso(("grande", 5., 2), ("menor", 4., 1), baterias=baterias)
    resultado = simular(*caso, reserva=0.5)
    assert resultado["cargas"].set_index("carga_id")["x"].to_dict() == {"grande": 0, "menor": 1}
    assert resultado["baterias"]["descarga_wh"].tolist() == [0., 4.]
    assert resultado["estado_final"] == pytest.approx({"a": 6., "b": 25.})


def test_criticas_usan_reserva_sin_financiar_descargas_no_criticas():
    # A entrega 3 Wh críticos y queda bajo su reserva; B cubre los 4 Wh no críticos.
    baterias = [_bateria(minimo_wh=2., eficiencia_descarga=0.5, potencia_descarga_max_w=4.),
                _bateria("b", capacidad_wh=30., minimo_wh=10., maximo_wh=30., inicial_wh=30.,
                          eficiencia_descarga=0.8, potencia_descarga_max_w=4.)]
    caso = _caso(("critica", 3., 3), ("otra", 4., 2), baterias=baterias)
    resultado = simular(*caso, reserva=0.5)
    assert resultado["cargas"]["x"].tolist() == [1, 1]
    assert resultado["baterias"]["descarga_wh"].tolist() == [3., 4.]
    assert resultado["estado_final"] == pytest.approx({"a": 4., "b": 25.})
    assert resultado["resumen"]["atendida_wh"] == 7.


def test_estado_bajo_reserva_sigue_sirviendo_criticas_y_cargando():
    caso = _caso(("critica", 2., 4), ("otra", 1., 1), baterias=[_bateria(inicial_wh=2.)])
    resultado = simular(*caso, reserva=0.75)
    assert resultado["cargas"].set_index("carga_id")["x"].to_dict() == {"critica": 1, "otra": 0}
    assert resultado["estado_final"] == {"a": 0.}
    demanda, _, sistema = _caso(("otra", 1., 1))
    carga = simular(demanda, pd.DataFrame({"t": [0], "ghi_w_m2": [3.]}), sistema,
                    estado_inicial=resultado["estado_final"], reserva=0.75)
    assert carga["estado_final"] == pytest.approx({"a": 1.6})
    assert carga["baterias"]["carga_wh"].tolist() == [2.]


def test_reserva_completa_permite_solar_sin_descargar_bateria():
    resultado = simular(*_caso(("otra", 2., 1), fv_wh=2.), reserva=1)
    assert resultado["cargas"]["x"].tolist() == [1]
    assert resultado["estado_final"] == {"a": 10.}
    assert resultado["baterias"]["descarga_wh"].tolist() == [0.]


@pytest.mark.parametrize("reserva", [-0.1, 1.1, float("nan"), float("inf"), True, "0.5", None])
def test_rechaza_reservas_invalidas(reserva):
    with pytest.raises(ValueError, match="reserva"):
        simular(*_caso(("otra", 1., 1)), reserva=reserva)


def test_reserva_no_se_reaplica_a_compromisos_fijos():
    demanda, solar, sistema = _caso(("otra", 10., 1))
    plan = demanda[["t", "carga_id"]].assign(x=1)
    with pytest.raises(ValueError, match="compromisos"):
        simular(demanda, solar, sistema, compromisos=plan, reserva=0.5)
    ejecutado = simular(demanda, solar, sistema, compromisos=plan, reserva=0)
    assert ejecutado["cargas"]["z"].tolist() == [1]
    assert ejecutado["estado_final"] == {"a": 0.}
