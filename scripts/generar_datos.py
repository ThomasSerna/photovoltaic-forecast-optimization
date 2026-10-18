"""Demanda SINTETICA reproducible. Python 3.10+, biblioteca estandar.
Ejecutar: python generar_datos.py
Modificar configuracion.json y volver a ejecutar para cambiar los supuestos.
"""
import csv
import hashlib
import json
import random
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent


def guardar(nombre, filas):
    with (ROOT / nombre).open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)


def solape(h, inicio, fin):
    return max(0., min(h + 1., fin) - max(float(h), inicio))


def generar(c):
    assert c['resolucion_h'] == 1 and c['clases_inicio_h'] == 7 and c['clases_fin_h'] == 12.5
    assert c['dias_lectivos'] == [0, 1, 2, 3, 4]
    rng = random.Random(c['semilla'])
    cargas = c['cargas']
    assert len({x['id'] for x in cargas}) == len(cargas)
    assert 0 < c['semanas_desarrollo'] < c['semanas']
    referencias = [1000 * (b['entero_kwh'] - a['entero_kwh']) for a, b in zip(c['lecturas'], c['lecturas'][1:])]
    assert len(referencias) == 4 and min(referencias) > 0
    dias = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
    horaria, detalle, diaria = [], [], []
    for d in range(7 * c['semanas']):
        lectivo = d % 7 < 5
        particion = 'desarrollo_sintetico' if d < c['semanas_desarrollo'] * 7 else 'evaluacion_sintetica'
        indice = rng.randrange(4) if lectivo else None
        factor_dia = rng.uniform(*c['variacion_lectiva']) if lectivo else None
        nevera = c['nevera_wh_dia'] * rng.uniform(*c['variacion_nevera'])
        objetivo = referencias[indice] * factor_dia if lectivo else nevera
        energia = {x['id']: [0.] * 24 for x in cargas}
        pesos_frio = [(0.8 if h < 7 or h >= 19 else 1.) * rng.uniform(0.9, 1.1) for h in range(24)]
        energia['nevera_01'] = [nevera * p / sum(pesos_frio) for p in pesos_frio]
        posiciones, capacidades, pesos = [], [], []
        for carga in cargas:
            if carga['permanente']:
                continue
            for h in range(24):
                horas = solape(h, carga['inicio_h'], carga['fin_h']) if lectivo else 0.
                if horas:
                    posiciones.append((carga['id'], h))
                    capacidades.append(carga['potencia_w_supuesta'] * horas)
                    pesos.append(carga['ocupacion_base_supuesta'] * rng.uniform(0.85, 1.15))
        factor = 0.
        if lectivo:
            activa = objetivo - nevera
            if not 0 <= activa <= sum(capacidades):
                raise ValueError('Demanda incompatible con potencias/horarios supuestos. Revisar configuracion; no se recorta la demanda.')
            bajo, alto = 0., 100.
            for _ in range(90):
                factor = (bajo + alto) / 2
                if sum(cap * min(1., factor * p) for cap, p in zip(capacidades, pesos)) < activa:
                    bajo = factor
                else:
                    alto = factor
            for (id_carga, h), cap, p in zip(posiciones, capacidades, pesos):
                energia[id_carga][h] = cap * min(1., factor * p)
        total_dia = 0.
        for h in range(24):
            t = d * 24 + h
            total_h = 0.
            for carga in cargas:
                valor = round(energia[carga['id']][h], 6)
                horas = 1. if carga['permanente'] else (solape(h, carga['inicio_h'], carga['fin_h']) if lectivo else 0.)
                assert 0 <= valor <= carga['potencia_w_supuesta'] * horas + 1e-6
                assert carga['permanente'] or lectivo or valor == 0
                assert carga['permanente'] or h in range(7, 13) or valor == 0
                total_h += valor
                detalle.append(dict(t=t, carga_id=carga['id'], demanda_wh=valor, solicitada=int(valor > 0),
                                    prioridad_supuesta=carga['prioridad_supuesta'],
                                    potencia_w_supuesta=carga['potencia_w_supuesta'], horas_disponibles=horas,
                                    horas_uso_equivalentes=round(valor / carga['potencia_w_supuesta'], 9),
                                    particion=particion, origen='sintetico'))
            total_h = round(total_h, 6)
            total_dia += total_h
            horaria.append(dict(t=t, dia_indice=d + 1, semana=d // 7 + 1, dia_semana=dias[d % 7],
                                hora_local=h, fecha='', zona_horaria=c['zona_horaria'], duracion_h=1,
                                horas_clase=solape(h, 7, 12.5) if lectivo else 0.,
                                demanda_total_wh=total_h, nevera_wh=round(energia['nevera_01'][h], 6),
                                actividad_escolar_wh=round(total_h - energia['nevera_01'][h], 6),
                                particion=particion, origen='sintetico'))
        assert abs(total_dia - objetivo) < 0.001
        diaria.append(dict(dia_indice=d + 1, semana=d // 7 + 1, dia_semana=dias[d % 7], fecha='',
                           es_lectivo=int(lectivo), intervalo_referencia=f'I{indice + 1}' if lectivo else '',
                           referencia_wh=referencias[indice] if lectivo else '',
                           multiplicador_lectivo=round(factor_dia, 9) if lectivo else '',
                           objetivo_wh=round(objetivo, 6), demanda_total_wh=round(total_dia, 6),
                           nevera_wh=round(nevera, 6), factor_asignacion=round(factor, 9),
                           particion=particion, origen='sintetico'))
    return horaria, detalle, diaria


def main():
    c = json.loads((ROOT / 'configuracion.json').read_text(encoding='utf-8'))
    horaria, detalle, diaria = generar(c)
    assert (horaria, detalle, diaria) == generar(c)
    assert [r['t'] for r in horaria] == list(range(c['semanas'] * 168))
    assert len(detalle) == len(horaria) * len(c['cargas'])
    for nombre, filas in [('demanda_horaria.csv', horaria), ('demanda_por_carga.csv', detalle), ('demanda_diaria.csv', diaria)]:
        guardar(nombre, filas)
    lecturas = [dict(orden=i + 1, dia=r['dia'], fecha='', hora='', entero_kwh=r['entero_kwh'], decimal_kwh='',
                     cota_inferior_kwh=r['entero_kwh'], cota_superior_exclusiva_kwh=r['entero_kwh'] + 1,
                     fuente=r['dia'] + '.jpeg', estado='entero_transcrito; decimal_no_usado') for i, r in enumerate(c['lecturas'])]
    intervalos = []
    for i, (a, b) in enumerate(zip(lecturas, lecturas[1:]), 1):
        delta = b['entero_kwh'] - a['entero_kwh']
        intervalos.append(dict(id=f'I{i}', inicio=a['dia'], fin=b['dia'], diferencia_enteros_kwh=delta,
                              cota_inferior_abierta_kwh=delta - 1, cota_superior_abierta_kwh=delta + 1,
                              duracion_real_h='', duracion_supuesta_h=24, origen='derivado; no_dia_confirmado'))
    guardar('lecturas_medidor.csv', lecturas)
    guardar('intervalos_medidor.csv', intervalos)
    guardar('catalogo_cargas.csv', c['cargas'])
    validacion = dict(estado='VALIDADO_BAJO_SUPUESTOS_PROVISIONALES', horas=len(horaria), dias=len(diaria),
                      cargas_individuales=len(c['cargas']), filas_carga_hora=len(detalle),
                      total_kwh=round(sum(r['demanda_total_wh'] for r in diaria) / 1000, 6),
                      media_lectiva_kwh=round(mean(r['demanda_total_wh'] for r in diaria if r['es_lectivo']) / 1000, 6),
                      media_fin_semana_kwh=round(mean(r['demanda_total_wh'] for r in diaria if not r['es_lectivo']) / 1000, 6),
                      max_error_conservacion_wh=max(abs(r['demanda_total_wh'] - r['objetivo_wh']) for r in diaria),
                      comprobaciones=['Reproducibilidad exacta', 'Sin huecos ni duplicados temporales',
                                      'Demanda no negativa', 'Potencia y ventana de cada carga respetadas',
                                      'Solo nevera fuera de clases y en fin de semana',
                                      'Conservacion diaria con tolerancia 0.001 Wh', 'Particiones cronologicas sinteticas'],
                      alcance='Validacion de demanda; no valida todavia PV, baterias ni MPC')
    (ROOT / 'validacion.json').write_text(json.dumps(validacion, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.glob('*.csv'))}
    (ROOT / 'sha256.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(validacion, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
