"""Experimento 2×2 de pronóstico (beta, Chronos-2) por controlador (original, CVaR).

Cruza los proveedores de la evaluación original con los dos modos del MPC, con los
parámetros que fijó la calibración del riesgo (α = 0.9, λ_R = 0). Con ese peso C y D no penalizan el riesgo: eligen el mayor J esperado
entre más calendarios. Marzo ya orientó esta extensión, así que los resultados son
exploratorios y no constituyen una validación confirmatoria. `--lambda-riesgo` repite el
cruce con otro peso como sensibilidad: no sustituye el calibrado.
"""

import argparse
from copy import copy
import importlib.metadata
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from src.cli import codigo_sha256, entradas_sha256, entorno, sha256_de
from src.configuracion import parametros_mpc
from src.controlador import ALPHA_RIESGO, _diario_riesgo, _periodo, calcular_cvar, proveedores
from src.datos import cargar_caso
from src.experimento import (
    COLORES, EJE, POLITICAS, REFERENCIA, SUPUESTOS, TINTA_SECUNDARIA, TOLERANCIA_WH, _estilo, _formato,
    _registros, evaluar, exportar, metricas_servicio, resumir, resumir_diferencias, tabla_ab,
    verificar_pareo, verificar_reproducibilidad,
)
from src.pronosticos import cargar_chronos

RAIZ = Path(__file__).resolve().parents[1]
CELDAS = {"A": ("original", "beta"), "B": ("original", "chronos"), "C": ("cvar", "beta"), "D": ("cvar", "chronos")}
LAMBDA_CALIBRADO = 0.  # IDEA §7, calibración del riesgo
INTERACCION = "(D-C)-(B-A)"
CONTRASTES = {"C-A": dict(C=1, A=-1), "D-B": dict(D=1, B=-1), "B-A": dict(B=1, A=-1), "D-C": dict(D=1, C=-1),
              INTERACCION: dict(D=1, C=-1, B=-1, A=1)}
FILAS = (
    ("J realizado", "Wh·p", "J"), ("S_pond", "fracción", "S_pond"),
    ("Energía no atendida", "Wh", "no_atendida_wh"), ("Servicio crítico (p ≥ 3)", "fracción", "servicio_critico"),
    ("CVaR diario de pérdida (α = 0.9)", "Wh·p", "CVaR_diario"), ("Pérdida del peor día", "Wh·p", "peor_dia"),
    ("Compromisos incumplidos", "carga·h", "compromisos_incumplidos"),
    ("Solicitudes no comprometidas", "carga·h", "solicitudes_no_comprometidas"),
    ("Solicitudes desatendidas", "carga·h", "solicitudes_desatendidas"),
    ("Tiempo de control, sin inferencia", "s", "control_s"),
    ("Calendarios únicos por origen", "calendarios", "candidatos_por_origen"),
)
ETIQUETAS = {"A": "A — beta, original", "B": "B — Chronos-2, original", "C": "C — beta, CVaR", "D": "D — Chronos-2, CVaR"}
ETIQUETAS_CONTRASTE = {"C-A": "C − A (beta)", "D-B": "D − B (Chronos-2)", INTERACCION: "(D − C) − (B − A)"}
ADVERTENCIAS = [
    "marzo ya orientó esta extensión: resultados exploratorios, sin validación confirmatoria ni significancia",
    "CVaR diario de 28 días: la cola del 10 % equivale a 2.8 días efectivos de una sola realización",
    "`cuantil` en decisiones.csv no mide uso de las colas: un calendario igual al de la mediana queda como q0.1",
]


def parametros_cvar(lambda_riesgo=LAMBDA_CALIBRADO):
    return dict(modo_mpc="cvar", alpha_cvar=ALPHA_RIESGO, lambda_riesgo=lambda_riesgo)


def advertencia_peso(lambda_riesgo):
    if lambda_riesgo == LAMBDA_CALIBRADO:
        return "con lambda_R=0, C y D no penalizan el riesgo: la diferencia con A y B mide un conjunto de candidatos más amplio"
    return (f"lambda_R={lambda_riesgo:g} no es el peso calibrado: sensibilidad exploratoria; "
            "la diferencia con A y B combina más candidatos y la penalización")


# --- 1. Ejecución de las cuatro celdas ---

def memorizar(pronosticar):
    """Un pronóstico por origen y horizonte: A/C y B/D reciben exactamente los mismos cuantiles."""
    guardados = {}

    def envuelto(historia, origen, horizonte):
        clave = (origen, horizonte)
        if clave not in guardados:
            guardados[clave] = pronosticar(historia, origen, horizonte)
        return guardados[clave].copy()
    return envuelto


def ejecutar_2x2(caso, pronosticadores, *, inicio=None, fin=None, lambda_riesgo=LAMBDA_CALIBRADO):
    """Recorre los dos controladores; cada celda conserva sus propios estados de batería."""
    config = caso.configuracion
    if parametros_mpc(config)[0] != "original" or config["lambda_terminal"] != 0:
        raise ValueError("experimento 2×2: la configuración base debe ser el MPC original con lambda_terminal=0")
    caso_cvar = copy(caso)
    caso_cvar.configuracion = dict(config, **parametros_cvar(lambda_riesgo))
    casos = dict(original=caso, cvar=caso_cvar)
    compartidos = {nombre: memorizar(pronosticadores[nombre]) for nombre in POLITICAS}
    evaluaciones = {modo: evaluar(c, compartidos, inicio=inicio, fin=fin) for modo, c in casos.items()}
    return dict(casos=casos, evaluaciones=evaluaciones, entre_modos=verificar_entre_modos(**evaluaciones))


def verificar_entre_modos(original, cvar):
    """Por proveedor, mismas entradas y pronósticos con los dos controladores.

    Con λ_T = 0 y el mismo estado inicial, los candidatos de CVaR contienen a los originales:
    en el primer origen, su mayor J esperado no puede ser menor, sea cual sea λ_R.
    """
    pareos = {}
    for nombre in POLITICAS:
        uno, otro = original["resultados"][nombre], cvar["resultados"][nombre]
        pareos[nombre] = verificar_pareo(dict(zip(POLITICAS, (uno, otro))),
                                         dict(zip(POLITICAS, (original["registros"][nombre], cvar["registros"][nombre]))))
        for p, q in zip(uno["pronosticos"], otro["pronosticos"], strict=True):
            if (not p["crudos"].equals(q["crudos"]) or not p["corregidos"].equals(q["corregidos"])
                    or not np.array_equal(p["escenarios"], q["escenarios"])):
                raise ValueError(f"pareo: {nombre} cambia de pronóstico en {p['origen_utc'].isoformat()}")
        primer_origen = otro["decisiones"]["origen_utc"].iloc[0]
        mejor = otro["candidatos"].loc[otro["candidatos"]["origen_utc"].eq(primer_origen), "J_esperado"].max()
        if mejor < uno["decisiones"]["J_esperado"].iloc[0] - TOLERANCIA_WH:
            raise ValueError(f"pareo: los candidatos CVaR de {nombre} no contienen a los originales")
    if not original["resultados"][REFERENCIA]["cargas"].equals(cvar["resultados"][REFERENCIA]["cargas"]):
        raise ValueError("pareo: la referencia reactiva depende del controlador")
    return dict(pareos=pareos, pronosticos_identicos=True, candidatos_contenidos=True, referencia_identica=True)


def _resultado(ejecucion, celda):
    modo, proveedor = CELDAS[celda]
    return ejecucion["evaluaciones"][modo]["resultados"][proveedor]


# --- 2. Indicadores y contrastes ---

def horas_con_x_distinto(a, b):
    """Horas ejecutadas con otro compromiso; mide decisiones, no etiquetas de cuantil."""
    pares = a["cargas"].merge(b["cargas"], on=["t", "carga_id"], suffixes=("_a", "_b"), validate="one_to_one")
    return int(pares.loc[pares["x_a"].ne(pares["x_b"]), "t"].nunique())


def metricas_2x2(ejecucion):
    """Servicio, riesgo realizado y costo por celda, más las pérdidas diarias."""
    caso = ejecucion["casos"]["original"]
    config = caso.configuracion
    evaluaciones = ejecucion["evaluaciones"]
    _, _, reloj = _periodo(caso, evaluaciones["original"]["inicio_utc"], evaluaciones["original"]["fin_utc"])
    metricas, diarios = {}, []
    for celda, (modo, proveedor) in CELDAS.items():
        resultado = _resultado(ejecucion, celda)
        dia = _diario_riesgo(resultado, reloj, config["zona_horaria"])
        diarios.append(dia.assign(celda=celda))
        # La inferencia solo ocurre en el primer modo; el control se mide igual en las cuatro celdas.
        inferencia = math.fsum(fila["inferencia_s"] for fila in evaluaciones[modo]["registros"][proveedor])
        metricas[celda] = dict(
            metricas_servicio(resultado, config),
            CVaR_diario=calcular_cvar(dia["perdida"], ALPHA_RIESGO), peor_dia=float(dia["perdida"].max()),
            control_s=evaluaciones[modo]["tiempos"][f"{proveedor}_s"] - inferencia,
            candidatos_por_origen=len(resultado["candidatos"]) / len(resultado["decisiones"]))
    return metricas, pd.concat(diarios, ignore_index=True)


def tabla_2x2(metricas, configuracion):
    """Una fila por indicador con las cuatro celdas y los contrastes sobre sus totales."""
    filas = []
    baterias = tuple((f"{b['id']}: mínimo", "Wh", f"{b['id']}_minimo_wh") for b in configuracion["baterias"])
    for indicador, unidad, campo in (*FILAS, *baterias):
        valores = {celda: metricas[celda][campo] for celda in CELDAS}
        contrastes = {nombre: None if any(valores[c] is None for c in pesos)
                      else math.fsum(peso * valores[c] for c, peso in pesos.items())
                      for nombre, pesos in CONTRASTES.items()}
        filas.append(dict(indicador=indicador, unidad=unidad, campo=campo, **valores, **contrastes))
    return pd.DataFrame(filas)


def diario_2x2(diarios):
    """J y pérdida por día local; el CVaR no se suma por días, así que no entra aquí."""
    tabla = diarios.pivot(index="fecha", columns="celda", values=["J", "perdida"])
    tabla.columns = [f"{campo}_{celda}" for campo, celda in tabla.columns]
    tabla = tabla.reset_index()
    for nombre, pesos in CONTRASTES.items():
        for campo in ("J", "perdida"):
            tabla[f"delta_{campo}_{nombre}"] = sum(peso * tabla[f"{campo}_{c}"] for c, peso in pesos.items())
    return tabla


def clasificar_compensacion(delta_j, delta_cvar):
    """Más J mejora y más CVaR empeora; hay compensación solo si los dos cambios se oponen."""
    j, cvar = (0 if abs(d) <= TOLERANCIA_WH else math.copysign(1, d) for d in (delta_j, delta_cvar))
    if j == cvar == 0:
        return "sin cambio"
    if j >= 0 and cvar <= 0:
        return "mejora"
    return "empeora" if j <= 0 and cvar >= 0 else "compensación"


def resumir_contrastes(tabla, diario, ejecucion):
    """Descripción de cada contraste, sin pruebas de significancia (resultados exploratorios)."""
    totales = tabla.set_index("campo")
    filas = []
    for nombre, pesos in CONTRASTES.items():
        delta_j, delta_cvar = float(totales.loc["J", nombre]), float(totales.loc["CVaR_diario", nombre])
        dias = resumir_diferencias(pd.DataFrame(dict(delta_J=diario[f"delta_J_{nombre}"])))
        fila = dict(contraste=nombre, delta_J=delta_j, delta_CVaR_diario=delta_cvar, compensacion=None,
                    horas_con_x_distinto=None, dias=dias["dias"], media=dias["media"], mediana=dias["mediana"],
                    minimo=dias["minimo"], maximo=dias["maximo"], dias_a_favor=dias["dias_b_mayor"],
                    dias_iguales=dias["dias_iguales"], dias_en_contra=dias["dias_b_menor"])
        if nombre != INTERACCION:
            uno, otro = (_resultado(ejecucion, celda) for celda in pesos)
            fila.update(compensacion=clasificar_compensacion(delta_j, delta_cvar),
                        horas_con_x_distinto=horas_con_x_distinto(uno, otro))
        filas.append(fila)
    return pd.DataFrame(filas)


def cotejar_publicadas(metricas, resumenes, lambda_riesgo=LAMBDA_CALIBRADO):
    """A, B y la referencia reproducen la evaluación original; B y D, la comparación
    posterior a la calibración del riesgo (IDEA §7).

    D solo tiene cifras publicadas con el peso calibrado.
    """
    original = resumenes["original"]["metricas"]
    valores = dict(
        S_pond_A=(metricas["A"]["S_pond"], .9312263476, 5e-11), S_pond_B=(metricas["B"]["S_pond"], .9328742419, 5e-11),
        delta_J_B_A=(metricas["B"]["J"] - metricas["A"]["J"], 505.599, .0005),
        J_reactiva=(original[REFERENCIA]["servicio"]["J"], 285240.845, .0005),
        MAE_diurno_beta=(original["beta"]["pronostico"]["diurno"]["corregido"]["mae_w_m2"], 66.33, .005),
        MAE_diurno_chronos=(original["chronos"]["pronostico"]["diurno"]["corregido"]["mae_w_m2"], 64.20, .005),
        CVaR_diario_B=(metricas["B"]["CVaR_diario"], 3949.084, .0005),
    )
    if lambda_riesgo == LAMBDA_CALIBRADO:
        valores.update(J_D=(metricas["D"]["J"], 286406.573, .0005),
                       CVaR_diario_D=(metricas["D"]["CVaR_diario"], 3887.228, .0005))
    filas = {nombre: dict(observado=valor, publicado=esperado, tolerancia=tolerancia,
                          coincide=abs(valor - esperado) <= tolerancia)
             for nombre, (valor, esperado, tolerancia) in valores.items()}
    if not all(fila["coincide"] for fila in filas.values()):
        raise ValueError(f"experimento 2×2: diferencias con cifras publicadas: {filas}")
    return filas


# --- 3. Tabla, figuras y comando ---

def tabla_markdown(tabla):
    """Tabla legible para el artículo; los valores exactos quedan en el CSV."""
    columnas = ("C-A", "D-B", INTERACCION)
    lineas = ["| Indicador | Unidad | A | B | C | D | C − A | D − B | (D − C) − (B − A) |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for fila in tabla.to_dict("records"):
        unidad = fila["unidad"]
        valores = [_formato(fila[c], unidad) for c in CELDAS]
        for nombre in columnas:
            diferencia = fila[nombre]
            if diferencia is None or math.isnan(diferencia):
                valores.append("—")
            elif unidad == "fracción":
                valores.append(f"{100 * diferencia:+.4f} p.p.")
            else:
                valores.append(f"{diferencia:+.0f}" if unidad == "carga·h" else f"{diferencia:+.3f}")
        lineas.append("| " + " | ".join([fila["indicador"], unidad, *valores]) + " |")
    return "\n".join(lineas) + "\n"


def graficar(carpeta, tabla, contrastes, diario):
    """Tres figuras: contrastes totales, ΔJ diario y compensación entre servicio y riesgo."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    carpeta.mkdir(parents=True, exist_ok=True)
    por_nombre = contrastes.set_index("contraste")
    colores = {"C-A": COLORES["beta"], "D-B": COLORES["chronos"], INTERACCION: TINTA_SECUNDARIA}

    def guardar(figura, nombre):
        figura.savefig(carpeta / f"{nombre}.png", dpi=170, bbox_inches="tight")
        figura.savefig(carpeta / f"{nombre}.svg", bbox_inches="tight")
        plt.close(figura)

    with plt.rc_context(_estilo()):
        figura, ejes = plt.subplots(1, 2, figsize=(11, 3.4), sharey=True)
        nombres = list(ETIQUETAS_CONTRASTE)[::-1]
        for eje, campo, titulo in ((ejes[0], "delta_J", "ΔJ total (Wh·p); positivo favorece al primer término"),
                                   (ejes[1], "delta_CVaR_diario", "ΔCVaR diario (Wh·p); negativo favorece al primer término")):
            barras = eje.barh([ETIQUETAS_CONTRASTE[n] for n in nombres], por_nombre.loc[nombres, campo],
                              height=.5, color=[colores[n] for n in nombres])
            eje.bar_label(barras, fmt="%+.2f", padding=4, color=TINTA_SECUNDARIA)
            eje.margins(x=.22)
            eje.axvline(0, color=EJE, linewidth=.8)
            eje.grid(axis="y", visible=False)
            eje.set_title(titulo)
        figura.tight_layout()
        guardar(figura, "contrastes")

        fechas = pd.to_datetime(diario["fecha"])
        figura, ejes = plt.subplots(2, 1, figsize=(12, 5.6), sharex=True, sharey=True)
        for eje, nombre in zip(ejes, ("C-A", "D-B"), strict=True):
            eje.bar(fechas, diario[f"delta_J_{nombre}"], width=.7, color=colores[nombre])
            eje.axhline(0, color=EJE, linewidth=.8)
            eje.grid(axis="x", visible=False)
            eje.set_title(f"ΔJ diario, {ETIQUETAS_CONTRASTE[nombre]}; positivo favorece al modo CVaR")
            eje.set_ylabel("Wh·p")
        ejes[-1].set_xlabel("Fecha local")
        figura.tight_layout()
        guardar(figura, "diferencias_diarias")

        totales = tabla.set_index("campo")
        figura, eje = plt.subplots(figsize=(7.5, 5.2))
        eje.axhline(0, color=EJE, linewidth=.8)
        eje.axvline(0, color=EJE, linewidth=.8)
        for celda, (modo, proveedor) in CELDAS.items():
            x = totales.loc["J", celda] - totales.loc["J", "A"]
            y = totales.loc["CVaR_diario", celda] - totales.loc["CVaR_diario", "A"]
            eje.plot([x], [y], linestyle="none", marker="o" if modo == "original" else "D", markersize=9,
                     markeredgecolor=eje.get_facecolor(), markeredgewidth=1.5, color=COLORES[proveedor],
                     label=ETIQUETAS[celda])
            eje.annotate(celda, (x, y), xytext=(9, 7), textcoords="offset points", color=TINTA_SECUNDARIA)
        eje.margins(.18)
        eje.set_title("Servicio y riesgo respecto a A; abajo a la derecha es mejor")
        eje.set_xlabel("ΔJ total respecto a A (Wh·p)")
        eje.set_ylabel("ΔCVaR diario respecto a A (Wh·p)")
        eje.legend(loc="center left", bbox_to_anchor=(1.01, .5))
        figura.tight_layout()
        guardar(figura, "compensacion")


def exportar_2x2(salida, ejecucion, resumenes, tablas_ab, tabla, diario, contrastes):
    """Las salidas de la evaluación pareada por modo y, en la raíz, las tablas y figuras del cruce."""
    for modo, caso in ejecucion["casos"].items():
        carpeta = salida / modo
        carpeta.mkdir()
        exportar(carpeta, caso, ejecucion["evaluaciones"][modo], resumenes[modo], tablas_ab[modo])
    tabla.to_csv(salida / "tabla_2x2.csv", index=False)
    (salida / "tabla_2x2.md").write_text(tabla_markdown(tabla), encoding="utf-8")
    diario.to_csv(salida / "diario_2x2.csv", index=False)
    contrastes.to_csv(salida / "contrastes.csv", index=False)
    graficar(salida / "figuras", tabla, contrastes, diario)


def _registros_tabla(tabla):
    return tabla.astype(object).where(tabla.notna(), None).to_dict("records")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=RAIZ / "scripts/configuracion.json")
    parser.add_argument("--salida", type=Path, default=RAIZ / "results/experimento_2x2")
    parser.add_argument("--lambda-riesgo", type=float, default=LAMBDA_CALIBRADO)
    args = parser.parse_args()
    cvar = parametros_cvar(args.lambda_riesgo)
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
        carga_s = perf_counter() - inicio
        crudos = proveedores(caso, pipeline)
        ejecucion = ejecutar_2x2(caso, crudos, lambda_riesgo=args.lambda_riesgo)
        evaluaciones = ejecucion["evaluaciones"]
        # La repetición no usa memoria: debe volver a calcular los pronósticos.
        reproducibilidad = {modo: verificar_reproducibilidad(c, crudos, evaluaciones[modo])
                            for modo, c in ejecucion["casos"].items()}
        duracion = perf_counter() - inicio
        resumenes = {modo: resumir(c, evaluaciones[modo]) for modo, c in ejecucion["casos"].items()}
        tablas_ab = {modo: tabla_ab(c, evaluaciones[modo], resumenes[modo]) for modo, c in ejecucion["casos"].items()}
        metricas, diarios = metricas_2x2(ejecucion)
        publicadas = cotejar_publicadas(metricas, resumenes, lambda_riesgo=args.lambda_riesgo)
        tabla = tabla_2x2(metricas, config)
        diario = diario_2x2(diarios)
        contrastes = resumir_contrastes(tabla, diario, ejecucion)
        exportar_2x2(salida, ejecucion, resumenes, tablas_ab, tabla, diario, contrastes)
        versiones = entorno()
        for paquete in ("numpy", "scipy", "chronos-forecasting", "torch", "transformers", "astral", "matplotlib"):
            versiones[paquete] = importlib.metadata.version(paquete)
        informe = dict(
            alcance="experimento 2×2 en marzo; exploratorio porque ese periodo ya orientó la extensión",
            celdas={celda: dict(modo_mpc=modo, proveedor=proveedor) for celda, (modo, proveedor) in CELDAS.items()},
            inicio_utc=evaluaciones["original"]["inicio_utc"].isoformat(),
            fin_utc=evaluaciones["original"]["fin_utc"].isoformat(), periodo_local=config["periodos"]["evaluacion"],
            estado_inicial={b["id"]: b["inicial_wh"] for b in config["baterias"]},
            semillas=dict(demanda=config["semilla"], escenarios=config["semilla_escenarios"]),
            control=dict({c: config[c] for c in ("horizonte", "replanificacion", "escenarios", "lambda_terminal")},
                         cvar=cvar),
            tiempos=dict(original=evaluaciones["original"]["tiempos"], cvar=evaluaciones["cvar"]["tiempos"],
                         carga_chronos_s=carga_s, total_s=duracion),
            verificaciones=dict(fisica={m: e["fisica"] for m, e in evaluaciones.items()},
                                pareo={m: e["pareo"] for m, e in evaluaciones.items()},
                                entre_modos=ejecucion["entre_modos"], reproducibilidad=reproducibilidad,
                                publicadas=publicadas),
            metricas=metricas, tabla_2x2=_registros_tabla(tabla), contrastes=_registros_tabla(contrastes),
            pronosticos=_registros(evaluaciones["original"]), configuracion=config, configuracion_cvar=cvar,
            entradas_sha256=entradas, codigo_sha256=codigo, guion_sha256=guion,
            uv_lock_sha256=sha256_de(RAIZ / "uv.lock"), entorno=versiones,
            supuestos=[*SUPUESTOS, advertencia_peso(args.lambda_riesgo), *ADVERTENCIAS],
            archivos_sha256={str(p.relative_to(salida)): sha256_de(p) for p in sorted(salida.rglob("*")) if p.is_file()},
        )
        texto = json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if (entradas_sha256(caso.rutas) != entradas or codigo_sha256() != codigo
                or sha256_de(Path(__file__)) != guion):
            raise ValueError("experimento 2×2: cambiaron entradas o código durante el experimento")
        temporal = salida / "resumen.json.tmp"
        try:
            temporal.write_text(texto, encoding="utf-8")
            temporal.replace(salida / "resumen.json")  # solo un resumen.json completo acredita éxito
        finally:
            temporal.unlink(missing_ok=True)
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\nLa salida {args.salida} puede estar incompleta; no es válida.\n")
    filas = contrastes.set_index("contraste")
    print(f"{salida}: J_A={metricas['A']['J']:.3f}, J_B={metricas['B']['J']:.3f}, J_C={metricas['C']['J']:.3f}, "
          f"J_D={metricas['D']['J']:.3f}; interacción J={filas.loc[INTERACCION, 'delta_J']:+.3f}")


if __name__ == "__main__":
    main()
