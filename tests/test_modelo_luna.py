"""Pruebas v4.9: selector de modelo (gpt-6-luna).

Verifica que el modelo elegido en la UI (`ai_config["model"]`) viaja sin
hardcodes hasta el payload de `llamar_llm`, de modo que `gpt-6-luna`
(lanzado 2026-09-22, $0.10/1M input) funciona con el endpoint
OpenAI-compatible que ya usa la app.
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

KM = {'titulo': 'Título'}


class TestModeloViajaHastaLlamada(unittest.TestCase):
    def test_gpt_6_luna_llega_al_payload(self):
        modelos_usados = []

        def fake_llamar_llm(cfg, mensajes, **kw):
            modelos_usados.append(cfg.get('model'))
            return ('{"resultados": [{"id": 1, "sub_tema": "Programa social comunitario", '
                    '"tono": "Positivo"}]}')

        rows = [{'Título': 'Cotelco lanza programa social en la región',
                 'CuerpoEs': 'Cotelco anunció un programa social que beneficiará a '
                            'miles de familias en la región Caribe con inversión '
                            'millonaria y apoyo del gremio hotelero.'}]
        extra = {'incluir_tema': False, 'voceros': [], 'votos': 1,
                 'tam_lote': 10, 'workers': 1}
        with patch.object(az, 'llamar_llm', side_effect=fake_llamar_llm), \
             patch.object(az, 'canonizar_subtemas', return_value=0), \
             patch.object(az, 'unificar_subtemas_noticias_similares', return_value=0):
            out = az.enrich_rows_with_ai(
                rows, KM, brand='Cotelco', aliases=[],
                api_key='k', model='gpt-6-luna',
                extra=extra, progress_callback=lambda p, m: None)

        self.assertTrue(modelos_usados, 'se esperaba al menos una llamada al modelo')
        self.assertTrue(all(m == 'gpt-6-luna' for m in modelos_usados),
                        'todas las llamadas deben usar gpt-6-luna, se vio: %r' % modelos_usados)
        self.assertEqual(out[0]['Tono_IA'], 'Positivo')
        self.assertEqual(out[0]['Subtema_IA'], 'Programa social comunitario')
        self.assertEqual(out[0]['Tema_IA'], '')


if __name__ == '__main__':
    unittest.main()
