# -*- coding: utf-8 -*-
"""Clausifier 2026-11-09: el tono es el del cliente (marca, alias o voceros).

- guardar_clasificacion valida que el tono corresponda al ROL del cliente en
  la nota (gestion/logro = Positivo, critica/afectacion = Negativo,
  equilibrada/referencial = Neutro).
- leer_noticias avisa cuando el texto no nombra al cliente.
- auditar_tono (agente aparte, $0 con un `query` falso) verifica las filas
  de riesgo y solo aplica veredictos válidos.
- El revisor de subtemas y el evaluador de agrupación reciben el cliente,
  alias, voceros y criterio en su propio pedido.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_agent_sdk import ResultMessage  # noqa: E402

import evaluador_agrupacion as ev  # noqa: E402
import motor_haiku as mh  # noqa: E402

MARCA, ALIAS, VOCEROS = 'Alcaldía de Bogotá', ['la Alcaldía'], ['Carlos Galán']


def _res(texto='', costo=0.01):
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id='fake', total_cost_usd=costo, result=texto)


def _items():
    return [
        {'id': '1', 'titulo': 'Alcaldía de Bogotá entrega viviendas en Usme',
         'contexto': 'La Alcaldía de Bogotá entregó 300 viviendas a familias de Usme.'},
        {'id': '2', 'titulo': 'Concejo denuncia sobrecostos',
         'contexto': 'Concejales denunciaron sobrecostos en un contrato de la Alcaldía.'},
        {'id': '3', 'titulo': 'Aumentan los homicidios en la ciudad',
         'contexto': 'Los homicidios aumentaron 12 % en el primer semestre del año.'},
        {'id': '4', 'titulo': 'Premio para empresa de aseo',
         'contexto': 'Al acto de premiación asistió Carlos Galán, entre otros invitados.'},
        {'id': '5', 'titulo': 'Concejo denuncia sobrecostos', 'contexto': 'otro medio'},
    ]


def _preparar(**kw):
    mh.preparar_estado(_items(), incluir_tema=False,
                       terminos=mh.terminos_cliente(MARCA, ALIAS, VOCEROS), **kw)


def _guardar(filas):
    return asyncio.run(mh._guardar_clasificacion({'filas_json': json.dumps(filas)}))


def _txt(r):
    return r['content'][0]['text']


SUB = {'1': 'Entrega de viviendas en Usme', '2': 'Sobrecostos en contrato distrital',
       '3': 'Aumento de homicidios en Bogotá', '4': 'Reconocimiento a operador de aseo'}


def test_roles_cubren_los_tres_tonos():
    assert set(mh.ROLES.values()) == set(mh.TONOS)
    for rol in ('gestion', 'logro', 'critica', 'afectacion', 'equilibrada', 'referencial'):
        assert rol in mh.ROLES and rol in mh.SYSTEM_PROMPT and rol in mh.PROMPT_AUDITOR_TONO


def test_rol_incoherente_con_el_tono_se_rechaza_y_luego_se_guarda():
    _preparar()
    r = _guardar([{'id': '3', 'subtema': SUB['3'], 'rol': 'referencial', 'tono': 'Negativo'}])
    assert r.get('is_error') and 'RECHAZADOS' in _txt(r) and "rol 'referencial'" in _txt(r)
    assert '3' not in mh._ESTADO['res']
    r = _guardar([{'id': '3', 'subtema': SUB['3'], 'rol': 'Referencial', 'tono': 'Neutro'}])
    assert '3' in mh._ESTADO['res'] and mh._ESTADO['res']['3']['rol'] == 'referencial'


def test_rol_invalido_es_error_y_sin_rol_sigue_valiendo():
    _preparar()
    assert _guardar([{'id': '1', 'subtema': SUB['1'], 'rol': 'bueno', 'tono': 'Positivo'}]).get('is_error')
    _guardar([{'id': '1', 'subtema': SUB['1'], 'tono': 'Positivo'}])
    assert mh._ESTADO['res']['1'] == {'tono': 'Positivo', 'subtema': SUB['1']}


def test_incoherencia_persistente_se_acepta_sin_rol_y_va_al_auditor():
    _preparar()
    fila = [{'id': '3', 'subtema': SUB['3'], 'rol': 'referencial', 'tono': 'Negativo'}]
    for _ in range(mh.MAX_RECHAZOS):
        assert _guardar(fila).get('is_error')
    _guardar(fila)
    assert mh._ESTADO['res']['3'] == {'tono': 'Negativo', 'subtema': SUB['3']}
    motivos = {c['id']: c['motivos'] for c in mh.candidatos_tono()}
    assert any('no coincidían' in m for m in motivos['3'])


def test_correccion_sin_rol_conserva_el_rol_solo_si_sigue_coherente():
    _preparar()
    _guardar([{'id': '1', 'subtema': SUB['1'], 'rol': 'gestion', 'tono': 'Positivo'}])
    _guardar([{'id': '1', 'subtema': 'Entrega de viviendas nuevas en Usme', 'tono': 'Positivo'}])
    assert mh._ESTADO['res']['1']['rol'] == 'gestion'
    _guardar([{'id': '1', 'subtema': SUB['1'], 'tono': 'Neutro'}])
    assert 'rol' not in mh._ESTADO['res']['1']


def test_con_pkl_de_tono_no_se_valida_el_rol():
    _preparar(tono_pkl=True)
    r = _guardar([{'id': '1', 'subtema': SUB['1'], 'rol': 'gestion', 'tono': 'Neutro'}])
    assert not r.get('is_error')


def test_leer_noticias_marca_sin_mencion_literal():
    _preparar()
    pag = json.loads(_txt(asyncio.run(mh._leer_noticias({'desde': 0}))))
    marca = {n['id']: n.get('sin_mencion_literal', False) for n in pag['noticias']}
    assert marca == {'1': False, '2': False, '3': True, '4': False, '5': False}
    assert mh.menciona_cliente('habló carlos galan ayer') and not mh.menciona_cliente('la alcaldesa')


def _clasificar_todo():
    _preparar()
    _guardar([{'id': '1', 'subtema': SUB['1'], 'rol': 'gestion', 'tono': 'Positivo'},
              {'id': '2', 'subtema': SUB['2'], 'rol': 'critica', 'tono': 'Negativo'},
              {'id': '3', 'subtema': SUB['3'], 'rol': 'afectacion', 'tono': 'Negativo'},
              {'id': '4', 'subtema': SUB['4'], 'rol': 'logro', 'tono': 'Positivo'}])


def test_candidatos_tono_motivos():
    _clasificar_todo()
    c = {x['id']: ' | '.join(x['motivos']) for x in mh.candidatos_tono()}
    assert '1' not in c                       # Positivo con el cliente en el título
    assert 'Negativo: confirma' in c['2']
    assert 'sin mención literal' in c['3']
    assert 'no está en el título' in c['4']
    assert '5' not in c                       # hereda de la 2


def test_auditar_tono_aplica_solo_veredictos_validos_y_propaga():
    _clasificar_todo()
    visto = {}

    async def q(prompt, options):
        assert options.tools == [] and options.max_turns == 1
        assert 'auditor-tono' in options.system_prompt
        visto['pedido'] = json.loads(prompt)
        yield _res(json.dumps({'resultados': [
            {'id': '2', 'rol': 'critica', 'tono': 'Negativo', 'razon': 'crítica dirigida'},
            {'id': '3', 'rol': 'referencial', 'tono': 'Neutro', 'razon': 'no habla del cliente'},
            {'id': '4', 'rol': 'referencial', 'tono': 'Positivo', 'razon': 'incoherente'},
            {'id': '99', 'rol': 'referencial', 'tono': 'Neutro'}]}))

    aud = mh.auditar_tono(MARCA, ALIAS, VOCEROS, 'Regla\\ndel cliente', query_fn=q)
    assert visto['pedido']['cliente'] == {'marca': MARCA, 'alias': ALIAS, 'voceros': VOCEROS}
    assert visto['pedido']['criterio_tono'] == 'Regla\ndel cliente'
    assert aud['candidatos'] == 3 and aud['confirmados'] == 1 and aud['error'] is None
    assert [(c['id'], c['de'], c['a']) for c in aud['cambios']] == [('3', 'Negativo', 'Neutro')]
    res = mh._ESTADO['res']
    assert res['3']['tono'] == 'Neutro' and res['3']['rol'] == 'referencial'
    assert res['4']['tono'] == 'Positivo'      # veredicto incoherente: no se aplica
    assert res['5']['tono'] == res['2']['tono'] == 'Negativo'


def test_auditar_tono_nunca_lanza():
    _clasificar_todo()

    async def boom(prompt, options):
        raise RuntimeError('sin red')
        yield  # pragma: no cover

    aud = mh.auditar_tono(MARCA, query_fn=boom)
    assert aud['cambios'] == [] and mh._ESTADO['res']['3']['tono'] == 'Negativo'


def test_clasificar_con_haiku_lanza_auditor_y_suma_costo():
    llamadas = []

    async def q(prompt, options):
        if 'auditor-tono' in (options.system_prompt or ''):
            llamadas.append('auditor')
            yield _res(json.dumps({'resultados': [
                {'id': '3', 'rol': 'referencial', 'tono': 'Neutro', 'razon': 'asunto ajeno'}]}), 0.02)
            return
        llamadas.append('agente')
        llamadas.append(options.agents['revisor-subtemas'].prompt)
        await mh._guardar_clasificacion({'filas_json': json.dumps(
            [{'id': '1', 'subtema': SUB['1'], 'rol': 'gestion', 'tono': 'Positivo'},
             {'id': '2', 'subtema': SUB['2'], 'rol': 'critica', 'tono': 'Negativo'},
             {'id': '3', 'subtema': SUB['3'], 'rol': 'afectacion', 'tono': 'Negativo'},
             {'id': '4', 'subtema': SUB['4'], 'rol': 'referencial', 'tono': 'Neutro'}])})
        yield _res('', 0.05)

    r = mh.clasificar_con_haiku(_items(), MARCA, ALIAS, VOCEROS, criterio_texto='Regla X',
                                query_fn=q)
    assert llamadas[0] == 'agente' and llamadas[-1] == 'auditor'
    # El subagente revisor conoce al cliente y el criterio sin que se lo pasen.
    assert 'CLIENTE: %s' % MARCA in llamadas[1] and 'Carlos Galán' in llamadas[1]
    assert 'Regla X' in llamadas[1]
    assert r['etiquetas']['3']['tono'] == 'Neutro'
    assert abs(r['costo_usd'] - 0.07) < 1e-9
    assert r['auditor_tono']['candidatos'] == 2 and len(r['auditor_tono']['cambios']) == 1
    assert r['roles'] == {'gestion': 1, 'critica': 1, 'referencial': 2}

    llamadas.clear()
    r = mh.clasificar_con_haiku(_items(), MARCA, ALIAS, VOCEROS, query_fn=q, tono_pkl=True)
    assert 'auditor' not in llamadas and r['auditor_tono'] is None
    llamadas.clear()
    mh.clasificar_con_haiku(_items(), MARCA, ALIAS, VOCEROS, query_fn=q, auditor_tono=False)
    assert 'auditor' not in llamadas


def test_criterio_del_catalogo_llega_con_saltos_reales():
    from catalogo_tono_tema import CRITERIOS_TONO
    crit = list(CRITERIOS_TONO.values())[0]
    assert '\\n' in crit                                  # el catálogo trae '\n' literales
    p = mh.armar_prompt(MARCA, ALIAS, VOCEROS, crit, False)
    assert '\\n' not in p and '<criterio_tono>' in p


def test_evaluador_recibe_cliente_y_criterio():
    rows = [{'Título': 'Cierre vial en Suba por obras', 'Contexto analizado': 'Cierre vial en Suba por obras del metro esta semana.'},
            {'Título': 'Cierre vial en Suba por obras', 'Contexto analizado': 'Cierre vial en Suba por las obras del metro, informó la Alcaldía.'}]
    mapa = {0: 1, 1: 2}
    cands = ev.construir_candidatos(rows, mapa, {'titulo': 'Título'})
    visto = {}

    async def q(prompt, options):
        visto['pedido'] = json.loads(prompt)
        visto['system'] = options.system_prompt
        yield _res(json.dumps({'grupos': []}))

    ev.evaluar(cands, rows, mapa, {1: {}, 2: {}}, {'titulo': 'Título'}, query_fn=q,
               cliente={'brand': MARCA, 'aliases': ALIAS, 'voceros': VOCEROS,
                        'criterio_texto': 'Regla X'})
    assert visto['pedido']['cliente'] == {'marca': MARCA, 'alias': ALIAS, 'voceros': VOCEROS}
    assert visto['pedido']['criterio_tono'] == 'Regla X'
    assert 'referencial' in visto['system'] and '__RUBRICAS__' not in visto['system']


def test_se_conservan_las_reglas_propias_de_esta_app():
    """Las mejoras de tono/subtema no pisan lo que esta app ya tenía."""
    assert 'referencial' in mh.SYSTEM_PROMPT and 'Prueba de precisión' in mh.SYSTEM_PROMPT
    if hasattr(mh, 'system_prompt'):                      # Secretaría: TIPO y GÉNERO
        p = mh.system_prompt(True)
        assert '<tipo_y_genero>' in p and '"genero":"Noticia|' in p and '"rol":"gestion|' in p
        assert '<tipo_y_genero>' not in mh.system_prompt(False)
    else:                                                 # Defensoría: hecho malo no es Negativo
        assert 'Que la noticia trate de un hecho malo' in mh.SYSTEM_PROMPT
        assert 'Un hecho malo' in mh.PROMPT_REVISOR
