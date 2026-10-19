"""Casos de aceptación de HU-01, incluidos archivos inconsistentes y fuga temporal."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from energy_project import cargar_caso, historial_admisible
from energy_project.configuracion import cargar_configuracion

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "scripts/configuracion.json"


@pytest.fixture(scope="module")
def caso():
    return cargar_caso(CONFIG)


@pytest.fixture
def configuracion(tmp_path):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["datos"] = {clave: str((CONFIG.parent / valor).resolve()) for clave, valor in config["datos"].items()}
    ruta = tmp_path / "configuracion.json"
    ruta.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return config, ruta


def test_caso_compartido_y_unidades(caso):
    assert len(caso.catalogo) == 13
    assert len(caso.demanda) == 26208
    assert len(caso.diaria) == 84
    assert len(caso.calendario) == len(caso.irradiancia_simulacion) == 2016
    assert len(caso.irradiancia_historica) == 17544
    assert caso.demanda["demanda_wh"].sum() / 1000 == pytest.approx(460.019613297, abs=1e-9)
    assert caso.irradiancia_historica["ghi_w_m2"].max() == 1083
    assert caso.irradiancia_historica["particion"].value_counts().to_dict() == {
        "historial_previo": 8904, "desarrollo": 1344, "evaluacion": 672, "reserva_no_usada": 6624,
    }
    assert caso.demanda.groupby("particion").size().to_dict() == {"desarrollo": 17472, "evaluacion": 8736}
    assert str(caso.calendario["inicio_intervalo"].dt.tz) == "America/Bogota"
    assert str(caso.irradiancia_historica["inicio_utc"].dt.tz) == "UTC"
    assert caso.calendario["inicio_intervalo"].iloc[0].isoformat() == "2025-01-06T00:00:00-05:00"
    assert caso.calendario["fin_intervalo"].iloc[-1].isoformat() == "2025-03-31T00:00:00-05:00"
    assert caso.irradiancia_historica["available_at"].equals(caso.irradiancia_historica["fin_utc"])
    assert [b["id"] for b in caso.configuracion["baterias"]] == ["bateria_01", "bateria_02"]


def test_ingesta_no_escribe_entradas(caso):
    antes = {nombre: hashlib.sha256(ruta.read_bytes()).hexdigest() for nombre, ruta in caso.rutas.items()}
    cargar_caso(CONFIG)
    despues = {nombre: hashlib.sha256(ruta.read_bytes()).hexdigest() for nombre, ruta in caso.rutas.items()}
    assert antes == despues


@pytest.mark.parametrize("origen,horas", [
    ("2025-01-06T00:00:00-05:00", 8904),
    ("2025-03-03T00:00:00-05:00", 10248),
    ("2025-03-31T00:00:00-05:00", 10920),
])
def test_fronteras_no_revelan_futuro(caso, origen, horas):
    contexto = historial_admisible(caso.irradiancia_historica, origen)
    assert len(contexto) == horas
    assert contexto["fin_utc"].max() == pd.Timestamp(origen)
    assert contexto["inicio_utc"].max() < pd.Timestamp(origen)


def test_observacion_terminada_pero_no_disponible(caso):
    origen = pd.Timestamp("2025-01-06T00:00:00-05:00")
    historico = caso.irradiancia_historica.copy()
    historico.loc[8903, "available_at"] = origen + pd.Timedelta(hours=1)
    contexto = historial_admisible(historico, origen)
    assert len(contexto) == 8903
    assert 8903 not in contexto.index


@pytest.mark.parametrize("origen", ["2025-01-06", "no es fecha", None])
def test_origen_inequivoco(caso, origen):
    with pytest.raises(ValueError, match="origen"):
        historial_admisible(caso.irradiancia_historica, origen)


@pytest.mark.parametrize("ruta_campo,valor", [
    (("horizonte",), 0), (("replanificacion",), 25), (("escenarios",), 2.5),
    (("semilla_escenarios",), True), (("potencia_bus_max_w",), float("inf")),
    (("lambda_terminal",), -0.1), (("lambda_terminal",), float("nan")),
    (("pv", "paneles"), True), (("pv", "potencia_nominal_w"), 1500),
    (("pv", "factor_desempeno"), 1.1), (("pv", "geometria"), "inclinado"),
    (("baterias", 1, "inicial_wh"), 470), (("baterias", 1, "maximo_wh"), 2500),
    (("baterias", 1, "id"), "bateria_01"), (("baterias", 1, "eficiencia_carga"), 0),
    (("baterias", 1, "eficiencia_descarga"), float("nan")),
    (("baterias", 1, "potencia_carga_max_w"), -1),
    (("baterias", 1, "limites_potencia"), "bateria"),
    (("irradiancia", "unidad"), "Wh"), (("datos", "catalogo"), None),
])
def test_configuraciones_invalidas(configuracion, ruta_campo, valor):
    config, ruta = configuracion
    contenedor = config
    for campo in ruta_campo[:-1]:
        contenedor = contenedor[campo]
    contenedor[ruta_campo[-1]] = valor
    ruta.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="configuracion.json"):
        cargar_configuracion(ruta)


@pytest.mark.parametrize("archivo,campo,valor", [
    ("demanda", "demanda_wh", float("nan")),
    ("demanda", "demanda_wh", -0.0000001),
    ("demanda", "demanda_wh", 10000),
    ("demanda", "carga_id", "desconocida"),
    ("demanda", "solicitada", 0),
    ("demanda", "prioridad_supuesta", 3.5),
    ("diaria", "objetivo_wh", 1),
    ("horaria", "demanda_total_wh", 0),
    ("catalogo", "permanente", "quizas"),
    ("calendario", "inicio_intervalo", "2025-01-06T01:00:00-05:00"),
    ("irradiancia_historica", "ghi_w_m2", float("inf")),
    ("irradiancia_historica", "ghi_w_m2", -1),
    ("irradiancia_historica", "irradiacion_wh_m2", 1000),
    ("irradiancia_historica", "inicio_utc", "2024-01-01T05:00:00"),
    ("irradiancia_historica", "fin_utc", "2024-01-01T07:00:00+00:00"),
    ("irradiancia_historica", "modelo", "Best Match"),
    ("irradiancia_simulacion", "particion", "evaluacion"),
    ("irradiancia_simulacion", "t", 1),
])
def test_csv_corrupto_identifica_archivo_y_campo(configuracion, archivo, campo, valor):
    config, ruta = configuracion
    tabla = pd.read_csv(config["datos"][archivo], keep_default_na=False)
    tabla[campo] = tabla[campo].astype(object)
    tabla.loc[0, campo] = valor
    alterada = ruta.parent / Path(config["datos"][archivo]).name
    tabla.to_csv(alterada, index=False)
    config["datos"][archivo] = str(alterada)
    ruta.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError) as error:
        cargar_caso(ruta)
    assert alterada.name in str(error.value)
    assert campo in str(error.value)


@pytest.mark.parametrize("cambio", ["duplicado", "faltante", "columna", "archivo"])
def test_historico_incompleto_o_duplicado(configuracion, cambio):
    config, ruta = configuracion
    tabla = pd.read_csv(config["datos"]["irradiancia_historica"])
    if cambio == "duplicado":
        tabla.iloc[1] = tabla.iloc[0]
    elif cambio == "faltante":
        tabla = tabla.iloc[:-1]
    elif cambio == "columna":
        tabla = tabla.drop(columns="ghi_w_m2")
    alterada = ruta.parent / "solar_roto.csv"
    if cambio != "archivo":
        tabla.to_csv(alterada, index=False)
    config["datos"]["irradiancia_historica"] = str(alterada)
    ruta.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="solar_roto.csv"):
        cargar_caso(ruta)


def test_solar_simulacion_debe_coincidir_con_historico(configuracion):
    config, ruta = configuracion
    tabla = pd.read_csv(config["datos"]["irradiancia_simulacion"])
    tabla.loc[0, ["ghi_w_m2", "irradiacion_wh_m2"]] = 1
    alterada = ruta.parent / "simulacion.csv"
    tabla.to_csv(alterada, index=False)
    config["datos"]["irradiancia_simulacion"] = str(alterada)
    ruta.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="subconjunto"):
        cargar_caso(ruta)


def test_cli_y_resumen(tmp_path):
    salida = tmp_path / "informe.json"
    proceso = subprocess.run([sys.executable, "-m", "energy_project.cli", "--config", str(CONFIG),
                              "--salida", str(salida)], capture_output=True, encoding="utf-8")
    assert proceso.returncode == 0, proceso.stderr
    informe = json.loads(salida.read_text(encoding="utf-8"))
    assert informe["conteos"]["horas_simulacion"] == 2016
    assert informe["max_error_conservacion_wh"] < 0.001
    assert informe["semillas"] == {"demanda": 20261018, "escenarios": 20261019}
    assert len(informe["entradas_sha256"]) == 10
    proceso = subprocess.run([sys.executable, "-m", "energy_project.cli", "--config", str(tmp_path / "no_existe.json")],
                             capture_output=True, encoding="utf-8")
    assert proceso.returncode == 1
    assert "no_existe.json" in proceso.stderr
    assert "Traceback" not in proceso.stderr


def test_informe_no_sobrescribe_entradas(configuracion):
    _, ruta = configuracion
    antes = ruta.read_bytes()
    proceso = subprocess.run([sys.executable, "-m", "energy_project.cli", "--config", str(ruta),
                              "--salida", str(ruta)], capture_output=True, encoding="utf-8")
    assert proceso.returncode == 1
    assert "no puede reemplazar" in proceso.stderr
    assert ruta.read_bytes() == antes
