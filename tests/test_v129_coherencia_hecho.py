# -*- coding: utf-8 -*-
"""v1.29: coherencia por hecho (misma noticia => mismo subtema y mismo tono).

- `consolidar_hechos_llm`: agrupa grupos del mismo hecho con un subtema fiel
  a TODAS las noticias; no agrupa si el rótulo no corresponde a alguna
  (no agrupar por agrupar); corrige un subtema ajeno solo si el actual no
  es fiel a su noticia; devuelve None si el modelo no responde.
- `garantizar_coherencia_hecho`: mismo titular / mismo grupo => mismo
  subtema; mismo subtema final => mismo tono (mayoría por notas, empate =>
  Neutro).
- `volcar_analisis_en_filas` ya no falla con NameError por `km`.
- Los ejemplos del prompt no enseñan dos subtemas para la misma noticia.
"""
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

import analyzer_tono_tema as az
import catalogo_tono_tema as cat

KM = {'titulo': 'Título'}
CFG = {'brand': 'Secretaría General de la Alcaldía de Bogotá',
       'aliases': ['Alcaldía de Bogotá', 'Secretaría General'],
       'voceros': ['Carlos Fernando Galán', 'Galán'], 'model': 'x'}

METRO_A = ('Galán anunció que abre el proceso para contratar la segunda línea del metro',
           'El alcalde Carlos Fernando Galán anunció la apertura de la licitación de la '
           'segunda línea del metro de Bogotá, que irá hacia Suba y Engativá.')
METRO_B = ('Arranca el proceso para contratar la línea 2 del metro',
           'La Alcaldía de Bogotá abrió la licitación de la segunda línea del metro, '
           'que conectará Suba y Engativá con el centro.')
HURTOS = ('Concejales cuestionan a Galán por el aumento de hurtos',
          'Varios concejales cuestionaron al alcalde Galán por el aumento de hurtos '
          'en Kennedy y Bosa durante el último trimestre.')


def _grupo(gid, tit, txt, n=1):
    return {'grupo': gid, 'n': n, 'titulo': tit, 'titulos_alt': [], 'texto': txt,
            'contexto': txt}


def _resp(hechos):
    return json.dumps({'hechos': hechos})


class TestConsolidarHechosLLM(unittest.TestCase):
    def test_fusiona_mismo_hecho_con_rotulo_fiel(self):
        grupos = [_grupo(1, *METRO_A, n=3), _grupo(2, *METRO_B), _grupo(3, *HURTOS)]
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              2: {'sub_tema': 'Contratación de la línea 2', 'tono': 'Neutro'},
              3: {'sub_tema': 'Críticas por aumento de hurtos', 'tono': 'Negativo'}}
        r = _resp([{'ids': [1, 2], 'sub_tema': 'Licitación de la segunda línea del metro'},
                   {'ids': [3], 'sub_tema': 'Críticas por aumento de hurtos'}])
        with patch.object(az, 'llamar_llm', return_value=r):
            cambios = az.consolidar_hechos_llm(CFG, grupos, et, uso={})
        self.assertEqual(cambios, 1)
        self.assertEqual(et[2]['sub_tema'], 'Licitación de la segunda línea del metro')
        self.assertEqual(et[1].get('hecho'), et[2].get('hecho'))
        self.assertEqual(et[3]['sub_tema'], 'Críticas por aumento de hurtos')

    def test_no_agrupa_hechos_distintos_que_solo_comparten_al_alcalde(self):
        grupos = [_grupo(1, *METRO_A), _grupo(2, *HURTOS)]
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              2: {'sub_tema': 'Críticas por aumento de hurtos', 'tono': 'Negativo'}}
        r = _resp([{'ids': [1, 2], 'sub_tema': 'Licitación de la segunda línea del metro'}])
        with patch.object(az, 'llamar_llm', return_value=r):
            az.consolidar_hechos_llm(CFG, grupos, et, uso={})
        self.assertEqual(et[2]['sub_tema'], 'Críticas por aumento de hurtos')
        # v1.30: no agrupados => cada uno es su propio hecho.
        self.assertNotEqual(et[1].get('hecho'), et[2].get('hecho'))

    def test_corrige_subtema_ajeno_de_un_grupo(self):
        grupos = [_grupo(1, *HURTOS)]
        grupos.append(_grupo(2, *METRO_A))
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Negativo'},
              2: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'}}
        r = _resp([{'ids': [1], 'sub_tema': 'Críticas por aumento de hurtos'},
                   {'ids': [2], 'sub_tema': 'Licitación de la segunda línea del metro'}])
        with patch.object(az, 'llamar_llm', return_value=r):
            az.consolidar_hechos_llm(CFG, grupos, et, uso={})
        self.assertEqual(et[1]['sub_tema'], 'Críticas por aumento de hurtos')

    def test_no_reescribe_subtema_fiel_de_un_grupo(self):
        grupos = [_grupo(1, *HURTOS), _grupo(2, *METRO_A)]
        et = {1: {'sub_tema': 'Críticas por aumento de hurtos', 'tono': 'Negativo'},
              2: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'}}
        r = _resp([{'ids': [1], 'sub_tema': 'Hurtos en Kennedy y Bosa'},
                   {'ids': [2], 'sub_tema': 'Licitación de la segunda línea del metro'}])
        with patch.object(az, 'llamar_llm', return_value=r):
            az.consolidar_hechos_llm(CFG, grupos, et, uso={})
        self.assertEqual(et[1]['sub_tema'], 'Críticas por aumento de hurtos')

    def test_fallo_del_modelo_devuelve_none(self):
        grupos = [_grupo(1, *METRO_A), _grupo(2, *HURTOS)]
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Neutro'},
              2: {'sub_tema': 'Críticas por aumento de hurtos', 'tono': 'Neutro'}}
        with patch.object(az, 'llamar_llm', side_effect=RuntimeError('caido')):
            self.assertIsNone(az.consolidar_hechos_llm(CFG, grupos, et, uso={}))

    def test_ids_desconocidos_o_repetidos_se_ignoran(self):
        grupos = [_grupo(1, *METRO_A), _grupo(2, *METRO_B)]
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Neutro'},
              2: {'sub_tema': 'Contratación de la línea 2', 'tono': 'Neutro'}}
        r = _resp([{'ids': [1, 99, 'x'], 'sub_tema': 'Licitación de la segunda línea del metro'},
                   {'ids': [1, 2], 'sub_tema': 'Licitación de la segunda línea del metro'}])
        with patch.object(az, 'llamar_llm', return_value=r):
            az.consolidar_hechos_llm(CFG, grupos, et, uso={})
        # El 1 ya se usó en el primer hecho: el segundo queda solo con el 2,
        # que no se reescribe porque su subtema actual sí le es fiel.
        self.assertEqual(et[2]['sub_tema'], 'Contratación de la línea 2')


def _fila(tit, txt):
    return {'Título': tit, 'CuerpoEs': txt}


class TestGarantizarCoherencia(unittest.TestCase):
    def test_mismo_titular_mismo_subtema(self):
        rows = [_fila(*METRO_A), _fila(*METRO_A), _fila(*METRO_A)]
        mapa = {0: 1, 1: 2, 2: 3}
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              2: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              3: {'sub_tema': 'Apertura de licitación del metro', 'tono': 'Neutro'}}
        aj, n_sub, n_tono = az.garantizar_coherencia_hecho(rows, mapa, et, {}, KM)
        self.assertEqual(aj.get(2), 'Licitación de la segunda línea del metro')
        self.assertEqual(n_sub, 1)
        self.assertEqual({et[g]['tono'] for g in et}, {'Positivo'})
        self.assertEqual(n_tono, 1)

    def test_mismo_subtema_mismo_tono_mayoria_por_notas(self):
        rows = [_fila(*METRO_A), _fila(*METRO_A), _fila(*METRO_B)]
        mapa = {0: 1, 1: 1, 2: 2}
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              2: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Negativo'}}
        az.garantizar_coherencia_hecho(rows, mapa, et, {}, KM)
        self.assertEqual(et[2]['tono'], 'Positivo')

    def test_empate_de_tono_cae_a_neutro(self):
        rows = [_fila(*METRO_A), _fila(*METRO_B)]
        mapa = {0: 1, 1: 2}
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              2: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Negativo'}}
        az.garantizar_coherencia_hecho(rows, mapa, et, {}, KM)
        self.assertEqual(et[1]['tono'], 'Neutro')
        self.assertEqual(et[2]['tono'], 'Neutro')

    def test_hechos_distintos_no_se_tocan(self):
        rows = [_fila(*METRO_A), _fila(*HURTOS)]
        mapa = {0: 1, 1: 2}
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              2: {'sub_tema': 'Críticas por aumento de hurtos', 'tono': 'Negativo'}}
        aj, n_sub, n_tono = az.garantizar_coherencia_hecho(rows, mapa, et, {}, KM)
        self.assertEqual((aj, n_sub, n_tono), ({}, 0, 0))
        self.assertEqual(et[2]['tono'], 'Negativo')

    def test_grupo_mezclado_no_impone_rotulo_ajeno(self):
        # Mismo grupo, pero una fila habla de otro hecho: ningún rótulo es
        # fiel a ambas, así que cada una conserva el que sí le corresponde.
        rows = [_fila(*METRO_A), _fila(*HURTOS)]
        mapa = {0: 1, 1: 1}
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Neutro'}}
        aj = {1: 'Críticas por aumento de hurtos'}
        aj2, _n, _t = az.garantizar_coherencia_hecho(rows, mapa, et, aj, KM)
        self.assertEqual(aj2.get(1), 'Críticas por aumento de hurtos')
        self.assertNotIn(0, aj2)

    def test_duplicadas_se_ignoran(self):
        rows = [_fila(*METRO_A), dict(_fila(*METRO_A), is_duplicate=True)]
        mapa = {0: 1}
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Neutro'}}
        self.assertEqual(az.garantizar_coherencia_hecho(rows, mapa, et, {}, KM), ({}, 0, 0))


class TestVolcarKm(unittest.TestCase):
    def test_subtema_vacio_no_falla(self):
        rows = [_fila(*METRO_A)]
        et = {1: {'sub_tema': '', 'tono': 'Neutro'}}
        az.volcar_analisis_en_filas(rows, {0: 1}, et, {}, incluir_tema=False, km=KM)
        self.assertTrue(rows[0]['Subtema_IA'])


class TestEjemplosCoherentes(unittest.TestCase):
    def test_mismo_hecho_en_ejemplos_lleva_mismo_subtema(self):
        por_sub = {e['titulo']: e['sub_tema'] for e in cat.EJEMPLOS}
        self.assertEqual(
            por_sub['Por demoras en el PAE, Procuraduría suspende a exsecretario de Educación de Sucre'],
            por_sub['Sancionan a exsecretario de Educación de Sucre, por demora en el PAE'])

    def test_regla_de_coherencia_en_prompt(self):
        self.assertIn('COHERENCIA', cat.REGLAS_SUBTEMA)


if __name__ == '__main__':
    unittest.main()
