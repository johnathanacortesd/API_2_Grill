"""Portado de Secretaría v1.44: el mismo rótulo con otras mayúsculas o tildes
es un solo subtema (versión general, sin la regla del «en Bogotá»)."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import analyzer_tono_tema as az


def test_mayusculas_y_tildes_un_solo_subtema():
    rows = [{'Subtema_IA': 'Declaratoria alerta ambiental Fase 1 suroccidente'},
            {'Subtema_IA': 'Declaratoria alerta ambiental Fase 1 suroccidente'},
            {'Subtema_IA': 'Declaratoria alerta ambiental fase 1 suroccidente'},
            {'Subtema_IA': 'Bar Week 2026 en Bogotá'},
            {'Subtema_IA': '-', 'is_duplicate': True}]
    assert az.unificar_forma_subtemas(rows) == 1
    assert rows[2]['Subtema_IA'] == 'Declaratoria alerta ambiental Fase 1 suroccidente'
    assert rows[3]['Subtema_IA'] == 'Bar Week 2026 en Bogotá'   # general: no se recorta
    assert rows[4]['Subtema_IA'] == '-'


def test_forma_manual_gana():
    rows = [{'Subtema_IA': 'Alerta fase 1'}, {'Subtema_IA': 'Alerta fase 1'},
            {'Subtema_IA': 'Alerta Fase 1', '_correccion_manual': True}]
    az.unificar_forma_subtemas(rows)
    assert {r['Subtema_IA'] for r in rows} == {'Alerta Fase 1'}


def test_rotulos_distintos_no_se_tocan():
    rows = [{'Subtema_IA': 'Obras en la calle 13'}, {'Subtema_IA': 'Obras en la calle 26'}]
    assert az.unificar_forma_subtemas(rows) == 0
