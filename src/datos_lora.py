"""Datos horarios trazables para el experimento LoRA; nunca entrena ni modifica T01."""

import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from .configuracion import cargar_configuracion, numero
from .datos import COLUMNAS_SOLARES, leer_csv, validar_solar
from .descarga import descargar_irradiancia
from .pronosticos import contexto_admisible


ANIOS = tuple(range(2015, 2024))
PERIODOS_LORA = {
    "entrenamiento": {"inicio": "2015-01-01T00:00:00-05:00", "fin": "2025-01-01T00:00:00-05:00"},
    "validacion": {"inicio": "2025-01-06T00:00:00-05:00", "fin": "2025-03-03T00:00:00-05:00"},
    "evaluacion": {"inicio": "2025-03-03T00:00:00-05:00", "fin": "2025-03-31T00:00:00-05:00"},
    "evaluacion_nueva": {"inicio": "2025-04-01T00:00:00-05:00", "fin": "2026-01-01T00:00:00-05:00"},
}
ARCHIVOS_ANIO = ("consulta.json", "respuesta.json.gz", "irradiancia_historica.csv.gz")
COLUMNAS = ["inicio_utc", "fin_utc", "available_at", "ghi_w_m2"]
NORMALIZACION_SHA256 = "JSON/CSV: CRLF a LF antes de SHA256; gzip: bytes exactos; originales: bytes exactos"


def _sha256(ruta):
    contenido = ruta.read_bytes()
    if ruta.suffix in (".json", ".csv"):
        contenido = contenido.replace(b"\r\n", b"\n")
    return hashlib.sha256(contenido).hexdigest()


def _json(ruta):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{ruta}: JSON ausente o inválido") from error


def _guardar_json(ruta, contenido):
    ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _configuracion(config_base):
    ruta = Path(config_base).resolve()
    config = cargar_configuracion(ruta)
    if config["zona_horaria"] != "America/Bogota":
        raise ValueError("LoRA: los periodos publicados requieren America/Bogota")
    for nombre in ("desarrollo", "evaluacion"):
        esperado = PERIODOS_LORA["validacion" if nombre == "desarrollo" else nombre]
        if config["periodos"][nombre] != esperado:
            raise ValueError(f"LoRA: periodos.{nombre} difiere del experimento publicado")
    ampliada = deepcopy(config)
    ampliada["periodos"]["historial_previo"]["inicio"] = PERIODOS_LORA["entrenamiento"]["inicio"]
    ampliada["datos"] = {nombre: str((ruta.parent / valor).resolve()) for nombre, valor in config["datos"].items()}
    return ruta, config, ampliada


def _verificar_hashes(carpeta, hashes, nombres):
    if not isinstance(hashes, dict) or set(hashes) != set(nombres):
        raise ValueError(f"{carpeta}: manifiesto de hashes incompleto")
    for nombre, esperado in hashes.items():
        ruta = carpeta / nombre
        if not ruta.is_file() or _sha256(ruta) != esperado:
            raise ValueError(f"{ruta}: SHA256 no coincide o archivo ausente")


def _leer_anio(carpeta, anio, config):
    metadatos = _json(carpeta / "metadatos.json")
    _verificar_hashes(carpeta, metadatos.get("sha256"), ARCHIVOS_ANIO)
    inicio, fin = (f"{valor}-01-01T00:00:00-05:00" for valor in (anio, anio + 1))
    if (metadatos.get("inicio_intervalo") != inicio or metadatos.get("fin_exclusivo") != fin
            or metadatos.get("coordenadas_solicitadas") != {"latitud": config["latitud"], "longitud": config["longitud"]}):
        raise ValueError(f"{carpeta}: adquisición de otra ventana o ubicación")
    ruta = carpeta / "irradiancia_historica.csv.gz"
    tabla = leer_csv(ruta, COLUMNAS_SOLARES)
    validar_solar(tabla, ruta, config, inicio, fin)
    return tabla[COLUMNAS]


def _adquirir_anio(ruta_config, config, salida, anio):
    carpeta = salida / str(anio)
    if carpeta.exists():
        _leer_anio(carpeta, anio, config)
        return
    with tempfile.TemporaryDirectory(prefix=f".adquisicion_{anio}_", dir=salida) as temporal:
        temporal = Path(temporal)
        adquisicion = descargar_irradiancia(
            ruta_config, temporal / "original", f"{anio}-01-01", f"{anio + 1}-01-01")
        publicada = temporal / "publicar"
        publicada.mkdir()
        (publicada / "consulta.json").write_bytes((adquisicion / "consulta.json").read_bytes())
        for nombre in ("respuesta.json", "irradiancia_historica.csv"):
            (publicada / (nombre + ".gz")).write_bytes(gzip.compress((adquisicion / nombre).read_bytes(), mtime=0))
        meta = _json(adquisicion / "metadatos.json")
        meta["sha256_originales"] = meta.pop("sha256")
        meta["sha256"] = {nombre: _sha256(publicada / nombre) for nombre in ARCHIVOS_ANIO}
        meta["normalizacion_sha256"] = NORMALIZACION_SHA256
        _guardar_json(publicada / "metadatos.json", meta)
        _leer_anio(publicada, anio, config)
        publicada.rename(carpeta)


def _historia(config_base, carpeta):
    ruta, config, ampliada = _configuracion(config_base)
    tablas = [_leer_anio(carpeta / str(anio), anio, ampliada) for anio in ANIOS]
    fuente = (ruta.parent / config["datos"]["irradiancia_historica"]).resolve()
    publicado = leer_csv(fuente, COLUMNAS_SOLARES)
    validar_solar(publicado, fuente, config, config["periodos"]["historial_previo"]["inicio"],
                  config["periodos"]["reserva_no_usada"]["fin"])
    tablas.append(publicado[COLUMNAS])
    historia = pd.concat(tablas, ignore_index=True)
    esperado = pd.date_range(PERIODOS_LORA["entrenamiento"]["inicio"],
                             PERIODOS_LORA["evaluacion_nueva"]["fin"], freq="h", inclusive="left").tz_convert("UTC")
    if (not pd.DatetimeIndex(historia["inicio_utc"]).equals(esperado)
            or not historia["available_at"].eq(historia["fin_utc"]).all()):
        raise ValueError("LoRA: histórico completo con huecos, solapamientos o disponibilidad incompatible")
    return historia, fuente


def preparar_datos(config_base=Path("scripts/configuracion.json"), salida=Path("data/chronos_lora")):
    """Descarga 2015–2023 por año y verifica el snapshot publicado 2024–2025.

    Reutiliza únicamente adquisiciones completas cuyos hashes coinciden; una
    carpeta existente dañada se rechaza sin sustituir sus archivos.
    """
    ruta, config, ampliada = _configuracion(config_base)
    salida = Path(salida).resolve()
    salida.mkdir(parents=True, exist_ok=True)
    manifiesto = salida / "manifiesto.json"
    if manifiesto.exists():
        cargar_datos(ruta, salida)
        return _json(manifiesto)
    with tempfile.TemporaryDirectory(prefix="chronos_lora_config_") as temporal:
        configuracion = Path(temporal) / "configuracion.json"
        _guardar_json(configuracion, ampliada)
        for anio in ANIOS:
            _adquirir_anio(configuracion, ampliada, salida, anio)
    historia, fuente = _historia(ruta, salida)
    nombres = [f"{anio}/{nombre}" for anio in ANIOS for nombre in (*ARCHIVOS_ANIO, "metadatos.json")]
    contenido = {
        "version": 1,
        "fuente": "Open-Meteo/ERA5; reanálisis, no sensor local",
        "coordenadas": {"latitud": config["latitud"], "longitud": config["longitud"]},
        "unidad": "W/m²", "resolucion_h": 1, "zona_horaria": config["zona_horaria"],
        "conversion_temporal": "CSV ya alineado al inicio de intervalo; no desplazar nuevamente",
        "normalizacion_sha256": NORMALIZACION_SHA256,
        "disponibilidad": "fin_intervalo_idealizada, no disponibilidad operativa de ERA5",
        "periodos": deepcopy(PERIODOS_LORA),
        "horas": len(historia),
        "horas_por_periodo": {nombre: int(((historia.inicio_utc >= pd.Timestamp(periodo["inicio"]))
                                          & (historia.fin_utc <= pd.Timestamp(periodo["fin"]))).sum())
                               for nombre, periodo in PERIODOS_LORA.items()},
        "base": {"configuracion_sha256": _sha256(ruta), "historico_sha256": _sha256(fuente)},
        "sha256": {nombre: _sha256(salida / nombre) for nombre in nombres},
        "notas": ["Entrenamiento termina antes de 2025; validación no actualiza pesos.",
                  "Marzo ya fue publicado: contraste exploratorio.",
                  "Abril–diciembre es holdout temporal nuevo para pronóstico, sin demanda energética sintética.",
                  "No se garantiza ausencia de datos semejantes en el preentrenamiento de Chronos-2."],
    }
    _guardar_json(manifiesto, contenido)
    return contenido


def cargar_datos(config_base, carpeta):
    """Carga y verifica hashes, fuente, unidades y rejilla horaria; no descarga."""
    carpeta = Path(carpeta).resolve()
    ruta, _, _ = _configuracion(config_base)
    manifiesto = _json(carpeta / "manifiesto.json")
    if manifiesto.get("version") != 1 or manifiesto.get("periodos") != PERIODOS_LORA:
        raise ValueError("LoRA: versión o periodos del manifiesto incompatibles")
    nombres = [f"{anio}/{nombre}" for anio in ANIOS for nombre in (*ARCHIVOS_ANIO, "metadatos.json")]
    _verificar_hashes(carpeta, manifiesto.get("sha256"), nombres)
    historia, fuente = _historia(ruta, carpeta)
    base = {"configuracion_sha256": _sha256(ruta), "historico_sha256": _sha256(fuente)}
    if manifiesto.get("base") != base or manifiesto.get("horas") != len(historia):
        raise ValueError("LoRA: cambió el snapshot publicado o su configuración")
    return historia


def ventanas_validacion(historia, inicio, fin, contexto=336, horizonte=24, paso=6):
    """Ventanas C+H float32: contexto pasado y etiquetas enteramente en [inicio, fin)."""
    for nombre, valor, maximo in (("contexto", contexto, 8192), ("horizonte", horizonte, 1024), ("paso", paso, 1024)):
        numero(valor, nombre, minimo=1, maximo=maximo, entero=True)
    inicio, fin = pd.Timestamp(inicio), pd.Timestamp(fin)
    if (inicio.tzinfo is None or fin.tzinfo is None or inicio >= fin
            or inicio != inicio.floor("h") or fin != fin.floor("h")):
        raise ValueError("validación: límites horarios con zona e inicio anterior al fin requeridos")
    ventanas = []
    ultimo = fin - pd.Timedelta(hours=horizonte)
    for origen in pd.date_range(inicio, ultimo, freq=f"{paso}h"):
        contexto_pasado = contexto_admisible(historia, origen, contexto)
        futuro = historia[(historia["inicio_utc"] >= origen)
                          & (historia["inicio_utc"] < origen + pd.Timedelta(hours=horizonte))]
        esperados = pd.date_range(origen, periods=horizonte, freq="h").tz_convert("UTC")
        if (not pd.DatetimeIndex(futuro["inicio_utc"]).equals(esperados)
                or not pd.DatetimeIndex(futuro["fin_utc"]).equals(esperados + pd.Timedelta(hours=1))):
            raise ValueError("validación: etiquetas con huecos, duplicados o intervalos inválidos")
        valores = np.concatenate((contexto_pasado.ghi_w_m2.to_numpy(), futuro.ghi_w_m2.to_numpy())).astype(np.float32)
        if not np.isfinite(valores).all() or (valores < 0).any():
            raise ValueError("validación: irradiancia no finita o negativa")
        ventanas.append(valores)
    if not ventanas:
        raise ValueError("validación: periodo sin horizontes completos")
    return ventanas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("scripts/configuracion.json"))
    parser.add_argument("--salida", type=Path, default=Path("data/chronos_lora"))
    args = parser.parse_args()
    try:
        manifiesto = preparar_datos(args.config, args.salida)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Datos LoRA preparados: {manifiesto['horas']} horas; {args.salida / 'manifiesto.json'}")


if __name__ == "__main__":
    main()
