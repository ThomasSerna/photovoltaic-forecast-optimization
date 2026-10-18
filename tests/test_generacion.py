import copy
import csv
import json
from pathlib import Path

import pytest

from energy_project.configuracion import validar_demanda
from energy_project.demanda import generar_calendario, generar_demanda
from energy_project.generacion import generar_archivos


RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "scripts" / "configuracion.json"


def configuracion():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def leer_csv(ruta):
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def test_regeneracion_preserva_cinco_csv_y_es_reproducible(tmp_path):
    primera, segunda = tmp_path / "primera", tmp_path / "segunda"
    resumen = generar_archivos(CONFIG, primera)
    assert resumen["horas"] == 2016
    assert resumen["cargas_individuales"] == 13
    assert resumen["max_error_conservacion_wh"] <= 0.001
    for nombre in ("catalogo_cargas.csv", "demanda_por_carga.csv", "calendario_simulacion.csv",
                   "irradiancia_historica.csv", "irradiancia_simulacion.csv"):
        assert leer_csv(primera / nombre) == leer_csv(RAIZ / "data" / nombre)
    generar_archivos(CONFIG, segunda)
    for ruta in primera.iterdir():
        assert ruta.read_bytes() == (segunda / ruta.name).read_bytes()
    hashes = json.loads((primera / "sha256.json").read_text())
    assert len(hashes) == 9
    assert (primera / "irradiancia_historica.csv").read_bytes() == (RAIZ / "data" / "irradiancia_historica.csv").read_bytes()


def test_demanda_conserva_objetivos_y_no_modifica_configuracion():
    config = configuracion()
    original = copy.deepcopy(config)
    horaria, demanda, diaria = generar_demanda(config)
    assert config == original
    assert len(diaria) == 84
    assert len(demanda) == len(horaria) * len(config["cargas"])
    assert [fila["t"] for fila in horaria] == list(range(2016))
    assert diaria[0]["fecha"] == "2025-01-06"
    assert diaria[-1]["fecha"] == "2025-03-30"
    for dia, fila in enumerate(diaria):
        energia = sum(hora["demanda_total_wh"] for hora in horaria[dia * 24:(dia + 1) * 24])
        assert abs(energia - fila["objetivo_wh"]) <= 0.001


def test_calendario_mapea_cortes_y_fin_exclusivo():
    calendario = generar_calendario(configuracion())
    assert calendario[1343]["particion_demanda"] == "desarrollo_sintetico"
    assert calendario[1343]["fin_intervalo"] == "2025-03-03T00:00:00-05:00"
    assert calendario[1344]["particion_demanda"] == "evaluacion_sintetica"
    assert calendario[-1]["fin_intervalo"] == "2025-03-31T00:00:00-05:00"


@pytest.mark.parametrize(("campo", "valor"), [
    ("resolucion_h", 0.5), ("resolucion_h", True),
    ("semanas", 1.5), ("semanas_desarrollo", 12), ("semilla", True),
    ("nevera_wh_dia", float("nan")), ("nevera_wh_dia", float("inf")),
    ("nevera_wh_dia", -1), ("variacion_lectiva", [1.1, 0.9]),
    ("variacion_nevera", [0, 1]), ("variacion_nevera", [1, float("nan")]),
    ("dias_lectivos", [False, 1, 2, 3, 4]),
    ("fecha_inicio_sintetica", "2025-01-07"), ("fecha_inicio_sintetica", None),
])
def test_rechaza_configuracion_de_demanda_invalida(campo, valor):
    config = configuracion()
    config[campo] = valor
    with pytest.raises(ValueError, match=campo):
        validar_demanda(config)


@pytest.mark.parametrize(("campo", "valor"), [
    ("potencia_w_supuesta", 0), ("potencia_w_supuesta", float("nan")),
    ("inicio_h", 6), ("fin_h", 13), ("inicio_h", True),
    ("prioridad_supuesta", 0), ("prioridad_supuesta", 1.5),
    ("ocupacion_base_supuesta", 0), ("cantidad", 2), ("permanente", True),
])
def test_rechaza_parametros_de_carga_invalidos(campo, valor):
    config = configuracion()
    config["cargas"][1][campo] = valor
    with pytest.raises(ValueError, match="cargas"):
        validar_demanda(config)


def test_rechaza_lecturas_y_periodos_inconsistentes():
    config = configuracion()
    config["lecturas"][2]["entero_kwh"] = config["lecturas"][1]["entero_kwh"]
    with pytest.raises(ValueError, match="lecturas"):
        validar_demanda(config)
    config = configuracion()
    config["periodos"]["desarrollo"]["fin"] = "2025-03-02T00:00:00-05:00"
    with pytest.raises(ValueError, match="periodos"):
        validar_demanda(config)


def test_rechaza_demanda_incompatible_sin_recortarla():
    config = configuracion()
    config["nevera_wh_dia"] = 20000
    with pytest.raises(ValueError, match="Demanda incompatible"):
        generar_demanda(config)


def test_historico_incompleto_falla_antes_de_publicar_archivos(tmp_path):
    config = configuracion()
    historico = tmp_path / "historico_incompleto.csv"
    lineas = (RAIZ / "data" / "irradiancia_historica.csv").read_text(encoding="utf-8").splitlines()
    historico.write_text("\n".join(lineas[:3]) + "\n", encoding="utf-8")
    config["datos"]["irradiancia_historica"] = str(historico)
    ruta_config = tmp_path / "configuracion.json"
    ruta_config.write_text(json.dumps(config), encoding="utf-8")
    salida = tmp_path / "salida"
    with pytest.raises(ValueError, match="historico_incompleto.csv.*cobertura"):
        generar_archivos(ruta_config, salida)
    assert not salida.exists()


def test_generacion_respeta_rutas_individuales_sin_salida(tmp_path):
    config = configuracion()
    config["datos"] = {clave: str(tmp_path / "datos" / Path(valor).name)
                       for clave, valor in config["datos"].items()}
    config["datos"]["catalogo"] = str(tmp_path / "catalogo" / "catalogo_cargas.csv")
    config["datos"]["irradiancia_historica"] = str(RAIZ / "data/irradiancia_historica.csv")
    ruta_config = tmp_path / "configuracion.json"
    ruta_config.write_text(json.dumps(config), encoding="utf-8")
    generar_archivos(ruta_config)
    assert Path(config["datos"]["catalogo"]).exists()
    assert not (tmp_path / "datos/catalogo_cargas.csv").exists()
    from energy_project import cargar_caso
    assert len(cargar_caso(ruta_config).catalogo) == 13
