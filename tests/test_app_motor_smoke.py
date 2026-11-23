# -*- coding: utf-8 -*-
"""Smoke de la app (Streamlit AppTest): selector de motor y error sin ANTHROPIC_API_KEY."""
import os
import unittest

from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')
MOTOR = 'Motor de clasificación'


def _app(con_clave_claude=False):
    at = AppTest.from_file(APP, default_timeout=60)
    at.secrets['APP_PASSWORD'] = 'prueba'
    at.secrets['OPENAI_API_KEY'] = 'sk-dummy'
    at.secrets['REGIONES_CSV_URL'] = 'http://localhost/regiones.csv'
    at.secrets['INTERNET_CSV_URL'] = 'http://localhost/internet.csv'
    if con_clave_claude:
        at.secrets['ANTHROPIC_API_KEY'] = 'sk-ant-dummy'
    at.session_state['password_correct'] = True
    return at.run()


class TestAppMotor(unittest.TestCase):
    def _radio(self, at):
        radios = [r for r in at.radio if r.label == MOTOR]
        self.assertEqual(len(radios), 1, 'falta el selector de motor')
        return radios[0]

    def test_carga_con_haiku_por_defecto(self):
        at = _app(con_clave_claude=True)
        self.assertFalse(at.exception)
        r = self._radio(at)
        self.assertEqual(r.value, 'Claude Haiku (agente + revisor)')
        self.assertIn('OpenAI Luna (gpt-6-luna)', r.options)
        self.assertFalse(any('ANTHROPIC_API_KEY' in e.value for e in at.error))

    def test_luna_opcional_sin_clave_claude(self):
        at = _app()
        self._radio(at).set_value('OpenAI Luna (gpt-6-luna)').run()
        self.assertFalse(at.exception)
        self.assertFalse(any('ANTHROPIC_API_KEY' in e.value for e in at.error))

    def test_haiku_sin_clave_muestra_error(self):
        at = _app()
        self._radio(at).set_value('Claude Haiku (agente + revisor)').run()
        self.assertFalse(at.exception)
        self.assertTrue(any('ANTHROPIC_API_KEY' in e.value for e in at.error))
        self.assertFalse(any(t.key == 'modelo_fijo' for t in at.text_input))

    def test_haiku_con_clave_sin_error(self):
        at = _app(con_clave_claude=True)
        self._radio(at).set_value('Claude Haiku (agente + revisor)').run()
        self.assertFalse(at.exception)
        self.assertFalse(any('ANTHROPIC_API_KEY' in e.value for e in at.error))
        self.assertTrue(any('Claude Platform' in c.value for c in at.caption))


if __name__ == '__main__':
    unittest.main()
