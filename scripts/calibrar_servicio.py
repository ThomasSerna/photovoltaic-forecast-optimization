"""Recalibración experimental por J realizado, sin seleccionar parámetros con marzo."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from src.cli import codigo_sha256, entradas_sha256, sha256_de
from src.controlador import ejecutar_mpc, _verificar_fisica
from src.datos import cargar_caso
from src.experimento import metricas_servicio
from src.configuracion import parametros_mpc
from scripts.comparar_mpc_chronos import pronostico_congelado

RAIZ = Path(__file__).resolve().parents[1]


def seleccionar_servicio(diario, fin, proveedor=None):
    """Mayor J total por proveedor, promediado entre proveedores; empate menor peso."""
    datos = diario.loc[(diario.fecha >= '2025-01-06') & (diario.fecha < fin) & diario.modo_mpc.eq('cvar')]
    if proveedor is not None:
        datos = datos.loc[datos.proveedor.eq(proveedor)]
    totales = datos.groupby(['lambda_riesgo', 'proveedor']).J.sum()
    tabla = totales.groupby('lambda_riesgo').mean().rename('J_medio').reset_index()
    if tabla.empty:
        raise ValueError('No hay observaciones de ajuste')
    ganador = tabla.sort_values(['J_medio', 'lambda_riesgo'], ascending=[False, True]).iloc[0]
    return float(ganador.lambda_riesgo), tabla


def calibraciones(diario):
    """Tres cortes expansivos semanales y ajuste completo, exclusivamente en desarrollo."""
    estrategias, cortes = [], []
    for proveedor in ('chronos', None):
        nombre = proveedor or 'comun_beta_chronos'
        for corte in ('2025-01-27', '2025-02-03', '2025-02-10'):
            peso, _ = seleccionar_servicio(diario, corte, proveedor)
            fin = (pd.Timestamp(corte)+pd.Timedelta(days=7)).strftime('%Y-%m-%d')
            validacion = diario.loc[(diario.fecha >= corte) & (diario.fecha < fin) & diario.modo_mpc.eq('cvar')]
            if proveedor:
                validacion = validacion.loc[validacion.proveedor.eq(proveedor)]
            scores = validacion.groupby(['lambda_riesgo','proveedor']).J.sum().groupby('lambda_riesgo').mean()
            cortes.append(dict(criterio=nombre, inicio_validacion=corte, fin_validacion=fin,
                               lambda_elegido=peso, J_validacion=float(scores.loc[peso]),
                               J_validacion_cero=float(scores.loc[0.])))
        peso, tabla = seleccionar_servicio(diario, '2025-02-17', proveedor)
        estrategias.append(dict(criterio=nombre, lambda_elegido=peso))
    return pd.DataFrame(estrategias), pd.DataFrame(cortes)


def fuente_desarrollo(registros):
    filas = []
    for p in registros:
        if p['proveedor'] != 'chronos':
            continue
        for h, fecha in enumerate(p['inicio_utc']):
            fila = dict(proveedor='chronos', origen_utc=p['origen_utc'], inicio_utc=fecha, anticipacion_h=h+1)
            fila.update({f'crudo_q{q}':p['crudos'][h][i] for i,q in enumerate(p['cuantiles'])})
            filas.append(fila)
    return pd.DataFrame(filas)


def verificar_fuentes_marzo(caso, regresion, comparacion):
    """Vincula cuantiles y baseline a sus manifiestos y a las condiciones actuales."""
    informes = [json.loads((p/'resumen.json').read_text(encoding='utf-8')) for p in (regresion, comparacion)]
    huellas = {}
    for carpeta, informe in zip((regresion, comparacion), informes):
        huellas[str(carpeta/'resumen.json')] = sha256_de(carpeta/'resumen.json')
        for nombre, esperado in informe['archivos_sha256'].items():
            ruta = (carpeta/nombre).resolve()
            if not ruta.is_relative_to(carpeta.resolve()) or sha256_de(ruta) != esperado:
                raise ValueError(f'Fuente de marzo alterada: {nombre}')
            huellas[str(ruta)] = esperado
        for nombre, actual in entradas_sha256(caso.rutas).items():
            if nombre != 'configuracion' and actual['sha256'] != informe['entradas_sha256'][nombre]['sha256']:
                raise ValueError(f'Fuente de marzo con entrada distinta: {nombre}')
        inicio, fin = (pd.Timestamp(caso.configuracion['periodos']['evaluacion'][k]) for k in ('inicio','fin'))
        if inicio != pd.Timestamp(informe['inicio_utc']) or fin != pd.Timestamp(informe['fin_utc']):
            raise ValueError('Fuente de marzo con fechas distintas')
    config = informes[1]['configuracion_nueva']
    if parametros_mpc(config) != ('cvar', .9, 0.):
        raise ValueError('El baseline debe usar alpha=0.9 y lambda=0')
    for campo in ('pv','baterias','potencia_bus_max_w','resolucion_h','horizonte','replanificacion',
                  'escenarios','pronosticos','semilla','semilla_escenarios','zona_horaria','latitud','longitud'):
        if config[campo] != caso.configuracion[campo]:
            raise ValueError(f'Condiciones de baseline distintas: {campo}')
    if str((regresion/'pronosticos.csv').resolve()) not in huellas:
        raise ValueError('Falta la huella de los pronósticos congelados')
    return huellas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--salida', type=Path, default=RAIZ/'results/calibracion_servicio')
    args = parser.parse_args()
    carpeta = RAIZ/'results/calibracion_riesgo'
    referencia = json.loads((carpeta/'resumen.json').read_text(encoding='utf-8'))
    for nombre, huella in referencia['archivos_sha256'].items():
        if sha256_de(carpeta/nombre) != huella:
            raise ValueError(f'Calibración de origen alterada: {nombre}')
    caso = cargar_caso(RAIZ/'scripts/configuracion.json')
    entradas, codigo = entradas_sha256(caso.rutas), codigo_sha256()
    for nombre, registro in entradas.items():
        if registro['sha256'] != referencia['entradas_sha256'][nombre]['sha256']:
            raise ValueError(f'Entrada distinta: {nombre}')
    guion = sha256_de(Path(__file__))
    regresion = RAIZ/'results/regresion_original'
    comparacion = RAIZ/'results/comparacion_mpc_chronos'
    fuentes_marzo = verificar_fuentes_marzo(caso, regresion, comparacion)
    diario = pd.read_csv(carpeta/'diario.csv',float_precision='round_trip')
    estrategias, cortes = calibraciones(diario)
    args.salida.mkdir(parents=True, exist_ok=False)
    estrategias.to_csv(args.salida/'estrategias.csv',index=False)
    cortes.to_csv(args.salida/'cortes_temporales.csv',index=False)
    comparaciones = []
    inicio = perf_counter()
    registros = json.loads((carpeta/'pronosticos.json').read_text(encoding='utf-8'))
    desarrollo = fuente_desarrollo(registros)
    marzo = pd.read_csv(regresion/'pronosticos.csv',float_precision='round_trip')
    base_marzo = json.loads((comparacion/'resumen.json').read_text(encoding='utf-8'))
    for peso in sorted(set(estrategias.lambda_elegido)):
        config = dict(caso.configuracion, modo_mpc='cvar', alpha_cvar=.9, lambda_riesgo=float(peso))
        candidato = replace(caso, configuracion=config)
        if peso == 0:
            valid = next(r for r in referencia['resultados']['validacion'] if r['proveedor']=='chronos' and r['modo_mpc']=='cvar')
            comparaciones.extend([dict(periodo='validacion',lambda_riesgo=0.,J=valid['J'],S_pond=valid['S_pond'],servicio_critico=valid['servicio_critico']),
                                  dict(periodo='marzo',lambda_riesgo=0.,**base_marzo['metricas']['nuevo'])])
            continue
        estado = next(f['estado'] for f in referencia['estados_frontera']
                      if f['proveedor']=='chronos' and f['modo_mpc']=='cvar' and f['lambda_riesgo']==peso)
        for fase, inicio_fase, fin, fuente, inicial in (
            ('validacion','2025-02-17T00:00:00-05:00','2025-03-03T00:00:00-05:00',desarrollo,estado),
            ('marzo','2025-03-03T00:00:00-05:00','2025-03-31T00:00:00-05:00',marzo,None)):
            print(f'Ejecutando {fase}, lambda={peso}',flush=True)
            resultado = ejecutar_mpc(candidato, pronostico_congelado(fuente,config),
                                    inicio=inicio_fase,fin=fin,estado_inicial=inicial)
            fisica = _verificar_fisica(resultado,config)
            metricas = metricas_servicio(resultado,config)
            comparaciones.append(dict(periodo=fase,lambda_riesgo=float(peso),**metricas,**fisica))
            for tabla in ('cargas','horas','baterias','decisiones'):
                resultado[tabla].to_csv(args.salida/f'{fase}_lambda{peso}_{tabla}.csv',index=False)
    pd.DataFrame(comparaciones).to_csv(args.salida/'comparaciones.csv',index=False)
    if entradas_sha256(caso.rutas)!=entradas or codigo_sha256()!=codigo or sha256_de(Path(__file__))!=guion:
        raise ValueError('Entradas o código cambiaron durante el ensayo')
    if verificar_fuentes_marzo(caso, regresion, comparacion) != fuentes_marzo:
        raise ValueError('Las referencias de marzo cambiaron durante el ensayo')
    informe = dict(objetivo='Maximizar J realizado; alpha=0.9, pesos (0,.25,.5,1,2), física y pronósticos sin cambios',
                   estrategias=estrategias.to_dict('records'), cortes=cortes.to_dict('records'), comparaciones=comparaciones,
                   limites=['Cortes reutilizan trayectorias continuas de cada peso desde el 6 de enero',
                            'Tres validaciones semanales tras ajustes de tres, cuatro y cinco semanas; sin reinicios de batería',
                            'Marzo es análisis exploratorio posterior a selección, ya observado anteriormente',
                            'No se declara óptimo global ni se modifican H, R, candidatos, infraestructura o pesos del modelo'],
                   entradas_sha256=entradas,codigo_sha256=codigo,guion_sha256=guion,fuentes_marzo_sha256=fuentes_marzo,
                   calibracion_origen_sha256=sha256_de(carpeta/'resumen.json'),duracion_s=perf_counter()-inicio,
                   archivos_sha256={p.name:sha256_de(p) for p in args.salida.iterdir() if p.is_file()})
    (args.salida/'resumen.json').write_text(json.dumps(informe,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print('Calibraciones y comparación terminadas',flush=True)


if __name__=='__main__':
    main()
