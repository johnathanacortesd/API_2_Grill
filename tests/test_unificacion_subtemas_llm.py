"""Pruebas v4.14: pase final de unificación de subtemas entre lotes.

- `_sanitizar_fusiones`: solo grupos válidos (>=2 índices distintos, en rango,
  sin repetir un índice en dos grupos).
- `_canonico_de_fusion`: gana el más frecuente; en empate, el más corto
  (mismo criterio que `canonizar_subtemas`).
- `unificar_subtemas_llm`: aplica la fusión del modelo a `etiquetas`,
  conserva el texto original del canónico (verbatim) y no rompe si la
  llamada LLM falla (devuelve 0).
- No llama al modelo si hay 0-1 subtemas únicos.
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az


def _etiquetas(subs):
    return {i + 1: {'sub_tema': s, 'tono': 'Neutro'} for i, s in enumerate(subs)}


def _grupos(subs):
    return [{'grupo': i + 1, 'titulo': 'Titular %d' % (i + 1)} for i in range(len(subs))]


class TestSanitizarFusiones(unittest.TestCase):
    def test_grupos_validos(self):
        self.assertEqual(az._sanitizar_fusiones([[1, 2], [3, 4, 5]], 5),
                         [[0, 1], [2, 3, 4]])

    def test_fuera_de_rango_se_descarta(self):
        self.assertEqual(az._sanitizar_fusiones([[1, 9], [2, 3]], 5), [[1, 2]])

    def test_singleton_se_descarta(self):
        self.assertEqual(az._sanitizar_fusiones([[1], [2, 3]], 5), [[1, 2]])

    def test_indice_repetido_en_dos_grupos(self):
        # El índice 2 ya se usó: el segundo grupo queda en singleton y se cae.
        self.assertEqual(az._sanitizar_fusiones([[1, 2], [2, 3]], 5), [[0, 1]])

    def test_valores_no_enteros(self):
        self.assertEqual(az._sanitizar_fusiones([['a', 1, 2], None, 'x'], 5),
                         [[0, 1]])

    def test_vacia(self):
        self.assertEqual(az._sanitizar_fusiones([], 5), [])
        self.assertEqual(az._sanitizar_fusiones(None, 5), [])


class TestCanonicoDeFusion(unittest.TestCase):
    def test_gana_el_mas_frecuente(self):
        textos = ['Apertura del laboratorio', 'Inauguración del laboratorio']
        conteo = {az.nz('Apertura del laboratorio'): 1,
                  az.nz('Inauguración del laboratorio'): 4}
        self.assertEqual(az._canonico_de_fusion([0, 1], textos, conteo),
                         'Inauguración del laboratorio')

    def test_empate_gana_el_mas_corto(self):
        textos = ['Apertura del laboratorio central', 'Apertura del laboratorio']
        conteo = {az.nz('Apertura del laboratorio central'): 2,
                  az.nz('Apertura del laboratorio'): 2}
        self.assertEqual(az._canonico_de_fusion([0, 1], textos, conteo),
                         'Apertura del laboratorio')

    def test_conserva_verbatim(self):
        textos = ['Foro de Periodismo Científico', 'foro periodismo cientifico']
        conteo = {az.nz('Foro de Periodismo Científico'): 3,
                  az.nz('foro periodismo cientifico'): 1}
        self.assertEqual(az._canonico_de_fusion([0, 1], textos, conteo),
                         'Foro de Periodismo Científico')


class TestUnificarSubtemasLlm(unittest.TestCase):
    def test_fusiona_parafrasis(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio',
                'Obras en Sincelejo']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        payload = '{"fusiones": [[1, 2]]}'
        with patch.object(az, 'llamar_llm', return_value=payload) as m:
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertTrue(m.called)
        self.assertEqual(cambios, 1)
        # Empate de frecuencia (1-1): gana el más corto.
        self.assertEqual(et[1]['sub_tema'], 'Apertura del laboratorio')
        self.assertEqual(et[2]['sub_tema'], 'Apertura del laboratorio')
        self.assertEqual(et[3]['sub_tema'], 'Obras en Sincelejo')

    def test_respeta_frecuencia_del_lote(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio',
                'Inauguración del laboratorio']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        with patch.object(az, 'llamar_llm', return_value='{"fusiones": [[1, 2]]}'):
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 1)
        for gid in (1, 2, 3):
            self.assertEqual(et[gid]['sub_tema'], 'Inauguración del laboratorio')

    def test_sin_fusiones_no_cambia(self):
        subs = ['Apertura del laboratorio', 'Obras en Sincelejo']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        with patch.object(az, 'llamar_llm', return_value='{"fusiones": []}'):
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 0)
        self.assertEqual(et[1]['sub_tema'], 'Apertura del laboratorio')

    def test_fallo_llm_no_rompe(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        with patch.object(az, 'llamar_llm', side_effect=RuntimeError('HTTP 500')):
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 0)
        self.assertEqual(et[1]['sub_tema'], 'Apertura del laboratorio')
        self.assertEqual(et[2]['sub_tema'], 'Inauguración del laboratorio')

    def test_un_subtema_no_llama(self):
        et = _etiquetas(['Solo uno'])
        gr = _grupos(['Solo uno'])
        with patch.object(az, 'llamar_llm') as m:
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 0)
        m.assert_not_called()

    def test_suma_uso_cuando_hay_llamada(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        uso = {'input': 0, 'output': 0, 'llamadas': 0}

        def fake(cfg, mensajes, json_mode=True, max_tokens=4000, uso=None, **kw):
            if uso is not None:
                uso['input'] += 10
                uso['llamadas'] += 1
            return '{"fusiones": []}'

        with patch.object(az, 'llamar_llm', side_effect=fake):
            az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso=uso)
        self.assertEqual(uso['llamadas'], 1)
        self.assertEqual(uso['input'], 10)


if __name__ == '__main__':
    unittest.main()
