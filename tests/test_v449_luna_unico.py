"""v4.49 — solo se usa gpt-6-luna, sin opción a otros modelos.

El campo "Modelo de IA" queda fijo en gpt-6-luna y deshabilitado en los
dos paneles (perfil manual y cliente personalizado): ningún selector
ofrece modelos y el valor que fluye a la configuración es siempre luna.
"""
import os
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestLunaUnico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(RAIZ, "app.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_version_es_v449(self):
        self.assertIn('APP_VERSION = "v4.49"', self.src)

    def test_sin_otros_modelos_en_app(self):
        self.assertNotIn("gpt-6-sol", self.src)
        self.assertNotIn("gpt-4.1-nano", self.src)

    def test_modelo_fijo_en_ambos_paneles(self):
        self.assertEqual(self.src.count('value="gpt-6-luna"'), 2)
        self.assertIn('key="modelo_fijo"', self.src)
        self.assertIn('key="modelo_custom"', self.src)
        # Cada campo de modelo va deshabilitado (no editable por el usuario)
        for clave in ('key="modelo_fijo"', 'key="modelo_custom"'):
            bloque = self.src[self.src.index(clave) - 200:self.src.index(clave)]
            self.assertIn("disabled=True", bloque)


if __name__ == "__main__":
    unittest.main()
