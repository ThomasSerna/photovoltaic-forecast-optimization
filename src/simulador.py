"""T03: física horaria corregida de IDEA.md §4, basada en Acuña et al. (2021).

Energías en Wh y potencias en W; paso fijo de 1 h. Eficiencias, límites de
potencia y reparto por identificador son adaptaciones del proyecto, no una
transcripción literal de las ecuaciones (1)–(10) del artículo.
"""

import math

import pandas as pd

from .configuracion import RESOLUCION_H, SELECCIONES, numero
from .datos import TOLERANCIA_NUMERICA

PRIORIDAD_CRITICA = 3  # IDEA §6: solo p >= 3 puede descargar por debajo de la reserva


def _energia_fotovoltaica(ghi_w_m2, pv):
    """Oferta FV de una hora; 1000 W/m² es referencia STC, no una cota de GHI."""
    numero(ghi_w_m2, "ghi_w_m2")
    potencia_w = min(pv["inversor_max_w"],
                     pv["potencia_nominal_w"] * (ghi_w_m2 / 1000) * pv["factor_desempeno"])
    return potencia_w * RESOLUCION_H


def _limite_descarga(bateria, energia_wh):
    """Máxima energía entregable al bus desde el estado previo (IDEA §4.3)."""
    return min(bateria["potencia_descarga_max_w"] * RESOLUCION_H,
               bateria["eficiencia_descarga"] * (energia_wh - bateria["minimo_wh"]))


def _despachar(fv_wh, atendida_wh, baterias, estado, limites_descarga=None):
    """Despacha solo la demanda aceptada; devuelve flujos, estado nuevo y vertido.

    Configuración y estado deben estar validados; atendida_wh debe ser factible.
    Carga únicamente con excedente FV y descarga únicamente para cubrir déficit.
    Las cotas operativas opcionales limitan la descarga por batería en el plan.
    """
    excedente_wh = max(0., fv_wh - atendida_wh)
    deficit_wh = max(0., atendida_wh - fv_wh)
    flujos, estado_siguiente = [], {}
    for bateria in sorted(baterias, key=lambda b: b["id"]):
        bateria_id = bateria["id"]
        inicial_wh = estado[bateria_id]
        eta_c, eta_d = bateria["eficiencia_carga"], bateria["eficiencia_descarga"]
        hueco_bus_wh = (bateria["maximo_wh"] - inicial_wh) / eta_c
        disponible_bus_wh = eta_d * (inicial_wh - bateria["minimo_wh"])
        carga_wh = min(excedente_wh, bateria["potencia_carga_max_w"] * RESOLUCION_H, hueco_bus_wh)
        limite_descarga_wh = _limite_descarga(bateria, inicial_wh)
        if limites_descarga is not None:
            limite_descarga_wh = min(limite_descarga_wh, limites_descarga[bateria_id])
        descarga_wh = min(deficit_wh, limite_descarga_wh)
        final_wh = inicial_wh
        # C_{t+1} = C_t + eta_c*c - d/eta_d; cerca del límite, calcular desde ese extremo evita cancelación.
        if carga_wh > 0:
            if carga_wh <= hueco_bus_wh / 2:
                final_wh = inicial_wh + eta_c * carga_wh
            else:
                final_wh = bateria["maximo_wh"] - (hueco_bus_wh - carga_wh) * eta_c
        elif descarga_wh > 0:
            if descarga_wh <= disponible_bus_wh / 2:
                final_wh = inicial_wh - descarga_wh / eta_d
            else:
                final_wh = bateria["minimo_wh"] + (disponible_bus_wh - descarga_wh) / eta_d
        excedente_wh -= carga_wh
        deficit_wh -= descarga_wh
        estado_siguiente[bateria_id] = final_wh
        flujos.append(dict(bateria_id=bateria_id, inicial_wh=inicial_wh, carga_wh=carga_wh,
                           descarga_wh=descarga_wh, final_wh=final_wh))
        error_bateria_wh = final_wh - (inicial_wh + eta_c * carga_wh - descarga_wh / eta_d)
        if (not bateria["minimo_wh"] <= final_wh <= bateria["maximo_wh"]
                or abs(error_bateria_wh) > TOLERANCIA_NUMERICA or carga_wh * descarga_wh != 0):
            raise ValueError(f"baterias.{bateria_id}: despacho inconsistente")
    error_bus_wh = math.fsum([fv_wh, -atendida_wh, -excedente_wh,
                             *(f["descarga_wh"] - f["carga_wh"] for f in flujos)])
    if deficit_wh > TOLERANCIA_NUMERICA or abs(error_bus_wh) > TOLERANCIA_NUMERICA:
        raise ValueError("despacho: la energía atendida no conserva el balance del bus")
    return flujos, estado_siguiente, excedente_wh


def _copiar_tabla(tabla, nombre, columnas):
    """Comprueba columnas y tiempos enteros; trabaja siempre sobre una copia."""
    if not isinstance(tabla, pd.DataFrame) or tabla.empty:
        raise ValueError(f"{nombre}: se requiere una tabla no vacía")
    if not tabla.columns.is_unique or not set(columnas) <= set(tabla.columns):
        raise ValueError(f"{nombre}: columnas ausentes o repetidas; se requieren {columnas}")
    tabla = tabla.copy()
    for valor in tabla["t"].tolist():
        numero(valor, f"{nombre}.t", entero=True)
    if "carga_id" in columnas and not tabla["carga_id"].map(
            lambda v: isinstance(v, str) and bool(v.strip())).all():
        raise ValueError(f"{nombre}.carga_id: se requieren identificadores no vacíos")
    return tabla


def _preparar_entradas(demanda, irradiancia, compromisos):
    """Valida la rejilla carga-hora y alinea el calendario fijo por claves, no por posición."""
    claves = ["t", "carga_id"]
    demanda = _copiar_tabla(demanda, "demanda", claves + ["demanda_wh", "solicitada",
                          "prioridad_supuesta", "potencia_w_supuesta"])
    irradiancia = _copiar_tabla(irradiancia, "irradiancia", ["t", "ghi_w_m2"])
    if demanda.duplicated(claves).any() or irradiancia["t"].duplicated().any():
        raise ValueError("demanda/irradiancia: claves repetidas")
    for campo in ("demanda_wh", "prioridad_supuesta", "potencia_w_supuesta"):
        for valor in demanda[campo].tolist():
            numero(valor, f"demanda.{campo}", estricto=campo != "demanda_wh",
                   entero=campo == "prioridad_supuesta")
    for valor in demanda["solicitada"].tolist():
        numero(valor, "demanda.solicitada", maximo=1, entero=True)
    if not (demanda["solicitada"] == (demanda["demanda_wh"] > 0).astype(int)).all():
        raise ValueError("demanda.solicitada: debe indicar si demanda_wh > 0")
    if (demanda["demanda_wh"] > demanda["potencia_w_supuesta"] * RESOLUCION_H).any():
        raise ValueError("demanda.demanda_wh: supera potencia nominal por intervalo")
    for valor in irradiancia["ghi_w_m2"].tolist():
        numero(valor, "irradiancia.ghi_w_m2")
    horas = sorted(irradiancia["t"].tolist())
    if horas != list(range(horas[0], horas[0] + len(horas))):
        raise ValueError("irradiancia.t: se requieren horas consecutivas")
    if (set(demanda["t"]) != set(horas)
            or len(demanda) != len(horas) * demanda["carga_id"].nunique()):
        raise ValueError("demanda: rejilla carga-hora incompleta o distinta de irradiancia")
    demanda = demanda.sort_values(claves).reset_index(drop=True)
    demanda["x"] = demanda["solicitada"]
    if compromisos is not None:
        plan = _copiar_tabla(compromisos, "compromisos", claves + ["x"])
        for valor in plan["x"].tolist():
            numero(valor, "compromisos.x", maximo=1, entero=True)
        indice = pd.MultiIndex.from_frame(demanda[claves])
        if (len(plan) != len(demanda) or plan.duplicated(claves).any()
                or not indice.isin(pd.MultiIndex.from_frame(plan[claves])).all()):
            raise ValueError("compromisos: las claves deben coincidir exactamente con la demanda")
        demanda["x"] = plan.set_index(claves)["x"].reindex(indice).to_numpy()
        if (demanda["x"] > demanda["solicitada"]).any():
            raise ValueError("compromisos.x: no puede superar la solicitud")
    return demanda, irradiancia.sort_values("t")


def _estado_inicial(baterias, estado):
    """Inicializa una vez; un estado suministrado debe cubrir todas las baterías."""
    if estado is None:
        estado = {b["id"]: b["inicial_wh"] for b in baterias}
    if not isinstance(estado, dict) or set(estado) != {b["id"] for b in baterias}:
        raise ValueError("estado_inicial: se requiere un valor por identificador de batería")
    for bateria in baterias:
        numero(estado[bateria["id"]], f"estado_inicial.{bateria['id']}",
               minimo=bateria["minimo_wh"], maximo=bateria["maximo_wh"])
    return estado.copy()


def _limites_reserva(baterias, estado, fv_wh, critica_wh, reserva):
    """Permite gastar la reserva solo en cargas críticas, sin actualizar estados."""
    deficit = max(0., critica_wh - fv_wh)
    limites = {}
    for bateria in sorted(baterias, key=lambda b: b["id"]):
        id_ = bateria["id"]
        limite = _limite_descarga(bateria, estado[id_])
        critica = min(deficit, limite)
        deficit -= critica
        piso = bateria["minimo_wh"] + reserva * (bateria["maximo_wh"] - bateria["minimo_wh"])
        despues_critica = estado[id_] - critica / bateria["eficiencia_descarga"]
        adicional = min(limite - critica,
                        bateria["eficiencia_descarga"] * max(0., despues_critica - piso))
        limites[id_] = min(limite, critica + adicional)
    return limites


def _registrar_servicio(carga, calendario_fijo):
    if not calendario_fijo:
        carga["x"] = carga["z"]
    carga["comprometida_wh"] = carga["demanda_wh"] * carga["x"]
    carga["atendida_wh"] = carga["demanda_wh"] * carga["z"]
    carga["no_atendida_wh"] = carga["demanda_wh"] - carga["atendida_wh"]


def _seleccionar(cargas, fv_wh, baterias, estado, potencia_max_w, calendario_fijo, reserva,
                 seleccion="prioridad"):
    """Interpretación de algoritmos 1–2: (-prioridad, -energía, id), servicio completo.

    Con seleccion="mochila" y potencia nominal candidata superior al límite, delega en
    _seleccionar_mochila (adaptación opcional, IDEA §4.4); en otro caso no cambia nada.
    """
    orden = sorted(cargas, key=lambda c: (-c["prioridad_supuesta"], -c["demanda_wh"], c["carga_id"]))
    if (seleccion == "mochila"
            and math.fsum(c["potencia_w_supuesta"] for c in orden if c["x"] == 1) > potencia_max_w):
        return _seleccionar_mochila(orden, fv_wh, baterias, estado, potencia_max_w, calendario_fijo, reserva)
    energias, potencias = [], []
    disponible_wh = math.fsum([fv_wh, *(_limite_descarga(b, estado[b["id"]]) for b in baterias)])
    limites = None
    for carga in orden:
        if reserva and carga["prioridad_supuesta"] < PRIORIDAD_CRITICA and limites is None:
            limites = _limites_reserva(baterias, estado, fv_wh, math.fsum(energias), reserva)
            disponible_wh = math.fsum([fv_wh, *limites.values()])
        energia = math.fsum([*energias, carga["demanda_wh"]])
        potencia = math.fsum([*potencias, carga["potencia_w_supuesta"]])
        carga["z"] = int(carga["x"] == 1 and energia <= disponible_wh and potencia <= potencia_max_w)
        if carga["z"]:
            energias.append(carga["demanda_wh"])
            potencias.append(carga["potencia_w_supuesta"])
        _registrar_servicio(carga, calendario_fijo)
    return math.fsum(energias), math.fsum(potencias), limites


def _seleccionar_mochila(orden, fv_wh, baterias, estado, potencia_max_w, calendario_fijo, reserva):
    """Críticas con la regla de prioridad; no críticas por enumeración exacta de subconjuntos.

    Maximiza Σ p·D, el beneficio de la ecuación (10), con los mismos presupuestos de energía
    (o límites de reserva de §6) y potencia nominal que la regla original, sin tolerancia.
    Empates: menor energía y después la primera máscara en el orden (-p, -D, id).
    """
    energias, potencias = [], []
    disponible_wh = math.fsum([fv_wh, *(_limite_descarga(b, estado[b["id"]]) for b in baterias)])
    limites = None
    no_criticas = [c for c in orden if c["prioridad_supuesta"] < PRIORIDAD_CRITICA]
    for carga in orden:
        if carga["prioridad_supuesta"] >= PRIORIDAD_CRITICA:
            energia = math.fsum([*energias, carga["demanda_wh"]])
            potencia = math.fsum([*potencias, carga["potencia_w_supuesta"]])
            carga["z"] = int(carga["x"] == 1 and energia <= disponible_wh and potencia <= potencia_max_w)
            if carga["z"]:
                energias.append(carga["demanda_wh"])
                potencias.append(carga["potencia_w_supuesta"])
    if reserva and no_criticas:
        limites = _limites_reserva(baterias, estado, fv_wh, math.fsum(energias), reserva)
        disponible_wh = math.fsum([fv_wh, *limites.values()])
    elegibles = [c for c in no_criticas if c["x"] == 1]
    mejor, clave_mejor = [], None
    for mascara in range(2 ** len(elegibles)):
        subconjunto = [c for i, c in enumerate(elegibles) if mascara >> i & 1]
        energia = math.fsum([*energias, *(c["demanda_wh"] for c in subconjunto)])
        potencia = math.fsum([*potencias, *(c["potencia_w_supuesta"] for c in subconjunto)])
        if energia <= disponible_wh and potencia <= potencia_max_w:
            clave = (math.fsum(c["prioridad_supuesta"] * c["demanda_wh"] for c in subconjunto), -energia)
            if clave_mejor is None or clave > clave_mejor:
                mejor, clave_mejor = subconjunto, clave
    elegidas = {c["carga_id"] for c in mejor}
    for carga in no_criticas:
        carga["z"] = int(carga["carga_id"] in elegidas)
    energias.extend(c["demanda_wh"] for c in mejor)
    potencias.extend(c["potencia_w_supuesta"] for c in mejor)
    for carga in orden:
        _registrar_servicio(carga, calendario_fijo)
    return math.fsum(energias), math.fsum(potencias), limites


def simular(demanda, irradiancia, configuracion, *, estado_inicial=None, compromisos=None, reserva=0):
    """Simula una trayectoria explícita sin modificar entradas ni consultar otras fuentes.

    `configuracion` procede de cargar_caso/cargar_configuracion (T01). `demanda`
    conserva su rejilla completa de equipos y horas; `irradiancia` aporta t y
    ghi_w_m2. `compromisos`, si existe, aporta t, carga_id y x para esa misma
    rejilla. Los t enteros consecutivos pueden empezar en cualquier hora >= 0.
    `reserva` en [0, 1] protege esa fracción de la capacidad utilizable individual
    solo ante cargas de prioridad < 3; genera candidatos de T04. Una reserva
    no nula es incompatible con compromisos fijos, evaluados sin otra reserva.
    `configuracion["seleccion_cargas"]` elige la regla de selección: "prioridad"
    (por defecto, también si falta) o "mochila", adaptación opcional de IDEA §4.4.

    Devuelve tablas cargas/horas/baterias, estado_final por batería y resumen
    energético con J y S_pond. Para continuar, pasar estado_final a la siguiente
    llamada; sin estado se usan los iniciales configurados. No implementa MPC.
    """
    numero(configuracion["resolucion_h"], "resolucion_h", minimo=1, maximo=1, entero=True)
    numero(reserva, "reserva", maximo=1)
    seleccion = configuracion.get("seleccion_cargas", "prioridad")
    if seleccion not in SELECCIONES:
        raise ValueError(f"seleccion_cargas: se admite {SELECCIONES}; recibido {seleccion!r}")
    if reserva and compromisos is not None:
        raise ValueError("reserva: solo se aplica al generar planes, sin compromisos fijos")
    demanda, irradiancia = _preparar_entradas(demanda, irradiancia, compromisos)
    baterias = configuracion["baterias"]
    estado = _estado_inicial(baterias, estado_inicial)
    potencia_max_w = min(configuracion["potencia_bus_max_w"], configuracion["pv"]["inversor_max_w"])
    ghi = irradiancia.set_index("t")["ghi_w_m2"].to_dict()
    filas_cargas, filas_horas, filas_baterias = [], [], []
    for t, grupo in demanda.groupby("t", sort=True):
        cargas = grupo.to_dict("records")
        fv_wh = _energia_fotovoltaica(ghi[t], configuracion["pv"])
        atendida_wh, potencia_w, limites = _seleccionar(
            cargas, fv_wh, baterias, estado, potencia_max_w, compromisos is not None, reserva, seleccion)
        flujos, estado, vertido_wh = _despachar(fv_wh, atendida_wh, baterias, estado, limites)
        solicitada_wh = math.fsum(c["demanda_wh"] for c in cargas)
        comprometida_wh = math.fsum(c["comprometida_wh"] for c in cargas)
        filas_cargas.extend(cargas)
        filas_baterias.extend(dict(t=t, **flujo) for flujo in flujos)
        filas_horas.append(dict(t=t, fv_wh=fv_wh, solicitada_wh=solicitada_wh,
                               comprometida_wh=comprometida_wh, atendida_wh=atendida_wh,
                               no_atendida_wh=math.fsum(c["no_atendida_wh"] for c in cargas),
                               potencia_atendida_w=potencia_w, vertido_wh=vertido_wh))
    cargas = pd.DataFrame(filas_cargas)
    horas, flujos = pd.DataFrame(filas_horas), pd.DataFrame(filas_baterias)
    resumen = {campo: math.fsum(horas[campo]) for campo in
               ("fv_wh", "solicitada_wh", "comprometida_wh", "atendida_wh", "no_atendida_wh", "vertido_wh")}
    resumen.update({campo: math.fsum(flujos[campo]) for campo in ("carga_wh", "descarga_wh")})
    resumen["J"] = math.fsum(c["prioridad_supuesta"] * c["atendida_wh"] for c in filas_cargas)
    solicitada_ponderada = math.fsum(c["prioridad_supuesta"] * c["demanda_wh"] for c in filas_cargas)
    resumen["S_pond"] = resumen["J"] / solicitada_ponderada if solicitada_ponderada else None
    return dict(cargas=cargas, horas=horas, baterias=flujos, estado_final=estado, resumen=resumen)
