"""T02: Chronos-2 y correcciones comunes de GHI media horaria (W/m²).

Adaptación de IDEA.md §5; no implementa el despacho ni las ecuaciones (1)–(10)
de Acuña et al. (2021). El pipeline se carga una vez y se recibe explícitamente.
"""

import re

import numpy as np
import pandas as pd

from .configuracion import numero
from .datos import historial_admisible

CHECKPOINT = "amazon/chronos-2"
CUANTILES = tuple(i / 10 for i in range(1, 10))


def validar_cuantiles(cuantiles):
    """Niveles crecientes dentro del rango nativo; 0.5 identifica la mediana."""
    if not isinstance(cuantiles, (list, tuple)) or not cuantiles:
        raise ValueError("cuantiles: se requiere una lista no vacía")
    for q in cuantiles:
        numero(q, "cuantiles", minimo=0.01, maximo=0.99)
    if any(a >= b for a, b in zip(cuantiles, cuantiles[1:])) or 0.5 not in cuantiles:
        raise ValueError("cuantiles: deben ser únicos, crecientes e incluir 0.5")


def cargar_chronos(revision, dispositivo="cpu"):
    """Carga el checkpoint oficial fijado a un commit, en FP32 y sin entrenamiento."""
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("revision: se requiere un SHA completo de 40 caracteres")
    if dispositivo not in ("cpu", "cuda"):
        raise ValueError("dispositivo: se admite cpu o cuda, sin cambio automático")
    import torch
    from chronos import Chronos2Pipeline

    return Chronos2Pipeline.from_pretrained(
        CHECKPOINT, revision=revision, device_map=dispositivo, dtype=torch.float32,
    )


def contexto_admisible(historico, origen, contexto):
    """Exige C intervalos consecutivos [origen-C h, origen), terminados y disponibles."""
    numero(contexto, "contexto", minimo=1, maximo=8192, entero=True)
    columnas = {"inicio_utc", "fin_utc", "available_at", "ghi_w_m2"}
    if not columnas <= set(historico.columns):
        raise ValueError("histórico: faltan columnas solares; usar cargar_caso")
    admisible = historial_admisible(historico, origen)
    origen = pd.Timestamp(origen).tz_convert("UTC")
    if origen != origen.floor("h"):
        raise ValueError("origen: debe coincidir con el comienzo de una hora")
    inicio = origen - pd.Timedelta(hours=contexto)
    ventana = admisible.loc[(admisible["inicio_utc"] >= inicio) & (admisible["inicio_utc"] < origen)]
    esperadas = pd.date_range(inicio, periods=contexto, freq="h", name="inicio_utc")
    if (not pd.DatetimeIndex(ventana["inicio_utc"]).equals(esperadas)
            or not pd.DatetimeIndex(ventana["fin_utc"]).equals(esperadas + pd.Timedelta(hours=1))):
        raise ValueError("contexto: se requieren todas las horas consecutivas y disponibles hasta el origen")
    valores = ventana["ghi_w_m2"].to_numpy(dtype=float)
    if not np.isfinite(valores).all() or (valores < 0).any():
        raise ValueError("contexto: ghi_w_m2 debe ser finita y no negativa")
    return ventana.copy()


def pronosticar_chronos(pipeline, historico, origen, *, contexto=336, horizonte=24, cuantiles=CUANTILES):
    """Cuantiles crudos H×Q, índice inicio_utc; el primer intervalo empieza en origen."""
    numero(horizonte, "horizonte", minimo=1, maximo=1024, entero=True)
    validar_cuantiles(cuantiles)
    ventana = contexto_admisible(historico, origen, contexto)
    valores = ventana["ghi_w_m2"].to_numpy(dtype=np.float32)
    predicciones, _ = pipeline.predict_quantiles(
        inputs=[valores], prediction_length=horizonte, quantile_levels=list(cuantiles),
        context_length=contexto, batch_size=1,
    )
    if len(predicciones) != 1:
        raise ValueError("Chronos-2: se esperaba una sola serie")
    salida = predicciones[0]
    if hasattr(salida, "detach"):
        salida = salida.detach().cpu().numpy()
    salida = np.asarray(salida)
    if salida.shape != (1, horizonte, len(cuantiles)) or not np.isfinite(salida).all():
        raise ValueError("Chronos-2: cuantiles no finitos o forma distinta de (1, H, Q)")
    indice = pd.date_range(pd.Timestamp(origen).tz_convert("UTC"), periods=horizonte, freq="h", name="inicio_utc")
    return pd.DataFrame(salida[0], index=indice, columns=cuantiles)


def mascara_noche(indice, configuracion):
    """True solo si [inicio, inicio+1 h) está completamente fuera de amanecer–ocaso.

    Supuesto astronómico: horizonte sin obstáculos, observador a nivel del suelo.
    Los intervalos que cruzan amanecer u ocaso conservan su irradiancia media.
    """
    from astral import Observer
    from astral.sun import sunrise, sunset

    if not isinstance(indice, pd.DatetimeIndex) or indice.tz is None or indice.hasnans:
        raise ValueError("inicio_utc: se requiere un índice de fechas válidas con zona horaria")
    locales = indice.tz_convert(configuracion["zona_horaria"])
    observador = Observer(configuracion["latitud"], configuracion["longitud"])
    fechas = {}
    for fecha in set(locales.date):
        fechas[fecha] = (sunrise(observador, fecha, locales.tz), sunset(observador, fecha, locales.tz))
    return np.array([
        inicio + pd.Timedelta(hours=1) <= fechas[inicio.date()][0] or inicio >= fechas[inicio.date()][1]
        for inicio in locales
    ], dtype=bool)


def corregir_cuantiles(tabla, configuracion):
    """Reordenamiento, no negatividad y noche, comunes a Chronos y la futura beta.

    No recorta máximos: 1000 W/m² es referencia STC, no un techo de irradiancia.
    Devuelve una copia y los conteos de las correcciones aplicadas.
    """
    validar_cuantiles(list(tabla.columns))
    crudos = tabla.to_numpy(dtype=float)
    if tabla.empty or not np.isfinite(crudos).all():
        raise ValueError("cuantiles: se requiere una tabla no vacía de valores finitos")
    noche = mascara_noche(tabla.index, configuracion)
    corregidos = np.maximum(np.sort(crudos, axis=1), 0)
    nocturnos = np.count_nonzero(corregidos[noche])
    corregidos[noche] = 0
    diagnostico = {
        "filas_cruzadas": int((np.diff(crudos, axis=1) < 0).any(axis=1).sum()),
        "valores_negativos": int((crudos < 0).sum()),
        "valores_nocturnos_corregidos": int(nocturnos),
        "valores_corregidos": int(np.count_nonzero(crudos != corregidos)),
    }
    return pd.DataFrame(corregidos, index=tabla.index, columns=tabla.columns), diagnostico
