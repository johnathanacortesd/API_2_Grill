# -*- coding: utf-8 -*-
"""v4.45: límite de seguridad de 1.000 filas leídas por dossier.

Pedido del usuario (2026-10-21): capa de seguridad en Grill-API / API_2_Grill
para que solo se puedan revisar hasta 1.000 filas (noticias) por corrida.
Si el XLSX trae más, antes de procesar debe salir un mensaje aclarando que
se debe dividir el archivo, porque máximo se procesarán 1.000 filas; el fin
es evitar llamadas masivas a la API key.

Implementación (pipeline.py): `process_dossier` cuenta las filas que
devuelve `load_dossier_dataframe` (que ya descarta las filas vacías) y, si
superan `MAX_FILAS_POR_DOSSIER = 1000`, lanza `LimiteFilasError` antes de
normalizar/clasificar. En app.py, `run_cleaning_process` muestra ese
mensaje directo con st.error.
"""
from __future__ import annotations

import io
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub del paquete `openai` (mismo patrón que test_v432_pkl_solo.py).
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

from openpyxl import Workbook

from pipeline import (
    MAX_FILAS_POR_DOSSIER,
    LimiteFilasError,
    _fmt_miles,
    process_dossier,
)


def _dossier_bytes(n_filas):
    wb = Workbook()
    ws = wb.active
    ws.append(["Título", "Resumen - Aclaracion", "Medio", "Fecha"])
    for _ in range(n_filas):
        ws.append(["Colpensiones paga las mesadas puntualmente",
                   "pago de mesadas pensiones dinero", "Prensa", "2026-09-01"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class LimiteFilasTests(unittest.TestCase):
    def test_constante_es_1000(self):
        self.assertEqual(MAX_FILAS_POR_DOSSIER, 1000)

    def test_fmt_miles_con_punto(self):
        self.assertEqual(_fmt_miles(1000), "1.000")
        self.assertEqual(_fmt_miles(1001), "1.001")
        self.assertEqual(_fmt_miles(53540), "53.540")

    def test_1001_filas_lanza_error_con_mensaje(self):
        with self.assertRaises(LimiteFilasError) as ctx:
            process_dossier(_dossier_bytes(1001), {}, {})
        msg = str(ctx.exception)
        self.assertIn("1.001", msg)
        self.assertIn("1.000", msg)
        self.assertIn("Divide el XLSX", msg)

    def test_1000_filas_exactas_procesa(self):
        res = process_dossier(_dossier_bytes(1000), {}, {})
        self.assertEqual(res["total_rows"], 1000)
        self.assertTrue(res["output_data"])


if __name__ == "__main__":
    unittest.main()
