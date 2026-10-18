"""Comprobación reproducible de HU-01; todavía no despacha energía."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

from .datos import cargar_caso


def resumen_caso(caso):
    config = caso.configuracion
    return {
        "estado": "DATOS_Y_CONFIGURACION_VALIDADOS_BAJO_SUPUESTOS",
        "alcance": "HU-01; no verifica balances FV/baterías, despacho ni MPC",
        "sitio": config["sitio"],
        "conteos": {"cargas": len(caso.catalogo), "horas_simulacion": len(caso.calendario),
                    "solicitudes_carga_hora": len(caso.demanda), "dias": len(caso.diaria),
                    "horas_historicas": len(caso.irradiancia_historica)},
        "horas_por_particion": caso.irradiancia_historica["particion"].value_counts().to_dict(),
        "demanda_total_kwh": float(caso.demanda["demanda_wh"].sum() / 1000),
        "max_error_conservacion_wh": float((caso.demanda.groupby(caso.demanda["t"] // 24)["demanda_wh"].sum()
                                            .reset_index(drop=True) - caso.diaria["objetivo_wh"]).abs().max()),
        "periodos": config["periodos"],
        "semillas": {"demanda": config["semilla"], "escenarios": config["semilla_escenarios"]},
        "sistema": {campo: config[campo] for campo in ("pv", "baterias", "potencia_bus_max_w", "resolucion_h",
                                                     "horizonte", "replanificacion", "escenarios")},
        "supuestos": ["demanda sintética programada común a A y B; no medición horaria",
                      "fechas asignadas; calendario sin festivos ni vacaciones",
                      "FV y baterías provisionales sin calibrar; límites individuales en el bus",
                      "ERA5 es reanálisis, no medición local; available_at = fin_intervalo idealizado",
                      "snapshot solar heredado sin respuesta cruda original en el repositorio"],
        "entradas_sha256": {clave: {"ruta": str(ruta), "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest()}
                            for clave, ruta in caso.rutas.items()},
        "codigo_sha256": {ruta.name: hashlib.sha256(ruta.read_bytes()).hexdigest()
                          for ruta in sorted(Path(__file__).parent.glob("*.py"))},
        "entorno": {"python": platform.python_version(), "plataforma": platform.platform(),
                    **{nombre: importlib.metadata.version(nombre) for nombre in ("energy-project", "pandas", "tzdata")}},
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("scripts/configuracion.json"))
    parser.add_argument("--salida", type=Path, default=Path("results/preparacion_hu01.json"))
    args = parser.parse_args()
    try:
        caso = cargar_caso(args.config)
        if args.salida.resolve() in caso.rutas.values():
            raise ValueError("salida: el informe no puede reemplazar datos ni configuración de entrada")
        texto = json.dumps(resumen_caso(caso), ensure_ascii=False, indent=2) + "\n"
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(texto, encoding="utf-8")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(texto, end="")


if __name__ == "__main__":
    main()
