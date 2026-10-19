"""Calibración pareada mínima, con recorridos físicos y pronósticos dobles."""

from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

from energy_project import controlador
from energy_project.simulador import simular
from test_controlador import ORIGEN, _caso, _cuantiles


@pytest.mark.parametrize("empate,peso_esperado", [(False, 0.75), (True, 0.)])
def test_calibracion_reutiliza_pronosticos_y_conserva_ocho_estados(
        monkeypatch, empate, peso_esperado):
    caso = _caso()
    fin = ORIGEN + pd.Timedelta(hours=2)
    caso.configuracion["pronosticos"].update(revision="a" * 40, dispositivo="cpu")
    caso.configuracion["escenarios"] = 5
    caso.configuracion["periodos"] = {
        "desarrollo": dict(inicio=ORIGEN.isoformat(), fin=fin.isoformat()),
        "evaluacion": dict(inicio=fin.isoformat(), fin=(fin + pd.Timedelta(days=7)).isoformat()),
    }
    original = deepcopy(caso.configuracion)
    monkeypatch.setattr(controlador, "CALIBRACION_INICIO", ORIGEN)
    monkeypatch.setattr(controlador, "CALIBRACION_FIN", fin)
    llamadas, cargas, estados = {"beta": [], "chronos": []}, [], {}
    pipeline = object()

    def cargar(revision, dispositivo):
        cargas.append((revision, dispositivo))
        return pipeline

    def pronostico(proveedor, historia, origen, **opciones):
        llamadas[proveedor].append(origen)
        assert len(historia) == 336 and (historia.available_at <= origen).all()
        valores = [0., 1000.] if origen == ORIGEN else [0.]
        tabla = _cuantiles(valores[:opciones["horizonte"]], origen)
        if proveedor == "beta" and not empate and origen == ORIGEN:
            tabla.iloc[1, 0] = 0.
        tabla.attrs["proveedor"] = proveedor
        return tabla

    def beta(historia, origen, **opciones):
        return pronostico("beta", historia, origen, **opciones)

    def chronos(modelo, historia, origen, **opciones):
        assert modelo is pipeline
        return pronostico("chronos", historia, origen, **opciones)

    def escenarios(cuantiles, historia, origen, configuracion):
        # Rangos comunes: beta deja un escenario sin sol; Chronos mantiene cinco.
        u = np.tile(np.array([0.1, 0.5, 0.6, 0.7, 0.9])[:, None], (1, len(cuantiles)))
        trayectorias = np.column_stack([
            np.interp(u[:, h], cuantiles.columns, cuantiles.iloc[h])
            for h in range(len(cuantiles))
        ])
        return dict(escenarios=trayectorias, rangos=u,
                    bloques=pd.DatetimeIndex([origen - pd.Timedelta(days=1)] * 5),
                    semilla=[20261019, int(origen.timestamp() // 3600)])

    ejecutar = controlador._ejecutar_bloque

    def observar(caso, reloj, origen, fin, prediccion, estado, configuracion):
        clave = (prediccion["crudos"].attrs["proveedor"], configuracion["lambda_terminal"])
        estados.setdefault(clave, []).append(estado.copy())
        return ejecutar(caso, reloj, origen, fin, prediccion, estado, configuracion)

    monkeypatch.setattr(controlador, "cargar_chronos", cargar)
    monkeypatch.setattr(controlador, "pronosticar_beta", beta)
    monkeypatch.setattr(controlador, "pronosticar_chronos", chronos)
    monkeypatch.setattr(controlador, "generar_escenarios", escenarios)
    monkeypatch.setattr(controlador, "_ejecutar_bloque", observar)
    resultado = controlador.calibrar_mpc(caso)
    assert len(cargas) == 1
    assert llamadas == {proveedor: [ORIGEN, ORIGEN + pd.Timedelta(hours=1)]
                        for proveedor in ("beta", "chronos")}
    assert len(resultado["resultados"]) == len(resultado["tabla"]) == 8
    assert len(estados) == 8
    for clave, historia in estados.items():
        assert historia[0] == {"b": 1.}
        reservado = not empate and clave == ("beta", 0.75)
        assert historia[1] == {"b": 1. if reservado else 0.}
    tabla = resultado["tabla"]
    medias = tabla.groupby("lambda_terminal").J.mean()
    assert resultado["lambda_terminal"] == medias.idxmax() == peso_esperado
    assert set(tabla.proveedor) == {"beta", "chronos"}
    for (proveedor, peso), recorrido in resultado["resultados"].items():
        fila = tabla.loc[tabla.proveedor.eq(proveedor) & tabla.lambda_terminal.eq(peso)].iloc[0]
        assert fila.J == recorrido["resumen"]["J"]
        assert fila.S_pond == recorrido["resumen"]["S_pond"]
    assert resultado["inicio_utc"] == ORIGEN and resultado["fin_utc"] == fin
    assert all(len(registros) == 2 for registros in resultado["pronosticos"].values())
    assert caso.configuracion == original


def test_verificacion_fisica_detecta_un_balance_de_bateria_alterado():
    caso = _caso()
    resultado = simular(caso.demanda, caso.irradiancia_simulacion, caso.configuracion)
    informe = controlador._verificar_fisica(resultado, caso.configuracion)
    assert informe["violaciones_fisicas"] == 0
    assert informe["max_error_bus_wh"] <= 1e-6
    assert informe["max_error_bateria_wh"] <= 1e-6
    resultado["baterias"].loc[0, "final_wh"] += 0.1
    with pytest.raises(ValueError):
        controlador._verificar_fisica(resultado, caso.configuracion)


@pytest.fixture
def publicacion(tmp_path, monkeypatch):
    caso = _caso()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(caso.configuracion), encoding="utf-8")
    original = config.read_bytes()
    caso.rutas = {"configuracion": config}
    salida = tmp_path / "salida"
    ensayo = dict(lambda_terminal=0.75, tabla=pd.DataFrame([dict(J=1.)]), pronosticos={},
                  inicio_utc=ORIGEN, fin_utc=ORIGEN + pd.Timedelta(hours=2))
    monkeypatch.setattr("energy_project.datos.cargar_caso", lambda ruta: caso)
    monkeypatch.setattr(controlador, "calibrar_mpc", lambda caso: ensayo)
    monkeypatch.setattr(importlib.metadata, "version", lambda paquete: "prueba")
    monkeypatch.setattr(sys, "argv", ["controlador", "--config", str(config), "--salida", str(salida)])
    return caso, config, salida, original


@pytest.mark.parametrize("fallo", [
    "escritura_configuracion", "escritura_informe", "reemplazo_informe", "reemplazo_configuracion", "codigo",
])
def test_calibracion_fallida_no_publica_informe_ni_reemplaza_configuracion(publicacion, monkeypatch, fallo):
    _, config, salida, original = publicacion
    temporal_config = config.with_name(config.name + ".t04.tmp")
    temporal_informe = salida / "resumen.json.tmp"
    if fallo.startswith("escritura"):
        escribir = Path.write_text
        ruta_fallida = temporal_config if fallo == "escritura_configuracion" else temporal_informe

        def fallar_escritura(ruta, *args, **kwargs):
            if ruta == ruta_fallida:
                escribir(ruta, "archivo parcial", encoding="utf-8")
                raise OSError("escritura fallida")
            return escribir(ruta, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fallar_escritura)
    elif fallo.startswith("reemplazo"):
        reemplazar = Path.replace
        ruta_fallida = temporal_config if fallo == "reemplazo_configuracion" else temporal_informe

        def fallar_reemplazo(ruta, destino):
            if ruta == ruta_fallida:
                raise OSError("reemplazo fallido")
            return reemplazar(ruta, destino)

        monkeypatch.setattr(Path, "replace", fallar_reemplazo)
    else:
        hashes = iter([{"controlador.py": "original"}, {"controlador.py": "editado"}])
        monkeypatch.setattr("energy_project.cli.codigo_sha256", lambda: next(hashes))
    with pytest.raises(SystemExit) as error:
        controlador.main()
    assert error.value.code == 1
    assert not (salida / "resumen.json").exists()
    assert config.read_bytes() == original
    assert not temporal_config.exists()
    assert not temporal_informe.exists()


def test_calibracion_exitosa_publica_informe_y_configuracion_coherentes(publicacion):
    caso, config, salida, original = publicacion
    controlador.main()
    actualizada = json.loads(config.read_text(encoding="utf-8"))
    informe = json.loads((salida / "resumen.json").read_text(encoding="utf-8"))
    assert actualizada["lambda_terminal"] == informe["lambda_terminal"] == 0.75
    assert informe["configuracion"] == actualizada
    assert informe["configuracion_entrada"] == caso.configuracion == json.loads(original)
    assert informe["entradas_sha256"]["configuracion"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert not config.with_name(config.name + ".t04.tmp").exists()
    assert not (salida / "resumen.json.tmp").exists()
