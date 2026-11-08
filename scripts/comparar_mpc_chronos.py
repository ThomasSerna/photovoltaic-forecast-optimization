"""Compara el MPC original y el calibrado con los mismos pronósticos de Chronos-2."""

import argparse
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from src.cli import CAMPOS_DEL_SISTEMA, codigo_sha256, entradas_sha256, entorno, sha256_de
from src.configuracion import parametros_mpc
from src.controlador import _calendario_utc, _periodo, _verificar_fisica, calcular_cvar, ejecutar_mpc
from src.datos import cargar_caso
from src.experimento import _estilo, _instrumentar, diario, metricas_servicio


RAIZ = Path(__file__).resolve().parents[1]
COLORES = {"anterior": "#2a78d6", "nuevo": "#eb6834"}
ETIQUETAS = {"anterior": "MPC anterior", "nuevo": "MPC nuevo"}


def pronostico_congelado(tabla, configuracion):
    """El callback conserva únicamente cuantiles emitidos; nunca la GHI realizada."""
    niveles = configuracion["pronosticos"]["cuantiles"]
    columnas = [f"crudo_q{q}" for q in niveles]
    fuente = tabla.loc[tabla.proveedor.eq("chronos"), ["origen_utc", "inicio_utc", "anticipacion_h", *columnas]].copy()
    for nombre in ("origen_utc", "inicio_utc"):
        fuente[nombre] = pd.to_datetime(fuente[nombre], utc=True)
    if (fuente.empty or fuente.duplicated(["origen_utc", "inicio_utc"]).any()
            or not np.isfinite(fuente[columnas].to_numpy(dtype=float)).all()):
        raise ValueError("pronósticos congelados: filas vacías, duplicadas o no finitas")
    congelados = {}
    for origen, grupo in fuente.groupby("origen_utc", sort=True):
        grupo = grupo.sort_values("inicio_utc")
        esperados = pd.date_range(origen, periods=len(grupo), freq="h", name="inicio_utc")
        if (not pd.DatetimeIndex(grupo.inicio_utc).equals(esperados)
                or grupo.anticipacion_h.tolist() != list(range(1, len(grupo) + 1))):
            raise ValueError("pronósticos congelados: horizonte discontinuo o anticipaciones inválidas")
        congelados[origen] = pd.DataFrame(grupo[columnas].to_numpy(), index=esperados, columns=niveles)

    def pronosticar(historia, origen, horizonte):
        origen = pd.Timestamp(origen).tz_convert("UTC")
        if (len(historia) != configuracion["pronosticos"]["contexto"]
                or (historia.available_at > origen).any() or (historia.fin_utc > origen).any()
                or historia.fin_utc.iloc[-1] != origen):
            raise ValueError("pronósticos congelados: historia no admisible o información futura")
        if origen not in congelados or len(congelados[origen]) != horizonte:
            raise ValueError("pronósticos congelados: origen u horizonte distintos del registro")
        return congelados[origen].copy()

    return pronosticar


def cargar_anterior(carpeta):
    informe = json.loads((carpeta / "resumen.json").read_text(encoding="utf-8"))
    tablas = {}
    for nombre in ("cargas", "horas", "baterias"):
        tabla = pd.read_csv(carpeta / f"{nombre}.csv", float_precision="round_trip")
        tablas[nombre] = tabla.loc[tabla.politica.eq("chronos")].drop(columns="politica").reset_index(drop=True)
    estado = tablas["baterias"].groupby("bateria_id").final_wh.last().to_dict()
    return informe, dict(**tablas, estado_final=estado, resumen=informe["metricas"]["chronos"]["servicio"])


def verificar_entradas(caso, informe, referencia):
    anterior, nuevo = informe["configuracion"], caso.configuracion
    campos = (*CAMPOS_DEL_SISTEMA, "semilla", "semilla_escenarios", "pronosticos", "zona_horaria",
              "latitud", "longitud", "periodos")
    if any(anterior[c] != nuevo[c] for c in campos):
        raise ValueError("pareo: cambiaron condiciones físicas, temporales o de pronóstico")
    if parametros_mpc(anterior)[0] != "original" or parametros_mpc(nuevo) != ("cvar", .9, 0.):
        raise ValueError("comparación: se requieren el MPC original y el CVaR calibrado con alpha=0.9, lambda=0")
    entradas = entradas_sha256(caso.rutas)
    for nombre, registro in entradas.items():
        if nombre != "configuracion" and registro["sha256"] != informe["entradas_sha256"][nombre]["sha256"]:
            raise ValueError(f"pareo: cambió la entrada {nombre}")
    for nombre, huella in informe["archivos_sha256"].items():
        ruta = (referencia / nombre).resolve()
        if not ruta.is_relative_to(referencia.resolve()) or sha256_de(ruta) != huella:
            raise ValueError(f"referencia: artefacto alterado {nombre}")


def diario_comparativo(resultados, calendario):
    tabla = diario(resultados, calendario)
    fechas = calendario.set_index("t").inicio_intervalo.dt.date
    for nombre, resultado in resultados.items():
        cargas = resultado["cargas"]
        perdida = (cargas.prioridad_supuesta * cargas.no_atendida_wh).groupby(cargas.t.map(fechas)).sum()
        tabla[f"perdida_ponderada_{nombre}"] = tabla.fecha.map(perdida)
    tabla["delta_J"] = tabla.J_nuevo - tabla.J_anterior
    tabla["delta_acumulado_J"] = tabla.delta_J.cumsum()
    return tabla


def comparar(caso, informe, anterior, tabla_pronosticos):
    config = caso.configuracion
    periodo = config["periodos"]["evaluacion"]
    inicio, fin, reloj = _periodo(caso, periodo["inicio"], periodo["fin"])
    if inicio != pd.Timestamp(informe["inicio_utc"]) or fin != pd.Timestamp(informe["fin_utc"]):
        raise ValueError("pareo: fechas de evaluación distintas de la referencia")
    claves = ["t", "carga_id", "demanda_wh", "solicitada", "prioridad_supuesta", "potencia_w_supuesta"]
    esperada = caso.demanda.loc[caso.demanda.t.isin(reloj.t), claves].sort_values(["t", "carga_id"])
    try:
        pd.testing.assert_frame_equal(anterior["cargas"][claves].sort_values(["t", "carga_id"]).reset_index(drop=True),
                                      esperada.reset_index(drop=True), check_dtype=False, check_exact=True)
    except AssertionError as error:
        raise ValueError("pareo: demanda distinta de la referencia") from error
    if (anterior["horas"].t.tolist() != reloj.t.tolist()
            or anterior["baterias"].duplicated(["t", "bateria_id"]).any()):
        raise ValueError("referencia: horas o baterías duplicadas o incompletas")
    fisica = dict(anterior=_verificar_fisica(anterior, informe["configuracion"]))
    registros = []
    callback = _instrumentar(pronostico_congelado(tabla_pronosticos, config), registros,
                             config["pronosticos"]["contexto"])
    nuevo = ejecutar_mpc(caso, callback, inicio=inicio, fin=fin)
    fisica["nuevo"] = _verificar_fisica(nuevo, config)
    esperado = {b["id"]: b["inicial_wh"] for b in config["baterias"]}
    for resultado in (anterior, nuevo):
        if resultado["baterias"].groupby("bateria_id").inicial_wh.first().to_dict() != esperado:
            raise ValueError("pareo: estados iniciales individuales distintos")
    fuente = tabla_pronosticos.loc[tabla_pronosticos.proveedor.eq("chronos")].copy()
    for nombre in ("origen_utc", "inicio_utc"):
        fuente[nombre] = pd.to_datetime(fuente[nombre], utc=True)
    if set(fuente.origen_utc) != {p["origen_utc"] for p in nuevo["pronosticos"]}:
        raise ValueError("pareo: orígenes de pronóstico distintos")
    max_error = 0.
    for p in nuevo["pronosticos"]:
        filas = fuente.loc[fuente.origen_utc.eq(p["origen_utc"])].sort_values("inicio_utc")
        for prefijo, nombre in (("crudo_q", "crudos"), ("q", "corregidos")):
            guardados = filas[[f"{prefijo}{q}" for q in config["pronosticos"]["cuantiles"]]].to_numpy()
            if not np.array_equal(guardados, p[nombre].to_numpy()):
                raise ValueError(f"pareo: cambiaron pronósticos {nombre}")
            max_error = max(max_error, float(np.abs(guardados - p[nombre].to_numpy()).max()))
    resultados = dict(anterior=anterior, nuevo=nuevo)
    por_dia = diario_comparativo(resultados, caso.calendario)
    metricas = dict(anterior=informe["metricas"]["chronos"]["servicio"].copy(),
                    nuevo=metricas_servicio(nuevo, config))
    for nombre in resultados:
        metricas[nombre]["CVaR_diario"] = calcular_cvar(por_dia[f"perdida_ponderada_{nombre}"], .9)
    return dict(resultados=resultados, diario=por_dia, metricas=metricas, registros=registros,
                verificaciones=dict(fisica=fisica, filtraciones=0, origenes=len(registros),
                                    pronosticos_identicos=True, max_diferencia_pronostico=max_error))


def graficar(carpeta, caso, comparacion):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    carpeta.mkdir(parents=True, exist_ok=True)
    tabla = comparacion["diario"]
    fechas = pd.DatetimeIndex(pd.to_datetime(tabla.fecha))
    titulo = "Chronos-2 · MPC anterior / nuevo (λ_R=0)"
    rango = f"{fechas.min():%d-%m-%Y} a {fechas.max():%d-%m-%Y}"

    def terminar(figura, ejes, nombre, subtitulo, ticks):
        figura.suptitle(f"{titulo}\n{subtitulo} · {rango}", x=.08, y=.98, ha="left", fontsize=12)
        handles, labels = ejes[0].get_legend_handles_labels()
        if handles:
            figura.legend(handles, labels, loc="upper right", bbox_to_anchor=(.98, .91), ncols=2)
        figura.subplots_adjust(top=.80, bottom=.12, left=.09, right=.96, hspace=.38)
        if ticks is not None:
            posiciones = np.unique(np.linspace(0, len(ticks) - 1, min(8, len(ticks)), dtype=int))
            ejes[-1].set_xticks(ticks.take(posiciones))
            ejes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%d-%m"))
            ejes[-1].set_xlabel("Fecha local (día-mes, America/Bogota)")
        figura.savefig(carpeta / f"{nombre}.png", dpi=170, bbox_inches="tight")
        figura.savefig(carpeta / f"{nombre}.svg", bbox_inches="tight")
        plt.close(figura)

    with plt.rc_context(_estilo()):
        figura, ejes = plt.subplots(2, 1, figsize=(12, 6.8), sharex=True)
        for nombre in ("anterior", "nuevo"):
            estilo = dict(color=COLORES[nombre], label=ETIQUETAS[nombre], marker="o", markersize=3.5,
                          linestyle="--" if nombre == "anterior" else "-")
            ejes[0].plot(fechas, 100 * tabla[f"S_pond_{nombre}"], **estilo)
            ejes[1].plot(fechas, tabla[f"no_atendida_wh_{nombre}"], **estilo)
        minimo = min(tabla.S_pond_anterior.min(), tabla.S_pond_nuevo.min()) * 100
        ejes[0].set_ylim(max(0, 5 * math.floor(minimo / 5) - 5), 101)
        ejes[0].set_ylabel("S_pond diario (%) · escala focal")
        ejes[1].set_ylabel("Energía no atendida (Wh)")
        ejes[1].set_ylim(bottom=0)
        terminar(figura, ejes, "servicio_diario", "Servicio realizado por día", fechas)

        figura, ejes = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True)
        ejes[0].bar(fechas, tabla.delta_J, width=.7,
                    color=np.where(tabla.delta_J.ge(0), COLORES["anterior"], COLORES["nuevo"]))
        ejes[1].plot(fechas, tabla.delta_acumulado_J, color="#52514e", marker="o", markersize=3)
        ejes[1].annotate(f"{tabla.delta_acumulado_J.iloc[-1]:+.3f} Wh·p", (fechas[-1], tabla.delta_acumulado_J.iloc[-1]),
                         xytext=(-5, 10), textcoords="offset points", ha="right", fontsize=10)
        for eje in ejes:
            eje.axhline(0, color="#898781", linewidth=.8)
        ejes[0].set_ylabel("ΔJ diario (Wh·p)")
        ejes[1].set_ylabel("ΔJ acumulado (Wh·p)")
        terminar(figura, ejes, "diferencias_servicio", "Diferencia nuevo − anterior; positivo favorece al nuevo", fechas)

        baterias = caso.configuracion["baterias"]
        figura, arreglo = plt.subplots(len(baterias), 1, figsize=(12, 3.2 * len(baterias) + .7),
                                       sharex=True, squeeze=False)
        ejes = arreglo[:, 0]
        reloj = _calendario_utc(caso.calendario).set_index("t").inicio_utc
        for eje, bateria in zip(ejes, baterias, strict=True):
            for nombre, resultado in comparacion["resultados"].items():
                flujos = resultado["baterias"].loc[resultado["baterias"].bateria_id.eq(bateria["id"])].sort_values("t")
                comienzos = pd.DatetimeIndex(flujos.t.map(reloj)).tz_convert(caso.configuracion["zona_horaria"])
                tiempos = comienzos[:1].append(comienzos + pd.Timedelta(hours=1))
                valores = np.r_[flujos.inicial_wh.iloc[0], flujos.final_wh.to_numpy()]
                eje.plot(tiempos.tz_localize(None), valores, color=COLORES[nombre], label=ETIQUETAS[nombre],
                         linestyle="--" if nombre == "anterior" else "-", linewidth=1.1)
            for campo, etiqueta in (("minimo_wh", "mínimo"), ("maximo_wh", "máximo")):
                limite = bateria[campo]
                eje.axhline(limite, color="#898781", linewidth=.8)
                eje.text(.995, limite, etiqueta, transform=eje.get_yaxis_transform(), ha="right", va="bottom",
                         fontsize=8, color="#52514e")
            eje.set_title(bateria["id"])
            eje.set_ylabel("Energía almacenada (Wh)")
        terminar(figura, ejes, "baterias", "Estados individuales y límites físicos", tiempos.tz_localize(None))

        figura, arreglo = plt.subplots(2, 2, figsize=(11, 7.5))
        ejes = arreglo.ravel()
        indicadores = (("J", 1e-3, "J realizado (kWh·p)"), ("no_atendida_wh", 1e-3, "Energía no atendida (kWh)"),
                       ("CVaR_diario", 1e-3, "CVaR₀,₉ de pérdida diaria (kWh·p)"),
                       ("servicio_critico", 100, "Servicio crítico (%)"))
        for eje, (campo, factor, etiqueta) in zip(ejes, indicadores, strict=True):
            valores = [comparacion["metricas"][nombre][campo] * factor for nombre in ("anterior", "nuevo")]
            barras = eje.bar(["Anterior", "Nuevo"], valores, color=list(COLORES.values()), width=.55)
            eje.bar_label(barras, labels=[f"{v:.3f}" for v in valores], padding=4, fontsize=10)
            eje.set_ylim(0, max(max(valores) * 1.2, 1.))
            eje.set_title(etiqueta)
            eje.grid(axis="x", visible=False)
        terminar(figura, ejes, "resumen_metricas", f"Misma demanda, irradiancia y pronósticos; {len(tabla)} días", None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--referencia", type=Path, default=RAIZ / "results/regresion_original")
    parser.add_argument("--config", type=Path, default=RAIZ / "results/calibracion_riesgo/configuracion_cvar.json")
    parser.add_argument("--salida", type=Path, default=RAIZ / "results/comparacion_mpc_chronos")
    args = parser.parse_args()
    try:
        caso = cargar_caso(args.config)
        referencia, salida = args.referencia.resolve(), args.salida.resolve()
        if salida == referencia or salida in [p.resolve() for p in caso.rutas.values()]:
            raise ValueError("salida: no puede reemplazar una entrada")
        informe, anterior = cargar_anterior(referencia)
        verificar_entradas(caso, informe, referencia)
        entradas, codigo = entradas_sha256(caso.rutas), codigo_sha256()
        guion = sha256_de(Path(__file__))
        fuente_sha256 = sha256_de(referencia / "resumen.json")
        salida.mkdir(parents=True, exist_ok=False)
        inicio = perf_counter()
        fuente = pd.read_csv(referencia / "pronosticos.csv", float_precision="round_trip")
        comparacion = comparar(caso, informe, anterior, fuente)
        for nombre in ("cargas", "horas", "baterias", "decisiones", "candidatos", "planes_elegidos"):
            comparacion["resultados"]["nuevo"][nombre].to_csv(salida / f"{nombre}_nuevo.csv", index=False)
        comparacion["diario"].to_csv(salida / "diario.csv", index=False)
        filas = []
        for campo, unidad in (("J", "Wh·p"), ("S_pond", "fracción"), ("no_atendida_wh", "Wh"),
                              ("CVaR_diario", "Wh·p"), ("servicio_critico", "fracción"),
                              ("nivel_servicio_horas", "fracción"), ("compromisos_incumplidos", "carga·h")):
            a, b = (comparacion["metricas"][n][campo] for n in ("anterior", "nuevo"))
            filas.append(dict(indicador=campo, unidad=unidad, anterior=a, nuevo=b, diferencia_nuevo_menos_anterior=b - a))
        pd.DataFrame(filas).to_csv(salida / "metricas.csv", index=False)
        graficar(salida / "figuras", caso, comparacion)
        salida_informe = dict(
            alcance="Comparación de Chronos-2 en marzo: MPC anterior y nuevo, con parámetros ya fijados en la calibración del riesgo",
            metodo="Pronósticos crudos congelados; no se carga Chronos, no se ajustan parámetros ni se ejecuta beta",
            inicio_utc=informe["inicio_utc"], fin_utc=informe["fin_utc"],
            configuracion_anterior=informe["configuracion"], configuracion_nueva=caso.configuracion,
            referencia=dict(ruta=str(referencia), resumen_sha256=fuente_sha256,
                            revision_anterior=informe["referencia_git"], archivos_sha256=informe["archivos_sha256"]),
            metricas=comparacion["metricas"], verificaciones=comparacion["verificaciones"],
            contextos=[dict(origen_utc=r["origen_utc"].isoformat(), horizonte=r["horizonte"],
                            contexto_fin_utc=r["contexto_fin_utc"].isoformat(), contexto_sha256=r["contexto_sha256"])
                       for r in comparacion["registros"]],
            semillas=informe["semillas"], entradas_sha256=entradas, codigo_sha256=codigo, guion_sha256=guion,
            duracion_s=perf_counter() - inicio, entorno=entorno(),
            advertencias=["Resultados exploratorios de 28 días, sin intervalos de confianza ni significancia inferida",
                          "lambda_R=0 amplía candidatos; no equivale al MPC original",
                          "El tiempo excluye inferencia real y no mide una mejora de velocidad frente a la evaluación original"],
            archivos_sha256={str(p.relative_to(salida)): sha256_de(p)
                            for p in sorted(salida.rglob("*")) if p.is_file()},
        )
        verificar_entradas(caso, informe, referencia)
        if (entradas_sha256(caso.rutas) != entradas or codigo_sha256() != codigo
                or sha256_de(Path(__file__)) != guion or sha256_de(referencia / "resumen.json") != fuente_sha256):
            raise ValueError("comparación: cambiaron entradas, referencia o código durante la corrida")
        temporal = salida / "resumen.json.tmp"
        try:
            temporal.write_text(json.dumps(salida_informe, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            temporal.replace(salida / "resumen.json")
        finally:
            temporal.unlink(missing_ok=True)
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\nLa salida {args.salida} puede estar incompleta; no es válida.\n")
    print(f"{salida}: J anterior={comparacion['metricas']['anterior']['J']:.6f}, "
          f"nuevo={comparacion['metricas']['nuevo']['J']:.6f}")


if __name__ == "__main__":
    main()
