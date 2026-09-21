"""Pruebas de precisión v4.7: mismo hecho = mismo tono/tema/subtema + tono hacia la marca.

Cubren las mejoras pedidas por el usuario (2026-09-21):
  A. Agrupación por Contexto analizado: noticias iguales/similares se detectan
     por Título, CuerpoEs o contexto analizado similar (no solo keywords).
  B. Tono hacia la marca: gestiones/estudios/acciones propias -> Positivo;
     críticas/ataques/crisis contra la marca -> Negativo;
     crítica CON respuesta de la marca -> Neutro (equilibra la información).
  C. El tema debe corresponder al subtema Y a la noticia (sin forzados).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyzer_tono_tema as az

BRAND = 'Universidad Simón Bolívar'
ALIASES = ['Unisimón']
VOCEROS = ['Rector Pérez']


def grupo(gid, titulo, contexto, tono='Neutro', subtema='Hecho de prueba'):
    return {'grupo': gid, 'titulo': titulo, 'contexto': contexto,
            'texto': contexto}


class TestAgrupacionPorContexto(unittest.TestCase):
    CTX_ESTUDIO = (
        "La Universidad Simón Bolívar publicó el estudio Brechas digitales en el Caribe "
        "con datos de 12000 hogares encuestados en cinco departamentos durante 2025. "
        "El rector Pérez afirmó que los resultados muestran avances importantes en "
        "conectividad rural y que la institución seguirá midiendo el fenómeno cada año "
        "en toda la región Caribe."
    )
    CTX_ESTUDIO_B = (
        "Con datos de 12000 hogares encuestados en cinco departamentos durante 2025, "
        "la Universidad Simón Bolívar publicó el estudio Brechas digitales en el Caribe. "
        "El rector Pérez afirmó que los resultados muestran avances importantes en "
        "conectividad rural y que la institución seguirá midiendo el fenómeno cada año "
        "en toda la región Caribe."
    )
    CTX_OTRO = (
        "La Universidad Simón Bolívar inauguró el nuevo bloque de laboratorios de ingeniería "
        "con una inversión de 8000 millones de pesos. El rector Pérez destacó que la obra "
        "beneficiará a más de 3000 estudiantes de pregrado y posgrado en la sede principal."
    )

    def _filas(self, ctx1, ctx2):
        return [
            {'Título': 'Universidad publica investigación sobre conectividad regional',
             'CuerpoEs': 'cuerpo corto uno',
             'Contexto analizado': ctx1},
            {'Título': 'Rector presenta avances del estudio sobre brechas digitales',
             'CuerpoEs': 'cuerpo corto dos',
             'Contexto analizado': ctx2},
        ]

    def test_mismo_contexto_une_aunque_titulo_difiera(self):
        grupos, _mapa = az.construir_grupos(self._filas(self.CTX_ESTUDIO, self.CTX_ESTUDIO_B), {})
        self.assertEqual(len(grupos), 1, 'mismo hecho por contexto debió formar un solo grupo')

    def test_contextos_distintos_no_se_unen(self):
        grupos, _mapa = az.construir_grupos(self._filas(self.CTX_ESTUDIO, self.CTX_OTRO), {})
        self.assertEqual(len(grupos), 2, 'hechos distintos no deben unirse por la marca')

    def test_contextos_mismo_hecho_helper(self):
        self.assertTrue(az._contextos_mismo_hecho(self.CTX_ESTUDIO, self.CTX_ESTUDIO_B))
        self.assertFalse(az._contextos_mismo_hecho(self.CTX_ESTUDIO, self.CTX_OTRO))
        self.assertFalse(az._contextos_mismo_hecho('', self.CTX_ESTUDIO))
        self.assertFalse(az._contextos_mismo_hecho('corto', 'corto'))

    def test_unificar_subtemas_por_contexto_marca(self):
        grupos = [
            {'grupo': 1, 'titulo': 'Universidad publica investigación sobre conectividad',
             'contexto_marca': self.CTX_ESTUDIO},
            {'grupo': 2, 'titulo': 'Rector presenta avances del estudio regional',
             'contexto_marca': self.CTX_ESTUDIO_B},
        ]
        etiquetas = {1: {'sub_tema': 'Estudio sobre brechas digitales'},
                     2: {'sub_tema': 'Presentación de resultados de conectividad'}}
        az.unificar_subtemas_noticias_similares(grupos, etiquetas)
        self.assertEqual(az.nz(etiquetas[1]['sub_tema']), az.nz(etiquetas[2]['sub_tema']))

    def test_unificar_tono_por_contexto_marca(self):
        ctx = self.CTX_ESTUDIO + ' ' + self.CTX_ESTUDIO_B
        grupos = [
            {'grupo': 1, 'titulo': 't1', 'contexto_marca': ctx},
            {'grupo': 2, 'titulo': 't2', 'contexto_marca': ctx},
        ]
        etiquetas = {1: {'sub_tema': 'Sanción a exsecretario de Educación', 'tono': 'Positivo'},
                     2: {'sub_tema': 'Sanción por retraso en el PAE', 'tono': 'Neutro'}}
        cambios = az.unificar_tono_mismo_hecho(grupos, etiquetas)
        self.assertGreater(cambios, 0)
        self.assertEqual(etiquetas[1]['tono'], etiquetas[2]['tono'])

    def test_unificar_tono_no_toca_negativo(self):
        ctx = self.CTX_ESTUDIO + ' ' + self.CTX_ESTUDIO_B
        grupos = [
            {'grupo': 1, 'titulo': 't1', 'contexto_marca': ctx},
            {'grupo': 2, 'titulo': 't2', 'contexto_marca': ctx},
        ]
        etiquetas = {1: {'sub_tema': 'Hecho X', 'tono': 'Positivo'},
                     2: {'sub_tema': 'Hecho Y', 'tono': 'Negativo'}}
        az.unificar_tono_mismo_hecho(grupos, etiquetas)
        self.assertEqual(etiquetas[2]['tono'], 'Negativo')


class TestTonoHaciaLaMarca(unittest.TestCase):
    def test_critica_con_respuesta_baja_a_neutro(self):
        ctx = ("El concejal denunció que la Universidad Simón Bolívar incurrió en sobrecostos "
               "en la obra del campus. En respuesta a la denuncia, el rector Pérez presentó un "
               "descargo con los soportes de la contratación y aseguró que los recursos se "
               "ejecutaron correctamente.")
        etiquetas = {1: {'tono': 'Negativo', 'sub_tema': 'Denuncia por sobrecostos'}}
        bajados = az.aplicar_regla_critica_con_respuesta(
            [grupo(1, 'Concejal denuncia sobrecostos en obra del campus', ctx)],
            etiquetas, BRAND, ALIASES, VOCEROS)
        self.assertEqual(etiquetas[1]['tono'], 'Neutro')
        self.assertEqual(bajados, [1])

    def test_critica_sin_respuesta_se_mantiene_negativo(self):
        ctx = ("El concejal denunció que la Universidad Simón Bolívar incurrió en sobrecostos "
               "en la obra del campus y exigió una investigación de la contraloría.")
        etiquetas = {1: {'tono': 'Negativo', 'sub_tema': 'Denuncia por sobrecostos'}}
        bajados = az.aplicar_regla_critica_con_respuesta(
            [grupo(1, 'Concejal denuncia sobrecostos en obra del campus', ctx)],
            etiquetas, BRAND, ALIASES, VOCEROS)
        self.assertEqual(etiquetas[1]['tono'], 'Negativo')
        self.assertEqual(bajados, [])

    def test_critica_con_respuesta_no_sube_a_positivo(self):
        # El Neutro de la crítica equilibrada es "pegajoso": la guarda positiva
        # no lo sube aunque el vocero declare.
        ctx = ("El concejal denunció sobrecostos en la obra del campus de la Universidad "
               "Simón Bolívar. En respuesta a la denuncia, el rector Pérez presentó un "
               "descargo y aseguró que los recursos se ejecutaron correctamente.")
        etiquetas = {1: {'tono': 'Negativo', 'sub_tema': 'Denuncia por sobrecostos'}}
        gs = [grupo(1, 'Concejal denuncia sobrecostos en obra del campus', ctx)]
        az.aplicar_regla_critica_con_respuesta(gs, etiquetas, BRAND, ALIASES, VOCEROS)
        az.aplicar_guarda_positiva(gs, etiquetas, BRAND, ALIASES, VOCEROS)
        self.assertEqual(etiquetas[1]['tono'], 'Neutro')

    def test_ataque_dirigido_a_la_marca_es_critica(self):
        self.assertTrue(az._critica_dirigida(
            'El concejal ataca a la Universidad Simón Bolívar por el manejo de los recursos',
            BRAND, ALIASES))
        self.assertTrue(az._critica_dirigida(
            'Escándalo por el desvío de recursos en la Universidad Simón Bolívar, según la contraloría',
            BRAND, ALIASES))

    def test_ataque_sin_blanco_no_es_critica_dirigida(self):
        self.assertFalse(az._critica_dirigida(
            'Un ataque de risa se apoderó del auditorio durante la ceremonia',
            BRAND, ALIASES))

    def test_estudio_de_la_marca_es_positivo(self):
        ctx = ("La Universidad Simón Bolívar publicó el estudio Brechas digitales en el Caribe "
               "con datos de 12000 hogares encuestados en cinco departamentos.")
        etiquetas = {1: {'tono': 'Neutro', 'sub_tema': 'Estudio sobre brechas digitales'}}
        subidos = az.aplicar_guarda_positiva(
            [grupo(1, 'Estudio sobre brechas digitales', ctx)],
            etiquetas, BRAND, ALIASES, VOCEROS)
        self.assertEqual(etiquetas[1]['tono'], 'Positivo')
        self.assertEqual(subidos, [1])

    def test_gestion_de_la_marca_es_positivo(self):
        ctx = ("La Universidad Simón Bolívar inauguró el nuevo bloque de laboratorios con una "
               "inversión de 8000 millones de pesos.")
        etiquetas = {1: {'tono': 'Neutro', 'sub_tema': 'Inauguración de laboratorios'}}
        az.aplicar_guarda_positiva(
            [grupo(1, 'Inauguración de laboratorios', ctx)],
            etiquetas, BRAND, ALIASES, VOCEROS)
        self.assertEqual(etiquetas[1]['tono'], 'Positivo')

    def test_respuesta_marca_detecta_descargo_del_actor(self):
        self.assertTrue(az._respuesta_marca(
            'En respuesta a la denuncia, el rector Pérez presentó un descargo.',
            ['rector perez', 'universidad simon bolivar']))
        self.assertFalse(az._respuesta_marca(
            'El concejal denunció sobrecostos y exigió una investigación.',
            ['rector perez', 'universidad simon bolivar']))


class TestTemaCorresponde(unittest.TestCase):
    def test_tema_ajeno_no_se_asigna_al_miembro(self):
        self.assertFalse(az._tema_relevante_para_miembro(
            'Prevención del suicidio', 'Ascenso político de Gutiérrez',
            'El diputado Gutiérrez anunció su candidatura a la gobernación'))

    def test_tema_propio_si_se_asigna(self):
        self.assertTrue(az._tema_relevante_para_miembro(
            'Prevención del suicidio', 'Semana de prevención del suicidio',
            'La universidad organiza la semana de prevención del suicidio'))

    def test_prompt_temas_incluye_contextos(self):
        fam = [{'id': 1, 'subtemas': ['Estudio sobre brechas digitales'],
                'titulos': ['Universidad publica estudio'],
                'contextos': ['La Universidad Simón Bolívar publicó el estudio con datos de 12000 hogares.']}]
        txt = az.prompt_temas_familias(fam)
        self.assertIn('CONTEXTOS:', txt)
        self.assertIn('12000 hogares', txt)


if __name__ == '__main__':
    unittest.main()
