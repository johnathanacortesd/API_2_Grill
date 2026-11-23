# -*- coding: utf-8 -*-
"""Defensoría v1.27: tono de Alertas Tempranas (depende de la nota),
criterio de tono del perfil y columna texto con más de 15 palabras."""
import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az
import perfil_cliente
from pipeline import texto_con_minimo

P = perfil_cliente.cargar_perfil('defensoria_del_pueblo')


def _correr(titulo, contexto, tono):
    g = {'grupo': 1, 'titulo': titulo, 'contexto': contexto}
    et = {1: {'tono': tono, 'sub_tema': 'Alertas Defensoría del Pueblo'}}
    az.aplicar_regla_alerta_temprana([g], et, P['brand'], P['aliases'], voceros=P['voceros'])
    return et[1]['tono']


class TestAlertaTemprana(unittest.TestCase):
    def test_defensoria_emite_alerta_es_positivo(self):
        self.assertEqual(_correr(
            'Defensoría emite alerta temprana por reclutamiento en el Cauca',
            'La Defensoría del Pueblo emitió la Alerta Temprana 030 por reclutamiento de menores.',
            'Neutro'), 'Positivo')

    def test_vocera_advierte_es_positivo(self):
        self.assertEqual(_correr(
            'Riesgo para líderes en Arauca',
            'Iris Marín advirtió en una alerta temprana el riesgo para líderes sociales en Arauca.',
            'Neutro'), 'Positivo')

    def test_alerta_citada_como_antecedente_no_se_toca(self):
        self.assertEqual(_correr(
            'Enfrentamientos dejan tres muertos en el Catatumbo',
            'Los combates ocurrieron en una zona sobre la que existía una alerta temprana de la Defensoría.',
            'Neutro'), 'Neutro')

    def test_critica_a_la_defensoria_no_se_sube(self):
        self.assertEqual(_correr(
            'Critican a la Defensoría por alerta temprana tardía',
            'Concejales criticaron a la Defensoría del Pueblo porque emitió tarde la alerta temprana.',
            'Negativo'), 'Negativo')

    def test_sin_alerta_no_aplica(self):
        self.assertEqual(_correr('Defensoría presenta informe', 'La Defensoría presentó un informe anual.',
                                 'Neutro'), 'Neutro')


class TestSubtemaFijoEstricto(unittest.TestCase):
    REGLAS = [{'contiene': 'alerta temprana', 'subtema': 'Alertas Defensoría del Pueblo'}]

    def _aplicar(self, titulo, texto, sub, estricta=True):
        g = [{'grupo': 1, 'titulo': titulo, 'titulos_alt': [], 'texto': texto, 'contexto': texto}]
        et = {1: {'sub_tema': sub, 'tono': 'Neutro'}}
        az.aplicar_subtemas_fijos(g, et, self.REGLAS, estricta=estricta)
        return et[1]['sub_tema']

    def test_alerta_en_titular_aplica(self):
        self.assertEqual(self._aplicar('Defensoría emite alerta temprana en Arauca', 'x',
                                       'Riesgo en Arauca'), 'Alertas Defensoría del Pueblo')

    def test_modelo_ya_la_identifico_aplica(self):
        self.assertEqual(self._aplicar('Riesgo de reclutamiento en el Cauca', 'x',
                                       'Alerta temprana en el Cauca'), 'Alertas Defensoría del Pueblo')

    def test_alerta_en_el_texto_aplica(self):
        # v1.28, regla del usuario: si el texto dice «alerta temprana», el
        # subtema es el fijo (el tono sigue dependiendo de la nota).
        texto = 'Los combates ocurrieron en una zona sobre la que existía una alerta temprana.'
        self.assertEqual(self._aplicar('Combates en el Catatumbo dejan tres muertos', texto,
                                       'Combates en el Catatumbo'), 'Alertas Defensoría del Pueblo')

    def test_palabras_sueltas_no_aplican(self):
        texto = 'La alerta de la Policía llegó temprana, antes de los combates.'
        self.assertEqual(self._aplicar('Combates en el Catatumbo', texto, 'Combates en el Catatumbo'),
                         'Combates en el Catatumbo')

    def test_garantia_final_por_fila(self):
        rows = [{'Título': 'Combates en Arauca', 'CuerpoEs': 'x',
                 'Resumen - Aclaracion': 'Hay alertas tempranas vigentes en la zona.'},
                {'Título': 'Informe anual', 'CuerpoEs': 'La Defensoría presentó su informe.'},
                {'Título': 'Alerta temprana en Cauca', 'is_duplicate': True}]
        aj = az.aplicar_subtemas_fijos_final(rows, {0: 'Otro rótulo'}, {'titulo': 'Título'}, self.REGLAS)
        self.assertEqual(aj, {0: 'Alertas Defensoría del Pueblo'})

    def test_modo_no_estricto_conserva_comportamiento_anterior(self):
        texto = 'Los combates ocurrieron en una zona sobre la que existía una alerta temprana.'
        self.assertEqual(self._aplicar('Combates en el Catatumbo', texto, 'Combates en el Catatumbo',
                                       estricta=False), 'Alertas Defensoría del Pueblo')


class TestPerfilDefensoria(unittest.TestCase):
    def test_criterio_y_flags(self):
        self.assertTrue(P['tono_alertas_tempranas'])
        self.assertTrue(P['agrupacion_estricta'])
        self.assertIn('Iris Marín Ortiz', P['criterio_custom'])
        self.assertIn('Alertas Tempranas: depende de la nota', P['criterio_custom'])
        self.assertEqual(perfil_cliente.validar_perfil(P), [])


class TestTextoMinimo(unittest.TestCase):
    RES = ('La Alcaldía de Bogotá anunció cambios. El alcalde Galán dijo que habrá nuevas rutas en Suba. '
           'Las obras empiezan en marzo y durarán dos años.')

    def test_largo_no_se_toca(self):
        t = ' '.join(['palabra'] * 20)
        self.assertEqual(texto_con_minimo(t, self.RES), t)

    def test_completa_con_oraciones_siguientes_literales(self):
        out = texto_con_minimo('El alcalde Galán dijo que habrá nuevas rutas en Suba.', self.RES)
        self.assertEqual(out, 'El alcalde Galán dijo que habrá nuevas rutas en Suba. '
                              'Las obras empiezan en marzo y durarán dos años.')
        self.assertGreater(len(out.split()), 15)

    def test_vacio_toma_inicio_del_resumen(self):
        out = texto_con_minimo('', self.RES)
        self.assertTrue(out.startswith('La Alcaldía de Bogotá anunció cambios.'))
        self.assertGreater(len(out.split()), 15)

    def test_no_inventa_palabras(self):
        out = texto_con_minimo('Galán habló.', self.RES)
        vocab = set(('Galán habló. ' + self.RES).split())
        self.assertTrue(set(out.split()) <= vocab)

    def test_sin_resumen_queda_igual(self):
        self.assertEqual(texto_con_minimo('Corto.', ''), 'Corto.')

    def test_quita_el_titular(self):
        out = texto_con_minimo('', 'Titular X. ' + self.RES, titulo='Titular X.')
        self.assertNotIn('Titular X', out)



if __name__ == '__main__':
    unittest.main()
