"""T05: evaluación pareada de beta (A) frente a Chronos-2 (B) en el periodo reservado.

Reutiliza sin cambios la física de T03 y el MPC y los pronosticadores de T04 (IDEA
§§4–7): solo cambia el pronóstico. La referencia reactiva aplica la heurística de T03
con la irradiancia realizada de cada hora; tiene información perfecta de la hora en
curso, por lo que es una referencia y no una alternativa admisible.
"""

import hashlib
import math
from time import perf_counter

import numpy as np
import pandas as pd

from .controlador import _periodo, _verificar_fisica, ejecutar_mpc
from .pronosticos import mascara_noche
from .simulador import PRIORIDAD_CRITICA, simular

POLITICAS = ("beta", "chronos")  # A y B
REFERENCIA = "reactiva"
TOLERANCIA_WH = 1e-6  # misma tolerancia absoluta de los balances (IDEA §4.4)


# --- 1. Ejecución pareada y verificaciones ---

def _instrumentar(pronosticar, registro, contexto):
    """Rechaza historia no admisible y mide la inferencia de cada origen."""
    def envuelto(historia, origen, horizonte):
        origen = pd.Timestamp(origen).tz_convert("UTC")
        futuras = (historia["available_at"] > origen).sum() + (historia["fin_utc"] > origen).sum()
        if futuras or len(historia) != contexto:
            raise ValueError(f"filtración: historia no admisible para el origen {origen.isoformat()}")
        huella = hashlib.sha256(historia["ghi_w_m2"].to_numpy(dtype=float).tobytes()).hexdigest()
        inicio = perf_counter()
        crudos = pronosticar(historia, origen, horizonte)
        registro.append(dict(origen_utc=origen, horizonte=horizonte, inferencia_s=perf_counter() - inicio,
                             contexto_fin_utc=historia["fin_utc"].iloc[-1], contexto_sha256=huella))
        return crudos
    return envuelto


def _periodo_evaluacion(caso, inicio, fin):
    """Por defecto todo periodos.evaluacion; nunca fechas de desarrollo ni de calibración."""
    evaluacion = caso.configuracion["periodos"]["evaluacion"]
    inicio = pd.Timestamp(evaluacion["inicio"] if inicio is None else inicio)
    fin = pd.Timestamp(evaluacion["fin"] if fin is None else fin)
    if (inicio.tzinfo is None or fin.tzinfo is None
            or inicio < pd.Timestamp(evaluacion["inicio"]) or fin > pd.Timestamp(evaluacion["fin"])):
        raise ValueError("evaluación: el periodo debe quedar dentro de periodos.evaluacion")
    return _periodo(caso, inicio, fin)


def ejecutar_reactiva(caso, inicio, fin):
    """Heurística T03 con la GHI realizada de cada hora y el estado configurado."""
    _, _, reloj = _periodo(caso, inicio, fin)
    demanda = caso.demanda.loc[caso.demanda["t"].isin(reloj["t"])]
    solar = caso.irradiancia_simulacion.loc[caso.irradiancia_simulacion["t"].isin(reloj["t"])]
    if "inicio_utc" in solar:
        pares = solar[["t", "inicio_utc"]].sort_values("t").reset_index(drop=True)
        if not pares.equals(reloj[["t", "inicio_utc"]].reset_index(drop=True)):
            raise ValueError("realización: fechas y t no coinciden con el calendario")
    return simular(demanda, solar[["t", "ghi_w_m2"]], caso.configuracion)


def verificar_pareo(resultados, registros):
    """A y B reciben por origen la misma demanda, estado, contexto, bloques y rangos.

    Los bloques y rangos comunes son el análogo de los números aleatorios comunes
    del artículo (§3.1). Cada bloque histórico debe terminar antes del origen.
    """
    a, b = (resultados[nombre] for nombre in POLITICAS)
    claves = ["t", "carga_id", "demanda_wh", "solicitada", "prioridad_supuesta"]
    if not a["cargas"][claves].equals(b["cargas"][claves]):
        raise ValueError("pareo: A y B no evalúan la misma demanda")
    iniciales = [r["baterias"].groupby("bateria_id")["inicial_wh"].first() for r in resultados.values()]
    if any(not inicial.equals(iniciales[0]) for inicial in iniciales):
        raise ValueError("pareo: las políticas no parten del mismo estado de baterías")
    columnas = ["origen_utc", "horizonte", "contexto_fin_utc", "contexto_sha256"]
    registro_a, registro_b = (pd.DataFrame(registros[nombre])[columnas] for nombre in POLITICAS)
    if not registro_a.equals(registro_b):
        raise ValueError("pareo: A y B no reciben los mismos orígenes y contextos")
    if not registro_a["contexto_fin_utc"].eq(registro_a["origen_utc"]).all():
        raise ValueError("filtración: un contexto no termina exactamente en su origen")
    for pa, pb in zip(a["pronosticos"], b["pronosticos"], strict=True):
        if (pa["origen_utc"] != pb["origen_utc"] or pa["semilla"] != pb["semilla"]
                or not pa["bloques"].equals(pb["bloques"]) or not np.array_equal(pa["rangos"], pb["rangos"])):
            raise ValueError(f"pareo: escenarios distintos en {pa['origen_utc'].isoformat()}")
        if (pa["bloques"] + pd.Timedelta(hours=24) > pa["origen_utc"]).any():
            raise ValueError(f"filtración: bloque posterior al origen {pa['origen_utc'].isoformat()}")
    return dict(origenes=len(registro_a), filtraciones=0, entradas_comunes=True)


def evaluar(caso, pronosticadores, *, inicio=None, fin=None):
    """Ejecuta A, B y la referencia en el mismo periodo, verifica física y pareo.

    `pronosticadores` contiene los callbacks de controlador.proveedores (o dobles).
    Las tres políticas parten del estado configurado; después, cada una conserva
    sus propios estados realizados, sin igualarlos.
    """
    inicio, fin, _ = _periodo_evaluacion(caso, inicio, fin)
    contexto = caso.configuracion["pronosticos"]["contexto"]
    resultados, registros, tiempos = {}, {}, {}
    for nombre in POLITICAS:
        registros[nombre] = []
        reloj = perf_counter()
        resultados[nombre] = ejecutar_mpc(
            caso, _instrumentar(pronosticadores[nombre], registros[nombre], contexto), inicio=inicio, fin=fin)
        tiempos[f"{nombre}_s"] = perf_counter() - reloj
    reloj = perf_counter()
    resultados[REFERENCIA] = ejecutar_reactiva(caso, inicio, fin)
    tiempos[f"{REFERENCIA}_s"] = perf_counter() - reloj
    fisica = {nombre: _verificar_fisica(resultado, caso.configuracion) for nombre, resultado in resultados.items()}
    return dict(inicio_utc=inicio, fin_utc=fin, resultados=resultados, registros=registros,
                fisica=fisica, pareo=verificar_pareo(resultados, registros), tiempos=tiempos)


def verificar_reproducibilidad(caso, pronosticadores, evaluacion):
    """Repite como máximo 2H horas iniciales y exige las mismas decisiones y flujos.

    Solo compara horas ejecutadas por orígenes con horizonte completo en ambos
    recorridos: el último tramo del ensayo corto trunca H. Los cuantiles crudos
    usan la tolerancia de repetición de T02 (rtol=1e-5, atol=1e-6).
    """
    config = caso.configuracion
    hora = pd.Timedelta(hours=1)
    inicio, fin = evaluacion["inicio_utc"], evaluacion["fin_utc"]
    fin_corto = min(fin, inicio + 2 * config["horizonte"] * hora)
    origenes = list(pd.date_range(inicio, fin_corto, freq=f"{config['replanificacion']}h", inclusive="left"))
    if fin_corto < fin:
        origenes = [o for o in origenes if o + config["horizonte"] * hora <= fin_corto]
    hasta = min(fin_corto, origenes[-1] + config["replanificacion"] * hora)
    horas = set(_periodo(caso, inicio, hasta)[2]["t"])
    columnas = {"cargas": ["atendida_wh", "comprometida_wh"], "horas": ["fv_wh", "atendida_wh", "vertido_wh"],
                "baterias": ["inicial_wh", "carga_wh", "descarga_wh", "final_wh"]}
    max_wh, max_cuantiles = 0., 0.
    for nombre in POLITICAS:
        original = evaluacion["resultados"][nombre]
        repeticion = ejecutar_mpc(caso, pronosticadores[nombre], inicio=inicio, fin=fin_corto)
        for tabla, campos in columnas.items():
            uno, otro = (r[tabla].loc[r[tabla]["t"].isin(horas)].reset_index(drop=True)
                         for r in (original, repeticion))
            if len(uno) != len(otro) or (tabla == "cargas" and not uno[["t", "carga_id", "x", "z"]].equals(
                    otro[["t", "carga_id", "x", "z"]])):
                raise ValueError(f"reproducibilidad: {nombre} cambia decisiones al repetirse")
            diferencia = float(np.abs(uno[campos].to_numpy(dtype=float) - otro[campos].to_numpy(dtype=float)).max())
            if diferencia > TOLERANCIA_WH:
                raise ValueError(f"reproducibilidad: {nombre} cambia {tabla} en {diferencia:g} Wh")
            max_wh = max(max_wh, diferencia)
        for p, q in zip(original["pronosticos"], repeticion["pronosticos"]):
            if p["origen_utc"] not in origenes:
                continue
            if not p["crudos"].index.equals(q["crudos"].index) or not np.allclose(
                    q["crudos"], p["crudos"], rtol=1e-5, atol=1e-6):
                raise ValueError(f"reproducibilidad: {nombre} cambia sus cuantiles crudos")
            max_cuantiles = max(max_cuantiles, float(np.abs(q["crudos"].to_numpy() - p["crudos"].to_numpy()).max()))
    return dict(horas_comparadas=len(horas), origenes_comparados=len(origenes), max_diferencia_wh=max_wh,
                max_diferencia_cuantiles_w_m2=max_cuantiles, coincide=True)


# --- 2. Métricas de pronóstico (W/m²) ---

def tabla_pronosticos(pronosticos, real):
    """Una fila por origen y anticipación h = 1..H, con GHI realizada, crudos y corregidos."""
    tablas = []
    for p in pronosticos:
        crudos, corregidos = p["crudos"], p["corregidos"]
        tabla = pd.DataFrame({"origen_utc": p["origen_utc"], "inicio_utc": corregidos.index,
                              "anticipacion_h": np.arange(1, len(corregidos) + 1),
                              "ghi_real_w_m2": real.reindex(corregidos.index).to_numpy(dtype=float)})
        for cuantil in corregidos.columns:
            tabla[f"crudo_q{cuantil}"] = crudos[cuantil].to_numpy()
        for cuantil in corregidos.columns:
            tabla[f"q{cuantil}"] = corregidos[cuantil].to_numpy()
        tablas.append(tabla)
    tabla = pd.concat(tablas, ignore_index=True)
    if not np.isfinite(tabla["ghi_real_w_m2"]).all():
        raise ValueError("pronósticos: falta irradiancia realizada en algún intervalo")
    return tabla


def _errores(tabla, niveles, prefijo):
    """MAE y RMSE de la mediana, pinball medio y cobertura entre los cuantiles extremos."""
    if tabla.empty:
        return dict(pares=0, mae_w_m2=None, rmse_w_m2=None, pinball_w_m2=None, cobertura=None)
    real = tabla["ghi_real_w_m2"].to_numpy(dtype=float)
    cuantiles = tabla[[f"{prefijo}{q}" for q in niveles]].to_numpy(dtype=float)
    error = tabla[f"{prefijo}0.5"].to_numpy(dtype=float) - real
    residuo = real[:, None] - cuantiles
    q = np.asarray(niveles, dtype=float)
    pinball = np.maximum(q * residuo, (q - 1) * residuo)
    dentro = (cuantiles[:, 0] <= real) & (real <= cuantiles[:, -1])
    return dict(pares=len(real), mae_w_m2=float(np.abs(error).mean()),
                rmse_w_m2=float(np.sqrt(np.mean(error ** 2))), pinball_w_m2=float(pinball.mean()),
                cobertura=float(dentro.mean()))


def metricas_pronostico(tabla, configuracion):
    """Errores sobre pares (origen, anticipación); los horizontes solapados cuentan por separado.

    Diurno excluye solo intervalos completamente nocturnos (Astral), sin umbral
    sobre la verdad. «diurno_ejecutado» limita a las R primeras horas de cada origen.
    """
    niveles = configuracion["pronosticos"]["cuantiles"]
    diurno = ~mascara_noche(pd.DatetimeIndex(tabla["inicio_utc"]), configuracion)
    ejecutado = tabla["anticipacion_h"].le(configuracion["replanificacion"]).to_numpy()
    subconjuntos = {"global": np.ones(len(tabla), dtype=bool), "diurno": diurno,
                    "diurno_ejecutado": diurno & ejecutado}
    metricas = {nombre: {"corregido": _errores(tabla[mascara], niveles, "q"),
                         "crudo": _errores(tabla[mascara], niveles, "crudo_q")}
                for nombre, mascara in subconjuntos.items()}
    metricas["cobertura_nominal"] = niveles[-1] - niveles[0]
    return metricas


# --- 3. Métricas de servicio y baterías (Wh) ---

def _fraccion(cargas):
    solicitada = math.fsum(cargas["demanda_wh"])
    return math.fsum(cargas["atendida_wh"]) / solicitada if solicitada else None


def metricas_servicio(resultado, configuracion):
    """Indicadores de IDEA §7 sobre la ejecución realizada, más los análogos del artículo §4.2.

    El artículo mide nivel de servicio y faltante sobre réplicas simuladas; aquí se
    calculan sobre las horas realizadas con demanda: proporción de horas atendidas
    por completo y fracción media no atendida en las horas con déficit.
    """
    cargas, horas, baterias = (resultado[nombre] for nombre in ("cargas", "horas", "baterias"))
    resumen = resultado["resumen"]
    con_demanda = horas.loc[horas["solicitada_wh"] > 0]
    deficit = con_demanda.loc[con_demanda["no_atendida_wh"] > TOLERANCIA_WH]
    solicitada = resumen["solicitada_wh"]
    fila = dict(
        J=resumen["J"], S_pond=resumen["S_pond"], solicitada_wh=solicitada,
        comprometida_wh=resumen["comprometida_wh"], atendida_wh=resumen["atendida_wh"],
        no_atendida_wh=resumen["no_atendida_wh"],
        no_atendida_fraccion=resumen["no_atendida_wh"] / solicitada if solicitada else None,
        servicio_critico=_fraccion(cargas.loc[cargas["prioridad_supuesta"] >= PRIORIDAD_CRITICA]),
        nivel_servicio_horas=float((con_demanda["no_atendida_wh"] <= TOLERANCIA_WH).mean()) if len(con_demanda) else None,
        faltante_medio=float((deficit["no_atendida_wh"] / deficit["solicitada_wh"]).mean()) if len(deficit) else None,
        compromisos_incumplidos=int((cargas["x"].eq(1) & cargas["z"].eq(0)).sum()),
        solicitudes_no_comprometidas=int((cargas["solicitada"].eq(1) & cargas["x"].eq(0)).sum()),
        solicitudes_desatendidas=int((cargas["solicitada"].eq(1) & cargas["z"].eq(0)).sum()),
        fv_wh=resumen["fv_wh"], vertido_wh=resumen["vertido_wh"],
        carga_wh=resumen["carga_wh"], descarga_wh=resumen["descarga_wh"],
    )
    for prioridad, grupo in cargas.groupby("prioridad_supuesta"):
        fila[f"servicio_p{prioridad}"] = _fraccion(grupo)
    for bateria in configuracion["baterias"]:
        id_ = bateria["id"]
        flujos = baterias.loc[baterias["bateria_id"].eq(id_)]
        fila[f"{id_}_minimo_wh"] = float(min(flujos["inicial_wh"].min(), flujos["final_wh"].min()))
        fila[f"{id_}_medio_wh"] = float(flujos["final_wh"].mean())
        fila[f"{id_}_final_wh"] = resultado["estado_final"][id_]
        fila[f"{id_}_horas_en_minimo"] = int((flujos["final_wh"] <= bateria["minimo_wh"] + TOLERANCIA_WH).sum())
    return fila


def diario(resultados, calendario):
    """J, S_pond y energía no atendida por día local; ΔJ = J_B − J_A si están ambas ramas."""
    fechas = calendario.set_index("t")["inicio_intervalo"].dt.date
    columnas = {}
    for nombre, resultado in resultados.items():
        cargas = resultado["cargas"]
        dia = pd.DataFrame({"fecha": cargas["t"].map(fechas),
                            "beneficio": cargas["prioridad_supuesta"] * cargas["atendida_wh"],
                            "ponderada": cargas["prioridad_supuesta"] * cargas["demanda_wh"],
                            "no_atendida_wh": cargas["no_atendida_wh"]}).groupby("fecha").sum()
        columnas[f"J_{nombre}"] = dia["beneficio"]
        columnas[f"S_pond_{nombre}"] = dia["beneficio"] / dia["ponderada"].where(dia["ponderada"] > 0)
        columnas[f"no_atendida_wh_{nombre}"] = dia["no_atendida_wh"]
    tabla = pd.DataFrame(columnas)
    if set(POLITICAS) <= set(resultados):
        tabla["delta_J"] = tabla["J_chronos"] - tabla["J_beta"]
        tabla["delta_S_pond"] = tabla["S_pond_chronos"] - tabla["S_pond_beta"]
    return tabla.rename_axis("fecha").reset_index()


def resumir_diferencias(tabla):
    """Descripción de ΔJ diario, sin pruebas de significancia (resultados exploratorios)."""
    delta = tabla["delta_J"]
    return dict(dias=len(delta), media=float(delta.mean()), mediana=float(delta.median()),
                minimo=float(delta.min()), maximo=float(delta.max()),
                dias_b_mayor=int((delta > TOLERANCIA_WH).sum()),
                dias_iguales=int((delta.abs() <= TOLERANCIA_WH).sum()),
                dias_b_menor=int((delta < -TOLERANCIA_WH).sum()))


# --- 4. Resumen, tabla A/B, gráficas y comando ---

ETIQUETAS = {"beta": "A — beta", "chronos": "B — Chronos-2", REFERENCIA: "Referencia reactiva"}
COLORES = {"beta": "#2a78d6", "chronos": "#eb6834", REFERENCIA: "#1baf7a"}  # paleta validada (CVD)
TINTA, TINTA_SECUNDARIA, TINTA_TENUE = "#0b0b0b", "#52514e", "#898781"
SUPERFICIE, REJILLA, EJE = "#fcfcfb", "#e1e0d9", "#c3c2b7"
FILAS_PRONOSTICO = (
    ("MAE diurno", "W/m²", "diurno", "mae_w_m2"), ("RMSE diurno", "W/m²", "diurno", "rmse_w_m2"),
    ("Pinball diurno", "W/m²", "diurno", "pinball_w_m2"),
    ("Cobertura 0.1–0.9 diurna", "fracción", "diurno", "cobertura"),
    ("MAE diurno, h ≤ R", "W/m²", "diurno_ejecutado", "mae_w_m2"),
    ("MAE global", "W/m²", "global", "mae_w_m2"), ("RMSE global", "W/m²", "global", "rmse_w_m2"),
)
FILAS_SERVICIO = (
    ("J realizado", "Wh·p", "J"), ("S_pond", "fracción", "S_pond"),
    ("Energía no atendida", "Wh", "no_atendida_wh"), ("Energía no atendida", "fracción", "no_atendida_fraccion"),
    ("Servicio crítico (p ≥ 3)", "fracción", "servicio_critico"),
    ("Nivel de servicio horario", "fracción", "nivel_servicio_horas"),
    ("Faltante medio en horas con déficit", "fracción", "faltante_medio"),
    ("Compromisos incumplidos", "carga·h", "compromisos_incumplidos"),
    ("Solicitudes no comprometidas", "carga·h", "solicitudes_no_comprometidas"),
    ("Solicitudes desatendidas", "carga·h", "solicitudes_desatendidas"),
    ("Vertido", "Wh", "vertido_wh"),
)
SUPUESTOS = [
    "demanda sintética programada y conocida, común a A, B y referencia; no es medición ni pronóstico de demanda",
    "ERA5 es reanálisis, no sensor local; available_at = fin del intervalo es una idealización",
    "beta2 por hora local (loc=0, escala=1.05×máximo positivo) y Chronos-2 zero-shot con la misma historia de 336 h",
    "escenarios con bloques y rangos comunes por origen; no garantizan calibración conjunta",
    "MPC heurístico de T04 con lambda_terminal calibrado en desarrollo; sin garantía de optimalidad",
    "con calendario fijo no se encienden cargas no comprometidas (z ≤ x): un pronóstico pesimista reduce el servicio",
    "referencia reactiva: heurística T03 con la GHI realizada de cada hora; información perfecta de la hora, no admisible",
    "28 días y una sola realización meteorológica: resultados exploratorios, sin pruebas de significancia",
]


def _inferencias(registro):
    tiempos = [fila["inferencia_s"] for fila in registro]
    return dict(origenes=len(tiempos), media_s=float(np.mean(tiempos)), maxima_s=float(np.max(tiempos)),
                total_s=math.fsum(tiempos))


def resumir(caso, evaluacion):
    """Métricas por política, tablas de pronóstico y diferencias diarias B − A."""
    config = caso.configuracion
    resultados = evaluacion["resultados"]
    real = caso.irradiancia_simulacion.set_index("inicio_utc")["ghi_w_m2"]
    pronosticos = {n: tabla_pronosticos(resultados[n]["pronosticos"], real) for n in POLITICAS}
    metricas = {n: dict(servicio=metricas_servicio(r, config)) for n, r in resultados.items()}
    for nombre in POLITICAS:
        reservas = resultados[nombre]["decisiones"]["reserva"].value_counts().sort_index()
        metricas[nombre].update(
            pronostico=metricas_pronostico(pronosticos[nombre], config),
            inferencia=_inferencias(evaluacion["registros"][nombre]),
            reservas_elegidas={f"{reserva:g}": int(veces) for reserva, veces in reservas.items()})
    por_dia = diario(resultados, caso.calendario)
    return dict(metricas=metricas, pronosticos=pronosticos, diario=por_dia,
                diferencias_diarias=resumir_diferencias(por_dia))


def tabla_ab(caso, evaluacion, resumen):
    """Una fila por indicador: A, B, diferencia B − A y referencia reactiva."""
    metricas = resumen["metricas"]
    filas = []

    def agregar(grupo, indicador, unidad, valores):
        a, b = valores.get("beta"), valores.get("chronos")
        diferencia = b - a if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
        filas.append(dict(grupo=grupo, indicador=indicador, unidad=unidad, beta=a, chronos=b,
                          diferencia_b_menos_a=diferencia, reactiva=valores.get(REFERENCIA)))

    for indicador, unidad, subconjunto, campo in FILAS_PRONOSTICO:
        agregar("Pronóstico", indicador, unidad,
                {n: metricas[n]["pronostico"][subconjunto]["corregido"][campo] for n in POLITICAS})
    for indicador, unidad, campo in FILAS_SERVICIO:
        agregar("Servicio", indicador, unidad, {n: m["servicio"][campo] for n, m in metricas.items()})
    for bateria in caso.configuracion["baterias"]:
        for sufijo, indicador, unidad in (("minimo_wh", "mínimo", "Wh"), ("medio_wh", "medio", "Wh"),
                                          ("final_wh", "final", "Wh"), ("horas_en_minimo", "horas en mínimo", "h")):
            agregar("Baterías", f"{bateria['id']}: {indicador}", unidad,
                    {n: m["servicio"][f"{bateria['id']}_{sufijo}"] for n, m in metricas.items()})
    agregar("Consistencia", "Violaciones físicas", "conteo",
            {n: f["violaciones_fisicas"] for n, f in evaluacion["fisica"].items()})
    agregar("Consistencia", "Filtraciones de futuro", "conteo",
            {n: evaluacion["pareo"]["filtraciones"] for n in POLITICAS})
    agregar("Recursos", "Inferencia media por origen", "s",
            {n: metricas[n]["inferencia"]["media_s"] for n in POLITICAS})
    agregar("Recursos", "Ejecución de la política", "s",
            {n: evaluacion["tiempos"][f"{n}_s"] for n in (*POLITICAS, REFERENCIA)})
    return pd.DataFrame(filas)


def _formato(valor, unidad):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    if unidad == "fracción":
        return f"{100 * valor:.2f} %"
    return f"{valor:.0f}" if unidad in ("conteo", "carga·h", "h") else f"{valor:.3f}"


def tabla_markdown(tabla):
    """Tabla A/B legible para el artículo; los valores exactos quedan en el CSV."""
    lineas = ["| Grupo | Indicador | Unidad | A — beta | B — Chronos-2 | B − A | Referencia reactiva |",
              "|---|---|---|---:|---:|---:|---:|"]
    for fila in tabla.to_dict("records"):
        unidad = fila["unidad"]
        valores = [_formato(fila[c], unidad) for c in ("beta", "chronos", "diferencia_b_menos_a", "reactiva")]
        if unidad == "fracción" and valores[2] != "—":
            valores[2] = f"{100 * fila['diferencia_b_menos_a']:+.2f} p.p."
        lineas.append("| " + " | ".join([fila["grupo"], fila["indicador"], unidad, *valores]) + " |")
    return "\n".join(lineas) + "\n"


def _estilo():
    """Superficie, tinta y rejilla recesivas; el color queda reservado a los datos."""
    return {"figure.facecolor": SUPERFICIE, "axes.facecolor": SUPERFICIE, "savefig.facecolor": SUPERFICIE,
            "axes.edgecolor": EJE, "axes.linewidth": 0.8, "axes.grid": True, "grid.color": REJILLA,
            "grid.linewidth": 0.6, "grid.linestyle": "-", "axes.spines.top": False, "axes.spines.right": False,
            "text.color": TINTA, "axes.labelcolor": TINTA_SECUNDARIA, "axes.titlecolor": TINTA,
            "axes.titlesize": 11, "axes.titlelocation": "left", "xtick.color": TINTA_TENUE,
            "ytick.color": TINTA_TENUE, "legend.frameon": False, "font.size": 9, "lines.linewidth": 1.5,
            "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round"}


def graficar(carpeta, caso, evaluacion, resumen):
    """Cuatro figuras PNG en hora local; los datos de cada una están en los CSV exportados."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    zona = caso.configuracion["zona_horaria"]
    replanificacion = caso.configuracion["replanificacion"]
    niveles = caso.configuracion["pronosticos"]["cuantiles"]
    resultados = evaluacion["resultados"]
    hora_local = caso.calendario.set_index("t")["inicio_intervalo"]
    carpeta.mkdir(exist_ok=True)
    with plt.rc_context(_estilo()):
        figura, ejes = plt.subplots(len(POLITICAS), 1, figsize=(12, 6.5), sharex=True, sharey=True)
        for eje, nombre in zip(ejes, POLITICAS):
            tabla = resumen["pronosticos"][nombre]
            tabla = tabla.loc[tabla["anticipacion_h"] <= replanificacion].sort_values("inicio_utc")
            fechas = pd.DatetimeIndex(tabla["inicio_utc"]).tz_convert(zona)
            eje.fill_between(fechas, tabla[f"q{niveles[0]}"], tabla[f"q{niveles[-1]}"], color=COLORES[nombre],
                             alpha=0.15, linewidth=0, label=f"Cuantiles {niveles[0]:g}–{niveles[-1]:g}")
            eje.plot(fechas, tabla["ghi_real_w_m2"], color=TINTA, linewidth=1, label="ERA5 realizada")
            eje.plot(fechas, tabla["q0.5"], color=COLORES[nombre], label="Mediana pronosticada")
            eje.set_title(f"{ETIQUETAS[nombre]}: primeras {replanificacion} h de cada origen")
            eje.set_ylabel("GHI media horaria (W/m²)")
            eje.legend(loc="upper right", ncols=3)
        ejes[-1].set_xlabel(f"Hora local ({zona})")
        figura.tight_layout()
        figura.savefig(carpeta / "irradiancia.png", dpi=150)
        plt.close(figura)

        por_dia = resumen["diario"]
        fechas = pd.to_datetime(por_dia["fecha"])
        figura, ejes = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True)
        for nombre in resultados:
            estilo = dict(color=COLORES[nombre], marker="o", markersize=4, label=ETIQUETAS[nombre])
            ejes[0].plot(fechas, por_dia[f"S_pond_{nombre}"], **estilo)
            ejes[1].plot(fechas, por_dia[f"no_atendida_wh_{nombre}"], **estilo)
        ejes[0].set_title("Fracción ponderada de demanda atendida por día (S_pond diario)")
        ejes[0].set_ylabel("S_pond")
        ejes[1].set_title("Energía solicitada no atendida por día")
        ejes[1].set_ylabel("Wh")
        ejes[1].set_xlabel("Fecha local")
        ejes[0].legend(loc="lower left", ncols=3)
        figura.tight_layout()
        figura.savefig(carpeta / "demanda_atendida.png", dpi=150)
        plt.close(figura)

        baterias = caso.configuracion["baterias"]
        figura, ejes = plt.subplots(len(baterias), 1, figsize=(12, 3.2 * len(baterias)), sharex=True,
                                    squeeze=False)
        for eje, bateria in zip(ejes[:, 0], baterias):
            for nombre, resultado in resultados.items():
                flujos = resultado["baterias"].loc[resultado["baterias"]["bateria_id"].eq(bateria["id"])]
                fin_hora = pd.DatetimeIndex(flujos["t"].map(hora_local)) + pd.Timedelta(hours=1)
                eje.plot(fin_hora, flujos["final_wh"], color=COLORES[nombre], linewidth=1.2,
                         label=ETIQUETAS[nombre])
            for limite, texto in ((bateria["minimo_wh"], "mínimo"), (bateria["maximo_wh"], "máximo")):
                eje.axhline(limite, color=EJE, linewidth=0.8)
                eje.annotate(texto, (1, limite), xycoords=("axes fraction", "data"), xytext=(4, 0),
                             textcoords="offset points", va="center", color=TINTA_TENUE)
            eje.set_title(f"Energía almacenada en {bateria['id']} al final de cada hora")
            eje.set_ylabel("Wh")
        ejes[0, 0].legend(loc="lower left", ncols=3)
        ejes[-1, 0].set_xlabel(f"Hora local ({zona})")
        figura.tight_layout()
        figura.savefig(carpeta / "baterias.png", dpi=150)
        plt.close(figura)

        figura, eje = plt.subplots(figsize=(12, 3.8))
        eje.bar(fechas, por_dia["delta_J"], width=0.4, color=TINTA_SECUNDARIA)
        eje.axhline(0, color=EJE, linewidth=0.8)
        eje.set_title("ΔJ diario = J(B, Chronos-2) − J(A, beta); positivo favorece a Chronos-2")
        eje.set_ylabel("Wh·p")
        eje.set_xlabel("Fecha local")
        figura.tight_layout()
        figura.savefig(carpeta / "diferencias_diarias.png", dpi=150)
        plt.close(figura)


def exportar(salida, caso, evaluacion, resumen, tabla):
    """CSV por política (columna `politica`), pronósticos, diario, tabla A/B y figuras."""
    resultados = evaluacion["resultados"]
    for nombre in ("cargas", "horas", "baterias"):
        pd.concat([r[nombre].assign(politica=p) for p, r in resultados.items()], ignore_index=True).to_csv(
            salida / f"{nombre}.csv", index=False)
    pd.concat([resultados[p]["decisiones"].assign(politica=p) for p in POLITICAS], ignore_index=True).to_csv(
        salida / "decisiones.csv", index=False)
    pd.concat([resumen["pronosticos"][p].assign(proveedor=p) for p in POLITICAS], ignore_index=True).to_csv(
        salida / "pronosticos.csv", index=False)
    resumen["diario"].to_csv(salida / "diario.csv", index=False)
    tabla.to_csv(salida / "tabla_ab.csv", index=False)
    (salida / "tabla_ab.md").write_text(tabla_markdown(tabla), encoding="utf-8")
    graficar(salida / "figuras", caso, evaluacion, resumen)


def _registros(evaluacion):
    """Semillas, bloques, correcciones, contexto e inferencia de cada origen y proveedor."""
    filas = []
    for nombre in POLITICAS:
        for p, r in zip(evaluacion["resultados"][nombre]["pronosticos"], evaluacion["registros"][nombre]):
            filas.append(dict(proveedor=nombre, origen_utc=p["origen_utc"].isoformat(), horizonte=r["horizonte"],
                              inferencia_s=r["inferencia_s"], contexto_sha256=r["contexto_sha256"],
                              semilla=p["semilla"], bloques=[f.isoformat() for f in p["bloques"]],
                              correcciones=p["correcciones"]))
    return filas


def main():
    """Evalúa A/B en el periodo reservado y exporta tabla, gráficas y registro (T05)."""
    import argparse
    import importlib.metadata
    import json
    from pathlib import Path

    from .cli import codigo_sha256, entradas_sha256, entorno, sha256_de
    from .controlador import proveedores
    from .datos import cargar_caso
    from .pronosticos import cargar_chronos

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--config", type=Path, default=Path("scripts/configuracion.json"))
    parser.add_argument("--salida", type=Path, default=Path("results/t05_evaluacion"))
    args = parser.parse_args()
    try:
        caso = cargar_caso(args.config)
        config = caso.configuracion
        salida = args.salida.resolve()
        if salida == args.config.resolve() or salida in [p.resolve() for p in caso.rutas.values()]:
            raise ValueError("salida: no puede reemplazar una entrada")
        salida.mkdir(parents=True, exist_ok=False)
        entradas_antes, codigo_antes = entradas_sha256(caso.rutas), codigo_sha256()
        inicio = perf_counter()
        opciones = config["pronosticos"]
        pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])
        carga_s = perf_counter() - inicio
        pronosticadores = proveedores(caso, pipeline)
        evaluacion = evaluar(caso, pronosticadores)
        reproducibilidad = verificar_reproducibilidad(caso, pronosticadores, evaluacion)
        duracion = perf_counter() - inicio
        resumen = resumir(caso, evaluacion)
        tabla = tabla_ab(caso, evaluacion, resumen)
        exportar(salida, caso, evaluacion, resumen, tabla)
        versiones = entorno()
        for paquete in ("numpy", "scipy", "chronos-forecasting", "torch", "transformers", "astral", "matplotlib"):
            versiones[paquete] = importlib.metadata.version(paquete)
        informe = dict(
            alcance="T05: evaluación pareada A/B en el periodo reservado; resultados exploratorios",
            hipotesis="Chronos-2 puede reducir el error de irradiancia y mejorar la demanda ponderada "
                      "atendida; también puede empatar o rendir peor (IDEA §1)",
            inicio_utc=evaluacion["inicio_utc"].isoformat(), fin_utc=evaluacion["fin_utc"].isoformat(),
            periodo_local=config["periodos"]["evaluacion"],
            estado_inicial={b["id"]: b["inicial_wh"] for b in config["baterias"]},
            semillas={"demanda": config["semilla"], "escenarios": config["semilla_escenarios"]},
            control={c: config[c] for c in ("horizonte", "replanificacion", "escenarios", "lambda_terminal")},
            tiempos=dict(evaluacion["tiempos"], carga_chronos_s=carga_s, total_s=duracion),
            verificaciones=dict(fisica=evaluacion["fisica"], pareo=evaluacion["pareo"],
                                reproducibilidad=reproducibilidad),
            metricas=resumen["metricas"], diferencias_diarias=resumen["diferencias_diarias"],
            tabla_ab=tabla.astype(object).where(tabla.notna(), None).to_dict("records"),
            pronosticos=_registros(evaluacion), configuracion=config,
            entradas_sha256=entradas_antes, codigo_sha256=codigo_antes, entorno=versiones,
            uv_lock_sha256=sha256_de(Path(__file__).resolve().parents[1] / "uv.lock"), supuestos=SUPUESTOS,
        )
        texto = json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if codigo_sha256() != codigo_antes or entradas_sha256(caso.rutas) != entradas_antes:
            raise ValueError("evaluación: cambiaron entradas o código durante el experimento")
        temporal = salida / "resumen.json.tmp"
        try:
            temporal.write_text(texto, encoding="utf-8")
            temporal.replace(salida / "resumen.json")  # solo un resumen.json completo acredita éxito
        finally:
            temporal.unlink(missing_ok=True)
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\nLa salida {args.salida} puede estar incompleta; no es válida.\n")
    servicio = {n: resumen["metricas"][n]["servicio"]["S_pond"] for n in POLITICAS}
    print(f"{salida}: S_pond beta={servicio['beta']:.6f}, chronos={servicio['chronos']:.6f}")


if __name__ == "__main__":
    main()
