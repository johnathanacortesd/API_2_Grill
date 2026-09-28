"""v4.34 — El subtema jamás es el titular idéntico ni lleva comillas.

- `validar`: identidad total normalizada subtema==título SIEMPRE marca
  'copia_titular', aunque el titular "parezca etiqueta" (v4.21 solo cubría
  la copia parcial; la identidad pasaba limpia).
- `reparar_subtema_determinista`: la identidad es mecánica (título largo →
  rótulo honesto más corto; título nominal corto → '' para que la vuelta LLM
  reformule); la cita parcial se descomilla conservando las palabras.
- `construir_ai_config_custom`: paridad con la pestaña estándar (criterio
  personalizado, taxonomía, cubos, votos, lote, workers, umbrales, modelo).
"""
import sys
import types
import unittest

# Stub del paquete `openai` (mismo patrón que test_v432_pkl_solo.py): pipeline
# lo importa vía ai_analyzer y ningún test de este archivo hace llamadas reales.
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as A
from pipeline import construir_ai_config_custom


class TestIdentidadTitular(unittest.TestCase):
    def test_identidad_total_se_marca_aunque_parezca_etiqueta(self):
        t = "Gobierno avanza en ruta pensional"
        pr = A.validar(t, "Neutro", [t, "cuerpo de la noticia"])
        self.assertIn("copia_titular", pr)

    def test_identidad_normalizada_puntuacion_y_mayusculas(self):
        t = "Gobierno avanza en ruta pensional."
        s = "gobierno avanza en ruta pensional"
        pr = A.validar(s, "Neutro", [t, "cuerpo"])
        self.assertIn("copia_titular", pr)

    def test_solapamiento_parcial_valido_no_se_marca(self):
        # v4.21 se preserva: etiqueta válida que coincide con PARTE del
        # titular no es copia perezosa.
        s = "Estado de salud de Yamid Amat"
        t = "Actualización sobre el estado de salud de Yamid Amat"
        pr = A.validar(s, "Neutro", [t, "cuerpo"])
        self.assertNotIn("copia_titular", pr)

    def test_reparacion_identidad_titulo_largo(self):
        t = "Esto es lo que cambia con la reforma pensional: Casa Blu del 26 de septiembre"
        nuevo = A.reparar_subtema_determinista(t, t)
        self.assertTrue(nuevo)
        self.assertNotEqual(A.nz(nuevo), A.nz(t))
        self.assertNotIn("copia_titular", A.validar(nuevo, "Neutro", [t, "cuerpo"]))

    def test_reparacion_identidad_titulo_corto_nominal_va_a_llm(self):
        # Sin reformulación honesta posible: '' → la vuelta LLM reformula.
        t = "Gobierno avanza en ruta pensional"
        self.assertEqual(A.reparar_subtema_determinista(t, t), "")


class TestComillas(unittest.TestCase):
    def test_cita_parcial_se_descomilla_conservando_palabras(self):
        s = 'Anuncio de "paz total" en pensiones'
        t = "Ministra anuncia paz total en pensiones"
        nuevo = A.reparar_subtema_determinista(s, t)
        self.assertTrue(nuevo)
        self.assertNotRegex(nuevo, r'["\'«»“”‘’]')
        self.assertIn("paz total", nuevo)

    def test_cita_total_no_deja_comillas(self):
        s = '"Septiembre era el momento perfecto"'
        t = "Nacimiento de Gael"
        nuevo = A.reparar_subtema_determinista(s, t)
        # '' (vuelta LLM) o rótulo derivado: nunca con comillas.
        self.assertNotRegex(nuevo, r'["\'«»“”‘’]')


class TestConfigCustomParidad(unittest.TestCase):
    def _base(self, **kw):
        base = dict(brand="Colpensiones", alias_txt="", voceros_txt="",
                    criterio="Aspectual estricto", incluir_tema=False,
                    incluir_prominencia=False, enable_ai=True,
                    api_key="k", typesafe_api_key=None, historial_dir=None,
                    tone_pkl_bytes=None, theme_pkl_bytes=None)
        base.update(kw)
        return construir_ai_config_custom(**base)

    def test_parametros_nuevos_pasan_al_config(self):
        tax = {"temas": ["Trámites y Servicios"], "reglas": []}
        cfg = self._base(criterio_texto="Mi regla", taxonomia=tax,
                         cubos_objetivo=20, votos=3, tam_lote=12, workers=4,
                         umbral_titulo=80, umbral_cuerpo=75,
                         model="gpt-6-luna")
        self.assertEqual(cfg["criterio_texto"], "Mi regla")
        self.assertEqual(cfg["taxonomia"], tax)
        self.assertEqual(cfg["cubos_objetivo"], 20)
        self.assertEqual(cfg["votos"], 3)
        self.assertEqual(cfg["tam_lote"], 12)
        self.assertEqual(cfg["workers"], 4)
        self.assertEqual(cfg["umbral_titulo"], 80)
        self.assertEqual(cfg["umbral_cuerpo"], 75)
        self.assertEqual(cfg["model"], "gpt-6-luna")
        self.assertTrue(cfg["preservar_columnas"])

    def test_defaults_igual_que_antes(self):
        cfg = self._base()
        self.assertEqual(cfg["criterio_texto"], "")
        self.assertEqual(cfg["taxonomia"], "Automática según el archivo (recomendada)")
        self.assertEqual(cfg["cubos_objetivo"], 16)
        self.assertEqual(cfg["votos"], 2)
        self.assertEqual(cfg["tam_lote"], 10)
        self.assertEqual(cfg["workers"], 8)
        self.assertEqual(cfg["umbral_titulo"], 92)
        self.assertEqual(cfg["umbral_cuerpo"], 85)
        self.assertEqual(cfg["model"], "gpt-4.1-nano-2025-04-14")


if __name__ == "__main__":
    unittest.main()
