"""Pruebas v4.11: compatibilidad de parametros con modelos nuevos.

Regresion del fallo visto con gpt-6-luna el 2026-09-22: la API devuelve
HTTP 400 "Unsupported parameter: 'max_tokens' is not supported with this
model. Use 'max_completion_tokens' instead." Cada llamada fallida hacia que
el grupo cayera al fallback (tono Neutro + subtema de palabras del titulo),
lo que explicaba a la vez la demora (reintentos) y la calidad destruida.
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


class _Resp:
    def __init__(self, status, text='', data=None):
        self.status_code = status
        self.text = text
        self._data = data

    def json(self):
        return self._data


_OK = {'choices': [{'message': {'content': '{"resultados": []}'}}]}
_ERR_LUNA = ("{ \"error\": { \"message\": \"Unsupported parameter: 'max_tokens' "
            "is not supported with this model. Use 'max_completion_tokens' "
            "instead.\", \"type\": \"invalid_request_error\", \"param\": \"max_tokens\" } }")


class TestParamLimite(unittest.TestCase):
    def test_luna_usa_max_completion_tokens(self):
        self.assertEqual(az._param_limite('gpt-6-luna'), 'max_completion_tokens')

    def test_sol_usa_max_completion_tokens(self):
        self.assertEqual(az._param_limite('gpt-6-sol'), 'max_completion_tokens')

    def test_nano_sigue_con_max_tokens(self):
        self.assertEqual(az._param_limite('gpt-4.1-nano-2025-04-14'), 'max_tokens')


class TestLlamarLlmAutocorreccion(unittest.TestCase):
    def _cfg(self, model):
        return {'api_key': 'k', 'model': model, 'base_url': 'https://x.test/v1'}

    def test_payload_luna_no_lleva_max_tokens(self):
        vistos = []

        def fake_post(url, headers=None, json=None, timeout=None):
            vistos.append(json)
            return _Resp(200, data=_OK)

        with patch.object(az, '_http_post', side_effect=fake_post):
            az.llamar_llm(self._cfg('gpt-6-luna'), [{'role': 'user', 'content': 'hola'}],
                          json_mode=False)
        self.assertEqual(len(vistos), 1)
        self.assertIn('max_completion_tokens', vistos[0])
        self.assertNotIn('max_tokens', vistos[0])
        self.assertEqual(vistos[0]['model'], 'gpt-6-luna')

    def test_swap_reactivo_ante_400_de_modelo_desconocido(self):
        vistos = []
        llamadas = {'n': 0}

        def fake_post(url, headers=None, json=None, timeout=None):
            llamadas['n'] += 1
            vistos.append(dict(json))
            if llamadas['n'] == 1:
                return _Resp(400, text=_ERR_LUNA)
            return _Resp(200, data=_OK)

        with patch.object(az, '_http_post', side_effect=fake_post):
            out = az.llamar_llm(self._cfg('modelo-futuro-xyz'),
                                [{'role': 'user', 'content': 'hola'}], json_mode=False)
        self.assertEqual(llamadas['n'], 2)
        self.assertIn('max_tokens', vistos[0])
        self.assertIn('max_completion_tokens', vistos[1])
        self.assertNotIn('max_tokens', vistos[1])
        self.assertIn('resultados', out)

    def test_temperature_se_retira_si_el_modelo_la_rechaza(self):
        vistos = []
        llamadas = {'n': 0}

        def fake_post(url, headers=None, json=None, timeout=None):
            llamadas['n'] += 1
            vistos.append(dict(json))
            if llamadas['n'] == 1:
                return _Resp(400, text="{ \"error\": { \"message\": \"Unsupported parameter: "
                                      "'temperature' is not supported with this model.\" } }")
            return _Resp(200, data=_OK)

        with patch.object(az, '_http_post', side_effect=fake_post):
            az.llamar_llm(self._cfg('gpt-6-luna'), [{'role': 'user', 'content': 'hola'}],
                          json_mode=False)
        self.assertEqual(llamadas['n'], 2)
        self.assertIn('temperature', vistos[0])
        self.assertNotIn('temperature', vistos[1])


class TestSesionReutilizada(unittest.TestCase):
    def test_una_sola_sesion_para_varias_llamadas(self):
        import analyzer_tono_tema as az2
        az2._SESION_HTTP = None
        llamadas = {'post': 0, 'session': 0}

        class _FakeSession:
            def post(self, url, headers=None, json=None, timeout=None):
                llamadas['post'] += 1
                return _Resp(200, data=_OK)

        def fake_session():
            llamadas['session'] += 1
            return _FakeSession()

        with patch.object(az.requests, 'Session', side_effect=fake_session):
            az._http_post('https://x.test/v1', {}, {}, 10)
            az._http_post('https://x.test/v1', {}, {}, 10)
        self.assertEqual(llamadas['session'], 1)
        self.assertEqual(llamadas['post'], 2)
        az2._SESION_HTTP = None


if __name__ == '__main__':
    unittest.main()
