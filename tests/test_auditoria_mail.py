"""Pruebas de la auditoria de uso por correo (sin red: smtplib simulado)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auditoria_mail as am


class FakeSMTP:
    enviados = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, pwd):
        self.user, self.pwd = user, pwd

    def send_message(self, msg):
        FakeSMTP.enviados.append(msg)


class TestAuditoriaMail(unittest.TestCase):
    def setUp(self):
        FakeSMTP.enviados = []
        self.env = {
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "usuario@gmail.com",
            "SMTP_PASSWORD": "abcd efgh ijkl mnop",
            "SMTP_FROM": "usuario@gmail.com",
            "USAGE_NOTIFY_EMAIL": "dueno@gmail.com",
        }

    def test_sin_config_no_envia_y_no_falla(self):
        with patch.dict(os.environ, {}, clear=True):
            # st.secrets no existe fuera de Streamlit: _leer_config lo tolera.
            self.assertFalse(am.enviar_auditoria_desde_resultado({}, {}))

    def test_construye_mensaje_con_datos_del_cliente(self):
        cfg = dict(self.env)
        result = {"total_rows": 57, "unique_rows": 41, "duplicates": 16,
                  "process_duration": "12.3s", "output_filename": "dossier_2026.xlsx",
                  "_conteo_tonos": {"Positivo": 30, "Neutro": 11}}
        ai_config = {"brand": "Universidad Simón Bolívar",
                     "aliases": ["Unisimón"], "voceros": ["José Consuegra"],
                     "criterio": "Aspectual estricto (recomendado)"}
        msg = am.construir_mensaje(result, ai_config, cfg)
        cuerpo = msg.get_content()
        self.assertIn("Universidad Simón Bolívar", cuerpo)
        self.assertIn("Unisimón", cuerpo)
        self.assertIn("57", cuerpo)
        self.assertEqual(msg["To"], "dueno@gmail.com")
        self.assertIn("Universidad Simón Bolívar", msg["Subject"])

    def test_envia_por_smtp_con_starttls(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(am.smtplib, "SMTP", FakeSMTP):
            ok = am.enviar_auditoria_desde_resultado(
                {"total_rows": 10}, {"brand": "Fenavi"})
        self.assertTrue(ok)
        self.assertEqual(len(FakeSMTP.enviados), 1)
        self.assertIn("Fenavi", FakeSMTP.enviados[0].get_content())


if __name__ == "__main__":
    unittest.main()
