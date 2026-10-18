"""Genera la demanda eléctrica SINTÉTICA y reproducible del Centro Educativo Rural El Rayo.

Ejecutar desde la raíz: uv run python scripts/generar_datos.py
Para cambiar los supuestos, modificar scripts/configuracion.json.

Lee la configuración indicada y escribe en data/ por defecto:
- demanda_horaria.csv, demanda_por_carga.csv y demanda_diaria.csv: demanda sintética (Wh).
- lecturas_medidor.csv e intervalos_medidor.csv: lecturas del medidor y sus diferencias.
- catalogo_cargas.csv: las cargas de configuracion.json.
- calendario_simulacion.csv e irradiancia_simulacion.csv: alineación con el histórico local.
- validacion.json (resumen de las comprobaciones) y sha256.json (hashes de los CSV).

El modelo está en demanda.py y sus fórmulas en docs/datos.md. Si la demanda no cabe en los
horarios y potencias supuestos, el script falla: no recorta la demanda ni añade cargas ocultas.
"""
import argparse
import csv
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

import pandas as pd

from .configuracion import cargar_configuracion
from .datos import COLUMNAS_SOLARES, leer_csv, validar_solar
from .demanda import (
    DECIMALES_WH, WH_POR_KWH, filas_intervalos, filas_lecturas, generar_calendario, generar_demanda,
)

CAMPOS_FECHA = ("inicio_intervalo", "fin_intervalo", "inicio_utc", "fin_utc")


# --- Histórico solar: se valida con la misma función que la ingesta y se copia como texto ---

def leer_texto_solar(ruta):
    """Filas del histórico solar como texto, para copiarlas sin reformatear; exige las columnas exactas."""
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        lector = csv.DictReader(archivo)
        if lector.fieldnames != list(COLUMNAS_SOLARES):
            raise ValueError(f"{ruta}: columnas esperadas {list(COLUMNAS_SOLARES)}; recibidas {lector.fieldnames}")
        return list(lector)


def comprobar_desfases(filas, ruta, zona):
    """Las columnas locales deben escribir el desfase de su zona y las UTC, +00:00.

    validar_solar compara instantes; como el generador copia el texto, exige además esta forma.
    """
    for numero, fila in enumerate(filas, 2):
        try:
            inicio, fin, inicio_utc, fin_utc = (datetime.fromisoformat(fila[campo]) for campo in CAMPOS_FECHA)
        except ValueError as error:
            raise ValueError(f"{ruta}: fila {numero}: fecha solar inválida") from error
        if (any(fecha.utcoffset() != fecha.astimezone(zona).utcoffset() for fecha in (inicio, fin))
                or any(fecha.utcoffset() != timedelta(0) for fecha in (inicio_utc, fin_utc))):
            raise ValueError(f"{ruta}: fila {numero}: tiempos UTC/locales incoherentes")


def ventana_solar(ruta, config, calendario):
    """Filas del histórico solar que cubren las horas del calendario, con su índice t y el texto original.

    El histórico completo se valida con datos.validar_solar, la misma función de la ingesta, y no se
    vuelven a desplazar sus intervalos.
    """
    filas = leer_texto_solar(ruta)
    periodos = config["periodos"]
    tabla = leer_csv(ruta, COLUMNAS_SOLARES)
    validar_solar(tabla, ruta, config, periodos["historial_previo"]["inicio"], periodos["reserva_no_usada"]["fin"])
    comprobar_desfases(filas, ruta, ZoneInfo(config["zona_horaria"]))
    por_inicio = dict(zip(tabla["inicio_utc"], filas))
    return [dict(t=hora["t"], **por_inicio[pd.Timestamp(hora["inicio_intervalo"])]) for hora in calendario]


# --- Resumen y escritura de archivos ---

def resumen_validacion(config, horaria, por_carga, diaria):
    """Tamaños, energía generada (kWh) y comprobaciones superadas por la demanda."""
    demanda_lectiva_wh = [fila["demanda_total_wh"] for fila in diaria if fila["es_lectivo"]]
    demanda_fin_semana_wh = [fila["demanda_total_wh"] for fila in diaria if not fila["es_lectivo"]]
    return dict(
        estado="VALIDADO_BAJO_SUPUESTOS_PROVISIONALES",
        horas=len(horaria),
        dias=len(diaria),
        cargas_individuales=len(config["cargas"]),
        filas_carga_hora=len(por_carga),
        total_kwh=round(sum(fila["demanda_total_wh"] for fila in diaria) / WH_POR_KWH, DECIMALES_WH),
        media_lectiva_kwh=round(mean(demanda_lectiva_wh) / WH_POR_KWH, DECIMALES_WH),
        media_fin_semana_kwh=round(mean(demanda_fin_semana_wh) / WH_POR_KWH, DECIMALES_WH),
        max_error_conservacion_wh=max(abs(fila["demanda_total_wh"] - fila["objetivo_wh"]) for fila in diaria),
        comprobaciones=[
            "Reproducibilidad exacta",
            "Sin huecos ni duplicados temporales",
            "Demanda no negativa",
            "Potencia y ventana de cada carga respetadas",
            "Solo nevera fuera de clases y en fin de semana",
            "Conservacion diaria con tolerancia 0.001 Wh",
            "Particiones cronologicas sinteticas",
        ],
        alcance="Validacion de demanda; no valida todavia PV, baterias ni MPC",
    )


def guardar_csv(ruta, filas):
    """Escribe filas con el orden de columnas de sus claves."""
    with ruta.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def publicar_tablas(tablas, rutas, salidas):
    """Escribe cada tabla en su destino y copia el histórico solar, que se conserva sin cambios."""
    for clave, filas in tablas.items():
        salidas[clave].parent.mkdir(parents=True, exist_ok=True)
        guardar_csv(salidas[clave], filas)
    historico = salidas["irradiancia_historica"]
    if historico != rutas["irradiancia_historica"]:
        historico.write_bytes(rutas["irradiancia_historica"].read_bytes())


def escribir_resumenes(destino, validacion, salidas):
    """Escribe validacion.json y sha256.json (los hashes de los archivos publicados)."""
    texto_validacion = json.dumps(validacion, ensure_ascii=False, indent=2)
    (destino / "validacion.json").write_text(texto_validacion + "\n", encoding="utf-8")
    hashes = {ruta.name: hashlib.sha256(ruta.read_bytes()).hexdigest() for ruta in sorted(salidas.values())}
    (destino / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")


def generar_archivos(ruta_config, salida=None):
    """Regenera el caso en la carpeta elegida y devuelve su resumen de validación.

    Todo se calcula y valida en memoria antes de escribir: si algo falla, no se publica nada.
    """
    ruta_config = Path(ruta_config).resolve()
    config = cargar_configuracion(ruta_config)
    horaria, por_carga, diaria = generar_demanda(config)
    calendario = generar_calendario(config)
    rutas = {clave: (ruta_config.parent / valor).resolve() for clave, valor in config["datos"].items()}
    nombres = {clave: ruta.name for clave, ruta in rutas.items()}
    if len(set(nombres.values())) != len(nombres):
        raise ValueError(f"{ruta_config}: datos: los nombres de los archivos deben ser distintos")
    tablas = dict(horaria=horaria, demanda=por_carga, diaria=diaria, catalogo=config["cargas"],
                  lecturas=filas_lecturas(config["lecturas"]), intervalos=filas_intervalos(config["lecturas"]),
                  calendario=calendario,
                  irradiancia_simulacion=ventana_solar(rutas["irradiancia_historica"], config, calendario))
    destino = Path(salida).resolve() if salida is not None else rutas["demanda"].parent
    salidas = {clave: destino / nombres[clave] if salida is not None else ruta for clave, ruta in rutas.items()}
    publicar_tablas(tablas, rutas, salidas)
    validacion = resumen_validacion(config, horaria, por_carga, diaria)
    escribir_resumenes(destino, validacion, salidas)
    return validacion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("scripts/configuracion.json"))
    parser.add_argument("--salida", type=Path, help="Carpeta de salida; por defecto, carpeta de datos configurada")
    args = parser.parse_args()
    try:
        resumen = generar_archivos(args.config, args.salida)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
