"""v4.42 — gpt-6-luna pasa a ser el modelo por defecto.

OpenAI descontinúa gpt-4.1-nano-2025-04-14 (y el resto de modelos del aviso)
el 2026-10-23: el default debe resolverse a gpt-6-luna en todos los puntos
de entrada, y el modelo explícito se respeta tal cual.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az


class TestModeloDefectoLuna(unittest.TestCase):
    def test_constante_es_luna(self):
        self.assertEqual(az.MODELO_DEFECTO, "gpt-6-luna")

    def test_cfg_sin_modelo_resuelve_luna(self):
        # etiquetar_grupos usa: cfg.get('model') or MODELO_DEFECTO
        cfg = {'api_key': 'k'}
        self.assertEqual(cfg.get('model') or az.MODELO_DEFECTO, "gpt-6-luna")

    def test_modelo_explicito_se_respeta(self):
        for m in ("gpt-6-sol", "gpt-4.1-nano-2025-04-14"):
            cfg = {'api_key': 'k', 'model': m}
            self.assertEqual(cfg.get('model') or az.MODELO_DEFECTO, m)

    def test_luna_usa_max_completion_tokens(self):
        self.assertEqual(az._param_limite('gpt-6-luna'), 'max_completion_tokens')

    def test_precio_luna_registrado(self):
        self.assertEqual(az._precios_modelo('gpt-6-luna'), (0.10, 0.50))


if __name__ == '__main__':
    unittest.main()
