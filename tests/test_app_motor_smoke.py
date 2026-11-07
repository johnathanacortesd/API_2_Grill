# -*- coding: utf-8 -*-
"""Smoke de Clausifier (Streamlit AppTest): título, crédito, motor Haiku por
defecto, PKL visibles en modo Haiku y perfiles de Secretaría/Defensoría."""
import os
import unittest

from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')
MOTOR = 'Motor de clasificación'
CREDITO = 'Clausifier · Creado por Johnathan Cortés y Claude'


def _app(con_clave_claude=False):
    at = AppTest.from_file(APP, default_timeout=60)
    at.secrets['APP_PASSWORD'] = 'prueba'
    at.secrets['OPENAI_API_KEY'] = 'sk-dummy'
    at.secrets['TYPESAFE_API_KEY'] = 'ts-dummy'
    at.secrets['REGIONES_CSV_URL'] = 'http://localhost/regiones.csv'
    at.secrets['INTERNET_CSV_URL'] = 'http://localhost/internet.csv'
    if con_clave_claude:
        at.secrets['ANTHROPIC_API_KEY'] = 'sk-ant-dummy'
    at.session_state['password_correct'] = True
    return at.run()


def _markdown(at):
    return '\n'.join(m.value for m in at.markdown)


class TestAppClausifier(unittest.TestCase):
    def _radio(self, at):
        radios = [r for r in at.radio if r.label == MOTOR]
        self.assertEqual(len(radios), 1, 'falta el selector de motor')
        return radios[0]

    def test_titulo_y_credito(self):
        at = _app(con_clave_claude=True)
        self.assertFalse(at.exception)
        md = _markdown(at)
        self.assertIn('Clausifier</div>', md)
        self.assertIn(CREDITO, md)
        import app
        self.assertIn('page_title="Clausifier"', open(app.__file__, encoding='utf-8').read())

    def test_haiku_por_defecto(self):
        at = _app(con_clave_claude=True)
        r = self._radio(at)
        self.assertEqual(r.value, 'Claude Haiku (agente + revisor)')
        self.assertIn('OpenAI Luna (gpt-6-luna)', r.options)
        self.assertFalse(any('ANTHROPIC_API_KEY' in e.value for e in at.error))
        self.assertTrue(any('Claude Platform' in c.value for c in at.caption))

    def test_pkl_visibles_en_haiku(self):
        at = _app(con_clave_claude=True)
        self.assertEqual(self._radio(at).value, 'Claude Haiku (agente + revisor)')
        # AppTest no expone file_uploader como elemento tipado: se busca por key.
        claves = set()

        def recorrer(nodo):
            for hijo in getattr(nodo, 'children', {}).values():
                k = getattr(hijo, 'key', None) or getattr(getattr(hijo, 'proto', None), 'id', '')
                claves.add(str(k))
                recorrer(hijo)
        recorrer(at._tree)
        texto = ' '.join(claves)
        for k in ('pkl_tono', 'pkl_tema', 'pkl_tono_custom', 'pkl_tema_custom'):
            self.assertIn(k, texto)

    def test_perfiles_secretaria_y_defensoria(self):
        at = _app(con_clave_claude=True)
        sel = [s for s in at.selectbox if s.label == 'Perfil de cliente'][0]
        nombres = ' | '.join(sel.options)
        self.assertIn('Defensor', nombres)
        self.assertIn('Secretar', nombres)

    def test_luna_opcional_sin_clave_claude(self):
        at = _app()
        self._radio(at).set_value('OpenAI Luna (gpt-6-luna)').run()
        self.assertFalse(at.exception)
        self.assertFalse(any('ANTHROPIC_API_KEY' in e.value for e in at.error))
        self.assertTrue(any(t.key == 'modelo_fijo' for t in at.text_input))

    def test_haiku_sin_clave_muestra_error(self):
        at = _app()
        self.assertFalse(at.exception)
        self.assertTrue(any('ANTHROPIC_API_KEY' in e.value for e in at.error))
        self.assertFalse(any(t.key == 'modelo_fijo' for t in at.text_input))


if __name__ == '__main__':
    unittest.main()
