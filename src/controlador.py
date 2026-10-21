"""T04: escenarios empíricos y MPC heurístico común (adaptación de IDEA §§5–6).

La reserva genera compromisos; su evaluación y ejecución conservan la física
de T03. El artículo original no usa este MPC ni su puntuación esperada.
"""

import math

import numpy as np
import pandas as pd

from .configuracion import numero
from .pronosticos import (
    cargar_chronos, contexto_admisible, corregir_cuantiles, pronosticar_beta,
    pronosticar_chronos, pronosticar_t0, pronosticar_timesfm, validar_cuantiles,
)
from .simulador import _estado_inicial, simular

RESERVAS = (0., 0.25, 0.5, 0.75)
PESOS_TERMINALES = (0., 0.25, 0.5, 0.75)
CALIBRACION_INICIO = "2025-01-06T00:00:00-05:00"
CALIBRACION_FIN = "2025-01-13T00:00:00-05:00"


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


def proveedores(caso, pipeline, modelo_timesfm=None, modelo_t0=None):
    """Callbacks (historia_admisible, origen_utc, horizonte) -> cuantiles crudos.

    A (beta) y B (Chronos-2) siempre; C (TimesFM 3.0) y D (t0-gamma) solo si se recibe
    su modelo.
    Comparten contexto, cuantiles y zona horaria de la configuración; solo cambia
    el modelo de pronóstico. La calibración usa exactamente A y B.
    """
    config = caso.configuracion
    opciones = config["pronosticos"]
    callbacks = {
        "beta": lambda historia, origen, horizonte: pronosticar_beta(
            historia, origen, contexto=opciones["contexto"], horizonte=horizonte,
            cuantiles=opciones["cuantiles"], zona_horaria=config["zona_horaria"]),
        "chronos": lambda historia, origen, horizonte: pronosticar_chronos(
            pipeline, historia, origen, contexto=opciones["contexto"], horizonte=horizonte,
            cuantiles=opciones["cuantiles"]),
    }
    if modelo_timesfm is not None:
        callbacks["timesfm"] = lambda historia, origen, horizonte: pronosticar_timesfm(
            modelo_timesfm, historia, origen, contexto=opciones["contexto"], horizonte=horizonte,
            cuantiles=opciones["cuantiles"])
    if modelo_t0 is not None:
        callbacks["t0"] = lambda historia, origen, horizonte: pronosticar_t0(
            modelo_t0, historia, origen, contexto=opciones["contexto"], horizonte=horizonte,
            cuantiles=opciones["cuantiles"])
    return callbacks


def calibrar_mpc(caso, pipeline=None):
    """Ensayo pareado de desarrollo: selecciona un solo peso por J realizado medio.

    Reutiliza los pronósticos de cada origen entre pesos. Cada combinación de
    proveedor y peso conserva su estado independiente, sin consultar evaluación.
    """
    inicio, fin, reloj = _periodo(caso, CALIBRACION_INICIO, CALIBRACION_FIN)
    config = caso.configuracion
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
    parser.add_argument("--salida", type=Path, default=Path("results/t04_calibracion"))
    args = parser.parse_args()
    try:
        caso = cargar_caso(args.config)
        salida = args.salida.resolve()
        if salida == args.config.resolve() or salida in [p.resolve() for p in caso.rutas.values()]:
            raise ValueError("salida: no puede reemplazar una entrada")
        salida.mkdir(parents=True, exist_ok=False)
        entradas_antes = entradas_sha256(caso.rutas)
        codigo_antes = codigo_sha256()
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
            alcance="T04: calibración pareada de desarrollo, no evaluación final",
            inicio_utc=ensayo["inicio_utc"].isoformat(), fin_utc=ensayo["fin_utc"].isoformat(),
            lambda_terminal=elegido, pesos=list(PESOS_TERMINALES), duracion_s=duracion,
            criterio="máximo promedio de J realizado de beta y Chronos; empate por menor peso",
            configuracion=config, resultados=ensayo["tabla"].to_dict("records"), pronosticos=registros,
            configuracion_entrada=caso.configuracion,
            entradas_sha256=entradas_antes, codigo_sha256=codigo_antes, entorno=versiones,
            uv_lock_sha256=sha256_de(Path(__file__).resolve().parents[1] / "uv.lock"),
            supuestos=["beta2: loc=0, escala=1.05*máximo positivo por hora de las últimas 336 h",
                       "rangos por columna con empates promedio; colas constantes en q0.1 y q0.9",
                       "reservas solo al generar candidatos; física T03 en evaluación y ejecución",
                       "MPC heurístico adaptado, sin garantía de optimalidad o calibración conjunta"],
        )
        texto = json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if codigo_sha256() != codigo_antes or entradas_sha256(caso.rutas) != entradas_antes:
            raise ValueError("calibración: cambiaron entradas o código durante el ensayo")
        temporal_config = None
        temporal_informe = salida / "resumen.json.tmp"
        ruta_informe = salida / "resumen.json"
        try:
            with NamedTemporaryFile(dir=args.config.parent, prefix=args.config.name + ".t04.",
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
