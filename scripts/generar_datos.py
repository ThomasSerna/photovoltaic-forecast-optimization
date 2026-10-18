"""Genera la demanda eléctrica SINTÉTICA y reproducible del Centro Educativo Rural El Rayo.

Requisitos: Python 3.10+, solo biblioteca estándar.
Ejecutar: python generar_datos.py
Para cambiar los supuestos, modificar configuracion.json y volver a ejecutar.

Lee configuracion.json y escribe en la carpeta de este script:
- demanda_horaria.csv, demanda_por_carga.csv y demanda_diaria.csv: demanda sintética (Wh).
- lecturas_medidor.csv e intervalos_medidor.csv: lecturas del medidor y sus diferencias.
- catalogo_cargas.csv: las cargas de configuracion.json.
- validacion.json (resumen de las comprobaciones) y sha256.json (hashes de los CSV).

Procedimiento para cada día (ver docs/IDEA.md y docs/datos.md):
1. Objetivo diario de energía. En un día lectivo se elige al azar, con reemplazo, una de
   las cuatro diferencias entre lecturas consecutivas del medidor y se multiplica por un
   factor uniforme en `variacion_lectiva`. En fin de semana el objetivo es solo la nevera.
2. La nevera consume `nevera_wh_dia` por un factor uniforme en `variacion_nevera`,
   repartido entre las 24 horas.
3. El resto del objetivo (actividad escolar) se reparte entre las franjas carga-hora de
   las cargas no permanentes, sin superar potencia × horas disponibles. Si no cabe, el
   script falla: no recorta la demanda ni añade cargas ocultas.
"""
import csv
import hashlib
import json
import random
from pathlib import Path
from statistics import mean

CARPETA = Path(__file__).resolve().parent  # contiene configuracion.json y recibe las salidas

# Supuestos fijos del código; validar_configuracion exige que configuracion.json coincida.
RESOLUCION_H = 1
CLASES_INICIO_H = 7
CLASES_FIN_H = 12.5
DIAS_LECTIVOS = [0, 1, 2, 3, 4]  # índices de DIAS_SEMANA: lunes a viernes

DIAS_SEMANA = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
HORAS_CON_CLASE = range(7, 13)  # horas locales que se cruzan con las clases de 07:00 a 12:30
ID_NEVERA = 'nevera_01'  # única carga permanente a la que se asigna energía


def validar_configuracion(config):
    """Comprueba que la configuración respeta los supuestos fijos del código."""
    assert config['resolucion_h'] == RESOLUCION_H, 'Solo se admite resolución horaria'
    assert config['clases_inicio_h'] == CLASES_INICIO_H and config['clases_fin_h'] == CLASES_FIN_H, \
        'Solo se admiten clases de 07:00 a 12:30'
    assert config['dias_lectivos'] == DIAS_LECTIVOS, 'Solo se admiten clases de lunes a viernes'
    ids = [carga['id'] for carga in config['cargas']]
    assert len(set(ids)) == len(ids), 'Hay cargas con el mismo id'
    assert 0 < config['semanas_desarrollo'] < config['semanas'], \
        'Las particiones de desarrollo y evaluación necesitan al menos una semana cada una'


def horas_solapadas(hora, inicio_h, fin_h):
    """Horas (de 0 a 1) de la franja [hora, hora + 1) que caen dentro de la ventana [inicio_h, fin_h)."""
    return max(0., min(hora + 1., fin_h) - max(float(hora), inicio_h))


def horas_disponibles(carga, hora, lectivo):
    """Horas (de 0 a 1) de la franja [hora, hora + 1) en las que la carga puede funcionar.

    Una carga permanente puede funcionar toda la hora; las demás, solo en días lectivos
    y dentro de su ventana de uso [inicio_h, fin_h).
    """
    if carga['permanente']:
        return 1.
    if not lectivo:
        return 0.
    return horas_solapadas(hora, carga['inicio_h'], carga['fin_h'])


def perfil_nevera(nevera_dia_wh, rng):
    """Reparte la energía diaria de la nevera (Wh) entre las 24 horas, en proporción a un peso.

    Supuesto fijo del código: el peso es 0.8 de noche (19:00 a 07:00) y 1 de día,
    multiplicado por un ruido uniforme en [0.9, 1.1].
    """
    pesos = [(0.8 if hora < 7 or hora >= 19 else 1.) * rng.uniform(0.9, 1.1) for hora in range(24)]
    suma_pesos = sum(pesos)
    return [nevera_dia_wh * peso / suma_pesos for peso in pesos]


def franjas_de_uso(cargas, rng):
    """Franjas carga-hora en las que las cargas no permanentes pueden funcionar un día lectivo.

    Devuelve tuplas (carga_id, hora, capacidad_wh, peso), donde:
    - capacidad_wh es la energía máxima de la franja: potencia × horas dentro de la ventana.
    - peso es la ocupación base supuesta por un ruido uniforme en [0.85, 1.15] (supuesto fijo).
    """
    franjas = []
    for carga in cargas:
        if carga['permanente']:
            continue
        for hora in range(24):
            horas = horas_solapadas(hora, carga['inicio_h'], carga['fin_h'])
            if horas > 0:
                capacidad_wh = carga['potencia_w_supuesta'] * horas
                peso = carga['ocupacion_base_supuesta'] * rng.uniform(0.85, 1.15)
                franjas.append((carga['id'], hora, capacidad_wh, peso))
    return franjas


def energia_franja(capacidad_wh, peso, factor):
    """Energía asignada a una franja (Wh): la fracción min(1, factor × peso) de su capacidad."""
    return capacidad_wh * min(1., factor * peso)


def factor_de_asignacion(franjas, objetivo_wh):
    """Factor con el que la energía asignada al conjunto de franjas suma objetivo_wh.

    La energía asignada no decrece al aumentar el factor, así que este se busca por bisección
    en [0, 100] con 90 iteraciones. Falla si el objetivo es negativo o supera la capacidad total.
    """
    capacidad_total_wh = sum(capacidad_wh for _, _, capacidad_wh, _ in franjas)
    if not 0 <= objetivo_wh <= capacidad_total_wh:
        raise ValueError('Demanda incompatible con potencias/horarios supuestos. '
                         'Revisar configuracion; no se recorta la demanda.')
    bajo, alto = 0., 100.
    for _ in range(90):
        factor = (bajo + alto) / 2
        asignada_wh = sum(energia_franja(capacidad_wh, peso, factor) for _, _, capacidad_wh, peso in franjas)
        if asignada_wh < objetivo_wh:
            bajo = factor
        else:
            alto = factor
    return factor


def energia_del_dia(cargas, lectivo, nevera_dia_wh, actividad_wh, rng):
    """Energía de cada carga en cada hora local del día (Wh) y factor de asignación usado.

    La nevera reparte nevera_dia_wh entre las 24 horas. En días lectivos, actividad_wh
    (objetivo del día menos la nevera) se reparte entre las franjas de uso de las demás cargas.
    """
    energia = {carga['id']: [0.] * 24 for carga in cargas}
    energia[ID_NEVERA] = perfil_nevera(nevera_dia_wh, rng)
    if not lectivo:
        return energia, 0.
    franjas = franjas_de_uso(cargas, rng)
    factor = factor_de_asignacion(franjas, actividad_wh)
    for carga_id, hora, capacidad_wh, peso in franjas:
        energia[carga_id][hora] = energia_franja(capacidad_wh, peso, factor)
    return energia, factor


def filas_del_dia(dia, lectivo, particion, energia, config):
    """Filas horarias y por carga de un día, y la demanda total del día (Wh).

    La energía se redondea a 6 decimales y se comprueban los límites de cada carga.
    """
    filas_horarias, filas_por_carga = [], []
    total_dia_wh = 0.
    for hora in range(24):
        t = 24 * dia + hora  # índice horario del horizonte, desde 0
        total_hora_wh = 0.
        for carga in config['cargas']:
            demanda_wh = round(energia[carga['id']][hora], 6)
            horas = horas_disponibles(carga, hora, lectivo)
            # La holgura de 1e-6 Wh absorbe el redondeo a 6 decimales.
            assert 0 <= demanda_wh <= carga['potencia_w_supuesta'] * horas + 1e-6, \
                'Demanda negativa o mayor que potencia por horas disponibles'
            assert carga['permanente'] or lectivo or demanda_wh == 0, \
                'Carga no permanente con demanda en un día no lectivo'
            assert carga['permanente'] or hora in HORAS_CON_CLASE or demanda_wh == 0, \
                'Carga no permanente con demanda fuera de clases'
            total_hora_wh += demanda_wh
            filas_por_carga.append(dict(
                t=t,
                carga_id=carga['id'],
                demanda_wh=demanda_wh,
                solicitada=int(demanda_wh > 0),
                prioridad_supuesta=carga['prioridad_supuesta'],
                potencia_w_supuesta=carga['potencia_w_supuesta'],
                horas_disponibles=horas,
                horas_uso_equivalentes=round(demanda_wh / carga['potencia_w_supuesta'], 9),
                particion=particion,
                origen='sintetico',
            ))
        total_hora_wh = round(total_hora_wh, 6)
        total_dia_wh += total_hora_wh
        nevera_hora_wh = energia[ID_NEVERA][hora]
        filas_horarias.append(dict(
            t=t,
            dia_indice=dia + 1,
            semana=dia // 7 + 1,
            dia_semana=DIAS_SEMANA[dia % 7],
            hora_local=hora,
            fecha='',
            zona_horaria=config['zona_horaria'],
            duracion_h=RESOLUCION_H,
            horas_clase=horas_solapadas(hora, CLASES_INICIO_H, CLASES_FIN_H) if lectivo else 0.,
            demanda_total_wh=total_hora_wh,
            nevera_wh=round(nevera_hora_wh, 6),
            actividad_escolar_wh=round(total_hora_wh - nevera_hora_wh, 6),
            particion=particion,
            origen='sintetico',
        ))
    return filas_horarias, filas_por_carga, total_dia_wh


def generar_demanda(config):
    """Genera la demanda sintética de todo el horizonte, con la energía en Wh.

    Devuelve tres listas de filas (diccionarios): demanda horaria total, demanda por
    carga y hora, y resumen diario.
    """
    validar_configuracion(config)
    lecturas = config['lecturas']
    # Energía entre lecturas consecutivas del medidor (Wh); son los intervalos I1 a I4.
    referencias_wh = [1000 * (fin['entero_kwh'] - inicio['entero_kwh'])
                      for inicio, fin in zip(lecturas, lecturas[1:])]
    assert len(referencias_wh) == 4 and min(referencias_wh) > 0, \
        'Se esperan 5 lecturas del medidor estrictamente crecientes'
    rng = random.Random(config['semilla'])
    cargas = config['cargas']
    horaria, por_carga, diaria = [], [], []

    for dia in range(7 * config['semanas']):
        lectivo = dia % 7 in DIAS_LECTIVOS
        particion = 'desarrollo_sintetico' if dia < 7 * config['semanas_desarrollo'] else 'evaluacion_sintetica'

        # Sorteos del día, siempre en este orden: referencia, multiplicador, nevera, 24 pesos
        # horarios de la nevera y un peso por franja. Cambiarlo altera los datos de cada semilla.
        indice = rng.randrange(len(referencias_wh)) if lectivo else None
        multiplicador = rng.uniform(*config['variacion_lectiva']) if lectivo else None
        nevera_dia_wh = config['nevera_wh_dia'] * rng.uniform(*config['variacion_nevera'])
        objetivo_wh = referencias_wh[indice] * multiplicador if lectivo else nevera_dia_wh
        energia, factor = energia_del_dia(cargas, lectivo, nevera_dia_wh, objetivo_wh - nevera_dia_wh, rng)

        filas_horarias, filas_por_carga, total_dia_wh = filas_del_dia(dia, lectivo, particion, energia, config)
        assert abs(total_dia_wh - objetivo_wh) < 0.001, 'La demanda del día no suma su objetivo'
        horaria += filas_horarias
        por_carga += filas_por_carga
        diaria.append(dict(
            dia_indice=dia + 1,
            semana=dia // 7 + 1,
            dia_semana=DIAS_SEMANA[dia % 7],
            fecha='',
            es_lectivo=int(lectivo),
            intervalo_referencia=f'I{indice + 1}' if lectivo else '',
            referencia_wh=referencias_wh[indice] if lectivo else '',
            multiplicador_lectivo=round(multiplicador, 9) if lectivo else '',
            objetivo_wh=round(objetivo_wh, 6),
            demanda_total_wh=round(total_dia_wh, 6),
            nevera_wh=round(nevera_dia_wh, 6),
            factor_asignacion=round(factor, 9),
            particion=particion,
            origen='sintetico',
        ))
    return horaria, por_carga, diaria


def filas_lecturas(lecturas):
    """Lecturas del medidor. Solo se transcribió la parte entera, así que el valor real
    está en [entero_kwh, entero_kwh + 1). Fecha, hora y décimas no están confirmadas."""
    return [dict(
        orden=orden,
        dia=lectura['dia'],
        fecha='',
        hora='',
        entero_kwh=lectura['entero_kwh'],
        decimal_kwh='',
        cota_inferior_kwh=lectura['entero_kwh'],
        cota_superior_exclusiva_kwh=lectura['entero_kwh'] + 1,
        fuente=lectura['dia'] + '.jpeg',
        estado='entero_transcrito; decimal_no_usado',
    ) for orden, lectura in enumerate(lecturas, 1)]


def filas_intervalos(lecturas):
    """Intervalos entre lecturas consecutivas (I1, I2, ...), que se suponen de 24 h.

    Como cada lectura está truncada, la diferencia real está en el intervalo abierto
    (diferencia - 1, diferencia + 1) kWh.
    """
    filas = []
    for numero, (inicio, fin) in enumerate(zip(lecturas, lecturas[1:]), 1):
        diferencia_kwh = fin['entero_kwh'] - inicio['entero_kwh']
        filas.append(dict(
            id=f'I{numero}',
            inicio=inicio['dia'],
            fin=fin['dia'],
            diferencia_enteros_kwh=diferencia_kwh,
            cota_inferior_abierta_kwh=diferencia_kwh - 1,
            cota_superior_abierta_kwh=diferencia_kwh + 1,
            duracion_real_h='',
            duracion_supuesta_h=24,
            origen='derivado; no_dia_confirmado',
        ))
    return filas


def resumen_validacion(config, horaria, por_carga, diaria):
    """Tamaños, energía generada (kWh) y comprobaciones superadas por la demanda."""
    demanda_lectiva_wh = [fila['demanda_total_wh'] for fila in diaria if fila['es_lectivo']]
    demanda_fin_semana_wh = [fila['demanda_total_wh'] for fila in diaria if not fila['es_lectivo']]
    return dict(
        estado='VALIDADO_BAJO_SUPUESTOS_PROVISIONALES',
        horas=len(horaria),
        dias=len(diaria),
        cargas_individuales=len(config['cargas']),
        filas_carga_hora=len(por_carga),
        total_kwh=round(sum(fila['demanda_total_wh'] for fila in diaria) / 1000, 6),
        media_lectiva_kwh=round(mean(demanda_lectiva_wh) / 1000, 6),
        media_fin_semana_kwh=round(mean(demanda_fin_semana_wh) / 1000, 6),
        max_error_conservacion_wh=max(abs(fila['demanda_total_wh'] - fila['objetivo_wh']) for fila in diaria),
        comprobaciones=[
            'Reproducibilidad exacta',
            'Sin huecos ni duplicados temporales',
            'Demanda no negativa',
            'Potencia y ventana de cada carga respetadas',
            'Solo nevera fuera de clases y en fin de semana',
            'Conservacion diaria con tolerancia 0.001 Wh',
            'Particiones cronologicas sinteticas',
        ],
        alcance='Validacion de demanda; no valida todavia PV, baterias ni MPC',
    )


def guardar_csv(nombre, filas):
    """Escribe las filas (diccionarios) en CARPETA/nombre; las columnas siguen el orden de sus claves."""
    with (CARPETA / nombre).open('w', encoding='utf-8', newline='') as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def main():
    """Genera la demanda, la comprueba y escribe todos los archivos de salida."""
    config = json.loads((CARPETA / 'configuracion.json').read_text(encoding='utf-8'))
    horaria, por_carga, diaria = generar_demanda(config)
    assert (horaria, por_carga, diaria) == generar_demanda(config), 'La generación no es reproducible'
    assert [fila['t'] for fila in horaria] == list(range(7 * 24 * config['semanas'])), \
        'El índice t tiene huecos o duplicados'
    assert len(por_carga) == len(horaria) * len(config['cargas']), 'Faltan o sobran filas carga-hora'

    guardar_csv('demanda_horaria.csv', horaria)
    guardar_csv('demanda_por_carga.csv', por_carga)
    guardar_csv('demanda_diaria.csv', diaria)
    guardar_csv('lecturas_medidor.csv', filas_lecturas(config['lecturas']))
    guardar_csv('intervalos_medidor.csv', filas_intervalos(config['lecturas']))
    guardar_csv('catalogo_cargas.csv', config['cargas'])

    validacion = resumen_validacion(config, horaria, por_carga, diaria)
    texto_validacion = json.dumps(validacion, ensure_ascii=False, indent=2)
    (CARPETA / 'validacion.json').write_text(texto_validacion + '\n', encoding='utf-8')
    # Los hashes se calculan cuando ya están escritos todos los CSV de la carpeta.
    hashes = {ruta.name: hashlib.sha256(ruta.read_bytes()).hexdigest()
              for ruta in sorted(CARPETA.glob('*.csv'))}
    (CARPETA / 'sha256.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    print(texto_validacion)


if __name__ == '__main__':
    main()
