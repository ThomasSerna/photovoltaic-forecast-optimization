from pathlib import Path
import sys
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.calibrar_servicio import seleccionar_servicio, calibraciones


def datos():
    filas=[]
    for fecha in pd.date_range('2025-01-06','2025-02-16'):
        for proveedor in ('chronos','beta'):
            for peso in (0.,1.):
                filas.append(dict(fecha=fecha.strftime('%Y-%m-%d'),modo_mpc='cvar',proveedor=proveedor,lambda_riesgo=peso,J=10+peso))
    return pd.DataFrame(filas)


def test_maximiza_servicio_sin_criterio_de_cola_y_empate_menor_peso():
    d=datos()
    assert seleccionar_servicio(d,'2025-02-17','chronos')[0]==1.
    d['J']=10.
    assert seleccionar_servicio(d,'2025-02-17','chronos')[0]==0.


def test_cortes_y_seleccion_no_usan_resultados_posteriores():
    d=datos()
    estrategias,cortes=calibraciones(d)
    posterior=d.iloc[:1].copy()
    posterior['fecha']='2025-03-04'
    posterior['J']=1e12
    otros,otros_cortes=calibraciones(pd.concat([d,posterior],ignore_index=True))
    pd.testing.assert_frame_equal(estrategias,otros)
    pd.testing.assert_frame_equal(cortes,otros_cortes)
    assert len(cortes)==6


def test_fuentes_marzo_rechazan_cuantiles_alterados_y_otro_baseline(tmp_path):
    from copy import deepcopy
    import json
    from types import SimpleNamespace
    import pytest
    from src.cli import entradas_sha256, sha256_de
    from test_controlador import _config
    from scripts.calibrar_servicio import verificar_fuentes_marzo

    config=_config()
    config.update(semilla=1, periodos=dict(evaluacion=dict(inicio='2025-03-03T00:00:00-05:00',fin='2025-03-04T00:00:00-05:00')))
    entrada=tmp_path/'demanda.csv'
    entrada.write_text('entrada estable',encoding='utf-8')
    caso=SimpleNamespace(configuracion=config,rutas=dict(demanda=entrada))
    reg,comp=tmp_path/'reg',tmp_path/'comp'
    reg.mkdir(); comp.mkdir()
    cuantiles=reg/'pronosticos.csv'
    cuantiles.write_text('cuantiles congelados',encoding='utf-8')
    base=dict(entradas_sha256=entradas_sha256(caso.rutas),inicio_utc=config['periodos']['evaluacion']['inicio'],
              fin_utc=config['periodos']['evaluacion']['fin'])
    informe_reg=dict(base,archivos_sha256={'pronosticos.csv':sha256_de(cuantiles)})
    (reg/'resumen.json').write_text(json.dumps(informe_reg),encoding='utf-8')
    baseline=dict(base,archivos_sha256={},configuracion_nueva=dict(deepcopy(config),modo_mpc='cvar',alpha_cvar=.9,lambda_riesgo=0.))
    (comp/'resumen.json').write_text(json.dumps(baseline),encoding='utf-8')
    assert str(cuantiles.resolve()) in verificar_fuentes_marzo(caso,reg,comp)
    cuantiles.write_text('datos alterados',encoding='utf-8')
    with pytest.raises(ValueError,match='alterada'):
        verificar_fuentes_marzo(caso,reg,comp)
    cuantiles.write_text('cuantiles congelados',encoding='utf-8')
    baseline['configuracion_nueva']['lambda_riesgo']=1.
    (comp/'resumen.json').write_text(json.dumps(baseline),encoding='utf-8')
    with pytest.raises(ValueError,match='baseline'):
        verificar_fuentes_marzo(caso,reg,comp)
