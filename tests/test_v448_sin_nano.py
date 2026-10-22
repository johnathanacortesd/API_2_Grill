"""v4.48 — gpt-4.1-nano sale del selector "Modelo de IA".

OpenAI apagó gpt-4.1-nano-2025-04-14 el 2026-10-23: ya no debe ofrecerse
en ningún panel (perfil manual ni cliente personalizado); el default del
selector es gpt-6-luna. Los precios/parámetros históricos de nano en el
analizador se conservan (corridas pasadas y su costo ya registrado).
"""
import os
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSinNanoEnSelector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(RAIZ, "app.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_version_es_v448(self):
        self.assertIn('APP_VERSION = "v4.48"', self.src)

    def test_nano_no_aparece_en_app(self):
        self.assertNotIn("gpt-4.1-nano", self.src)

    def test_ambos_selectores_solo_luna_y_sol(self):
        self.assertEqual(self.src.count('options=["gpt-6-luna", "gpt-6-sol"]'), 2)


if __name__ == "__main__":
    unittest.main()
