"""Clausifier 2026-11-08: verbos modales/auxiliares y verbo finito interno."""
import motor_haiku as mh

TIT = "Puede agendar un turno para esterilizar a su mascota"
CTX = ("La jornada de esterilización se realizará del 9 al 13 de noviembre en la Unidad de "
       "Cuidado Animal de Engativá. Las personas que tienen perros y gatos en Bogotá podrán "
       "acceder a nuevas jornadas de esterilización gratuita en noviembre.")


def test_rechaza_modales_y_conjugados():
    for s in ("Puede agendar un turno", "Podrán acceder a jornadas", "Habilitó nuevos turnos",
              "Debe pagar el impuesto predial", "Hay nuevas jornadas gratuitas",
              "Será la nueva sede distrital", "Se abren inscripciones para cursos"):
        assert mh.empieza_con_verbo(s), s
        assert mh.problemas_subtema(s, TIT, CTX), s


def test_acepta_nominal_especifico():
    assert mh.problemas_subtema("Jornada de esterilización gratuita de mascotas en Engativá",
                                TIT, CTX) == []


def test_sustantivos_no_son_verbo():
    for s in ("Poder judicial y reforma", "Deber de cuidado ciudadano", "Mar de Coveñas",
              "Lugar del accidente vial", "Hogar de paso animal", "Mujer víctima de violencia",
              "Cáncer infantil en Bogotá", "Bienestar de la población migrante",
              "Popular apoyo al alcalde", "Nueva era de movilidad"):
        assert not mh.empieza_con_verbo(s), s


def test_verbo_finito_interno():
    assert mh.contiene_verbo_finito("Presunto hacker tenía información inteligencia") == "tenía"
    assert mh.problemas_subtema("Presunto hacker tenía información inteligencia")
    for s in ("Presunto hacker con información de inteligencia",
              "Policía de Bogotá y la seguridad", "Alcaldía de Bogotá en Engativá",
              "Esta semana de vacunación canina", "Secretaría General y su gestión"):
        assert mh.contiene_verbo_finito(s) is None, s
