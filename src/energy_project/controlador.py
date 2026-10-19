"""T04: escenarios empíricos y MPC heurístico común (adaptación de IDEA §§5–6).

La reserva genera compromisos; su evaluación y ejecución conservan la física
de T03. El artículo original no usa este MPC ni su puntuación esperada.
"""

import math

import numpy as np
import pandas as pd

from .configuracion import numero
from .pronosticos import contexto_admisible, corregir_cuantiles, validar_cuantiles
from .simulador import _estado_inicial, simular

RESERVAS = (0., 0.25, 0.5, 0.75)


def _validar_cuantiles_horarios(tabla, origen, horizonte, cuantiles):
    """Exige exactamente los intervalos y niveles solicitados, antes de corregir."""
    validar_cuantiles(list(cuantiles))
    indice = pd.date_range(origen, periods=horizonte, freq="h", name="inicio_utc")
    if (not isinstance(tabla, pd.DataFrame) or tabla.empty
            or not tabla.index.equals(indice) or list(tabla.columns) != list(cuantiles)
            or not np.isfinite(tabla.to_numpy(dtype=float)).all()):
        raise ValueError("pronóstico: tiempos, cuantiles o valores distintos de los solicitados")


def generar_escenarios(cuantiles, historico, origen, configuracion):
    """Mismos rangos históricos N×H para cualquier proveedor en un mismo origen.

    Bloques diarios alineados, empates promedio y colas constantes. Esta
    aproximación no garantiza calibración conjunta ni marginales exactas.
    """
    horizonte = len(cuantiles)
    numero(horizonte, "horizonte de escenarios", minimo=1, maximo=24, entero=True)
    contexto = configuracion["pronosticos"]["contexto"]
    ventana = contexto_admisible(historico, origen, contexto)
    origen = pd.Timestamp(origen).tz_convert("UTC")
    _validar_cuantiles_horarios(cuantiles, origen, horizonte, configuracion["pronosticos"]["cuantiles"])
    valores = cuantiles.to_numpy(dtype=float)
    if (valores < 0).any() or (np.diff(valores, axis=1) < 0).any():
        raise ValueError("escenarios: se requieren cuantiles corregidos y no decrecientes")
    bloques = len(ventana) // 24
    if not bloques:
        raise ValueError("escenarios: se requiere al menos un bloque histórico completo de 24 h")
    # La ventana termina en el origen: estos bloques empiezan a su misma hora local.
    ventana = ventana.iloc[-24 * bloques:]
    historia = ventana["ghi_w_m2"].to_numpy(dtype=float).reshape(bloques, 24)
    rangos = (pd.DataFrame(historia).rank(axis=0, method="average").to_numpy() - 0.5) / bloques
    numero(configuracion["escenarios"], "escenarios", minimo=1, entero=True)
    numero(configuracion["semilla_escenarios"], "semilla_escenarios", entero=True)
    semilla = [configuracion["semilla_escenarios"], int(origen.timestamp() // 3600) % (2 ** 64)]
    rng = np.random.default_rng(np.random.SeedSequence(semilla))
    indices = rng.integers(bloques, size=configuracion["escenarios"])
    rangos = rangos[indices, :horizonte]
    escenarios = np.column_stack([
        np.interp(rangos[:, h], cuantiles.columns.to_numpy(dtype=float), valores[h])
        for h in range(horizonte)
    ])
    inicios = pd.DatetimeIndex(ventana["inicio_utc"].iloc[::24])
    return dict(escenarios=escenarios, rangos=rangos, bloques=inicios.take(indices), semilla=semilla)


def _calendario_utc(calendario):
    """Una sola correspondencia explícita entre t y las horas absolutas."""
    if not {"t", "inicio_intervalo"} <= set(calendario.columns) or calendario.empty:
        raise ValueError("calendario: faltan t/inicio_intervalo u horas")
    tabla = calendario[["t", "inicio_intervalo"]].copy()
    for t in tabla["t"].tolist():
        numero(t, "calendario.t", entero=True)
    indice = pd.DatetimeIndex(tabla["inicio_intervalo"])
    if indice.tz is None or indice.hasnans:
        raise ValueError("calendario: las fechas requieren zona horaria")
    tabla["inicio_utc"] = indice.tz_convert("UTC")
    tabla = tabla.sort_values("t")
    if (tabla["t"].duplicated().any() or tabla["inicio_utc"].duplicated().any()
            or tabla["t"].tolist() != list(range(int(tabla.t.iloc[0]), int(tabla.t.iloc[0]) + len(tabla)))
            or not pd.DatetimeIndex(tabla["inicio_utc"]).equals(
                pd.date_range(tabla.inicio_utc.iloc[0], periods=len(tabla), freq="h"))):
        raise ValueError("calendario: se requieren claves únicas y horas consecutivas")
    return tabla


def planificar_mpc(demanda, calendario, cuantiles, escenarios, configuracion, *, estado_inicial=None):
    """Propone cuatro calendarios y evalúa cada plan fijo en todos los escenarios.

    No recibe irradiancia realizada. Empates de score conservan la menor reserva.
    """
    horizonte = len(cuantiles)
    numero(horizonte, "horizonte MPC", minimo=1, maximo=24, entero=True)
    _validar_cuantiles_horarios(cuantiles, cuantiles.index[0], horizonte,
                              configuracion["pronosticos"]["cuantiles"])
    valores = cuantiles.to_numpy(dtype=float)
    if (valores < 0).any() or (np.diff(valores, axis=1) < 0).any():
        raise ValueError("MPC: se requieren cuantiles corregidos")
    escenarios = np.asarray(escenarios, dtype=float)
    if (escenarios.shape != (configuracion["escenarios"], horizonte)
            or not np.isfinite(escenarios).all() or (escenarios < 0).any()):
        raise ValueError("escenarios: se requiere una matriz finita y no negativa N×H")
    peso = numero(configuracion["lambda_terminal"], "lambda_terminal")
    reloj = _calendario_utc(calendario).set_index("inicio_utc").reindex(cuantiles.index)
    if reloj["t"].isna().any():
        raise ValueError("calendario: faltan horas del pronóstico")
    solar = pd.DataFrame({"t": reloj["t"].to_numpy(dtype=int), "ghi_w_m2": cuantiles[0.5].to_numpy()})
    estado = _estado_inicial(configuracion["baterias"], estado_inicial)
    planes, filas, evaluados = [], [], {}
    for reserva in RESERVAS:
        candidato = simular(demanda, solar, configuracion, estado_inicial=estado, reserva=reserva)
        plan = candidato["cargas"][["t", "carga_id", "x"]].copy()
        planes.append(plan)
        clave = plan["x"].to_numpy().tobytes()
        # Reservas distintas pueden proponer exactamente el mismo calendario.
        if clave not in evaluados:
            beneficios, terminales = [], []
            for trayectoria in escenarios:
                resultado = simular(demanda, solar.assign(ghi_w_m2=trayectoria), configuracion,
                                    estado_inicial=estado, compromisos=plan)
                beneficios.append(resultado["resumen"]["J"])
                terminales.append(math.fsum(
                    b["eficiencia_descarga"] * (resultado["estado_final"][b["id"]] - b["minimo_wh"])
                    for b in configuracion["baterias"]))
            evaluados[clave] = (math.fsum(beneficios) / len(beneficios),
                                math.fsum(terminales) / len(terminales))
        beneficio, terminal = evaluados[clave]
        filas.append(dict(reserva=reserva, J_esperado=beneficio, terminal_wh=terminal,
                          score=beneficio + peso * terminal))
    puntuaciones = pd.DataFrame(filas)
    elegido = int(puntuaciones["score"].idxmax())
    return dict(compromisos=planes[elegido], puntuaciones=puntuaciones,
                reserva=RESERVAS[elegido], planes=planes)


def _periodo(caso, inicio, fin):
    inicio, fin = pd.Timestamp(inicio), pd.Timestamp(fin)
    if (inicio.tzinfo is None or fin.tzinfo is None or inicio >= fin
            or inicio != inicio.floor("h") or fin != fin.floor("h")):
        raise ValueError("periodo: se requieren horas con zona e inicio < fin")
    inicio, fin = inicio.tz_convert("UTC"), fin.tz_convert("UTC")
    reloj = _calendario_utc(caso.calendario)
    reloj = reloj.loc[reloj.inicio_utc.ge(inicio) & reloj.inicio_utc.lt(fin)]
    if not pd.DatetimeIndex(reloj["inicio_utc"]).equals(pd.date_range(inicio, fin, freq="h", inclusive="left")):
        raise ValueError("periodo: el calendario no cubre todas las horas solicitadas")
    config = caso.configuracion
    numero(config["horizonte"], "horizonte", minimo=1, maximo=24, entero=True)
    numero(config["replanificacion"], "replanificacion", minimo=1, maximo=config["horizonte"], entero=True)
    return inicio, fin, reloj


def _predecir(caso, pronosticar, origen, horizonte):
    config = caso.configuracion
    historia = contexto_admisible(caso.irradiancia_historica, origen, config["pronosticos"]["contexto"])
    crudos = pronosticar(historia.copy(), origen, horizonte)
    _validar_cuantiles_horarios(crudos, origen, horizonte, config["pronosticos"]["cuantiles"])
    corregidos, diagnostico = corregir_cuantiles(crudos, config)
    muestreo = generar_escenarios(corregidos, historia, origen, config)
    return dict(crudos=crudos, corregidos=corregidos, correcciones=diagnostico, **muestreo)


def _ejecutar_bloque(caso, reloj, origen, fin, prediccion, estado, configuracion):
    indices = prediccion["corregidos"].index
    ventana = reloj.loc[reloj.inicio_utc.isin(indices)]
    demanda = caso.demanda.loc[caso.demanda["t"].isin(ventana["t"])]
    plan = planificar_mpc(demanda, ventana, prediccion["corregidos"], prediccion["escenarios"],
                          configuracion, estado_inicial=estado)
    hasta = min(fin, origen + pd.Timedelta(hours=configuracion["replanificacion"]))
    ejecutadas = ventana.loc[ventana.inicio_utc.lt(hasta), "t"]
    demanda = demanda.loc[demanda["t"].isin(ejecutadas)]
    # La realización se consulta únicamente después de elegir los compromisos.
    solar = caso.irradiancia_simulacion.loc[caso.irradiancia_simulacion["t"].isin(ejecutadas)]
    if "inicio_utc" in solar:
        pares = solar[["t", "inicio_utc"]].sort_values("t").reset_index(drop=True)
        esperado = ventana.loc[ventana["t"].isin(ejecutadas), ["t", "inicio_utc"]].reset_index(drop=True)
        if not pares.equals(esperado):
            raise ValueError("realización: fechas y t no coinciden con el calendario")
    resultado = simular(demanda, solar, configuracion, estado_inicial=estado,
                        compromisos=plan["compromisos"].loc[plan["compromisos"]["t"].isin(ejecutadas)])
    fila = plan["puntuaciones"].loc[plan["puntuaciones"].reserva.eq(plan["reserva"])].iloc[0].to_dict()
    fila.update(origen_utc=origen, horas_ejecutadas=len(ejecutadas))
    return resultado, fila


def _unir_resultados(resultados, decisiones, pronosticos):
    tablas = {nombre: pd.concat([r[nombre] for r in resultados], ignore_index=True)
              for nombre in ("cargas", "horas", "baterias")}
    resumen = {campo: math.fsum(r["resumen"][campo] for r in resultados)
               for campo in resultados[0]["resumen"] if campo != "S_pond"}
    cargas = tablas["cargas"]
    denominador = math.fsum(cargas["prioridad_supuesta"] * cargas["demanda_wh"])
    resumen["S_pond"] = resumen["J"] / denominador if denominador else None
    return dict(**tablas, estado_final=resultados[-1]["estado_final"].copy(), resumen=resumen,
                decisiones=pd.DataFrame(decisiones), pronosticos=pronosticos)


def ejecutar_mpc(caso, pronosticar, *, inicio, fin, estado_inicial=None):
    """Recorre [inicio, fin); callback(historia_admisible, origen_utc, horizonte).

    Devuelve las tablas de T03, decisiones y pronósticos crudos/corregidos con
    rangos y bloques. Solo las observaciones cerradas llegan al callback.
    """
    origen, fin, reloj = _periodo(caso, inicio, fin)
    config = caso.configuracion
    estado = _estado_inicial(config["baterias"], estado_inicial)
    resultados, decisiones, pronosticos = [], [], []
    while origen < fin:
        horizonte = min(config["horizonte"], int((fin - origen) / pd.Timedelta(hours=1)))
        prediccion = _predecir(caso, pronosticar, origen, horizonte)
        resultado, decision = _ejecutar_bloque(caso, reloj, origen, fin, prediccion, estado, config)
        resultados.append(resultado)
        decisiones.append(decision)
        pronosticos.append(dict(origen_utc=origen, **prediccion))
        estado = resultado["estado_final"]
        origen += pd.Timedelta(hours=config["replanificacion"])
    return _unir_resultados(resultados, decisiones, pronosticos)

