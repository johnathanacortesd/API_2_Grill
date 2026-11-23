# -*- coding: utf-8 -*-
"""Motor Claude Haiku (motor_haiku.py) con un `query` falso: $0, sin red.

El `query` falso hace lo que haría el agente: lee las páginas con la tool
real, guarda con la tool real y pide los candidatos de consistencia.
"""
from __future__ import annotations

import io
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

from claude_agent_sdk import ResultMessage  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402

import analyzer_tono_tema as az  # noqa: E402
import motor_haiku as mh  # noqa: E402
import pipeline  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISTO: dict = {}


def _resultado():
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=5, session_id='fake', total_cost_usd=0.0123)


async def fake_query(prompt, options):
    """Agente falso: clasifica cada página con reglas fijas usando las tools reales."""
    VISTO['prompt'] = prompt
    VISTO['options'] = options
    incluir = 'INCLUIR TEMA' in prompt
    desde = 0
    while desde is not None:
        pag = json.loads((await mh._leer_noticias({'desde': desde}))['content'][0]['text'])
        filas = []
        for n in pag['noticias']:
            VISTO.setdefault('enviados', []).append(n['id'])
            if 'igual_a' in n:
                continue
            ctx = n.get('contexto') or ''
            f = {'id': n['id'], 'subtema': 'Hecho haiku %s' % n['id'][:20],  # Clausifier: 3+ palabras
                 'tono': 'Positivo' if 'entrega' in ctx.lower() else 'Neutro'}
            if incluir:
                f['tema'] = 'Gestión institucional'
            filas.append(f)
        r = await mh._guardar_clasificacion({'filas_json': json.dumps(filas)})
        VISTO['ultimo_guardado'] = r['content'][0]['text']
        desde = pag['siguiente']
    cand = json.loads((await mh._candidatos_consistencia({'desde': 0}))['content'][0]['text'])
    VISTO['candidatos'] = cand['total']
    yield _resultado()


def _items():
    return [
        {'id': '1', 'titulo': 'Alcaldía entrega colegio en Bosa', 'contexto': 'La Alcaldía entrega un colegio nuevo.'},
        {'id': '2', 'titulo': 'Alcaldía entrega colegio en Bosa', 'contexto': 'otro medio'},   # mismo título
        {'id': '3', 'titulo': 'Debate en el Concejo', 'contexto': 'El Concejo debatió el presupuesto.'},
        {'id': '4', 'titulo': 'copia exacta', 'contexto': '-', 'duplicado_de': '3'},           # duplicado explícito
    ]


class TestMotorHaiku(unittest.TestCase):
    def setUp(self):
        VISTO.clear()

    def test_todas_las_filas_y_duplicados_heredan(self):
        r = mh.clasificar_con_haiku(_items(), 'Alcaldía de Bogotá', ['Alcaldía'], ['Carlos Fernando Galán'],
                                    criterio_texto='Regla X', incluir_tema=False, query_fn=fake_query)
        et = r['etiquetas']
        self.assertEqual(set(et), {'1', '2', '3', '4'})
        for v in et.values():
            self.assertIn(v['tono'], mh.TONOS)
            self.assertTrue(v['subtema'])
            self.assertNotIn('tema', v)
        self.assertEqual(et['2'], et['1'])
        self.assertEqual(et['4'], et['3'])
        self.assertEqual(et['1']['tono'], 'Positivo')
        self.assertEqual(r['faltan'], 0)
        self.assertAlmostEqual(r['costo_usd'], 0.0123)
        self.assertEqual(r['turnos'], 5)
        self.assertNotIn('4', VISTO['enviados'])          # el duplicado explícito no se envía
        self.assertIn('Faltan por clasificar: 0', VISTO['ultimo_guardado'])
        self.assertIn('<criterio_tono>', VISTO['prompt'])
        self.assertIn('SIN TEMA', VISTO['prompt'])

    def test_tema_solo_con_incluir_tema(self):
        r = mh.clasificar_con_haiku(_items(), 'Alcaldía', incluir_tema=True, query_fn=fake_query)
        self.assertTrue(all(v.get('tema') == 'Gestión institucional' for v in r['etiquetas'].values()))

    def test_opciones_aisladas(self):
        mh.clasificar_con_haiku(_items(), 'Alcaldía', query_fn=fake_query)
        o = VISTO['options']
        self.assertEqual(o.setting_sources, [])
        for t in ('Bash', 'Edit', 'Read', 'Grep', 'Glob'):
            self.assertIn(t, o.disallowed_tools)
        self.assertIn('revisor-subtemas', o.agents)
        self.assertEqual(o.agents['revisor-subtemas'].model, 'haiku')
        self.assertFalse(str(o.cwd).startswith(REPO))
        self.assertGreater(o.max_budget_usd, 0)
        self.assertGreater(o.max_turns, 0)

    def test_guardar_valida_tono_y_taxonomia(self):
        mh.preparar_estado(_items(), incluir_tema=True, taxonomia=['Educación', 'Movilidad'])
        import asyncio
        malo = asyncio.run(mh._guardar_clasificacion(
            {'filas_json': json.dumps([{'id': '1', 'subtema': 's', 'tono': 'Bueno'}])}))
        self.assertTrue(malo.get('is_error'))
        fuera = asyncio.run(mh._guardar_clasificacion(
            {'filas_json': json.dumps([{'id': '1', 'subtema': 's', 'tono': 'Neutro', 'tema': 'Deportes'}])}))
        self.assertTrue(fuera.get('is_error'))
        ok = asyncio.run(mh._guardar_clasificacion(
            # Clausifier 2026-11-07: el subtema debe pasar el validador de forma
            # (3-7 palabras, sin verbo inicial); 's' ahora se rechaza.
            {'filas_json': json.dumps([{'id': '1', 'subtema': 'Feria escolar de ciencias',
                                        'tono': 'Neutro', 'tema': 'educacion'}])}))
        self.assertFalse(ok.get('is_error'))
        self.assertEqual(mh._ESTADO['res']['2']['tema'], 'Educación')   # canónico y heredado

    def test_sin_clave_falla_claro(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ANTHROPIC_API_KEY', None)
            with self.assertRaises(mh.ErrorMotorHaiku):
                mh.clasificar_con_haiku(_items(), 'Alcaldía')


def _rows():
    return [
        {'Título': 'La marca entrega un colegio en Bosa',
         'CuerpoEs': 'La marca entrega un colegio nuevo a la comunidad de Bosa.'},
        {'Título': 'Concejo debate el presupuesto distrital',
         'CuerpoEs': 'El Concejo debatió el presupuesto y la marca presentó cifras.'},
    ]


class TestEnrichMotor(unittest.TestCase):
    def _haiku(self, incluir_tema):
        real = mh.clasificar_con_haiku
        with patch.object(mh, 'clasificar_con_haiku',
                          side_effect=lambda *a, **k: real(*a, query_fn=fake_query, **k)) as m, \
             patch.object(az, 'etiquetar_grupos', side_effect=AssertionError('OpenAI no')), \
             patch.object(az, 'consolidar_hechos_llm', side_effect=AssertionError('OpenAI no')), \
             patch.object(az, 'asignar_temas', side_effect=AssertionError('OpenAI no')), \
             patch.object(az, 'llamar_llm', side_effect=AssertionError('OpenAI no')):
            out = az.enrich_rows_with_ai(
                _rows(), {'titulo': 'Título'}, brand='Marca', aliases=[], api_key='k',
                extra={'motor': 'haiku', 'incluir_tema': incluir_tema, 'voceros': []},
                progress_callback=lambda p, msg: None)
        self.assertTrue(m.called)
        return out

    def test_haiku_sin_tema(self):
        out = self._haiku(False)
        for r in out:
            self.assertIn(r['Tono_IA'], ('Positivo', 'Neutro', 'Negativo'))
            self.assertTrue(r['Subtema_IA'])
            self.assertFalse(r['Tema_IA'])
        res = az.ultimo_resumen()
        self.assertEqual(res.get('motor'), 'haiku')
        self.assertAlmostEqual(res.get('costo_aprox_usd'), 0.0123)

    def test_haiku_con_tema(self):
        out = self._haiku(True)
        self.assertTrue(all(r['Tema_IA'] == 'Gestión institucional' for r in out))

    def test_openai_igual_que_antes(self):
        with patch.object(mh, 'clasificar_con_haiku', side_effect=AssertionError('Haiku no')), \
             patch.object(az, 'etiquetar_grupos',
                          return_value={1: {'tono': 'Positivo', 'sub_tema': 'Entrega de colegio'},
                                        2: {'tono': 'Neutro', 'sub_tema': 'Debate del presupuesto'}}) as eg, \
             patch.object(az, 'consolidar_hechos_llm', return_value=0), \
             patch.object(az, 'coherencia_subtemas_por_texto', return_value={}, create=True), \
             patch.object(az, 'reparar_copias_finales', side_effect=lambda *a, **k: k.get('ajustes_subtema') or {}):
            out = az.enrich_rows_with_ai(
                _rows(), {'titulo': 'Título'}, brand='Marca', aliases=[], api_key='k',
                extra={'motor': 'openai', 'incluir_tema': False, 'voceros': []},
                progress_callback=lambda p, msg: None)
        self.assertTrue(eg.called)
        self.assertEqual(az.ultimo_resumen().get('motor'), 'openai')
        self.assertNotIn('haiku', az.ultimo_resumen())
        for r in out:
            self.assertIn(r['Tono_IA'], ('Positivo', 'Neutro', 'Negativo'))
            self.assertTrue(r['Subtema_IA'])


def _dossier_bytes():
    wb = Workbook()
    ws = wb.active
    ws.append(['Título', 'Resumen - Aclaracion', 'Medio', 'Fecha'])
    ws.append(['La marca entrega un colegio', 'La marca entrega un colegio nuevo.', 'Prensa', '2026-09-01'])
    ws.append(['Concejo debate presupuesto', 'El Concejo y la marca debatieron.', 'Prensa', '2026-09-02'])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestPipelineMotor(unittest.TestCase):
    def _cfg(self, motor):
        return {'enabled': True, 'brand': 'Marca', 'aliases': [], 'voceros': [], 'criterio': None,
                'incluir_tema': False, 'api_key': 'k', 'motor': motor,
                'tone_pkl_bytes': b'x', 'theme_pkl_bytes': None}

    def test_haiku_salta_pkl_y_pasa_motor(self):
        def enrich(rows, **kw):
            self.assertEqual(kw['extra']['motor'], 'haiku')
            self.assertIsNone(kw['tone_model'])
            for r in rows:
                r.update(Tono_IA='Neutro', Subtema_IA='x', Tema_IA='')
            return rows
        with patch.object(pipeline, '_load_optional_pkl_models', side_effect=AssertionError('PKL no')), \
             patch.object(pipeline, 'enrich_rows_with_ai', side_effect=enrich):
            res = pipeline.process_dossier(io.BytesIO(_dossier_bytes()), {}, {}, ai_config=self._cfg('haiku'))
        ws = load_workbook(io.BytesIO(res['output_data']), read_only=True).active
        cols = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        self.assertIn('Tono_IA', cols)

    def test_openai_carga_pkl_como_antes(self):
        with patch.object(pipeline, '_load_optional_pkl_models', return_value=(None, None)) as lp, \
             patch.object(pipeline, 'enrich_rows_with_ai', side_effect=lambda rows, **kw: rows):
            pipeline.process_dossier(io.BytesIO(_dossier_bytes()), {}, {}, ai_config=self._cfg('openai'))
        self.assertTrue(lp.called)


if __name__ == '__main__':
    unittest.main()
