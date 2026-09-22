"""Pruebas v4.13: costo aproximado de IA y respeto del PKL de tema.

A. Costo:
  - `_precios_modelo` devuelve las tarifas (USD/millón) por modelo.
  - `_costo_aprox_usd` calcula entrada + salida.
  - `llamar_llm(..., uso=...)` acumula prompt/completion tokens de la
    respuesta real (`usage`) en el dict, sin romper cuando falta.

B. PKL de tema (verificación pedida por el usuario, 2026-09-22):
  - `aplicar_pkl_del_cliente` conserva las clases del modelo verbatim
    (solo strip), marca origen 'pkl' y nunca toca el subtema.
  - `enrich_rows_with_ai` con `theme_model` y `incluir_tema=False` igual
    genera Tema_IA desde el PKL (el modelo del cliente manda) y nunca
    llama a `asignar_temas`/`corregir_temas_con_jev`.
  - `pipeline._ai_extra_con_pkl` fuerza incluir_tema=True solo con PKL.
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub del paquete `openai`: no instalable en este entorno (conflicto debian,
# mismo motivo del error preexistente en test_pkl_tono_tema).
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az
import pipeline


class FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class TestPrecios(unittest.TestCase):
    def test_nano(self):
        self.assertEqual(az._precios_modelo('gpt-4.1-nano-2025-04-14'), (0.10, 0.40))

    def test_luna(self):
        self.assertEqual(az._precios_modelo('gpt-6-luna'), (0.10, 0.50))

    def test_sol(self):
        self.assertEqual(az._precios_modelo('gpt-6-sol'), (2.00, 10.00))

    def test_desconocido_cae_a_nano(self):
        self.assertEqual(az._precios_modelo('gpt-x'), (0.10, 0.40))

    def test_costo(self):
        costo, pin, pout = az._costo_aprox_usd(
            'gpt-4.1-nano-2025-04-14', {'input': 1_000_000, 'output': 500_000})
        self.assertAlmostEqual(costo, 0.30)
        self.assertEqual((pin, pout), (0.10, 0.40))

    def test_sumar_uso_none_no_falla(self):
        az._sumar_uso(None, 10, 20)


class TestLlamarLlmUso(unittest.TestCase):
    def _cfg(self):
        return {'api_key': 'k', 'model': 'gpt-4.1-nano-2025-04-14', 'timeout': 5}

    def test_acumula_tokens(self):
        payload = {'choices': [{'message': {'content': '{"resultados": []}'}}],
                   'usage': {'prompt_tokens': 1234, 'completion_tokens': 56}}
        uso = {}
        with patch.object(az, '_http_post', return_value=FakeResp(payload)):
            txt = az.llamar_llm(self._cfg(), [{'role': 'user', 'content': 'hola'}], uso=uso)
        self.assertEqual(txt, '{"resultados": []}')
        self.assertEqual(uso, {'input': 1234, 'output': 56, 'llamadas': 1})

    def test_sin_usage_en_respuesta(self):
        payload = {'choices': [{'message': {'content': 'ok'}}]}
        uso = {}
        with patch.object(az, '_http_post', return_value=FakeResp(payload)):
            az.llamar_llm(self._cfg(), [{'role': 'user', 'content': 'hola'}], uso=uso)
        self.assertEqual(uso, {'input': 0, 'output': 0, 'llamadas': 1})

    def test_sin_uso_no_rastrea(self):
        payload = {'choices': [{'message': {'content': 'ok'}}],
                   'usage': {'prompt_tokens': 5, 'completion_tokens': 5}}
        with patch.object(az, '_http_post', return_value=FakeResp(payload)):
            self.assertEqual(az.llamar_llm(self._cfg(), [{'role': 'user', 'content': 'h'}]), 'ok')


class StubTema:
    """PKL de mentira: clases fijas en orden de llamada, con espacios."""

    def __init__(self, clases):
        self._clases = list(clases)
        self.n = 0

    def predict(self, texts):
        out = [self._clases[(self.n + i) % len(self._clases)] for i in range(len(texts))]
        self.n += len(texts)
        return out


class TestPklTemaRespetado(unittest.TestCase):
    def test_clases_verbatim_y_origen_pkl(self):
        grupos = [
            {'grupo': 1, 'idxs': [0], 'titulo': 'La U inaugura laboratorio'},
            {'grupo': 2, 'idxs': [1], 'titulo': 'Rector habla del PISA'},
        ]
        rows = [{'Contexto analizado': 'La Universidad inaugura laboratorio de IA'},
                {'Contexto analizado': 'El rector comentó los resultados PISA'}]
        etiquetas = {1: {'sub_tema': 'Inauguración laboratorio', 'tono': 'Positivo'},
                     2: {'sub_tema': 'Resultados PISA', 'tono': 'Positivo'}}
        temas = {1: 'Tema viejo del lote', 2: 'Otro tema viejo'}
        origen = {1: 'llm', 2: 'llm'}
        modelo = StubTema(['  Educación Superior  ', 'Evaluación PISA'])
        applied = az.aplicar_pkl_del_cliente(grupos, rows, etiquetas, temas, origen,
                                             theme_model=modelo)
        self.assertEqual(applied, {'tono': 0, 'tema': 2})
        # Verbatim: solo strip, sin reescritura del quality-gate del lote.
        self.assertEqual(temas[1], 'Educación Superior')
        self.assertEqual(temas[2], 'Evaluación PISA')
        self.assertEqual(origen[1], 'pkl')
        self.assertEqual(origen[2], 'pkl')
        # El subtema jamás se toca.
        self.assertEqual(etiquetas[1]['sub_tema'], 'Inauguración laboratorio')
        self.assertEqual(etiquetas[2]['sub_tema'], 'Resultados PISA')

    def test_enrich_con_pkl_ignora_checkbox_apagado(self):
        rows = [
            {'Título': 'La marca lanza un programa social en barrios vulnerables',
             'CuerpoEs': 'La marca anunció un programa social que beneficiará a miles de familias.'},
            {'Título': 'Gremio hotelero reporta ocupación récord en temporada',
             'CuerpoEs': 'El gremio reportó cifras históricas de ocupación hotelera este mes.'},
        ]
        modelo = StubTema(['  Responsabilidad Social  ', 'Turismo'])
        with patch.object(az, 'etiquetar_grupos',
                          return_value={1: {'tono': 'Positivo', 'sub_tema': 'Programa social'},
                                        2: {'tono': 'Neutro', 'sub_tema': 'Ocupación hotelera'}}), \
             patch.object(az, 'canonizar_subtemas', return_value=0), \
             patch.object(az, 'unificar_subtemas_noticias_similares', return_value=0), \
             patch.object(az, 'asignar_temas',
                          side_effect=AssertionError('asignar_temas no debe llamarse con PKL')), \
             patch.object(az, 'corregir_temas_con_jev',
                          side_effect=AssertionError('jev no debe llamarse con PKL')):
            out = az.enrich_rows_with_ai(
                rows, {'titulo': 'Título'}, brand='Marca', aliases=[], api_key='k',
                theme_model=modelo,
                extra={'incluir_tema': False, 'voceros': []},
                progress_callback=lambda p, m: None)
        for r in out:
            self.assertTrue(r['Tema_IA'], 'el PKL de tema debe generar Tema_IA')
        self.assertEqual(set(r['Tema_IA'] for r in out),
                         {'Responsabilidad Social', 'Turismo'})
        self.assertEqual(az.ultimo_resumen().get('modo_taxonomia'), 'pkl')
        # El costo se registra aunque el tema venga del PKL (el tono sí usó LLM).
        self.assertIn('costo_aprox_usd', az.ultimo_resumen())

    def test_pipeline_fuerza_tema_solo_con_pkl(self):
        self.assertTrue(
            pipeline._ai_extra_con_pkl({'incluir_tema': False, 'brand': 'X'}, object())
            ['incluir_tema'])
        self.assertFalse(
            pipeline._ai_extra_con_pkl({'incluir_tema': False}, None)['incluir_tema'])
        self.assertTrue(
            pipeline._ai_extra_con_pkl({'incluir_tema': True}, None)['incluir_tema'])


if __name__ == '__main__':
    unittest.main()
