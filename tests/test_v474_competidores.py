"""v4.74: modo competidores. Cada fila se clasifica hacia la marca de su
«Menciones - Empresa» (o la que nombra la nota); el motor corre una vez por
marca y el xlsx agrega «Marca analizada» antes del tono."""
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
            tono = 'Negativo' if 'critican' in ctx else ('Positivo' if 'lidera' in ctx else 'Neutro')
            filas.append({'id': n['id'], 'subtema': 'Desempeño comercial del trimestre', 'tono': tono})
        await mh._guardar_clasificacion({'filas_json': json.dumps(filas)})
        desde = pag['siguiente']
    yield _ok()


TEXTO = ('GSK lidera las ventas de vacunas en Colombia este trimestre con un alza del 12 por ciento. '
         'Por otro lado, critican a Sanofi por el retraso en la entrega de dosis a los hospitales.')


def _dossier():
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha', 'Menciones - Empresa'])
    ws.append(['1', 'Mercado de vacunas en Colombia', TEXTO, 'Portafolio', '2026-09-01', 'GSK; Sanofi'])
    ws.append(['2', 'AstraZeneca lidera nuevo ensayo en Bogotá',
               'AstraZeneca lidera un ensayo clínico de cáncer de pulmón en Bogotá con 300 pacientes.',
               'El Tiempo', '2026-09-02', ''])
    ws.append(['3', 'Clima en Bogotá', 'Lluvias fuertes en la capital durante el fin de semana.',
               'Semana', '2026-09-03', ''])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


COMP = pipeline.parsear_competidores(
    'GSK: GlaxoSmithKline\nModerna\nAstraZeneca: AZ\nSanofi: Sanofi Pasteur')


def _correr(**extra):
    CLIENTES.clear()
    cfg = {'enabled': True, 'brand': '', 'aliases': [], 'voceros': [], 'criterio': None,
           'incluir_tema': False, 'motor': 'haiku', 'api_key': None, 'formato_boletin': True,
           'agrupacion_estricta': True, 'competidores': COMP, 'memoria': False}
    cfg.update(extra)
    real = mh.clasificar_con_haiku
    with patch.object(mh, 'clasificar_con_haiku',
                      side_effect=lambda *a, **k: real(*a, **dict(k, query_fn=_fake_query))), \
         patch.object(az, 'llamar_llm', side_effect=AssertionError('sin OpenAI')):
        res = pipeline.process_dossier(io.BytesIO(_dossier()), {}, {}, ai_config=cfg)
    filas = list(load_workbook(io.BytesIO(res['output_data']), read_only=True).active
                 .iter_rows(values_only=True))
    cab = list(filas[0])
    return res, cab, [dict(zip(cab, f)) for f in filas[1:]]


def test_parsear():
    assert [c['brand'] for c in COMP] == ['GSK', 'Moderna', 'AstraZeneca', 'Sanofi']
    assert COMP[0]['aliases'] == ['GlaxoSmithKline']
    c = pipeline.parsear_competidores('Pfizer: Pfizer Inc | Ana Gómez; Luis Díaz\nPfizer\n- Bayer;')
    assert c == [{'brand': 'Pfizer', 'aliases': ['Pfizer Inc'], 'voceros': ['Ana Gómez', 'Luis Díaz']},
                 {'brand': 'Bayer', 'aliases': [], 'voceros': []}]


def test_asignacion_por_mencion_y_por_texto():
    km = pipeline.KEY_MAP
    assert pipeline.asignar_marca_competidor({'Menciones - Empresa': 'GlaxoSmithKline Colombia'}, COMP, km) == 'GSK'
    assert pipeline.asignar_marca_competidor(
        {'Título': 'AZ y Sanofi firman alianza', 'Menciones - Empresa': ''}, COMP, km) == 'AstraZeneca'
    assert pipeline.asignar_marca_competidor(
        {'Título': 'Clima', 'Resumen - Aclaracion': 'Sanofi Pasteur abre planta'}, COMP, km) == 'Sanofi'
    assert pipeline.asignar_marca_competidor({'Título': 'Clima en Bogotá'}, COMP, km) is None


def test_cada_fila_con_el_tono_de_su_marca():
    res, cab, datos = _correr()
    assert cab.index(pipeline.COL_MARCA_ANALIZADA) == cab.index('Tono_IA') - 1
    por = {(str(d['ID Noticia']), d[pipeline.COL_MARCA_ANALIZADA]): d for d in datos}
    # La misma nota: Sanofi (criticada) Negativo; GSK no hereda esa crítica.
    # («lidera las ventas» es un dato: la guarda de tono del motor lo deja Neutro.)
    assert por[('1', 'Sanofi')]['Tono_IA'] == 'Negativo'
    assert por[('1', 'GSK')]['Tono_IA'] in ('Positivo', 'Neutro')
    assert por[('2', 'AstraZeneca')]['Tono_IA'] == 'Positivo'
    assert por[('3', pipeline.SIN_MARCA_LISTADA)]['Tono_IA'] in ('-', None)
    # Una pasada por marca con filas (Moderna no aparece: no se corre).
    assert sorted(set(CLIENTES)) == ['AstraZeneca', 'GSK', 'Sanofi']
    comp = res['analisis']['competidores']
    assert comp['GSK']['filas'] == 1 and comp['Moderna']['filas'] == 0
    assert res['analisis']['competidores_sin_marca'] == 1


def test_competidores_solo_subtema():
    res, cab, datos = _correr(columnas_ia='subtema')
    assert 'Tono_IA' not in cab
    assert cab.index(pipeline.COL_MARCA_ANALIZADA) == cab.index('Subtema_IA') - 1
