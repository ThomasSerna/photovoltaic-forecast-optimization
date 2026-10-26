"""Diagnóstico con información perfecta: oráculo del MPC y cota superior de J por MILP.

Ambos conocen la GHI realizada del periodo, así que no son alternativas admisibles ni
entran en B − A (IDEA §7). El oráculo es el MPC de T04–T05 con un pronóstico que
devuelve la realización en todos los cuantiles: mide lo que este controlador hace con
información perfecta. El óptimo resuelve el periodo completo con la física de IDEA §4:

  max  J = Σ_{t,k} p_{k,t} D_{k,t} z_{k,t}                     z binaria, z ≤ r, x = z
  s.a. Σ_k D z + Σ_b c_b + v = E^FV_t + Σ_b d_b                  (bus, §4.3)
       Σ_k P_k z ≤ min(P_inv, P_bus)                              (potencia nominal, §4.2)
       C_{b,t+1} = C_{b,t} + η_c c_b − d_b/η_d,  C_min ≤ C ≤ C_max
       c_b ≤ min(P_c Δt, (C_max − C_{b,t})/η_c),  d_b ≤ min(P_d Δt, η_d (C_{b,t} − C_min))
       s_t ∈ {0, 1}: excedente (s=1) → d = 0; déficit (s=0) → c = 0 y v = 0
       C_{b,T} ≥ C_term,b                                        (condición terminal opcional)

El modo s_t impone la dirección del despacho de §4.4 y, con ella, c_b d_b = 0. Todo
recorrido realizado que cumpla esta física y termine con al menos C_term es factible
para el MILP, por lo que J* acota su J. El reparto entre baterías es libre: re-simular el
calendario óptimo con el despacho por identificador de T03 puede perder algún compromiso.
"""

import math
from time import perf_counter

import numpy as np
import pandas as pd

from .simulador import _energia_fotovoltaica, _estado_inicial, _preparar_entradas

TOLERANCIA_WH = 1e-6  # misma tolerancia absoluta de los balances (IDEA §4.4)


def ghi_realizada(caso):
    """GHI reservada del caso indexada por inicio_utc, mediante el calendario validado."""
    from .controlador import _calendario_utc

    reloj = _calendario_utc(caso.calendario)
    tabla = caso.irradiancia_simulacion[["t", "ghi_w_m2"]].merge(reloj[["t", "inicio_utc"]], on="t")
    return tabla.set_index("inicio_utc")["ghi_w_m2"].sort_index()


def pronostico_perfecto(realizada, cuantiles):
    """Callback del oráculo: ignora la historia y devuelve la GHI realizada en todos los cuantiles."""
    cuantiles = list(cuantiles)

    def pronosticar(historia, origen, horizonte):
        indice = pd.date_range(pd.Timestamp(origen).tz_convert("UTC"), periods=horizonte, freq="h",
                               name="inicio_utc")
        valores = realizada.reindex(indice)
        if valores.isna().any():
            raise ValueError(f"oráculo: falta GHI realizada en el horizonte de {indice[0].isoformat()}")
        return pd.DataFrame(np.repeat(valores.to_numpy(dtype=float)[:, None], len(cuantiles), axis=1),
                            index=indice, columns=cuantiles)
    return pronosticar


def optimo_informacion_perfecta(demanda, irradiancia, configuracion, *, estado_inicial=None,
                                terminal_minimo=None, limite_s=1800.):
    """MILP del periodo con GHI conocida; devuelve las tablas de `simular` y el estado del solver."""
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_array

    demanda, irradiancia = _preparar_entradas(demanda, irradiancia, None)
    baterias = sorted(configuracion["baterias"], key=lambda b: b["id"])
    estado = _estado_inicial(baterias, estado_inicial)
    terminal = _estado_inicial(baterias, terminal_minimo) if terminal_minimo is not None else None
    horas = irradiancia["t"].tolist()
    T, B = len(horas), len(baterias)
    ghi = irradiancia.set_index("t")["ghi_w_m2"]
    E = np.array([_energia_fotovoltaica(float(ghi[t]), configuracion["pv"]) for t in horas])
    potencia_max_w = min(configuracion["potencia_bus_max_w"], configuracion["pv"]["inversor_max_w"])
    solicitudes = demanda.loc[demanda["solicitada"].eq(1)]
    hora_de = solicitudes["t"].map({t: i for i, t in enumerate(horas)}).to_numpy()

    # Índices de variables: z | c_{b,t} | d_{b,t} | C_{b,t}, t = 0..T | v_t | s_t
    nz = len(solicitudes)
    iz = np.arange(nz)
    ic = nz + np.arange(B * T).reshape(B, T)
    id_ = nz + B * T + np.arange(B * T).reshape(B, T)
    iC = nz + 2 * B * T + np.arange(B * (T + 1)).reshape(B, T + 1)
    iv = nz + 2 * B * T + B * (T + 1) + np.arange(T)
    is_ = iv + T
    n = int(is_[-1] + 1) if T else nz

    filas, columnas, valores, inferior, superior = [], [], [], [], []

    def restriccion(coeficientes, minimo, maximo):
        for columna, valor in coeficientes:
            filas.append(len(inferior))
            columnas.append(int(columna))
            valores.append(float(valor))
        inferior.append(minimo)
        superior.append(maximo)

    D = solicitudes["demanda_wh"].to_numpy(dtype=float)
    P = solicitudes["potencia_w_supuesta"].to_numpy(dtype=float)
    tope_c = math.fsum(b["potencia_carga_max_w"] for b in baterias)
    tope_d = math.fsum(b["potencia_descarga_max_w"] for b in baterias)
    for i in range(T):
        js = np.flatnonzero(hora_de == i)
        restriccion([*zip(iz[js], D[js]), *((ic[b, i], 1.) for b in range(B)), (iv[i], 1.),
                     *((id_[b, i], -1.) for b in range(B))], E[i], E[i])
        restriccion(list(zip(iz[js], P[js])), -np.inf, potencia_max_w)
        restriccion([*((ic[b, i], 1.) for b in range(B)), (is_[i], -tope_c)], -np.inf, 0.)
        restriccion([*((id_[b, i], 1.) for b in range(B)), (is_[i], tope_d)], -np.inf, tope_d)
        restriccion([(iv[i], 1.), (is_[i], -E[i])], -np.inf, 0.)
        for b, bateria in enumerate(baterias):
            eta_c, eta_d = bateria["eficiencia_carga"], bateria["eficiencia_descarga"]
            restriccion([(iC[b, i + 1], 1.), (iC[b, i], -1.), (ic[b, i], -eta_c), (id_[b, i], 1 / eta_d)], 0., 0.)
            restriccion([(ic[b, i], eta_c), (iC[b, i], 1.)], -np.inf, bateria["maximo_wh"])
            restriccion([(id_[b, i], 1.), (iC[b, i], -eta_d)], -np.inf, -eta_d * bateria["minimo_wh"])

    cota_inferior, cota_superior = np.zeros(n), np.full(n, np.inf)
    cota_superior[iz] = 1.
    cota_superior[is_] = 1.
    for b, bateria in enumerate(baterias):
        cota_superior[ic[b]] = bateria["potencia_carga_max_w"]
        cota_superior[id_[b]] = bateria["potencia_descarga_max_w"]
        cota_inferior[iC[b]], cota_superior[iC[b]] = bateria["minimo_wh"], bateria["maximo_wh"]
        cota_inferior[iC[b, 0]] = cota_superior[iC[b, 0]] = estado[bateria["id"]]
        if terminal is not None:
            cota_inferior[iC[b, T]] = terminal[bateria["id"]]
    integralidad = np.zeros(n)
    integralidad[iz] = integralidad[is_] = 1
    objetivo = np.zeros(n)
    objetivo[iz] = -(solicitudes["prioridad_supuesta"].to_numpy(dtype=float) * D)

    reloj = perf_counter()
    A = coo_array((valores, (filas, columnas)), shape=(len(inferior), n)).tocsr()
    res = milp(objetivo, constraints=LinearConstraint(A, inferior, superior), integrality=integralidad,
               bounds=Bounds(cota_inferior, cota_superior),
               options=dict(time_limit=limite_s, mip_rel_gap=1e-7, disp=False))
    tiempo_s = perf_counter() - reloj
    if res.x is None:
        raise RuntimeError(f"óptimo: el MILP no devolvió solución ({res.status}: {res.message})")
    x = res.x
    z = (x[iz] > 0.5).astype(int)

    cargas = demanda.copy()
    cargas["z"] = 0
    cargas.loc[solicitudes.index, "z"] = z
    cargas["x"] = cargas["z"]
    cargas["comprometida_wh"] = cargas["demanda_wh"] * cargas["x"]
    cargas["atendida_wh"] = cargas["demanda_wh"] * cargas["z"]
    cargas["no_atendida_wh"] = cargas["demanda_wh"] - cargas["atendida_wh"]
    por_hora = cargas.assign(potencia_w=cargas["potencia_w_supuesta"] * cargas["z"]).groupby("t")
    tabla_horas = pd.DataFrame({
        "t": horas, "fv_wh": E, "solicitada_wh": por_hora["demanda_wh"].sum().reindex(horas).to_numpy(),
        "comprometida_wh": por_hora["comprometida_wh"].sum().reindex(horas).to_numpy(),
        "atendida_wh": por_hora["atendida_wh"].sum().reindex(horas).to_numpy(),
        "no_atendida_wh": por_hora["no_atendida_wh"].sum().reindex(horas).to_numpy(),
        "potencia_atendida_w": por_hora["potencia_w"].sum().reindex(horas).to_numpy(),
        "vertido_wh": x[iv]})
    flujos = pd.DataFrame([dict(t=t, bateria_id=bateria["id"], inicial_wh=x[iC[b, i]], carga_wh=x[ic[b, i]],
                                descarga_wh=x[id_[b, i]], final_wh=x[iC[b, i + 1]])
                           for i, t in enumerate(horas) for b, bateria in enumerate(baterias)])
    resumen = {campo: math.fsum(tabla_horas[campo]) for campo in
               ("fv_wh", "solicitada_wh", "comprometida_wh", "atendida_wh", "no_atendida_wh", "vertido_wh")}
    resumen.update({campo: math.fsum(flujos[campo]) for campo in ("carga_wh", "descarga_wh")})
    resumen["J"] = math.fsum(cargas["prioridad_supuesta"] * cargas["atendida_wh"])
    ponderada = math.fsum(cargas["prioridad_supuesta"] * cargas["demanda_wh"])
    resumen["S_pond"] = resumen["J"] / ponderada if ponderada else None
    solver = dict(estado=int(res.status), mensaje=res.message, tiempo_s=tiempo_s, variables=n,
                  restricciones=len(inferior), binarias=int(integralidad.sum()), objetivo=-float(res.fun),
                  cota_superior=-float(res.mip_dual_bound), brecha_relativa=float(res.mip_gap))
    return dict(cargas=cargas, horas=tabla_horas, baterias=flujos,
                estado_final={b["id"]: float(x[iC[i, T]]) for i, b in enumerate(baterias)},
                resumen=resumen, solver=solver, terminal_minimo=terminal)


def verificar_optimo(resultado, configuracion, tolerancia=TOLERANCIA_WH):
    """Contrasta la solución del MILP con IDEA §4 con tolerancia absoluta; ValueError si falla."""
    cargas, horas, flujos = (resultado[n] for n in ("cargas", "horas", "baterias"))
    if (not cargas[["x", "z"]].isin([0, 1]).all().all() or (cargas.z > cargas.solicitada).any()
            or not (cargas.atendida_wh == cargas.demanda_wh * cargas.z).all()):
        raise ValueError("óptimo: servicio fuera de las solicitudes o no completo")
    potencia_max_w = min(configuracion["potencia_bus_max_w"], configuracion["pv"]["inversor_max_w"])
    neto = flujos.groupby("t")[["carga_wh", "descarga_wh"]].sum()
    h = horas.set_index("t")
    excedente = (h.fv_wh - h.atendida_wh).clip(lower=0)
    deficit = (h.atendida_wh - h.fv_wh).clip(lower=0)
    errores = dict(
        potencia_w=float((h.potencia_atendida_w - potencia_max_w).max()),
        bus_wh=float((h.fv_wh + neto.descarga_wh - h.atendida_wh - neto.carga_wh - h.vertido_wh).abs().max()),
        vertido_negativo_wh=float(-h.vertido_wh.min()),
        carga_sin_excedente_wh=float((neto.carga_wh - excedente).max()),
        descarga_sin_deficit_wh=float((neto.descarga_wh - deficit).max()),
    )
    for bateria in configuracion["baterias"]:
        b = flujos.loc[flujos.bateria_id.eq(bateria["id"])].sort_values("t")
        eta_c, eta_d = bateria["eficiencia_carga"], bateria["eficiencia_descarga"]
        id_ = bateria["id"]
        errores[f"{id_}_dinamica_wh"] = float((b.final_wh - b.inicial_wh - eta_c * b.carga_wh
                                               + b.descarga_wh / eta_d).abs().max())
        errores[f"{id_}_limites_wh"] = float(max((bateria["minimo_wh"] - b[["inicial_wh", "final_wh"]]).max().max(),
                                                 (b[["inicial_wh", "final_wh"]] - bateria["maximo_wh"]).max().max()))
        errores[f"{id_}_flujos_wh"] = float(max(-b.carga_wh.min(), -b.descarga_wh.min(),
                                                (b.carga_wh - bateria["potencia_carga_max_w"]).max(),
                                                (b.descarga_wh - bateria["potencia_descarga_max_w"]).max(),
                                                (b.carga_wh - (bateria["maximo_wh"] - b.inicial_wh) / eta_c).max(),
                                                (b.descarga_wh - eta_d * (b.inicial_wh - bateria["minimo_wh"])).max()))
        errores[f"{id_}_simultaneas_wh"] = float(np.minimum(b.carga_wh, b.descarga_wh).max())
        errores[f"{id_}_continuidad_wh"] = (float(np.abs(b.inicial_wh.to_numpy()[1:] - b.final_wh.to_numpy()[:-1]).max())
                                            if len(b) > 1 else 0.)
        if resultado.get("terminal_minimo") is not None:
            errores[f"{id_}_terminal_wh"] = float(resultado["terminal_minimo"][id_] - b.final_wh.iloc[-1])
    violaciones = {campo: error for campo, error in errores.items() if error > tolerancia}
    if violaciones:
        raise ValueError(f"óptimo: restricciones de IDEA §4 incumplidas: {violaciones}")
    return dict(max_errores=errores, violaciones_fisicas=0, tolerancia_wh=tolerancia)


def _tabla_periodo(caso, inicio, fin):
    from .controlador import _periodo

    _, _, reloj = _periodo(caso, inicio, fin)
    demanda = caso.demanda.loc[caso.demanda["t"].isin(reloj["t"])]
    solar = caso.irradiancia_simulacion.loc[caso.irradiancia_simulacion["t"].isin(reloj["t"]), ["t", "ghi_w_m2"]]
    return demanda, solar


def main():
    """Oráculo y óptimo con información perfecta en el periodo reservado (diagnóstico, IDEA §7)."""
    import argparse
    import importlib.metadata
    import json
    from pathlib import Path

    from .cli import codigo_sha256, entradas_sha256, entorno
    from .configuracion import SELECCIONES
    from .controlador import _verificar_fisica, ejecutar_mpc, proveedores
    from .datos import cargar_caso
    from .experimento import _periodo_evaluacion, diario, ejecutar_reactiva, metricas_servicio
    from .pronosticos import cargar_chronos
    from .simulador import simular

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--config", type=Path, default=Path("scripts/configuracion.json"))
    parser.add_argument("--salida", type=Path, default=Path("results/cotas_evaluacion"))
    parser.add_argument("--seleccion", choices=SELECCIONES,
                        help="Regla de selección de las políticas (IDEA §4.4); por defecto, la de la configuración")
    args = parser.parse_args()
    try:
        caso = cargar_caso(args.config)
        if args.seleccion is not None:
            caso.configuracion["seleccion_cargas"] = args.seleccion
        config = caso.configuracion
        salida = args.salida.resolve()
        salida.mkdir(parents=True, exist_ok=False)
        entradas, codigo = entradas_sha256(caso.rutas), codigo_sha256()
        inicio, fin, _ = _periodo_evaluacion(caso, None, None)
        opciones = config["pronosticos"]
        pronosticadores = proveedores(caso, cargar_chronos(opciones["revision"], opciones["dispositivo"]))
        pronosticadores["oraculo"] = pronostico_perfecto(ghi_realizada(caso), opciones["cuantiles"])
        resultados, tiempos = {}, {}
        for nombre, pronosticar in pronosticadores.items():
            reloj = perf_counter()
            resultados[nombre] = ejecutar_mpc(caso, pronosticar, inicio=inicio, fin=fin)
            tiempos[nombre] = perf_counter() - reloj
        resultados["reactiva"] = ejecutar_reactiva(caso, inicio, fin)
        fisica = {nombre: _verificar_fisica(r, config) for nombre, r in resultados.items()}
        # El oráculo debe planificar con la realidad exacta: sin correcciones ni dispersión de escenarios.
        oraculo = dict(
            max_correccion_w_m2=max(float(np.abs(p["corregidos"].to_numpy() - p["crudos"].to_numpy()).max())
                                   for p in resultados["oraculo"]["pronosticos"]),
            max_dispersion_escenarios_w_m2=max(float(np.ptp(p["escenarios"], axis=0).max())
                                              for p in resultados["oraculo"]["pronosticos"]))
        demanda, solar = _tabla_periodo(caso, inicio, fin)
        # Mínimo por batería de la energía final de las políticas: cota válida para todas ellas.
        terminal = {b["id"]: min(r["estado_final"][b["id"]] for r in resultados.values()) for b in config["baterias"]}
        optimo = optimo_informacion_perfecta(demanda, solar, config, terminal_minimo=terminal)
        verificacion = verificar_optimo(optimo, config)
        resimulado = simular(demanda, solar, config, compromisos=optimo["cargas"][["t", "carga_id", "x"]])
        fisica["optimo_resimulado"] = _verificar_fisica(resimulado, config)
        todas = dict(resultados, optimo=optimo, optimo_resimulado=resimulado)
        metricas = {nombre: metricas_servicio(r, config) for nombre, r in todas.items()}
        cota = optimo["solver"]["cota_superior"]
        distancias = {nombre: dict(J=m["J"], optimo_menos_J=optimo["resumen"]["J"] - m["J"],
                                   cota_menos_J=cota - m["J"],
                                   relativa=(optimo["resumen"]["J"] - m["J"]) / m["J"])
                      for nombre, m in metricas.items() if nombre != "optimo"}
        tabla = pd.DataFrame([dict(politica=n, **m) for n, m in metricas.items()])
        tabla.to_csv(salida / "tabla_cotas.csv", index=False)
        diario(todas, caso.calendario).to_csv(salida / "diario_cotas.csv", index=False)
        optimo["cargas"].to_csv(salida / "cargas_optimas.csv", index=False)
        versiones = entorno()
        for paquete in ("numpy", "scipy", "pandas"):
            versiones[paquete] = importlib.metadata.version(paquete)
        informe = dict(
            alcance="Diagnóstico con información perfecta: no son alternativas admisibles ni entran en B − A",
            inicio_utc=inicio.isoformat(), fin_utc=fin.isoformat(), periodo_local=config["periodos"]["evaluacion"],
            control=dict({c: config[c] for c in ("horizonte", "replanificacion", "escenarios", "lambda_terminal")},
                         seleccion_cargas=config.get("seleccion_cargas", "prioridad")),
            semillas={"demanda": config["semilla"], "escenarios": config["semilla_escenarios"]},
            terminal_minimo=terminal, solver=optimo["solver"], verificacion_optimo=verificacion,
            verificacion_fisica=fisica, oraculo=oraculo, distancias_al_optimo=distancias,
            metricas=metricas, tiempos_s=tiempos, entradas_sha256=entradas, codigo_sha256=codigo, entorno=versiones)
        if entradas_sha256(caso.rutas) != entradas or codigo_sha256() != codigo:
            raise ValueError("cotas: cambiaron entradas o código durante el diagnóstico")
        (salida / "resumen.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2, default=str) + "\n",
                                             encoding="utf-8")
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\nLa salida {args.salida} puede estar incompleta; no es válida.\n")
    for nombre, d in distancias.items():
        print(f"{nombre:18s} J={d['J']:.3f}  óptimo − J = {d['optimo_menos_J']:.3f} ({d['relativa']:.2%})")


if __name__ == "__main__":
    main()
