"""Fix anti-copia 2026-10-28 (sin push).

Caso real del usuario:
  título:  "Todas las empresas tienden a morir - CAMBIO COLOMBIA"
  subtema: "Empresas tienden a morir - CAMBIO COLOMBIA"
El subtema jamás debe ser el título ni su recorte casi total, ni llevar
el nombre del medio. Y Contexto analizado no debe dejarse llevar por el
título: es solo cuerpo, el título ya va aparte.

Se verifica:
- validar() marca copia_titular en el caso real, aunque la etiqueta sea
  "válida por sí misma" (la excepción de v4.21 no aplica a casi-identidad).
- Una frase nominal PARCIAL legítima ("Estado de salud de Yamid Amat"
  en un titular más largo) NO se marca: no se rompe lo que v4.21 protegía.
- `_contexto_exacto_marca` no antepone el título ni lo devuelve cuando
  no hay mención en el cuerpo; `_contexto_minimo_util` completa con el
  cuerpo, no con el título, cuando hay cuerpo suficiente.
- La puerta final `reparar_copias_finales` quita el sufijo del medio y
  reformula la copia con el modelo (mock), sin tocar el tono; si el modelo
  falla, el grupo no exporta la copia.
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

TIT = "Todas las empresas tienden a morir - CAMBIO COLOMBIA"
SUB = "Empresas tienden a morir - CAMBIO COLOMBIA"
CUERPO = ("Cuando era estudiante, un profesor me dijo que todas las "
          "empresas tienden a morir. No le creí. Hoy repito su frase "
          "como propia. Creo que Peter Drucker dejó una receta para "
          "contrarrestar esa tendencia: revisarse cada tres años, sobre "
          "todo cuando las cosas van bien.")


class TestCasiIdentidad(unittest.TestCase):
    def test_caso_real_es_copia(self):
        self.assertTrue(az._es_copia_casi_identica(SUB, TIT))
        self.assertIn('copia_titular',
                      az.validar(SUB, 'Neutro', [TIT]))

    def test_parcial_legitimo_no_es_copia(self):
        self.assertFalse(az._es_copia_casi_identica(
            "Estado de salud de Yamid Amat",
            "Actualización sobre el estado de salud de Yamid Amat en clínica"))
        self.assertEqual(
            az.validar("Estado de salud de Yamid Amat", 'Neutro',
                       ["Actualización sobre el estado de salud de Yamid Amat en clínica"]),
            [])

    def test_parafrasis_no_es_copia(self):
        self.assertFalse(az._es_copia_casi_identica(
            "Mortalidad de las empresas", TIT))
        self.assertEqual(az.validar("Mortalidad de las empresas", 'Neutro', [TIT]),
                         [])

    def test_quitar_sufijo_medio(self):
        self.assertEqual(az._quitar_sufijo_medio(SUB, ["Cambio Colombia"]),
                         "Empresas tienden a morir")
        # Un nombre con guion que NO es medio no se corta.
        self.assertEqual(
            az._quitar_sufijo_medio("Variante Sampués - Segovia",
                                    ["Cambio Colombia"]),
            "Variante Sampués - Segovia")


class TestContextoSinTitulo(unittest.TestCase):
    def test_contexto_exacto_no_antepone_titulo(self):
        ctx = az._contexto_exacto_marca(
            "Cotelco presentó sus propuestas. Otro párrafo sin mención.",
            "Cotelco anuncia propuestas", "Cotelco", [])
        self.assertNotIn("Cotelco anuncia propuestas", ctx)
        self.assertIn("Cotelco presentó sus propuestas", ctx)

    def test_contexto_exacto_sin_mencion_es_vacio(self):
        self.assertEqual(
            az._contexto_exacto_marca("Texto sin la marca.", "Mi titular",
                                      "Cotelco", []),
            "")

    def test_contexto_minimo_completa_con_cuerpo_no_con_titulo(self):
        ctx = az._contexto_minimo_util("Cotelco.", TIT, CUERPO)
        self.assertTrue(ctx.startswith("Cuando era estudiante"))
        self.assertFalse(ctx.startswith(TIT))

    def test_contexto_minimo_sin_cuerpo_ultimo_recurso_titulo(self):
        ctx = az._contexto_minimo_util("Cotelco.", TIT, "")
        self.assertIn("empresas tienden a morir", ctx)


class TestPuertaFinal(unittest.TestCase):
    def _grupo_y_rows(self):
        grupos = [{'grupo': 1, 'titulo': TIT, 'texto': CUERPO,
                   'idxs': [0]}]
        rows = [{'Título': TIT, 'CuerpoEs': CUERPO, 'Medio': 'Cambio Colombia'}]
        etiquetas = {1: {'sub_tema': SUB, 'tono': 'Neutro'}}
        return grupos, rows, etiquetas

    def test_reformula_copia_con_modelo_y_no_toca_tono(self):
        grupos, rows, etiquetas = self._grupo_y_rows()
        cfg = {'brand': 'X', 'aliases': [], 'voceros': [],
               'criterio': 'Aspectual estricto', 'api_key': 'k',
               'model': 'gpt-6-luna'}
        payload = ('{"resultados":[{"id":1,"sub_tema":"Mortalidad de las empresas",'
                   '"tono":"Positivo"}]}')
        with patch.object(az, 'llamar_llm', return_value=payload):
            ajustes = az.reparar_copias_finales(
                cfg, grupos, etiquetas, rows, {0: 1}, {}, ajustes_subtema={})
        self.assertEqual(etiquetas[1]['sub_tema'], "Mortalidad de las empresas")
        self.assertEqual(etiquetas[1]['tono'], 'Neutro')
        self.assertEqual(ajustes, {})

    def test_sin_modelo_no_exporta_copia(self):
        grupos, rows, etiquetas = self._grupo_y_rows()
        cfg = {'brand': 'X', 'aliases': [], 'voceros': [],
               'criterio': 'Aspectual estricto', 'api_key': '',
               'model': 'gpt-6-luna'}
        ajustes = az.reparar_copias_finales(
            cfg, grupos, etiquetas, rows, {0: 1}, {}, ajustes_subtema={})
        self.assertNotEqual(etiquetas[1]['sub_tema'], SUB)
        self.assertNotIn('CAMBIO', etiquetas[1]['sub_tema'])

    def test_ajuste_por_fila_copia_se_reformula(self):
        grupos, rows, etiquetas = self._grupo_y_rows()
        etiquetas[1] = {'sub_tema': 'Mortalidad de las empresas', 'tono': 'Neutro'}
        cfg = {'brand': 'X', 'aliases': [], 'voceros': [],
               'criterio': 'Aspectual estricto', 'api_key': 'k',
               'model': 'gpt-6-luna'}
        payload = ('{"resultados":[{"id":900000,"sub_tema":"Mortalidad de las empresas",'
                   '"tono":"Neutro"}]}')
        with patch.object(az, 'llamar_llm', return_value=payload):
            ajustes = az.reparar_copias_finales(
                cfg, grupos, etiquetas, rows, {0: 1}, {},
                ajustes_subtema={0: SUB})
        self.assertEqual(ajustes[0], "Mortalidad de las empresas")


if __name__ == '__main__':
    unittest.main()
