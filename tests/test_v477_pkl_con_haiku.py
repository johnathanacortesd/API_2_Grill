"""v4.77: los PKL del cliente se pueden subir y se aplican también con Claude
Haiku: el PKL de tono pone el Tono (Haiku no lo analiza: «SIN TONO»), el de
tema pone el Tema y Haiku hace el Subtema, que nunca lo pone un PKL."""
import io
import json
import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import joblib  # noqa: E402
from claude_agent_sdk import ResultMessage  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402

import analyzer_tono_tema as az  # noqa: E402
import motor_haiku as mh  # noqa: E402
import pipeline  # noqa: E402

PROMPTS = []


def _ok(result=None):
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id='fake', total_cost_usd=0.0, result=result)


async def _fake_query(prompt, options):
    PROMPTS.append((options.system_prompt or '', prompt))
    if 'auditor-tono' in (options.system_prompt or '') or not getattr(options, 'mcp_servers', None):
        yield _ok('{"resultados":[]}')
        return
    desde = 0
    while desde is not None:
        pag = json.loads((await mh._leer_noticias({'desde': desde}))['content'][0]['text'])
        filas = [{'id': n['id'], 'subtema': 'Avance del programa de vacunación', 'tono': 'Neutro',
                  'tema': 'Tema de Haiku'}
                 for n in pag['noticias'] if 'igual_a' not in n]
        await mh._guardar_clasificacion({'filas_json': json.dumps(filas)})
        desde = pag['siguiente']
    yield _ok()


def _pkl(textos, etiquetas):
    m = Pipeline([('tfidf', TfidfVectorizer()), ('clf', LogisticRegression())])
    m.fit(textos, etiquetas)
    buf = io.BytesIO()
    joblib.dump(m, buf)
    return buf.getvalue()


PKL_TONO = _pkl(['la marca recibe premio y reconocimiento', 'premio para la marca',
                 'critican a la marca por fallas', 'denuncian a la marca'],
                ['Positivo', 'Positivo', 'Negativo', 'Negativo'])
PKL_TEMA = _pkl(['premio reconocimiento', 'premio', 'critican fallas', 'denuncian'],
                ['Reputación', 'Reputación', 'Crisis', 'Crisis'])


def _dossier():
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha'])
    ws.append(['1', 'La marca recibe premio', 'La marca recibe premio y reconocimiento nacional.',
               'Prensa', '2026-09-01'])
    ws.append(['2', 'Critican a la marca', 'Critican a la marca por fallas en el servicio.',
               'Radio', '2026-09-02'])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _correr(**extra):
    PROMPTS.clear()
    cfg = {'enabled': True, 'brand': 'Marca', 'aliases': [], 'voceros': [], 'criterio': None,
           'incluir_tema': False, 'motor': 'haiku', 'api_key': None, 'formato_boletin': True,
           'agrupacion_estricta': True, 'memoria': False}
    cfg.update(extra)
    real = mh.clasificar_con_haiku
    with patch.object(mh, 'clasificar_con_haiku',
                      side_effect=lambda *a, **k: real(*a, **dict(k, query_fn=_fake_query))), \
         patch.object(az, 'llamar_llm', side_effect=AssertionError('sin OpenAI')):
        res = pipeline.process_dossier(io.BytesIO(_dossier()), {}, {}, ai_config=cfg)
    hoja = list(load_workbook(io.BytesIO(res['output_data']), read_only=True).active
                .iter_rows(values_only=True))
    cab = list(hoja[0])
    return res, cab, {str(d['ID Noticia']): d for d in (dict(zip(cab, f)) for f in hoja[1:])}


def _agente():
    return [p for sp, p in PROMPTS if 'auditor-tono' not in sp and 'CLIENTE:' in p]


def test_pkl_de_tono_con_haiku():
    res, cab, d = _correr(tone_pkl_bytes=PKL_TONO)
    assert d['1']['Tono_IA'] == 'Positivo' and d['2']['Tono_IA'] == 'Negativo'
    # El subtema lo pone Haiku (con las guardas de fidelidad del motor), nunca el PKL.
    assert all(x['Subtema_IA'] and x['Subtema_IA'] not in ('-', 'Positivo', 'Negativo')
               for x in d.values())
    assert _agente() and all('SIN TONO' in p for p in _agente())
    assert not any('auditor-tono' in sp for sp, _p in PROMPTS)


def test_pkl_de_tema_con_haiku():
    res, cab, d = _correr(theme_pkl_bytes=PKL_TEMA)
    # El tema sale del PKL aunque no se haya pedido Tema_IA, entre Tono y Subtema.
    assert cab.index('Tono_IA') < cab.index('Tema_IA') < cab.index('Subtema_IA')
    assert d['1']['Tema_IA'] == 'Reputación' and d['2']['Tema_IA'] == 'Crisis'
    assert all('SIN TEMA' in p for p in _agente())     # Haiku no pide tema
    assert all(x['Subtema_IA'] and x['Subtema_IA'] != '-' for x in d.values())


def test_tema_ia_pedido_sale_en_el_xlsx():
    res, cab, d = _correr(incluir_tema=True)
    assert cab.index('Tono_IA') < cab.index('Tema_IA') < cab.index('Subtema_IA')
    assert all(x['Tema_IA'] for x in d.values())


def test_solo_pkl_sin_ia():
    res, cab, d = _correr(enabled=False, tone_pkl_bytes=PKL_TONO)
    assert 'Subtema_IA' not in cab and 'Tono_IA' in cab
    assert d['1']['Tono_IA'] == 'Positivo'


def test_sin_pkl_haiku_hace_todo():
    res, cab, d = _correr()
    assert all('SIN TONO' not in p for p in _agente())
