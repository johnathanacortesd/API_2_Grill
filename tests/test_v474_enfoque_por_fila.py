"""v4.74: el tono de cada fila se mide hacia la marca de su «Menciones -
Empresa» / «Empresa rel.»: el cliente (marca, alias y voceros escritos) si
es él o viene vacía; si es otra marca, hacia esa marca. Sin filas de otras
marcas es la corrida de siempre."""
import io
import json
import os
import re
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
from claude_agent_sdk import ResultMessage  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402

import analyzer_tono_tema as az  # noqa: E402
import motor_haiku as mh  # noqa: E402
import pipeline  # noqa: E402

CLIENTES = []


def _ok(result=None):
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id='fake', total_cost_usd=0.01, result=result)


async def _fake_query(prompt, options):
    if 'auditor-tono' in (options.system_prompt or '') or not getattr(options, 'mcp_servers', None):
        yield _ok('{"resultados":[]}')
        return
    cliente = re.search(r'CLIENTE: (.+)', prompt).group(1).strip()
    CLIENTES.append(cliente)
    desde = 0
    while desde is not None:
        pag = json.loads((await mh._leer_noticias({'desde': desde}))['content'][0]['text'])
        filas = []
        for n in pag['noticias']:
            if 'igual_a' in n:
                continue
            # Como el agente real: el tono sale de lo que la nota dice del CLIENTE.
            frases = [f for f in re.split(r'(?<=[.!?])\s+', n.get('contexto') or '')
                      if cliente.lower() in f.lower()]
            ctx = ' '.join(frases).lower()
            tono = 'Negativo' if 'critican' in ctx else ('Positivo' if 'premio' in ctx else 'Neutro')
            filas.append({'id': n['id'], 'subtema': 'Desempeño comercial del trimestre', 'tono': tono})
        await mh._guardar_clasificacion({'filas_json': json.dumps(filas)})
        desde = pag['siguiente']
    yield _ok()


TEXTO = ('GSK recibe el premio a la mejor farmacéutica del año por su programa de vacunación. '
         'Por otro lado, critican a Sanofi por el retraso en la entrega de dosis a los hospitales.')


def _dossier(menciones):
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Menciones - Empresa'])
    for i, (tit, txt, men) in enumerate(menciones, 1):
        ws.append([str(i), tit, txt, 'Portafolio', '2026-09-0%d' % i, men])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _correr(filas, **extra):
    CLIENTES.clear()
    cfg = {'enabled': True, 'brand': 'GSK', 'aliases': ['GlaxoSmithKline'], 'voceros': [],
           'criterio': None, 'incluir_tema': False, 'motor': 'haiku', 'api_key': None,
           'formato_boletin': True, 'agrupacion_estricta': True, 'memoria': False}
    cfg.update(extra)
    real = mh.clasificar_con_haiku
    with patch.object(mh, 'clasificar_con_haiku',
                      side_effect=lambda *a, **k: real(*a, **dict(k, query_fn=_fake_query))), \
         patch.object(az, 'llamar_llm', side_effect=AssertionError('sin OpenAI')):
        res = pipeline.process_dossier(io.BytesIO(_dossier(filas)), {}, {}, ai_config=cfg)
    hoja = list(load_workbook(io.BytesIO(res['output_data']), read_only=True).active
                .iter_rows(values_only=True))
    cab = list(hoja[0])
    return res, cab, [dict(zip(cab, f)) for f in hoja[1:]]


def test_enfoque_de_fila():
    f = pipeline.enfoque_de_fila
    assert f({'Menciones - Empresa': 'GSK'}, 'GSK', []) is None
    assert f({'Menciones - Empresa': 'GlaxoSmithKline Colombia'}, 'GSK', ['GlaxoSmithKline']) is None
    assert f({'Menciones - Empresa': ''}, 'GSK', []) is None
    assert f({}, 'GSK', []) is None
    assert f({'Menciones - Empresa': 'Sanofi'}, 'GSK', ['GlaxoSmithKline']) == 'Sanofi'


def test_solo_cliente_es_la_corrida_de_siempre():
    res, cab, datos = _correr([('Mercado de vacunas', TEXTO, 'GSK'),
                               ('Ensayo clínico en Bogotá', 'GSK abre un ensayo clínico en Bogotá.', '')])
    assert CLIENTES == ['GSK']
    assert pipeline.COL_MARCA_ANALIZADA not in cab
    assert 'marcas_analizadas' not in res['analisis']


def test_fila_de_otra_marca_lleva_su_propio_tono():
    res, cab, datos = _correr([('Mercado de vacunas', TEXTO, 'GSK; Sanofi')])
    assert sorted(CLIENTES) == ['GSK', 'Sanofi']
    assert cab.index(pipeline.COL_MARCA_ANALIZADA) == cab.index('Tono_IA') - 1
    por = {d[pipeline.COL_MARCA_ANALIZADA]: d for d in datos}
    assert por['Sanofi']['Tono_IA'] == 'Negativo'
    assert por['GSK']['Tono_IA'] == 'Positivo'
    assert all(d['Subtema_IA'] and d['Subtema_IA'] != '-' for d in datos)
    assert res['analisis']['marcas_analizadas']['Sanofi'] == {'filas': 1, 'ok': True}


def test_una_sola_marca_escrita_distinto_es_el_cliente():
    res, cab, datos = _correr([('Mercado de vacunas', TEXTO, 'Glaxo Smith Kline S.A.')],
                              aliases=[])
    assert CLIENTES == ['GSK']
    assert pipeline.COL_MARCA_ANALIZADA not in cab


def test_marca_que_falla_no_tumba_las_demas():
    real = pipeline.enrich_rows_with_ai

    def _falla(**kw):
        if kw['brand'] == 'Sanofi':
            raise RuntimeError('El motor Claude Haiku falló: prueba')
        return real(**kw)
    with patch.object(pipeline, 'enrich_rows_with_ai', side_effect=_falla):
        res, cab, datos = _correr([('Mercado de vacunas', TEXTO, 'GSK; Sanofi')])
    por = {d[pipeline.COL_MARCA_ANALIZADA]: d for d in datos}
    assert por['GSK']['Tono_IA'] == 'Positivo'
    assert por['Sanofi']['Tono_IA'] in ('-', None)
    assert any('Sanofi' in x['motivo'] for x in res['analisis']['para_revisar'])
    assert res['analisis']['marcas_analizadas']['Sanofi']['ok'] is False
