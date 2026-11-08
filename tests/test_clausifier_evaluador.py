"""Clausifier 2026-11-08: agente evaluador-agrupacion con query falso ($0)."""
import json

from claude_agent_sdk import ResultMessage

import evaluador_agrupacion as ev
import motor_haiku as mh


def _res(texto):
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id='fake', total_cost_usd=0.001, result=texto)


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


MAPA = {0: 1, 1: 2, 2: 3, 3: 4}


def test_candidatos_por_titulo_y_contexto():
    cands = ev.construir_candidatos(_rows(), MAPA, {'titulo': 'Título'})
    assert [0, 1] in cands['comps'] and [2, 3] in cands['comps']


def test_mismo_grupo_determinista_es_candidato_y_filas_identicas_una_unidad():
    rows = _rows() + [dict(_rows()[0])]
    cands = ev.construir_candidatos(rows, {0: 1, 1: 1, 2: 3, 3: 4, 4: 1}, {'titulo': 'Título'})
    assert [0, 1] in cands['comps'] and cands['miembros'][0] == [0, 4]


def _fake(decisiones):
    async def q(prompt, options):
        assert options.tools == [] and options.max_turns == 1
        assert 'evaluador-agrupacion' in options.system_prompt
        yield _res(json.dumps({'grupos': decisiones}))
    return q


def test_separar_impide_fusion_determinista_y_agrupar_unifica():
    etiquetas = {1: {'sub_tema': 'Cierre de vías por obras', 'tono': 'Neutro'},
                 2: {'sub_tema': 'Cierre de vías por obras', 'tono': 'Neutro'},
                 3: {'sub_tema': 'Entrega de colegio en Bosa', 'tono': 'Positivo'},
                 4: {'sub_tema': 'Nuevo colegio distrital en Bosa', 'tono': 'Neutro'}}
    cands = ev.construir_candidatos(_rows(), MAPA, {'titulo': 'Título'})
    dec = ev.evaluar(cands, _rows(), MAPA, etiquetas, {'titulo': 'Título'}, query_fn=_fake([
        {'id': 'c1', 'decision': 'separar', 'motivo': 'lugares y causas distintos',
         'subtemas': {'0': 'Cierre vial en Suba por obras del metro',
                      '1': 'Cierre vial en Kennedy por marcha'}},
        {'id': 'c2', 'decision': 'agrupar', 'subtema': 'Entrega de colegio nuevo en Bosa',
         'tono': 'Positivo'}]))
    assert ev.ULTIMO['lotes'] == 1 and len(dec) == 2
    estado = ev.aplicar_inicial(dec, cands, MAPA, etiquetas)
    assert etiquetas[1]['sub_tema'] != etiquetas[2]['sub_tema']
    assert etiquetas[3]['sub_tema'] == etiquetas[4]['sub_tema'] == 'Entrega de colegio nuevo en Bosa'
    rows = _rows()
    # Un pase determinista posterior fusiona 1 y 2 y separa 3 y 4.
    for i, s, t in ((0, 'Cierre de vías en Bogotá', 'Neutro'), (1, 'Cierre de vías en Bogotá', 'Neutro'),
                    (2, 'Entrega de colegio nuevo en Bosa', 'Positivo'),
                    (3, 'Colegio en Bosa', 'Positivo')):
        rows[i]['Subtema_IA'], rows[i]['Tono_IA'] = s, t
    inf = ev.hacer_cumplir(dec, estado, rows, MAPA)
    assert rows[0]['Subtema_IA'] == 'Cierre vial en Suba por obras del metro'
    assert rows[1]['Subtema_IA'] == 'Cierre vial en Kennedy por marcha'
    assert rows[2]['Subtema_IA'] == rows[3]['Subtema_IA'] == 'Entrega de colegio nuevo en Bosa'
    assert rows[2]['Tono_IA'] == rows[3]['Tono_IA'] == 'Positivo'
    assert inf['pares_restaurados'] == [[0, 1]] and inf['grupos_unificados'] == [[2, 3]]


def test_subtema_invalido_del_evaluador_no_entra():
    etiquetas = {3: {'sub_tema': 'Entrega de colegio en Bosa', 'tono': 'Positivo'},
                 4: {'sub_tema': 'Entrega de colegio en Bosa', 'tono': 'Positivo'}}
    d = ev.normalizar_decision({'decision': 'agrupar', 'subtema': 'Entregó colegio', 'tono': 'X'}, [2, 3])
    ev.aplicar_inicial([d], {}, MAPA, etiquetas)
    assert etiquetas[3]['sub_tema'] == 'Entrega de colegio en Bosa'
    assert mh.problemas_subtema('Entregó colegio')


def test_sin_candidatos_no_llama():
    def boom(**k):
        raise AssertionError('no debe llamar')
    assert ev.evaluar({'comps': []}, [], {}, {}, query_fn=boom) == []


def test_separar_dentro_del_mismo_grupo_determinista():
    rows = _rows()[:2]
    mapa = {0: 1, 1: 1}
    etiquetas = {1: {'sub_tema': 'Cierres viales por marcha y obras', 'tono': 'Neutro'}}
    cands = ev.construir_candidatos(rows, mapa, {'titulo': 'Título'})
    dec = ev.evaluar(cands, rows, mapa, etiquetas, {'titulo': 'Título'}, query_fn=_fake([
        {'id': 'c1', 'decision': 'separar', 'motivo': 'otro lugar y otra causa',
         'subtemas': {'0': 'Cierre vial en Suba por obras del metro',
                      '1': 'Cierre vial en Kennedy por marcha'}}]))
    estado = ev.aplicar_inicial(dec, cands, mapa, etiquetas)
    for r in rows:
        r['Subtema_IA'], r['Tono_IA'] = 'Cierres viales por marcha y obras', 'Neutro'
    ev.hacer_cumplir(dec, estado, rows, mapa)
    assert rows[0]['Subtema_IA'] == 'Cierre vial en Suba por obras del metro'
    assert rows[1]['Subtema_IA'] == 'Cierre vial en Kennedy por marcha'
