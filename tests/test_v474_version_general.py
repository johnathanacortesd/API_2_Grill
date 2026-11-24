"""v4.74: versión general del motor de Defensoría. Sin cliente fijo, sin la
hoja de notas analizadas de Defensoría y sin «HORA B»; el xlsx sale con el
orden de columnas del boletín y con tono y subtema en cada fila."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import io  # noqa: E402
import json  # noqa: E402
import types  # noqa: E402
from unittest.mock import patch  # noqa: E402

_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
from claude_agent_sdk import ResultMessage  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402

import analyzer_tono_tema as az  # noqa: E402
import motor_haiku as mh  # noqa: E402
import pipeline  # noqa: E402


def _ok(result=None):
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id='fake', total_cost_usd=0.0, result=result)


PROMPTS = []


async def _fake_query(prompt, options):
    """Agente falso ($0, sin red): usa las tools reales del motor."""
    PROMPTS.append((options.system_prompt or '', prompt))
    if 'auditor-tono' in (options.system_prompt or ''):
        yield _ok('{"resultados":[]}')
        return
    if not getattr(options, 'mcp_servers', None):
        # Otras llamadas sin herramientas (evaluador): no cambian nada.
        yield _ok('{"resultados":[]}')
        return
    desde = 0
    while desde is not None:
        pag = json.loads((await mh._leer_noticias({'desde': desde}))['content'][0]['text'])
        filas = [{'id': n['id'], 'subtema': 'Entrega de colegio nuevo'
                  if 'colegio' in (n.get('contexto') or '').lower() else 'Debate del presupuesto distrital',
                  'tono': 'Positivo' if 'entrega' in (n.get('contexto') or '').lower() else 'Neutro'}
                 for n in pag['noticias'] if 'igual_a' not in n]
        await mh._guardar_clasificacion({'filas_json': json.dumps(filas)})
        desde = pag['siguiente']
    yield _ok()


def _dossier():
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha'])
    ws.append(['101', 'La marca entrega un colegio', 'La marca entrega un colegio nuevo a la comunidad.',
               'Prensa', '2026-09-01'])
    ws.append(['102', 'Concejo debate presupuesto', 'El Concejo y la marca debatieron el presupuesto.',
               'Radio', '2026-09-02'])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cfg(**extra):
    d = {'enabled': True, 'brand': 'Marca', 'aliases': [], 'voceros': [], 'criterio': None,
         'incluir_tema': False, 'motor': 'haiku', 'api_key': None,
         'tone_pkl_bytes': None, 'theme_pkl_bytes': None}
    d.update(extra)
    return d


def _correr(cfg):
    real = mh.clasificar_con_haiku
    with patch.object(mh, 'clasificar_con_haiku',
                      side_effect=lambda *a, **k: real(*a, **dict(k, query_fn=_fake_query))), \
         patch.object(az, 'llamar_llm', side_effect=AssertionError('sin OpenAI')):
        res = pipeline.process_dossier(io.BytesIO(_dossier()), {}, {}, ai_config=cfg)
    filas = list(load_workbook(io.BytesIO(res['output_data']), read_only=True).active
                 .iter_rows(values_only=True))
    cab = list(filas[0])
    return res, cab, [dict(zip(cab, f)) for f in filas[1:]]

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app():
    with open(os.path.join(REPO, 'app.py'), encoding='utf-8') as f:
        return f.read()


def test_columnas_boletin_sin_hora_b():
    cols = pipeline.columnas_boletin(False)
    assert 'HORA B' not in cols
    assert 'TIPO' not in cols and 'GÉNERO' not in cols
    assert cols.index('Tono_IA') < cols.index('Subtema_IA') < cols.index('Link Nota')
    assert 'FECHA INFORME' in cols


def test_xlsx_general_con_tono_subtema_y_orden():
    res, cab, datos = _correr(_cfg(formato_boletin=True, agrupacion_estricta=True,
                                   clasificar_tipo_genero=False))
    assert 'HORA B' not in cab
    assert cab == [c for c in pipeline.columnas_boletin(False) if c in cab]
    assert datos
    for r in datos:
        assert r['Tono_IA'] in ('Positivo', 'Neutro', 'Negativo')
        assert r['Subtema_IA'] and r['Subtema_IA'] != '-'


def test_app_sin_cliente_fijo():
    src = _app()
    assert 'CLIENTE_NOMBRE' not in src
    assert 'App de:' not in src
    assert 'Defensoría del Pueblo — Limpieza' not in src
    assert '_indice_perfil_defecto = 0' in src
    # La hoja de notas analizadas es de Defensoría: no se ofrece.
    assert 'Excluir notas ya analizadas (Google Sheets)' not in src
    # Ajustes generales de Defensoría: agrupación estricta, guía de subtema y
    # formato de columnas del boletín.
    assert 'st.session_state["_perfil_agrupacion_estricta"] = True' in src
    assert 'GUIA_SUBTEMA_GENERAL' in src
    assert 'Jev' not in src.split('Activar análisis reputacional')[1][:80]


def test_solo_tono_sin_subtema_en_xlsx():
    res, cab, datos = _correr(_cfg(formato_boletin=True, columnas_ia='tono'))
    assert 'Tono_IA' in cab and 'Subtema_IA' not in cab
    assert all(r['Tono_IA'] in ('Positivo', 'Neutro', 'Negativo') for r in datos)
    assert all('Subtema' not in f for f in res['vista'])


def test_solo_subtema_no_analiza_tono():
    PROMPTS.clear()
    res, cab, datos = _correr(_cfg(formato_boletin=True, columnas_ia='subtema'))
    assert 'Subtema_IA' in cab and 'Tono_IA' not in cab
    assert all(r['Subtema_IA'] for r in datos)
    agente = [p for sp, p in PROMPTS if 'auditor-tono' not in sp and 'SIN TEMA' in p]
    assert agente and all('SIN TONO' in p and '<criterio_tono>' not in p for p in agente)
    assert not any('auditor-tono' in sp for sp, _p in PROMPTS)
    assert all('Tono' not in f for f in res['vista'])


def test_ambas_por_defecto():
    PROMPTS.clear()
    res, cab, datos = _correr(_cfg(formato_boletin=True))
    assert 'Tono_IA' in cab and 'Subtema_IA' in cab
    assert not any('SIN TONO' in p for _sp, p in PROMPTS)


def test_hora_del_origen_sale_en_el_xlsx():
    """v4.78: la columna «Hora» del dossier de origen sale en el xlsx, justo
    después de Fecha, con el mismo valor."""
    import datetime as _dt
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Hora'])
    ws.append(['101', 'La marca entrega un colegio', 'La marca entrega un colegio nuevo a la comunidad.',
               'Prensa', '2026-09-01', '08:35:00'])
    ws.append(['102', 'Concejo debate presupuesto', 'El Concejo y la marca debatieron el presupuesto.',
               'Radio', '2026-09-02', _dt.time(17, 5)])
    buf = io.BytesIO()
    wb.save(buf)
    global _dossier
    original = _dossier
    _dossier = lambda: buf.getvalue()  # noqa: E731
    try:
        res, cab, datos = _correr(_cfg(formato_boletin=True))
    finally:
        _dossier = original
    assert cab.index('Hora') == cab.index('Fecha') + 1
    horas = {str(d['ID Noticia']): str(d['Hora']) for d in datos}
    assert horas['101'].startswith('08:35') and horas['102'].startswith('17:05')
    # Fechas «aaaa-mm-dd» en texto: no se voltean día y mes.
    fechas = {str(d['ID Noticia']): d['Fecha'] for d in datos}
    assert (fechas['101'].month, fechas['101'].day) == (9, 1)
    assert (fechas['102'].month, fechas['102'].day) == (9, 2)


def test_leer_fechas():
    import datetime as _dt
    import pandas as pd
    got = pipeline._leer_fechas(pd.Series(['2026-09-01', '01/09/2026', '13/02/2026',
                                           _dt.datetime(2026, 9, 1, 10), None, 'x',
                                           '2026-09-01 08:00:00'])).tolist()
    sep1 = pd.Timestamp('2026-09-01')
    assert got[:4] == [sep1, sep1, pd.Timestamp('2026-02-13'), sep1]
    assert pd.isna(got[4]) and pd.isna(got[5]) and got[6] == sep1


def test_tamano_por_tipo_de_medio():
    """v4.80: «Tamaño» va justo antes de Audiencia. Internet, Prensa y
    Revistas: la Dimensioncm2 del origen. Radio y Televisión: el valor de
    «Duración - Nro. Caracteres» (que también se queda en su columna). Las
    duraciones «hh:mm:ss» salen tal cual, no como número."""
    import datetime as _dt
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Tipo de Medio',
               'Dimensioncm2', 'Duración - Nro. Caracteres', 'Audiencia'])
    filas = [
        ('101', 'Prensa', 'El Tiempo', 120.5, 3400),
        ('102', 'Radio', 'Caracol Radio', '', '00:02:30'),
        ('103', 'Televisión', 'Noticias Caracol', '', _dt.time(0, 1, 45)),
        ('104', 'Internet', 'Semana.com', '', 2800),
        ('105', 'Radio', 'Blu Radio', '', 95),
    ]
    for i, (idn, tipo, medio, dim, dur) in enumerate(filas):
        ws.append([idn, 'La marca anuncia plan %d en Bogotá' % i,
                   'La marca anunció el plan número %d para la ciudad de Bogotá.' % i,
                   medio, '0%d/09/2026' % (i + 1), tipo, dim, dur, 1000])
    buf = io.BytesIO()
    wb.save(buf)
    global _dossier
    original = _dossier
    _dossier = lambda: buf.getvalue()  # noqa: E731
    try:
        res, cab, datos = _correr(_cfg(formato_boletin=True))
    finally:
        _dossier = original
    assert 'Tamaño' in cab and 'Dimensioncm2' not in cab
    assert cab.index('Tamaño') == cab.index('Audiencia') - 1
    d = {str(x['ID Noticia']): x for x in datos}
    k = 'Duración - Nro. Caracteres'
    assert float(d['101']['Tamaño']) == 120.5 and int(d['101'][k]) == 3400   # prensa
    assert d['102']['Tamaño'] == '00:02:30' and d['102'][k] == '00:02:30'    # radio (texto)
    assert d['103']['Tamaño'] == '00:01:45' and d['103'][k] == '00:01:45'    # TV (hora de Excel)
    assert d['104']['Tamaño'] in (None, '', '-') and int(d['104'][k]) == 2800  # internet
    assert int(d['105']['Tamaño']) == 95 and int(d['105'][k]) == 95          # radio (segundos)


def test_duracion_texto():
    import datetime as _dt
    assert pipeline._es_duracion('00:02:30') and pipeline._es_duracion('2:30')
    assert pipeline._es_duracion(_dt.time(0, 2, 30)) and pipeline._es_duracion(_dt.timedelta(seconds=150))
    assert not pipeline._es_duracion('3400') and not pipeline._es_duracion(150)
    assert not pipeline._es_duracion(_dt.datetime(2026, 9, 1))
    assert pipeline._texto_duracion(_dt.timedelta(seconds=150)) == '00:02:30'
    assert pipeline._texto_duracion(_dt.time(1, 2, 3)) == '01:02:03'


def test_nro_pagina_y_menciones_empresa():
    """v4.81: «Nro. Pagina» justo antes de «Duración - Nro. Caracteres» y
    «Menciones - Empresa» justo después de «Link (Streaming - Imagen)»: el
    valor de «Menciones - Empresa» del origen en Radio y Televisión, el de
    «Empresa rel.» en Internet, Prensa y Revistas."""
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Tipo de Medio',
               'Nro. Pagina', 'Duración - Nro. Caracteres', 'Menciones - Empresa', 'Empresa rel.'])
    filas = [('201', 'Prensa', 'El Tiempo', 7, 3400, '', 'Marca S.A.'),
             ('202', 'Radio', 'Caracol Radio', '', '00:02:30', 'Marca', ''),
             ('203', 'Internet', 'Semana.com', '', 2800, 'otra cosa', 'Marca Colombia')]
    for i, (idn, tipo, medio, pag, dur, men, rel) in enumerate(filas):
        ws.append([idn, 'La marca anuncia plan %d en Bogotá' % i,
                   'La marca anunció el plan número %d para la ciudad de Bogotá.' % i,
                   medio, '0%d/09/2026' % (i + 1), tipo, pag, dur, men, rel])
    buf = io.BytesIO()
    wb.save(buf)
    global _dossier
    original = _dossier
    _dossier = lambda: buf.getvalue()  # noqa: E731
    try:
        res, cab, datos = _correr(_cfg(formato_boletin=True))
    finally:
        _dossier = original
    assert cab.index('Nro. Pagina') == cab.index('Duración - Nro. Caracteres') - 1
    assert cab.index('Menciones - Empresa') == cab.index('Link (Streaming - Imagen)') + 1
    assert 'Empresa rel.' not in cab
    d = {str(x['ID Noticia']): x for x in datos}
    assert int(d['201']['Nro. Pagina']) == 7
    assert d['201']['Menciones - Empresa'] == 'Marca S.A.'        # prensa: Empresa rel.
    assert d['202']['Menciones - Empresa'] == 'Marca'             # radio: Menciones - Empresa
    assert d['203']['Menciones - Empresa'] == 'Marca Colombia'    # internet: Empresa rel.


def _correr_filas(encabezados, filas, **cfg):
    wb = Workbook()
    ws = wb.active
    ws.append(encabezados)
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    global _dossier
    original = _dossier
    _dossier = lambda: buf.getvalue()  # noqa: E731
    try:
        return _correr(_cfg(formato_boletin=True, **cfg))
    finally:
        _dossier = original


def _fila_marcas(idn, tipo, men, rel):
    return [idn, 'La marca y Sanofi firman alianza %s' % idn,
            'La marca y Sanofi firmaron una alianza en Bogotá.', 'Medio X', '01/09/2026', tipo, men, rel]


def test_varias_marcas_duplican_la_fila_con_encabezados_variantes():
    """v4.82: marcas separadas por «;» en «Menciones - Empresa» (radio/TV) o
    «Empresa rel.» (internet, prensa, revistas) => una fila por marca, cada
    una con una sola marca, aunque el encabezado venga escrito de otra forma
    (mayúsculas, sin punto, sin espacios, espacio al final)."""
    base = ['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Tipo de Medio']
    for hm, hr in [('Menciones - Empresa', 'Empresa rel.'), ('Menciones - Empresa', 'Empresa Rel.'),
                   ('Menciones-Empresa', 'Empresa rel'), ('MENCIONES - EMPRESA', 'EMPRESA REL.'),
                   ('Menciones - Empresa ', 'Empresa rel. ')]:
        res, cab, datos = _correr_filas(base + [hm, hr], [
            _fila_marcas('1', 'Radio', 'Marca; Sanofi; GSK', ''),
            _fila_marcas('2', 'Prensa', '', 'Marca; Sanofi'),
            _fila_marcas('3', 'Internet', '', 'Marca;Sanofi;Marca')])
        got = sorted((str(d['ID Noticia']), d['Menciones - Empresa']) for d in datos)
        assert got == [('1', 'GSK'), ('1', 'Marca'), ('1', 'Sanofi'), ('2', 'Marca'), ('2', 'Sanofi'),
                       ('3', 'Marca'), ('3', 'Sanofi')], (hm, hr, got)


def test_menciones_columna_de_respaldo():
    """Si la columna que corresponde al tipo de medio viene vacía, se usa la otra."""
    base = ['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Tipo de Medio',
            'Menciones - Empresa', 'Empresa rel.']
    res, cab, datos = _correr_filas(base, [_fila_marcas('1', 'Web', '', 'Marca; Sanofi'),
                                           _fila_marcas('2', 'Radio', '', 'Marca')])
    got = sorted((str(d['ID Noticia']), d['Menciones - Empresa']) for d in datos)
    assert got == [('1', 'Marca'), ('1', 'Sanofi'), ('2', 'Marca')]


def test_columnas_personalizadas_marca_original_tambien_se_divide():
    base = ['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Tipo de Medio',
            'Menciones - Empresa', 'Empresa Rel.']
    res, cab, datos = _correr_filas(base, [_fila_marcas('1', 'Prensa', '', 'Marca; Sanofi')],
                                    preservar_columnas=True)
    assert sorted(d['Empresa Rel.'] for d in datos) == ['Marca', 'Sanofi']


def test_canonizar_columnas():
    import pandas as pd
    df = pd.DataFrame({'HORA ': ['08:00'], 'DimensionCm2': [12], 'Empresa Rel.': ['A;B'],
                       'Hora': ['09:00']})
    out = pipeline.canonizar_columnas(df)
    assert list(out['Dimensioncm2']) == [12] and list(out['Empresa rel.']) == ['A;B']
    assert list(out['Hora']) == ['09:00']          # la exacta manda
    assert 'Empresa Rel.' in out.columns            # la original se conserva
