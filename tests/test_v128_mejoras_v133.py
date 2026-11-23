# -*- coding: utf-8 -*-
"""Defensoría v1.28: mejoras genéricas traídas de Secretaría v1.33."""
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
B, A, V = P['brand'], P['aliases'], P['voceros']
KM = {'titulo': 'Título'}


class TestTextoCreditoAlFinal(unittest.TestCase):
    def test_toma_oraciones_anteriores(self):
        res = ('La Defensoría acompañó a las comunidades desplazadas del Catatumbo durante toda '
               'la semana y verificó las condiciones de los albergues temporales. Fuente: Defensoría del Pueblo.')
        out = texto_con_minimo('Fuente: Defensoría del Pueblo', res)
        self.assertGreater(len(out.split()), 15)
        self.assertTrue(out.endswith('Fuente: Defensoría del Pueblo'))


class TestSubtemaEventoGrande(unittest.TestCase):
    def test_un_subtema_por_evento(self):
        titulos = ['Crisis humanitaria en el Catatumbo deja miles de desplazados',
                   'Defensoría visita el Catatumbo por crisis humanitaria',
                   'Así avanza la crisis humanitaria en el Catatumbo',
                   'Catatumbo: las cifras de la crisis humanitaria']
        subs = ['Crisis humanitaria en el Catatumbo', 'Visita al Catatumbo',
                'Crisis humanitaria en el Catatumbo', 'Cifras de desplazamiento']
        ctx = 'La crisis humanitaria en el Catatumbo deja miles de desplazados, advirtió la Defensoría.'
        rows = [{'Título': t, 'CuerpoEs': ctx, 'Contexto analizado': ctx} for t in titulos]
        et = {i + 1: {'sub_tema': s_} for i, s_ in enumerate(subs)}
        aj = az.unificar_por_asunto_titular(rows, {i: i + 1 for i in range(4)}, et, {}, KM,
                                            brand=B, aliases=A, voceros=V)
        self.assertEqual({aj.get(i) or subs[i] for i in range(4)}, {'Crisis humanitaria en el Catatumbo'})

    def test_variantes_del_rotulo(self):
        ctx = 'Asesinato del líder social Juan Pérez en Arauca, denunció la Defensoría.'
        rows = [{'Título': 'Asesinan a líder social Juan Pérez', 'CuerpoEs': ctx, 'Contexto analizado': ctx},
                {'Título': 'Matan a Juan Pérez en Arauca', 'CuerpoEs': ctx, 'Contexto analizado': ctx}]
        et = {1: {'sub_tema': 'Asesinato del líder social Juan Pérez'},
              2: {'sub_tema': 'Asesinato de Juan Pérez'}}
        aj = az.unificar_variantes_rotulo_estricto(rows, {0: 1, 1: 2}, et, {}, KM, brand=B, aliases=A)
        self.assertEqual(aj.get(1), 'Asesinato del líder social Juan Pérez')


class TestVerbosGestion(unittest.TestCase):
    def test_designacion_es_gestion(self):
        act = [az.nz(x) for x in [B] + A + V if len(az.nz(x)) >= 4]
        self.assertTrue(az._marca_sujeto_de_gestion(
            'La Defensoría del Pueblo designó a un nuevo defensor regional para el Chocó.', act))


class TestLaborPositiva(unittest.TestCase):
    def _t(self, tit, ctx, tono='Neutro'):
        g = {'grupo': 1, 'titulo': tit, 'contexto': ctx}
        et = {1: {'tono': tono, 'sub_tema': 'x'}}
        az.aplicar_regla_labor_positiva([g], et, B, A, voceros=V)
        return et[1]['tono']

    def test_acompanamiento_es_positivo(self):
        self.assertEqual(self._t('Defensoría acompaña a desplazados',
                                 'La Defensoría del Pueblo acompañó a 300 familias desplazadas en Arauca.'),
                         'Positivo')

    def test_vocera_advierte_es_positivo(self):
        self.assertEqual(self._t('Riesgo para líderes', 'Iris Marín advirtió sobre el riesgo para líderes sociales.'),
                         'Positivo')

    def test_mencion_de_paso_sigue_neutro(self):
        self.assertEqual(self._t('Gobierno lanza plan de vivienda',
                                 'El Gobierno lanzó un plan; la Defensoría del Pueblo asistió al evento.'), 'Neutro')

    def test_critica_a_la_defensoria_no_sube(self):
        self.assertEqual(self._t('Critican a la Defensoría', 'La Defensoría del Pueblo fue criticada por su informe.'),
                         'Neutro')


class TestConservarRotulo(unittest.TestCase):
    def test_no_cambia_por_copia_del_titular(self):
        tit = 'Congresistas cuestionan a Iris Marín por informe incompleto'
        txt = 'Congresistas cuestionaron a Iris Marín por presentar un informe incompleto sobre líderes sociales.'
        rows = [{'Título': tit, 'CuerpoEs': txt}]
        et = {1: {'sub_tema': 'Críticas por informe sobre líderes'}}
        self.assertEqual(az.aplicar_guarda_fidelidad_subtema(rows, {0: 1}, et, KM, brand=B, aliases=A), {})

    def test_rotulo_de_otra_noticia_se_sigue_rechazando(self):
        tit = 'Defensoría acompaña a desplazados en Arauca'
        txt = 'La Defensoría acompañó a 300 familias desplazadas en Arauca.'
        rows = [{'Título': tit, 'CuerpoEs': txt}]
        et = {1: {'sub_tema': 'Diplomado para formación de cuidadores'}}
        self.assertIn(0, az.aplicar_guarda_fidelidad_subtema(rows, {0: 1}, et, KM, brand=B, aliases=A))


if __name__ == '__main__':
    unittest.main()
