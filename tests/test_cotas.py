"""Diagnóstico con información perfecta (IDEA §7): oráculo y cota superior por MILP."""

import itertools
from pathlib import Path
import random

import pandas as pd
import pytest

pytest.importorskip("scipy.optimize")

from src import cargar_caso  # noqa: E402
from src.controlador import _verificar_fisica  # noqa: E402
from src.cotas import optimo_informacion_perfecta, pronostico_perfecto, verificar_optimo  # noqa: E402
from src.simulador import simular  # noqa: E402


@pytest.fixture
def sistema():
    bateria = dict(id="b", capacidad_wh=100., minimo_wh=20., maximo_wh=100., inicial_wh=40.,
                   potencia_carga_max_w=30., potencia_descarga_max_w=25.,
                   eficiencia_carga=0.8, eficiencia_descarga=0.8, limites_potencia="bus")
    return dict(resolucion_h=1, potencia_bus_max_w=100.,
                pv=dict(potencia_nominal_w=100., inversor_max_w=100., factor_desempeno=1.), baterias=[bateria])


def _demanda(horas):
    """horas: lista de dicts {carga: (Wh, prioridad, W)} con la misma rejilla de cargas."""
    filas = [dict(t=t, carga_id=k, demanda_wh=d, prioridad_supuesta=p, potencia_w_supuesta=w, solicitada=int(d > 0))
             for t, hora in enumerate(horas) for k, (d, p, w) in hora.items()]
    return pd.DataFrame(filas)


def _solar(*ghi):
    return pd.DataFrame({"t": range(len(ghi)), "ghi_w_m2": ghi})


def test_optimo_intertemporal_supera_a_la_heuristica_horaria(sistema):
    # 16 Wh útiles (0.8 × (40 − 20)) alcanzan para una sola carga de 16 Wh, sin sol.
    demanda = _demanda([{"uno": (16., 1, 20.), "dos": (0., 2, 20.)},
                        {"uno": (0., 1, 20.), "dos": (16., 2, 20.)}])
    heuristica = simular(demanda, _solar(0., 0.), sistema)
    assert heuristica["resumen"]["J"] == pytest.approx(16.)  # gasta la batería en la prioridad 1
    optimo = optimo_informacion_perfecta(demanda, _solar(0., 0.), sistema)
    servicio = optimo["cargas"].set_index(["t", "carga_id"])["z"]
    assert servicio[(0, "uno")] == 0 and servicio[(1, "dos")] == 1
    assert optimo["resumen"]["J"] == pytest.approx(32.)
    assert optimo["solver"]["cota_superior"] >= optimo["resumen"]["J"] - 1e-9
    assert optimo["estado_final"]["b"] == pytest.approx(20.)
    assert verificar_optimo(optimo, sistema)["violaciones_fisicas"] == 0
    # El calendario óptimo es ejecutable con la física y el despacho de T03.
    replica = simular(demanda, _solar(0., 0.), sistema, compromisos=optimo["cargas"][["t", "carga_id", "x"]])
    assert replica["resumen"]["J"] == pytest.approx(32.)
    assert _verificar_fisica(replica, sistema)["violaciones_fisicas"] == 0


def test_condicion_terminal_y_terminal_imposible(sistema):
    demanda = _demanda([{"uno": (16., 1, 20.)}, {"uno": (16., 1, 20.)}])
    optimo = optimo_informacion_perfecta(demanda, _solar(0., 0.), sistema, terminal_minimo={"b": 40.})
    assert optimo["resumen"]["J"] == 0. and optimo["estado_final"]["b"] == pytest.approx(40.)
    assert verificar_optimo(optimo, sistema)["max_errores"]["b_terminal_wh"] <= 1e-6
    with pytest.raises(RuntimeError, match="MILP"):
        optimo_informacion_perfecta(demanda, _solar(0., 0.), sistema, terminal_minimo={"b": 60.})


def test_optimo_coincide_con_enumeracion_en_casos_pequenos(sistema):
    """Con una batería, el despacho de T03 maximiza el estado en cada hora: un calendario es
    factible si y solo si su re-simulación atiende todos los compromisos."""
    rng = random.Random(20261026)
    for _ in range(25):
        horas = [{k: (rng.choice([0., round(rng.uniform(1., 40.), 3)]), rng.choice([1, 2, 3]), 40.)
                  for k in ("a", "b", "c")} for _ in range(2)]
        demanda = _demanda(horas)
        solar = _solar(*(rng.choice([0., rng.uniform(0., 600.)]) for _ in range(2)))
        sistema["baterias"][0]["inicial_wh"] = rng.uniform(20., 100.)
        pedidas = demanda.index[demanda["solicitada"].eq(1)].tolist()
        mejor = 0.
        for r in range(len(pedidas) + 1):
            for sub in itertools.combinations(pedidas, r):
                plan = demanda[["t", "carga_id"]].assign(x=0)
                plan.loc[list(sub), "x"] = 1
                resultado = simular(demanda, solar, sistema, compromisos=plan)
                if (resultado["cargas"]["z"] == resultado["cargas"]["x"]).all():
                    mejor = max(mejor, resultado["resumen"]["J"])
        optimo = optimo_informacion_perfecta(demanda, solar, sistema)
        verificar_optimo(optimo, sistema)
        assert optimo["resumen"]["J"] == pytest.approx(mejor, abs=1e-6)


def test_verificacion_detecta_violaciones(sistema):
    demanda = _demanda([{"uno": (10., 1, 20.)}])
    optimo = optimo_informacion_perfecta(demanda, _solar(500.), sistema)
    optimo["baterias"].loc[0, "final_wh"] += 1.
    with pytest.raises(ValueError, match="IDEA"):
        verificar_optimo(optimo, sistema)


def test_pronostico_perfecto_repite_la_realizacion_en_todos_los_cuantiles():
    indice = pd.date_range("2025-03-03T05:00:00Z", periods=3, freq="h", name="inicio_utc")
    pronosticar = pronostico_perfecto(pd.Series([0., 120.5, 300.], index=indice), [0.1, 0.5, 0.9])
    tabla = pronosticar(None, pd.Timestamp("2025-03-03T01:00:00-05:00"), 2)
    assert tabla.index.equals(indice[1:]) and list(tabla.columns) == [0.1, 0.5, 0.9]
    assert (tabla.to_numpy() == [[120.5] * 3, [300.] * 3]).all()
    with pytest.raises(ValueError, match="oráculo"):
        pronosticar(None, indice[2], 2)


def test_dia_real_del_caso_acota_a_la_heuristica_y_es_ejecutable():
    caso = cargar_caso(Path(__file__).resolve().parents[1] / "scripts/configuracion.json")
    config = caso.configuracion
    horas = sorted(caso.demanda["t"].unique())[24 * 3:24 * 4]  # un jueves de desarrollo
    demanda = caso.demanda.loc[caso.demanda["t"].isin(horas)]
    solar = caso.irradiancia_simulacion.loc[caso.irradiancia_simulacion["t"].isin(horas), ["t", "ghi_w_m2"]]
    heuristica = simular(demanda, solar, config)
    optimo = optimo_informacion_perfecta(demanda, solar, config, terminal_minimo=heuristica["estado_final"])
    assert verificar_optimo(optimo, config)["violaciones_fisicas"] == 0
    assert optimo["resumen"]["J"] >= heuristica["resumen"]["J"] - 1e-6
    replica = simular(demanda, solar, config, compromisos=optimo["cargas"][["t", "carga_id", "x"]])
    assert _verificar_fisica(replica, config)["violaciones_fisicas"] == 0
