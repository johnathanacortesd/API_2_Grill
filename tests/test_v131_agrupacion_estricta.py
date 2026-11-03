# -*- coding: utf-8 -*-
"""v1.31: agrupación estricta por evidencia (título, Contexto analizado o
cuerpo iguales o muy parecidos) y Jev en paralelo con el etiquetado."""
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

CFG = {'brand': 'Secretaría General de la Alcaldía de Bogotá', 'aliases': ['Alcaldía de Bogotá'],
       'voceros': ['Carlos Fernando Galán', 'Galán'], 'model': 'x', 'agrupacion_estricta': True}
CTX_METRO = ('El alcalde Carlos Fernando Galán anunció que la licitación de la segunda línea del '
             'metro de Bogotá abrirá en enero y que la obra conectará Suba y Engativá con el centro.')


def _g(gid, tit, ctx, texto=None, n=1):
    return {'grupo': gid, 'n': n, 'titulo': tit, 'titulos_alt': [], 'texto': texto or ctx,
            'contexto': ctx, 'contexto_marca': ctx}


class TestGrafoEvidencia(unittest.TestCase):
    def test_titulos_muy_parecidos_son_evidencia(self):
        g = [_g(1, 'Galán anuncia licitación de la segunda línea del metro', 'a b c'),
             _g(2, 'Galán anunció licitación de la segunda línea del metro', 'x y z')]
        self.assertIn(2, az.grafo_evidencia_grupos(g)[1])

    def test_mismo_tema_distinta_noticia_no_es_evidencia(self):
        g = [_g(1, 'Galán anuncia licitación de la segunda línea del metro', CTX_METRO),
             _g(2, 'Concejales critican sobrecostos en obras del metro',
                'Concejales del Polo criticaron los sobrecostos en las obras del viaducto de la '
                'primera línea y pidieron a la empresa Metro de Bogotá explicaciones públicas.')]
        self.assertEqual(az.grafo_evidencia_grupos(g)[1], set())

    def test_contexto_analizado_compartido_es_evidencia(self):
        g = [_g(1, 'Arranca la contratación de la línea 2', CTX_METRO),
             _g(2, 'Lo que dijo Galán sobre el metro', 'Según el medio, ' + CTX_METRO)]
        self.assertIn(2, az.grafo_evidencia_grupos(g)[1])


class TestConsolidarPorEvidencia(unittest.TestCase):
    def test_sin_evidencia_no_se_agrupa_ni_llama_al_modelo(self):
        g = [_g(1, 'Galán anuncia licitación de la segunda línea del metro', CTX_METRO),
             _g(2, 'Concejales critican sobrecostos en obras del metro',
                'Concejales criticaron al alcalde Galán por sobrecostos en el viaducto del metro.')]
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              2: {'sub_tema': 'Sobrecostos en obras del metro', 'tono': 'Negativo'}}
        with patch.object(az, 'llamar_llm', side_effect=AssertionError('no debe llamar')):
            az.consolidar_por_evidencia(CFG, g, et, uso={})
        self.assertEqual(et[2]['sub_tema'], 'Sobrecostos en obras del metro')
        self.assertNotEqual(et[1]['hecho'], et[2]['hecho'])

    def test_con_evidencia_y_rotulo_fiel_se_unifica_sin_llm(self):
        g = [_g(1, 'Arranca la contratación de la línea 2', CTX_METRO, n=3),
             _g(2, 'Lo que dijo Galán sobre el metro', 'Según el medio, ' + CTX_METRO)]
        et = {1: {'sub_tema': 'Licitación de la segunda línea del metro', 'tono': 'Positivo'},
              2: {'sub_tema': 'Declaraciones sobre el metro', 'tono': 'Neutro'}}
        with patch.object(az, 'llamar_llm', side_effect=AssertionError('no debe llamar')):
            cambios = az.consolidar_por_evidencia(CFG, g, et, uso={})
        self.assertEqual(cambios, 1)
        self.assertEqual(et[2]['sub_tema'], 'Licitación de la segunda línea del metro')
        self.assertEqual(et[1]['hecho'], et[2]['hecho'])


class TestPasesTituloEstrictos(unittest.TestCase):
    def test_titulos_que_comparten_palabras_no_se_unen(self):
        rows = [{'Título': 'Alcaldía de Bogotá anuncia nuevas medidas de seguridad en Kennedy',
                 'CuerpoEs': 'Medidas de seguridad en Kennedy anunciadas por la Alcaldía.'},
                {'Título': 'Alcaldía de Bogotá anuncia nuevas medidas de movilidad en Suba por obras',
                 'CuerpoEs': 'Cierres viales en Suba por obras del metro.'}]
        mapa = {0: 1, 1: 2}
        et = {1: {'sub_tema': 'Medidas de seguridad en Kennedy', 'tono': 'Positivo'},
              2: {'sub_tema': 'Cierres viales en Suba', 'tono': 'Neutro'}}
        n = az.unificar_subtema_titulo_final(rows, mapa, et, {}, {'titulo': 'Título'},
                                             brand=CFG['brand'], aliases=CFG['aliases'],
                                             estricta=True)
        self.assertEqual(n, 0)

    def test_construir_grupos_estricto_omite_anclas(self):
        rows = [{'Título': 'Uno dos tres cuatro', 'CuerpoEs': 'texto uno'},
                {'Título': 'Cinco seis siete ocho', 'CuerpoEs': 'texto dos'}]
        with patch.object(az, '_mismo_evento', return_value=True), \
                patch.object(az, '_misma_historia_riesgo', return_value=True):
            g_laxo, _ = az.construir_grupos(rows, {'titulo': 'Título'})
            g_estr, _ = az.construir_grupos(rows, {'titulo': 'Título'}, estricta=True)
        self.assertEqual(len(g_laxo), 1)
        self.assertEqual(len(g_estr), 2)


class TestJevEnParalelo(unittest.TestCase):
    def test_jev_aplica_tono_y_corre_aunque_el_etiquetado_tarde(self):
        grupos = [{'grupo': 1, 'n': 1, 'titulo': 'Galán entrega parque en Usme',
                   'texto': 'El alcalde Galán entregó un parque en Usme.', 'contexto': ''}]
        r = json.dumps({'resultados': [{'id': 1, 'sub_tema': 'Entrega de parque en Usme',
                                        'tono': 'Neutro'}]})
        cfg = dict(CFG, typesafe_api_key='k')
        with patch.object(az, 'llamar_llm', return_value=r), \
                patch.object(az, '_tono_con_jev', return_value='Positivo') as jev:
            et = az.etiquetar_grupos(cfg, grupos, votos=1, uso={})
        self.assertEqual(jev.call_count, 1)
        self.assertEqual(et[1]['tono'], 'Positivo')


class TestDefaultGrill(unittest.TestCase):
    def test_estricta_es_default_en_grill(self):
        # v4.71 (Grill): sin flag en el perfil, la agrupación es estricta.
        import inspect
        src = inspect.getsource(az.enrich_rows_with_ai)
        self.assertIn("extra.get('agrupacion_estricta', True)", src)


class TestPositivoGestion(unittest.TestCase):
    A = [az.nz(x) for x in ['Alcaldía de Bogotá', 'Galán', 'el alcalde', 'Carlos Fernando Galán']]

    def test_marca_sujeto_de_gestion(self):
        self.assertTrue(az._marca_sujeto_de_gestion(
            'La Alcaldía de Bogotá presentó el diseño del parque de Usme.', self.A))
        self.assertTrue(az._marca_sujeto_de_gestion(
            'El alcalde Carlos Fernando Galán entregó 30 buses eléctricos.', self.A))

    def test_otro_sujeto_tras_coma_no_cuenta(self):
        self.assertFalse(az._marca_sujeto_de_gestion(
            'Según datos de la Alcaldía de Bogotá, la empresa X inauguró su sede.', self.A))
        self.assertFalse(az._marca_sujeto_de_gestion(
            'Un vecino presentó una queja; la Alcaldía de Bogotá no se pronunció.', self.A))

    def test_gestion_con_una_mencion_sigue_positiva(self):
        g = {'grupo': 1, 'titulo': 'Así será el nuevo parque de Usme',
             'contexto': 'La Alcaldía de Bogotá presentó el diseño del parque de Usme.'}
        et = {1: {'tono': 'Positivo', 'sub_tema': 'Diseño del parque de Usme'}}
        az.aplicar_regla_positivo_incidental([g], et, CFG['brand'], CFG['aliases'],
                                             voceros=CFG['voceros'])
        self.assertEqual(et[1]['tono'], 'Positivo')


class TestDesempateConEvidencia(unittest.TestCase):
    KM = {'titulo': 'Título'}

    def _rows(self, ctx2):
        return [{'Título': 'Galán anuncia licitación de la línea 2', 'CuerpoEs': 'x',
                 'Contexto analizado': 'El alcalde Galán anunció la licitación de la línea 2 del metro.'},
                {'Título': 'Licitación de la línea 2 del metro', 'CuerpoEs': 'x',
                 'Contexto analizado': ctx2}]

    def test_positivo_neutro_con_gestion_gana_positivo(self):
        et = {1: {'sub_tema': 'Licitación de la línea 2', 'tono': 'Positivo', 'hecho': 'e1'},
              2: {'sub_tema': 'Licitación de la línea 2', 'tono': 'Neutro', 'hecho': 'e1'}}
        az.garantizar_coherencia_hecho(self._rows('La licitación de la línea 2 abre en enero.'),
                                       {0: 1, 1: 2}, et, {}, self.KM, brand=CFG['brand'],
                                       aliases=CFG['aliases'], voceros=CFG['voceros'])
        self.assertEqual({et[1]['tono'], et[2]['tono']}, {'Positivo'})

    def test_negativo_neutro_con_critica_gana_negativo(self):
        et = {1: {'sub_tema': 'Licitación de la línea 2', 'tono': 'Negativo', 'hecho': 'e1'},
              2: {'sub_tema': 'Licitación de la línea 2', 'tono': 'Neutro', 'hecho': 'e1'}}
        rows = self._rows('Concejales cuestionaron al alcalde Galán por la licitación de la línea 2.')
        rows[0]['Contexto analizado'] = 'La licitación de la línea 2 del metro abre en enero.'
        az.garantizar_coherencia_hecho(rows, {0: 1, 1: 2}, et, {}, self.KM, brand=CFG['brand'],
                                       aliases=CFG['aliases'], voceros=CFG['voceros'])
        self.assertEqual({et[1]['tono'], et[2]['tono']}, {'Negativo'})


class TestPatronCritica(unittest.TestCase):
    def test_contratacion_no_es_critica(self):
        self.assertIsNone(az.CRITICA_PAT.search('la contratación de la línea 2 del metro'))
        self.assertIsNone(az.CRITICA_PAT.search('las fotomultas de Bogotá'))

    def test_criticas_reales_siguen(self):
        for t in ('Concejales atacan al alcalde', 'denuncian sobrecostos',
                  'le pusieron una multa a la Alcaldía'):
            self.assertIsNotNone(az.CRITICA_PAT.search(t), t)


if __name__ == '__main__':
    unittest.main()
