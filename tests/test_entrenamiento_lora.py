"""Guardas de GPU y de pesos entrenables, sin descargar ni entrenar modelos."""

import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import numpy as np
import pandas as pd
import pytest


def entorno_falso(monkeypatch, *, disponible=True, arquitecturas=None, bf16=True):
    torch = MagicMock()
    torch.__version__ = "2.14.1+cu130"
    torch.version.cuda = "13.0"
    torch.cuda.is_available.return_value = disponible
    torch.cuda.get_device_name.return_value = "NVIDIA GeForce RTX 5070"
    torch.cuda.get_device_capability.return_value = (12, 0)
    torch.cuda.get_arch_list.return_value = arquitecturas or ["sm_120"]
    torch.cuda.is_bf16_supported.return_value = bf16
    torch.cuda.get_device_properties.return_value = SimpleNamespace(total_memory=12 * 1024**3)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "peft", SimpleNamespace(__version__="0.21.2"))
    return torch


@pytest.mark.parametrize("opciones", [
    {"disponible": False},
    {"arquitecturas": ["sm_86"]},
    {"bf16": False},
])
def test_verificar_entorno_rechaza_gpu_inutilizable(monkeypatch, opciones):
    from src.ajuste_lora import verificar_entorno

    entorno_falso(monkeypatch, **opciones)
    with pytest.raises((ValueError, RuntimeError)):
        verificar_entorno()


def test_verificar_entorno_exige_peft_sin_fallback(monkeypatch):
    from src.ajuste_lora import verificar_entorno

    entorno_falso(monkeypatch)
    monkeypatch.setitem(sys.modules, "peft", None)
    with pytest.raises((ValueError, RuntimeError, ImportError)):
        verificar_entorno()


def test_verificar_entorno_acepta_rtx5070(monkeypatch):
    from src.ajuste_lora import verificar_entorno

    entorno_falso(monkeypatch)
    resultado = verificar_entorno()
    assert isinstance(resultado, dict)
    assert "RTX 5070" in str(resultado)


class ModeloFalso:
    def __init__(self, *, base_entrenable=False, adaptador_entrenable=True):
        self.pesos = [
            ("base_model.model.encoder.q.weight", base_entrenable, 100),
            ("base_model.model.encoder.q.lora_A.default.weight", adaptador_entrenable, 8),
            ("base_model.model.encoder.q.lora_B.default.weight", adaptador_entrenable, 8),
        ]

    def named_parameters(self):
        for nombre, entrenable, cantidad in self.pesos:
            yield nombre, SimpleNamespace(requires_grad=entrenable, numel=lambda n=cantidad: n)

    def parameters(self):
        return (parametro for _, parametro in self.named_parameters())


@pytest.fixture
def callback_factory():
    pytest.importorskip("transformers.trainer_callback")
    from src.ajuste_lora import crear_callback_lora

    return crear_callback_lora


def test_callback_permite_solo_lora_y_registra_conteos(callback_factory):
    registro = {}
    callback = callback_factory(registro)
    callback.on_train_begin(None, SimpleNamespace(), None, model=ModeloFalso())
    assert registro
    assert 16 in registro.values()
    assert 116 in registro.values()


@pytest.mark.parametrize("opciones", [
    {"base_entrenable": True},
    {"adaptador_entrenable": False},
])
def test_callback_rechaza_pesos_base_o_ningun_adaptador(opciones, callback_factory):
    callback = callback_factory({})
    with pytest.raises((ValueError, RuntimeError)):
        callback.on_train_begin(None, SimpleNamespace(), None, model=ModeloFalso(**opciones))


def test_callback_conserva_historial_y_mejor_checkpoint(callback_factory):
    registro = {}
    callback = callback_factory(registro)
    estado = SimpleNamespace(global_step=100, best_model_checkpoint="checkpoint-100", best_metric=0.125)
    callback.on_log(None, estado, None, logs={"loss": 0.25})
    callback.on_train_end(None, estado, None)
    assert "checkpoint-100" in str(registro)
    assert "0.125" in str(registro)
    assert "0.25" in str(registro)


def guardar_adaptador(ruta, modulo, revision):
    ruta.mkdir()
    config = dict(peft_type="LORA", base_model_name_or_path=modulo.CHECKPOINT,
                  revision=revision, bias="none", target_modules=list(modulo.MODULOS_LORA))
    (ruta / "adapter_config.json").write_text(json.dumps(config), encoding="utf-8")
    (ruta / "adapter_model.safetensors").write_bytes(b"adaptador falso, sin pesos reales")
    return config


@pytest.fixture
def entrenamiento_doble(monkeypatch, tmp_path):
    from src import ajuste_lora as modulo

    revision = "a" * 40
    snapshot = tmp_path / "snapshot.csv"
    snapshot.write_text("inicio_utc,ghi_w_m2\n2015-01-01T05:00:00Z,0\n", encoding="utf-8")
    opciones = dict(config_base=str(snapshot), datos=str(tmp_path / "datos"),
                    salida_entrenamiento=str(tmp_path / "entrenamiento"),
                    entrenamiento=dict(pasos=1000, batch_size=8, acumulacion=4, rango=8, alpha=16,
                                       evaluar_cada=100, semilla=2026, tasa_aprendizaje=1e-5))
    config = dict(horizonte=24, replanificacion=6,
                  pronosticos=dict(contexto=336, revision=revision, cuantiles=[0.1, 0.5, 0.9]))
    indice = pd.date_range("2014-12-31T05:00:00Z", periods=450, freq="h")
    indice = indice.append(pd.date_range("2025-01-01T05:00:00Z", periods=3, freq="h"))
    historia = pd.DataFrame(dict(inicio_utc=indice, fin_utc=indice + pd.Timedelta(hours=1),
                                 ghi_w_m2=np.arange(len(indice), dtype=float)))
    validacion = [np.arange(360, dtype=np.float32), np.ones(360, dtype=np.float32)]
    monkeypatch.setattr(modulo, "cargar_opciones", lambda ruta: opciones)
    monkeypatch.setattr(modulo, "verificar_entorno", lambda: {"gpu": "RTX 5070 simulada"})
    monkeypatch.setattr(modulo, "cargar_caso", lambda ruta: SimpleNamespace(configuracion=config))
    monkeypatch.setattr(modulo, "cargar_datos", lambda *args: historia)
    ventanas = Mock(return_value=validacion)
    monkeypatch.setattr(modulo, "ventanas_validacion", ventanas)
    monkeypatch.setattr(modulo, "_trazabilidad", lambda *args: {"snapshot_sha256": "sin cambios"})
    monkeypatch.setattr(modulo, "_versiones", lambda: {"chronos-forecasting": "2.3.2"})
    monkeypatch.setattr(modulo, "crear_callback_lora", lambda registro: SimpleNamespace(registro=registro))
    torch = entorno_falso(monkeypatch)
    torch.cuda.max_memory_allocated.return_value = 123456
    torch.equal.side_effect = np.array_equal
    cargar_tensores = Mock(side_effect=lambda ruta: {"lora_A": np.array([1., 2.])})
    monkeypatch.setitem(sys.modules, "safetensors.torch", SimpleNamespace(load_file=cargar_tensores))
    semilla = Mock()
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(set_seed=semilla))
    afinado, recargado = object(), object()

    def ajustar(**kwargs):
        guardar_adaptador(kwargs["output_dir"] / "finetuned-ckpt", modulo, revision)
        mejor = kwargs["output_dir"] / f"checkpoint-{kwargs['num_steps']}"
        mejor.mkdir()
        (mejor / "adapter_model.safetensors").write_bytes(b"mismos pesos falsos")
        kwargs["callbacks"][0].registro.update(parametros_entrenables=16, parametros_totales=116,
                                               checkpoint_elegido=str(mejor), perdida_validacion=0.25,
                                               pasos_completados=kwargs["num_steps"])
        return afinado

    pipeline = SimpleNamespace(fit=Mock(side_effect=ajustar))
    cargar_base = Mock(return_value=pipeline)
    monkeypatch.setattr(modulo, "cargar_chronos", cargar_base)
    recargar = Mock(return_value=recargado)
    monkeypatch.setitem(sys.modules, "chronos", SimpleNamespace(
        Chronos2Pipeline=SimpleNamespace(from_pretrained=recargar)))
    pronosticar = Mock(side_effect=lambda *args, **kwargs: pd.DataFrame([[1., 2., 3.], [2., 3., 4.]]))
    monkeypatch.setattr(modulo, "pronosticar_chronos", pronosticar)
    return SimpleNamespace(modulo=modulo, opciones=opciones, config=config, historia=historia,
                           validacion=validacion, ventanas=ventanas, semilla=semilla, pipeline=pipeline,
                           cargar_base=cargar_base, recargar=recargar, pronosticar=pronosticar,
                           cargar_tensores=cargar_tensores, snapshot=snapshot, tmp_path=tmp_path,
                           revision=revision)


def test_entrenar_doble_lora_guardado_recarga_y_trazabilidad(entrenamiento_doble):
    doble = entrenamiento_doble
    previo = doble.snapshot.read_bytes()
    salida = doble.tmp_path / "prueba_corta"
    informe = doble.modulo.entrenar(doble.tmp_path / "config.json", salida=salida, pasos=2)
    ajustes = doble.pipeline.fit.call_args.kwargs
    assert ajustes["finetune_mode"] == "lora"
    assert ajustes["prediction_length"] == 24
    assert ajustes["context_length"] == ajustes["min_past"] == 336
    assert ajustes["validation_inputs"] is doble.validacion
    assert ajustes["seed"] == ajustes["data_seed"] == 2026
    assert ajustes["num_steps"] == 2
    assert ajustes["bf16"] is True and ajustes["fp16"] is False
    assert ajustes["disable_data_parallel"] is True
    assert ajustes["gradient_accumulation_steps"] == 4
    assert ajustes["lora_config"]["revision"] == doble.revision
    assert ajustes["lora_config"]["bias"] == "none"
    np.testing.assert_array_equal(ajustes["inputs"][0], np.arange(24, 450, dtype=np.float32))
    doble.semilla.assert_called_once_with(2026)
    doble.cargar_base.assert_called_once_with(doble.revision, "cuda")
    assert doble.ventanas.call_args.kwargs == dict(
        **doble.modulo.PERIODOS_LORA["validacion"], contexto=336, horizonte=24, paso=6)
    assert doble.recargar.call_args.args == (str(salida.resolve() / "finetuned-ckpt"),)
    assert doble.recargar.call_args.kwargs["device_map"] == "cuda"
    assert doble.pronosticar.call_count == 2
    assert doble.cargar_tensores.call_count == 2
    assert {c.args[0] for c in doble.cargar_tensores.call_args_list} == {
        str(salida.resolve() / carpeta / "adapter_model.safetensors")
        for carpeta in ("checkpoint-2", "finetuned-ckpt")}
    assert informe["estado"] == "ENTRENADO_Y_RECARGADO"
    assert informe["prueba_corta"] is True
    assert informe["horas_entrenamiento"] == 426
    assert informe["ventanas_validacion"] == 2
    assert informe["recarga"]["coincide"] is True
    assert json.loads((salida / "resumen.json").read_text(encoding="utf-8")) == informe
    for nombre, huella in informe["adaptador_sha256"].items():
        assert hashlib.sha256((salida / "finetuned-ckpt" / nombre).read_bytes()).hexdigest() == huella
    assert doble.snapshot.read_bytes() == previo


def test_entrenar_rechaza_cpu_antes_de_crear_salidas(entrenamiento_doble, monkeypatch):
    doble = entrenamiento_doble

    def sin_gpu():
        raise RuntimeError("Sin GPU CUDA")

    monkeypatch.setattr(doble.modulo, "verificar_entorno", sin_gpu)
    salida = doble.tmp_path / "no_creada"
    with pytest.raises(RuntimeError, match="Sin GPU"):
        doble.modulo.entrenar(doble.tmp_path / "config.json", salida=salida)
    assert not salida.exists()
    doble.cargar_base.assert_not_called()
    doble.pipeline.fit.assert_not_called()


def test_prueba_corta_exige_salida_propia(entrenamiento_doble):
    doble = entrenamiento_doble
    with pytest.raises(ValueError, match="salida propia"):
        doble.modulo.entrenar(doble.tmp_path / "config.json", pasos=2)
    doble.pipeline.fit.assert_not_called()
    assert not (doble.tmp_path / "entrenamiento").exists()


def test_recarga_inconsistente_no_publica_resumen(entrenamiento_doble):
    doble = entrenamiento_doble
    doble.pronosticar.side_effect = [pd.DataFrame([[1.]]), pd.DataFrame([[100.]])]
    salida = doble.tmp_path / "incompleta"
    with pytest.raises(RuntimeError, match="recarga"):
        doble.modulo.entrenar(doble.tmp_path / "config.json", salida=salida, pasos=2)
    assert not (salida / "resumen.json").exists()


@pytest.mark.parametrize("campo,valor", [
    ("revision", "b" * 40),
    ("base_model_name_or_path", "otro/modelo"),
    ("peft_type", "IA3"),
    ("bias", "all"),
    ("target_modules", ["self_attention.q"]),
    ("modules_to_save", ["output_patch_embedding"]),
])
def test_adaptador_ajeno_se_rechaza_antes_de_cargar_pesos(tmp_path, monkeypatch, campo, valor):
    from src import ajuste_lora as modulo

    revision = "a" * 40
    ruta = tmp_path / "adaptador"
    config = guardar_adaptador(ruta, modulo, revision)
    config[campo] = valor
    (ruta / "adapter_config.json").write_text(json.dumps(config), encoding="utf-8")
    cargar = Mock()
    monkeypatch.setitem(sys.modules, "chronos", SimpleNamespace(
        Chronos2Pipeline=SimpleNamespace(from_pretrained=cargar)))
    with pytest.raises(ValueError, match="Adaptador"):
        modulo.cargar_adaptador(ruta, revision)
    cargar.assert_not_called()


def test_adaptador_sin_pesos_se_rechaza(tmp_path):
    from src import ajuste_lora as modulo

    ruta = tmp_path / "adaptador"
    guardar_adaptador(ruta, modulo, "a" * 40)
    (ruta / "adapter_model.safetensors").unlink()
    with pytest.raises(ValueError, match="safetensors"):
        modulo.cargar_adaptador(ruta, "a" * 40)


@pytest.mark.parametrize("campo,valor", [
    ("pasos_completados", 1),
    ("checkpoint_elegido", None),
    ("perdida_validacion", float("nan")),
    ("perdida_validacion", None),
    ("parametros_entrenables", 0),
    ("parametros_totales", 16),
])
def test_mejor_checkpoint_exige_registro_completo(tmp_path, campo, valor):
    from src.ajuste_lora import _verificar_mejor_checkpoint

    registro = dict(pasos_completados=2, checkpoint_elegido="checkpoint-2", perdida_validacion=0.25,
                    parametros_entrenables=16, parametros_totales=116)
    registro[campo] = valor
    with pytest.raises(RuntimeError, match="selección"):
        _verificar_mejor_checkpoint(registro, 2, tmp_path)


@pytest.mark.parametrize("final", [
    {"otro": np.array([1.])},
    {"lora_A": np.array([2.])},
    {},
])
def test_mejor_checkpoint_rechaza_tensores_distintos(tmp_path, monkeypatch, final):
    from src.ajuste_lora import _verificar_mejor_checkpoint

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(equal=np.array_equal))
    monkeypatch.setitem(sys.modules, "safetensors.torch", SimpleNamespace(
        load_file=Mock(side_effect=[{"lora_A": np.array([1.])}, final])))
    registro = dict(pasos_completados=2, checkpoint_elegido="checkpoint-2", perdida_validacion=0.25,
                    parametros_entrenables=16, parametros_totales=116)
    with pytest.raises(RuntimeError, match="mejor checkpoint"):
        _verificar_mejor_checkpoint(registro, 2, tmp_path)


def test_mejor_checkpoint_rechaza_adaptadores_vacios(tmp_path, monkeypatch):
    from src.ajuste_lora import _verificar_mejor_checkpoint

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(equal=np.array_equal))
    monkeypatch.setitem(sys.modules, "safetensors.torch", SimpleNamespace(load_file=Mock(return_value={})))
    registro = dict(pasos_completados=2, checkpoint_elegido="checkpoint-2", perdida_validacion=0.25,
                    parametros_entrenables=16, parametros_totales=116)
    with pytest.raises(RuntimeError, match="mejor checkpoint"):
        _verificar_mejor_checkpoint(registro, 2, tmp_path)
