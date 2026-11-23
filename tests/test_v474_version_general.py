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


async def _fake_query(prompt, options):
    """Agente falso ($0, sin red): usa las tools reales del motor."""
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
