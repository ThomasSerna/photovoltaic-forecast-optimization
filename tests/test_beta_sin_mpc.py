from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = spec_from_file_location('beta_sin_mpc', Path(__file__).resolve().parents[1]/'scripts/comparar_beta_sin_mpc.py')
modulo = module_from_spec(spec)
spec.loader.exec_module(modulo)


def test_modal_y_desempate_no_usa_beneficio():
    a = np.zeros((2,10), dtype=int)
    b = np.ones((2,10), dtype=int)
    elegido, conteos = modulo.elegir_frecuente([a,b,b])
    assert elegido == 1 and conteos.tolist() == [1,2,2]
    assert modulo.elegir_frecuente([a,b])[0] == 0


def test_similitud_es_por_carga_con_perfil_completo_no_por_celda():
    a = np.zeros((24,10), dtype=int)
    b = a.copy()
    b[0,:2] = 1
    assert modulo.elegir_frecuente([a,b])[1].tolist() == [1,1]
    b[0,1] = 0
    assert modulo.elegir_frecuente([a,b])[1].tolist() == [2,2]


def test_rechaza_perfiles_invalidos():
    with pytest.raises(ValueError):
        modulo.elegir_frecuente([[[2]]])


def test_dia_completo_continuidad_y_sin_realizacion_futura_en_plan(monkeypatch):
    import pandas as pd
    from test_controlador import _caso
    caso = _caso(real=(0.,)*48, prioridades=(1,)*48)
    for tabla in (caso.calendario, caso.irradiancia_simulacion, caso.irradiancia_historica):
        for columna in tabla.select_dtypes(include=['datetimetz']).columns:
            tabla[columna] -= pd.Timedelta(hours=11)
    inicio = caso.calendario.inicio_utc.iloc[0]
    caso.configuracion['periodos'] = dict(evaluacion=dict(inicio=inicio, fin=inicio+pd.Timedelta(hours=48)))
    monkeypatch.setattr(modulo, '_predecir', lambda *args: dict(corregidos=pd.DataFrame(index=pd.date_range(args[2], periods=24, freq='h')),
                                                             escenarios=np.zeros((3,24))))
    uno = modulo.ejecutar_beta_sin_mpc(caso)
    caso.irradiancia_simulacion.loc[caso.irradiancia_simulacion.t.ge(6), 'ghi_w_m2'] = 1000.
    otro = modulo.ejecutar_beta_sin_mpc(caso)
    assert len(uno['decisiones']) == 2
    assert uno['decisiones'].horas_ejecutadas.tolist() == [24,24]
    assert uno['baterias'].loc[uno['baterias'].t.eq(24), 'inicial_wh'].iloc[0] == uno['baterias'].loc[uno['baterias'].t.eq(23), 'final_wh'].iloc[0]
    pd.testing.assert_frame_equal(uno['planes_elegidos'].iloc[:24], otro['planes_elegidos'].iloc[:24])
