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
