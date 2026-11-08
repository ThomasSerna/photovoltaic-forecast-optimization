"""Calibración orientada al servicio: compara λ_R en desarrollo, sin usar marzo.

Adaptación del proyecto, con el criterio de la rama `experimental/beta-comparation`: elige
el peso de mayor J medio entre beta y Chronos-2 en el ajuste de la calibración del riesgo; en empate, el menor.
Aquí se añade la validación del 17 de febrero al 3 de marzo con ambos proveedores, con la
regla de esa calibración: frente a λ_R = 0, cada uno conserva el 99 % de J y el CVaR diario medio no
empeora. Solo se recorren λ_R ∈ {0, 1}: los otros pesos tienen menor J medio en IDEA §7.
"""

import argparse
from copy import copy
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from scripts.experimento_2x2 import memorizar, parametros_cvar
from src.cli import codigo_sha256, entradas_sha256, entorno, sha256_de
from src.controlador import (
    RIESGO_CORTE, RIESGO_FIN, RIESGO_INICIO, _diario_riesgo, _periodo, _resumir_riesgo, _validar_riesgo,
    _verificar_fisica, ejecutar_mpc, proveedores,
)
from src.datos import cargar_caso
from src.experimento import POLITICAS, metricas_servicio
from src.pronosticos import cargar_chronos

RAIZ = Path(__file__).resolve().parents[1]
PESOS = (0., 1.)


def comparar_pesos(caso, pronosticadores, *, pesos=PESOS):
    """Una trayectoria continua por proveedor y peso; sus días se reparten entre las dos fases."""
    config = caso.configuracion
    inicio, fin, reloj = _periodo(caso, RIESGO_INICIO, RIESGO_FIN)
    corte = pd.Timestamp(RIESGO_CORTE).strftime("%Y-%m-%d")
    compartidos = {nombre: memorizar(pronosticadores[nombre]) for nombre in POLITICAS}
    diarios, fisica, incumplidos = [], [], []
    for proveedor in POLITICAS:
        for peso in pesos:
            caso_peso = copy(caso)
            caso_peso.configuracion = dict(config, **parametros_cvar(peso))
            resultado = ejecutar_mpc(caso_peso, compartidos[proveedor], inicio=inicio, fin=fin)
            etiquetas = dict(proveedor=proveedor, modo_mpc="cvar", lambda_riesgo=peso)
            dia = _diario_riesgo(resultado, reloj, config["zona_horaria"]).assign(**etiquetas)
            diarios.append(dia.assign(fase=np.where(dia["fecha"] < corte, "ajuste", "validacion")))
            fisica.append(dict(etiquetas, **_verificar_fisica(resultado, caso_peso.configuracion)))
            incumplidos.append(dict(etiquetas, **{
                c: metricas_servicio(resultado, caso_peso.configuracion)[c]
                for c in ("compromisos_incumplidos", "solicitudes_desatendidas")}))
    diario = pd.concat(diarios, ignore_index=True)
    ajuste, validacion = (_resumir_riesgo(diario.loc[diario["fase"].eq(fase)]) for fase in ("ajuste", "validacion"))
    elegido = seleccionar_servicio(ajuste)
    return dict(lambda_riesgo=elegido, ajuste=ajuste, validacion=validacion, diario=diario, fisica=fisica,
                incumplidos=incumplidos, veredicto=_validar_riesgo(validacion, elegido))


def seleccionar_servicio(ajuste):
    """Mayor J medio entre proveedores; `idxmax` devuelve el menor peso si hay empate."""
    return float(ajuste.groupby("lambda_riesgo", sort=True)["J"].mean().idxmax())


def cotejar_calibracion(ajuste, validacion):
    """Compara con IDEA §7, a su precisión: ajuste medio de λ_R ∈ {0, 1} y validación con cero."""
    medias = ajuste.groupby("lambda_riesgo")[["J", "CVaR_diario"]].mean()
    cero = validacion.loc[validacion["lambda_riesgo"].eq(0.)].set_index("proveedor")
    valores = dict(
        J_ajuste_0=(medias.loc[0., "J"], 447641.358), CVaR_ajuste_0=(medias.loc[0., "CVaR_diario"], 3083.645),
        J_ajuste_1=(medias.loc[1., "J"], 447688.355), CVaR_ajuste_1=(medias.loc[1., "CVaR_diario"], 3103.842),
        J_validacion_beta=(cero.loc["beta", "J"], 150935.735), J_validacion_chronos=(cero.loc["chronos", "J"], 150794.594),
        CVaR_validacion_beta=(cero.loc["beta", "CVaR_diario"], 2809.385),
        CVaR_validacion_chronos=(cero.loc["chronos", "CVaR_diario"], 2910.200),
    )
    return {nombre: dict(observado=float(valor), publicado=esperado, coincide=bool(abs(valor - esperado) <= .0005))
            for nombre, (valor, esperado) in valores.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=RAIZ / "scripts/configuracion.json")
    parser.add_argument("--salida", type=Path, default=RAIZ / "results/pesos_desarrollo")
    args = parser.parse_args()
    try:
        caso = cargar_caso(args.config)
        config = caso.configuracion
        salida = args.salida.resolve()
        if salida == args.config.resolve() or salida in [p.resolve() for p in caso.rutas.values()]:
            raise ValueError("salida: no puede reemplazar una entrada")
        salida.mkdir(parents=True, exist_ok=False)
        entradas, codigo, guion = entradas_sha256(caso.rutas), codigo_sha256(), sha256_de(Path(__file__))
        inicio = perf_counter()
        opciones = config["pronosticos"]
        pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])
        ensayo = comparar_pesos(caso, proveedores(caso, pipeline))
        duracion = perf_counter() - inicio
        for nombre in ("ajuste", "validacion", "diario"):
            ensayo[nombre].to_csv(salida / f"{nombre}.csv", index=False)
        informe = dict(
            alcance="Calibración orientada al servicio en desarrollo; marzo no interviene",
            criterio="mayor J medio entre beta y Chronos-2 en el ajuste; empate por menor peso",
            criterio_validacion="regla de la calibración del riesgo: 99 % de J en cada rama y CVaR diario medio no mayor que con peso cero",
            inicio=RIESGO_INICIO, corte=RIESGO_CORTE, fin=RIESGO_FIN, pesos=list(PESOS), lambda_riesgo=ensayo["lambda_riesgo"],
            veredicto=ensayo["veredicto"], cifras_calibracion=cotejar_calibracion(ensayo["ajuste"], ensayo["validacion"]),
            ajuste=ensayo["ajuste"].to_dict("records"),
            validacion=ensayo["validacion"].to_dict("records"), fisica=ensayo["fisica"],
            incumplidos=ensayo["incumplidos"], duracion_s=duracion,
            semillas=dict(demanda=config["semilla"], escenarios=config["semilla_escenarios"]),
            entradas_sha256=entradas, codigo_sha256=codigo, guion_sha256=guion,
            uv_lock_sha256=sha256_de(RAIZ / "uv.lock"), entorno=entorno(),
        )
        if (entradas_sha256(caso.rutas) != entradas or codigo_sha256() != codigo
                or sha256_de(Path(__file__)) != guion):
            raise ValueError("calibración: cambiaron entradas o código durante el ensayo")
        temporal = salida / "resumen.json.tmp"
        try:
            temporal.write_text(json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            temporal.replace(salida / "resumen.json")
        finally:
            temporal.unlink(missing_ok=True)
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\nLa salida {args.salida} puede estar incompleta; no es válida.\n")
    print(f"{salida}: lambda_riesgo={ensayo['lambda_riesgo']:g}, recomendado={ensayo['veredicto']['recomendado']}")


if __name__ == "__main__":
    main()
