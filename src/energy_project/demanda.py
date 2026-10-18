"""Genera la demanda eléctrica SINTÉTICA y reproducible del Centro Educativo Rural El Rayo.

Ejecutar desde la raíz: uv run python scripts/generar_datos.py
Para cambiar los supuestos, modificar scripts/configuracion.json.

Lee la configuración indicada y escribe en data/ por defecto:
- demanda_horaria.csv, demanda_por_carga.csv y demanda_diaria.csv: demanda sintética (Wh).
- lecturas_medidor.csv e intervalos_medidor.csv: lecturas del medidor y sus diferencias.
- catalogo_cargas.csv: las cargas de configuracion.json.
- calendario_simulacion.csv e irradiancia_simulacion.csv: alineación con el histórico local.
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
import argparse
import csv
import hashlib
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    try:
        _validar_configuracion(config)
    except (KeyError, TypeError, ZoneInfoNotFoundError) as error:
        raise ValueError(f'configuracion: campo faltante o inválido: {error}') from error


def numero_finito(valor, campo, minimo=0, estricto=True):
    """Rechaza valores no numéricos, infinitos y fuera del dominio declarado."""
    if (isinstance(valor, bool) or not isinstance(valor, (int, float))
            or not math.isfinite(valor) or (valor <= minimo if estricto else valor < minimo)):
        comparacion = 'mayor que' if estricto else 'mayor o igual que'
        raise ValueError(f'configuracion: {campo} debe ser finito y {comparacion} {minimo}; recibido {valor!r}')


def _validar_configuracion(config):
    if config['resolucion_h'] != RESOLUCION_H or isinstance(config['resolucion_h'], bool):
        raise ValueError('configuracion: resolucion_h: solo se admite resolución horaria (1 h)')
    if config['clases_inicio_h'] != CLASES_INICIO_H or config['clases_fin_h'] != CLASES_FIN_H:
        raise ValueError('configuracion: clases_inicio_h/clases_fin_h: solo se admiten clases de 07:00 a 12:30')
    if (config['dias_lectivos'] != DIAS_LECTIVOS
            or any(type(dia) is not int for dia in config['dias_lectivos'])):
        raise ValueError('configuracion: dias_lectivos: solo se admiten clases de lunes a viernes')
    for campo in ('semanas', 'semanas_desarrollo', 'semilla'):
        if type(config[campo]) is not int or config[campo] < 0:
            raise ValueError(f'configuracion: {campo} debe ser un entero no negativo')
    if not 0 < config['semanas_desarrollo'] < config['semanas']:
        raise ValueError('configuracion: semanas_desarrollo/semanas: cada partición necesita al menos una semana')
    numero_finito(config['nevera_wh_dia'], 'nevera_wh_dia')
    for campo in ('variacion_lectiva', 'variacion_nevera'):
        valores = config[campo]
        if not isinstance(valores, list) or len(valores) != 2:
            raise ValueError(f'configuracion: {campo} debe contener dos límites')
        for valor in valores:
            numero_finito(valor, campo)
        if valores[0] > valores[1]:
            raise ValueError(f'configuracion: {campo} tiene los límites invertidos')

    cargas = config['cargas']
    if not isinstance(cargas, list) or not cargas:
        raise ValueError('configuracion: cargas debe ser una lista no vacía')
    ids = [carga['id'] for carga in cargas]
    if any(not isinstance(id_, str) or not id_.strip() for id_ in ids) or len(set(ids)) != len(ids):
        raise ValueError('configuracion: cargas.id debe ser texto no vacío y único')
    if [carga['id'] for carga in cargas if carga['permanente']] != [ID_NEVERA]:
        raise ValueError(f'configuracion: cargas.permanente: solo {ID_NEVERA} puede ser permanente')
    for carga in cargas:
        campo = f'cargas[{carga["id"]}]'
        if type(carga['cantidad']) is not int or carga['cantidad'] != 1:
            raise ValueError(f'configuracion: {campo}.cantidad debe ser 1: catálogo de equipos individuales')
        if type(carga['permanente']) is not bool:
            raise ValueError(f'configuracion: {campo}.permanente debe ser booleano')
        for atributo in ('potencia_w_supuesta', 'ocupacion_base_supuesta'):
            numero_finito(carga[atributo], f'{campo}.{atributo}')
        if carga['ocupacion_base_supuesta'] > 1:
            raise ValueError(f'configuracion: {campo}.ocupacion_base_supuesta debe ser <= 1')
        if type(carga['prioridad_supuesta']) is not int or carga['prioridad_supuesta'] <= 0:
            raise ValueError(f'configuracion: {campo}.prioridad_supuesta debe ser un entero positivo')
        numero_finito(carga['inicio_h'], f'{campo}.inicio_h', estricto=False)
        numero_finito(carga['fin_h'], f'{campo}.fin_h')
        if not carga['inicio_h'] < carga['fin_h'] <= 24:
            raise ValueError(f'configuracion: {campo}: ventana inválida [inicio_h, fin_h)')
        if carga['permanente']:
            if carga['inicio_h'] != 0 or carga['fin_h'] != 24:
                raise ValueError(f'configuracion: {campo}: la nevera debe estar disponible las 24 h')
        elif not CLASES_INICIO_H <= carga['inicio_h'] < carga['fin_h'] <= CLASES_FIN_H:
            raise ValueError(f'configuracion: {campo}: ventana fuera de clases de 07:00 a 12:30')

    lecturas = config['lecturas']
    if not isinstance(lecturas, list) or len(lecturas) != 5:
        raise ValueError('configuracion: lecturas: se esperan 5 lecturas del medidor')
    for indice, lectura in enumerate(lecturas):
        if lectura['dia'] != DIAS_SEMANA[indice]:
            raise ValueError(f'configuracion: lecturas[{indice}].dia debe ser {DIAS_SEMANA[indice]}')
        valor = lectura['entero_kwh']
        if type(valor) is not int or valor < 0:
            raise ValueError(f'configuracion: lecturas[{indice}].entero_kwh debe ser un entero no negativo')
        if indice and valor <= lecturas[indice - 1]['entero_kwh']:
            raise ValueError(f'configuracion: lecturas[{indice}].entero_kwh debe ser estrictamente creciente')

    try:
        inicio_fecha = date.fromisoformat(config['fecha_inicio_sintetica'])
    except (TypeError, ValueError) as error:
        raise ValueError('configuracion: fecha_inicio_sintetica debe ser una fecha ISO (YYYY-MM-DD)') from error
    if inicio_fecha.weekday() != 0:
        raise ValueError('configuracion: fecha_inicio_sintetica debe ser lunes')
    zona = ZoneInfo(config['zona_horaria'])
    periodos = []
    for nombre in ('historial_previo', 'desarrollo', 'evaluacion', 'reserva_no_usada'):
        fechas = []
        for limite in ('inicio', 'fin'):
            try:
                fecha = datetime.fromisoformat(config['periodos'][nombre][limite])
            except ValueError as error:
                raise ValueError(f'configuracion: periodos.{nombre}.{limite} debe ser ISO con zona horaria') from error
            if fecha.tzinfo is None or fecha.utcoffset() != fecha.astimezone(zona).utcoffset():
                raise ValueError(f'configuracion: periodos.{nombre}.{limite} debe usar la zona {zona}')
            if fecha.hour or fecha.minute or fecha.second or fecha.microsecond:
                raise ValueError(f'configuracion: periodos.{nombre}.{limite} debe ser medianoche local')
            fechas.append(fecha)
        if fechas[0] >= fechas[1] or (periodos and periodos[-1][1] != fechas[0]):
            raise ValueError(f'configuracion: periodos.{nombre}: los periodos deben ser positivos y contiguos')
        periodos.append(fechas)
    inicio = datetime.combine(inicio_fecha, datetime.min.time(), zona)
    if (periodos[1][0] != inicio
            or periodos[1][1] != inicio + timedelta(weeks=config['semanas_desarrollo'])
            or periodos[2][1] != inicio + timedelta(weeks=config['semanas'])):
        raise ValueError('configuracion: periodos desarrollo/evaluacion no coinciden con fecha_inicio_sintetica y semanas')


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
            if not 0 <= demanda_wh <= carga['potencia_w_supuesta'] * horas + 1e-6:
                raise ValueError(f'demanda: t={t}, carga={carga["id"]}: {demanda_wh} Wh fuera de potencia × horas disponibles')
            if not carga['permanente'] and demanda_wh and (not lectivo or hora not in HORAS_CON_CLASE):
                raise ValueError(f'demanda: t={t}, carga={carga["id"]}: demanda fuera del horario lectivo')
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
            fecha=(date.fromisoformat(config['fecha_inicio_sintetica']) + timedelta(days=dia)).isoformat(),
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
        if abs(total_dia_wh - objetivo_wh) > 0.001:
            raise ValueError(f'demanda: día {dia + 1}: total {total_dia_wh} Wh no conserva objetivo {objetivo_wh} Wh (tolerancia 0.001 Wh)')
        horaria += filas_horarias
        por_carga += filas_por_carga
        diaria.append(dict(
            dia_indice=dia + 1,
            semana=dia // 7 + 1,
            dia_semana=DIAS_SEMANA[dia % 7],
            fecha=(date.fromisoformat(config['fecha_inicio_sintetica']) + timedelta(days=dia)).isoformat(),
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


def generar_calendario(config):
    """Asigna fechas locales a t; no representa las fechas desconocidas del medidor."""
    inicio = datetime.combine(date.fromisoformat(config['fecha_inicio_sintetica']),
                              datetime.min.time(), ZoneInfo(config['zona_horaria']))
    filas = []
    for t in range(config['semanas'] * 7 * 24):
        fecha = inicio + timedelta(hours=t)
        filas.append(dict(
            t=t,
            dia_indice=t // 24 + 1,
            semana=t // (24 * 7) + 1,
            dia_semana=DIAS_SEMANA[fecha.weekday()],
            hora_local=fecha.hour,
            inicio_intervalo=fecha.isoformat(),
            fin_intervalo=(fecha + timedelta(hours=1)).isoformat(),
            fecha_asignada_simulacion=fecha.date().isoformat(),
            particion_demanda=('desarrollo_sintetico' if t < config['semanas_desarrollo'] * 7 * 24
                               else 'evaluacion_sintetica'),
            origen_calendario='asignado_para_simulacion_no_fecha_del_medidor',
        ))
    return filas


def subconjunto_solar(ruta, config, calendario):
    """Selecciona el histórico ya normalizado, sin volver a desplazar sus intervalos."""
    with ruta.open(encoding='utf-8-sig', newline='') as archivo:
        lector = csv.DictReader(archivo)
        columnas = ['inicio_intervalo', 'fin_intervalo', 'inicio_utc', 'fin_utc', 'duracion_h',
                    'ghi_w_m2', 'irradiacion_wh_m2', 'fuente', 'modelo', 'particion']
        if lector.fieldnames != columnas:
            raise ValueError(f'{ruta}: columnas esperadas {columnas}; recibidas {lector.fieldnames}')
        historico = list(lector)
    limites = [(nombre, datetime.fromisoformat(periodo['inicio']), datetime.fromisoformat(periodo['fin']))
               for nombre, periodo in config['periodos'].items()]
    esperado = datetime.fromisoformat(config['periodos']['historial_previo']['inicio'])
    fin_historico = datetime.fromisoformat(config['periodos']['reserva_no_usada']['fin'])
    por_inicio = {}
    zona = ZoneInfo(config['zona_horaria'])
    for numero, fila in enumerate(historico, 2):
        try:
            inicio = datetime.fromisoformat(fila['inicio_intervalo'])
            fin = datetime.fromisoformat(fila['fin_intervalo'])
            inicio_utc = datetime.fromisoformat(fila['inicio_utc'])
            fin_utc = datetime.fromisoformat(fila['fin_utc'])
            duracion, ghi, irradiacion = (float(fila[campo]) for campo in
                                          ('duracion_h', 'ghi_w_m2', 'irradiacion_wh_m2'))
        except (TypeError, ValueError) as error:
            raise ValueError(f'{ruta}: fila {numero}: fecha o valor solar inválido') from error
        if any(fecha.tzinfo is None for fecha in (inicio, fin, inicio_utc, fin_utc)):
            raise ValueError(f'{ruta}: fila {numero}: fechas sin zona horaria')
        if inicio != esperado or inicio >= fin_historico or fin - inicio != timedelta(hours=1):
            raise ValueError(f'{ruta}: fila {numero}: intervalo duplicado, faltante o fuera de periodo; esperado {esperado.isoformat()}')
        if (inicio != inicio_utc or fin != fin_utc
                or inicio.utcoffset() != inicio.astimezone(zona).utcoffset()
                or fin.utcoffset() != fin.astimezone(zona).utcoffset()
                or inicio_utc.utcoffset() != timedelta(0) or fin_utc.utcoffset() != timedelta(0)):
            raise ValueError(f'{ruta}: fila {numero}: tiempos UTC/locales incoherentes')
        if (not all(math.isfinite(valor) for valor in (duracion, ghi, irradiacion))
                or duracion != 1 or ghi < 0 or irradiacion < 0
                or not math.isclose(irradiacion, ghi, rel_tol=0, abs_tol=1e-6)):
            raise ValueError(f'{ruta}: fila {numero}: duración o irradiancia inválida (W/m² y Wh/m²)')
        particion = next((nombre for nombre, desde, hasta in limites if desde <= inicio < hasta), None)
        if (fila['particion'] != particion or fila['fuente'] != config['irradiancia']['fuente']
                or fila['modelo'] != config['irradiancia']['modelo']):
            raise ValueError(f'{ruta}: fila {numero}: fuente, modelo o partición incoherente')
        por_inicio[inicio] = fila
        esperado = fin
    if esperado != fin_historico:
        raise ValueError(f'{ruta}: histórico incompleto: faltan intervalos desde {esperado.isoformat()}')
    return [dict(t=fila['t'], **por_inicio[datetime.fromisoformat(fila['inicio_intervalo'])])
            for fila in calendario]


def guardar_csv(ruta, filas):
    """Escribe filas con el orden de columnas de sus claves."""
    with ruta.open('w', encoding='utf-8', newline='') as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def generar_archivos(config_path, salida=None):
    """Regenera el caso en la carpeta elegida y devuelve su resumen de validación."""
    from .configuracion import cargar_configuracion

    config_path = Path(config_path).resolve()
    config = cargar_configuracion(config_path)
    horaria, por_carga, diaria = generar_demanda(config)
    calendario = generar_calendario(config)
    rutas = {clave: (config_path.parent / valor).resolve() for clave, valor in config['datos'].items()}
    destino = Path(salida).resolve() if salida is not None else rutas['demanda'].parent
    nombres = {clave: ruta.name for clave, ruta in rutas.items()}
    if len(set(nombres.values())) != len(nombres):
        raise ValueError(f'{config_path}: datos: los nombres de los archivos deben ser distintos')
    solar = subconjunto_solar(rutas['irradiancia_historica'], config, calendario)
    contenido = dict(horaria=horaria, demanda=por_carga, diaria=diaria, catalogo=config['cargas'],
                     lecturas=filas_lecturas(config['lecturas']), intervalos=filas_intervalos(config['lecturas']),
                     calendario=calendario, irradiancia_simulacion=solar)
    salidas = {clave: destino / nombres[clave] if salida is not None else ruta
               for clave, ruta in rutas.items()}
    for clave, filas in contenido.items():
        salidas[clave].parent.mkdir(parents=True, exist_ok=True)
        guardar_csv(salidas[clave], filas)
    ruta_historica = salidas['irradiancia_historica']
    if ruta_historica != rutas['irradiancia_historica']:
        ruta_historica.write_bytes(rutas['irradiancia_historica'].read_bytes())
    validacion = resumen_validacion(config, horaria, por_carga, diaria)
    texto_validacion = json.dumps(validacion, ensure_ascii=False, indent=2)
    (destino / 'validacion.json').write_text(texto_validacion + '\n', encoding='utf-8')
    hashes = {ruta.name: hashlib.sha256(ruta.read_bytes()).hexdigest()
              for ruta in sorted(salidas.values())}
    (destino / 'sha256.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    return validacion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('scripts/configuracion.json'))
    parser.add_argument('--salida', type=Path, help='Carpeta de salida; por defecto, carpeta de datos configurada')
    args = parser.parse_args()
    try:
        resumen = generar_archivos(args.config, args.salida)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Error: {error}\n')
    print(json.dumps(resumen, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
