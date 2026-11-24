# -*- coding: utf-8 -*-
"""v4.86: el auditor de tono y el evaluador de agrupación también corren con
OpenAI Luna (mismo prompt, mismos lotes y misma validación que con Claude
Haiku), con llamadas a la API de OpenAI en vez del SDK de Claude."""
import json
import os
import re
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import analyzer_tono_tema as az  # noqa: E402
import evaluador_agrupacion as ev  # noqa: E402
import motor_haiku as mh  # noqa: E402

CFG = {'brand': 'Marca', 'aliases': [], 'voceros': ['Ana Gómez'], 'api_key': 'k',
       'model': 'gpt-6-luna', 'criterio': None}


def _grupos():
    return [
        {'grupo': 1, 'titulo': 'Lluvias inundan barrios de Cali', 'n': 1, 'titulos_alt': [],
         'texto': 'Las lluvias inundaron 30 barrios de Cali.',
         'contexto_marca': 'Las lluvias inundaron 30 barrios de Cali, según cifras de la Marca.'},
        {'grupo': 2, 'titulo': 'Concejales critican a la Marca por demoras', 'n': 1, 'titulos_alt': [],
         'texto': 'Concejales criticaron a la Marca por demoras en la obra.',
         'contexto_marca': 'Concejales criticaron a la Marca por demoras en la obra.'},
        {'grupo': 3, 'titulo': 'La Marca entrega colegio en Bosa', 'n': 1, 'titulos_alt': [],
         'texto': 'La Marca entregó un colegio en Bosa.',
         'contexto_marca': 'La Marca entregó un colegio en Bosa para 1.200 estudiantes.'},
    ]


def _etiquetas():
    return {1: {'sub_tema': 'Inundaciones en barrios de Cali', 'tono': 'Negativo', 'rol': 'afectacion'},
            2: {'sub_tema': 'Críticas por demoras en obra', 'tono': 'Negativo', 'rol': 'critica'},
            3: {'sub_tema': 'Entrega de colegio en Bosa', 'tono': 'Positivo', 'rol': 'gestion'}}


# --- auditor de tono -------------------------------------------------------

def test_candidatos_de_riesgo():
    c = {x['id']: x['motivos'] for x in az.candidatos_tono_luna(CFG, _grupos(), _etiquetas())}
    assert set(c) == {'1', '2'}                      # el Positivo con la marca en el título no
    assert any('Negativo' in m for m in c['1'])


def test_auditor_aplica_solo_veredictos_coherentes():
    pedidos = []

    def fake(cfg, msgs, uso=None, **kw):
        pedidos.append(msgs)
        assert 'auditor-tono' in msgs[0]['content']
        filas = json.loads(msgs[1]['content'])['filas']
        assert {f['id'] for f in filas} == {'1', '2'}
        return json.dumps({'resultados': [
            {'id': '1', 'rol': 'referencial', 'tono': 'Neutro', 'razon': 'la Marca solo da la cifra'},
            {'id': '2', 'rol': 'critica', 'tono': 'Positivo', 'razon': 'incoherente'}]})
    with patch.object(az, 'llamar_llm', side_effect=fake):
        r = az.auditar_tono_luna(CFG, _grupos(), _etiquetas())
    assert r['candidatos'] == 2 and r['lotes'] == 1 and len(pedidos) == 1
    assert r['cambios'] == [{'grupo': 1, 'de': 'Negativo', 'a': 'Neutro', 'rol': 'referencial',
                             'razon': 'la Marca solo da la cifra'}]   # el incoherente no entra


def test_auditor_no_corre_con_jev_pkl_o_solo_subtema():
    assert az.auditor_luna_activo(CFG)
    assert not az.auditor_luna_activo(dict(CFG, typesafe_api_key='x'))
    assert not az.auditor_luna_activo(dict(CFG, _tono_pkl=True))
    assert not az.auditor_luna_activo(dict(CFG, columnas_ia='subtema'))
    assert not az.auditor_luna_activo(dict(CFG, auditor_tono=False))
    assert not az.auditor_luna_activo(dict(CFG, api_key=''))


def test_auditor_que_falla_no_tumba():
    with patch.object(az, 'llamar_llm', side_effect=RuntimeError('sin red')):
        r = az.auditar_tono_luna(CFG, _grupos(), _etiquetas())
    assert r['cambios'] == [] and 'sin red' in (r['error'] or '')


# --- evaluador de agrupación ----------------------------------------------

def _rows():
    return [
        {'Título': 'Alcaldía anuncia cierre de vías en Suba por obras del metro',
         'Contexto analizado': 'Cierre de vías en Suba por obras del metro desde el lunes.'},
        {'Título': 'Alcaldía anuncia cierre de vías en Kennedy por marcha',
         'Contexto analizado': 'Cierre de vías en Kennedy por una marcha el viernes.'},
        {'Título': 'Bogotá entrega colegio nuevo en Bosa',
         'Contexto analizado': 'La Alcaldía entregó un colegio en Bosa para 1.200 estudiantes.'},
        {'Título': 'Nuevo colegio para 1.200 niños abre en Bosa',
         'Contexto analizado': 'La Alcaldía entregó un colegio en Bosa para 1.200 estudiantes.'},
    ]


def test_evaluador_con_luna():
    mapa = {0: 1, 1: 2, 2: 3, 3: 4}
    etiquetas = {1: {'sub_tema': 'Cierre de vías por obras', 'tono': 'Neutro'},
                 2: {'sub_tema': 'Cierre de vías por obras', 'tono': 'Neutro'},
                 3: {'sub_tema': 'Entrega de colegio en Bosa', 'tono': 'Positivo'},
                 4: {'sub_tema': 'Nuevo colegio distrital en Bosa', 'tono': 'Neutro'}}
    cands = ev.construir_candidatos(_rows(), mapa, {'titulo': 'Título'})
    llamadas = []

    def llm(system, prompt):
        llamadas.append(prompt)
        assert 'evaluador-agrupacion' in system
        assert json.loads(prompt)['cliente']['marca'] == 'Alcaldía'
        return json.dumps({'grupos': [
            {'id': 'c1', 'decision': 'separar', 'motivo': 'lugares y causas distintos',
             'subtemas': {'0': 'Cierre vial en Suba por obras del metro',
                          '1': 'Cierre vial en Kennedy por marcha'}},
            {'id': 'c2', 'decision': 'agrupar', 'subtema': 'Entrega de colegio nuevo en Bosa',
             'tono': 'Positivo'}]})

    def sin_sdk():
        raise AssertionError('con llm_fn no se usa el SDK de Claude')
    with patch.object(mh, '_sdk', side_effect=sin_sdk):
        dec = ev.evaluar(cands, _rows(), mapa, etiquetas, {'titulo': 'Título'},
                         cliente={'brand': 'Alcaldía'}, llm_fn=llm)
    assert len(llamadas) == 1 and len(dec) == 2
    ev.aplicar_inicial(dec, cands, mapa, etiquetas)
    assert etiquetas[1]['sub_tema'] != etiquetas[2]['sub_tema']
    assert etiquetas[3]['sub_tema'] == etiquetas[4]['sub_tema'] == 'Entrega de colegio nuevo en Bosa'


def test_evaluador_luna_lote_que_falla():
    mapa = {0: 1, 1: 2, 2: 3, 3: 4}
    cands = ev.construir_candidatos(_rows(), mapa, {'titulo': 'Título'})

    def llm(system, prompt):
        raise RuntimeError('timeout')
    dec = ev.evaluar(cands, _rows(), mapa, {}, {'titulo': 'Título'}, llm_fn=llm)
    assert dec == [] and 'timeout' in ev.ULTIMO['error']


# --- de punta a punta: el auditor y el evaluador entran en el flujo Luna ----

def test_flujo_luna_llama_auditor_y_evaluador():
    import io
    from openpyxl import Workbook, load_workbook
    import pipeline
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Tipo de Medio'])
    ws.append(['1', 'Lluvias inundan barrios de Cali',
               'Las lluvias inundaron 30 barrios de Cali, según cifras de la Marca.',
               'El Tiempo', '01/09/2026', 'Prensa'])
    ws.append(['2', 'La Marca entrega colegio en Bosa',
               'La Marca entregó un colegio en Bosa para 1.200 estudiantes.',
               'Caracol Radio', '02/09/2026', 'Radio'])
    buf = io.BytesIO()
    wb.save(buf)
    vistos = []

    def fake(cfg, msgs, uso=None, **kw):
        sys_msg, user = msgs[0]['content'], msgs[-1]['content']
        if 'auditor-tono' in sys_msg:
            vistos.append('auditor')
            filas = json.loads(user)['filas']
            return json.dumps({'resultados': [{'id': f['id'], 'rol': 'referencial', 'tono': 'Neutro',
                                               'razon': 'mención de paso'} for f in filas]})
        if 'evaluador-agrupacion' in sys_msg:
            vistos.append('evaluador')
            return json.dumps({'grupos': []})
        ids = [int(x) for x in re.findall(r'GRUPO id=(\d+)', user)]
        out = []
        for i in ids:
            m = re.search(r'GRUPO id=%d.*?TITULAR: ([^\n]*)' % i, user, re.S)
            t = (m.group(1) if m else '').lower()
            out.append({'id': i, 'sub_tema': 'Inundaciones en barrios de Cali' if 'lluvias' in t
                        else 'Entrega de colegio en Bosa',
                        'rol': 'afectacion' if 'lluvias' in t else 'gestion',
                        'tono': 'Negativo' if 'lluvias' in t else 'Positivo'})
        return json.dumps({'resultados': out})
    cfg = {'enabled': True, 'brand': 'Marca', 'aliases': [], 'voceros': [], 'criterio': None,
           'incluir_tema': False, 'motor': 'openai', 'api_key': 'k', 'formato_boletin': True,
           'agrupacion_estricta': True, 'memoria': False}
    with patch.object(az, 'llamar_llm', side_effect=fake):
        res = pipeline.process_dossier(io.BytesIO(buf.getvalue()), {}, {}, ai_config=cfg)
    hoja = list(load_workbook(io.BytesIO(res['output_data']), read_only=True).active
                .iter_rows(values_only=True))
    d = {str(dict(zip(hoja[0], f))['ID Noticia']): dict(zip(hoja[0], f)) for f in hoja[1:]}
    assert 'auditor' in vistos
    assert res['analisis']['auditor_tono_luna']['cambios']
    assert d['1']['Tono_IA'] == 'Neutro'          # el auditor corrigió el Negativo de paso
    assert d['2']['Tono_IA'] == 'Positivo'
