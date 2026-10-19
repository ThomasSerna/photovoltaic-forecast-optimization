"""Pruebas del modelo de demanda con casos pequeños que se verifican a mano.

Las fórmulas y la numeración (D1)-(D5) están en docs/datos.md, sección «Modelo de la demanda sintética».
"""

from pathlib import Path

import pytest

from energy_project.configuracion import cargar_configuracion
from energy_project.demanda import (
    Franja, ObjetivoDia, energia_franja, factor_de_asignacion, generar_demanda, horas_solapadas,
    perfil_nevera, referencias_del_medidor, sortear_objetivo,
)


class Grabador:
    """Generador aleatorio de prueba: devuelve el punto medio de cada rango y registra cada sorteo."""

    def __init__(self):
        self.llamadas = []

    def randrange(self, n):
        self.llamadas.append(("randrange", n))
        return 1

    def uniform(self, bajo, alto):
        self.llamadas.append(("uniform", bajo, alto))
        return (bajo + alto) / 2


@pytest.mark.parametrize(("hora", "inicio_h", "fin_h", "esperado"), [
    (9, 9, 12.5, 1.0),  # hora completa dentro de la ventana
    (12, 9, 12.5, 0.5),  # la ventana termina a las 12:30: min(13, 12.5) - max(12, 9) = 0.5
    (8, 9, 12.5, 0.0),  # antes de la ventana
    (13, 9, 12.5, 0.0),  # después de la ventana
    (10, 10, 10.25, 0.25),  # ventana de 15 minutos, como la licuadora
])
def test_horas_solapadas_d3(hora, inicio_h, fin_h, esperado):
    assert horas_solapadas(hora, inicio_h, fin_h) == esperado


def test_perfil_nevera_sin_ruido_d2():
    """Sin ruido, g_h vale 0.8 de 19:00 a 07:00 (12 h) y 1 de día (12 h): suma 12·0.8 + 12·1 = 21.6.

    Con 2160 Wh al día, cada hora nocturna recibe 2160·0.8/21.6 = 80 Wh y cada hora diurna, 100 Wh.
    """
    rng = Grabador()
    perfil = perfil_nevera(2160., rng)
    noche = [0, 1, 2, 3, 4, 5, 6, 19, 20, 21, 22, 23]
    assert [perfil[hora] for hora in noche] == pytest.approx([80.] * 12)
    assert [perfil[hora] for hora in range(7, 19)] == pytest.approx([100.] * 12)
    assert sum(perfil) == pytest.approx(2160.)
    assert rng.llamadas == [("uniform", 0.9, 1.1)] * 24  # un sorteo por hora


def test_factor_de_asignacion_d4():
    """D(κ) = 100·min(1, κ·1) + 200·min(1, κ·0.5), con dos franjas de ω = 1 y ω = 0.5.

    Para κ ≤ 1, D = 200κ: el objetivo 150 da κ = 0.75 (75 + 75 Wh).
    Para 1 ≤ κ ≤ 2, D = 100 + 100κ: el objetivo 250 da κ = 1.5. En κ = 2 se satura toda la capacidad (300 Wh).
    """
    franjas = [Franja("a", 8, 100., 1.), Franja("b", 9, 200., 0.5)]
    assert factor_de_asignacion(franjas, 150.) == pytest.approx(0.75)
    assert factor_de_asignacion(franjas, 250.) == pytest.approx(1.5)
    assert factor_de_asignacion(franjas, 300.) == pytest.approx(2.)
    assert [energia_franja(franja, 0.75) for franja in franjas] == pytest.approx([75., 75.])
    for objetivo_wh in (-1., 300.01):  # negativo o por encima de la capacidad: no se recorta, se rechaza
        with pytest.raises(ValueError, match="Demanda incompatible"):
            factor_de_asignacion(franjas, objetivo_wh)


@pytest.mark.parametrize(("objetivo_wh", "factor_esperado"), [(50., 500.), (100., 1000.)])
def test_factor_de_asignacion_amplia_cota_para_pesos_pequenos(objetivo_wh, factor_esperado):
    """D(κ) = 100·min(1, κ·0.001): 50 Wh exige κ=500 y 100 Wh exige κ=1000."""
    franja = Franja("a", 8, 100., 0.001)
    factor = factor_de_asignacion([franja], objetivo_wh)
    assert factor == pytest.approx(factor_esperado)
    assert energia_franja(franja, factor) == pytest.approx(objetivo_wh)


def test_factor_de_asignacion_sin_demanda():
    assert factor_de_asignacion([], 0.) == 0.
    assert factor_de_asignacion([Franja("a", 8, 100., 0.001)], 0.) == 0.


def test_factor_de_asignacion_con_pesos_de_distintas_magnitudes():
    franjas = [Franja("a", 8, 100., 0.001), Franja("b", 8, 100., 1e-100)]
    factor = factor_de_asignacion(franjas, 50.)
    assert factor == pytest.approx(500.)
    assert sum(energia_franja(franja, factor) for franja in franjas) == pytest.approx(50.)


def test_factor_de_asignacion_cerca_del_limite_numerico():
    franja = Franja("a", 8, 100., 5e-309)
    factor = factor_de_asignacion([franja], 50.)
    assert factor == pytest.approx(1e308)
    assert energia_franja(franja, factor) == pytest.approx(50.)
    with pytest.raises(ValueError, match="rango numérico"):
        factor_de_asignacion([Franja("a", 8, 100., 1e-320)], 50.)


def test_demanda_con_ocupaciones_pequenas_conserva_objetivo():
    config = cargar_configuracion(Path(__file__).resolve().parents[1] / "scripts/configuracion.json")
    for carga in config["cargas"]:
        if not carga["permanente"]:
            carga["ocupacion_base_supuesta"] = 0.001
    _, _, diaria = generar_demanda(config)
    assert any(fila["factor_asignacion"] > 100 for fila in diaria)
    for fila in diaria:
        assert abs(fila["demanda_total_wh"] - fila["objetivo_wh"]) <= 0.001


def test_objetivo_del_dia_d1_y_orden_de_sorteos():
    """Con el punto medio de cada rango (m_d = u_d = 1) y la referencia i_d = 2 (la segunda):

    - Las lecturas 36075, 36082, 36087, 36096 y 36103 kWh dan D^ref = 1000·(7, 5, 9, 7) = 7000, 5000, 9000, 7000 Wh.
    - Día lectivo: D^nev = 1800·1 = 1800 Wh y D_d = 5000·1 = 5000 Wh. Sorteos: i_d, m_d y u_d, en ese orden.
    - Fin de semana: solo se sortea u_d y D_d = D^nev = 1800 Wh.
    """
    lecturas = [{"entero_kwh": valor} for valor in (36075, 36082, 36087, 36096, 36103)]
    config = {"variacion_lectiva": [0.95, 1.05], "variacion_nevera": [0.9, 1.1], "nevera_wh_dia": 1800}
    referencias_wh = referencias_del_medidor(lecturas)
    assert referencias_wh == [7000, 5000, 9000, 7000]

    lectivo = Grabador()
    assert sortear_objetivo(True, referencias_wh, config, lectivo) == ObjetivoDia(
        indice=1, referencia_wh=5000, multiplicador=1.0, nevera_wh=1800.0, total_wh=5000.0)
    assert lectivo.llamadas == [("randrange", 4), ("uniform", 0.95, 1.05), ("uniform", 0.9, 1.1)]

    fin_de_semana = Grabador()
    assert sortear_objetivo(False, referencias_wh, config, fin_de_semana) == ObjetivoDia(
        indice=None, referencia_wh=None, multiplicador=None, nevera_wh=1800.0, total_wh=1800.0)
    assert fin_de_semana.llamadas == [("uniform", 0.9, 1.1)]
