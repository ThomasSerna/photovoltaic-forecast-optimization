"""Extensión experimental: preparar, verificar, entrenar y comparar Chronos-2 LoRA.

El entrenamiento exige CUDA y PEFT; nunca se sustituye por CPU o ajuste completo.
No cambia la física ni calibra el MPC. Véase docs/chronos_lora.md.
"""

import argparse
from copy import deepcopy
import gc
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys
from time import perf_counter

import numpy as np
import pandas as pd

from .cli import codigo_sha256, entradas_sha256, sha256_de
from .configuracion import numero
from .datos import cargar_caso
from .datos_lora import PERIODOS_LORA, cargar_datos, preparar_datos, ventanas_validacion
from .pronosticos import CHECKPOINT, cargar_chronos, corregir_cuantiles, pronosticar_chronos


RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "scripts" / "configuracion_lora.json"
MODULOS_LORA = ("self_attention.q", "self_attention.k", "self_attention.v", "self_attention.o",
                "output_patch_embedding.output_layer")


def _leer_json(ruta):
    try:
        valor = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{ruta}: JSON ausente o inválido") from error
    if not isinstance(valor, dict):
        raise ValueError(f"{ruta}: se requiere un objeto JSON")
    return valor


def _guardar_json(ruta, contenido):
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_text(json.dumps(contenido, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                        encoding="utf-8")
    temporal.replace(ruta)


def cargar_opciones(ruta_config=CONFIG):
    ruta = Path(ruta_config).resolve()
    opciones = _leer_json(ruta)
    for campo in ("config_base", "datos", "salida_entrenamiento", "salida_comparacion"):
        if not isinstance(opciones.get(campo), str) or not opciones[campo].strip():
            raise ValueError(f"{campo}: se requiere una ruta no vacía relativa al JSON")
        opciones[campo] = str((ruta.parent / opciones[campo]).resolve())
    ajustes = opciones["entrenamiento"]
    for campo in ("pasos", "batch_size", "acumulacion", "rango", "alpha", "evaluar_cada"):
        numero(ajustes[campo], campo, minimo=1, entero=True)
    numero(ajustes["semilla"], "semilla", entero=True)
    numero(ajustes["tasa_aprendizaje"], "tasa_aprendizaje", minimo=0, maximo=1, estricto=True)
    return opciones


def verificar_entorno():
    """Comprueba la GPU real con una operación pequeña; no carga pesos ni entrena."""
    import torch

    if not torch.cuda.is_available() or torch.version.cuda is None:
        raise RuntimeError("El entrenamiento exige PyTorch CUDA y una GPU NVIDIA; no se admite CPU")
    import peft
    capacidad = torch.cuda.get_device_capability(0)
    arquitectura = f"sm_{capacidad[0]}{capacidad[1]}"
    if arquitectura not in torch.cuda.get_arch_list():
        raise RuntimeError(f"PyTorch no incluye {arquitectura}; instalar el extra entrenamiento en Windows")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("La configuración de entrenamiento exige BF16 compatible")
    prueba = torch.ones((8, 8), dtype=torch.bfloat16, device="cuda")
    _ = prueba @ prueba
    torch.cuda.synchronize()
    return dict(gpu=torch.cuda.get_device_name(0), capacidad=list(capacidad),
                vram_total_bytes=int(torch.cuda.get_device_properties(0).total_memory),
                torch=torch.__version__, cuda=torch.version.cuda, peft=peft.__version__,
                python=platform.python_version(), plataforma=platform.platform())


def crear_callback_lora(registro):
    """Detiene cualquier ajuste de pesos base antes de la primera actualización."""
    from transformers.trainer_callback import TrainerCallback

    class SoloLoRA(TrainerCallback):
        def on_train_begin(self, args, state, control, model=None, **kwargs):
            parametros = list(model.named_parameters())
            entrenables = [(n, p) for n, p in parametros if p.requires_grad]
            if not entrenables or any("lora_" not in n for n, _ in entrenables):
                raise RuntimeError("Solo deben entrenarse los parámetros LoRA; pesos base congelados")
            registro["parametros_entrenables"] = sum(p.numel() for _, p in entrenables)
            registro["parametros_totales"] = sum(p.numel() for _, p in parametros)

        def on_log(self, args, state, control, logs=None, **kwargs):
            registro.setdefault("historial", []).append(dict(paso=state.global_step, **(logs or {})))

        def on_train_end(self, args, state, control, **kwargs):
            registro.update(checkpoint_elegido=state.best_model_checkpoint, perdida_validacion=state.best_metric,
                            pasos_completados=state.global_step)

    return SoloLoRA()


def _config_adaptador(ruta, revision):
    config = _leer_json(Path(ruta) / "adapter_config.json")
    if (config.get("peft_type") != "LORA" or config.get("base_model_name_or_path") != CHECKPOINT
            or config.get("revision") != revision or config.get("bias") != "none"
            or set(config.get("target_modules", [])) != set(MODULOS_LORA)
            or config.get("modules_to_save")):
        raise ValueError("Adaptador: se requiere LoRA puro del checkpoint y revisión oficiales configurados")
    if not (Path(ruta) / "adapter_model.safetensors").is_file():
        raise ValueError("Adaptador: faltan adapter_model.safetensors")
    return config


def cargar_adaptador(ruta, revision, dispositivo="cuda"):
    """La revisión base proviene de adapter_config.json, nunca de la rama main del Hub."""
    if dispositivo not in ("cpu", "cuda"):
        raise ValueError("dispositivo: se admite cpu o cuda")
    _config_adaptador(ruta, revision)
    import torch
    from chronos import Chronos2Pipeline

    return Chronos2Pipeline.from_pretrained(str(ruta), device_map=dispositivo, dtype=torch.float32)


def _trazabilidad(opciones, ruta_config):
    return dict(configuracion_sha256=sha256_de(Path(ruta_config)),
                configuracion_base_sha256=sha256_de(Path(opciones["config_base"])),
                datos_manifiesto_sha256=sha256_de(Path(opciones["datos"]) / "manifiesto.json"),
                uv_lock_sha256=sha256_de(RAIZ / "uv.lock"), codigo_sha256=codigo_sha256())


def _versiones():
    return {nombre: importlib.metadata.version(nombre) for nombre in
            ("chronos-forecasting", "torch", "peft", "transformers", "accelerate", "numpy", "pandas")}


def _verificar_mejor_checkpoint(registro, pasos, adaptador):
    """La recarga final también debe contener los pesos elegidos por validación."""
    perdida = registro.get("perdida_validacion")
    if (registro.get("pasos_completados") != pasos or not registro.get("checkpoint_elegido")
            or not isinstance(perdida, (int, float)) or not math.isfinite(perdida)
            or not 0 < registro.get("parametros_entrenables", 0) < registro.get("parametros_totales", 0)):
        raise RuntimeError("El entrenamiento no acredita pasos completos y selección finita por validación")
    import torch
    from safetensors.torch import load_file

    elegido = load_file(str(Path(registro["checkpoint_elegido"]) / "adapter_model.safetensors"))
    final = load_file(str(adaptador / "adapter_model.safetensors"))
    if not elegido or not final or elegido.keys() != final.keys() or any(
            not torch.equal(elegido[k], final[k]) for k in elegido):
        raise RuntimeError("El adaptador final no contiene los pesos del mejor checkpoint de validación")


def entrenar(ruta_config=CONFIG, salida=None, pasos=None):
    """Entrena únicamente en GPU. Un resumen final acredita guardado y recarga correctos."""
    opciones = cargar_opciones(ruta_config)
    entorno = verificar_entorno()  # antes de descargar pesos o crear salidas
    import torch
    from transformers import set_seed

    caso = cargar_caso(opciones["config_base"])
    historia = cargar_datos(opciones["config_base"], opciones["datos"])
    ajustes = dict(opciones["entrenamiento"])
    if pasos is not None:
        numero(pasos, "pasos", minimo=1, entero=True)
        if pasos < ajustes["pasos"] and salida is None:
            raise ValueError("La prueba corta requiere --salida propia para conservar la ruta del entrenamiento final")
        ajustes["pasos"] = pasos
    contexto = caso.configuracion["pronosticos"]["contexto"]
    horizonte = caso.configuracion["horizonte"]
    periodo = PERIODOS_LORA["entrenamiento"]
    filas = historia.loc[(historia["inicio_utc"] >= pd.Timestamp(periodo["inicio"]))
                         & (historia["fin_utc"] <= pd.Timestamp(periodo["fin"]))]
    if len(filas) < contexto + horizonte:
        raise ValueError("Entrenamiento: no hay una ventana completa de contexto y objetivo")
    validacion = ventanas_validacion(historia, **PERIODOS_LORA["validacion"], contexto=contexto,
                                    horizonte=horizonte, paso=caso.configuracion["replanificacion"])
    trazabilidad = _trazabilidad(opciones, ruta_config)
    salida = Path(salida or opciones["salida_entrenamiento"]).resolve()
    salida.mkdir(parents=True, exist_ok=False)
    registro = {}
    set_seed(ajustes["semilla"])
    torch.cuda.reset_peak_memory_stats()
    inicio = perf_counter()
    revision = caso.configuracion["pronosticos"]["revision"]
    pipeline = cargar_chronos(revision, "cuda")
    torch.cuda.synchronize()
    carga_s = perf_counter() - inicio
    inicio = perf_counter()
    afinado = pipeline.fit(
        inputs=[filas["ghi_w_m2"].to_numpy(dtype=np.float32)], validation_inputs=validacion,
        prediction_length=horizonte, context_length=contexto, min_past=contexto,
        finetune_mode="lora", lora_config=dict(r=ajustes["rango"], lora_alpha=ajustes["alpha"],
            target_modules=list(MODULOS_LORA), bias="none", lora_dropout=0.0, revision=revision),
        learning_rate=ajustes["tasa_aprendizaje"], num_steps=ajustes["pasos"], batch_size=ajustes["batch_size"],
        gradient_accumulation_steps=ajustes["acumulacion"], output_dir=salida,
        callbacks=[crear_callback_lora(registro)], seed=ajustes["semilla"], data_seed=ajustes["semilla"],
        bf16=True, fp16=False, dataloader_num_workers=0, disable_data_parallel=True,
        eval_steps=ajustes["evaluar_cada"], save_steps=ajustes["evaluar_cada"],
        logging_steps=ajustes["evaluar_cada"], save_only_model=True, report_to="none",
    )
    torch.cuda.synchronize()
    entrenamiento_s = perf_counter() - inicio
    memoria = int(torch.cuda.max_memory_allocated())
    adaptador = salida / "finetuned-ckpt"
    _config_adaptador(adaptador, revision)
    _verificar_mejor_checkpoint(registro, ajustes["pasos"], adaptador)
    origen = PERIODOS_LORA["validacion"]["inicio"]
    kwargs = dict(contexto=contexto, horizonte=horizonte, cuantiles=caso.configuracion["pronosticos"]["cuantiles"])
    antes = pronosticar_chronos(afinado, historia, origen, **kwargs)
    del afinado, pipeline
    gc.collect()
    torch.cuda.empty_cache()
    recargado = cargar_adaptador(adaptador, revision)
    despues = pronosticar_chronos(recargado, historia, origen, **kwargs)
    if not np.allclose(antes, despues, rtol=1e-4, atol=1e-3):
        raise RuntimeError("La recarga del adaptador cambia la inferencia fuera de la tolerancia documentada")
    if trazabilidad != _trazabilidad(opciones, ruta_config):
        raise RuntimeError("Las entradas o el código cambiaron durante el entrenamiento")
    informe = dict(estado="ENTRENADO_Y_RECARGADO", checkpoint=CHECKPOINT, revision=revision,
        prueba_corta=ajustes["pasos"] < opciones["entrenamiento"]["pasos"], opciones=opciones,
        entrenamiento=ajustes, periodos=PERIODOS_LORA, horas_entrenamiento=len(filas),
        ventanas_validacion=len(validacion), seleccion="menor eval_loss; solo validación",
        registro=registro, entorno=entorno, versiones=_versiones(), trazabilidad=trazabilidad,
        carga_s=carga_s, entrenamiento_s=entrenamiento_s, vram_maxima_asignada_bytes=memoria,
        recarga=dict(coincide=True, rtol=1e-4, atol_w_m2=1e-3,
                     diferencia_maxima_w_m2=float(np.abs(antes.to_numpy() - despues.to_numpy()).max())),
        adaptador_sha256={p.name: sha256_de(p) for p in sorted(adaptador.iterdir()) if p.is_file()})
    _guardar_json(salida / "resumen.json", informe)
    return informe


def _pronostico_nuevo(pipeline, historia, config, sincronizar):
    """Rolling-origin sobre la reserva: observar pasado del test no actualiza pesos."""
    from .experimento import metricas_pronostico, tabla_pronosticos

    inicio, fin = (pd.Timestamp(PERIODOS_LORA["evaluacion_nueva"][k]) for k in ("inicio", "fin"))
    horizonte = config["horizonte"]
    ultimo = fin - pd.Timedelta(hours=horizonte)
    registros, tiempos = [], []
    for origen in pd.date_range(inicio, ultimo, freq=f"{config['replanificacion']}h"):
        sincronizar()
        reloj = perf_counter()
        crudos = pronosticar_chronos(pipeline, historia, origen, contexto=config["pronosticos"]["contexto"],
                                    horizonte=horizonte, cuantiles=config["pronosticos"]["cuantiles"])
        sincronizar()
        tiempos.append(perf_counter() - reloj)
        corregidos, _ = corregir_cuantiles(crudos, config)
        registros.append(dict(origen_utc=origen.tz_convert("UTC"), crudos=crudos, corregidos=corregidos))
    tabla = tabla_pronosticos(registros, historia.set_index("inicio_utc")["ghi_w_m2"])
    return tabla, dict(pronostico=metricas_pronostico(tabla, config), origenes=len(tiempos),
                       inferencia_media_s=float(np.mean(tiempos)))


def comparar(ruta_config=CONFIG, salida=None, adaptador=None, dispositivo="cuda"):
    """Evalúa el checkpoint ya seleccionado; no llama a fit ni recalibra el controlador."""
    from .controlador import proveedores
    from .experimento import evaluar, exportar, resumir, tabla_ab, verificar_reproducibilidad

    opciones = cargar_opciones(ruta_config)
    caso = cargar_caso(opciones["config_base"])
    caso.configuracion = deepcopy(caso.configuracion)
    caso.configuracion["pronosticos"]["dispositivo"] = dispositivo
    historia = cargar_datos(opciones["config_base"], opciones["datos"])
    adaptador = Path(adaptador or Path(opciones["salida_entrenamiento"]) / "finetuned-ckpt").resolve()
    entrenamiento_sha256 = sha256_de(adaptador.parent / "resumen.json")
    entrenamiento = _leer_json(adaptador.parent / "resumen.json")
    trazabilidad = _trazabilidad(opciones, ruta_config)
    if entrenamiento.get("estado") != "ENTRENADO_Y_RECARGADO" or entrenamiento.get("prueba_corta"):
        raise ValueError("La comparación final requiere un entrenamiento completo y verificado")
    if entrenamiento.get("trazabilidad") != trazabilidad:
        raise ValueError("Código, configuración, datos o lock difieren del entrenamiento; usar su mismo checkout")
    huellas = entrenamiento.get("adaptador_sha256", {})
    if not {"adapter_config.json", "adapter_model.safetensors"} <= set(huellas):
        raise ValueError("El entrenamiento no contiene hashes completos del adaptador")
    for nombre, huella in huellas.items():
        if Path(nombre).name != nombre or sha256_de(adaptador / nombre) != huella:
            raise ValueError("El adaptador difiere del entrenamiento registrado")
    revision = caso.configuracion["pronosticos"]["revision"]
    _config_adaptador(adaptador, revision)
    import torch

    if dispositivo == "cuda":
        verificar_entorno()
    sincronizar = torch.cuda.synchronize if dispositivo == "cuda" else lambda: None
    salida = Path(salida or opciones["salida_comparacion"]).resolve()
    salida.mkdir(parents=True, exist_ok=False)
    entradas = entradas_sha256(caso.rutas)
    base = cargar_chronos(revision, dispositivo)
    afinado = cargar_adaptador(adaptador, revision, dispositivo)
    pronosticadores = proveedores(caso, base)
    parametros = dict(contexto=caso.configuracion["pronosticos"]["contexto"],
                      cuantiles=caso.configuracion["pronosticos"]["cuantiles"])
    pronosticadores["chronos_lora"] = lambda h, o, n: pronosticar_chronos(afinado, h, o, horizonte=n, **parametros)
    evaluacion = evaluar(caso, pronosticadores, sincronizar=sincronizar)
    repeticion = verificar_reproducibilidad(caso, pronosticadores, evaluacion)
    resumen = resumir(caso, evaluacion)
    exportar(salida, caso, evaluacion, resumen, tabla_ab(caso, evaluacion, resumen))
    nuevas, metricas_nuevas = {}, {}
    for nombre, pipeline in (("chronos", base), ("chronos_lora", afinado)):
        nuevas[nombre], metricas_nuevas[nombre] = _pronostico_nuevo(pipeline, historia, caso.configuracion, sincronizar)
    claves = ["origen_utc", "inicio_utc", "anticipacion_h", "ghi_real_w_m2"]
    if not nuevas["chronos"][claves].equals(nuevas["chronos_lora"][claves]):
        raise ValueError("Prueba adicional: base y LoRA no evalúan los mismos objetivos")
    pd.concat([t.assign(proveedor=n) for n, t in nuevas.items()], ignore_index=True).to_csv(
        salida / "pronosticos_reserva.csv", index=False)
    filas = []
    for subconjunto in ("global", "diurno", "diurno_ejecutado"):
        for correccion in ("crudo", "corregido"):
            for metrica in ("mae_w_m2", "rmse_w_m2", "pinball_w_m2", "cobertura"):
                a, b = [metricas_nuevas[n]["pronostico"][subconjunto][correccion][metrica]
                        for n in ("chronos", "chronos_lora")]
                filas.append(dict(subconjunto=subconjunto, correccion=correccion, metrica=metrica,
                                  chronos=a, chronos_lora=b, diferencia_lora_menos_base=b - a))
    tabla_nueva = pd.DataFrame(filas)
    tabla_nueva.to_csv(salida / "tabla_reserva.csv", index=False)
    _graficar_reserva(salida, nuevas, caso.configuracion)
    if trazabilidad != _trazabilidad(opciones, ruta_config) or entradas != entradas_sha256(caso.rutas):
        raise RuntimeError("Las entradas o el código cambiaron durante la comparación")
    if (entrenamiento_sha256 != sha256_de(adaptador.parent / "resumen.json")
            or any(sha256_de(adaptador / n) != h for n, h in huellas.items())):
        raise RuntimeError("El adaptador o el resumen de entrenamiento cambiaron durante la comparación")
    informe = dict(estado="COMPARACION_COMPLETA", checkpoint=CHECKPOINT, revision=revision,
        adaptador=str(adaptador), adaptador_sha256=huellas, entrenamiento_sha256=entrenamiento_sha256,
        dispositivo=dispositivo, precision_inferencia="float32 ambos modelos", periodos=PERIODOS_LORA,
        trazabilidad=trazabilidad, entradas_sha256=entradas, versiones=_versiones(),
        marzo=dict(metricas=resumen["metricas"], diferencias_diarias=resumen["diferencias_diarias"],
                   diferencias_lora=resumen["diferencias_diarias_lora"], verificaciones=evaluacion["pareo"],
                   fisica=evaluacion["fisica"], reproducibilidad=repeticion),
        reserva=dict(metricas=metricas_nuevas, diferencias=filas),
        limites=["Marzo fue inspeccionado previamente: comparación exploratoria.",
                 "La reserva no interviene en selección del checkpoint; no hay demanda simulada en ella.",
                 "ERA5 es reanálisis con disponibilidad idealizada; demanda y sistema son semisintéticos.",
                 "Una mejora predictiva no implica una mejora de servicio ni significancia estadística."])
    texto = "# Chronos-2 base frente a LoRA\n\n## Marzo: pronóstico y energía\n\n"
    texto += (salida / "tabla_lora.md").read_text(encoding="utf-8")
    texto += "\n## Abril–diciembre: solo pronóstico\n\n"
    texto += "| Subconjunto | Cuantiles | Métrica | Base | LoRA | LoRA−base |\n|---|---|---|---:|---:|---:|\n"
    for f in filas:
        texto += (f"| {f['subconjunto']} | {f['correccion']} | {f['metrica']} | {f['chronos']:.6g} | "
                  f"{f['chronos_lora']:.6g} | {f['diferencia_lora_menos_base']:+.6g} |\n")
    texto += "\nMenor MAE/RMSE/pinball es mejor; mayor J/S_pond es mejor. La cobertura se interpreta respecto a 0.8.\n\n"
    texto += "\n".join(f"- {limite}" for limite in informe["limites"]) + "\n"
    (salida / "informe.md").write_text(texto, encoding="utf-8")
    _guardar_json(salida / "resumen.json", informe)
    return informe


def _graficar_reserva(salida, tablas, config):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ejes = plt.subplots(2, 1, figsize=(12, 6), sharex=True, sharey=True)
    for eje, (nombre, tabla) in zip(ejes, tablas.items()):
        tabla = tabla[tabla["anticipacion_h"] <= config["replanificacion"]].iloc[:7 * 24]
        fechas = pd.DatetimeIndex(tabla["inicio_utc"]).tz_convert(config["zona_horaria"])
        eje.plot(fechas, tabla["ghi_real_w_m2"], color="black", label="ERA5")
        eje.plot(fechas, tabla["q0.5"], label=nombre)
        eje.fill_between(fechas, tabla["q0.1"], tabla["q0.9"], alpha=0.2, label="Cuantiles 0.1–0.9")
        eje.set_ylabel("GHI (W/m²)")
        eje.legend()
    ejes[0].set_title("Reserva: primera semana de abril; h ≤ 6 de cada origen")
    ejes[-1].set_xlabel("Hora local (America/Bogota)")
    fig.tight_layout()
    fig.savefig(salida / "reserva_irradiancia.png", dpi=150)
    plt.close(fig)


def main():
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accion", choices=("preparar", "verificar", "entrenar", "comparar"))
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--salida", type=Path, help="Carpeta nueva; no sobrescribe resultados")
    parser.add_argument("--adaptador", type=Path)
    parser.add_argument("--pasos", type=int, help="Prueba corta: usar --pasos 2 y --salida independiente")
    parser.add_argument("--dispositivo", choices=("cpu", "cuda"), default="cuda", help="Solo comparación")
    args = parser.parse_args()
    try:
        opciones = cargar_opciones(args.config)
        if args.accion == "preparar":
            informe = preparar_datos(opciones["config_base"], args.salida or opciones["datos"])
        elif args.accion == "verificar":
            informe = verificar_entorno()
            historia = cargar_datos(opciones["config_base"], opciones["datos"])
            informe["horas_datos_validadas"] = len(historia)
        elif args.accion == "entrenar":
            informe = entrenar(args.config, args.salida, args.pasos)
        else:
            informe = comparar(args.config, args.salida, args.adaptador, args.dispositivo)
    except (OSError, ValueError, RuntimeError, ImportError, KeyError, TypeError) as error:
        parser.exit(1, f"Error: {error}\nSin resumen.json final, cualquier salida de esta ejecución está incompleta.\n")
    print(json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
