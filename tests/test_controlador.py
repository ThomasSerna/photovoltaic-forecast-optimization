"""Casos manuales de escenarios comunes y MPC de IDEA.md §§5–6."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("astral")

from src import controlador
from src.pronosticos import contexto_admisible


ORIGEN = pd.Timestamp("2025-01-06T11:00:00-05:00").tz_convert("UTC")
CUANTILES = (0.1, 0.5, 0.9)


def _config():
    return dict(
        resolucion_h=1, zona_horaria="America/Bogota", latitud=5.5610705,
        longitud=-75.8092448, horizonte=2, replanificacion=1, escenarios=3,
        semilla_escenarios=20261019, lambda_terminal=0.,
        pronosticos=dict(contexto=336, cuantiles=list(CUANTILES)),
        potencia_bus_max_w=1.,
        pv=dict(potencia_nominal_w=1., inversor_max_w=1., factor_desempeno=1.),
        baterias=[dict(id="b", capacidad_wh=1., minimo_wh=0., maximo_wh=1.,
                       inicial_wh=1., potencia_carga_max_w=1., potencia_descarga_max_w=1.,
                       eficiencia_carga=1., eficiencia_descarga=1., limites_potencia="bus")],
    )


def _historia(valores, inicio=None):
    inicio = ORIGEN - pd.Timedelta(hours=len(valores)) if inicio is None else inicio
    fechas = pd.date_range(inicio, periods=len(valores), freq="h")
    fines = fechas + pd.Timedelta(hours=1)
    return pd.DataFrame(dict(inicio_utc=fechas, fin_utc=fines, available_at=fines,
                             ghi_w_m2=np.asarray(valores, dtype=float)))


def _caso(real=(0., 0.), prioridades=(1, 3)):
    indice = pd.date_range(ORIGEN, periods=len(real), freq="h", name="inicio_utc")
    calendario = pd.DataFrame(dict(t=range(len(real)), inicio_utc=indice,
                                   fin_utc=indice + pd.Timedelta(hours=1),
                                   inicio_intervalo=indice.tz_convert("America/Bogota")))
    demanda = pd.DataFrame(dict(t=range(len(real)), carga_id="carga", demanda_wh=1.,
                                solicitada=1, prioridad_supuesta=prioridades,
                                potencia_w_supuesta=1.))
    solar = _historia(real, ORIGEN).assign(t=range(len(real)))
    historia = pd.concat([_historia(np.zeros(336)), solar.drop(columns="t")], ignore_index=True)
    return SimpleNamespace(configuracion=_config(), calendario=calendario, demanda=demanda,
                           irradiancia_simulacion=solar, irradiancia_historica=historia)


def _cuantiles(valores, origen=ORIGEN):
    indice = pd.date_range(origen, periods=len(valores), freq="h", name="inicio_utc")
    return pd.DataFrame(np.repeat(np.asarray(valores)[:, None], len(CUANTILES), axis=1),
                        index=indice, columns=CUANTILES)


def test_rangos_por_columna_con_empates_y_bloques_completos():
    bloques = np.array([[dia, 13 - dia, 7.] * 8 for dia in range(14)], dtype=float)
    historia = _historia(bloques.ravel())
    config = _config()
    config["escenarios"] = 50
    cuantiles = pd.DataFrame(np.tile([10., 50., 90.], (3, 1)),
                            index=_cuantiles([0.] * 3).index, columns=CUANTILES)
    resultado = controlador.generar_escenarios(cuantiles, historia, ORIGEN, config)
    inicios = pd.DatetimeIndex(resultado["bloques"])
    dias = ((inicios - historia.inicio_utc.iloc[0]) / pd.Timedelta(days=1)).to_numpy()
    assert len(inicios) == 50 and np.equal(dias, np.floor(dias)).all()
    assert ((dias >= 0) & (dias < 14)).all()
    esperados = np.column_stack(((dias + 0.5) / 14, (13.5 - dias) / 14,
                                 np.full(len(dias), 0.5)))
    np.testing.assert_allclose(resultado["rangos"], esperados)
    np.testing.assert_allclose(resultado["escenarios"],
                               np.clip(esperados * 100, 10., 90.))


def test_escenarios_pareados_repetibles_y_sin_futuro():
    historia = _historia(np.arange(336))
    config = _config()
    primero = controlador.generar_escenarios(_cuantiles([10., 20.]), historia, ORIGEN, config)
    otro = controlador.generar_escenarios(_cuantiles([100., 200.]), historia, ORIGEN, config)
    futuro = pd.concat([historia, _historia([999999.], ORIGEN)], ignore_index=True)
    repetido = controlador.generar_escenarios(_cuantiles([10., 20.]), futuro, ORIGEN, config)
    for nombre in ("rangos", "bloques"):
        np.testing.assert_array_equal(primero[nombre], otro[nombre])
        np.testing.assert_array_equal(primero[nombre], repetido[nombre])
    np.testing.assert_array_equal(primero["escenarios"], repetido["escenarios"])
    np.testing.assert_allclose(otro["escenarios"], primero["escenarios"] * 10)


@pytest.mark.parametrize("alteracion", ["hueco", "no_disponible"])
def test_no_construye_bloques_con_historia_incompleta(alteracion):
    historia = _historia(np.arange(336))
    if alteracion == "hueco":
        historia = historia.drop(index=50)
    else:
        historia.loc[50, "available_at"] = ORIGEN + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="contexto"):
        controlador.generar_escenarios(_cuantiles([10., 20.]), historia, ORIGEN, _config())


def test_anticipacion_cambia_decision_presente_solo_por_sol_futuro():
    caso = _caso()
    planes = []
    for futuro in (0., 1000.):
        cuantiles = _cuantiles([0., futuro])
        resultado = controlador.planificar_mpc(
            caso.demanda, caso.calendario, cuantiles, np.tile([0., futuro], (3, 1)),
            caso.configuracion,
        )
        planes.append(resultado["compromisos"].sort_values("t")["x"].tolist())
        tabla = resultado["puntuaciones"]
        assert tabla.reserva.tolist() == [0., 0.25, 0.5, 0.75]
        np.testing.assert_allclose(tabla.score, tabla.J_esperado)
    assert planes == [[0, 1], [1, 1]]


@pytest.mark.parametrize("modo", ["original", "cvar"])
def test_evaluar_escenarios_conserva_calendarios_y_estado_inicial(monkeypatch, modo):
    caso = _caso()
    caso.configuracion.update(modo_mpc=modo, lambda_riesgo=1.)
    real = controlador.simular
    candidatos, evaluaciones = [], []

    def observar(demanda, solar, configuracion, **kwargs):
        resultado = real(demanda, solar, configuracion, **kwargs)
        if kwargs.get("compromisos") is None:
            candidatos.append(resultado["cargas"][["t", "carga_id", "x"]].copy())
        else:
            evaluaciones.append((kwargs["compromisos"].copy(), deepcopy(kwargs["estado_inicial"])))
            pd.testing.assert_frame_equal(
                resultado["cargas"][["t", "carga_id", "x"]].reset_index(drop=True),
                kwargs["compromisos"].sort_values(["t", "carga_id"]).reset_index(drop=True),
            )
            assert (resultado["cargas"].z <= resultado["cargas"].x).all()
        return resultado

    monkeypatch.setattr(controlador, "simular", observar)
    controlador.planificar_mpc(caso.demanda, caso.calendario, _cuantiles([0., 1000.]),
                              np.array([[0., 0.], [0., 1000.], [0., 0.]]),
                              caso.configuracion, estado_inicial={"b": 1.})
    assert len(candidatos) == (4 if modo == "original" else 12)
    distintos = {tuple(candidato.x) for candidato in candidatos}
    assert {tuple(plan.x) for plan, _ in evaluaciones} == distintos
    for calendario in distintos:
        assert sum(tuple(plan.x) == calendario for plan, _ in evaluaciones) == 3
    for plan, estado in evaluaciones:
        assert estado == {"b": 1.}
        assert any(plan.equals(candidato) for candidato in candidatos)


@pytest.mark.parametrize("modo", ["original", "cvar"])
def test_ejecucion_continua_estado_real_y_trunca_ultimo_horizonte(monkeypatch, modo):
    caso = _caso(real=(0., 1000., 0.), prioridades=(1, 1, 1))
    caso.configuracion.update(modo_mpc=modo, lambda_riesgo=1.)
    real = controlador.planificar_mpc
    estados, horizontes = [], []

    def observar(*args, **kwargs):
        estados.append(deepcopy(kwargs.get("estado_inicial")))
        return real(*args, **kwargs)

    def pronosticar(historia, origen, horizonte):
        contexto_admisible(historia, origen, 336)
        horizontes.append(horizonte)
        return _cuantiles([1000.] * horizonte, origen)

    monkeypatch.setattr(controlador, "planificar_mpc", observar)
    resultado = controlador.ejecutar_mpc(
        caso, pronosticar, inicio=ORIGEN, fin=ORIGEN + pd.Timedelta(hours=3),
    )
    assert horizontes == [2, 2, 1]
    assert estados[0] in (None, {"b": 1.}) and estados[1:] == [{"b": 0.}, {"b": 0.}]
    assert resultado["horas"].t.tolist() == [0, 1, 2]
    assert resultado["cargas"].z.tolist() == [1, 1, 0]
    assert resultado["estado_final"] == {"b": 0.}
    assert resultado["resumen"]["J"] == 2.
    assert resultado["resumen"]["S_pond"] == pytest.approx(2 / 3)
    assert len(resultado["decisiones"]) == len(resultado["pronosticos"]) == 3
    planes = resultado["planes_elegidos"]
    assert planes.groupby("origen_utc").size().tolist() == [2, 2, 1]
    assert planes.groupby("t").size().to_dict() == {0: 1, 1: 2, 2: 2}
    candidatos = resultado["candidatos"]
    assert not candidatos.duplicated(["origen_utc", "candidato_id"]).any()
    assert candidatos.groupby("origen_utc").elegido.sum().eq(1).all()
    for decision in resultado["decisiones"].to_dict("records"):
        elegido = candidatos.loc[candidatos.origen_utc.eq(decision["origen_utc"]) & candidatos.elegido].iloc[0]
        assert elegido.candidato_id == decision["candidato_id"]
        assert elegido.modo_mpc == modo
        assert elegido.alpha_cvar == .9
        assert elegido.lambda_riesgo == (1. if modo == "cvar" else 0.)
    assert controlador._verificar_fisica(resultado, caso.configuracion)["violaciones_fisicas"] == 0


@pytest.mark.parametrize("modo", ["original", "cvar"])
def test_proveedores_identicos_son_pareados_y_terminal_no_es_J(modo):
    caso = _caso(real=(1000., 1000.), prioridades=(1, 1))
    caso.configuracion["lambda_terminal"] = 0.75
    caso.configuracion.update(modo_mpc=modo, lambda_riesgo=1.)

    def pronosticar(historia, origen, horizonte):
        return _cuantiles([1000.] * horizonte, origen)

    resultados = [controlador.ejecutar_mpc(
        caso, pronosticar, inicio=ORIGEN, fin=ORIGEN + pd.Timedelta(hours=2),
    ) for _ in range(2)]
    for nombre in ("cargas", "horas", "baterias", "decisiones", "candidatos", "planes_elegidos"):
        pd.testing.assert_frame_equal(resultados[0][nombre], resultados[1][nombre])
    assert resultados[0]["resumen"]["J"] == 2.
    assert resultados[0]["resumen"]["S_pond"] == 1.
    assert resultados[0]["estado_final"] == {"b": 1.}
    decisiones = resultados[0]["decisiones"]
    assert decisiones.score.tolist() == ([2.75, 1.75] if modo == "original" else [2., 1.])


@pytest.mark.parametrize("modo", ["original", "cvar"])
def test_realizacion_futura_no_cambia_primera_decision(modo):
    caso = _caso(real=(0., 0.), prioridades=(1, 3))
    caso.configuracion.update(modo_mpc=modo, lambda_riesgo=1.)
    alterado = deepcopy(caso)
    alterado.irradiancia_simulacion.loc[1, "ghi_w_m2"] = 1000.
    alterado.irradiancia_historica.loc[
        alterado.irradiancia_historica.inicio_utc >= ORIGEN + pd.Timedelta(hours=1),
        "ghi_w_m2",
    ] = 1000.

    def pronosticar(historia, origen, horizonte):
        admisible = contexto_admisible(historia, origen, 336)
        assert (admisible.available_at <= origen).all()
        return _cuantiles([0.] * horizonte, origen)

    resultados = [controlador.ejecutar_mpc(
        actual, pronosticar, inicio=ORIGEN, fin=ORIGEN + pd.Timedelta(hours=2),
    ) for actual in (caso, alterado)]
    pd.testing.assert_series_equal(resultados[0]["decisiones"].iloc[0],
                                   resultados[1]["decisiones"].iloc[0])
    pd.testing.assert_frame_equal(resultados[0]["cargas"].query("t == 0"),
                                  resultados[1]["cargas"].query("t == 0"))


@pytest.mark.parametrize("perdidas,alpha,esperado", [
    ([0., 0., 0.], .9, 0.),
    ([3.] * 50, .9, 3.),
    ([0.] * 49 + [3.], .9, .6),
    ([0.] * 49 + [3.], .95, 1.2),
    ([5., 5., 1., 0.], .5, 5.),
    ([10., 9., 0., 0.], .625, 14.5 / 1.5),
    ([100.], .9, 100.),
])
def test_cvar_empirico_con_cola_fraccionaria_y_empates(perdidas, alpha, esperado):
    assert controlador.calcular_cvar(perdidas, alpha) == pytest.approx(esperado)


@pytest.mark.parametrize("perdidas", [[], [[1., 2.]], [-1., 0.], [np.inf], [np.nan]])
def test_cvar_rechaza_perdidas_invalidas(perdidas):
    with pytest.raises(ValueError, match="CVaR"):
        controlador.calcular_cvar(perdidas, .9)


@pytest.mark.parametrize("alpha", [True, 0., 1., -.1, np.nan, np.inf])
def test_cvar_rechaza_alpha_invalido(alpha):
    with pytest.raises(ValueError, match="alpha_cvar"):
        controlador.calcular_cvar([1., 2.], alpha)


@pytest.mark.parametrize("peso,compromisos", [(0., [1, 1]), (1., [0, 1])])
def test_riesgo_cambia_plan_en_caso_manual_de_cincuenta_escenarios(peso, compromisos):
    caso = _caso()
    caso.configuracion.update(modo_mpc="cvar", lambda_riesgo=peso, escenarios=50, lambda_terminal=10.)
    caso.demanda.loc[0, "demanda_wh"] = .30
    escenarios = np.tile([0., 1000.], (50, 1))
    escenarios[-1, 1] = 0.
    entradas = deepcopy((caso.demanda, caso.configuracion, escenarios))
    resultado = controlador.planificar_mpc(
        caso.demanda, caso.calendario, _cuantiles([0., 1000.]), escenarios, caso.configuracion)
    assert resultado["compromisos"].x.tolist() == compromisos
    tabla = resultado["puntuaciones"]
    assert len(resultado["planes"]) == len(tabla) == 2
    assert tabla.reserva.tolist() == [0., .75]
    assert tabla.cuantil.tolist() == [.1, .1]
    assert tabla.candidato_id.tolist() == [0, 1]
    np.testing.assert_allclose(tabla.J_esperado, [3.24, 3.])
    np.testing.assert_allclose(tabla.perdida_media, [.06, .30])
    np.testing.assert_allclose(tabla.CVaR, [.60, .30])
    np.testing.assert_allclose(tabla.score, tabla.J_esperado - peso * tabla.CVaR)
    assert tabla.loc[tabla.elegido, "candidato_id"].tolist() == [resultado["candidato_id"]]
    pd.testing.assert_frame_equal(caso.demanda, entradas[0])
    assert caso.configuracion == entradas[1]
    np.testing.assert_array_equal(escenarios, entradas[2])


def test_mismo_rho_diferentes_cuantiles_se_registra_el_ganador_correcto():
    caso = _caso()
    caso.configuracion.update(modo_mpc="cvar", lambda_riesgo=0.)
    cuantiles = _cuantiles([0., 1000.])
    cuantiles.loc[cuantiles.index[1], .1] = 0.
    prediccion = dict(corregidos=cuantiles, escenarios=np.tile([0., 1000.], (3, 1)))
    reloj = controlador._calendario_utc(caso.calendario)
    resultado, decision = controlador._ejecutar_bloque(
        caso, reloj, ORIGEN, ORIGEN + pd.Timedelta(hours=2), prediccion, {"b": 1.}, caso.configuracion)
    assert decision["reserva"] == 0. and decision["cuantil"] == .5
    assert decision["J_esperado"] == 4.
    elegido = resultado["candidatos"].loc[lambda tabla: tabla.elegido].iloc[0]
    assert elegido.cuantil == decision["cuantil"]
    assert resultado["planes_elegidos"].x.tolist() == [1, 1]


def test_empate_favorece_menor_reserva_y_despues_menor_cuantil():
    caso = _caso()
    caso.configuracion.update(modo_mpc="cvar", lambda_riesgo=0.)
    cuantiles = _cuantiles([0., 1000.])
    escenarios = np.array([[0., 1000.], [0., 1000.], [0., 0.]])
    resultado = controlador.planificar_mpc(
        caso.demanda, caso.calendario, cuantiles, escenarios, caso.configuracion)
    np.testing.assert_allclose(resultado["puntuaciones"].score, [3., 3.])
    assert resultado["reserva"] == 0.
    assert resultado["compromisos"].x.tolist() == [1, 1]
    assert resultado["puntuaciones"].loc[lambda tabla: tabla.elegido, "cuantil"].tolist() == [.1]
