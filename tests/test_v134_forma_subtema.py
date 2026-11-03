# -*- coding: utf-8 -*-
"""v1.34: forma del subtema (regla del usuario 2026-11-02): jamás comas ni
puntos, jamás pronombre inicial, jamás «sujeto + verbo», jamás copia del
titular. Casos reales: «Días dejan cinco muertos en el suroccidente» y
«Ellos, una bebé de dos meses»."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az

KM = {'titulo': 'Título'}


class TestDetector(unittest.TestCase):
    def test_casos_reales_son_invalidos(self):
        self.assertIn('clausula_verbal', az._problemas_forma_subtema('Días dejan cinco muertos en el suroccidente'))
        p = az._problemas_forma_subtema('Ellos, una bebé de dos meses')
        self.assertIn('puntuacion', p)
        self.assertIn('inicio_pronombre', p)
        self.assertIn('puntuacion', az._problemas_forma_subtema('Alcalde Galán.'))

    def test_validar_los_marca(self):
        for s in ('Días dejan cinco muertos en el suroccidente', 'Ellos, una bebé de dos meses'):
            self.assertTrue(set(az.validar(s, 'Neutro', ['x'])) & {'clausula_verbal', 'puntuacion'}, s)

    def test_rotulos_buenos_no_se_marcan(self):
        for s in ('Licitación de la segunda línea del metro', 'Pico y placa festivo en Bogotá',
                  'Línea 137', 'Alertas Defensoría del Pueblo', 'Presupuesto de $42,7 billones',
                  'Bogotá, mi Ciudad, mi Casa', 'Muerte de Antonio Navarro Wolff'):
            self.assertEqual(az._problemas_forma_subtema(s), [], s)


class TestRespaldo(unittest.TestCase):
    def test_reparacion_sin_inventar(self):
        self.assertEqual(az._reparar_forma_subtema('Días dejan cinco muertos en el suroccidente'),
                         'Cinco muertos en el suroccidente')
        self.assertEqual(az._reparar_forma_subtema('Ellos, una bebé de dos meses'), 'Bebé de dos meses')

    def test_respaldo_del_titular_nunca_sale_mal_formado(self):
        for tit, txt in [('Tres días de combates dejan cinco muertos en el suroccidente', ''),
                         ('¿Qué pasó en el Cauca?', 'Entre ellos, una bebé de dos meses. Hubo ataques.')]:
            self.assertEqual(az._problemas_forma_subtema(az._subtema_especifico(tit, [txt])), [], tit)


class TestRevisionFinal(unittest.TestCase):
    def test_usa_el_subtema_del_grupo(self):
        rows = [{'Título': 'Tres días de combates dejan cinco muertos en el suroccidente',
                 'CuerpoEs': 'Los combates dejaron cinco muertos en el Cauca.'}]
        et = {1: {'sub_tema': 'Combates en el Cauca'}}
        aj = az.sanear_subtemas_finales(rows, {0: 1}, et, {0: 'Días dejan cinco muertos en el suroccidente'}, KM)
        self.assertEqual(aj[0], 'Combates en el Cauca')

    def test_sin_alternativa_valida_repara(self):
        rows = [{'Título': 'Ataque en Jamundí', 'CuerpoEs': 'Entre ellos, una bebé de dos meses.'}]
        et = {1: {'sub_tema': 'Ellos, una bebé de dos meses'}}
        aj = az.sanear_subtemas_finales(rows, {0: 1}, et, {}, KM)
        self.assertEqual(az._problemas_forma_subtema(aj.get(0, et[1]['sub_tema'])), [])

    def test_rotulo_bueno_no_se_toca(self):
        rows = [{'Título': 'Galán anuncia licitación', 'CuerpoEs': 'x'}]
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro'}}
        self.assertEqual(az.sanear_subtemas_finales(rows, {0: 1}, et, {}, KM), {})


if __name__ == '__main__':
    unittest.main()
