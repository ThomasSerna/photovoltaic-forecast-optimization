"""Configuración del experimento: supuestos fijos del código y validación de configuracion.json.

No ejecuta el simulador energético. `validar_demanda` exige que el JSON coincida con los
supuestos fijos de abajo; `validar_sistema` comprueba los dominios del resto de parámetros.
"""

from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Supuestos fijos del código; validar_demanda exige que configuracion.json coincida.
RESOLUCION_H = 1
CLASES_INICIO_H = 7
CLASES_FIN_H = 12.5
DIAS_LECTIVOS = [0, 1, 2, 3, 4]  # índices de DIAS_SEMANA: lunes a viernes
DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_POR_SEMANA = len(DIAS_SEMANA)
HORAS_DIA = 24
HORAS_CON_CLASE = range(CLASES_INICIO_H, math.ceil(CLASES_FIN_H))  # 7..12: horas que cruzan las clases
ID_NEVERA = "nevera_01"  # única carga permanente a la que se asigna energía

PERIODOS = ("historial_previo", "desarrollo", "evaluacion", "reserva_no_usada")
# Regla de selección de cargas (IDEA §4.4): "prioridad" reproduce T03–T05 y es la
# predeterminada si falta la clave; "mochila" es una adaptación opcional del proyecto.
SELECCIONES = ("prioridad", "mochila")
ARCHIVOS = (
    "catalogo", "demanda", "diaria", "horaria", "lecturas", "intervalos",
    "calendario", "irradiancia_historica", "irradiancia_simulacion",
)


def numero(valor, campo, minimo=0, maximo=math.inf, estricto=False, entero=False):
    """Devuelve valor si es un número finito (no booleano) del dominio; si no, ValueError.

    El dominio es [minimo, maximo], o (minimo, maximo] con estricto=True. entero=True exige int.
    """
    if (isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor)
            or (valor <= minimo if estricto else valor < minimo) or valor > maximo
            or (entero and not isinstance(valor, int))):
        tipo = "entero" if entero else "número finito"
        dominio = f"{'(' if estricto else '['}{minimo}, {maximo}{')' if maximo == math.inf else ']'}"
        raise ValueError(f"{campo}: se requiere un {tipo} en {dominio}; recibido {valor!r}")
    return valor


# --- Demanda: calendario escolar, consumos, cargas, lecturas del medidor y periodos ---

def validar_demanda(config):
    """Comprueba que la configuración respeta los supuestos fijos del código."""
    try:
        _validar_calendario(config)
        _validar_consumos(config)
        _validar_cargas(config["cargas"])
        _validar_lecturas(config["lecturas"])
        _validar_periodos(config)
    except ValueError as error:
        raise ValueError(f"configuracion: {error}") from error
    except (KeyError, TypeError, ZoneInfoNotFoundError) as error:
        raise ValueError(f"configuracion: campo faltante o inválido: {error}") from error


def _validar_calendario(config):
    """Resolución horaria, clases de lunes a viernes de 07:00 a 12:30 y semanas enteras."""
    if config["resolucion_h"] != RESOLUCION_H or isinstance(config["resolucion_h"], bool):
        raise ValueError("resolucion_h: solo se admite resolución horaria (1 h)")
    if config["clases_inicio_h"] != CLASES_INICIO_H or config["clases_fin_h"] != CLASES_FIN_H:
        raise ValueError("clases_inicio_h/clases_fin_h: solo se admiten clases de 07:00 a 12:30")
    if config["dias_lectivos"] != DIAS_LECTIVOS or any(type(dia) is not int for dia in config["dias_lectivos"]):
        raise ValueError("dias_lectivos: solo se admiten clases de lunes a viernes")
    for campo in ("semanas", "semanas_desarrollo", "semilla"):
        if type(config[campo]) is not int or config[campo] < 0:
            raise ValueError(f"{campo} debe ser un entero no negativo")
    if not 0 < config["semanas_desarrollo"] < config["semanas"]:
        raise ValueError("semanas_desarrollo/semanas: cada partición necesita al menos una semana")


def _validar_consumos(config):
    """Energía diaria de la nevera (Wh) y límites de los factores uniformes de variación."""
    numero(config["nevera_wh_dia"], "nevera_wh_dia", estricto=True)
    for campo in ("variacion_lectiva", "variacion_nevera"):
        limites = config[campo]
        if not isinstance(limites, list) or len(limites) != 2:
            raise ValueError(f"{campo} debe contener dos límites")
        for limite in limites:
            numero(limite, campo, estricto=True)
        if limites[0] > limites[1]:
            raise ValueError(f"{campo} tiene los límites invertidos")


def _validar_cargas(cargas):
    """Catálogo no vacío de equipos individuales con ids únicos; la nevera es la única permanente."""
    if not isinstance(cargas, list) or not cargas:
        raise ValueError("cargas debe ser una lista no vacía")
    ids = [carga["id"] for carga in cargas]
    if any(not isinstance(id_, str) or not id_.strip() for id_ in ids) or len(set(ids)) != len(ids):
        raise ValueError("cargas.id debe ser texto no vacío y único")
    if [carga["id"] for carga in cargas if carga["permanente"]] != [ID_NEVERA]:
        raise ValueError(f"cargas.permanente: solo {ID_NEVERA} puede ser permanente")
    for carga in cargas:
        _validar_carga(carga)


def _validar_carga(carga):
    """Una carga k: equipo individual con potencia P_k (W), ocupación base, prioridad y ventana de uso."""
    campo = f"cargas[{carga['id']}]"
    if type(carga["cantidad"]) is not int or carga["cantidad"] != 1:
        raise ValueError(f"{campo}.cantidad debe ser 1: catálogo de equipos individuales")
    if type(carga["permanente"]) is not bool:
        raise ValueError(f"{campo}.permanente debe ser booleano")
    for atributo in ("potencia_w_supuesta", "ocupacion_base_supuesta"):
        numero(carga[atributo], f"{campo}.{atributo}", estricto=True)
    if carga["ocupacion_base_supuesta"] > 1:
        raise ValueError(f"{campo}.ocupacion_base_supuesta debe ser <= 1")
    if type(carga["prioridad_supuesta"]) is not int or carga["prioridad_supuesta"] <= 0:
        raise ValueError(f"{campo}.prioridad_supuesta debe ser un entero positivo")
    _validar_ventana_de_uso(carga, campo)


def _validar_ventana_de_uso(carga, campo):
    """Ventana [inicio_h, fin_h) en horas del día: 24 h para la nevera; dentro de las clases para las demás."""
    numero(carga["inicio_h"], f"{campo}.inicio_h")
    numero(carga["fin_h"], f"{campo}.fin_h", estricto=True)
    if not carga["inicio_h"] < carga["fin_h"] <= HORAS_DIA:
        raise ValueError(f"{campo}: ventana inválida [inicio_h, fin_h)")
    if carga["permanente"]:
        if carga["inicio_h"] != 0 or carga["fin_h"] != HORAS_DIA:
            raise ValueError(f"{campo}: la nevera debe estar disponible las 24 h")
    elif not CLASES_INICIO_H <= carga["inicio_h"] < carga["fin_h"] <= CLASES_FIN_H:
        raise ValueError(f"{campo}: ventana fuera de clases de 07:00 a 12:30")


def _validar_lecturas(lecturas):
    """Cinco lecturas enteras del medidor (kWh), de lunes a viernes y estrictamente crecientes."""
    if not isinstance(lecturas, list) or len(lecturas) != 5:
        raise ValueError("lecturas: se esperan 5 lecturas del medidor")
    for indice, lectura in enumerate(lecturas):
        if lectura["dia"] != DIAS_SEMANA[indice]:
            raise ValueError(f"lecturas[{indice}].dia debe ser {DIAS_SEMANA[indice]}")
        valor = lectura["entero_kwh"]
        if type(valor) is not int or valor < 0:
            raise ValueError(f"lecturas[{indice}].entero_kwh debe ser un entero no negativo")
        if indice and valor <= lecturas[indice - 1]["entero_kwh"]:
            raise ValueError(f"lecturas[{indice}].entero_kwh debe ser estrictamente creciente")


def _validar_periodos(config):
    """Cuatro periodos locales contiguos, de medianoche a medianoche, alineados con las semanas."""
    inicio_fecha = _fecha_de_inicio(config)
    zona = ZoneInfo(config["zona_horaria"])
    periodos, fin_anterior = {}, None
    for nombre in PERIODOS:
        desde, hasta = [_medianoche_local(config, nombre, limite, zona) for limite in ("inicio", "fin")]
        if desde >= hasta or (fin_anterior is not None and fin_anterior != desde):
            raise ValueError(f"periodos.{nombre}: los periodos deben ser positivos y contiguos")
        periodos[nombre], fin_anterior = (desde, hasta), hasta
    inicio = datetime.combine(inicio_fecha, datetime.min.time(), zona)
    desarrollo, evaluacion = periodos["desarrollo"], periodos["evaluacion"]
    if (desarrollo[0] != inicio
            or desarrollo[1] != inicio + timedelta(weeks=config["semanas_desarrollo"])
            or evaluacion[1] != inicio + timedelta(weeks=config["semanas"])):
        raise ValueError("periodos desarrollo/evaluacion no coinciden con fecha_inicio_sintetica y semanas")


def _fecha_de_inicio(config):
    """Primer día simulado: fecha ISO que cae en lunes."""
    try:
        inicio = date.fromisoformat(config["fecha_inicio_sintetica"])
    except (TypeError, ValueError) as error:
        raise ValueError("fecha_inicio_sintetica debe ser una fecha ISO (YYYY-MM-DD)") from error
    if inicio.weekday() != 0:
        raise ValueError("fecha_inicio_sintetica debe ser lunes")
    return inicio


def _medianoche_local(config, nombre, limite, zona):
    """Extremo de un periodo: instante ISO con el desfase de la zona, exactamente a medianoche local."""
    try:
        fecha = datetime.fromisoformat(config["periodos"][nombre][limite])
    except ValueError as error:
        raise ValueError(f"periodos.{nombre}.{limite} debe ser ISO con zona horaria") from error
    if fecha.tzinfo is None or fecha.utcoffset() != fecha.astimezone(zona).utcoffset():
        raise ValueError(f"periodos.{nombre}.{limite} debe usar la zona {zona}")
    if fecha.hour or fecha.minute or fecha.second or fecha.microsecond:
        raise ValueError(f"periodos.{nombre}.{limite} debe ser medianoche local")
    return fecha


# --- Sistema: control, sitio, FV, baterías, archivos e irradiancia ---

def validar_sistema(config, carpeta):
    """Dominios de los parámetros del sistema; `carpeta` es la del JSON, base de las rutas de datos."""
    if config["zona_horaria"] != "America/Bogota":
        raise ValueError("zona_horaria: el caso usa America/Bogota")
    _validar_control(config)
    numero(config["latitud"], "latitud", -90, 90)
    numero(config["longitud"], "longitud", -180, 180)
    numero(config["potencia_bus_max_w"], "potencia_bus_max_w", estricto=True)
    _validar_pv(config["pv"])
    _validar_baterias(config["baterias"])
    _validar_archivos(config["datos"], carpeta)
    _validar_irradiancia(config["irradiancia"])


def _validar_control(config):
    """Horizonte, replanificación y escenarios (enteros positivos, replanificación ≤ horizonte) y semillas."""
    for campo in ("horizonte", "replanificacion", "escenarios"):
        numero(config[campo], campo, minimo=1, entero=True)
    if config["replanificacion"] > config["horizonte"]:
        raise ValueError("replanificacion: no puede superar horizonte")
    for campo in ("semilla", "semilla_escenarios"):
        numero(config[campo], campo, entero=True)
    numero(config["lambda_terminal"], "lambda_terminal")
    if config.get("seleccion_cargas", "prioridad") not in SELECCIONES:
        raise ValueError(f"seleccion_cargas: se admite {SELECCIONES}")


def _validar_pv(pv):
    """Arreglo FV: P_STC = paneles × potencia_panel_w, inversor, factor de desempeño f y geometría."""
    numero(pv["paneles"], "pv.paneles", minimo=1, entero=True)
    for campo in ("potencia_panel_w", "potencia_nominal_w", "inversor_max_w"):
        numero(pv[campo], f"pv.{campo}", estricto=True)
    numero(pv["factor_desempeno"], "pv.factor_desempeno", maximo=1, estricto=True)
    if not math.isclose(pv["paneles"] * pv["potencia_panel_w"], pv["potencia_nominal_w"]):
        raise ValueError("pv.potencia_nominal_w: debe ser paneles × potencia_panel_w")
    if pv["geometria"] != "horizontal_equivalente":
        raise ValueError("pv.geometria: solo se admite horizontal_equivalente")


def _validar_baterias(baterias):
    """Al menos una batería b individual, con identificador no vacío y único."""
    if not isinstance(baterias, list) or not baterias:
        raise ValueError("baterias: se requiere al menos una batería individual")
    ids = []
    for bateria in baterias:
        identificador = bateria["id"]
        if not isinstance(identificador, str) or not identificador.strip() or identificador in ids:
            raise ValueError(f"baterias.id: identificador vacío o repetido: {identificador!r}")
        ids.append(identificador)
        _validar_bateria(bateria, identificador)


def _validar_bateria(bateria, identificador):
    """Una batería: C_min ≤ C_0 ≤ C_max ≤ capacidad (Wh), potencias (W) y eficiencias en (0, 1]."""
    campo = f"baterias.{identificador}"
    for atributo in ("capacidad_wh", "minimo_wh", "maximo_wh", "inicial_wh"):
        numero(bateria[atributo], f"{campo}.{atributo}")
    if not (0 <= bateria["minimo_wh"] <= bateria["inicial_wh"]
            <= bateria["maximo_wh"] <= bateria["capacidad_wh"]
            and bateria["minimo_wh"] < bateria["maximo_wh"]):
        raise ValueError(f"{campo}: se requiere mínimo ≤ inicial ≤ máximo ≤ capacidad")
    for atributo in ("potencia_carga_max_w", "potencia_descarga_max_w"):
        numero(bateria[atributo], f"{campo}.{atributo}", estricto=True)
    for atributo in ("eficiencia_carga", "eficiencia_descarga"):
        numero(bateria[atributo], f"{campo}.{atributo}", maximo=1, estricto=True)
    if bateria["limites_potencia"] != "bus":
        raise ValueError(f"{campo}.limites_potencia: deben medirse en el bus")


def _validar_archivos(datos, carpeta):
    """Una ruta de archivo no vacía por tabla, todas distintas una vez resueltas respecto a `carpeta`."""
    for campo in ARCHIVOS:
        if not isinstance(datos[campo], str) or not datos[campo].strip():
            raise ValueError(f"datos.{campo}: se requiere una ruta de archivo")
    rutas = [(carpeta / datos[campo]).resolve() for campo in ARCHIVOS]
    if len(set(rutas)) != len(rutas):
        raise ValueError("datos: rutas de archivos repetidas")


def _validar_irradiancia(solar):
    """Procedencia fija de la irradiancia: Open-Meteo, ERA5, shortwave_radiation en W/m²."""
    for campo, valor in (("fuente", "Open-Meteo"), ("modelo", "ERA5"),
                         ("variable", "shortwave_radiation"), ("unidad", "W/m²"),
                         ("disponibilidad", "fin_intervalo_idealizada")):
        if solar[campo] != valor:
            raise ValueError(f"irradiancia.{campo}: se requiere {valor!r}")


def cargar_configuracion(ruta_config):
    """Lee JSON y valida parámetros; las rutas siguen siendo relativas al JSON."""
    ruta = Path(ruta_config).resolve()
    try:
        config = json.loads(ruta.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("la raíz debe ser un objeto JSON")
        validar_demanda(config)
        validar_sistema(config, ruta.parent)
        return config
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        raise ValueError(f"{ruta}: {error}") from error
