"""Casos manuales de la física y heurística de IDEA.md §4 (Wh, paso de 1 h)."""

from copy import deepcopy
import itertools
import math
from pathlib import Path
import random

import pandas as pd
import pytest

from src import cargar_caso
from src.simulador import (PRIORIDAD_CRITICA, _despachar, _energia_fotovoltaica, _limite_descarga,
                           _limites_reserva, simular)


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


@pytest.fixture
def sistema(bateria):
    return dict(resolucion_h=1, potencia_bus_max_w=100.,
                pv=dict(potencia_nominal_w=100., inversor_max_w=100., factor_desempeno=1.),
                baterias=[dict(bateria, inicial_wh=20.)])


def _demanda(*cargas, t=0):
    """Cada tupla contiene id, Wh solicitados, prioridad y potencia nominal W."""
    tabla = pd.DataFrame(cargas, columns=["carga_id", "demanda_wh", "prioridad_supuesta",
                                         "potencia_w_supuesta"])
    tabla["t"] = t
    tabla["solicitada"] = (tabla["demanda_wh"] > 0).astype(int)
    return tabla


def _solar(*ghi, t=0):
    return pd.DataFrame({"t": range(t, t + len(ghi)), "ghi_w_m2": ghi})


@pytest.mark.parametrize("cargas,elegida", [
    ([("a", 5., 2, 5.), ("b", 10., 2, 10.), ("c", 10., 1, 10.)], "b"),
    ([("b", 10., 2, 10.), ("a", 10., 2, 10.)], "a"),
    ([("a", 10., 1, 10.), ("b", 5., 2, 5.)], "b"),
])
def test_orden_por_prioridad_demanda_e_identificador(sistema, cargas, elegida):
    resultado = simular(_demanda(*cargas), _solar(100.), sistema)
    detalle = resultado["cargas"]
    assert detalle.loc[detalle["z"] == 1, "carga_id"].tolist() == [elegida]
    assert detalle["x"].equals(detalle["z"])


def test_rechazar_una_carga_no_gasta_bateria_ni_detiene_la_seleccion(sistema):
    sistema["baterias"][0]["inicial_wh"] = 60.
    demanda = _demanda(("grande", 30., 3, 30.), ("menor", 20., 2, 20.))
    resultado = simular(demanda, _solar(0.), sistema)
    assert resultado["cargas"].set_index("carga_id")["z"].to_dict() == {"grande": 0, "menor": 1}
    assert resultado["baterias"]["descarga_wh"].tolist() == [20.]
    assert resultado["estado_final"] == pytest.approx({"b": 35.})


@pytest.mark.parametrize("limite", ["inversor", "bus"])
def test_limite_nominal_es_distinto_de_la_energia_horaria(sistema, limite):
    if limite == "inversor":
        sistema["pv"]["inversor_max_w"] = 50.
    else:
        sistema["potencia_bus_max_w"] = 50.
    demanda = _demanda(("a", 10., 2, 60.), ("b", 5., 1, 40.))
    resultado = simular(demanda, _solar(1000.), sistema)
    assert resultado["cargas"].set_index("carga_id")["z"].to_dict() == {"a": 0, "b": 1}
    assert resultado["horas"]["potencia_atendida_w"].tolist() == [40.]


def _servicio(resultado):
    return resultado["cargas"].set_index("carga_id")["z"].to_dict()


def test_mochila_en_hora_limitada_por_potencia(sistema):
    # 100 W y 100 Wh FV: sobra energía, limita la potencia. Beneficio p·D entre paréntesis.
    demanda = _demanda(("a", 5., 3, 10.), ("l", 15., 2, 60.), ("tv", 10., 2, 25.),
                       *((f"v{i}", 11., 1, 12.) for i in range(1, 6)))
    prioridad = simular(demanda, _solar(1000.), sistema)
    # Prioridad: a (15), l (30) y tv (20) ocupan 95 W; ningún ventilador cabe. J = 65.
    assert _servicio(prioridad) == {"a": 1, "l": 1, "tv": 1, **{f"v{i}": 0 for i in range(1, 6)}}
    assert prioridad["resumen"]["J"] == pytest.approx(65.)
    assert simular(demanda, _solar(1000.), dict(sistema, seleccion_cargas="prioridad"))["cargas"].equals(
        prioridad["cargas"])
    # Mochila: la crítica primero; en 90 W, tv + 5 ventiladores (75) supera a l + tv (50).
    mochila = simular(demanda, _solar(1000.), dict(sistema, seleccion_cargas="mochila"))
    assert _servicio(mochila) == {"a": 1, "l": 0, "tv": 1, **{f"v{i}": 1 for i in range(1, 6)}}
    assert mochila["resumen"]["J"] == pytest.approx(90.)
    assert mochila["horas"]["potencia_atendida_w"].tolist() == [95.]
    assert mochila["horas"]["atendida_wh"].tolist() == [70.]
    assert mochila["cargas"]["x"].equals(mochila["cargas"]["z"])


def test_mochila_no_cambia_horas_sin_limite_de_potencia(sistema):
    sistema["baterias"][0]["inicial_wh"] = 60.
    demanda = pd.concat([_demanda(("grande", 30., 3, 30.), ("menor", 20., 2, 20.), ("otra", 4., 1, 5.)),
                         _demanda(("grande", 10., 3, 30.), ("menor", 0., 2, 20.), ("otra", 4., 1, 5.), t=1)])
    prioridad = simular(demanda, _solar(0., 30.), sistema)
    mochila = simular(demanda, _solar(0., 30.), dict(sistema, seleccion_cargas="mochila"))
    for tabla in ("cargas", "horas", "baterias"):
        assert mochila[tabla].equals(prioridad[tabla])
    assert mochila["estado_final"] == prioridad["estado_final"]


def _mejor_por_enumeracion(cargas, fv_wh, sistema, estado, reserva, criticas):
    """Fuerza bruta independiente: críticas dadas, luego el mejor subconjunto factible."""
    baterias = sistema["baterias"]
    potencia_max = min(sistema["potencia_bus_max_w"], sistema["pv"]["inversor_max_w"])
    e0 = [c["demanda_wh"] for c in criticas]
    p0 = [c["potencia_w_supuesta"] for c in criticas]
    no_criticas = [c for c in cargas if c["prioridad_supuesta"] < PRIORIDAD_CRITICA]
    if reserva and no_criticas:
        disponible = math.fsum([fv_wh, *_limites_reserva(baterias, estado, fv_wh, math.fsum(e0), reserva).values()])
    else:
        disponible = math.fsum([fv_wh, *(_limite_descarga(b, estado[b["id"]]) for b in baterias)])
    mejor = 0.
    elegibles = [c for c in no_criticas if c["demanda_wh"] > 0]
    for r in range(len(elegibles) + 1):
        for sub in itertools.combinations(elegibles, r):
            if (math.fsum([*e0, *(c["demanda_wh"] for c in sub)]) <= disponible
                    and math.fsum([*p0, *(c["potencia_w_supuesta"] for c in sub)]) <= potencia_max):
                mejor = max(mejor, math.fsum(c["prioridad_supuesta"] * c["demanda_wh"] for c in sub))
    return mejor + math.fsum(c["prioridad_supuesta"] * c["demanda_wh"] for c in criticas)


def test_mochila_coincide_con_fuerza_bruta_y_nunca_empeora_la_hora(sistema):
    rng = random.Random(20261025)
    sistema["baterias"].append(dict(sistema["baterias"][0], id="c", potencia_descarga_max_w=15.))
    limitadas = 0
    for _ in range(150):
        cargas = []
        for i in range(rng.randint(2, 8)):
            potencia = rng.choice([5., 10., 20., 40., 60.])
            demanda = rng.choice([0., round(rng.uniform(0.5, potencia), 3)])
            cargas.append((f"c{i}", demanda, rng.choice([1, 1, 2, 2, 3]), potencia))
        demanda = _demanda(*cargas)
        estado = {"b": rng.uniform(20., 100.), "c": rng.uniform(20., 100.)}
        ghi, reserva = rng.choice([0., rng.uniform(0., 1000.)]), rng.choice([0, 0, 0.25, 0.5])
        prioridad = simular(demanda, _solar(ghi), sistema, estado_inicial=estado, reserva=reserva)
        mochila = simular(demanda, _solar(ghi), dict(sistema, seleccion_cargas="mochila"),
                          estado_inicial=estado, reserva=reserva)
        detalle_p, detalle_m = (r["cargas"].set_index("carga_id") for r in (prioridad, mochila))
        es_critica = detalle_m["prioridad_supuesta"] >= PRIORIDAD_CRITICA
        assert detalle_m.loc[es_critica, "z"].equals(detalle_p.loc[es_critica, "z"])
        assert mochila["resumen"]["J"] >= prioridad["resumen"]["J"] - 1e-9
        if sum(p for _, d, _, p in cargas if d > 0) > 100.:
            limitadas += 1
            criticas = detalle_m.loc[es_critica & detalle_m["z"].eq(1)].reset_index().to_dict("records")
            esperado = _mejor_por_enumeracion(demanda.to_dict("records"), _energia_fotovoltaica(ghi, sistema["pv"]),
                                              sistema, estado, reserva, criticas)
            assert mochila["resumen"]["J"] == pytest.approx(esperado, abs=1e-9)
        else:
            assert mochila["cargas"].equals(prioridad["cargas"])
    assert limitadas >= 20


def test_seleccion_desconocida_se_rechaza(sistema):
    with pytest.raises(ValueError, match="seleccion_cargas"):
        simular(_demanda(("a", 5., 1, 5.)), _solar(0.), dict(sistema, seleccion_cargas="voraz"))


def test_no_se_amplia_el_presupuesto_con_la_tolerancia(sistema):
    sistema["baterias"][0]["inicial_wh"] = 60.
    demanda = _demanda(("a", math.nextafter(25., math.inf), 1, 30.))
    resultado = simular(demanda, _solar(0.), sistema)
    assert resultado["cargas"]["z"].tolist() == [0]
    assert resultado["baterias"]["descarga_wh"].tolist() == [0.]


def test_compromiso_fijo_con_deficit_y_sin_sustituciones(sistema):
    demanda = _demanda(("grande", 20., 3, 20.), ("pequena", 5., 1, 5.))
    plan = demanda[["t", "carga_id"]].assign(x=[1, 0]).iloc[::-1]
    resultado = simular(demanda, _solar(100.), sistema, compromisos=plan)
    detalle = resultado["cargas"].set_index("carga_id")
    assert detalle["x"].to_dict() == {"grande": 1, "pequena": 0}
    assert detalle["z"].to_dict() == {"grande": 0, "pequena": 0}
    assert detalle["comprometida_wh"].sum() == 20.
    assert detalle["atendida_wh"].sum() == 0.
    assert detalle["no_atendida_wh"].sum() == 25.
    assert resultado["resumen"]["J"] == 0.
    assert resultado["resumen"]["S_pond"] == 0.


def test_baterias_heterogeneas_no_se_sustituyen_por_capacidad_agregada(sistema):
    bateria = sistema["baterias"][0]
    sistema["baterias"] = [dict(bateria, id="a", inicial_wh=100., potencia_descarga_max_w=5.),
                           dict(bateria, id="b", inicial_wh=20., potencia_descarga_max_w=50.)]
    demanda = _demanda(("a", 6., 2, 6.), ("b", 5., 1, 5.))
    resultado = simular(demanda, _solar(0.), sistema)
    assert resultado["cargas"]["z"].tolist() == [0, 1]
    assert resultado["baterias"]["descarga_wh"].tolist() == [5., 0.]


def test_beneficio_ponderado_y_solicitud_nula(sistema):
    demanda = _demanda(("a", 10., 3, 10.), ("b", 5., 1, 5.))
    resultado = simular(demanda, _solar(100.), sistema)
    assert resultado["resumen"]["J"] == 30.
    assert resultado["resumen"]["S_pond"] == pytest.approx(30 / 35)
    nulo = simular(_demanda(("a", 0., 1, 10.)), _solar(1000.), sistema)
    assert nulo["resumen"]["J"] == 0.
    assert nulo["resumen"]["S_pond"] is None
    assert nulo["cargas"]["z"].tolist() == [0]


def test_servicio_completo_fraccionario_no_produce_energia_negativa(sistema):
    demanda = _demanda(("a", 0.9, 1, 1.), ("b", 0.2, 1, 1.), ("c", 0.1, 1, 1.))
    resultado = simular(demanda, _solar(12.), sistema)
    assert resultado["cargas"]["z"].tolist() == [1, 1, 1]
    assert resultado["horas"]["no_atendida_wh"].tolist() == [0.]
    assert resultado["resumen"]["no_atendida_wh"] == 0.
    assert resultado["resumen"]["atendida_wh"] <= resultado["resumen"]["solicitada_wh"]


def test_continuidad_entre_horizontes_y_entradas_sin_mutacion(sistema):
    demanda = pd.concat([_demanda(("a", energia, 1, 40.), t=t)
                         for t, energia in enumerate([10., 20., 10., 40.])], ignore_index=True)
    demanda = demanda.sample(frac=1, random_state=4)
    solar = _solar(500., 0., 500., 0.).iloc[::-1]
    plan = demanda[["t", "carga_id"]].assign(x=1)
    estado = {"b": 20.}
    antes = [tabla.copy(deep=True) for tabla in (demanda, solar, plan)]
    config_antes, estado_antes = deepcopy(sistema), dict(estado)
    completo = simular(demanda, solar, sistema, estado_inicial=estado, compromisos=plan)
    primero = simular(demanda[demanda.t < 2], solar[solar.t < 2], sistema,
                      estado_inicial=estado, compromisos=plan[plan.t < 2])
    segundo = simular(demanda[demanda.t >= 2], solar[solar.t >= 2], sistema,
                      estado_inicial=primero["estado_final"], compromisos=plan[plan.t >= 2])
    for nombre in ("cargas", "horas", "baterias"):
        unidos = pd.concat([primero[nombre], segundo[nombre]], ignore_index=True)
        pd.testing.assert_frame_equal(completo[nombre].reset_index(drop=True), unidos)
    assert completo["estado_final"] == segundo["estado_final"]
    for tabla, original in zip((demanda, solar, plan), antes):
        pd.testing.assert_frame_equal(tabla, original)
    assert sistema == config_antes and estado == estado_antes


@pytest.mark.parametrize("tabla,campo,valor", [
    ("demanda", "demanda_wh", -1.), ("demanda", "demanda_wh", float("nan")),
    ("demanda", "demanda_wh", True), ("demanda", "prioridad_supuesta", 1.5),
    ("demanda", "prioridad_supuesta", 0), ("demanda", "potencia_w_supuesta", float("inf")),
    ("demanda", "potencia_w_supuesta", 0.), ("demanda", "solicitada", 2),
    ("demanda", "solicitada", 0), ("demanda", "carga_id", ""),
    ("demanda", "t", 0.5), ("solar", "t", -1),
    ("solar", "ghi_w_m2", -1.), ("solar", "ghi_w_m2", float("inf")),
])
def test_rechaza_numeros_y_claves_invalidos(sistema, tabla, campo, valor):
    demanda, solar = _demanda(("a", 10., 1, 10.)), _solar(0.)
    alterada = demanda if tabla == "demanda" else solar
    alterada[campo] = alterada[campo].astype(object)
    alterada.loc[0, campo] = valor
    with pytest.raises(ValueError, match=campo):
        simular(demanda, solar, sistema)


@pytest.mark.parametrize("cambio", ["duplicado_demanda", "duplicado_solar", "falta_hora",
                                  "salto", "falta_carga", "sin_columna", "vacia"])
def test_rechaza_cobertura_incompleta_o_ambigua(sistema, cambio):
    demanda = pd.concat([_demanda(("a", 10., 1, 10.), ("b", 5., 1, 5.), t=t)
                         for t in range(2)], ignore_index=True)
    solar = _solar(0., 0.)
    if cambio == "duplicado_demanda":
        demanda = pd.concat([demanda, demanda.iloc[:1]])
    elif cambio == "duplicado_solar":
        solar = pd.concat([solar, solar.iloc[:1]])
    elif cambio == "falta_hora":
        solar = solar.iloc[:1]
    elif cambio == "salto":
        demanda.loc[demanda.t == 1, "t"] = 2
        solar.loc[1, "t"] = 2
    elif cambio == "falta_carga":
        demanda = demanda.iloc[:-1]
    elif cambio == "sin_columna":
        demanda = demanda.drop(columns="demanda_wh")
    else:
        demanda = demanda.iloc[:0]
    with pytest.raises(ValueError):
        simular(demanda, solar, sistema)


@pytest.mark.parametrize("estado", [{}, {"otra": 20.}, {"b": 19.}, {"b": 101.},
                                   {"b": float("nan")}, {"b": True}])
def test_rechaza_estados_iniciales_invalidos(sistema, estado):
    with pytest.raises(ValueError):
        simular(_demanda(("a", 10., 1, 10.)), _solar(0.), sistema, estado_inicial=estado)


@pytest.mark.parametrize("cambio", ["faltante", "duplicado", "ajeno", "fraccion", "booleano", "sin_solicitud"])
def test_rechaza_compromisos_invalidos(sistema, cambio):
    demanda = _demanda(("a", 10., 1, 10.), ("b", 0., 1, 10.))
    plan = demanda[["t", "carga_id"]].assign(x=[1, 0])
    if cambio == "faltante":
        plan = plan.iloc[:1]
    elif cambio == "duplicado":
        plan = pd.concat([plan, plan.iloc[:1]])
    elif cambio == "ajeno":
        plan.loc[0, "carga_id"] = "otra"
    else:
        plan["x"] = plan["x"].astype(object)
        plan.loc[0 if cambio != "sin_solicitud" else 1, "x"] = {
            "fraccion": 0.5, "booleano": True, "sin_solicitud": 1}[cambio]
    with pytest.raises(ValueError):
        simular(demanda, _solar(0.), sistema, compromisos=plan)


@pytest.mark.parametrize("seleccion", ["prioridad", "mochila"])
def test_desarrollo_completo_conserva_todos_los_limites_y_balances(seleccion):
    caso = cargar_caso(Path(__file__).resolve().parents[1] / "scripts/configuracion.json")
    caso.configuracion["seleccion_cargas"] = seleccion
    demanda = caso.demanda.loc[caso.demanda.particion == "desarrollo"]
    solar = caso.irradiancia_simulacion.loc[caso.irradiancia_simulacion.particion == "desarrollo"]
    resultado = simular(demanda, solar, caso.configuracion)
    cargas, horas, flujos = (resultado[n] for n in ("cargas", "horas", "baterias"))
    assert len(horas) == 1344
    assert cargas["z"].isin([0, 1]).all() and cargas["x"].isin([0, 1]).all()
    assert (cargas["z"] <= cargas["x"]).all() and (cargas["x"] <= cargas["solicitada"]).all()
    assert (cargas["atendida_wh"] == cargas["demanda_wh"] * cargas["z"]).all()
    nominal = (cargas["potencia_w_supuesta"] * cargas["z"]).groupby(cargas["t"]).sum()
    assert (nominal <= min(caso.configuracion["pv"]["inversor_max_w"],
                          caso.configuracion["potencia_bus_max_w"])).all()
    neto = flujos.groupby("t")[["carga_wh", "descarga_wh"]].sum()
    balance = horas.set_index("t").eval("fv_wh - atendida_wh - vertido_wh")
    assert (balance + neto.descarga_wh - neto.carga_wh).abs().max() <= 1e-6
    assert (horas["vertido_wh"] >= 0).all()
    for bateria in caso.configuracion["baterias"]:
        b = flujos.loc[flujos.bateria_id == bateria["id"]].reset_index(drop=True)
        assert b["inicial_wh"].iloc[0] == bateria["inicial_wh"]
        assert b["inicial_wh"].iloc[1:].tolist() == b["final_wh"].iloc[:-1].tolist()
        assert b[["inicial_wh", "final_wh"]].ge(bateria["minimo_wh"]).all().all()
        assert b[["inicial_wh", "final_wh"]].le(bateria["maximo_wh"]).all().all()
        assert (b[["carga_wh", "descarga_wh"]] >= 0).all().all()
        assert (b.carga_wh * b.descarga_wh == 0).all()
        assert (b.descarga_wh <= bateria["potencia_descarga_max_w"]).all()
        assert (b.carga_wh <= bateria["potencia_carga_max_w"]).all()
        assert (b.descarga_wh <= bateria["eficiencia_descarga"] *
                (b.inicial_wh - bateria["minimo_wh"])).all()
        assert (b.carga_wh <= (bateria["maximo_wh"] - b.inicial_wh) /
                bateria["eficiencia_carga"]).all()
        error = b.final_wh - (b.inicial_wh + bateria["eficiencia_carga"] * b.carga_wh -
                             b.descarga_wh / bateria["eficiencia_descarga"])
        assert error.abs().max() <= 1e-6
        assert resultado["estado_final"][bateria["id"]] == b["final_wh"].iloc[-1]
    beneficio = (cargas.prioridad_supuesta * cargas.atendida_wh).sum()
    solicitado = (cargas.prioridad_supuesta * cargas.demanda_wh).sum()
    assert resultado["resumen"]["J"] == pytest.approx(beneficio)
    assert resultado["resumen"]["S_pond"] == pytest.approx(beneficio / solicitado)
