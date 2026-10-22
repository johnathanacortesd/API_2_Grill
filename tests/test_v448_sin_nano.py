"""v4.48 — gpt-4.1-nano sale del selector "Modelo de IA" (en v4.49 el selector desaparece).

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

    def test_nano_no_aparece_en_app(self):
        self.assertNotIn("gpt-4.1-nano", self.src)

    def test_ningun_selector_ofrece_modelos(self):
        # v4.49: ya no hay selector de modelo; ningún options de modelos queda.
        self.assertNotIn('options=["gpt-6-luna"', self.src)


if __name__ == "__main__":
    unittest.main()
