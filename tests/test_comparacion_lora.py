"""Comparación completa con pipelines dobles; no descarga pesos ni llama a fit."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("astral")
pytest.importorskip("matplotlib")

from src import ajuste_lora, experimento
from test_controlador import ORIGEN, _historia
from test_experimento import _evaluable


HORA = pd.Timedelta(hours=1)


class PipelineDoble:
    def __init__(self, valor):
        self.valor = valor
        self.contextos = []

    def predict_quantiles(self, inputs, prediction_length, quantile_levels, **kwargs):
        self.contextos.append(np.asarray(inputs[0]).copy())
        salida = np.full((1, prediction_length, len(quantile_levels)), self.valor, dtype=float)
        return [salida], [salida[:, :, 0]]

    def fit(self, **kwargs):
        raise AssertionError("La comparación nunca debe entrenar")


@pytest.fixture
def comparacion_doble(tmp_path, monkeypatch):
    caso = _evaluable(real=(0., 1000.), prioridades=(1, 3))
    revision = "a" * 40
    caso.configuracion["pronosticos"].update(revision=revision, dispositivo="cpu")
    entrada = tmp_path / "snapshot.csv"
    entrada.write_text("snapshot inmutable\n", encoding="utf-8")
    caso.rutas = {"snapshot": entrada}
    historia = pd.concat([caso.irradiancia_historica, _historia([1000.] * 5, ORIGEN + 2 * HORA)],
                          ignore_index=True)
    periodos = deepcopy(ajuste_lora.PERIODOS_LORA)
    periodos["evaluacion_nueva"] = dict(inicio=(ORIGEN + 2 * HORA).isoformat(),
                                        fin=(ORIGEN + 7 * HORA).isoformat())
    monkeypatch.setattr(ajuste_lora, "PERIODOS_LORA", periodos)
    opciones = dict(config_base=str(entrada), datos=str(tmp_path / "datos"),
                    salida_entrenamiento=str(tmp_path / "entrenamiento"),
                    salida_comparacion=str(tmp_path / "comparacion"))
    adaptador = Path(opciones["salida_entrenamiento"]) / "finetuned-ckpt"
    adaptador.mkdir(parents=True)
    config_adaptador = dict(peft_type="LORA", base_model_name_or_path=ajuste_lora.CHECKPOINT,
                            revision=revision, bias="none", target_modules=list(ajuste_lora.MODULOS_LORA))
    (adaptador / "adapter_config.json").write_text(json.dumps(config_adaptador), encoding="utf-8")
    (adaptador / "adapter_model.safetensors").write_bytes(b"adaptador doble sin pesos reales")
    trazabilidad = {"codigo_sha256": "checkout congelado", "datos_manifiesto_sha256": "snapshot congelado"}
    entrenamiento = dict(estado="ENTRENADO_Y_RECARGADO", prueba_corta=False, trazabilidad=trazabilidad,
                          adaptador_sha256={p.name: ajuste_lora.sha256_de(p) for p in adaptador.iterdir()})
    ruta_entrenamiento = adaptador.parent / "resumen.json"
    ruta_entrenamiento.write_text(json.dumps(entrenamiento), encoding="utf-8")
    base, lora = PipelineDoble(0.), PipelineDoble(1000.)
    cargar_base, cargar_adaptador = Mock(return_value=base), Mock(return_value=lora)
    monkeypatch.setattr(ajuste_lora, "cargar_opciones", lambda ruta: opciones)
    monkeypatch.setattr(ajuste_lora, "cargar_caso", lambda ruta: caso)
    monkeypatch.setattr(ajuste_lora, "cargar_datos", lambda *args: historia)
    monkeypatch.setattr(ajuste_lora, "_trazabilidad", lambda *args: deepcopy(trazabilidad))
    monkeypatch.setattr(ajuste_lora, "_versiones", lambda: {"chronos-forecasting": "doble"})
    monkeypatch.setattr(ajuste_lora, "cargar_chronos", cargar_base)
    monkeypatch.setattr(ajuste_lora, "cargar_adaptador", cargar_adaptador)
    monkeypatch.setattr(ajuste_lora, "verificar_entorno", Mock(side_effect=AssertionError("Prueba solo CPU")))
    # Las gráficas energéticas se verifican en test_experimento; aquí se ejercita la figura de reserva.
    monkeypatch.setattr(experimento, "graficar", lambda *args: None)
    return SimpleNamespace(caso=caso, historia=historia, opciones=opciones, adaptador=adaptador,
                           revision=revision, entrenamiento=entrenamiento, ruta_entrenamiento=ruta_entrenamiento,
                           trazabilidad=trazabilidad, base=base, lora=lora, cargar_base=cargar_base,
                           cargar_adaptador=cargar_adaptador, salida=Path(opciones["salida_comparacion"]),
                           config=tmp_path / "config.json", entrada=entrada)


def test_comparacion_completa_exporta_modelos_reserva_y_resumen_al_final(comparacion_doble, monkeypatch):
    doble = comparacion_doble
    archivos_antes = {p: p.read_bytes() for p in (doble.entrada, doble.ruta_entrenamiento,
                                                *doble.adaptador.iterdir())}
    guardar = ajuste_lora._guardar_json
    publicados = []

    def publicar(ruta, informe):
        for nombre in ("informe.md", "tabla_lora.csv", "tabla_lora.md", "pronosticos_reserva.csv",
                       "tabla_reserva.csv", "reserva_irradiancia.png"):
            assert (doble.salida / nombre).stat().st_size > 0
        publicados.append(ruta.name)
        guardar(ruta, informe)

    monkeypatch.setattr(ajuste_lora, "_guardar_json", publicar)
    informe = ajuste_lora.comparar(doble.config, dispositivo="cpu")
    assert publicados == ["resumen.json"]
    assert json.loads((doble.salida / "resumen.json").read_text(encoding="utf-8")) == informe
    assert informe["estado"] == "COMPARACION_COMPLETA"
    assert informe["precision_inferencia"] == "float32 ambos modelos"
    assert informe["marzo"]["diferencias_lora"]["dias_lora_mayor"] == 1
    assert informe["marzo"]["reproducibilidad"]["coincide"]
    assert informe["reserva"]["metricas"]["chronos"]["origenes"] == 4
    assert informe["reserva"]["metricas"]["chronos_lora"]["origenes"] == 4
    tabla = pd.read_csv(doble.salida / "tabla_reserva.csv")
    global_mae = tabla[(tabla.subconjunto == "global") & (tabla.correccion == "crudo")
                       & (tabla.metrica == "mae_w_m2")].iloc[0]
    assert (global_mae.chronos, global_mae.chronos_lora, global_mae.diferencia_lora_menos_base) == (1000., 0., -1000.)
    assert set(pd.read_csv(doble.salida / "pronosticos_reserva.csv").proveedor) == {"chronos", "chronos_lora"}
    assert "Marzo fue inspeccionado" in (doble.salida / "informe.md").read_text(encoding="utf-8")
    doble.cargar_base.assert_called_once_with(doble.revision, "cpu")
    doble.cargar_adaptador.assert_called_once_with(doble.adaptador, doble.revision, "cpu")
    assert {p: p.read_bytes() for p in archivos_antes} == archivos_antes


def test_reserva_usa_horizontes_completos_y_solo_contexto_disponible(comparacion_doble):
    doble = comparacion_doble
    sincronizar = Mock()
    tabla, resumen = ajuste_lora._pronostico_nuevo(doble.base, doble.historia, doble.caso.configuracion,
                                                   sincronizar)
    inicio, fin = (pd.Timestamp(ajuste_lora.PERIODOS_LORA["evaluacion_nueva"][k]) for k in ("inicio", "fin"))
    origenes = pd.date_range(inicio, fin - 2 * HORA, freq="h")
    assert tabla.origen_utc.drop_duplicates().tolist() == origenes.tolist()
    assert tabla.groupby("origen_utc").size().tolist() == [2] * 4
    assert (tabla.inicio_utc >= inicio).all() and (tabla.inicio_utc + HORA <= fin).all()
    assert resumen["origenes"] == 4 and sincronizar.call_count == 8
    for origen, contexto in zip(origenes, doble.base.contextos, strict=True):
        pasado = doble.historia[(doble.historia.fin_utc <= origen) & (doble.historia.available_at <= origen)]
        np.testing.assert_array_equal(contexto, pasado.ghi_w_m2.iloc[-336:].to_numpy(dtype=np.float32))
    futuro_alterado = doble.historia.copy()
    futuro_alterado.loc[futuro_alterado.inicio_utc >= inicio, "ghi_w_m2"] += 777.
    ajuste_lora._pronostico_nuevo(doble.base, futuro_alterado, doble.caso.configuracion, lambda: None)
    np.testing.assert_array_equal(doble.base.contextos[0], doble.base.contextos[4])


@pytest.mark.parametrize("fallo", ["prueba_corta", "trazabilidad", "pesos"])
def test_comparacion_rechaza_entrenamiento_no_final_o_alterado(comparacion_doble, fallo):
    doble = comparacion_doble
    if fallo == "prueba_corta":
        doble.entrenamiento["prueba_corta"] = True
    elif fallo == "trazabilidad":
        doble.entrenamiento["trazabilidad"] = {"otro": "checkout"}
    else:
        (doble.adaptador / "adapter_model.safetensors").write_bytes(b"pesos alterados")
    doble.ruta_entrenamiento.write_text(json.dumps(doble.entrenamiento), encoding="utf-8")
    with pytest.raises(ValueError):
        ajuste_lora.comparar(doble.config, dispositivo="cpu")
    doble.cargar_base.assert_not_called()
    doble.cargar_adaptador.assert_not_called()
    assert not doble.salida.exists()


@pytest.mark.parametrize("archivo", ["pesos", "resumen_entrenamiento"])
def test_cambio_durante_backtest_no_publica_exito(comparacion_doble, monkeypatch, archivo):
    doble = comparacion_doble

    def cambiar(*args):
        ruta = (doble.adaptador / "adapter_model.safetensors" if archivo == "pesos"
                else doble.ruta_entrenamiento)
        ruta.write_bytes(ruta.read_bytes() + b"\nmodificado durante backtest")

    monkeypatch.setattr(ajuste_lora, "_graficar_reserva", cambiar)
    with pytest.raises((ValueError, RuntimeError), match="adaptador|entrenamiento"):
        ajuste_lora.comparar(doble.config, dispositivo="cpu")
    assert not (doble.salida / "resumen.json").exists()


def test_objetivos_distintos_en_reserva_no_publican_resumen(comparacion_doble, monkeypatch):
    doble = comparacion_doble
    pronosticar = ajuste_lora._pronostico_nuevo

    def alterar(pipeline, *args):
        tabla, metricas = pronosticar(pipeline, *args)
        if pipeline is doble.lora:
            tabla.loc[0, "ghi_real_w_m2"] += 1.
        return tabla, metricas

    monkeypatch.setattr(ajuste_lora, "_pronostico_nuevo", alterar)
    with pytest.raises(ValueError, match="mismos objetivos"):
        ajuste_lora.comparar(doble.config, dispositivo="cpu")
    assert not (doble.salida / "resumen.json").exists()


def test_fallo_al_escribir_informe_no_deja_resumen_exitoso(comparacion_doble, monkeypatch):
    doble = comparacion_doble
    escribir = Path.write_text

    def fallar(ruta, *args, **kwargs):
        if ruta == doble.salida / "informe.md":
            raise OSError("Sin espacio para el informe")
        return escribir(ruta, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fallar)
    monkeypatch.setattr(ajuste_lora, "_graficar_reserva", lambda *args: None)
    with pytest.raises(OSError, match="Sin espacio"):
        ajuste_lora.comparar(doble.config, dispositivo="cpu")
    assert not (doble.salida / "resumen.json").exists()
