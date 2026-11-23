# -*- coding: utf-8 -*-
"""Portado de Secretaría v1.30: nombres de tema, tono por hecho, crítica
a voceros (sin la columna texto, que es solo de Secretaría)."""
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

KM = {'titulo': 'Título'}
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestNombreDeTema(unittest.TestCase):
    def test_nombres_de_proyecto_son_validos(self):
        for s, t in [('Línea 137', 'Línea 137: denuncias por abusos contra mujeres'),
                     ('Metro de Bogotá', 'Metro de Bogotá avanza 60 % en obras'),
                     ('Regiotram del Norte', 'Firman convenio para el Regiotram del Norte')]:
            self.assertEqual(az.validar(s, 'Neutro', [t]), [], s)

    def test_recorte_en_minuscula_sigue_siendo_copia(self):
        pr = az.validar('Segunda línea del metro', 'Neutro',
                        ['Galán anuncia licitación de la segunda línea del metro'])
        self.assertIn('copia_titular', pr)

    def test_titular_identico_sigue_siendo_copia(self):
        self.assertIn('copia_titular', az.validar('Metro de Bogotá', 'Neutro', ['Metro de Bogotá']))

    def test_una_palabra_no_es_nombre_de_tema(self):
        self.assertFalse(az._es_nombre_de_tema('TransMilenio'))
        self.assertFalse(az._es_nombre_de_tema('Obras del metro'))


def _fila(tit, ctx):
    return {'Título': tit, 'CuerpoEs': ctx, 'Contexto analizado': ctx}


class TestTonoPorHecho(unittest.TestCase):
    def test_mismo_tema_hechos_distintos_conservan_tono(self):
        rows = [_fila('Metro de Bogotá llega al 70 % de avance', 'El metro de Bogotá avanza, dijo la Alcaldía de Bogotá.'),
                _fila('Concejales critican sobrecostos del metro', 'Concejales critican al alcalde Galán por sobrecostos del metro.')]
        mapa = {0: 1, 1: 2}
        et = {1: {'sub_tema': 'Metro de Bogotá', 'tono': 'Positivo', 'hecho': 'h1'},
              2: {'sub_tema': 'Metro de Bogotá', 'tono': 'Negativo', 'hecho': 'h2'}}
        az.garantizar_coherencia_hecho(rows, mapa, et, {}, KM)
        self.assertEqual(et[1]['tono'], 'Positivo')
        self.assertEqual(et[2]['tono'], 'Negativo')

    def test_mismo_hecho_mismo_tono_y_tipo(self):
        rows = [_fila('Metro de Bogotá llega al 70 % de avance', 'El metro de Bogotá avanza.'),
                _fila('Metro de Bogotá llega al 70 % de avance', 'El metro de Bogotá avanza.'),
                _fila('Obras del metro superan el 70 %', 'Las obras del metro de Bogotá avanzan.')]
        mapa = {0: 1, 1: 1, 2: 2}
        et = {1: {'sub_tema': 'Metro de Bogotá', 'tono': 'Positivo', 'hecho': 'h1',
                  'tipo': 'Proyectos y programas'},
              2: {'sub_tema': 'Metro de Bogotá', 'tono': 'Neutro', 'hecho': 'h1',
                  'tipo': 'Información general'}}
        az.garantizar_coherencia_hecho(rows, mapa, et, {}, KM)
        self.assertEqual(et[2]['tono'], 'Positivo')
        self.assertEqual(et[2]['tipo'], 'Proyectos y programas')

    def test_voto_final_por_hecho_no_por_tema(self):
        grupos = [{'grupo': g} for g in (1, 2, 3)]
        et = {1: {'sub_tema': 'Metro de Bogotá', 'tono': 'Positivo', 'hecho': 'h1'},
              2: {'sub_tema': 'Metro de Bogotá', 'tono': 'Positivo', 'hecho': 'h2'},
              3: {'sub_tema': 'Metro de Bogotá', 'tono': 'Neutro', 'hecho': 'h3'}}
        az.voto_final_tono_por_subtema(grupos, et)
        self.assertEqual(et[3]['tono'], 'Neutro')


class TestConsolidacionPorContexto(unittest.TestCase):
    def test_agrupacion_exige_contexto_analizado(self):
        # El cuerpo de 2 habla de hurtos, pero su Contexto analizado no:
        # el rótulo común no corresponde al Contexto y no se agrupa.
        g1 = {'grupo': 1, 'n': 1, 'titulo': 'Concejales cuestionan a Galán por hurtos',
              'texto': 'Concejales cuestionan al alcalde Galán por el aumento de hurtos en Kennedy.',
              'contexto': 'Concejales cuestionan al alcalde Galán por el aumento de hurtos en Kennedy.'}
        g2 = {'grupo': 2, 'n': 1, 'titulo': 'Balance de la semana en Bogotá',
              'texto': 'Hubo aumento de hurtos en Kennedy. Además, la Alcaldía de Bogotá inauguró un parque.',
              'contexto': 'La Alcaldía de Bogotá inauguró un parque en Usme.'}
        et = {1: {'sub_tema': 'Críticas por aumento de hurtos', 'tono': 'Negativo'},
              2: {'sub_tema': 'Inauguración de parque en Usme', 'tono': 'Positivo'}}
        r = json.dumps({'hechos': [{'ids': [1, 2], 'sub_tema': 'Aumento de hurtos en Kennedy'}]})
        with patch.object(az, 'llamar_llm', return_value=r):
            az.consolidar_hechos_llm({'brand': 'Alcaldía de Bogotá', 'aliases': []}, [g1, g2], et, uso={})
        self.assertEqual(et[2]['sub_tema'], 'Inauguración de parque en Usme')
        self.assertNotEqual(et[1].get('hecho'), et[2].get('hecho'))


class TestTipo(unittest.TestCase):
    def test_reglas_tipo_del_usuario(self):
        r = az.REGLAS_TIPO_GENERO
        for clave in ('nombramientos', 'presupuesto', 'cambios de sede', 'subsidios',
                      'jornadas de acompañamiento', 'NO hacen parte de la entidad', 'ranking'):
            self.assertIn(clave, r)
        self.assertNotIn('aunque hable de un proyecto', r)


class TestPerfil(unittest.TestCase):
    def test_guia_conserva_alertas_y_precision(self):
        with open(os.path.join(RAIZ, 'clientes', 'defensoria_del_pueblo.json'), encoding='utf-8') as f:
            d = json.load(f)
        self.assertIn('Alertas Defensoría del Pueblo', d['guia_subtema'])
        self.assertIn('NO fuerces', d['guia_subtema'])
        self.assertFalse(d['clasificar_tipo_genero'])
        self.assertEqual((d['hora_boletin_manana'], d['hora_boletin_tarde']), ('8:00 a.m.', '4:00 p.m.'))


class TestCriticaAVoceros(unittest.TestCase):
    B = 'Secretaría General de la Alcaldía de Bogotá'
    A = ['Alcaldía de Bogotá']
    V = ['Carlos Fernando Galán', 'Galán', 'el alcalde', 'Miguel Silva Moyano']

    def test_critica_al_alcalde_o_secretario_es_dirigida(self):
        for t in ('Concejales criticaron al alcalde Galán por sobrecostos en el metro.',
                  'Denuncian a Miguel Silva Moyano por contratos irregulares.',
                  'Cuestionan a la Alcaldía de Bogotá por los trancones.'):
            self.assertTrue(az._marca_blanco_de_critica(t, self.B, az._con_voceros(self.A, self.V)), t)

    def test_negativo_al_alcalde_no_baja_a_neutro(self):
        g = {'grupo': 1, 'titulo': 'Concejales critican sobrecostos del metro',
             'contexto': 'Concejales criticaron al alcalde Galán por sobrecostos en el metro.'}
        et = {1: {'sub_tema': 'Sobrecostos del metro', 'tono': 'Negativo'}}
        az.aplicar_regla_negativo_sin_blanco([g], et, self.B, self.A, voceros=self.V)
        self.assertEqual(et[1]['tono'], 'Negativo')

    def test_suceso_ajeno_no_es_critica(self):
        self.assertFalse(az._marca_blanco_de_critica(
            'La Policía capturó a un ladrón en Kennedy.', self.B, az._con_voceros(self.A, self.V)))
