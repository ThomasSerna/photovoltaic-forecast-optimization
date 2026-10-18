"""Casos manuales de la física y heurística de IDEA.md §4 (Wh, paso de 1 h)."""

from copy import deepcopy
import math

import pytest

from energy_project.simulador import _despachar, _energia_fotovoltaica, _limite_descarga


@pytest.fixture
def bateria():
    return dict(id="b", capacidad_wh=100., minimo_wh=20., maximo_wh=100., inicial_wh=60.,
                potencia_carga_max_w=30., potencia_descarga_max_w=25.,
                eficiencia_carga=0.8, eficiencia_descarga=0.8, limites_potencia="bus")


@pytest.mark.parametrize(("ghi", "esperado"), [(0., 0.), (1000., 1700.), (1500., 2000.)])
def test_fotovoltaica(ghi, esperado):
    pv = dict(potencia_nominal_w=2000., inversor_max_w=2000., factor_desempeno=0.85)
    assert _energia_fotovoltaica(ghi, pv) == pytest.approx(esperado)


def test_descarga_y_eficiencia(bateria):
    # 60 Wh iniciales, 20 Wh mínimos: 32 Wh útiles en bus, limitados a 25 Wh/h.
    assert _limite_descarga(bateria, 60.) == 25.
    flujos, estado, vertido = _despachar(0., 24., [bateria], {"b": 60.})
    assert flujos[0]["descarga_wh"] == 24.
    assert estado["b"] == pytest.approx(30.)  # 60 - 24/0.8
    assert flujos[0]["carga_wh"] == vertido == 0.


def test_excedente_carga_y_vertido(bateria):
    flujos, estado, vertido = _despachar(100., 20., [bateria], {"b": 20.})
    assert flujos[0]["carga_wh"] == 30.
    assert estado["b"] == pytest.approx(44.)  # 20 + 0.8*30
    assert flujos[0]["descarga_wh"] == 0.
    assert vertido == 50.


@pytest.mark.parametrize("inicial", [20., 100.])
def test_bateria_en_limites_y_sin_mutaciones(bateria, inicial):
    estado = {"b": inicial}
    antes = deepcopy(bateria)
    flujos, siguiente, vertido = _despachar(10., 0., [bateria], estado)
    assert siguiente["b"] == pytest.approx(min(100., inicial + 8.))
    assert vertido == (10. if inicial == 100. else 0.)
    assert flujos[0]["descarga_wh"] == 0.
    assert estado == {"b": inicial} and bateria == antes
    assert _limite_descarga(bateria, 20.) == 0.


def test_extremos_con_eficiencias_no_exactas(bateria):
    bateria.update(eficiencia_carga=0.95, eficiencia_descarga=0.95,
                   potencia_carga_max_w=1000., potencia_descarga_max_w=1000.)
    _, lleno, _ = _despachar(1000., 0., [bateria], {"b": 60.1})
    assert lleno["b"] == 100.
    _, minimo, _ = _despachar(0., _limite_descarga(bateria, 100.), [bateria], lleno)
    assert minimo["b"] == 20.
    # El estado producido es admisible directamente en el próximo horizonte.
    _, nuevo, _ = _despachar(0., 0., [bateria], minimo)
    assert nuevo == minimo


def test_flujos_pequenos_no_desplazan_el_estado_fuera_de_limites(bateria):
    bateria.update(minimo_wh=0.1, maximo_wh=0.7, eficiencia_carga=0.9,
                   eficiencia_descarga=0.9)
    _, estado, _ = _despachar(1e-18, 0., [bateria], {"b": 0.1})
    assert estado["b"] == 0.1
    _, estado, _ = _despachar(0., 1e-18, [bateria], {"b": 0.7})
    assert estado["b"] == 0.7


def test_carga_casi_saturada_conserva_cota_sin_recortar(bateria):
    bateria.update(maximo_wh=778.9883483646762, capacidad_wh=800.,
                   eficiencia_carga=0.5654622291396864, potencia_carga_max_w=1000.)
    inicial = 257.02310190987595
    hueco = (bateria["maximo_wh"] - inicial) / bateria["eficiencia_carga"]
    _, estado, _ = _despachar(math.nextafter(hueco, 0.), 0., [bateria], {"b": inicial})
    assert estado["b"] <= bateria["maximo_wh"]


def test_limites_individuales_y_orden_determinista(bateria):
    otra = dict(bateria, id="a", potencia_descarga_max_w=5.)
    flujos, estado, _ = _despachar(0., 15., [bateria, otra], {"a": 100., "b": 60.})
    assert [f["bateria_id"] for f in flujos] == ["a", "b"]
    assert [f["descarga_wh"] for f in flujos] == [5., 10.]
    assert estado == pytest.approx({"a": 93.75, "b": 47.5})
    # Mucha energía almacenada no permite violar las potencias de descarga.
    with pytest.raises(ValueError, match="balance del bus"):
        _despachar(0., 31., [bateria, otra], {"a": 100., "b": 100.})
