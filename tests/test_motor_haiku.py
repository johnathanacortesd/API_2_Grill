# -*- coding: utf-8 -*-
"""Clausifier: motor Claude Haiku (motor_haiku.py) con un `query` falso ($0, sin red).

Portado de API_Defensoria_del_Pueblo y adaptado a Grill: aquí los PKL de
tono/tema SÍ se cargan en modo Haiku y mandan sobre el agente.

El `query` falso hace lo que haría el agente: lee las páginas con la tool
real, guarda con la tool real y pide los candidatos de consistencia.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import joblib  # noqa: E402
from claude_agent_sdk import ResultMessage  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402

import analyzer_tono_tema as az  # noqa: E402
import motor_haiku as mh  # noqa: E402
import pipeline  # noqa: E402
from perfil_cliente import cargar_perfil, listar_perfiles  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISTO: dict = {}


def _resultado():
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=5, session_id='fake', total_cost_usd=0.0123)


async def fake_query(prompt, options):
    """Agente falso: clasifica cada página con reglas fijas usando las tools reales."""
    if 'auditor-tono' in (options.system_prompt or ''):
        # Auditor de tono (llamada aparte, sin herramientas): no cambia nada.
        VISTO['auditor'] = prompt
        yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                            num_turns=1, session_id='fake', total_cost_usd=0.0,
                            result='{"resultados":[]}')
        return
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
            f = {'id': n['id'], 'subtema': 'Hecho haiku %s' % n['id'][:20],
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
        self.assertNotIn('4', VISTO['enviados'])
        self.assertIn('Faltan por clasificar: 0', VISTO['ultimo_guardado'])
        self.assertIn('<criterio_tono>', VISTO['prompt'])
        self.assertIn('SIN TEMA', VISTO['prompt'])
        self.assertNotIn('TONO POR MODELO DEL CLIENTE', VISTO['prompt'])

    def test_tono_pkl_no_pide_tono(self):
        mh.clasificar_con_haiku(_items(), 'Alcaldía', criterio_texto='Regla X',
                                query_fn=fake_query, tono_pkl=True)
        self.assertIn('TONO POR MODELO DEL CLIENTE', VISTO['prompt'])
        self.assertNotIn('<criterio_tono>', VISTO['prompt'])

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
        self.assertEqual(mh._ESTADO['res']['2']['tema'], 'Educación')

    def test_sin_clave_falla_claro(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ANTHROPIC_API_KEY', None)
            with self.assertRaises(mh.ErrorMotorHaiku):
                mh.clasificar_con_haiku(_items(), 'Alcaldía')


# ─── PKL reales (sklearn + joblib) ─────────────────────────────────────────

def _pkl(textos, etiquetas) -> bytes:
    pipe = Pipeline([('tfidf', TfidfVectorizer()), ('clf', LogisticRegression(max_iter=200))])
    pipe.fit(textos, etiquetas)
    buf = io.BytesIO()
    joblib.dump(pipe, buf)
    return buf.getvalue()


def pkl_tono() -> bytes:
    """PKL de tono que siempre tiende a Negativo (distinto del agente falso)."""
    return _pkl(['colegio entrega marca', 'concejo debate presupuesto marca',
                 'crisis denuncia', 'sanción'], ['Negativo', 'Negativo', 'Negativo', 'Positivo'])


def pkl_tema() -> bytes:
    return _pkl(['colegio entrega educación', 'concejo presupuesto hacienda',
                 'colegio aula estudiantes', 'presupuesto impuestos'],
                ['Educación PKL', 'Hacienda PKL', 'Educación PKL', 'Hacienda PKL'])


def _dossier_bytes(n=2):
    wb = Workbook()
    ws = wb.active
    ws.append(['ID Noticia', 'Título', 'Resumen - Aclaracion', 'Medio', 'Fecha'])
    base = [('La marca entrega un colegio', 'La marca entrega un colegio nuevo a la comunidad.'),
            ('Concejo debate presupuesto', 'El Concejo y la marca debatieron el presupuesto.')]
    for i in range(n):
        t, c = base[i % 2]
        ws.append([str(100 + i), '%s %d' % (t, i) if n > 2 else t, c, 'Prensa', '2026-09-0%d' % (1 + i % 9)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sin_openai():
    """Parches que fallan si el camino Haiku toca OpenAI/Jev."""
    real = mh.clasificar_con_haiku
    return [
        patch.object(mh, 'clasificar_con_haiku',
                     side_effect=lambda *a, **k: real(*a, query_fn=fake_query, **k)),
        patch.object(az, 'etiquetar_grupos', side_effect=AssertionError('OpenAI no')),
        patch.object(az, 'consolidar_hechos_llm', side_effect=AssertionError('OpenAI no')),
        patch.object(az, 'consolidar_por_evidencia', side_effect=AssertionError('OpenAI no')),
        patch.object(az, 'asignar_temas', side_effect=AssertionError('OpenAI no')),
        patch.object(az, 'llamar_llm', side_effect=AssertionError('OpenAI no')),
    ]


def _correr(ai_config, n=2):
    VISTO.clear()
    parches = _sin_openai()
    for p in parches:
        p.start()
    try:
        res = pipeline.process_dossier(io.BytesIO(_dossier_bytes(n)), {}, {}, ai_config=ai_config)
    finally:
        for p in reversed(parches):
            p.stop()
    wb = load_workbook(io.BytesIO(res['output_data']), read_only=True)
    ws = wb.active
    filas = list(ws.iter_rows(values_only=True))
    cab = list(filas[0])
    datos = [dict(zip(cab, f)) for f in filas[1:]]
    return res, cab, datos


def _cfg(motor='haiku', tono=None, tema=None, incluir_tema=False, **extra):
    d = {'enabled': True, 'brand': 'Marca', 'aliases': [], 'voceros': [],
         'criterio': None, 'incluir_tema': incluir_tema, 'motor': motor,
         'api_key': None if motor == 'haiku' else 'k',
         'tone_pkl_bytes': tono, 'theme_pkl_bytes': tema}
    d.update(extra)
    return d


class TestHaikuConPkl(unittest.TestCase):
    """Matriz PKL × Haiku a través de process_dossier ($0)."""

    def test_sin_pkl_haiku_tono_y_subtema(self):
        res, cab, datos = _correr(_cfg())
        self.assertNotIn('Tema_IA', cab)
        for r in datos:
            self.assertIn(r['Tono_IA'], ('Positivo', 'Neutro', 'Negativo'))
            self.assertTrue(r['Subtema_IA'])
        a = res['analisis']
        self.assertEqual(a['motor'], 'haiku')
        self.assertEqual(a['origen_columnas'], {'tono': 'haiku', 'tema': 'omitido', 'subtema': 'haiku'})
        self.assertAlmostEqual(a['costo_aprox_usd'], 0.0123)
        self.assertEqual(a['haiku']['turnos'], 5)

    def test_sin_pkl_agregar_tema_ia_con_haiku(self):
        res, cab, datos = _correr(_cfg(incluir_tema=True))
        self.assertIn('Tema_IA', cab)
        self.assertTrue(all(r['Tema_IA'] == 'Gestión institucional' for r in datos))
        self.assertEqual(res['analisis']['origen_columnas']['tema'], 'haiku')

    def test_pkl_tono_gana_y_haiku_subtema(self):
        res, cab, datos = _correr(_cfg(tono=pkl_tono()))
        self.assertIn('TONO POR MODELO DEL CLIENTE', VISTO['prompt'])
        self.assertTrue(all(r['Tono_IA'] == 'Negativo' for r in datos))   # el agente falso diría Positivo
        self.assertTrue(all(r['Subtema_IA'] for r in datos))
        self.assertEqual(res['analisis']['origen_columnas']['tono'], 'pkl')
        self.assertEqual(res['analisis']['origen_columnas']['subtema'], 'haiku')

    def test_pkl_tema_gana(self):
        res, cab, datos = _correr(_cfg(tema=pkl_tema()))
        self.assertIn('SIN TEMA', VISTO['prompt'])                     # Haiku no pide tema
        self.assertIn('Tema_IA', cab)
        self.assertTrue(all(r['Tema_IA'] in ('Educación PKL', 'Hacienda PKL') for r in datos))
        self.assertTrue(all(r['Subtema_IA'] for r in datos))
        self.assertEqual(res['analisis']['origen_columnas']['tema'], 'pkl')

    def test_pkl_tema_gana_aunque_pidan_tema(self):
        res, cab, datos = _correr(_cfg(tema=pkl_tema(), incluir_tema=True))
        self.assertIn('SIN TEMA', VISTO['prompt'])
        self.assertTrue(all(r['Tema_IA'] in ('Educación PKL', 'Hacienda PKL') for r in datos))

    def test_ambos_pkl(self):
        res, cab, datos = _correr(_cfg(tono=pkl_tono(), tema=pkl_tema()))
        self.assertTrue(all(r['Tono_IA'] == 'Negativo' for r in datos))
        self.assertTrue(all(r['Tema_IA'] in ('Educación PKL', 'Hacienda PKL') for r in datos))
        self.assertTrue(all(r['Subtema_IA'] for r in datos))
        self.assertEqual(res['analisis']['origen_columnas'],
                         {'tono': 'pkl', 'tema': 'pkl', 'subtema': 'haiku'})

    def test_resultado_trae_tabla_duplicadas(self):
        res, _, _ = _correr(_cfg())
        self.assertIsInstance(res['duplicadas'], list)


class TestLunaSigueIgual(unittest.TestCase):
    def _openai(self, **kw):
        with patch.object(mh, 'clasificar_con_haiku', side_effect=AssertionError('Haiku no')), \
             patch.object(az, 'etiquetar_grupos',
                          side_effect=lambda cfg, grupos, *a, **k: {
                              g['grupo']: {'tono': 'Neutro', 'sub_tema': 'Hecho luna %d' % g['grupo']}
                              for g in grupos}) as eg, \
             patch.object(az, 'consolidar_por_evidencia', return_value=0), \
             patch.object(az, 'consolidar_hechos_llm', return_value=0), \
             patch.object(az, 'llamar_llm', side_effect=AssertionError('sin red')):
            cfg = _cfg(motor='openai', **kw)
            cfg['api_key'] = ''          # los pases LLM restantes caen a su camino determinista
            res = pipeline.process_dossier(io.BytesIO(_dossier_bytes()), {}, {}, ai_config=cfg)
        self.assertTrue(eg.called)
        ws = load_workbook(io.BytesIO(res['output_data']), read_only=True).active
        return res, [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]

    def test_luna_opcional(self):
        res, _ = self._openai()
        self.assertEqual(res['analisis']['motor'], 'openai')
        self.assertNotIn('haiku', res['analisis'])

    def test_motor_por_defecto_del_analizador_es_openai(self):
        # Sin la clave 'motor' (llamadas viejas) el analizador sigue en Luna;
        # la app es la que elige Haiku por defecto.
        with patch.object(mh, 'clasificar_con_haiku', side_effect=AssertionError('Haiku no')):
            cfg = pipeline.construir_ai_config_custom(
                'Marca', '', '', None, False, False, True, 'k', None, None, None, None)
        self.assertEqual(cfg['motor'], 'openai')

    def test_cabecera_igual_haiku_vs_luna(self):
        for kw in ({}, {'incluir_tema': True}, {'tono': pkl_tono(), 'tema': pkl_tema()}):
            _, cab_luna = self._openai(**kw)
            _, cab_haiku, _ = _correr(_cfg(**kw))
            self.assertEqual(cab_haiku, cab_luna, kw)


class TestPerfilesClausifier(unittest.TestCase):
    def test_perfiles_listados(self):
        ids = {p['id'] for p in listar_perfiles()}
        for pid in ('secretaria_general_de_la_alcaldia_de_bogota', 'defensoria_del_pueblo',
                    'fenavi', 'fundacion_santa_fe', 'universidad_simon_bolivar',
                    'universidad_tecnologica_de_bolivar'):
            self.assertIn(pid, ids)

    def test_flags_portados_y_sin_tipo_genero(self):
        sec = cargar_perfil('secretaria_general_de_la_alcaldia_de_bogota')
        de = cargar_perfil('defensoria_del_pueblo')
        self.assertTrue(sec['tono_distrito_positivo'])
        self.assertTrue(sec['tono_solo_gestion'])
        self.assertTrue(sec['agrupacion_estricta'])
        self.assertTrue(de['tono_alertas_tempranas'])
        self.assertTrue(de['tono_labor_positiva'])
        for p in (sec, de):
            self.assertNotIn('clasificar_tipo_genero', p)
            self.assertNotIn('tipo_por_actor', p)
            self.assertNotIn('formato_boletin', p)
        self.assertIsNone(cargar_perfil('fenavi')['agrupacion_estricta'])   # default del analizador

    def test_perfiles_corren_sin_columnas_tipo_genero(self):
        import app
        for pid in ('secretaria_general_de_la_alcaldia_de_bogota', 'defensoria_del_pueblo'):
            p = cargar_perfil(pid)
            cfg = _cfg(brand=p['brand'], aliases=p['aliases'], voceros=p['voceros'],
                       **app._flags_de_perfil(p), clasificar_tipo_genero=False)
            res, cab, datos = _correr(cfg)
            nombres = {str(c).strip().upper() for c in cab}
            for prohibida in ('TIPO', 'GÉNERO', 'GENERO', 'TIPO_IA', 'GÉNERO_IA', 'GENERO_IA'):
                self.assertNotIn(prohibida, nombres)
            self.assertTrue(all(r['Subtema_IA'] for r in datos))
            if p.get('guia_subtema'):
                self.assertIn('<guia_subtema>', VISTO['prompt'])

    def test_regla_alerta_temprana_sube_a_positivo(self):
        grupos = [{'grupo': 1, 'titulo': 'Defensoría emite alerta temprana por riesgo en Cauca',
                   'contexto': 'La Defensoría del Pueblo emitió una alerta temprana por riesgo.'}]
        et = {1: {'tono': 'Neutro', 'sub_tema': 'x'}}
        out = az.aplicar_regla_alerta_temprana(grupos, et, 'Defensoría del Pueblo', ['Defensoría'])
        self.assertEqual(out, [1])
        self.assertEqual(et[1]['tono'], 'Positivo')


class TestLimiteFilas(unittest.TestCase):
    def test_1000_filas_se_respeta_en_haiku(self):
        with patch.object(mh, 'clasificar_con_haiku', side_effect=AssertionError('no debe llegar')):
            with self.assertRaises(pipeline.LimiteFilasError):
                pipeline.process_dossier(io.BytesIO(_dossier_bytes(pipeline.MAX_FILAS_POR_DOSSIER + 1)),
                                         {}, {}, ai_config=_cfg())
        self.assertEqual(pipeline.MAX_FILAS_POR_DOSSIER, 1000)


if __name__ == '__main__':
    unittest.main()
