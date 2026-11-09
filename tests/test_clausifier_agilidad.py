# -*- coding: utf-8 -*-
"""Clausifier 2026-11-09: agilidad y pantalla de resultados ($0, sin red).

- Clasificación en paralelo por tramos + agente de cierre.
- Memoria del cliente: una noticia ya clasificada no vuelve al modelo.
- Tiempo por etapa, vista de resultados y lista «para revisar».
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_agent_sdk import ResultMessage  # noqa: E402
from openpyxl import Workbook  # noqa: E402

import analyzer_tono_tema as az  # noqa: E402
import memoria_cliente  # noqa: E402
import motor_haiku as mh  # noqa: E402
import pipeline  # noqa: E402

HECHOS = ['Entrega de colegio', 'Jornada de vacunación', 'Cierre vial por obras', 'Feria del libro',
          'Convenio de formación', 'Informe de seguridad', 'Premio de innovación', 'Captura de banda']
LUGARES = ['Bosa', 'Suba', 'Usme', 'Kennedy', 'Engativá', 'Fontibón', 'Chapinero', 'Tunjuelito',
           'Usaquén', 'Teusaquillo', 'Barrios Unidos', 'Puente Aranda', 'Ciudad Bolívar', 'Sumapaz']


def _res(costo=0.01, texto=''):
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=3, session_id='fake', total_cost_usd=costo, result=texto)


def _items(n):
    out = []
    for i in range(n):
        h, lugar = HECHOS[i % len(HECHOS)], LUGARES[(i // len(HECHOS)) % len(LUGARES)]
        out.append({'id': str(i), 'titulo': 'La Marca anuncia %s en %s edición %d' % (h.lower(), lugar, i),
                    'contexto': 'La Marca informó sobre %s en %s durante la edición %d del programa.'
                                % (h.lower(), lugar, i)})
    return out


def _subtema(i):
    return 'Programa distrital número %s en %s' % (
        ['uno', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho'][i % 8],
        LUGARES[(i // 8) % len(LUGARES)])


def _agente(registro):
    """Agente falso que respeta su tramo (lo lee del pedido, como el real)."""
    async def q(prompt, options):
        if 'auditor-tono' in (options.system_prompt or ''):
            registro.append(('auditor', None))
            yield _res(0.0, '{"resultados":[]}')
            return
        m = re.search(r'TRAMO (\d+)-(\d+)', prompt)
        cierre = 'CIERRE:' in prompt
        tramo = (int(m.group(1)), int(m.group(2))) if m else None
        vistos = []
        desde = tramo[0] if tramo else 0
        while desde is not None:
            pag = json.loads((await mh._leer_noticias({'desde': desde}, tramo=tramo,
                                                      pendientes=cierre))['content'][0]['text'])
            filas = [{'id': n['id'], 'subtema': _subtema(int(n['id'])), 'rol': 'gestion',
                      'tono': 'Positivo'} for n in pag['noticias'] if 'igual_a' not in n]
            vistos += [f['id'] for f in filas]
            if filas:
                r = await mh._guardar_clasificacion({'filas_json': json.dumps(filas)}, tramo=tramo)
                registro.append(('guardado', r['content'][0]['text']))
            desde = pag['siguiente']
            if cierre and not filas:
                break
        registro.append(('cierre' if cierre else 'tramo' if tramo else 'unico', vistos))
        await asyncio.sleep(0)
        yield _res()
    return q


def test_planear_tramos():
    assert mh.planear_tramos(60, 4) == []                       # pocas páginas: un agente
    assert mh.planear_tramos(500, 1) == []                      # paralelo apagado
    assert mh.planear_tramos(500, 4) == [(0, 125), (125, 250), (250, 375), (375, 500)]
    t = mh.planear_tramos(110, 4)
    assert t[0][0] == 0 and t[-1][1] == 110 and all(a < b for a, b in t)


def test_clasificacion_en_paralelo_cubre_todo_sin_repetir():
    reg = []
    r = mh.clasificar_con_haiku(_items(230), 'Marca', query_fn=_agente(reg), auditor_tono=False)
    tramos = [v for k, v in reg if k == 'tramo']
    assert r['agentes'] == 4 and len(tramos) == 4
    planos = [i for t in tramos for i in t]
    assert sorted(planos, key=int) == [str(i) for i in range(230)]      # cada noticia en un solo tramo
    assert [k for k, _ in reg if k in ('cierre', 'unico')] == ['cierre']
    assert [v for k, v in reg if k == 'cierre'] == [[]]                 # nada pendiente para el cierre
    assert r['faltan'] == 0 and r['filas'] == 230
    assert abs(r['costo_usd'] - 0.05) < 1e-9 and r['turnos'] == 15      # 4 tramos + cierre
    # Cada agente responde solo por su tramo.
    assert any('Faltan por clasificar: 0' in v for k, v in reg if k == 'guardado')


def test_agente_de_cierre_clasifica_lo_que_un_tramo_dejo():
    reg = []
    base = _agente(reg)

    async def q(prompt, options):
        if 'TRAMO 0-' in prompt:              # un agente falla sin clasificar nada
            reg.append(('fallo', None))
            yield _res(0.0)
            return
        async for m in base(prompt, options):
            yield m

    r = mh.clasificar_con_haiku(_items(230), 'Marca', query_fn=q, auditor_tono=False)
    cierre = [v for k, v in reg if k == 'cierre'][0]
    assert len(cierre) >= 25 and r['faltan'] == 0 and r['filas'] == 230


def test_paralelo_apagado_usa_un_solo_agente():
    reg = []
    r = mh.clasificar_con_haiku(_items(230), 'Marca', query_fn=_agente(reg), auditor_tono=False,
                                paralelo=1)
    assert r['agentes'] == 1 and [k for k, _ in reg if k != 'guardado'] == ['unico']


def test_previas_no_se_envian_y_quedan_clasificadas():
    its = _items(6)
    its[0]['previa'] = {'tono': 'Negativo', 'subtema': 'Sanción por contrato irregular'}
    its[1]['previa'] = {'tono': 'Bueno', 'subtema': 'x'}                 # inválida: se ignora
    reg = []
    r = mh.clasificar_con_haiku(its, 'Marca', query_fn=_agente(reg), auditor_tono=False)
    enviados = [v for k, v in reg if k == 'unico'][0]
    assert '0' not in enviados and '1' in enviados
    assert r['reutilizadas'] == 1
    assert r['etiquetas']['0'] == {'tono': 'Negativo', 'subtema': 'Sanción por contrato irregular'}

    llamadas = []

    async def nunca(prompt, options):
        llamadas.append(1)
        yield _res()

    for it in its:
        it['previa'] = {'tono': 'Neutro', 'subtema': 'Informe anual de gestión distrital'}
    r = mh.clasificar_con_haiku(its, 'Marca', query_fn=nunca, auditor_tono=False)
    assert not llamadas and r['agentes'] == 0 and r['filas'] == 6 and r['costo_usd'] == 0.0


def test_memoria_cliente_huella_y_vigencia(tmp_path):
    extra = {'historial_dir': str(tmp_path)}
    k = memoria_cliente.clave('Título Uno', 'Contexto de la nota')
    assert k == memoria_cliente.clave('titulo  uno', 'CONTEXTO de la nota.')
    assert memoria_cliente.guardar('Marca X', 'h1', {k: {'tono': 'Neutro', 'subtema': 'S'}}, extra) == 1
    assert memoria_cliente.cargar('Marca X', 'h1', extra)[k]['subtema'] == 'S'
    assert memoria_cliente.cargar('Marca X', 'h2', extra) == {}          # cambió la configuración
    assert memoria_cliente.cargar('Otra', 'h1', extra) == {}
    assert memoria_cliente.borrar('Marca X', extra) and not memoria_cliente.cargar('Marca X', 'h1', extra)


def _dossier():
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha'])
    ws.append(['101', 'La Marca entrega un colegio en Bosa',
               'La Marca entrega un colegio nuevo a la comunidad de Bosa.', 'Prensa A', '2026-09-01'])
    ws.append(['102', 'Concejo debate el presupuesto distrital',
               'El Concejo debatió el presupuesto; la nota no habla de la entidad.', 'Prensa B', '2026-09-02'])
    ws.append(['103', 'Jornada de vacunación en Suba',
               'La Marca abrió una jornada de vacunación infantil en Suba.', 'Radio C', '2026-09-03'])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _correr(reg, **extra):
    real = mh.clasificar_con_haiku
    cfg = {'enabled': True, 'brand': 'Marca', 'aliases': [], 'voceros': [], 'criterio': None,
           'incluir_tema': False, 'motor': 'haiku', 'api_key': None,
           'tone_pkl_bytes': None, 'theme_pkl_bytes': None}
    cfg.update(extra)
    with patch.object(mh, 'clasificar_con_haiku',
                      side_effect=lambda *a, **k: real(*a, query_fn=_agente(reg), **k)), \
            patch.object(mh, 'QUERY_FN', _agente(reg)), \
            patch.object(az, 'llamar_llm', side_effect=AssertionError('OpenAI no')):
        return pipeline.process_dossier(io.BytesIO(_dossier()), {}, {}, ai_config=cfg)


def test_resultado_trae_tiempos_vista_y_para_revisar():
    reg = []
    res = _correr(reg)
    etapas = [t['etapa'] for t in res['tiempos']]
    assert 'Lectura, limpieza y duplicados' in etapas and 'Generación del Excel' in etapas
    assert 'Análisis de tono, tema y subtema' in etapas
    sub = [t['etapa'] for t in res['tiempos'] if t.get('sub')]
    assert 'Clasificación (agente Haiku)' in sub and 'Agrupación de notas equivalentes' in sub
    assert all(t['segundos'] >= 0 for t in res['tiempos'])
    vista = res['vista']
    assert len(vista) == 3 and {f['ID'] for f in vista} == {'101', '102', '103'}
    assert all(f['Tono'] in ('Positivo', 'Neutro', 'Negativo') and f['Subtema'] for f in vista)
    rev = res['analisis']['para_revisar']
    sin = [x for x in rev if x['motivo'] == 'Sin mención literal de la marca']
    assert [x['id'] for x in sin] == ['102'] and sin[0]['subtema']
    assert ('auditor', None) in reg                       # corrió a la vez que el evaluador


def test_segunda_corrida_reutiliza_la_memoria_y_da_lo_mismo():
    reg1, reg2 = [], []
    r1 = _correr(reg1)
    assert r1['analisis']['memoria'] == {'activa': True, 'reutilizadas': 0, 'guardadas': 3}
    r2 = _correr(reg2)
    assert r2['analisis']['memoria']['reutilizadas'] == 3
    assert not [k for k, _ in reg2 if k in ('unico', 'tramo', 'cierre')]      # el agente no corrió
    assert r2['analisis']['costo_aprox_usd'] == 0
    clave = lambda r: sorted((f['ID'], f['Tono'], f['Subtema']) for f in r['vista'])  # noqa: E731
    assert clave(r1) == clave(r2)
    # Otra configuración (nuevo alias) no usa la memoria anterior.
    reg3 = []
    r3 = _correr(reg3, aliases=['la Marca'])
    assert r3['analisis']['memoria']['reutilizadas'] == 0
    # Y se puede apagar.
    reg4 = []
    r4 = _correr(reg4, memoria=False)
    assert r4['analisis']['memoria'] == {'activa': False, 'reutilizadas': 0, 'guardadas': 0}
    assert [k for k, _ in reg4 if k == 'unico']


def test_pantalla_resumen_de_tonos_y_subtemas():
    import app
    vista = [{'ID': '1', 'Tono': 'Positivo', 'Subtema': 'A', 'Tema': ''},
             {'ID': '2', 'Tono': 'Positivo', 'Subtema': 'A', 'Tema': ''},
             {'ID': '3', 'Tono': 'Negativo', 'Subtema': 'B', 'Tema': ''},
             {'ID': '4', 'Tono': 'Neutro', 'Subtema': '', 'Tema': ''}]
    assert app.resumen_tonos(vista) == [('Positivo', 2, 50.0), ('Neutro', 1, 25.0), ('Negativo', 1, 25.0)]
    df = app.tabla_subtemas(vista)
    assert list(df['Subtema']) == ['A', 'B'] and list(df['Notas']) == [2, 1]
    assert list(df.columns) == ['Subtema', 'Notas', 'Positivo', 'Neutro', 'Negativo']
