"""Configuración del experimento; no ejecuta el modelo energético de HU-02."""

import json
import math
from pathlib import Path

from .demanda import validar_configuracion as validar_demanda

PERIODOS = ("historial_previo", "desarrollo", "evaluacion", "reserva_no_usada")
ARCHIVOS = (
    "catalogo", "demanda", "diaria", "horaria", "lecturas", "intervalos",
    "calendario", "irradiancia_historica", "irradiancia_simulacion",
)


def numero(valor, campo, minimo=0, maximo=math.inf, entero=False):
    """Rechaza NaN, infinitos, booleanos y valores fuera del dominio declarado."""
    if (isinstance(valor, bool) or not isinstance(valor, (int, float))
            or not math.isfinite(valor) or not minimo <= valor <= maximo
            or (entero and not isinstance(valor, int))):
        raise ValueError(f"{campo}: valor numérico fuera de dominio: {valor!r}")
    return valor


def cargar_configuracion(ruta_config):
    """Lee JSON y valida parámetros; las rutas siguen siendo relativas al JSON."""
    ruta = Path(ruta_config).resolve()
    try:
        config = json.loads(ruta.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("la raíz debe ser un objeto JSON")
        validar_demanda(config)
        if config["zona_horaria"] != "America/Bogota":
            raise ValueError("zona_horaria: el caso usa America/Bogota")
        for campo in ("horizonte", "replanificacion", "escenarios"):
            numero(config[campo], campo, minimo=1, entero=True)
        if config["replanificacion"] > config["horizonte"]:
            raise ValueError("replanificacion: no puede superar horizonte")
        for campo in ("semilla", "semilla_escenarios"):
            numero(config[campo], campo, entero=True)
        numero(config["latitud"], "latitud", -90, 90)
        numero(config["longitud"], "longitud", -180, 180)
        numero(config["potencia_bus_max_w"], "potencia_bus_max_w", minimo=1e-12)
        pv = config["pv"]
        numero(pv["paneles"], "pv.paneles", minimo=1, entero=True)
        for campo in ("potencia_panel_w", "potencia_nominal_w", "inversor_max_w"):
            numero(pv[campo], f"pv.{campo}", minimo=1e-12)
        numero(pv["factor_desempeno"], "pv.factor_desempeno", minimo=1e-12, maximo=1)
        if not math.isclose(pv["paneles"] * pv["potencia_panel_w"], pv["potencia_nominal_w"]):
            raise ValueError("pv.potencia_nominal_w: debe ser paneles × potencia_panel_w")
        if pv["geometria"] != "horizontal_equivalente":
            raise ValueError("pv.geometria: solo se admite horizontal_equivalente")
        baterias = config["baterias"]
        if not isinstance(baterias, list) or not baterias:
            raise ValueError("baterias: se requiere al menos una batería individual")
        ids = []
        for bateria in baterias:
            identificador = bateria["id"]
            if not isinstance(identificador, str) or not identificador.strip() or identificador in ids:
                raise ValueError(f"baterias.id: identificador vacío o repetido: {identificador!r}")
            ids.append(identificador)
            for campo in ("capacidad_wh", "minimo_wh", "maximo_wh", "inicial_wh"):
                numero(bateria[campo], f"baterias.{identificador}.{campo}")
            if not (0 <= bateria["minimo_wh"] <= bateria["inicial_wh"]
                    <= bateria["maximo_wh"] <= bateria["capacidad_wh"]
                    and bateria["minimo_wh"] < bateria["maximo_wh"]):
                raise ValueError(f"baterias.{identificador}: se requiere mínimo ≤ inicial ≤ máximo ≤ capacidad")
            for campo in ("potencia_carga_max_w", "potencia_descarga_max_w"):
                numero(bateria[campo], f"baterias.{identificador}.{campo}", minimo=1e-12)
            for campo in ("eficiencia_carga", "eficiencia_descarga"):
                numero(bateria[campo], f"baterias.{identificador}.{campo}", minimo=1e-12, maximo=1)
            if bateria["limites_potencia"] != "bus":
                raise ValueError(f"baterias.{identificador}.limites_potencia: deben medirse en el bus")
        for campo in ARCHIVOS:
            if not isinstance(config["datos"][campo], str) or not config["datos"][campo].strip():
                raise ValueError(f"datos.{campo}: se requiere una ruta de archivo")
        rutas = [(ruta.parent / config["datos"][campo]).resolve() for campo in ARCHIVOS]
        if len(set(rutas)) != len(rutas):
            raise ValueError("datos: rutas de archivos repetidas")
        solar = config["irradiancia"]
        for campo, valor in (("fuente", "Open-Meteo"), ("modelo", "ERA5"),
                             ("variable", "shortwave_radiation"), ("unidad", "W/m²"),
                             ("disponibilidad", "fin_intervalo_idealizada")):
            if solar[campo] != valor:
                raise ValueError(f"irradiancia.{campo}: se requiere {valor!r}")
        return config
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        raise ValueError(f"{ruta}: {error}") from error
