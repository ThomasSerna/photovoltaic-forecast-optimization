"""Calibración del riesgo: selección temporal, cola realizada y continuidad física."""

from copy import deepcopy
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

from src import controlador
from test_controlador import ORIGEN, _caso, _cuantiles


def _tabla(filas):
    return pd.DataFrame([dict(proveedor=n, modo_mpc="cvar", lambda_riesgo=peso,
                              J=servicios[i], CVaR_diario=colas[i])
                         for peso, servicios, colas in filas for i, n in enumerate(("beta", "chronos"))])


def test_seleccion_respeta_99_por_ciento_en_cada_rama_y_desempates():
    tabla = _tabla([(0., (100., 100.), (10., 10.)),
                    (.25, (98.999, 200.), (0., 0.)),
                    (.5, (99., 100.), (5., 5.)),
                    (1., (100., 100.), (5., 5.)),
                    (2., (100., 100.), (5., 5.))])
    elegido, seleccion = controlador._seleccionar_riesgo(tabla)
    assert elegido == 1.
    assert not seleccion.set_index("lambda_riesgo").loc[.25, "elegible"]
    assert seleccion.set_index("lambda_riesgo").loc[.5, "elegible"]


def test_seleccion_con_j_cero_y_empate_elige_cero():
    tabla = _tabla([(0., (0., 0.), (0., 0.)), (1., (0., 0.), (0., 0.))])
    elegido, seleccion = controlador._seleccionar_riesgo(tabla)
    assert elegido == 0.
    assert seleccion.elegible.all()


@pytest.mark.parametrize("servicios,colas,esperado", [
    ((99., 100.), (9., 11.), True),
    ((98.9, 100.), (0., 0.), False),
    ((100., 100.), (10., 10.01), False),
])
def test_validacion_no_reselecciona_y_aplica_criterio_conjunto(servicios, colas, esperado):
    tabla = _tabla([(0., (100., 100.), (10., 10.)), (1., servicios, colas)])
    assert controlador._validar_riesgo(tabla, 1.)["recomendado"] is esperado


def test_perdida_diaria_usa_prioridad_y_fecha_bogota():
    cargas = pd.DataFrame(dict(t=[0, 1], prioridad_supuesta=[1, 3], demanda_wh=[2., 4.],
                               atendida_wh=[1., 2.], no_atendida_wh=[1., 2.]))
    reloj = pd.DataFrame(dict(t=[0, 1], inicio_utc=pd.to_datetime(
        ["2025-01-07T04:00:00Z", "2025-01-07T05:00:00Z"])))
    diario = controlador._diario_riesgo(dict(cargas=cargas), reloj, "America/Bogota")
    assert diario.fecha.tolist() == ["2025-01-06", "2025-01-07"]
    assert diario.perdida.tolist() == [1., 6.]
    assert diario.J.tolist() == [1., 6.]
    assert diario.solicitada_critica_wh.tolist() == [0., 4.]


def test_cvar_diario_incluye_masa_fraccionaria():
    assert controlador.calcular_cvar([10., 8.] + [0.] * 12, .9) == pytest.approx((10 + .4 * 8) / 1.4)


@pytest.fixture
def campana(monkeypatch):
    caso = _caso(real=(0., 0., 0., 0.), prioridades=(1, 3, 1, 3))
    corte, fin = ORIGEN + pd.Timedelta(hours=2), ORIGEN + pd.Timedelta(hours=4)
    caso.configuracion["periodos"] = {"desarrollo": dict(inicio=ORIGEN, fin=fin)}
    monkeypatch.setattr(controlador, "RIESGO_INICIO", ORIGEN)
    monkeypatch.setattr(controlador, "RIESGO_CORTE", corte)
    monkeypatch.setattr(controlador, "RIESGO_FIN", fin)
    llamadas = []

    def proveedores(caso, pipeline):
        def pronosticar(n, historia, origen, horizonte):
            assert len(historia) == 336 and (historia.fin_utc <= origen).all()
            llamadas.append((n, origen, horizonte))
            return _cuantiles([0.] * horizonte, origen)
        return {n: lambda h, o, H, nombre=n: pronosticar(nombre, h, o, H) for n in ("beta", "chronos")}

    monkeypatch.setattr(controlador, "proveedores", proveedores)
    return caso, llamadas


def test_campana_comparte_pronosticos_y_continua_en_frontera_sin_mutacion(campana):
    caso, llamadas = campana
    original = deepcopy(caso.configuracion)
    ensayo = controlador.calibrar_riesgo_mpc(caso, object())
    assert len(llamadas) == 8
    assert all(H == 2 for _, o, H in llamadas if o < ORIGEN + pd.Timedelta(hours=3))
    assert len(ensayo["ajuste"]) == 12 and len(ensayo["validacion"]) == 4
    assert ensayo["lambda_riesgo"] == 0.
    assert ensayo["recomendado"]
    assert ensayo["verificaciones"]["reutilizadas"] > 0
    assert all(f["violaciones_fisicas"] == 0 for f in ensayo["fisica"])
    assert all(f["estado"] == {"b": 0.} for f in ensayo["estados_frontera"])
    muestras = [m for m in ensayo["muestras"] if m["fase"] == "validacion"]
    assert all(m["estado_inicial"] == {"b": 0.} for m in muestras)
    assert caso.configuracion == original
    assert controlador.verificar_repeticion_riesgo(caso, object(), ensayo)["coincide"]


def test_cambiar_realizacion_validacion_no_cambia_seleccion(campana):
    caso, _ = campana
    uno = controlador.calibrar_riesgo_mpc(caso, object())
    caso.irradiancia_simulacion.loc[caso.irradiancia_simulacion.t.ge(2), "ghi_w_m2"] = 1000.
    otro = controlador.calibrar_riesgo_mpc(caso, object())
    assert uno["lambda_riesgo"] == otro["lambda_riesgo"]
    pd.testing.assert_frame_equal(uno["ajuste"], otro["ajuste"])
    pd.testing.assert_frame_equal(uno["seleccion"], otro["seleccion"])


def test_evaluaciones_compartidas_equivalen_a_recalcular_por_peso():
    caso = _caso()
    config = dict(caso.configuracion, modo_mpc="cvar", alpha_cvar=.9, lambda_riesgo=0.)
    cuantiles = _cuantiles([0., 1000.])
    escenarios = np.array([[0., 0.], [0., 1000.], [0., 1000.]])
    evaluacion = controlador._evaluar_candidatos(caso.demanda, caso.calendario, cuantiles, escenarios, config)
    for peso in controlador.PESOS_RIESGO:
        control = dict(config, lambda_riesgo=peso)
        compartido = controlador._seleccionar_plan(evaluacion, control)
        independiente = controlador.planificar_mpc(caso.demanda, caso.calendario, cuantiles, escenarios, control)
        pd.testing.assert_frame_equal(compartido["puntuaciones"], independiente["puntuaciones"])
        pd.testing.assert_frame_equal(compartido["compromisos"], independiente["compromisos"])


def test_escenarios_duplicados_reutilizan_simulacion_y_conservan_su_masa(monkeypatch):
    caso = _caso()
    config = dict(caso.configuracion, modo_mpc="cvar", alpha_cvar=.9, lambda_riesgo=1.)
    cuantiles = _cuantiles([0., 1000.])
    escenarios = np.array([[0., 0.], [0., 1000.], [0., 1000.]])
    simular = controlador.simular
    llamadas = []

    def contar(*args, **kwargs):
        llamadas.append("compromisos" in kwargs)
        return simular(*args, **kwargs)

    monkeypatch.setattr(controlador, "simular", contar)
    evaluacion = controlador._evaluar_candidatos(caso.demanda, caso.calendario, cuantiles, escenarios, config)
    assert sum(llamadas) == 2 * len(evaluacion["planes"])
    assert len(llamadas) - sum(llamadas) == 12
    for plan, fila in zip(evaluacion["planes"], evaluacion["puntuaciones"].itertuples(), strict=True):
        resultados = [simular(caso.demanda, pd.DataFrame(dict(t=[0, 1], ghi_w_m2=s)), config, compromisos=plan)
                      for s in escenarios]
        assert fila.J_esperado == math.fsum(r["resumen"]["J"] for r in resultados) / 3
        perdidas = [math.fsum(r["cargas"].prioridad_supuesta * r["cargas"].no_atendida_wh) for r in resultados]
        assert fila.CVaR == controlador.calcular_cvar(perdidas, .9)


def test_calibracion_fuera_de_desarrollo_no_carga_modelo(campana, monkeypatch):
    caso, _ = campana
    caso.configuracion["periodos"]["desarrollo"]["fin"] = ORIGEN + pd.Timedelta(hours=3)
    monkeypatch.setattr(controlador, "cargar_chronos", lambda *a: pytest.fail("No debe cargar"))
    with pytest.raises(ValueError, match="desarrollo"):
        controlador.calibrar_riesgo_mpc(caso)


@pytest.mark.parametrize("fallo", [False, True, "otra_unidad"])
def test_cli_cvar_publica_al_final_y_conserva_configuracion(campana, tmp_path, monkeypatch, fallo):
    caso, _ = campana
    caso.configuracion.update(semilla=123, datos={"demanda": "demanda.csv"})
    caso.configuracion["pronosticos"].update(revision="a" * 40, dispositivo="cpu")
    demanda = tmp_path / "demanda.csv"
    demanda.write_text("dato de entrada\n", encoding="utf-8")
    config = tmp_path / "config.json"
    serializable = deepcopy(caso.configuracion)
    serializable["periodos"]["desarrollo"] = {n: v.isoformat() for n, v in serializable["periodos"]["desarrollo"].items()}
    caso.configuracion = serializable
    config.write_text(json.dumps(serializable), encoding="utf-8")
    original = config.read_bytes()
    caso.rutas = dict(configuracion=config, demanda=demanda)
    salida = tmp_path / "salida"
    monkeypatch.setattr("src.datos.cargar_caso", lambda ruta: caso)
    monkeypatch.setattr(controlador, "cargar_chronos", lambda *a: object())
    monkeypatch.setattr(importlib.metadata, "version", lambda p: "prueba")
    monkeypatch.setattr(sys, "argv", ["controlador", "--config", str(config), "--salida", str(salida), "--modo", "cvar"])
    if fallo == "otra_unidad":
        relativa = os.path.relpath

        def unidad_distinta(ruta, base):
            if Path(base) == salida:
                raise ValueError("unidades distintas")
            return relativa(ruta, base)

        monkeypatch.setattr(os.path, "relpath", unidad_distinta)
    if fallo is True:
        reemplazar = Path.replace

        def fallar(ruta, destino):
            if ruta.name == "resumen.json.tmp":
                raise OSError("fallo de publicación")
            return reemplazar(ruta, destino)

        monkeypatch.setattr(Path, "replace", fallar)
        with pytest.raises(SystemExit) as error:
            controlador.main()
        assert error.value.code == 1
        assert not (salida / "resumen.json").exists()
        assert not (salida / "resumen.json.tmp").exists()
    else:
        controlador.main()
        informe = json.loads((salida / "resumen.json").read_text(encoding="utf-8"))
        generada = json.loads((salida / "configuracion_cvar.json").read_text(encoding="utf-8"))
        assert informe["lambda_riesgo"] == generada["lambda_riesgo"] == 0.
        assert (salida / generada["datos"]["demanda"]).resolve() == demanda
        assert informe["verificaciones"]["reproducibilidad"]["coincide"]
    assert config.read_bytes() == original
