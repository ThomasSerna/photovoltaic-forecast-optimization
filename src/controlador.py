"""Escenarios empíricos y MPC heurístico común (adaptación de IDEA §§5–6).

La reserva genera compromisos; su evaluación y ejecución conservan la física
del simulador. El artículo original no usa este MPC ni su puntuación esperada.
"""

import hashlib
import math

import numpy as np
import pandas as pd

from .configuracion import numero, parametros_mpc
from .pronosticos import (
    cargar_chronos, contexto_admisible, corregir_cuantiles, pronosticar_beta,
    pronosticar_chronos, validar_cuantiles,
)
from .simulador import PRIORIDAD_CRITICA, _estado_inicial, simular

RESERVAS = (0., 0.25, 0.5, 0.75)
PESOS_TERMINALES = (0., 0.25, 0.5, 0.75)
CALIBRACION_INICIO = "2025-01-06T00:00:00-05:00"
CALIBRACION_FIN = "2025-01-13T00:00:00-05:00"
RIESGO_INICIO = "2025-01-06T00:00:00-05:00"
RIESGO_CORTE = "2025-02-17T00:00:00-05:00"
RIESGO_FIN = "2025-03-03T00:00:00-05:00"
PESOS_RIESGO = (0., 0.25, 0.5, 1., 2.)
ALPHA_RIESGO = 0.9


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


def calcular_cvar(perdidas, alpha):
    """Media de la peor masa 1-alpha, incluida la fracción del escenario frontera."""
    numero(alpha, "alpha_cvar", maximo=1, estricto=True)
    if alpha == 1:
        raise ValueError("alpha_cvar: debe ser menor que 1")
    perdidas = np.asarray(perdidas, dtype=float)
    if (perdidas.ndim != 1 or not len(perdidas)
            or not np.isfinite(perdidas).all() or (perdidas < 0).any()):
        raise ValueError("CVaR: se requieren pérdidas finitas, no negativas y no vacías")
    masa = len(perdidas) * (1 - alpha)
    pesos = np.clip(masa - np.arange(len(perdidas)), 0, 1)
    return math.fsum(np.sort(perdidas)[::-1] * pesos) / masa


def _evaluar_candidatos(demanda, calendario, cuantiles, escenarios, configuracion, *, estado_inicial=None):
    """Estadísticas de planes fijos; independientes del peso de su puntuación."""
    modo, alpha, _ = parametros_mpc(configuracion)
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
    numero(configuracion["lambda_terminal"], "lambda_terminal")
    reloj = _calendario_utc(calendario).set_index("inicio_utc").reindex(cuantiles.index)
    if reloj["t"].isna().any():
        raise ValueError("calendario: faltan horas del pronóstico")
    solar = pd.DataFrame({"t": reloj["t"].to_numpy(dtype=int), "ghi_w_m2": cuantiles[0.5].to_numpy()})
    estado = _estado_inicial(configuracion["baterias"], estado_inicial)
    planes, filas, evaluados = [], [], {}
    for reserva in RESERVAS:
        for cuantil in ((0.5,) if modo == "original" else (0.1, 0.5, 0.9)):
            candidato = simular(demanda, solar.assign(ghi_w_m2=cuantiles[cuantil].to_numpy()),
                                configuracion, estado_inicial=estado, reserva=reserva)
            plan = candidato["cargas"][["t", "carga_id", "x"]].copy()
            clave = plan["x"].to_numpy().tobytes()
            if modo == "cvar" and clave in evaluados:
                continue
            planes.append(plan)
            # Cuantiles y reservas distintos pueden proponer el mismo calendario.
            if clave not in evaluados:
                beneficios, terminales, perdidas = [], [], []
                trayectorias_evaluadas = {}
                for trayectoria in escenarios:
                    huella = trayectoria.tobytes()
                    if huella not in trayectorias_evaluadas:
                        resultado = simular(demanda, solar.assign(ghi_w_m2=trayectoria), configuracion,
                                            estado_inicial=estado, compromisos=plan)
                        cargas = resultado["cargas"]
                        trayectorias_evaluadas[huella] = (
                            resultado["resumen"]["J"], math.fsum(cargas.prioridad_supuesta * cargas.no_atendida_wh),
                            math.fsum(b["eficiencia_descarga"] * (resultado["estado_final"][b["id"]] - b["minimo_wh"])
                                      for b in configuracion["baterias"]))
                    beneficio, perdida, terminal = trayectorias_evaluadas[huella]
                    beneficios.append(beneficio)
                    perdidas.append(perdida)
                    terminales.append(terminal)
                evaluados[clave] = dict(
                    candidato_id=len(evaluados), J_esperado=math.fsum(beneficios) / len(beneficios),
                    terminal_wh=math.fsum(terminales) / len(terminales),
                    perdida_media=math.fsum(perdidas) / len(perdidas), CVaR=calcular_cvar(perdidas, alpha))
            estadisticas = evaluados[clave]
            filas.append(dict(**estadisticas, reserva=reserva, cuantil=cuantil))
    return dict(planes=planes, puntuaciones=pd.DataFrame(filas))


def _seleccionar_plan(evaluacion, configuracion):
    modo, alpha, peso_riesgo = parametros_mpc(configuracion)
    puntuaciones = evaluacion["puntuaciones"].copy()
    puntuaciones["score"] = (puntuaciones.J_esperado - peso_riesgo * puntuaciones.CVaR if modo == "cvar"
                             else puntuaciones.J_esperado + configuracion["lambda_terminal"] * puntuaciones.terminal_wh)
    puntuaciones = puntuaciones.assign(modo_mpc=modo, alpha_cvar=alpha, lambda_riesgo=peso_riesgo)
    elegido = int(puntuaciones["score"].idxmax())
    puntuaciones["elegido"] = puntuaciones.index == elegido
    return dict(compromisos=evaluacion["planes"][elegido], puntuaciones=puntuaciones,
                reserva=float(puntuaciones.loc[elegido, "reserva"]), planes=evaluacion["planes"],
                candidato_id=int(puntuaciones.loc[elegido, "candidato_id"]))


def planificar_mpc(demanda, calendario, cuantiles, escenarios, configuracion, *, estado_inicial=None):
    """Propone y elige sin irradiancia realizada; empates por reserva y cuantil."""
    evaluacion = _evaluar_candidatos(demanda, calendario, cuantiles, escenarios, configuracion,
                                    estado_inicial=estado_inicial)
    return _seleccionar_plan(evaluacion, configuracion)


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


def _ejecutar_bloque(caso, reloj, origen, fin, prediccion, estado, configuracion, *, evaluacion=None):
    indices = prediccion["corregidos"].index
    ventana = reloj.loc[reloj.inicio_utc.isin(indices)]
    demanda = caso.demanda.loc[caso.demanda["t"].isin(ventana["t"])]
    plan = (_seleccionar_plan(evaluacion, configuracion) if evaluacion is not None else
            planificar_mpc(demanda, ventana, prediccion["corregidos"], prediccion["escenarios"],
                           configuracion, estado_inicial=estado))
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
    candidatos = plan["puntuaciones"].drop_duplicates("candidato_id").copy()
    candidatos["elegido"] = candidatos.candidato_id.eq(plan["candidato_id"])
    resultado["candidatos"] = candidatos.assign(origen_utc=origen, horizonte=len(indices))
    resultado["planes_elegidos"] = plan["compromisos"].assign(
        origen_utc=origen, candidato_id=plan["candidato_id"])
    fila = candidatos.loc[candidatos.candidato_id.eq(plan["candidato_id"])].iloc[0].to_dict()
    fila.update(origen_utc=origen, horas_ejecutadas=len(ejecutadas))
    return resultado, fila


def _unir_resultados(resultados, decisiones, pronosticos):
    tablas = {nombre: pd.concat([r[nombre] for r in resultados], ignore_index=True)
              for nombre in ("cargas", "horas", "baterias", "candidatos", "planes_elegidos")}
    resumen = {campo: math.fsum(r["resumen"][campo] for r in resultados)
               for campo in resultados[0]["resumen"] if campo != "S_pond"}
    cargas = tablas["cargas"]
    denominador = math.fsum(cargas["prioridad_supuesta"] * cargas["demanda_wh"])
    resumen["S_pond"] = resumen["J"] / denominador if denominador else None
    return dict(**tablas, estado_final=resultados[-1]["estado_final"].copy(), resumen=resumen,
                decisiones=pd.DataFrame(decisiones), pronosticos=pronosticos)


def ejecutar_mpc(caso, pronosticar, *, inicio, fin, estado_inicial=None):
    """Recorre [inicio, fin); callback(historia_admisible, origen_utc, horizonte).

    Devuelve las tablas del simulador, decisiones y pronósticos crudos/corregidos con
    rangos y bloques. Solo las observaciones cerradas llegan al callback.
    """
    origen, fin, reloj = _periodo(caso, inicio, fin)
    config = caso.configuracion
    parametros_mpc(config)
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


def _verificar_fisica(resultado, configuracion):
    """Contrasta los resultados realizados con IDEA §4, sin ampliar presupuestos."""
    cargas, horas, baterias = (resultado[n] for n in ("cargas", "horas", "baterias"))
    potencia_max_w = min(configuracion["potencia_bus_max_w"], configuracion["pv"]["inversor_max_w"])
    potencia = (cargas["potencia_w_supuesta"] * cargas["z"]).groupby(cargas["t"]).sum()
    if (not cargas[["x", "z", "solicitada"]].isin([0, 1]).all().all()
            or (cargas.z > cargas.x).any() or (cargas.x > cargas.solicitada).any()):
        raise ValueError("verificación: decisiones de servicio fuera de solicitudes o compromisos")
    if not (cargas.atendida_wh == cargas.demanda_wh * cargas.z).all():
        raise ValueError("verificación: energía atendida distinta de demanda por servicio")
    if (potencia > potencia_max_w).any():
        raise ValueError("verificación: potencia nominal atendida superior al límite del sistema")
    if (horas.vertido_wh < 0).any():
        raise ValueError("verificación: vertido negativo")

    error_bateria = 0.
    for bateria in configuracion["baterias"]:
        flujos = baterias.loc[baterias.bateria_id.eq(bateria["id"])].sort_values("t")
        inicial, final = flujos.inicial_wh, flujos.final_wh
        carga, descarga = flujos.carga_wh, flujos.descarga_wh
        eta_c, eta_d = bateria["eficiencia_carga"], bateria["eficiencia_descarga"]
        nombre = f"verificación: batería {bateria['id']}"
        if (not flujos[["inicial_wh", "final_wh"]].ge(bateria["minimo_wh"]).all().all()
                or not flujos[["inicial_wh", "final_wh"]].le(bateria["maximo_wh"]).all().all()):
            raise ValueError(f"{nombre}: estado fuera de límites")
        if not flujos[["carga_wh", "descarga_wh"]].ge(0).all().all():
            raise ValueError(f"{nombre}: flujo negativo")
        if (carga * descarga != 0).any():
            raise ValueError(f"{nombre}: carga y descarga simultáneas")
        if ((carga > bateria["potencia_carga_max_w"]).any()
                or (descarga > bateria["potencia_descarga_max_w"]).any()):
            raise ValueError(f"{nombre}: flujo superior al límite de potencia")
        if ((carga > (bateria["maximo_wh"] - inicial) / eta_c).any()
                or (descarga > eta_d * (inicial - bateria["minimo_wh"])).any()):
            raise ValueError(f"{nombre}: flujo superior a la energía disponible o al espacio de carga")
        if inicial.iloc[1:].tolist() != final.iloc[:-1].tolist():
            raise ValueError(f"{nombre}: discontinuidad entre horas")
        if resultado["estado_final"][bateria["id"]] != final.iloc[-1]:
            raise ValueError(f"{nombre}: estado final distinto del último estado registrado")

        error = float((final - inicial - eta_c * carga + descarga / eta_d).abs().max())
        if not np.isfinite(error) or error > 1e-6:
            raise ValueError(f"{nombre}: error de balance superior a 1e-6 Wh")
        error_bateria = max(error_bateria, error)

    neto = baterias.groupby("t")[["carga_wh", "descarga_wh"]].sum()
    por_hora = horas.set_index("t")
    balance_bus = (por_hora.fv_wh - por_hora.atendida_wh - por_hora.vertido_wh
                   + neto.descarga_wh - neto.carga_wh)
    error_bus = float(balance_bus.abs().max())
    if not np.isfinite(error_bus) or error_bus > 1e-6:
        raise ValueError("verificación: error de balance del bus superior a 1e-6 Wh")
    # IDEA §4.4: cargar solo con excedente FV y descargar solo para cubrir déficit.
    # Excluye descargas hacia el vertido y traspasos entre baterías en la misma hora.
    excedente = (por_hora.fv_wh - por_hora.atendida_wh).clip(lower=0)
    deficit = (por_hora.atendida_wh - por_hora.fv_wh).clip(lower=0)
    if (neto.carga_wh > excedente + 1e-6).any() or (neto.descarga_wh > deficit + 1e-6).any():
        raise ValueError("verificación: carga sin excedente FV o descarga sin déficit")
    return dict(max_error_bus_wh=error_bus, max_error_bateria_wh=error_bateria, violaciones_fisicas=0)


def proveedores(caso, pipeline):
    """Callbacks A/B (historia_admisible, origen_utc, horizonte) -> cuantiles crudos.

    Comparten contexto, cuantiles y zona horaria de la configuración; solo cambia
    el modelo de pronóstico. Calibración y evaluación usan exactamente estos dos.
    """
    config = caso.configuracion
    opciones = config["pronosticos"]
    return {
        "beta": lambda historia, origen, horizonte: pronosticar_beta(
            historia, origen, contexto=opciones["contexto"], horizonte=horizonte,
            cuantiles=opciones["cuantiles"], zona_horaria=config["zona_horaria"]),
        "chronos": lambda historia, origen, horizonte: pronosticar_chronos(
            pipeline, historia, origen, contexto=opciones["contexto"], horizonte=horizonte,
            cuantiles=opciones["cuantiles"]),
    }


def calibrar_mpc(caso, pipeline=None):
    """Ensayo pareado de desarrollo: selecciona un solo peso por J realizado medio.

    Reutiliza los pronósticos de cada origen entre pesos. Cada combinación de
    proveedor y peso conserva su estado independiente, sin consultar evaluación.
    """
    config = caso.configuracion
    if parametros_mpc(config)[0] != "original":
        raise ValueError("calibración: el peso terminal solo se calibra en modo original; para CVaR use --modo cvar")
    inicio, fin, reloj = _periodo(caso, CALIBRACION_INICIO, CALIBRACION_FIN)
    desarrollo = config["periodos"]["desarrollo"]
    if inicio < pd.Timestamp(desarrollo["inicio"]) or fin > pd.Timestamp(desarrollo["fin"]):
        raise ValueError("calibración: el ensayo debe permanecer dentro de desarrollo")
    if pipeline is None:
        opciones = config["pronosticos"]
        pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])
    pronosticadores = proveedores(caso, pipeline)
    claves = [(nombre, peso) for nombre in pronosticadores for peso in PESOS_TERMINALES]
    estados = {clave: _estado_inicial(config["baterias"], None) for clave in claves}
    recorridos, decisiones = {clave: [] for clave in claves}, {clave: [] for clave in claves}
    pronosticos = {nombre: [] for nombre in pronosticadores}
    for origen in pd.date_range(inicio, fin, freq=f"{config['replanificacion']}h", inclusive="left"):
        horizonte = min(config["horizonte"], int((fin - origen) / pd.Timedelta(hours=1)))
        for nombre, pronosticar in pronosticadores.items():
            prediccion = _predecir(caso, pronosticar, origen, horizonte)
            pronosticos[nombre].append(dict(origen_utc=origen, **prediccion))
            for peso in PESOS_TERMINALES:
                clave = (nombre, peso)
                opciones_control = dict(config, lambda_terminal=peso)
                resultado, decision = _ejecutar_bloque(
                    caso, reloj, origen, fin, prediccion, estados[clave], opciones_control)
                estados[clave] = resultado["estado_final"]
                recorridos[clave].append(resultado)
                decisiones[clave].append(decision)
    resultados, filas = {}, []
    for nombre, peso in claves:
        clave = (nombre, peso)
        resultado = _unir_resultados(recorridos[clave], decisiones[clave], pronosticos[nombre])
        diagnostico = _verificar_fisica(resultado, config)
        resultados[clave] = resultado
        filas.append(dict(proveedor=nombre, lambda_terminal=peso, **resultado["resumen"], **diagnostico))
    tabla = pd.DataFrame(filas)
    medias = tabla.groupby("lambda_terminal", sort=True)["J"].mean()
    elegido = float(medias.idxmax())
    return dict(lambda_terminal=elegido, tabla=tabla, resultados=resultados, pronosticos=pronosticos,
                inicio_utc=inicio, fin_utc=fin)


def _diario_riesgo(resultado, reloj, zona):
    """Pérdidas realizadas en Wh·p y servicio crítico por día local."""
    cargas = resultado["cargas"].copy()
    fechas = reloj.set_index("t").inicio_utc.dt.tz_convert(zona).dt.strftime("%Y-%m-%d")
    cargas["fecha"] = cargas.t.map(fechas)
    cargas["J"] = cargas.prioridad_supuesta * cargas.atendida_wh
    cargas["perdida"] = cargas.prioridad_supuesta * cargas.no_atendida_wh
    cargas["solicitada_ponderada"] = cargas.prioridad_supuesta * cargas.demanda_wh
    criticas = cargas.prioridad_supuesta.ge(PRIORIDAD_CRITICA)
    cargas["solicitada_critica_wh"] = cargas.demanda_wh.where(criticas, 0.)
    cargas["atendida_critica_wh"] = cargas.atendida_wh.where(criticas, 0.)
    campos = ["J", "perdida", "solicitada_ponderada", "solicitada_critica_wh", "atendida_critica_wh"]
    return cargas.groupby("fecha", sort=True)[campos].sum().reset_index()


def _resumir_riesgo(diario):
    filas = []
    for (proveedor, modo, peso), grupo in diario.groupby(["proveedor", "modo_mpc", "lambda_riesgo"], sort=True):
        totales = {c: math.fsum(grupo[c]) for c in
                   ("J", "perdida", "solicitada_ponderada", "solicitada_critica_wh", "atendida_critica_wh")}
        filas.append(dict(proveedor=proveedor, modo_mpc=modo, lambda_riesgo=peso, **totales,
                          S_pond=totales["J"] / totales["solicitada_ponderada"] if totales["solicitada_ponderada"] else None,
                          servicio_critico=totales["atendida_critica_wh"] / totales["solicitada_critica_wh"]
                          if totales["solicitada_critica_wh"] else None,
                          CVaR_diario=calcular_cvar(grupo.perdida, ALPHA_RIESGO), dias=len(grupo)))
    return pd.DataFrame(filas)


def _seleccionar_riesgo(tabla):
    cvar = tabla.loc[tabla.modo_mpc.eq("cvar")]
    referencia = cvar.loc[cvar.lambda_riesgo.eq(0)].set_index("proveedor").J
    filas = []
    for peso, grupo in cvar.groupby("lambda_riesgo", sort=True):
        if set(grupo.proveedor) != {"beta", "chronos"} or len(grupo) != 2:
            raise ValueError("calibración: se requieren beta y Chronos por peso")
        elegible = all(f.J >= .99 * referencia[f.proveedor] for f in grupo.itertuples())
        filas.append(dict(lambda_riesgo=peso, elegible=elegible,
                          J_medio=math.fsum(grupo.J) / 2, CVaR_diario_medio=math.fsum(grupo.CVaR_diario) / 2))
    seleccion = pd.DataFrame(filas)
    admisibles = seleccion.loc[seleccion.elegible].sort_values(
        ["CVaR_diario_medio", "J_medio", "lambda_riesgo"], ascending=[True, False, True])
    if admisibles.empty:
        raise ValueError("calibración: falta una referencia de peso cero admisible")
    return float(admisibles.iloc[0].lambda_riesgo), seleccion


def _validar_riesgo(tabla, elegido):
    cvar = tabla.loc[tabla.modo_mpc.eq("cvar")]
    referencia = cvar.loc[cvar.lambda_riesgo.eq(0)].set_index("proveedor")
    seleccionado = cvar.loc[cvar.lambda_riesgo.eq(elegido)].set_index("proveedor")
    servicio = all(seleccionado.loc[n, "J"] >= .99 * referencia.loc[n, "J"] for n in ("beta", "chronos"))
    cola = math.fsum(seleccionado.CVaR_diario) <= math.fsum(referencia.CVaR_diario)
    return dict(servicio_admisible=bool(servicio), cola_no_empeora=bool(cola), recomendado=bool(servicio and cola))


def calibrar_riesgo_mpc(caso, pipeline=None):
    """Calibración del riesgo: selecciona con seis semanas y valida dos, sin tocar entradas ni marzo."""
    config = caso.configuracion
    inicio, fin, reloj = _periodo(caso, RIESGO_INICIO, RIESGO_FIN)
    corte = pd.Timestamp(RIESGO_CORTE).tz_convert("UTC")
    desarrollo = config["periodos"]["desarrollo"]
    paso = pd.Timedelta(hours=config["replanificacion"])
    if (not inicio < corte < fin or (corte - inicio) % paso or (fin - inicio) % paso
            or inicio < pd.Timestamp(desarrollo["inicio"]) or fin > pd.Timestamp(desarrollo["fin"])):
        raise ValueError("calibración: ajuste y validación deben ser bloques de desarrollo alineados con R")
    if pipeline is None:
        opciones = config["pronosticos"]
        pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])
    pronosticadores = proveedores(caso, pipeline)
    if set(pronosticadores) != {"beta", "chronos"}:
        raise ValueError("calibración: se requieren los dos proveedores beta y Chronos")
    claves = [(n, modo, peso) for n in pronosticadores
              for modo, pesos in (("cvar", PESOS_RIESGO), ("original", (0.,))) for peso in pesos]
    estados = {clave: _estado_inicial(config["baterias"], None) for clave in claves}
    fisica = {clave: dict(max_error_bus_wh=0., max_error_bateria_wh=0., violaciones_fisicas=0) for clave in claves}
    pronosticos = {n: [] for n in pronosticadores}
    diarios, decisiones, candidatos, muestras, fronteras = [], [], [], [], []
    seleccionado, seleccion, ajuste = None, None, None
    evaluaciones, reutilizadas = 0, 0
    for origen in pd.date_range(inicio, fin, freq=paso, inclusive="left"):
        fase = "ajuste" if origen < corte else "validacion"
        if origen == corte:
            acumulado = pd.concat(diarios, ignore_index=True).groupby(
                ["fase", "proveedor", "modo_mpc", "lambda_riesgo", "fecha"], as_index=False).sum()
            ajuste = _resumir_riesgo(acumulado.loc[acumulado.fase.eq("ajuste")])
            seleccionado, seleccion = _seleccionar_riesgo(ajuste)
            fronteras = [dict(proveedor=n, modo_mpc=m, lambda_riesgo=p, estado=estados[(n, m, p)].copy())
                         for n, m, p in claves]
            claves = [c for c in claves if c[1] == "original" or c[2] in {0., seleccionado}]
        horizonte = min(config["horizonte"], int((fin - origen) / pd.Timedelta(hours=1)))
        predicciones = {}
        for nombre, pronosticar in pronosticadores.items():
            historia = contexto_admisible(caso.irradiancia_historica, origen, config["pronosticos"]["contexto"])
            if (historia.available_at.gt(origen) | historia.fin_utc.gt(origen)).any():
                raise ValueError("filtración: contexto futuro en calibración")
            prediccion = _predecir(caso, pronosticar, origen, horizonte)
            if (prediccion["bloques"] + pd.Timedelta(hours=24) > origen).any():
                raise ValueError("filtración: bloque futuro en calibración")
            pronosticos[nombre].append(dict(
                origen_utc=origen, contexto_fin_utc=historia.fin_utc.iloc[-1],
                contexto_sha256=hashlib.sha256(historia.ghi_w_m2.to_numpy(dtype=float).tobytes()).hexdigest(),
                **prediccion))
            predicciones[nombre] = prediccion
            cache = {}
            for clave in [c for c in claves if c[0] == nombre]:
                _, modo, peso = clave
                control = dict(config, modo_mpc=modo, alpha_cvar=ALPHA_RIESGO, lambda_riesgo=peso)
                estado = estados[clave]
                # La caché vive solo en este proveedor/origen y exige estados idénticos.
                huella = (modo, tuple(sorted(estado.items())))
                if huella not in cache:
                    ventana = reloj.loc[reloj.inicio_utc.isin(prediccion["corregidos"].index)]
                    demanda = caso.demanda.loc[caso.demanda.t.isin(ventana.t)]
                    cache[huella] = _evaluar_candidatos(demanda, ventana, prediccion["corregidos"],
                                                       prediccion["escenarios"], control, estado_inicial=estado)
                    evaluaciones += 1
                else:
                    reutilizadas += 1
                resultado, decision = _ejecutar_bloque(caso, reloj, origen, fin, prediccion, estado, control,
                                                      evaluacion=cache[huella])
                registrados = resultado["baterias"].groupby("bateria_id").inicial_wh.first().to_dict()
                if registrados != estado:
                    raise ValueError("verificación: discontinuidad entre bloques")
                diagnostico = _verificar_fisica(resultado, control)
                fisica[clave] = {c: max(fisica[clave][c], diagnostico[c]) for c in diagnostico}
                estados[clave] = resultado["estado_final"].copy()
                etiquetas = dict(fase=fase, proveedor=nombre, modo_mpc=modo, lambda_riesgo=peso)
                diarios.append(_diario_riesgo(resultado, reloj, config["zona_horaria"]).assign(**etiquetas))
                decisiones.append(dict(**decision, **{c: v for c, v in etiquetas.items() if c not in decision}))
                candidatos.append(resultado["candidatos"].assign(**etiquetas))
                if origen < (inicio if fase == "ajuste" else corte) + pd.Timedelta(hours=48):
                    muestras.append(dict(**etiquetas, origen_utc=origen, estado_inicial=estado.copy(),
                                         resultado=resultado, decision=decision))
        a, b = predicciones["beta"], predicciones["chronos"]
        if (a["semilla"] != b["semilla"] or not a["bloques"].equals(b["bloques"])
                or not np.array_equal(a["rangos"], b["rangos"])):
            raise ValueError("pareo: escenarios no comparten bloques y rangos")
    diario = pd.concat(diarios, ignore_index=True).groupby(
        ["fase", "proveedor", "modo_mpc", "lambda_riesgo", "fecha"], as_index=False).sum()
    validacion = _resumir_riesgo(diario.loc[diario.fase.eq("validacion")])
    veredicto = _validar_riesgo(validacion, seleccionado)
    muestras = [m for m in muestras if m["modo_mpc"] == "original" or m["lambda_riesgo"] in {0., seleccionado}]
    return dict(lambda_riesgo=seleccionado, alpha_cvar=ALPHA_RIESGO, inicio_utc=inicio, corte_utc=corte, fin_utc=fin,
                ajuste=ajuste, validacion=validacion, diario=diario, seleccion=seleccion,
                decisiones=pd.DataFrame(decisiones), candidatos=pd.concat(candidatos, ignore_index=True),
                pronosticos=pronosticos, estados_frontera=fronteras, muestras=muestras,
                fisica=[dict(proveedor=n, modo_mpc=m, lambda_riesgo=p, **d) for (n, m, p), d in fisica.items()],
                verificaciones=dict(**veredicto, filtraciones=0, escenarios_pareados=True,
                                    evaluaciones=evaluaciones, reutilizadas=reutilizadas), **veredicto)


def verificar_repeticion_riesgo(caso, pipeline, ensayo):
    """Repite orígenes con historia real y compara los bloques iniciales de cada fase."""
    pronosticadores = proveedores(caso, pipeline)
    predicciones, evaluaciones, max_error = {}, {}, 0.
    _, _, reloj = _periodo(caso, ensayo["inicio_utc"], ensayo["fin_utc"])
    por_origen = {(n, p["origen_utc"]): p for n, lista in ensayo["pronosticos"].items() for p in lista}
    for muestra in ensayo["muestras"]:
        n, origen = muestra["proveedor"], muestra["origen_utc"]
        clave = (n, origen)
        original = por_origen[clave]
        if clave not in predicciones:
            predicciones[clave] = _predecir(caso, pronosticadores[n], origen, len(original["crudos"]))
            if not np.allclose(original["crudos"], predicciones[clave]["crudos"], rtol=1e-5, atol=1e-6):
                raise ValueError("reproducibilidad: cambiaron cuantiles crudos")
        control = dict(caso.configuracion, modo_mpc=muestra["modo_mpc"], alpha_cvar=ALPHA_RIESGO,
                       lambda_riesgo=muestra["lambda_riesgo"])
        huella = (n, origen, muestra["modo_mpc"], tuple(sorted(muestra["estado_inicial"].items())))
        if huella not in evaluaciones:
            prediccion = predicciones[clave]
            ventana = reloj.loc[reloj.inicio_utc.isin(prediccion["corregidos"].index)]
            demanda = caso.demanda.loc[caso.demanda.t.isin(ventana.t)]
            evaluaciones[huella] = _evaluar_candidatos(demanda, ventana, prediccion["corregidos"],
                                                       prediccion["escenarios"], control,
                                                       estado_inicial=muestra["estado_inicial"])
        resultado, decision = _ejecutar_bloque(caso, reloj, origen, ensayo["fin_utc"], predicciones[clave],
                                              muestra["estado_inicial"], control, evaluacion=evaluaciones[huella])
        esperado = muestra["resultado"]
        for tabla in ("cargas", "horas", "baterias", "candidatos", "planes_elegidos"):
            try:
                pd.testing.assert_frame_equal(resultado[tabla], esperado[tabla], check_exact=False, rtol=0, atol=1e-6)
            except AssertionError as error:
                raise ValueError(f"reproducibilidad: cambió {tabla}") from error
        try:
            pd.testing.assert_series_equal(pd.Series(decision), pd.Series(muestra["decision"]),
                                           check_exact=False, rtol=0, atol=1e-6)
        except AssertionError as error:
            raise ValueError("reproducibilidad: cambió la decisión") from error
        if (set(resultado["estado_final"]) != set(esperado["estado_final"])
                or any(abs(resultado["estado_final"][b] - esperado["estado_final"][b]) > 1e-6
                       for b in esperado["estado_final"])):
            raise ValueError("reproducibilidad: cambió el estado final")
        max_error = max(max_error, float(np.abs(original["crudos"].to_numpy() - predicciones[clave]["crudos"].to_numpy()).max()))
    return dict(coincide=True, bloques=len(ensayo["muestras"]), origenes_proveedor=len(predicciones),
                max_diferencia_cuantiles_w_m2=max_error)


def _publicar_riesgo(caso, salida, entradas_antes, codigo_antes):
    """La configuración de entrada se conserva; resumen.json acredita el éxito."""
    import importlib.metadata
    import json
    import os
    from pathlib import Path
    from time import perf_counter

    from .cli import codigo_sha256, entradas_sha256, entorno, sha256_de

    inicio = perf_counter()
    opciones = caso.configuracion["pronosticos"]
    pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])
    ensayo = calibrar_riesgo_mpc(caso, pipeline)
    repeticion = verificar_repeticion_riesgo(caso, pipeline, ensayo)
    config = dict(caso.configuracion, modo_mpc="cvar", alpha_cvar=ALPHA_RIESGO,
                  lambda_riesgo=ensayo["lambda_riesgo"])
    config["datos"] = {}
    for nombre in caso.configuracion["datos"]:
        try:
            config["datos"][nombre] = os.path.relpath(caso.rutas[nombre], salida)
        except ValueError:
            config["datos"][nombre] = str(caso.rutas[nombre])  # Windows: datos y salida en unidades distintas.
    for nombre in ("ajuste", "validacion", "diario", "seleccion", "decisiones", "candidatos"):
        ensayo[nombre].to_csv(salida / f"{nombre}.csv", index=False)
    registros = []
    for nombre, lista in ensayo["pronosticos"].items():
        for p in lista:
            registros.append(dict(
                proveedor=nombre, origen_utc=p["origen_utc"].isoformat(),
                contexto_fin_utc=p["contexto_fin_utc"].isoformat(), contexto_sha256=p["contexto_sha256"],
                semilla=p["semilla"], bloques=[f.isoformat() for f in p["bloques"]],
                correcciones=p["correcciones"], inicio_utc=[f.isoformat() for f in p["crudos"].index],
                cuantiles=list(p["crudos"].columns), crudos=p["crudos"].to_numpy().tolist(),
                corregidos=p["corregidos"].to_numpy().tolist()))
    (salida / "pronosticos.json").write_text(
        json.dumps(registros, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    (salida / "configuracion_cvar.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    versiones = entorno()
    for paquete in ("numpy", "scipy", "chronos-forecasting", "torch", "astral"):
        versiones[paquete] = importlib.metadata.version(paquete)
    tablas = {n: ensayo[n].astype(object).where(ensayo[n].notna(), None).to_dict("records")
              for n in ("ajuste", "validacion", "seleccion")}
    informe = dict(
        alcance="calibración del riesgo y validación temporal en desarrollo; marzo no interviene",
        inicio_utc=ensayo["inicio_utc"].isoformat(), corte_utc=ensayo["corte_utc"].isoformat(),
        fin_utc=ensayo["fin_utc"].isoformat(), alpha_cvar=ALPHA_RIESGO,
        lambda_riesgo=ensayo["lambda_riesgo"], pesos=list(PESOS_RIESGO), duracion_s=perf_counter() - inicio,
        criterio="J >= 0.99 J_CVaR0 en cada proveedor; mínimo CVaR diario medio A/B; "
                 "empate por mayor J medio y menor peso",
        criterio_validacion="99% de J en cada rama y CVaR diario medio no mayor que CVaR0; sin reajuste",
        recomendado=ensayo["recomendado"], resultados=tablas,
        verificaciones=dict(ensayo["verificaciones"], fisica=ensayo["fisica"], reproducibilidad=repeticion),
        estados_frontera=ensayo["estados_frontera"], configuracion=config,
        configuracion_entrada=caso.configuracion,
        semillas={"demanda": caso.configuracion["semilla"], "escenarios": caso.configuracion["semilla_escenarios"]},
        entradas_sha256=entradas_antes, codigo_sha256=codigo_antes, entorno=versiones,
        uv_lock_sha256=sha256_de(Path(__file__).resolve().parents[1] / "uv.lock"),
        archivos_sha256={p.name: sha256_de(p) for p in sorted(salida.iterdir()) if p.is_file()},
        supuestos=["MPC y CVaR son adaptaciones; no cambian la física ni los pronosticadores",
                   "pérdida diaria realizada en Wh·p; separada del CVaR interno de escenarios",
                   "14 días de validación representan 1.4 días efectivos en la cola del 10%",
                   "peso cero o empate son resultados válidos y no demuestran mejora"],
    )
    if codigo_sha256() != codigo_antes or entradas_sha256(caso.rutas) != entradas_antes:
        raise ValueError("calibración: cambiaron entradas o código durante el ensayo")
    temporal = salida / "resumen.json.tmp"
    try:
        temporal.write_text(json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporal.replace(salida / "resumen.json")
    finally:
        temporal.unlink(missing_ok=True)
    return ensayo["lambda_riesgo"]


def main():
    """Calibra en desarrollo, verifica y guarda el peso común sin tocar evaluación."""
    import argparse
    import importlib.metadata
    import json
    from pathlib import Path
    from tempfile import NamedTemporaryFile
    from time import perf_counter

    from .cli import codigo_sha256, entradas_sha256, entorno, sha256_de
    from .datos import cargar_caso

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--config", type=Path, default=Path("scripts/configuracion.json"))
    parser.add_argument("--salida", type=Path)
    parser.add_argument("--modo", choices=("original", "cvar"), default="original")
    args = parser.parse_args()
    if args.salida is None:
        args.salida = Path("results/calibracion_riesgo" if args.modo == "cvar" else "results/calibracion_terminal")
    try:
        caso = cargar_caso(args.config)
        if args.modo == "original" and parametros_mpc(caso.configuracion)[0] != "original":
            raise ValueError("calibración: el peso terminal solo se calibra en modo original; para CVaR use --modo cvar")
        salida = args.salida.resolve()
        if salida == args.config.resolve() or salida in [p.resolve() for p in caso.rutas.values()]:
            raise ValueError("salida: no puede reemplazar una entrada")
        salida.mkdir(parents=True, exist_ok=False)
        entradas_antes = entradas_sha256(caso.rutas)
        codigo_antes = codigo_sha256()
        if args.modo == "cvar":
            elegido = _publicar_riesgo(caso, salida, entradas_antes, codigo_antes)
            print(f"{salida}: lambda_riesgo={elegido:g}")
            return
        inicio = perf_counter()
        ensayo = calibrar_mpc(caso)
        duracion = perf_counter() - inicio
        elegido = ensayo["lambda_terminal"]
        config = dict(caso.configuracion, lambda_terminal=elegido)
        versiones = entorno()
        for paquete in ("numpy", "scipy", "chronos-forecasting", "torch", "astral"):
            versiones[paquete] = importlib.metadata.version(paquete)
        registros = []
        for nombre, pronosticos in ensayo["pronosticos"].items():
            for p in pronosticos:
                registros.append(dict(
                    proveedor=nombre, origen_utc=p["origen_utc"].isoformat(),
                    semilla=p["semilla"], bloques=[f.isoformat() for f in p["bloques"]],
                    correcciones=p["correcciones"], inicio_utc=[f.isoformat() for f in p["crudos"].index],
                    crudos=p["crudos"].to_numpy().tolist(), corregidos=p["corregidos"].to_numpy().tolist()))
        informe = dict(
            alcance="calibración pareada del peso terminal en desarrollo, no evaluación final",
            inicio_utc=ensayo["inicio_utc"].isoformat(), fin_utc=ensayo["fin_utc"].isoformat(),
            lambda_terminal=elegido, pesos=list(PESOS_TERMINALES), duracion_s=duracion,
            criterio="máximo promedio de J realizado de beta y Chronos; empate por menor peso",
            configuracion=config, resultados=ensayo["tabla"].to_dict("records"), pronosticos=registros,
            configuracion_entrada=caso.configuracion,
            entradas_sha256=entradas_antes, codigo_sha256=codigo_antes, entorno=versiones,
            uv_lock_sha256=sha256_de(Path(__file__).resolve().parents[1] / "uv.lock"),
            supuestos=["beta2: loc=0, escala=1.05*máximo positivo por hora de las últimas 336 h",
                       "rangos por columna con empates promedio; colas constantes en q0.1 y q0.9",
                       "reservas solo al generar candidatos; física del simulador en evaluación y ejecución",
                       "MPC heurístico adaptado, sin garantía de optimalidad o calibración conjunta"],
        )
        texto = json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if codigo_sha256() != codigo_antes or entradas_sha256(caso.rutas) != entradas_antes:
            raise ValueError("calibración: cambiaron entradas o código durante el ensayo")
        temporal_config = None
        temporal_informe = salida / "resumen.json.tmp"
        ruta_informe = salida / "resumen.json"
        try:
            with NamedTemporaryFile(dir=args.config.parent, prefix=args.config.name + ".calibracion.",
                                    suffix=".tmp", delete=False) as archivo:
                temporal_config = Path(archivo.name)
            temporal_config.write_text(
                json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            temporal_informe.write_text(texto, encoding="utf-8")
            temporal_informe.replace(ruta_informe)
            try:
                # La configuración solo cambia cuando el informe completo ya está publicado.
                temporal_config.replace(args.config)
            except OSError:
                ruta_informe.unlink()
                raise
        finally:
            if temporal_config is not None:
                temporal_config.unlink(missing_ok=True)
            temporal_informe.unlink(missing_ok=True)
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\nLa salida {args.salida} puede estar incompleta; no es válida.\n")
    print(f"{salida}: lambda_terminal={elegido:g}")


if __name__ == "__main__":
    main()
