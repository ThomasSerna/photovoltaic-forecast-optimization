"""Experimento 2×2 de T08 con dobles: contrastes a mano, pareo entre modos y comando."""

from collections import Counter
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

pytest.importorskip("astral")
pytest.importorskip("matplotlib")

from src.configuracion import parametros_mpc
from test_controlador import CUANTILES, ORIGEN, _cuantiles
from test_experimento import _constante, _evaluable

SPEC = spec_from_file_location("experimento_2x2", Path(__file__).resolve().parents[1] / "scripts/experimento_2x2.py")
modulo = module_from_spec(SPEC)
SPEC.loader.exec_module(modulo)

HORA = pd.Timedelta(hours=1)


def _ejecucion():
    caso = _evaluable(real=(0., 1000., 0.), prioridades=(1, 1, 1))
    return modulo.ejecutar_2x2(caso, dict(beta=_constante(0.), chronos=_constante(1000.)))


def _metricas():
    """J y CVaR elegidos para sumar a mano; el resto de indicadores queda en cero."""
    campos = [campo for _, _, campo in modulo.FILAS] + ["b_minimo_wh"]
    valores = dict(A=(10., 5.), B=(13., 5.), C=(11., 4.), D=(16., 1.))
    return {celda: dict(dict.fromkeys(campos, 0.), J=j, CVaR_diario=cvar) for celda, (j, cvar) in valores.items()}


def test_memorizar_infiere_una_vez_por_origen_y_devuelve_copias():
    llamadas = []

    def pronosticar(historia, origen, horizonte):
        llamadas.append(origen)
        return _cuantiles([1.] * horizonte, origen)

    memorizado = modulo.memorizar(pronosticar)
    primero = memorizado(None, ORIGEN, 2)
    primero.iloc[0, 0] = 99.
    assert memorizado(None, ORIGEN, 2).iloc[0, 0] == 1.
    memorizado(None, ORIGEN + HORA, 2)
    assert llamadas == [ORIGEN, ORIGEN + HORA]


def test_ejecutar_2x2_comparte_pronosticos_y_conserva_la_configuracion_base():
    caso = _evaluable(real=(0., 1000., 0.), prioridades=(1, 1, 1))
    base = deepcopy(caso.configuracion)
    inferencias = Counter()

    def contando(nombre, valor):
        def pronosticar(historia, origen, horizonte):
            inferencias[nombre] += 1
            return _cuantiles([valor] * horizonte, origen)
        return pronosticar

    ejecucion = modulo.ejecutar_2x2(caso, dict(beta=contando("beta", 0.), chronos=contando("chronos", 1000.)))
    assert inferencias == dict(beta=3, chronos=3)  # tres orígenes, no seis
    assert caso.configuracion == base
    assert ejecucion["casos"]["original"] is caso
    assert parametros_mpc(ejecucion["casos"]["cvar"].configuracion) == ("cvar", .9, 0.)
    assert ejecucion["entre_modos"]["pronosticos_identicos"]
    fisica = [f for e in ejecucion["evaluaciones"].values() for f in e["fisica"].values()]
    assert {f["violaciones_fisicas"] for f in fisica} == {0}


@pytest.mark.parametrize("cambio", [dict(modo_mpc="cvar", lambda_riesgo=0.), dict(lambda_terminal=.25)])
def test_ejecutar_2x2_exige_el_mpc_original_sin_termino_terminal(cambio):
    caso = _evaluable(real=(0., 1000.), prioridades=(1, 3))
    caso.configuracion.update(cambio)
    with pytest.raises(ValueError, match="configuración base"):
        modulo.ejecutar_2x2(caso, dict(beta=_constante(0.), chronos=_constante(0.)))


def test_candidatos_cvar_contienen_a_los_originales_con_cuantiles_dispersos():
    caso = _evaluable(real=(0., 0.), prioridades=(1, 3))

    def disperso(historia, origen, horizonte):
        filas = [[0., 0., 1000.]] * horizonte
        return pd.DataFrame(filas, columns=CUANTILES,
                            index=pd.date_range(origen, periods=horizonte, freq="h", name="inicio_utc"))

    ejecucion = modulo.ejecutar_2x2(caso, dict(beta=_constante(0.), chronos=disperso))
    assert ejecucion["entre_modos"]["candidatos_contenidos"]
    original, cvar = (ejecucion["evaluaciones"][m]["resultados"]["chronos"] for m in ("original", "cvar"))
    assert cvar["decisiones"]["score"].iloc[0] >= original["decisiones"]["score"].iloc[0]
    assert len(cvar["candidatos"]) >= len(original["candidatos"])


@pytest.mark.parametrize("alteracion, mensaje", [
    (lambda r: r["pronosticos"][0]["crudos"].iloc.__setitem__((0, 0), 7.), "cambia de pronóstico"),
    (lambda r: r["pronosticos"][0]["escenarios"].__setitem__((0, 0), 7.), "cambia de pronóstico"),
    (lambda r: r["decisiones"].loc.__setitem__((0, "score"), -1.), "no contienen"),
])
def test_verificar_entre_modos_detecta_alteraciones(alteracion, mensaje):
    evaluaciones = _ejecucion()["evaluaciones"]
    alteracion(evaluaciones["cvar"]["resultados"]["beta"])
    with pytest.raises(ValueError, match=mensaje):
        modulo.verificar_entre_modos(**evaluaciones)


def test_contrastes_e_interaccion_comprobables_a_mano():
    metricas = _metricas()
    metricas["C"]["servicio_critico"] = None
    tabla = modulo.tabla_2x2(metricas, dict(baterias=[dict(id="b")])).set_index("campo")
    j, cvar = tabla.loc["J"], tabla.loc["CVaR_diario"]
    assert (j["C-A"], j["D-B"], j["B-A"], j["D-C"]) == (1., 3., 3., 5.)
    assert j[modulo.INTERACCION] == 2. == j["D-B"] - j["C-A"]
    assert cvar[modulo.INTERACCION] == -3. == cvar["D-B"] - cvar["C-A"]
    assert pd.isna(tabla.loc["servicio_critico", "C-A"])
    assert tabla.loc["servicio_critico", "D-B"] == 0.
    assert tabla.loc["b_minimo_wh", "indicador"] == "b: mínimo"
    texto = modulo.tabla_markdown(tabla.reset_index())
    assert "| J realizado | Wh·p | 10.000 | 13.000 | 11.000 | 16.000 | +1.000 | +3.000 | +2.000 |" in texto
    assert "| Servicio crítico (p ≥ 3) | fracción | 0.00 % | 0.00 % | — | 0.00 % | — | +0.0000 p.p. | — |" in texto


def test_cvar_diario_de_veintiocho_dias_usa_dos_dias_y_ocho_decimas():
    perdidas = [0.] * 25 + [10., 20., 30.]
    assert modulo.calcular_cvar(perdidas, .9) == pytest.approx((30 + 20 + .8 * 10) / 2.8)


def test_diario_2x2_y_resumen_comprobables_a_mano():
    fechas = ("2025-03-03", "2025-03-04")
    diarios = pd.DataFrame(
        [(f, c, j, p) for c, valores in dict(A=((5., 3.), (5., 3.)), B=((5., 3.), (6., 2.)),
                                             C=((7., 1.), (5., 3.)), D=((7., 1.), (9., 0.))).items()
         for f, (j, p) in zip(fechas, valores)], columns=["fecha", "celda", "J", "perdida"])
    diario = modulo.diario_2x2(diarios)
    assert diario["delta_J_C-A"].tolist() == [2., 0.]
    assert diario["delta_J_D-B"].tolist() == [2., 3.]
    assert diario[f"delta_J_{modulo.INTERACCION}"].tolist() == [0., 3.]
    assert diario["delta_perdida_D-B"].tolist() == [-2., -2.]
    assert not any("CVaR" in columna for columna in diario.columns)


def test_horas_con_x_distinto_cuenta_horas_y_no_cargas():
    uno = dict(cargas=pd.DataFrame(dict(t=[0, 0, 1, 2], carga_id=list("abab"), x=[1, 1, 0, 1])))
    otro = dict(cargas=pd.DataFrame(dict(t=[0, 0, 1, 2], carga_id=list("abab"), x=[0, 0, 0, 0])))
    assert modulo.horas_con_x_distinto(uno, otro) == 2
    assert modulo.horas_con_x_distinto(uno, uno) == 0


@pytest.mark.parametrize("delta_j, delta_cvar, esperado", [
    (1., -1., "mejora"), (1., 0., "mejora"), (0., -1., "mejora"),
    (-1., 1., "empeora"), (-1., 0., "empeora"), (0., 1., "empeora"),
    (1., 1., "compensación"), (-1., -1., "compensación"),
    (0., 0., "sin cambio"), (1e-7, -1e-7, "sin cambio"),
])
def test_clasificar_compensacion(delta_j, delta_cvar, esperado):
    assert modulo.clasificar_compensacion(delta_j, delta_cvar) == esperado


def _publicadas():
    pronostico = lambda mae: dict(pronostico=dict(diurno=dict(corregido=dict(mae_w_m2=mae))))
    metricas = dict(A=dict(S_pond=.9312263476, J=1000.),
                    B=dict(S_pond=.9328742419, J=1505.599, CVaR_diario=3949.084),
                    D=dict(J=286406.573, CVaR_diario=3887.228))
    return metricas, dict(original=dict(metricas=dict(beta=pronostico(66.33), chronos=pronostico(64.20),
                                                      reactiva=dict(servicio=dict(J=285240.845)))))


def test_cifras_publicadas_incluyen_d_y_rechazan_cambios():
    metricas, resumenes = _publicadas()
    assert all(fila["coincide"] for fila in modulo.cotejar_publicadas(metricas, resumenes).values())
    metricas["D"]["J"] += .001
    with pytest.raises(ValueError, match="cifras publicadas"):
        modulo.cotejar_publicadas(metricas, resumenes)


@pytest.fixture
def comando(tmp_path, monkeypatch):
    """Comando T08 sobre el caso de anticipación, con dobles en lugar de Chronos-2."""
    caso = _evaluable(real=(0., 1000.), prioridades=(1, 3))
    caso.configuracion["semilla"] = 20261018
    caso.configuracion["pronosticos"].update(revision="a" * 40, dispositivo="cpu")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(caso.configuracion), encoding="utf-8")
    caso.rutas = {"configuracion": config}
    dobles = dict(beta=_constante(0.), chronos=_constante(1000.))
    monkeypatch.setattr(modulo, "cargar_caso", lambda ruta: caso)
    monkeypatch.setattr(modulo, "cargar_chronos", lambda revision, dispositivo: object())
    monkeypatch.setattr(modulo, "proveedores", lambda caso, pipeline: dobles)
    monkeypatch.setattr(modulo, "cotejar_publicadas", lambda metricas, resumenes: {})
    salida = tmp_path / "t08"
    monkeypatch.setattr(sys, "argv", ["experimento_2x2", "--config", str(config), "--salida", str(salida)])
    return config, salida, config.read_bytes()


def test_comando_exporta_cruce_figuras_y_registro_sin_tocar_entradas(comando):
    config, salida, original = comando
    modulo.main()
    assert config.read_bytes() == original
    for modo in ("original", "cvar"):
        assert (salida / modo / "tabla_ab.csv").stat().st_size > 0
        assert (salida / modo / "figuras" / "baterias.png").stat().st_size > 0
    for nombre in ("tabla_2x2.csv", "tabla_2x2.md", "diario_2x2.csv", "contrastes.csv"):
        assert (salida / nombre).stat().st_size > 0
    for nombre in ("contrastes", "diferencias_diarias", "compensacion"):
        for extension in ("png", "svg"):
            assert (salida / "figuras" / f"{nombre}.{extension}").stat().st_size > 5000
    informe = json.loads((salida / "resumen.json").read_text(encoding="utf-8"))
    assert informe["control"]["cvar"] == dict(modo_mpc="cvar", alpha_cvar=.9, lambda_riesgo=0.)
    assert informe["celdas"]["C"] == dict(modo_mpc="cvar", proveedor="beta")
    assert informe["verificaciones"]["entre_modos"]["pronosticos_identicos"]
    assert {r["coincide"] for r in informe["verificaciones"]["reproducibilidad"].values()} == {True}
    assert "tabla_2x2.csv" in informe["archivos_sha256"]
    # Contraste a mano de la tabla con los CSV de cada modo.
    tabla = pd.read_csv(salida / "tabla_2x2.csv").set_index("campo")
    original_ab, cvar_ab = (pd.read_csv(salida / m / "tabla_ab.csv").set_index(["indicador", "unidad"])
                            for m in ("original", "cvar"))
    fila = ("J realizado", "Wh·p")
    assert tabla.loc["J", "A"] == original_ab.loc[fila, "beta"]
    assert tabla.loc["J", "D"] == cvar_ab.loc[fila, "chronos"]
    assert tabla.loc["J", "D-B"] == cvar_ab.loc[fila, "chronos"] - original_ab.loc[fila, "chronos"]
    contrastes = pd.read_csv(salida / "contrastes.csv").set_index("contraste")
    assert pd.isna(contrastes.loc[modulo.INTERACCION, "compensacion"])
    assert contrastes.loc["C-A", "compensacion"] in ("sin cambio", "mejora", "empeora", "compensación")


@pytest.mark.parametrize("fallo", ["salida_existente", "publicadas"])
def test_comando_fallido_no_publica_resumen(comando, monkeypatch, fallo):
    config, salida, original = comando
    if fallo == "salida_existente":
        salida.mkdir()
    else:
        def distinto(metricas, resumenes):
            raise ValueError("T08: diferencias con cifras publicadas")
        monkeypatch.setattr(modulo, "cotejar_publicadas", distinto)
    with pytest.raises(SystemExit) as error:
        modulo.main()
    assert error.value.code == 1
    assert not (salida / "resumen.json").exists() and not (salida / "resumen.json.tmp").exists()
    assert config.read_bytes() == original
