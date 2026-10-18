"""T03: física horaria corregida de IDEA.md §4, basada en Acuña et al. (2021).

Energías en Wh y potencias en W; paso fijo de 1 h. Eficiencias, límites de
potencia y reparto por identificador son adaptaciones del proyecto, no una
transcripción literal de las ecuaciones (1)–(10) del artículo.
"""

import math

from .configuracion import RESOLUCION_H, numero
from .datos import TOLERANCIA_NUMERICA


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


def _despachar(fv_wh, atendida_wh, baterias, estado):
    """Despacha solo la demanda aceptada; devuelve flujos, estado nuevo y vertido.

    Configuración y estado deben estar validados; atendida_wh debe ser factible.
    Carga únicamente con excedente FV y descarga únicamente para cubrir déficit.
    """
    excedente = max(0., fv_wh - atendida_wh)
    deficit = max(0., atendida_wh - fv_wh)
    flujos, siguiente = [], {}
    for bateria in sorted(baterias, key=lambda b: b["id"]):
        id_ = bateria["id"]
        inicial = estado[id_]
        eta_c, eta_d = bateria["eficiencia_carga"], bateria["eficiencia_descarga"]
        hueco_bus = (bateria["maximo_wh"] - inicial) / eta_c
        disponible_bus = eta_d * (inicial - bateria["minimo_wh"])
        carga = min(excedente, bateria["potencia_carga_max_w"] * RESOLUCION_H, hueco_bus)
        descarga = min(deficit, _limite_descarga(bateria, inicial))
        final = inicial
        # Usar el extremo cercano evita cancelación numérica sin recortar el estado.
        if carga > 0:
            final = (inicial + eta_c * carga if carga <= hueco_bus / 2
                     else bateria["maximo_wh"] - (hueco_bus - carga) * eta_c)
        elif descarga > 0:
            final = (inicial - descarga / eta_d if descarga <= disponible_bus / 2
                     else bateria["minimo_wh"] + (disponible_bus - descarga) / eta_d)
        excedente -= carga
        deficit -= descarga
        siguiente[id_] = final
        flujos.append(dict(bateria_id=id_, inicial_wh=inicial, carga_wh=carga,
                           descarga_wh=descarga, final_wh=final))
        error = final - (inicial + eta_c * carga - descarga / eta_d)
        if (not bateria["minimo_wh"] <= final <= bateria["maximo_wh"]
                or abs(error) > TOLERANCIA_NUMERICA or carga * descarga != 0):
            raise ValueError(f"baterias.{id_}: despacho inconsistente")
    error_bus = math.fsum([fv_wh, -atendida_wh, -excedente,
                          *(f["descarga_wh"] - f["carga_wh"] for f in flujos)])
    if deficit > TOLERANCIA_NUMERICA or abs(error_bus) > TOLERANCIA_NUMERICA:
        raise ValueError("despacho: la energía atendida no conserva el balance del bus")
    return flujos, siguiente, excedente
