"""Evaluación pareada de T05 con dobles: métricas a mano, pareo, filtraciones y repetición."""

import json
import math
import sys

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("astral")
pytest.importorskip("matplotlib")

from src import experimento
from src.simulador import simular
from test_controlador import CUANTILES, ORIGEN, _caso, _config, _cuantiles, _historia

HORA = pd.Timedelta(hours=1)


def _evaluable(real, prioridades):
    """Caso manual de test_controlador cuyo periodo de evaluación son sus horas reales."""
    caso = _caso(real=real, prioridades=prioridades)
    fin = ORIGEN + len(real) * HORA
    caso.configuracion["periodos"] = {"evaluacion": dict(inicio=ORIGEN.isoformat(), fin=fin.isoformat())}
    return caso


def _constante(valor):
    return lambda historia, origen, horizonte: _cuantiles([valor] * horizonte, origen)


def test_metricas_pronostico_comprobables_a_mano():
    noche = pd.Timestamp("2025-01-06T23:00:00-05:00").tz_convert("UTC")
    dia = pd.DataFrame([[0., 10., 20.], [0., 0., 0.]], columns=CUANTILES,
                       index=pd.date_range(ORIGEN, periods=2, freq="h", name="inicio_utc"))
    nocturno = pd.DataFrame([[0., 0., 0.]], columns=CUANTILES,
                            index=pd.date_range(noche, periods=1, freq="h", name="inicio_utc"))
    crudo = dia.copy()
    crudo.iloc[1, 0] = -5.
    pronosticos = [dict(origen_utc=ORIGEN, crudos=crudo, corregidos=dia),
                   dict(origen_utc=noche, crudos=nocturno, corregidos=nocturno)]
    real = pd.Series([10., 10., 0.], index=[ORIGEN, ORIGEN + HORA, noche])
    tabla = experimento.tabla_pronosticos(pronosticos, real)
    assert tabla["anticipacion_h"].tolist() == [1, 2, 1]
    config = _config()
    config["replanificacion"] = 1
    metricas = experimento.metricas_pronostico(tabla, config)
    # Pinball por fila: (1 + 0 + 1), (1 + 5 + 9) y 0; cobertura 0.1–0.9: dentro, fuera, dentro.
    assert metricas["global"]["corregido"] == pytest.approx(dict(
        pares=3, mae_w_m2=10 / 3, rmse_w_m2=math.sqrt(100 / 3), pinball_w_m2=17 / 9, cobertura=2 / 3))
    assert metricas["diurno"]["corregido"] == pytest.approx(dict(
        pares=2, mae_w_m2=5., rmse_w_m2=math.sqrt(50), pinball_w_m2=17 / 6, cobertura=0.5))
    assert metricas["diurno_ejecutado"]["corregido"] == pytest.approx(dict(
        pares=1, mae_w_m2=0., rmse_w_m2=0., pinball_w_m2=2 / 3, cobertura=1.))
    # El crudo negativo solo cambia el pinball de q0.1: 0.1 × 15 en vez de 0.1 × 10.
    assert metricas["diurno"]["crudo"]["pinball_w_m2"] == pytest.approx(17.5 / 6)
    assert metricas["cobertura_nominal"] == pytest.approx(0.8)


def test_metricas_servicio_e_indicadores_del_articulo_a_mano():
    config = _config()
    config["potencia_bus_max_w"] = 10.
    config["pv"]["inversor_max_w"] = 10.
    config["baterias"][0].update(capacidad_wh=2., maximo_wh=2., inicial_wh=2.,
                                 potencia_carga_max_w=10., potencia_descarga_max_w=10.)
    horas = [(2., 1.), (2., 1.), (0., 0.), (0., 1.)]  # Wh de a (p=1) y b (p=3) por hora
    demanda = pd.DataFrame(
        [(t, carga, wh, p) for t, energias in enumerate(horas)
         for carga, wh, p in zip(("a", "b"), energias, (1, 3))],
        columns=["t", "carga_id", "demanda_wh", "prioridad_supuesta"],
    ).assign(solicitada=lambda d: (d.demanda_wh > 0).astype(int), potencia_w_supuesta=2.)
    solar = pd.DataFrame({"t": range(4), "ghi_w_m2": [0., 0., 0., 1000.]})
    # t0: 2 Wh útiles, cabe b y no a; t1: queda 1 Wh para b; t2 sin demanda; t3: FV cubre b.
    fila = experimento.metricas_servicio(simular(demanda, solar, config), config)
    assert fila["J"] == 9. and fila["S_pond"] == pytest.approx(9 / 13)
    assert (fila["solicitada_wh"], fila["atendida_wh"], fila["no_atendida_wh"]) == (7., 3., 4.)
    assert fila["no_atendida_fraccion"] == pytest.approx(4 / 7)
    assert fila["servicio_critico"] == fila["servicio_p3"] == 1.
    assert fila["servicio_p1"] == 0.
    assert fila["nivel_servicio_horas"] == pytest.approx(1 / 3)
    assert fila["faltante_medio"] == pytest.approx(2 / 3)
    assert (fila["compromisos_incumplidos"], fila["solicitudes_no_comprometidas"],
            fila["solicitudes_desatendidas"]) == (0, 2, 2)
    assert (fila["b_minimo_wh"], fila["b_medio_wh"], fila["b_final_wh"], fila["b_horas_en_minimo"]) == (
        0., 0.25, 0., 3)


def test_diferencias_diarias_por_fecha_local():
    calendario = pd.DataFrame({"t": [0, 1], "inicio_intervalo": pd.DatetimeIndex(
        ["2025-03-03T23:00:00", "2025-03-04T00:00:00"]).tz_localize("America/Bogota")})

    def resultado(atendida):
        return dict(cargas=pd.DataFrame(dict(t=[0, 1], prioridad_supuesta=[1, 3], demanda_wh=[1., 1.],
                                             atendida_wh=atendida, no_atendida_wh=1. - np.array(atendida))))

    tabla = experimento.diario({"beta": resultado([1., 0.]), "chronos": resultado([1., 1.])}, calendario)
    assert [str(f) for f in tabla["fecha"]] == ["2025-03-03", "2025-03-04"]
    assert tabla["delta_J"].tolist() == [0., 3.]
    assert tabla["S_pond_beta"].tolist() == [1., 0.]
    assert experimento.resumir_diferencias(tabla) == dict(
        dias=2, media=1.5, mediana=1.5, minimo=0., maximo=3., dias_b_mayor=1, dias_iguales=1, dias_b_menor=0)


def test_proveedores_identicos_dan_ramas_identicas_y_pareadas():
    caso = _evaluable(real=(0., 1000., 0.), prioridades=(1, 1, 1))
    evaluacion = experimento.evaluar(caso, {nombre: _constante(1000.) for nombre in experimento.POLITICAS})
    a, b = (evaluacion["resultados"][nombre] for nombre in experimento.POLITICAS)
    for tabla in ("cargas", "horas", "baterias", "decisiones"):
        pd.testing.assert_frame_equal(a[tabla], b[tabla])
    assert evaluacion["pareo"] == dict(origenes=3, filtraciones=0, entradas_comunes=True)
    assert all(f["violaciones_fisicas"] == 0 for f in evaluacion["fisica"].values())
    registro = pd.DataFrame(evaluacion["registros"]["beta"])
    assert registro["contexto_fin_utc"].eq(registro["origen_utc"]).all()
    assert registro["horizonte"].tolist() == [2, 2, 1]
    tabla = experimento.diario(evaluacion["resultados"], caso.calendario)
    assert (tabla["delta_J"] == 0).all()


def test_ramas_conservan_estados_distintos_y_la_referencia_reacciona():
    # Anticipación de IDEA §6: A sin sol futuro reserva la batería; B la gasta en t=0.
    caso = _evaluable(real=(0., 1000.), prioridades=(1, 3))
    evaluacion = experimento.evaluar(caso, {"beta": _constante(0.), "chronos": _constante(1000.)})
    a, b, ref = (evaluacion["resultados"][n] for n in (*experimento.POLITICAS, experimento.REFERENCIA))
    assert a["cargas"]["z"].tolist() == [0, 1] and b["cargas"]["z"].tolist() == [1, 1]
    assert a["baterias"]["inicial_wh"].tolist() == [1., 1.]
    assert b["baterias"]["inicial_wh"].tolist() == [1., 0.]
    assert (a["resumen"]["J"], b["resumen"]["J"], ref["resumen"]["J"]) == (3., 4., 4.)
    assert ref["cargas"]["x"].tolist() == ref["cargas"]["z"].tolist() == [1, 1]
    tabla = experimento.diario(evaluacion["resultados"], caso.calendario)
    assert tabla["delta_J"].sum() == 1.


def test_instrumentar_rechaza_historia_no_admisible():
    historia = _historia(np.zeros(336))
    registro = []
    envuelto = experimento._instrumentar(lambda h, o, horizonte: "cuantiles", registro, 336)
    assert envuelto(historia, ORIGEN, 2) == "cuantiles"
    assert registro[0]["contexto_fin_utc"] == ORIGEN and registro[0]["inferencia_s"] >= 0
    futura = historia.copy()
    futura.loc[335, "available_at"] = ORIGEN + HORA
    for alterada in (futura, historia.iloc[1:]):
        with pytest.raises(ValueError, match="filtración"):
            envuelto(alterada, ORIGEN, 2)
    assert len(registro) == 1


@pytest.mark.parametrize("inicio,fin", [(ORIGEN - HORA, None), (None, ORIGEN + 3 * HORA)])
def test_rechaza_fechas_fuera_de_evaluacion(inicio, fin):
    caso = _evaluable(real=(0., 1000.), prioridades=(1, 3))
    with pytest.raises(ValueError, match="evaluación"):
        experimento.evaluar(caso, {n: _constante(0.) for n in experimento.POLITICAS}, inicio=inicio, fin=fin)


def test_pareo_detecta_escenarios_distintos():
    caso = _evaluable(real=(0., 1000.), prioridades=(1, 3))
    evaluacion = experimento.evaluar(caso, {n: _constante(0.) for n in experimento.POLITICAS})
    evaluacion["resultados"]["chronos"]["pronosticos"][0]["semilla"] = [0, 0]
    with pytest.raises(ValueError, match="escenarios distintos"):
        experimento.verificar_pareo(evaluacion["resultados"], evaluacion["registros"])


def test_reproducibilidad_compara_solo_horizontes_completos():
    # H=2, R=1 y 6 h: se repiten 4 h; los orígenes 0–2 tienen horizonte completo.
    caso = _evaluable(real=(0., 1000., 0., 1000., 0., 1000.), prioridades=(1, 3, 1, 3, 1, 3))
    pronosticadores = {"beta": _constante(0.), "chronos": _constante(1000.)}
    evaluacion = experimento.evaluar(caso, pronosticadores)
    informe = experimento.verificar_reproducibilidad(caso, pronosticadores, evaluacion)
    assert informe == dict(horas_comparadas=3, origenes_comparados=3, max_diferencia_wh=0.,
                           max_diferencia_cuantiles_w_m2=0., coincide=True)


def test_reproducibilidad_detecta_un_pronosticador_inestable():
    caso = _evaluable(real=(0., 1000.), prioridades=(1, 3))
    vistos = set()

    def inestable(historia, origen, horizonte):
        valor = 1000. if origen in vistos else 0.
        vistos.add(origen)
        return _cuantiles([valor] * horizonte, origen)

    pronosticadores = {"beta": _constante(0.), "chronos": inestable}
    evaluacion = experimento.evaluar(caso, pronosticadores)
    with pytest.raises(ValueError, match="reproducibilidad: chronos"):
        experimento.verificar_reproducibilidad(caso, pronosticadores, evaluacion)


@pytest.fixture
def comando(tmp_path, monkeypatch):
    """Comando T05 sobre el caso de anticipación, con dobles en lugar de Chronos-2."""
    caso = _evaluable(real=(0., 1000.), prioridades=(1, 3))
    caso.configuracion["semilla"] = 20261018
    caso.configuracion["pronosticos"].update(revision="a" * 40, dispositivo="cpu")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(caso.configuracion), encoding="utf-8")
    caso.rutas = {"configuracion": config}
    dobles = {"beta": _constante(0.), "chronos": _constante(1000.)}
    monkeypatch.setattr("src.datos.cargar_caso", lambda ruta: caso)
    monkeypatch.setattr("src.pronosticos.cargar_chronos", lambda revision, dispositivo: object())
    monkeypatch.setattr("src.controlador.proveedores", lambda caso, pipeline: dobles)
    salida = tmp_path / "t05"
    monkeypatch.setattr(sys, "argv", ["experimento", "--config", str(config), "--salida", str(salida)])
    return config, salida, config.read_bytes()


def test_comando_exporta_tabla_graficas_y_registro_sin_tocar_entradas(comando):
    config, salida, original = comando
    experimento.main()
    assert config.read_bytes() == original
    for nombre in ("cargas", "horas", "baterias", "decisiones", "pronosticos", "diario", "tabla_ab"):
        assert (salida / f"{nombre}.csv").stat().st_size > 0
    for nombre in ("irradiancia", "demanda_atendida", "baterias", "diferencias_diarias"):
        assert (salida / "figuras" / f"{nombre}.png").stat().st_size > 0
    informe = json.loads((salida / "resumen.json").read_text(encoding="utf-8"))
    verificaciones = informe["verificaciones"]
    assert {f["violaciones_fisicas"] for f in verificaciones["fisica"].values()} == {0}
    assert verificaciones["pareo"]["filtraciones"] == 0 and verificaciones["reproducibilidad"]["coincide"]
    assert informe["metricas"]["beta"]["reservas_elegidas"] == {"0.25": 1, "0": 1}
    assert informe["diferencias_diarias"]["dias_b_mayor"] == 1
    # Contraste a mano de una fila de la tabla A/B con los CSV exportados.
    horas = pd.read_csv(salida / "horas.csv")
    tabla = pd.read_csv(salida / "tabla_ab.csv").set_index(["indicador", "unidad"])
    for politica in ("beta", "chronos", "reactiva"):
        assert tabla.loc[("Energía no atendida", "Wh"), politica] == horas.loc[
            horas.politica.eq(politica), "no_atendida_wh"].sum()
    assert tabla.loc[("J realizado", "Wh·p"), "diferencia_b_menos_a"] == 1.
    assert "| Servicio | J realizado | Wh·p | 3.000 | 4.000 | 1.000 | 4.000 |" in (
        salida / "tabla_ab.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("fallo", ["salida_existente", "codigo"])
def test_comando_fallido_no_publica_resumen(comando, monkeypatch, fallo):
    config, salida, original = comando
    if fallo == "salida_existente":
        salida.mkdir()
    else:
        hashes = iter([{"experimento.py": "original"}, {"experimento.py": "editado"}])
        monkeypatch.setattr("src.cli.codigo_sha256", lambda: next(hashes))
    with pytest.raises(SystemExit) as error:
        experimento.main()
    assert error.value.code == 1
    assert not (salida / "resumen.json").exists() and not (salida / "resumen.json.tmp").exists()
    assert config.read_bytes() == original
