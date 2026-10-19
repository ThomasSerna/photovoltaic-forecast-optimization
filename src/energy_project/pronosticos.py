"""Chronos-2, referencia beta y correcciones comunes de GHI media horaria (W/m²).

Adaptación de IDEA.md §5; no implementa el despacho ni las ecuaciones (1)–(10)
de Acuña et al. (2021). El pipeline se carga una vez y se recibe explícitamente.
"""

import re
import warnings
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def pronosticar_beta(historico, origen, *, contexto=336, horizonte=24, cuantiles=CUANTILES,
                    zona_horaria="America/Bogota"):
    """Mezcla por hora local de masa cero y beta positiva escalada (IDEA.md §5).

    Dos formas por MLE, loc=0 y escala=1.05×máximo positivo de la ventana común.
    Ese soporte estadístico no es un límite físico de GHI. Los positivos constantes
    son masas puntuales; no se cambia de familia si falla un ajuste.
    Devuelve cuantiles crudos H×Q: aplicar después la corrección común de noche.
    """
    from scipy.stats import beta

    numero(horizonte, "horizonte", minimo=1, maximo=1024, entero=True)
    validar_cuantiles(cuantiles)
    ventana = contexto_admisible(historico, origen, contexto)
    try:
        zona = ZoneInfo(zona_horaria)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("zona_horaria: se requiere una zona horaria válida") from error
    origen = pd.Timestamp(origen).tz_convert("UTC")
    indice = pd.date_range(origen, periods=horizonte, freq="h", name="inicio_utc")
    horas = ventana["inicio_utc"].dt.tz_convert(zona).dt.hour
    futuras = indice.tz_convert(zona).hour
    niveles = np.asarray(cuantiles, dtype=float)
    salida = np.zeros((horizonte, len(cuantiles)))
    for hora in sorted(set(futuras)):
        valores = ventana.loc[horas == hora, "ghi_w_m2"].to_numpy(dtype=float)
        if not len(valores):
            raise ValueError(f"beta: origen {origen.isoformat()}, hora local {hora}: sin observaciones")
        positivos = valores[valores > 0]
        p0 = np.count_nonzero(valores == 0) / len(valores)
        superiores = niveles > p0
        fila = np.zeros(len(cuantiles))
        if len(positivos) and np.all(positivos == positivos[0]):
            fila[superiores] = positivos[0]
        elif len(positivos):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    parametros = np.asarray(beta.fit(positivos, floc=0, fscale=1.05 * positivos.max()))
                    if (parametros.shape != (4,) or not np.isfinite(parametros).all()
                            or (parametros[:2] <= 0).any() or parametros[2] != 0 or parametros[3] <= 0):
                        raise ValueError("parámetros beta inválidos")
                    condicionados = beta.ppf((niveles[superiores] - p0) / (1 - p0), *parametros)
                    if (not np.isfinite(condicionados).all() or (condicionados < 0).any()
                            or (np.diff(condicionados) < 0).any()):
                        raise ValueError("cuantiles beta inválidos")
                    fila[superiores] = condicionados
            except (ValueError, RuntimeError, ArithmeticError, Warning) as error:
                raise ValueError(f"beta: origen {origen.isoformat()}, hora local {hora}: {error}") from error
        salida[futuras == hora] = fila
    return pd.DataFrame(salida, index=indice, columns=cuantiles)


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
    """Reordenamiento, no negatividad y noche, comunes a Chronos y beta.

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




def _metricas_pronostico(pronostico, real, diurno):
    """Errores puntuales de la mediana; ambos orígenes cuentan por separado."""
    error = np.asarray(pronostico) - np.asarray(real)
    resultado = {}
    for nombre, mascara in (("global", np.ones(len(error), dtype=bool)), ("diurno", diurno)):
        errores = error[mascara]
        resultado[nombre] = {
            "horas": len(errores),
            "mae_w_m2": float(np.abs(errores).mean()) if len(errores) else None,
            "rmse_w_m2": float(np.sqrt(np.mean(errores ** 2))) if len(errores) else None,
        }
    return resultado


def _graficar_pronostico(tabla, real, salida):
    """Primer horizonte: observado, mediana y banda entre cuantiles extremos."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter

    indice = tabla.index.tz_convert("America/Bogota")
    figura, eje = plt.subplots(figsize=(10, 4))
    eje.fill_between(indice, tabla.iloc[:, 0], tabla.iloc[:, -1], alpha=0.2,
                     label=f"Cuantiles {tabla.columns[0]:g}–{tabla.columns[-1]:g}")
    eje.plot(indice, tabla[0.5], label="Chronos-2: mediana")
    eje.plot(indice, real, label="ERA5: referencia")
    eje.set(xlabel="Hora local (America/Bogota)", ylabel="GHI media horaria (W/m²)")
    eje.xaxis.set_major_formatter(DateFormatter("%d/%m %H:%M", tz=indice.tz))
    eje.legend()
    figura.autofmt_xdate()
    figura.tight_layout()
    figura.savefig(salida, dpi=150)
    plt.close(figura)


def ensayo_chronos(caso, salida):
    """Dos orígenes de desarrollo y repetición del primero; no ejecuta el test final."""
    import importlib.metadata
    import json
    import os
    from pathlib import Path
    import platform
    from time import perf_counter

    import psutil

    from .cli import codigo_sha256, entradas_sha256, entorno, sha256_de

    config = caso.configuracion
    opciones = config["pronosticos"]
    salida = Path(salida).resolve()
    if any(salida == ruta.resolve() for ruta in caso.rutas.values()):
        raise ValueError("salida: no puede reemplazar una entrada")
    salida.mkdir(parents=True, exist_ok=False)
    numero(config["horizonte"], "horizonte", minimo=1, maximo=1024, entero=True)
    validar_cuantiles(opciones["cuantiles"])
    origen = pd.Timestamp(config["periodos"]["desarrollo"]["inicio"]).tz_convert("UTC")
    origenes = [origen, origen + pd.Timedelta(hours=config["replanificacion"])]
    if origenes[-1] + pd.Timedelta(hours=config["horizonte"]) > pd.Timestamp(config["periodos"]["desarrollo"]["fin"]):
        raise ValueError("horizonte: el ensayo debe permanecer dentro de desarrollo")
    contextos = [contexto_admisible(caso.irradiancia_historica, actual, opciones["contexto"]) for actual in origenes]
    proceso = psutil.Process()
    memoria = [proceso.memory_info().rss]
    inicio = perf_counter()
    pipeline = cargar_chronos(opciones["revision"], opciones["dispositivo"])
    carga_s = perf_counter() - inicio
    memoria.append(proceso.memory_info().rss)
    crudos, corregidos, reales, diurnos, filas, ejecuciones = [], [], [], [], [], []
    for actual in origenes + [origen]:
        inicio = perf_counter()
        crudo = pronosticar_chronos(
            pipeline, caso.irradiancia_historica, actual, contexto=opciones["contexto"],
            horizonte=config["horizonte"], cuantiles=opciones["cuantiles"],
        )
        duracion = perf_counter() - inicio
        memoria.append(proceso.memory_info().rss)
        if not np.isfinite(crudo.to_numpy()).all():
            raise ValueError("Chronos-2: la inferencia contiene valores no finitos")
        if len(crudos) == 2:
            repetible = crudo.index.equals(crudos[0].index) and crudo.columns.equals(crudos[0].columns)
            if not repetible or not np.allclose(crudo, crudos[0], rtol=1e-5, atol=1e-6):
                raise ValueError("Chronos-2: la repetición del primer origen no coincide")
            repeticion_s = duracion
            break
        corregido, correcciones = corregir_cuantiles(crudo, config)
        real = caso.irradiancia_simulacion.set_index("inicio_utc")["ghi_w_m2"].reindex(crudo.index)
        if not np.isfinite(real.to_numpy()).all():
            raise ValueError("referencia: falta irradiancia realizada para el horizonte")
        diurno = ~mascara_noche(crudo.index, config)
        contexto = contextos[len(crudos)]
        ejecuciones.append({
            "origen_utc": actual.isoformat(), "inferencia_s": duracion,
            "contexto_horas": len(contexto),
            "contexto_primero_utc": contexto["inicio_utc"].iloc[0].isoformat(),
            "contexto_ultimo_fin_utc": contexto["fin_utc"].iloc[-1].isoformat(),
            "correcciones": correcciones,
            "rango_crudo_w_m2": [float(crudo.to_numpy().min()), float(crudo.to_numpy().max())],
            "rango_corregido_w_m2": [float(corregido.to_numpy().min()), float(corregido.to_numpy().max())],
        })
        tabla = pd.DataFrame({"origen_utc": actual.isoformat(), "inicio_utc": crudo.index,
                              "fin_utc": crudo.index + pd.Timedelta(hours=1),
                              "ghi_real_w_m2": real.to_numpy()})
        for cuantil in crudo.columns:
            tabla[f"crudo_q{cuantil}"] = crudo[cuantil].to_numpy()
            tabla[f"q{cuantil}"] = corregido[cuantil].to_numpy()
        filas.append(tabla)
        crudos.append(crudo)
        corregidos.append(corregido)
        reales.append(real.to_numpy())
        diurnos.append(diurno)
    versiones = entorno()
    for paquete in ("chronos-forecasting", "torch", "transformers", "huggingface-hub",
                    "numpy", "astral", "matplotlib", "psutil"):
        versiones[paquete] = importlib.metadata.version(paquete)
    resumen = {
        "alcance": "ensayo de desarrollo; métricas descriptivas con horizontes solapados, no backtest final",
        "checkpoint": "amazon/chronos-2", "revision": opciones["revision"],
        "contexto": opciones["contexto"], "horizonte": config["horizonte"],
        "cuantiles": opciones["cuantiles"], "dispositivo": str(pipeline.model.device),
        "configuracion": {campo: config[campo] for campo in (
            "sitio", "latitud", "longitud", "zona_horaria", "horizonte", "replanificacion", "pronosticos")},
        "dtype": str(pipeline.model.dtype), "carga_s": carga_s,
        "rss_muestras_bytes": memoria, "rss_max_observado_bytes": max(memoria),
        "ejecuciones": ejecuciones,
        "repeticion": {"coincide": True, "inferencia_s": repeticion_s, "rtol": 1e-5, "atol": 1e-6},
        "metricas": {
            nombre: _metricas_pronostico(pd.concat(tablas)[0.5].to_numpy(),
                                         np.concatenate(reales), np.concatenate(diurnos))
            for nombre, tablas in (("crudo", crudos), ("corregido", corregidos))
        },
        "entradas_sha256": entradas_sha256(caso.rutas), "codigo_sha256": codigo_sha256(),
        "entorno": versiones,
        "uv_lock_sha256": sha256_de(Path(__file__).resolve().parents[2] / "uv.lock"),
        "hardware": {"arquitectura": platform.machine(), "procesador": platform.processor(),
                     "cpu_logicos": os.cpu_count(), "ram_total_bytes": psutil.virtual_memory().total},
        "supuestos": ["ERA5: reanálisis, disponible idealmente al cierre de la hora",
                      "Chronos-2 zero-shot; no se garantiza ausencia de datos semejantes en preentrenamiento",
                      "Astral: horizonte sin obstáculos; cero solo en intervalos completamente nocturnos; sin techo GHI",
                      "carga_s puede incluir descarga inicial de pesos; inferencia_s incluye preparación del contexto",
                      "RSS muestreada entre operaciones; no es el pico continuo de memoria"],
    }
    pd.concat(filas, ignore_index=True).to_csv(salida / "pronosticos.csv", index=False)
    _graficar_pronostico(corregidos[0], reales[0], salida / "pronostico.png")
    (salida / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2,
                                                   allow_nan=False) + "\n", encoding="utf-8")
    return resumen


def main():
    import argparse
    from pathlib import Path

    from .datos import cargar_caso

    parser = argparse.ArgumentParser(description="Ensayo reproducible de Chronos-2 con dos orígenes de desarrollo")
    parser.add_argument("--config", type=Path, default=Path("scripts/configuracion.json"))
    parser.add_argument("--salida", type=Path, default=Path("results/chronos_t02"))
    args = parser.parse_args()
    try:
        ensayo_chronos(cargar_caso(args.config), args.salida)
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\nLa salida {args.salida} puede estar incompleta; no es un resultado válido.\n")
    print(args.salida.resolve())


if __name__ == "__main__":
    main()
