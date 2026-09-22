"""Pruebas v4.8: modo sin tema (solo tono + subtema).

Cubre el flag `incluir_tema` pedido por el usuario (2026-09-22):
  A. `output_columns_for_export(..., include_tema=False)` excluye la columna
     Tema_IA pero conserva Tono_IA y Subtema_IA.
  B. `volcar_analisis_en_filas(..., incluir_tema=False)` deja Tema_IA vacío y
     no ejecuta el fallback determinista de tema.
  C. `enrich_rows_with_ai(..., incluir_tema=False)` nunca llama a
     `asignar_temas` ni a `corregir_temas_con_jev` (la etapa LLM de temas se
     omite por completo).
  D. Regresión: con `incluir_tema=True` (default) el comportamiento no cambia.
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub del paquete `openai`: no instalable en este entorno (conflicto debian,
# mismo motivo del error preexistente en test_pkl_tono_tema). Ningún test de
# este archivo hace llamadas reales a la API.
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az
from pipeline import output_columns_for_export

KM = {'titulo': 'Título'}


def fila(titulo, cuerpo):
    return {'Título': titulo, 'CuerpoEs': cuerpo}


class TestColumnasSinTema(unittest.TestCase):
    def test_excluye_tema_ia(self):
        cols = output_columns_for_export(include_ai=True, include_tema=False)
        self.assertIn('Tono_IA', cols)
        self.assertIn('Subtema_IA', cols)
        self.assertNotIn('Tema_IA', cols)
        # Orden: Tono_IA y Subtema_IA siguen justo después de Audiencia.
        self.assertLess(cols.index('Tono_IA'), cols.index('Subtema_IA'))
        self.assertEqual(cols[-1], 'Contexto analizado')

    def test_default_conserva_tema_ia(self):
        cols = output_columns_for_export(include_ai=True)
        self.assertIn('Tema_IA', cols)
        cols2 = output_columns_for_export(include_ai=True, include_tema=True)
        self.assertEqual(cols, cols2)

    def test_sin_ia_no_cambia(self):
        cols = output_columns_for_export(include_ai=False, include_tema=False)
        self.assertNotIn('Tono_IA', cols)
        self.assertNotIn('Tema_IA', cols)
        self.assertNotIn('Subtema_IA', cols)


class TestVolcarSinTema(unittest.TestCase):
    def _rows(self):
        return [
            fila('La marca inaugura planta nueva en la región',
                 'La marca inauguró una planta con inversión millonaria.'),
            {'Título': 'dup', 'CuerpoEs': 'x', 'is_duplicate': True},
        ]

    def test_tema_ia_vacio_sin_fallback(self):
        rows = self._rows()
        mapa = {0: 1}
        etiquetas = {1: {'tono': 'Positivo', 'sub_tema': 'Expansión de la marca'}}
        az.volcar_analisis_en_filas(rows, mapa, etiquetas, {}, incluir_tema=False)
        self.assertEqual(rows[0]['Tema_IA'], '')
        self.assertEqual(rows[0]['Tono_IA'], 'Positivo')
        self.assertEqual(rows[0]['Subtema_IA'], 'Expansión de la marca')
        self.assertEqual(rows[1]['Tema_IA'], '')

    def test_default_mantiene_tema(self):
        rows = self._rows()
        mapa = {0: 1}
        etiquetas = {1: {'tono': 'Positivo', 'sub_tema': 'Expansión de la marca'}}
        az.volcar_analisis_en_filas(rows, mapa, etiquetas, {1: 'Crecimiento empresarial'})
        self.assertEqual(rows[0]['Tema_IA'], 'Crecimiento empresarial')


class TestEnrichOmiteEtapaTemas(unittest.TestCase):
    def _extra(self):
        return {'incluir_tema': False, 'voceros': []}

    def test_no_llama_asignar_temas(self):
        rows = [
            fila('La marca lanza un programa social en barrios vulnerables',
                 'La marca anunció un programa social que beneficiará a miles de familias.'),
            fila('Gremio hotelero reporta ocupación récord en temporada',
                 'El gremio reportó cifras históricas de ocupación hotelera este mes.'),
        ]
        with patch.object(az, 'etiquetar_grupos',
                          return_value={0: {'tono': 'Positivo', 'sub_tema': 'Programa social'},
                                        1: {'tono': 'Neutro', 'sub_tema': 'Ocupación hotelera'}}), \
             patch.object(az, 'canonizar_subtemas', return_value=0), \
             patch.object(az, 'unificar_subtemas_noticias_similares', return_value=0), \
             patch.object(az, 'asignar_temas',
                          side_effect=AssertionError('asignar_temas no debe llamarse')), \
             patch.object(az, 'corregir_temas_con_jev',
                          side_effect=AssertionError('jev no debe llamarse')):
            out = az.enrich_rows_with_ai(
                rows, KM, brand='Marca', aliases=[], api_key='k',
                extra=self._extra(), progress_callback=lambda p, m: None)
        for r in out:
            self.assertEqual(r['Tema_IA'], '')
            self.assertTrue(r['Tono_IA'])
            self.assertTrue(r['Subtema_IA'])
        self.assertEqual(az.ultimo_resumen().get('modo_taxonomia'), 'omitido')


if __name__ == '__main__':
    unittest.main()
