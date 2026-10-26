"""Compara las reglas de selección de cargas (IDEA §4.4) en un periodo, con el MPC de T05.

Ejecutar desde la raíz:
    uv run --locked --extra pronosticos python scripts/comparar_seleccion.py --salida results/seleccion_desarrollo

Beta, Chronos-2 y la referencia reactiva parten del estado configurado con λ_T, escenarios
y semillas de la configuración; solo cambia `seleccion_cargas`. Por defecto usa desarrollo,
para decidir la regla sin consultar la evaluación. No modifica la configuración.
"""

import argparse
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from src.cli import codigo_sha256, entradas_sha256
from src.configuracion import SELECCIONES
from src.controlador import _verificar_fisica, ejecutar_mpc, proveedores
from src.datos import cargar_caso
from src.experimento import diario, ejecutar_reactiva, metricas_servicio
from src.pronosticos import cargar_chronos


def comparar(caso, pronosticadores, inicio, fin):
    filas, por_dia = [], []
    for seleccion in SELECCIONES:
        caso.configuracion["seleccion_cargas"] = seleccion
        resultados, tiempos = {}, {}
        for nombre, pronosticar in pronosticadores.items():
            reloj = perf_counter()
            resultados[nombre] = ejecutar_mpc(caso, pronosticar, inicio=inicio, fin=fin)
            tiempos[nombre] = perf_counter() - reloj
        resultados["reactiva"] = ejecutar_reactiva(caso, inicio, fin)
        for nombre, resultado in resultados.items():
            fila = dict(seleccion=seleccion, politica=nombre, **metricas_servicio(resultado, caso.configuracion),
                        **_verificar_fisica(resultado, caso.configuracion), tiempo_s=tiempos.get(nombre))
            filas.append(fila)
        por_dia.append(diario(resultados, caso.calendario).assign(seleccion=seleccion))
    caso.configuracion.pop("seleccion_cargas")
    return pd.DataFrame(filas), pd.concat(por_dia, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=Path("scripts/configuracion.json"))
    parser.add_argument("--periodo", choices=("desarrollo", "evaluacion"), default="desarrollo")
    parser.add_argument("--salida", type=Path, required=True, help="Carpeta nueva")
    args = parser.parse_args()
    caso = cargar_caso(args.config)
    if "seleccion_cargas" in caso.configuracion:
        raise SystemExit("La configuración ya fija seleccion_cargas; este script compara ambas reglas.")
    args.salida.mkdir(parents=True, exist_ok=False)
    entradas, codigo = entradas_sha256(caso.rutas), codigo_sha256()
    periodo = caso.configuracion["periodos"][args.periodo]
    opciones = caso.configuracion["pronosticos"]
    pronosticadores = proveedores(caso, cargar_chronos(opciones["revision"], opciones["dispositivo"]))
    tabla, por_dia = comparar(caso, pronosticadores, pd.Timestamp(periodo["inicio"]), pd.Timestamp(periodo["fin"]))
    if entradas_sha256(caso.rutas) != entradas or codigo_sha256() != codigo:
        raise SystemExit("Cambiaron entradas o código durante la comparación.")
    tabla.to_csv(args.salida / "tabla_seleccion.csv", index=False)
    por_dia.to_csv(args.salida / "diario_seleccion.csv", index=False)
    resumen = dict(periodo=args.periodo, periodo_local=periodo, entradas_sha256=entradas, codigo_sha256=codigo,
                   control={c: caso.configuracion[c] for c in ("horizonte", "replanificacion", "escenarios",
                                                               "lambda_terminal", "semilla_escenarios")},
                   resultados=tabla.astype(object).where(tabla.notna(), None).to_dict("records"))
    (args.salida / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2, default=str) + "\n",
                                              encoding="utf-8")
    print(tabla[["seleccion", "politica", "J", "S_pond", "servicio_critico", "violaciones_fisicas"]].to_string(index=False))


if __name__ == "__main__":
    main()
