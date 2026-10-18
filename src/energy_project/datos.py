"""Ingesta del caso semisintético: valida las entradas sin reescribirlas."""

from dataclasses import dataclass
import math
from pathlib import Path

import pandas as pd

from .configuracion import ARCHIVOS, PERIODOS, cargar_configuracion
from .demanda import (
    filas_intervalos, filas_lecturas, generar_calendario, generar_demanda,
)

PARTICIONES = {"desarrollo_sintetico": "desarrollo", "evaluacion_sintetica": "evaluacion"}
COLUMNAS_SOLARES = (
    "inicio_intervalo", "fin_intervalo", "inicio_utc", "fin_utc", "duracion_h",
    "ghi_w_m2", "irradiacion_wh_m2", "fuente", "modelo", "particion",
)


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


def exigir(condicion, ruta, campo, mensaje):
    if isinstance(condicion, pd.Series):
        fallos = ~condicion.fillna(False)
        if not fallos.any():
            return
        fila = int(fallos[fallos].index[0]) + 2
        raise ValueError(f"{ruta}: fila {fila}, {campo}: {mensaje}")
    if not condicion:
        raise ValueError(f"{ruta}: {campo}: {mensaje}")


def leer_csv(ruta, columnas):
    try:
        tabla = pd.read_csv(ruta, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, ValueError, pd.errors.ParserError) as error:
        raise ValueError(f"{ruta}: no se pudo leer el CSV: {error}") from error
    faltantes = set(columnas) - set(tabla.columns)
    exigir(not faltantes, ruta, "columnas", f"faltan {sorted(faltantes)}")
    exigir(not tabla.empty, ruta, "filas", "tabla vacía")
    return tabla


def numerica(tabla, campo, ruta):
    serie = pd.to_numeric(tabla[campo], errors="coerce")
    exigir(serie.map(math.isfinite) & ~tabla[campo].map(lambda v: isinstance(v, bool)),
           ruta, campo, "se requiere un número finito, sin faltantes")
    tabla[campo] = serie
    return serie


def comparar(tabla, esperada, ruta, tolerancia=1e-6):
    """Contrasta el snapshot con su configuración y semilla, sin modificar archivos."""
    exigir(len(tabla) == len(esperada), ruta, "filas",
           f"se esperaban {len(esperada)}; se encontraron {len(tabla)}")
    for campo in esperada:
        referencia = esperada[campo]
        if pd.api.types.is_bool_dtype(referencia):
            valores = tabla[campo].astype(str)
            exigir(valores.isin(["True", "False"]), ruta, campo, "booleano inválido")
            tabla[campo] = valores.map({"True": True, "False": False})
            coincide = tabla[campo].eq(referencia)
        elif pd.api.types.is_numeric_dtype(referencia):
            valores = numerica(tabla, campo, ruta)
            if pd.api.types.is_integer_dtype(referencia):
                exigir(valores.mod(1).eq(0), ruta, campo, "se requiere un entero")
            coincide = (valores - referencia).abs().le(tolerancia)
        else:
            coincide = tabla[campo].astype(str).eq(referencia.astype(str))
        exigir(coincide, ruta, campo, "difiere de la configuración, calendario o semilla declarados")


def fechas(tabla, campo, ruta, zona="UTC"):
    valores = tabla[campo].astype(str)
    exigir(valores.str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", regex=True),
           ruta, campo, "fecha sin zona horaria explícita")
    convertidas = pd.to_datetime(valores, utc=True, format="mixed", errors="coerce")
    exigir(convertidas.notna(), ruta, campo, "fecha ISO 8601 inválida")
    tabla[campo] = convertidas.dt.tz_convert(zona)


def validar_solar(tabla, ruta, config, inicio, fin):
    for campo in ("duracion_h", "ghi_w_m2", "irradiacion_wh_m2"):
        numerica(tabla, campo, ruta)
    exigir(tabla["duracion_h"].eq(1), ruta, "duracion_h", "solo se admite 1 h")
    for campo in ("ghi_w_m2", "irradiacion_wh_m2"):
        exigir(tabla[campo].ge(0), ruta, campo, "valor negativo")
    exigir((tabla["irradiacion_wh_m2"] - tabla["ghi_w_m2"] * tabla["duracion_h"]).abs().le(1e-6),
           ruta, "irradiacion_wh_m2", "no coincide con GHI (W/m²) × duración (h)")
    for campo in ("inicio_intervalo", "fin_intervalo", "inicio_utc", "fin_utc"):
        fechas(tabla, campo, ruta, config["zona_horaria"] if "intervalo" in campo else "UTC")
    exigir(tabla["inicio_intervalo"].eq(tabla["inicio_utc"]), ruta, "inicio_utc", "no equivale al inicio local")
    exigir(tabla["fin_intervalo"].eq(tabla["fin_utc"]), ruta, "fin_utc", "no equivale al fin local")
    exigir((tabla["fin_utc"] - tabla["inicio_utc"]).eq(pd.Timedelta(hours=1)),
           ruta, "fin_utc", "intervalo distinto de una hora")
    esperadas = pd.date_range(inicio, fin, inclusive="left", freq="h").tz_convert("UTC")
    exigir(len(tabla) == len(esperadas), ruta, "cobertura", f"se requieren {len(esperadas)} horas")
    exigir(tabla["inicio_utc"].eq(pd.Series(esperadas)), ruta, "inicio_utc",
           "intervalo desordenado, duplicado, faltante o fuera del periodo")
    particion = pd.Series("", index=tabla.index)
    for nombre in PERIODOS:
        periodo = config["periodos"][nombre]
        mascara = tabla["inicio_utc"].ge(pd.Timestamp(periodo["inicio"])) & tabla["inicio_utc"].lt(pd.Timestamp(periodo["fin"]))
        particion.loc[mascara] = nombre
    exigir(tabla["particion"].eq(particion), ruta, "particion", "no coincide con los cortes temporales")
    for campo in ("fuente", "modelo"):
        exigir(tabla[campo].eq(config["irradiancia"][campo]), ruta, campo, "procedencia distinta de la declarada")
    # Hipótesis retrospectiva del experimento; no es el retraso de publicación de ERA5.
    tabla["available_at"] = tabla["fin_utc"]


def cargar_caso(ruta_config):
    """Carga y valida los nueve CSV; conserva el estado individual de cada batería.

    La reconstrucción en memoria verifica la demanda sintética contra su semilla.
    No descarga, interpola, recorta ni regenera archivos durante la ingesta.
    """
    ruta_config = Path(ruta_config).resolve()
    config = cargar_configuracion(ruta_config)
    rutas = {clave: (ruta_config.parent / config["datos"][clave]).resolve() for clave in ARCHIVOS}
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
    detalle = tablas["demanda"]
    exigir(~detalle.duplicated(["t", "carga_id"]), rutas["demanda"], "t/carga_id", "clave repetida")
    exigir(detalle["demanda_wh"].ge(0), rutas["demanda"], "demanda_wh", "demanda negativa")
    capacidad = detalle["potencia_w_supuesta"] * detalle["horas_disponibles"]
    exigir(detalle["demanda_wh"].le(capacidad + 1e-6), rutas["demanda"], "demanda_wh", "supera potencia × horas disponibles")
    exigir(detalle["solicitada"].eq(detalle["demanda_wh"].gt(0).astype(int)),
           rutas["demanda"], "solicitada", "debe indicar si demanda_wh > 0")
    agregada = detalle.groupby("t")["demanda_wh"].sum().reset_index(drop=True)
    exigir((agregada - tablas["horaria"]["demanda_total_wh"]).abs().le(1e-6),
           rutas["horaria"], "demanda_total_wh", "no concilia con las cargas")
    totales = detalle.groupby(detalle["t"] // 24)["demanda_wh"].sum().reset_index(drop=True)
    exigir((totales - tablas["diaria"]["objetivo_wh"]).abs().le(0.001),
           rutas["diaria"], "objetivo_wh", "no conserva el objetivo diario dentro de 0,001 Wh")
    exigir((totales - tablas["diaria"]["demanda_total_wh"]).abs().le(0.001),
           rutas["diaria"], "demanda_total_wh", "no concilia con las cargas")
    calendario = tablas["calendario"]
    for campo in ("inicio_intervalo", "fin_intervalo"):
        fechas(calendario, campo, rutas["calendario"], config["zona_horaria"])
    calendario["inicio_utc"] = calendario["inicio_intervalo"].dt.tz_convert("UTC")
    calendario["fin_utc"] = calendario["fin_intervalo"].dt.tz_convert("UTC")
    calendario["particion"] = calendario["particion_demanda"].map(PARTICIONES)
    for nombre in ("demanda", "horaria", "diaria"):
        tablas[nombre]["particion"] = tablas[nombre]["particion"].map(PARTICIONES)
    periodos = config["periodos"]
    for nombre, inicio, fin in (
        ("irradiancia_historica", periodos["historial_previo"]["inicio"], periodos["reserva_no_usada"]["fin"]),
        ("irradiancia_simulacion", periodos["desarrollo"]["inicio"], periodos["evaluacion"]["fin"]),
    ):
        tabla = leer_csv(rutas[nombre], COLUMNAS_SOLARES + (("t",) if nombre == "irradiancia_simulacion" else ()))
        validar_solar(tabla, rutas[nombre], config, inicio, fin)
        tablas[nombre] = tabla
    historico = tablas["irradiancia_historica"]
    solar = tablas["irradiancia_simulacion"]
    numerica(solar, "t", rutas["irradiancia_simulacion"])
    exigir(solar["t"].eq(calendario["t"]), rutas["irradiancia_simulacion"], "t", "no coincide con calendario")
    ventana = historico.loc[historico["inicio_utc"].ge(pd.Timestamp(periodos["desarrollo"]["inicio"]))
                            & historico["inicio_utc"].lt(pd.Timestamp(periodos["evaluacion"]["fin"]))].reset_index(drop=True)
    for campo in COLUMNAS_SOLARES:
        exigir(solar[campo].eq(ventana[campo]), rutas["irradiancia_simulacion"], campo,
               "no coincide con el subconjunto del histórico; no concatenar ambos archivos")
    rutas["configuracion"] = ruta_config
    return CasoDatos(configuracion=config, rutas=rutas, **tablas)


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
