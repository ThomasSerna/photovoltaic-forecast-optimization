"""Repite T05 original y lo contrasta con el controlador anterior a T06."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
from time import perf_counter
from types import ModuleType

import numpy as np
import pandas as pd

from src import controlador, experimento
from src.cli import codigo_sha256, entradas_sha256, entorno, sha256_de
from src.configuracion import parametros_mpc
from src.datos import cargar_caso
from src.pronosticos import cargar_chronos


REFERENCIA = "9bc279c7caa2aa81eab10567d48741e93fd10f9f"
RAIZ = Path(__file__).resolve().parents[1]


def cargar_referencia():
    """Carga solo código local de una revisión fija, sin modificar el checkout."""
    fuente = subprocess.run(
        ["git", "show", f"{REFERENCIA}:src/controlador.py"], cwd=RAIZ,
        check=True, capture_output=True, encoding="utf-8",
    ).stdout
    modulo = ModuleType("src._controlador_pre_t06")
    modulo.__package__ = "src"
    exec(compile(fuente, f"git:{REFERENCIA}/src/controlador.py", "exec"), modulo.__dict__)
    return modulo, hashlib.sha256(fuente.encode("utf-8")).hexdigest()


def comparar_resultados(actual, anterior):
    """Exige las mismas tablas físicas y decisiones; omite nuevas columnas de auditoría."""
    for nombre in ("cargas", "horas", "baterias", "decisiones"):
        izquierda, derecha = actual[nombre], anterior[nombre]
        try:
            pd.testing.assert_frame_equal(
                izquierda[derecha.columns].reset_index(drop=True), derecha.reset_index(drop=True),
                check_exact=False, rtol=0, atol=experimento.TOLERANCIA_WH,
            )
        except (AssertionError, KeyError) as error:
            raise ValueError(f"regresión original: cambió {nombre}") from error
    for nombre in ("resumen", "estado_final"):
        if actual[nombre].keys() != anterior[nombre].keys():
            raise ValueError(f"regresión original: cambiaron campos de {nombre}")
        for campo, valor in anterior[nombre].items():
            otro = actual[nombre][campo]
            if ((valor is None and otro is not None) or
                    (valor is not None and (otro is None or abs(otro - valor) > experimento.TOLERANCIA_WH))):
                raise ValueError(f"regresión original: cambió {nombre}.{campo}")
    for uno, otro in zip(actual["pronosticos"], anterior["pronosticos"], strict=True):
        if uno["origen_utc"] != otro["origen_utc"] or uno["semilla"] != otro["semilla"]:
            raise ValueError("regresión original: cambiaron orígenes o semillas")
        for nombre in ("crudos", "corregidos"):
            if not uno[nombre].equals(otro[nombre]):
                raise ValueError(f"regresión original: cambiaron pronósticos {nombre}")
        if (not uno["bloques"].equals(otro["bloques"]) or
                not np.array_equal(uno["rangos"], otro["rangos"])):
            raise ValueError("regresión original: cambiaron bloques o rangos")
    return dict(coincide=True, horas=len(actual["horas"]), origenes=len(actual["pronosticos"]))


def contrastar_publicados(resumen):
    """Compara con la precisión publicada en IDEA §7, no con cifras inventadas."""
    metricas = resumen["metricas"]
    valores = {
        "S_pond_beta": (metricas["beta"]["servicio"]["S_pond"], .9312263476, 5e-11),
        "S_pond_chronos": (metricas["chronos"]["servicio"]["S_pond"], .9328742419, 5e-11),
        "delta_J": (metricas["chronos"]["servicio"]["J"] - metricas["beta"]["servicio"]["J"], 505.599, .0005),
        "J_reactiva": (metricas["reactiva"]["servicio"]["J"], 285240.845, .0005),
        "MAE_diurno_beta": (metricas["beta"]["pronostico"]["diurno"]["corregido"]["mae_w_m2"], 66.33, .005),
        "MAE_diurno_chronos": (metricas["chronos"]["pronostico"]["diurno"]["corregido"]["mae_w_m2"], 64.20, .005),
    }
    filas = {nombre: dict(observado=valor, publicado=esperado, tolerancia=tolerancia,
                          coincide=abs(valor - esperado) <= tolerancia)
             for nombre, (valor, esperado, tolerancia) in valores.items()}
    if not all(fila["coincide"] for fila in filas.values()):
        raise ValueError(f"regresión original: diferencias con cifras publicadas: {filas}")
    return filas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=RAIZ / "scripts/configuracion.json")
    parser.add_argument("--salida", type=Path, default=RAIZ / "results/t07_regresion_original")
    args = parser.parse_args()
    try:
        caso = cargar_caso(args.config)
        config = caso.configuracion
        if parametros_mpc(config)[0] != "original" or config["lambda_terminal"] != 0:
            raise ValueError("regresión T05: se requiere modo original y lambda_terminal=0")
        salida = args.salida.resolve()
        if salida in [ruta.resolve() for ruta in caso.rutas.values()]:
            raise ValueError("salida: no puede reemplazar una entrada")
        salida.mkdir(parents=True, exist_ok=False)
        entradas, codigo = entradas_sha256(caso.rutas), codigo_sha256()
        guion_sha256 = sha256_de(Path(__file__))
        anterior, fuente_sha256 = cargar_referencia()
        inicio = perf_counter()
        opciones = config["pronosticos"]
        pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])
        proveedores = controlador.proveedores(caso, pipeline)
        evaluacion = experimento.evaluar(caso, proveedores)
        repeticion = experimento.verificar_reproducibilidad(caso, proveedores, evaluacion)
        comparaciones = {}
        for nombre in experimento.POLITICAS:
            actual = evaluacion["resultados"][nombre]
            congelados = {p["origen_utc"]: p["crudos"] for p in actual["pronosticos"]}

            def pronosticar(historia, origen, horizonte):
                tabla = congelados[origen]
                if len(tabla) != horizonte:
                    raise ValueError("regresión: horizonte distinto del pronóstico congelado")
                return tabla.copy()

            previo = anterior.ejecutar_mpc(caso, pronosticar, inicio=evaluacion["inicio_utc"],
                                          fin=evaluacion["fin_utc"])
            comparaciones[nombre] = comparar_resultados(actual, previo)
        resumen = experimento.resumir(caso, evaluacion)
        publicados = contrastar_publicados(resumen)
        tabla = experimento.tabla_ab(caso, evaluacion, resumen)
        experimento.exportar(salida, caso, evaluacion, resumen, tabla)
        versiones = entorno()
        for paquete in ("numpy", "scipy", "chronos-forecasting", "torch", "transformers", "astral", "matplotlib"):
            versiones[paquete] = importlib.metadata.version(paquete)
        informe = dict(
            alcance="T07: regresión integral de T05 original; marzo no calibra ni evalúa CVaR",
            referencia_git=REFERENCIA, fuente_referencia_sha256=fuente_sha256,
            inicio_utc=evaluacion["inicio_utc"].isoformat(), fin_utc=evaluacion["fin_utc"].isoformat(),
            configuracion=config, semillas=dict(demanda=config["semilla"], escenarios=config["semilla_escenarios"]),
            verificaciones=dict(regresion=comparaciones, publicados=publicados, reproducibilidad=repeticion,
                                fisica=evaluacion["fisica"], pareo=evaluacion["pareo"]),
            metricas=resumen["metricas"], diferencias_diarias=resumen["diferencias_diarias"],
            duracion_s=perf_counter() - inicio, entradas_sha256=entradas, codigo_sha256=codigo,
            guion_sha256=guion_sha256, uv_lock_sha256=sha256_de(RAIZ / "uv.lock"), entorno=versiones,
            archivos_sha256={str(p.relative_to(salida)): sha256_de(p)
                            for p in sorted(salida.rglob("*")) if p.is_file()},
        )
        if (entradas_sha256(caso.rutas) != entradas or codigo_sha256() != codigo or
                sha256_de(Path(__file__)) != guion_sha256):
            raise ValueError("regresión: cambiaron entradas o código durante la ejecución")
        temporal = salida / "resumen.json.tmp"
        try:
            temporal.write_text(json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                                encoding="utf-8")
            temporal.replace(salida / "resumen.json")
        finally:
            temporal.unlink(missing_ok=True)
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Error: {error}\nLa salida {args.salida} puede estar incompleta; no es válida.\n")
    print(f"{salida}: T05 original coincide con {REFERENCIA[:8]} y las cifras publicadas")


if __name__ == "__main__":
    main()
