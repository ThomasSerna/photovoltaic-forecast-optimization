"""Modelo de la demanda eléctrica SINTÉTICA del Centro Educativo Rural El Rayo (energía en Wh).

Genera, de forma reproducible a partir de la semilla de configuracion.json, la demanda horaria
por carga, la demanda horaria total y el resumen diario, además del calendario, las lecturas del
medidor y sus intervalos. No lee ni escribe archivos: de eso se encarga generacion.py.

Procedimiento para cada día (ver docs/IDEA.md y docs/datos.md):
1. Objetivo diario de energía. En un día lectivo se elige al azar, con reemplazo, una de
   las cuatro diferencias entre lecturas consecutivas del medidor y se multiplica por un
   factor uniforme en `variacion_lectiva`. En fin de semana el objetivo es solo la nevera.
2. La nevera consume `nevera_wh_dia` por un factor uniforme en `variacion_nevera`,
   repartido entre las 24 horas.
3. El resto del objetivo (actividad escolar) se reparte entre las franjas carga-hora de
   las cargas no permanentes, sin superar potencia × horas disponibles. Si no cabe, se lanza
   un error: no se recorta la demanda ni se añaden cargas ocultas.
"""
import random
from datetime import date, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from .configuracion import (
    CLASES_FIN_H, CLASES_INICIO_H, DIAS_LECTIVOS, DIAS_POR_SEMANA, DIAS_SEMANA, HORAS_CON_CLASE,
    HORAS_DIA, ID_NEVERA, RESOLUCION_H, validar_demanda,
)


# --- 1. Estructura temporal y partición ---
# Los intervalos de 1 h se indexan por t = 24·d + h (d: día desde 0, h: hora local) y las semanas empiezan en lunes.

PARTICION_DESARROLLO = "desarrollo_sintetico"
PARTICION_EVALUACION = "evaluacion_sintetica"


def es_lectivo(dia):
    """Indica si el día d (desde 0, empezando en lunes) tiene clases."""
    return dia % DIAS_POR_SEMANA in DIAS_LECTIVOS


def particion_del_dia(dia, config):
    """Partición cronológica del día d: las primeras `semanas_desarrollo` semanas son de desarrollo."""
    desarrollo = dia < DIAS_POR_SEMANA * config["semanas_desarrollo"]
    return PARTICION_DESARROLLO if desarrollo else PARTICION_EVALUACION


def fecha_del_dia(dia, config):
    """Fecha ISO asignada al día d; la fecha de inicio sintética es lunes."""
    return (date.fromisoformat(config["fecha_inicio_sintetica"]) + timedelta(days=dia)).isoformat()


# --- 2. Supuestos fijos del modelo ---
# No están en configuracion.json. Cambiarlos altera los datos que produce cada semilla.

WH_POR_KWH = 1000
NOCHE_INICIO_H = 19  # de 19:00 a 07:00 el peso de la nevera es PESO_NOCTURNO_NEVERA; de día, 1
NOCHE_FIN_H = 7
PESO_NOCTURNO_NEVERA = 0.8
RUIDO_NEVERA = (0.9, 1.1)  # ε_h ~ U[0.9, 1.1]: un sorteo por hora de la nevera
RUIDO_OCUPACION = (0.85, 1.15)  # ξ_{k,h} ~ U[0.85, 1.15]: un sorteo por franja carga-hora
FACTOR_MAXIMO = 100.  # cota superior de la bisección de κ_d (ver factor_de_asignacion)
ITERACIONES_BISECCION = 90
DECIMALES_WH = 6  # la energía publicada (Wh) se redondea a 6 decimales
DECIMALES_FACTOR = 9  # los factores y las horas equivalentes, a 9
TOLERANCIA_REDONDEO_WH = 1e-6  # holgura que absorbe ese redondeo en los límites de potencia
TOLERANCIA_CONSERVACION_WH = 0.001  # máxima diferencia entre la suma de las horas y el objetivo del día


# --- 3. Horas disponibles de cada carga ---

def horas_solapadas(hora, inicio_h, fin_h):
    """Horas (de 0 a 1) de la franja [hora, hora + 1) que caen dentro de la ventana [inicio_h, fin_h)."""
    return max(0., min(hora + 1., fin_h) - max(float(hora), inicio_h))


def horas_disponibles(carga, hora, lectivo):
    """Horas (de 0 a 1) de la franja [hora, hora + 1) en las que la carga puede funcionar.

    Una carga permanente puede funcionar toda la hora; las demás, solo en días lectivos
    y dentro de su ventana de uso [inicio_h, fin_h).
    """
    if carga["permanente"]:
        return 1.
    if not lectivo:
        return 0.
    return horas_solapadas(hora, carga["inicio_h"], carga["fin_h"])


# --- 4. Objetivo de energía del día ---

def referencias_del_medidor(lecturas):
    """Energía D^ref_i (Wh) entre lecturas consecutivas del medidor: los intervalos I1 a I4."""
    return [WH_POR_KWH * (fin["entero_kwh"] - inicio["entero_kwh"])
            for inicio, fin in zip(lecturas, lecturas[1:])]


class ObjetivoDia(NamedTuple):
    """Objetivo de energía D_d del día d (Wh) y los sorteos que lo determinan.

    indice, referencia_wh y multiplicador (i_d, D^ref_i y m_d) son None en fin de semana,
    cuando el objetivo es solo la energía de la nevera D^nev_d.
    """

    indice: int | None
    referencia_wh: int | None
    multiplicador: float | None
    nevera_wh: float
    total_wh: float


def sortear_objetivo(lectivo, referencias_wh, config, rng):
    """Sortea el objetivo D_d del día. Los sorteos van siempre en este orden: cambiarlo altera los datos.

    - Día lectivo: una referencia i_d al azar (con reemplazo), D_d = D^ref_{i_d}·m_d y m_d ~ U(variacion_lectiva).
    - Fin de semana: D_d = D^nev_d.
    En ambos casos D^nev_d = nevera_wh_dia·u_d, con u_d ~ U(variacion_nevera), se sortea al final.
    """
    indice = rng.randrange(len(referencias_wh)) if lectivo else None
    multiplicador = rng.uniform(*config["variacion_lectiva"]) if lectivo else None
    nevera_wh = config["nevera_wh_dia"] * rng.uniform(*config["variacion_nevera"])
    if not lectivo:
        return ObjetivoDia(None, None, None, nevera_wh, nevera_wh)
    referencia_wh = referencias_wh[indice]
    return ObjetivoDia(indice, referencia_wh, multiplicador, nevera_wh, referencia_wh * multiplicador)


# --- 5. Reparto del objetivo entre horas y cargas ---

def perfil_nevera(nevera_dia_wh, rng):
    """Reparte la energía diaria de la nevera (Wh) entre las 24 horas, en proporción a g_h = ν_h·ε_h.

    ν_h vale PESO_NOCTURNO_NEVERA de noche (19:00 a 07:00) y 1 de día; ε_h es un ruido uniforme
    en RUIDO_NEVERA. Consume HORAS_DIA sorteos, uno por hora.
    """
    pesos = []
    for hora in range(HORAS_DIA):
        es_noche = hora < NOCHE_FIN_H or hora >= NOCHE_INICIO_H
        pesos.append((PESO_NOCTURNO_NEVERA if es_noche else 1.) * rng.uniform(*RUIDO_NEVERA))
    suma_pesos = sum(pesos)
    return [nevera_dia_wh * peso / suma_pesos for peso in pesos]


class Franja(NamedTuple):
    """Franja carga-hora de un día lectivo en la que una carga no permanente puede funcionar."""

    carga_id: str
    hora: int  # hora local h: la franja es [h, h + 1)
    capacidad_wh: float  # D^max_{k,h} = P_k·ℓ_{k,h}: energía máxima de la franja
    peso: float  # ω_{k,h} = o_k·ξ_{k,h}: ocupación base supuesta por un ruido uniforme


def franjas_de_uso(cargas, rng):
    """Franjas carga-hora en las que las cargas no permanentes pueden funcionar un día lectivo.

    Recorre las cargas en el orden del catálogo y las horas en orden creciente; solo consume un
    sorteo (ξ_{k,h} en RUIDO_OCUPACION) por cada franja con horas dentro de la ventana de uso.
    """
    franjas = []
    for carga in cargas:
        if carga["permanente"]:
            continue
        for hora in range(HORAS_DIA):
            horas = horas_solapadas(hora, carga["inicio_h"], carga["fin_h"])
            if horas > 0:
                capacidad_wh = carga["potencia_w_supuesta"] * horas
                peso = carga["ocupacion_base_supuesta"] * rng.uniform(*RUIDO_OCUPACION)
                franjas.append(Franja(carga["id"], hora, capacidad_wh, peso))
    return franjas


def energia_franja(franja, factor):
    """Energía asignada a una franja (Wh): D_{k,h}(κ) = D^max_{k,h}·min(1, κ·ω_{k,h})."""
    return franja.capacidad_wh * min(1., factor * franja.peso)


def factor_de_asignacion(franjas, objetivo_wh):
    """Factor κ con el que la energía asignada al conjunto de franjas suma objetivo_wh.

    La energía asignada no decrece al aumentar κ, así que este se busca por bisección en
    [0, FACTOR_MAXIMO] con ITERACIONES_BISECCION iteraciones y se devuelve el último punto medio.
    Falla si el objetivo es negativo o supera la capacidad total.
    """
    capacidad_total_wh = sum(franja.capacidad_wh for franja in franjas)
    if not 0 <= objetivo_wh <= capacidad_total_wh:
        raise ValueError("Demanda incompatible con potencias/horarios supuestos. "
                         "Revisar configuracion; no se recorta la demanda.")
    # ponytail: FACTOR_MAXIMO = 100 satura toda franja con ω ≥ 0.01. Si un objetivo exigiera un κ mayor,
    # no se alcanzaría y comprobar_conservacion fallaría. Mejora: cota 1 / min(ω), que satura todas.
    bajo, alto = 0., FACTOR_MAXIMO
    for _ in range(ITERACIONES_BISECCION):
        factor = (bajo + alto) / 2
        asignada_wh = sum(energia_franja(franja, factor) for franja in franjas)
        if asignada_wh < objetivo_wh:
            bajo = factor
        else:
            alto = factor
    return factor


def energia_del_dia(cargas, lectivo, objetivo, rng):
    """Energía de cada carga en cada hora local del día (Wh) y factor κ_d de asignación.

    La nevera reparte D^nev_d entre las 24 horas. En un día lectivo, la actividad escolar
    D_d − D^nev_d se reparte entre las franjas de uso de las demás cargas; en fin de semana κ_d = 0.
    """
    energia = {carga["id"]: [0.] * HORAS_DIA for carga in cargas}
    energia[ID_NEVERA] = perfil_nevera(objetivo.nevera_wh, rng)
    if not lectivo:
        return energia, 0.
    franjas = franjas_de_uso(cargas, rng)
    factor = factor_de_asignacion(franjas, objetivo.total_wh - objetivo.nevera_wh)
    for franja in franjas:
        energia[franja.carga_id][franja.hora] = energia_franja(franja, factor)
    return energia, factor


# --- 6. Filas publicadas y generación del horizonte ---

def comprobar_limites(t, carga, demanda_wh, horas, en_clase):
    """Falla si la demanda de una carga-hora supera potencia × horas disponibles o cae fuera de clases."""
    capacidad_wh = carga["potencia_w_supuesta"] * horas
    if not 0 <= demanda_wh <= capacidad_wh + TOLERANCIA_REDONDEO_WH:
        raise ValueError(f"demanda: t={t}, carga={carga['id']}: {demanda_wh} Wh fuera de potencia × horas disponibles")
    if not carga["permanente"] and demanda_wh and not en_clase:
        raise ValueError(f"demanda: t={t}, carga={carga['id']}: demanda fuera del horario lectivo")


def filas_carga_hora(t, hora, lectivo, particion, energia, cargas):
    """Filas carga-hora del intervalo t y su demanda total (Wh), acumulada en el orden del catálogo."""
    filas, total_wh = [], 0.
    for carga in cargas:
        demanda_wh = round(energia[carga["id"]][hora], DECIMALES_WH)
        horas = horas_disponibles(carga, hora, lectivo)
        comprobar_limites(t, carga, demanda_wh, horas, lectivo and hora in HORAS_CON_CLASE)
        total_wh += demanda_wh
        filas.append(dict(
            t=t,
            carga_id=carga["id"],
            demanda_wh=demanda_wh,
            solicitada=int(demanda_wh > 0),
            prioridad_supuesta=carga["prioridad_supuesta"],
            potencia_w_supuesta=carga["potencia_w_supuesta"],
            horas_disponibles=horas,
            horas_uso_equivalentes=round(demanda_wh / carga["potencia_w_supuesta"], DECIMALES_FACTOR),
            particion=particion,
            origen="sintetico",
        ))
    return filas, total_wh


def filas_del_dia(dia, lectivo, energia, config):
    """Filas horarias y carga-hora del día d, y su demanda total (Wh).

    La energía se redondea a DECIMALES_WH y se comprueban los límites de cada carga. El total de
    cada hora se redondea antes de acumularse en el del día.
    """
    particion, fecha = particion_del_dia(dia, config), fecha_del_dia(dia, config)
    filas_horarias, filas_por_carga = [], []
    total_dia_wh = 0.
    for hora in range(HORAS_DIA):
        t = HORAS_DIA * dia + hora
        filas_hora, total_hora_wh = filas_carga_hora(t, hora, lectivo, particion, energia, config["cargas"])
        total_hora_wh = round(total_hora_wh, DECIMALES_WH)
        total_dia_wh += total_hora_wh
        nevera_hora_wh = energia[ID_NEVERA][hora]
        filas_por_carga += filas_hora
        filas_horarias.append(dict(
            t=t,
            dia_indice=dia + 1,
            semana=dia // DIAS_POR_SEMANA + 1,
            dia_semana=DIAS_SEMANA[dia % DIAS_POR_SEMANA],
            hora_local=hora,
            fecha=fecha,
            zona_horaria=config["zona_horaria"],
            duracion_h=RESOLUCION_H,
            horas_clase=horas_solapadas(hora, CLASES_INICIO_H, CLASES_FIN_H) if lectivo else 0.,
            demanda_total_wh=total_hora_wh,
            nevera_wh=round(nevera_hora_wh, DECIMALES_WH),
            actividad_escolar_wh=round(total_hora_wh - nevera_hora_wh, DECIMALES_WH),
            particion=particion,
            origen="sintetico",
        ))
    return filas_horarias, filas_por_carga, total_dia_wh


def comprobar_conservacion(dia, total_dia_wh, objetivo_wh):
    """Falla si la suma de las horas del día d se aparta del objetivo D_d más de la tolerancia."""
    if abs(total_dia_wh - objetivo_wh) > TOLERANCIA_CONSERVACION_WH:
        raise ValueError(f"demanda: día {dia + 1}: total {total_dia_wh} Wh no conserva objetivo {objetivo_wh} Wh "
                         f"(tolerancia {TOLERANCIA_CONSERVACION_WH} Wh)")


def fila_diaria(dia, lectivo, objetivo, factor, total_dia_wh, config):
    """Fila del resumen diario: objetivo D_d con sus sorteos, demanda total conciliada y factor κ_d."""
    return dict(
        dia_indice=dia + 1,
        semana=dia // DIAS_POR_SEMANA + 1,
        dia_semana=DIAS_SEMANA[dia % DIAS_POR_SEMANA],
        fecha=fecha_del_dia(dia, config),
        es_lectivo=int(lectivo),
        intervalo_referencia=f"I{objetivo.indice + 1}" if lectivo else "",
        referencia_wh=objetivo.referencia_wh if lectivo else "",
        multiplicador_lectivo=round(objetivo.multiplicador, DECIMALES_FACTOR) if lectivo else "",
        objetivo_wh=round(objetivo.total_wh, DECIMALES_WH),
        demanda_total_wh=round(total_dia_wh, DECIMALES_WH),
        nevera_wh=round(objetivo.nevera_wh, DECIMALES_WH),
        factor_asignacion=round(factor, DECIMALES_FACTOR),
        particion=particion_del_dia(dia, config),
        origen="sintetico",
    )


def generar_demanda(config):
    """Genera la demanda sintética de todo el horizonte, con la energía en Wh.

    Devuelve tres listas de filas (diccionarios): demanda horaria total, demanda por
    carga y hora, y resumen diario.

    Cada día consume números aleatorios siempre en este orden (cambiarlo altera los datos de
    cada semilla): sortear_objetivo (referencia, multiplicador, nevera), perfil_nevera (24 ruidos
    horarios) y franjas_de_uso (un ruido por franja, solo en días lectivos).
    """
    validar_demanda(config)
    referencias_wh = referencias_del_medidor(config["lecturas"])
    rng = random.Random(config["semilla"])
    horaria, por_carga, diaria = [], [], []
    for dia in range(DIAS_POR_SEMANA * config["semanas"]):
        lectivo = es_lectivo(dia)
        objetivo = sortear_objetivo(lectivo, referencias_wh, config, rng)
        energia, factor = energia_del_dia(config["cargas"], lectivo, objetivo, rng)
        filas_horarias, filas_por_carga, total_dia_wh = filas_del_dia(dia, lectivo, energia, config)
        comprobar_conservacion(dia, total_dia_wh, objetivo.total_wh)
        horaria += filas_horarias
        por_carga += filas_por_carga
        diaria.append(fila_diaria(dia, lectivo, objetivo, factor, total_dia_wh, config))
    return horaria, por_carga, diaria


# --- 7. Calendario, lecturas del medidor e intervalos ---

def generar_calendario(config):
    """Asigna fechas locales a t; no representa las fechas desconocidas del medidor."""
    inicio = datetime.combine(date.fromisoformat(config["fecha_inicio_sintetica"]),
                              datetime.min.time(), ZoneInfo(config["zona_horaria"]))
    filas = []
    for t in range(config["semanas"] * DIAS_POR_SEMANA * HORAS_DIA):
        fecha = inicio + timedelta(hours=t)
        filas.append(dict(
            t=t,
            dia_indice=t // HORAS_DIA + 1,
            semana=t // (HORAS_DIA * DIAS_POR_SEMANA) + 1,
            dia_semana=DIAS_SEMANA[fecha.weekday()],
            hora_local=fecha.hour,
            inicio_intervalo=fecha.isoformat(),
            fin_intervalo=(fecha + timedelta(hours=1)).isoformat(),
            fecha_asignada_simulacion=fecha.date().isoformat(),
            particion_demanda=particion_del_dia(t // HORAS_DIA, config),
            origen_calendario="asignado_para_simulacion_no_fecha_del_medidor",
        ))
    return filas


def filas_lecturas(lecturas):
    """Lecturas del medidor. Solo se transcribió la parte entera, así que el valor real
    está en [entero_kwh, entero_kwh + 1). Fecha, hora y décimas no están confirmadas."""
    return [dict(
        orden=orden,
        dia=lectura["dia"],
        fecha="",
        hora="",
        entero_kwh=lectura["entero_kwh"],
        decimal_kwh="",
        cota_inferior_kwh=lectura["entero_kwh"],
        cota_superior_exclusiva_kwh=lectura["entero_kwh"] + 1,
        fuente=lectura["dia"] + ".jpeg",
        estado="entero_transcrito; decimal_no_usado",
    ) for orden, lectura in enumerate(lecturas, 1)]


def filas_intervalos(lecturas):
    """Intervalos entre lecturas consecutivas (I1, I2, ...), que se suponen de 24 h.

    Como cada lectura está truncada, la diferencia real está en el intervalo abierto
    (diferencia - 1, diferencia + 1) kWh.
    """
    filas = []
    for numero, (inicio, fin) in enumerate(zip(lecturas, lecturas[1:]), 1):
        diferencia_kwh = fin["entero_kwh"] - inicio["entero_kwh"]
        filas.append(dict(
            id=f"I{numero}",
            inicio=inicio["dia"],
            fin=fin["dia"],
            diferencia_enteros_kwh=diferencia_kwh,
            cota_inferior_abierta_kwh=diferencia_kwh - 1,
            cota_superior_abierta_kwh=diferencia_kwh + 1,
            duracion_real_h="",
            duracion_supuesta_h=HORAS_DIA,
            origen="derivado; no_dia_confirmado",
        ))
    return filas
