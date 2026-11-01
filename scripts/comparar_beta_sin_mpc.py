"""Experimento beta sin MPC: calendario diario modal frente a Chronos + MPC nuevo."""

import argparse
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from src.cli import codigo_sha256, entradas_sha256, sha256_de
from src.controlador import _periodo, _predecir, _unir_resultados, _verificar_fisica, calcular_cvar, proveedores
from src.datos import cargar_caso
from src.experimento import metricas_servicio
from src.simulador import _estado_inicial, simular
from scripts.comparar_mpc_chronos import diario_comparativo, graficar


RAIZ = Path(__file__).resolve().parents[1]


def elegir_frecuente(perfiles):
    """§3.1: >=90% de cargas con el mismo perfil en TODAS las horas; empate por orden."""
    perfiles = np.asarray(perfiles)
    if perfiles.ndim != 3 or not perfiles.size or not np.isin(perfiles, [0, 1]).all():
        raise ValueError("Se requieren perfiles binarios escenario × hora × carga")
    similares = np.array([sum(np.mean(np.all(a == b, axis=0)) >= .9 for b in perfiles)
                          for a in perfiles], dtype=int)
    return int(similares.argmax()), similares


def ejecutar_beta_sin_mpc(caso):
    config = caso.configuracion
    periodo = config['periodos']['evaluacion']
    inicio, fin, reloj = _periodo(caso, periodo['inicio'], periodo['fin'])
    if inicio.tz_convert(config['zona_horaria']).hour or (fin-inicio) % pd.Timedelta(days=1):
        raise ValueError('Se requieren días locales completos')
    estado = _estado_inicial(config['baterias'], None)
    beta = proveedores(caso, None)['beta']
    resultados, decisiones, pronosticos = [], [], []
    for origen in pd.date_range(inicio, fin, freq='24h', inclusive='left'):
        prediccion = _predecir(caso, beta, origen, 24)
        ventana = reloj.loc[reloj.inicio_utc.isin(prediccion['corregidos'].index)]
        demanda = caso.demanda.loc[caso.demanda.t.isin(ventana.t)]
        planes, perfiles, cache = [], [], {}
        for trayectoria in prediccion['escenarios']:
            clave = trayectoria.tobytes()
            if clave not in cache:
                candidato = simular(demanda, pd.DataFrame(dict(t=ventana.t.to_numpy(), ghi_w_m2=trayectoria)),
                                    config, estado_inicial=estado)
                cache[clave] = candidato['cargas'][['t', 'carga_id', 'x']].copy()
            plan = cache[clave]
            planes.append(plan)
            perfiles.append(plan.pivot(index='t', columns='carga_id', values='x').to_numpy())
        elegido, frecuencias = elegir_frecuente(perfiles)
        # La irradiancia realizada solo se consulta después de fijar todo el día.
        solar = caso.irradiancia_simulacion.loc[caso.irradiancia_simulacion.t.isin(ventana.t)]
        realizado = simular(demanda, solar[['t', 'ghi_w_m2']], config,
                            estado_inicial=estado, compromisos=planes[elegido])
        if realizado['baterias'].groupby('bateria_id').inicial_wh.first().to_dict() != estado:
            raise ValueError('Discontinuidad entre días')
        _verificar_fisica(realizado, config)
        estado = realizado['estado_final'].copy()
        realizado['candidatos'] = pd.DataFrame(dict(escenario=range(len(planes)), similares=frecuencias,
                                                   elegido=np.arange(len(planes)) == elegido, origen_utc=origen))
        realizado['planes_elegidos'] = planes[elegido].assign(origen_utc=origen)
        resultados.append(realizado)
        decisiones.append(dict(origen_utc=origen, escenario=elegido, similares=int(frecuencias[elegido]),
                               horas_ejecutadas=24))
        pronosticos.append(dict(origen_utc=origen, **prediccion))
        print(f'Beta sin MPC: {origen.tz_convert(config["zona_horaria"]).date()}', flush=True)
    return _unir_resultados(resultados, decisiones, pronosticos)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=RAIZ/'scripts/configuracion.json')
    parser.add_argument('--chronos', type=Path, default=RAIZ/'results/comparacion_mpc_chronos')
    parser.add_argument('--salida', type=Path, default=RAIZ/'results/beta_sin_mpc')
    args = parser.parse_args()
    caso = cargar_caso(args.config)
    referencia = json.loads((args.chronos/'resumen.json').read_text(encoding='utf-8'))
    for nombre, huella in referencia['archivos_sha256'].items():
        if sha256_de(args.chronos/nombre) != huella:
            raise ValueError(f'Referencia alterada: {nombre}')
    entradas, codigo = entradas_sha256(caso.rutas), codigo_sha256()
    guion = sha256_de(Path(__file__))
    for nombre, registro in entradas.items():
        if nombre != 'configuracion' and registro['sha256'] != referencia['entradas_sha256'][nombre]['sha256']:
            raise ValueError(f'Entrada distinta: {nombre}')
    config_chronos = referencia['configuracion_nueva']
    for campo in ('pv', 'baterias', 'potencia_bus_max_w', 'periodos', 'pronosticos', 'semilla_escenarios'):
        if caso.configuracion[campo] != config_chronos[campo]:
            raise ValueError(f'Condiciones distintas: {campo}')
    args.salida.mkdir(parents=True, exist_ok=False)
    comienzo = perf_counter()
    beta = ejecutar_beta_sin_mpc(caso)
    chronos = {n: pd.read_csv(args.chronos/f'{n}_nuevo.csv', float_precision='round_trip')
               for n in ('cargas', 'horas', 'baterias')}
    chronos['estado_final'] = chronos['baterias'].groupby('bateria_id').final_wh.last().to_dict()
    for n in ('cargas', 'horas', 'baterias'):
        if not beta[n][['t']].equals(chronos[n][['t']]):
            raise ValueError(f'Horas distintas en {n}')
    if not beta['cargas'][['t','carga_id','demanda_wh']].equals(chronos['cargas'][['t','carga_id','demanda_wh']]):
        raise ValueError('Demanda distinta')
    for r in (beta, chronos):
        esperado = {b['id']: b['inicial_wh'] for b in caso.configuracion['baterias']}
        if r['baterias'].groupby('bateria_id').inicial_wh.first().to_dict() != esperado:
            raise ValueError('Estados iniciales distintos')
    fisica = {n: _verificar_fisica(r, caso.configuracion) for n,r in [('beta', beta),('chronos',chronos)]}
    resultados = dict(anterior=beta, nuevo=chronos)
    diario = diario_comparativo(resultados, caso.calendario)
    metricas = dict(anterior=metricas_servicio(beta, caso.configuracion), nuevo=referencia['metricas']['nuevo'].copy())
    for nombre in metricas:
        metricas[nombre]['CVaR_diario'] = calcular_cvar(diario['perdida_ponderada_'+nombre], .9)
    mejora = 100*(metricas['nuevo']['J']/metricas['anterior']['J']-1)
    for nombre in ('cargas', 'horas', 'baterias', 'decisiones', 'candidatos', 'planes_elegidos'):
        beta[nombre].to_csv(args.salida/f'{nombre}_beta.csv', index=False)
    diario.to_csv(args.salida/'diario.csv', index=False)
    pd.DataFrame(metricas).to_csv(args.salida/'metricas.csv')
    # Reutiliza el diseño de las figuras con etiquetas específicas del experimento.
    from scripts import comparar_mpc_chronos as figuras
    figuras.ETIQUETAS.update(anterior='Beta sin MPC', nuevo='Chronos + MPC nuevo')
    graficar(args.salida/'figuras', caso, dict(diario=diario, resultados=resultados, metricas=metricas),
             titulo='Beta sin MPC / Chronos-2 + MPC nuevo (λ_R=0)',
             condiciones='Misma demanda e irradiancia; pronósticos beta y Chronos distintos')
    informe = dict(metodo='Adaptación §3.1: beta existente, 50 escenarios, plan diario modal con similitud 90% de perfiles completos por carga; sin MPC',
                   supuestos=['Beta2 y física del proyecto; no reproducción literal beta4/Volcanes',
                              '24 horas completas sin recálculo intradiario; estados continuos entre días',
                              'Frecuencia por vecindad directa, sin cierre transitivo; desempate primer escenario',
                              'Misma muestra de 50 escenarios del proyecto, sin parada adaptativa del paper'],
                   metricas=metricas, mejora_J_pct=mejora, fisica=fisica, duracion_s=perf_counter()-comienzo,
                   entradas_sha256=entradas, codigo_sha256=codigo, guion_sha256=guion, configuracion=caso.configuracion,
                   referencia_sha256=sha256_de(args.chronos/'resumen.json'),
                   archivos_sha256={str(p.relative_to(args.salida)):sha256_de(p) for p in args.salida.rglob('*') if p.is_file()})
    if entradas_sha256(caso.rutas) != entradas or codigo_sha256() != codigo:
        raise ValueError('Entradas o código cambiaron durante el experimento')
    (args.salida/'resumen.json').write_text(json.dumps(informe, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(f'Mejora de Chronos + MPC nuevo frente a beta sin MPC: {mejora:.6f}%')


if __name__ == '__main__':
    main()
