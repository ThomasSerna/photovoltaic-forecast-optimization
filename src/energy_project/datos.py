"""Ingesta del caso semisintético: valida las entradas sin reescribirlas.

cargar_caso lee los nueve CSV y contrasta la demanda y el calendario con lo que regeneran la
configuración y la semilla; las series solares se validan por su propia estructura y la de
simulación debe ser un subconjunto exacto del histórico. Las funciones `exigir*` lanzan
ValueError con el archivo, el campo y, si procede, la primera fila que falla.
"""

from dataclasses import dataclass
import math
from pathlib import Path

import pandas as pd

from .configuracion import ARCHIVOS, HORAS_DIA, PERIODOS, cargar_configuracion
from .demanda import (
    PARTICION_DESARROLLO, PARTICION_EVALUACION, TOLERANCIA_CONSERVACION_WH, TOLERANCIA_REDONDEO_WH,
    filas_intervalos, filas_lecturas, generar_calendario, generar_demanda,
)

PARTICIONES = {PARTICION_DESARROLLO: "desarrollo", PARTICION_EVALUACION: "evaluacion"}
COLUMNAS_SOLARES = (
    "inicio_intervalo", "fin_intervalo", "inicio_utc", "fin_utc", "duracion_h",
    "ghi_w_m2", "irradiacion_wh_m2", "fuente", "modelo", "particion",
)
TOLERANCIA_NUMERICA = 1e-6  # diferencia absoluta aceptada al comparar valores leídos con los esperados


@dataclass
class CasoDatos:
    """Una demanda programada compartida; meteorología separada de las cargas."""

    configuracion: dict
    rutas: dict[str, Path]
    catalogo: pd.DataFrame
    demanda: pd.DataFrame
    diaria: pd.DataFrame
    horaria: pd.DataFrame
    lecturas: pd.DataFrame
    intervalos: pd.DataFrame
    calendario: pd.DataFrame
    irradiancia_historica: pd.DataFrame
    irradiancia_simulacion: pd.DataFrame


# --- 1. Comprobaciones y conversiones de columnas ---

def exigir(condicion, ruta, campo, mensaje):
    """Falla con `ruta: campo: mensaje` si la condición (un booleano) no se cumple."""
    if not condicion:
        raise ValueError(f"{ruta}: {campo}: {mensaje}")


def exigir_filas(condicion, ruta, campo, mensaje):
    """Falla indicando la primera fila del CSV (contando el encabezado) que no cumple la condición.

    `condicion` es una Serie booleana con un valor por fila; un valor faltante cuenta como fallo.
    """
    fallos = ~condicion.fillna(False)
    if fallos.any():
        fila = int(fallos[fallos].index[0]) + 2
        raise ValueError(f"{ruta}: fila {fila}, {campo}: {mensaje}")


def leer_csv(ruta, columnas):
    """Lee un CSV completo (los textos vacíos no se vuelven NaN); exige las columnas dadas y alguna fila."""
    try:
        tabla = pd.read_csv(ruta, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, ValueError, pd.errors.ParserError) as error:
        raise ValueError(f"{ruta}: no se pudo leer el CSV: {error}") from error
    faltantes = set(columnas) - set(tabla.columns)
    exigir(not faltantes, ruta, "columnas", f"faltan {sorted(faltantes)}")
    exigir(not tabla.empty, ruta, "filas", "tabla vacía")
    return tabla


def convertir_numerica(tabla, campo, ruta):
    """Convierte la columna a número (modifica la tabla); exige valores finitos, sin booleanos ni faltantes."""
    serie = pd.to_numeric(tabla[campo], errors="coerce")
    exigir_filas(serie.map(math.isfinite) & ~tabla[campo].map(lambda v: isinstance(v, bool)),
                 ruta, campo, "se requiere un número finito, sin faltantes")
    tabla[campo] = serie
    return serie


def convertir_fechas(tabla, campo, ruta, zona="UTC"):
    """Convierte la columna a fechas en `zona` (modifica la tabla); exige ISO 8601 con zona horaria explícita."""
    valores = tabla[campo].astype(str)
    exigir_filas(valores.str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", regex=True),
                 ruta, campo, "fecha sin zona horaria explícita")
    convertidas = pd.to_datetime(valores, utc=True, format="mixed", errors="coerce")
    exigir_filas(convertidas.notna(), ruta, campo, "fecha ISO 8601 inválida")
    tabla[campo] = convertidas.dt.tz_convert(zona)


def en_periodo(instantes, inicio, fin):
    """Máscara de los instantes dentro de [inicio, fin); los extremos son texto ISO con zona."""
    return (instantes >= pd.Timestamp(inicio)) & (instantes < pd.Timestamp(fin))


def particion_por_periodo(instantes, periodos):
    """Nombre del periodo de PERIODOS que contiene cada instante (extremo derecho exclusivo), o "" si ninguno."""
    particion = pd.Series("", index=instantes.index)
    for nombre in PERIODOS:
        particion.loc[en_periodo(instantes, periodos[nombre]["inicio"], periodos[nombre]["fin"])] = nombre
    return particion


def energia_diaria_wh(detalle):
    """Energía solicitada por día (Wh): suma de demanda_wh de todas las cargas y horas, un valor por día."""
    return detalle.groupby(detalle["t"] // HORAS_DIA)["demanda_wh"].sum().reset_index(drop=True)


# --- 2. Demanda y calendario: se contrastan con lo que regeneran la configuración y la semilla ---

def comparar(tabla, esperada, ruta):
    """Contrasta cada columna leída con la regenerada desde la configuración y la semilla.

    Convierte las columnas booleanas y numéricas a su tipo (modifica la tabla); no escribe archivos.
    """
    exigir(len(tabla) == len(esperada), ruta, "filas", f"se esperaban {len(esperada)}; se encontraron {len(tabla)}")
    for campo in esperada:
        referencia = esperada[campo]
        if pd.api.types.is_bool_dtype(referencia):
            coincide = _coincide_booleana(tabla, campo, referencia, ruta)
        elif pd.api.types.is_numeric_dtype(referencia):
            coincide = _coincide_numerica(tabla, campo, referencia, ruta)
        else:
            coincide = tabla[campo].astype(str) == referencia.astype(str)
        exigir_filas(coincide, ruta, campo, "difiere de la configuración, calendario o semilla declarados")


def _coincide_booleana(tabla, campo, referencia, ruta):
    valores = tabla[campo].astype(str)
    exigir_filas(valores.isin(["True", "False"]), ruta, campo, "booleano inválido")
    tabla[campo] = valores.map({"True": True, "False": False})
    return tabla[campo] == referencia


def _coincide_numerica(tabla, campo, referencia, ruta):
    valores = convertir_numerica(tabla, campo, ruta)
    if pd.api.types.is_integer_dtype(referencia):
        exigir_filas(valores % 1 == 0, ruta, campo, "se requiere un entero")
    return (valores - referencia).abs() <= TOLERANCIA_NUMERICA


def leer_tablas_de_demanda(config, rutas):
    """Lee las siete tablas regenerables (catálogo, demanda, calendario...) y las contrasta con su regeneración."""
    horaria, demanda, diaria = generar_demanda(config)
    esperadas = dict(catalogo=config["cargas"], demanda=demanda, horaria=horaria, diaria=diaria,
                     lecturas=filas_lecturas(config["lecturas"]), intervalos=filas_intervalos(config["lecturas"]),
                     calendario=generar_calendario(config))
    tablas = {}
    for nombre, filas in esperadas.items():
        referencia = pd.DataFrame(filas)
        tabla = leer_csv(rutas[nombre], referencia.columns)
        comparar(tabla, referencia, rutas[nombre])
        tablas[nombre] = tabla
    return tablas


def validar_balance_de_demanda(tablas, rutas):
    """Claves únicas, límites por carga-hora y conciliación de los totales horario y diario con las cargas."""
    detalle, ruta = tablas["demanda"], rutas["demanda"]
    exigir_filas(~detalle.duplicated(["t", "carga_id"]), ruta, "t/carga_id", "clave repetida")
    exigir_filas(detalle["demanda_wh"] >= 0, ruta, "demanda_wh", "demanda negativa")
    capacidad_wh = detalle["potencia_w_supuesta"] * detalle["horas_disponibles"]
    exigir_filas(detalle["demanda_wh"] <= capacidad_wh + TOLERANCIA_REDONDEO_WH,
                 ruta, "demanda_wh", "supera potencia × horas disponibles")
    exigir_filas(detalle["solicitada"] == (detalle["demanda_wh"] > 0).astype(int),
                 ruta, "solicitada", "debe indicar si demanda_wh > 0")
    por_hora_wh = detalle.groupby("t")["demanda_wh"].sum().reset_index(drop=True)
    exigir_filas((por_hora_wh - tablas["horaria"]["demanda_total_wh"]).abs() <= TOLERANCIA_NUMERICA,
                 rutas["horaria"], "demanda_total_wh", "no concilia con las cargas")
    por_dia_wh = energia_diaria_wh(detalle)
    exigir_filas((por_dia_wh - tablas["diaria"]["objetivo_wh"]).abs() <= TOLERANCIA_CONSERVACION_WH,
                 rutas["diaria"], "objetivo_wh", "no conserva el objetivo diario dentro de 0,001 Wh")
    exigir_filas((por_dia_wh - tablas["diaria"]["demanda_total_wh"]).abs() <= TOLERANCIA_CONSERVACION_WH,
                 rutas["diaria"], "demanda_total_wh", "no concilia con las cargas")


def preparar_calendario(calendario, ruta, config):
    """Añade al calendario leído los tiempos UTC y la partición con los nombres de la ingesta (modifica la tabla)."""
    for campo in ("inicio_intervalo", "fin_intervalo"):
        convertir_fechas(calendario, campo, ruta, config["zona_horaria"])
    calendario["inicio_utc"] = calendario["inicio_intervalo"].dt.tz_convert("UTC")
    calendario["fin_utc"] = calendario["fin_intervalo"].dt.tz_convert("UTC")
    calendario["particion"] = calendario["particion_demanda"].map(PARTICIONES)


# --- 3. Series solares: se validan por su propia estructura ---

def validar_valores_solares(tabla, ruta):
    """Duración de 1 h, GHI y energía no negativas, y energía = GHI (W/m²) × duración (h)."""
    for campo in ("duracion_h", "ghi_w_m2", "irradiacion_wh_m2"):
        convertir_numerica(tabla, campo, ruta)
    exigir_filas(tabla["duracion_h"] == 1, ruta, "duracion_h", "solo se admite 1 h")
    for campo in ("ghi_w_m2", "irradiacion_wh_m2"):
        exigir_filas(tabla[campo] >= 0, ruta, campo, "valor negativo")
    energia_wh_m2 = tabla["ghi_w_m2"] * tabla["duracion_h"]
    exigir_filas((tabla["irradiacion_wh_m2"] - energia_wh_m2).abs() <= TOLERANCIA_NUMERICA,
                 ruta, "irradiacion_wh_m2", "no coincide con GHI (W/m²) × duración (h)")


def validar_tiempos_solares(tabla, ruta, config, inicio, fin):
    """Fechas con zona explícita, intervalos de 1 h y cobertura horaria exacta de [inicio, fin), en orden."""
    for campo in ("inicio_intervalo", "fin_intervalo", "inicio_utc", "fin_utc"):
        convertir_fechas(tabla, campo, ruta, config["zona_horaria"] if "intervalo" in campo else "UTC")
    exigir_filas(tabla["inicio_intervalo"] == tabla["inicio_utc"], ruta, "inicio_utc", "no equivale al inicio local")
    exigir_filas(tabla["fin_intervalo"] == tabla["fin_utc"], ruta, "fin_utc", "no equivale al fin local")
    exigir_filas((tabla["fin_utc"] - tabla["inicio_utc"]) == pd.Timedelta(hours=1),
                 ruta, "fin_utc", "intervalo distinto de una hora")
    esperadas = pd.date_range(inicio, fin, inclusive="left", freq="h").tz_convert("UTC")
    exigir(len(tabla) == len(esperadas), ruta, "cobertura", f"se requieren {len(esperadas)} horas")
    exigir_filas(tabla["inicio_utc"] == pd.Series(esperadas), ruta, "inicio_utc",
                 "intervalo desordenado, duplicado, faltante o fuera del periodo")


def validar_procedencia_solar(tabla, ruta, config):
    """Partición según los cortes de config["periodos"], y fuente y modelo declarados."""
    exigir_filas(tabla["particion"] == particion_por_periodo(tabla["inicio_utc"], config["periodos"]),
                 ruta, "particion", "no coincide con los cortes temporales")
    for campo in ("fuente", "modelo"):
        exigir_filas(tabla[campo] == config["irradiancia"][campo], ruta, campo, "procedencia distinta de la declarada")


def validar_solar(tabla, ruta, config, inicio, fin):
    """Valida una serie solar horaria de [inicio, fin) y le añade `available_at` (modifica la tabla)."""
    validar_valores_solares(tabla, ruta)
    validar_tiempos_solares(tabla, ruta, config, inicio, fin)
    validar_procedencia_solar(tabla, ruta, config)
    # Hipótesis retrospectiva del experimento; no es el retraso de publicación de ERA5.
    tabla["available_at"] = tabla["fin_utc"]


def leer_series_solares(config, rutas):
    """Lee y valida el histórico completo y la serie de simulación, que añade la columna t."""
    periodos = config["periodos"]
    historico = leer_csv(rutas["irradiancia_historica"], COLUMNAS_SOLARES)
    validar_solar(historico, rutas["irradiancia_historica"], config,
                  periodos["historial_previo"]["inicio"], periodos["reserva_no_usada"]["fin"])
    simulacion = leer_csv(rutas["irradiancia_simulacion"], COLUMNAS_SOLARES + ("t",))
    validar_solar(simulacion, rutas["irradiancia_simulacion"], config,
                  periodos["desarrollo"]["inicio"], periodos["evaluacion"]["fin"])
    return historico, simulacion


def validar_simulacion_solar(historico, simulacion, calendario, periodos, ruta):
    """La serie de simulación sigue al calendario (t) y es exactamente el subconjunto del histórico."""
    convertir_numerica(simulacion, "t", ruta)
    exigir_filas(simulacion["t"] == calendario["t"], ruta, "t", "no coincide con calendario")
    en_simulacion = en_periodo(historico["inicio_utc"], periodos["desarrollo"]["inicio"], periodos["evaluacion"]["fin"])
    ventana = historico.loc[en_simulacion].reset_index(drop=True)
    for campo in COLUMNAS_SOLARES:
        exigir_filas(simulacion[campo] == ventana[campo], ruta, campo,
                     "no coincide con el subconjunto del histórico; no concatenar ambos archivos")


# --- 4. Caso completo y consulta del historial ---

def cargar_caso(ruta_config):
    """Carga y valida los nueve CSV; conserva el estado individual de cada batería.

    La reconstrucción en memoria verifica la demanda sintética contra su semilla.
    No descarga, interpola, recorta ni regenera archivos durante la ingesta.
    """
    ruta_config = Path(ruta_config).resolve()
    config = cargar_configuracion(ruta_config)
    rutas = {clave: (ruta_config.parent / config["datos"][clave]).resolve() for clave in ARCHIVOS}
    tablas = leer_tablas_de_demanda(config, rutas)
    validar_balance_de_demanda(tablas, rutas)
    preparar_calendario(tablas["calendario"], rutas["calendario"], config)
    for nombre in ("demanda", "horaria", "diaria"):
        tablas[nombre]["particion"] = tablas[nombre]["particion"].map(PARTICIONES)
    historico, simulacion = leer_series_solares(config, rutas)
    validar_simulacion_solar(historico, simulacion, tablas["calendario"], config["periodos"],
                             rutas["irradiancia_simulacion"])
    rutas["configuracion"] = ruta_config
    return CasoDatos(configuracion=config, rutas=rutas, irradiancia_historica=historico,
                     irradiancia_simulacion=simulacion, **tablas)


def historial_admisible(historico, origen):
    """Solo observaciones terminadas y disponibles antes o en el origen (inclusivo)."""
    try:
        origen = pd.Timestamp(origen)
    except (TypeError, ValueError) as error:
        raise ValueError("origen: se requiere una fecha con zona horaria") from error
    if pd.isna(origen) or origen.tzinfo is None:
        raise ValueError("origen: se requiere una fecha con zona horaria")
    if not {"fin_utc", "available_at"} <= set(historico.columns):
        raise ValueError("histórico: faltan fin_utc o available_at; usar cargar_caso")
    mascara = historico["fin_utc"].le(origen) & historico["available_at"].le(origen)
    return historico.loc[mascara].copy()
