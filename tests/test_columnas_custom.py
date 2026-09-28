"""v4.27: pestaña "Columnas personalizadas" — lectura, sugerencia y renombre
de las columnas de título y cuerpo (CuerpoEs) sin tocar el pipeline."""
import io
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub del paquete `openai`: no instalable en este entorno (mismo patrón que
# test_sin_tema.py). Ningún test de este archivo hace llamadas reales a la API.
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

from pipeline import leer_columnas_xlsx, renombrar_columnas_xlsx, sugerir_columna


def _xlsx_bytes(headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    bio = io.BytesIO()
    wb.save(bio)
    wb.close()
    return bio.getvalue()


def _headers_of(blob):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    try:
        return [c.value for c in next(wb.active.iter_rows(min_row=1, max_row=1))]
    finally:
        wb.close()


def _row2_of(blob):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    try:
        return [c.value for c in next(wb.active.iter_rows(min_row=2, max_row=2))]
    finally:
        wb.close()


class TestColumnasCustom(unittest.TestCase):
    def test_lee_columnas(self):
        blob = _xlsx_bytes(["Headline", "Body", "Medio"], [["t1", "b1", "m1"]])
        cols = leer_columnas_xlsx(blob)
        self.assertEqual([lbl for lbl, _ in cols], ["Headline", "Body", "Medio"])
        self.assertEqual([idx for _, idx in cols], [1, 2, 3])

    def test_duplicados_reciben_etiqueta_unica(self):
        blob = _xlsx_bytes(["A", "A", "B"], [["1", "2", "3"]])
        cols = leer_columnas_xlsx(blob)
        self.assertEqual([lbl for lbl, _ in cols], ["A", "A (2)", "B"])

    def test_renombra_y_preserva_datos(self):
        blob = _xlsx_bytes(["Headline", "Body", "Medio"], [["t1", "b1", "m1"]])
        out = renombrar_columnas_xlsx(blob, "Headline", "Body")
        self.assertEqual(_headers_of(out), ["Título", "Resumen - Aclaracion", "Medio"])
        self.assertEqual(_row2_of(out), ["t1", "b1", "m1"])

    def test_aparta_columna_que_ya_tenia_el_nombre(self):
        blob = _xlsx_bytes(["Título", "Headline", "Body"], [["x", "t1", "b1"]])
        out = renombrar_columnas_xlsx(blob, "Headline", "Body")
        self.assertEqual(_headers_of(out),
                         ["Título (original)", "Título", "Resumen - Aclaracion"])
        self.assertEqual(_row2_of(out), ["x", "t1", "b1"])

    def test_misma_columna_da_error(self):
        blob = _xlsx_bytes(["A", "B"], [["1", "2"]])
        with self.assertRaises(ValueError):
            renombrar_columnas_xlsx(blob, "A", "A")

    def test_columna_inexistente_da_error(self):
        blob = _xlsx_bytes(["A", "B"], [["1", "2"]])
        with self.assertRaises(ValueError):
            renombrar_columnas_xlsx(blob, "A", "Z")

    def test_sugerir_columna(self):
        self.assertEqual(
            sugerir_columna(["Headline", "Resumen"], ("titulo", "headline")), "Headline")
        self.assertEqual(
            sugerir_columna(["Headline", "Resumen"], ("resumen - aclaracion", "cuerpoes")),
            "Resumen")
        self.assertIsNone(sugerir_columna(["A", "B"], ("titulo",)))

    def test_renombrado_pasa_por_el_loader_del_pipeline(self):
        # El xlsx renombrado debe ser legible por load_dossier_dataframe con
        # las columnas que el pipeline espera.
        from pipeline import load_dossier_dataframe
        blob = _xlsx_bytes(["Headline", "Body", "Medio"],
                           [["El título", "El cuerpo", "El Medio"]])
        out = renombrar_columnas_xlsx(blob, "Headline", "Body")
        df = load_dossier_dataframe(out)
        self.assertIn("Título", df.columns)
        self.assertIn("Resumen - Aclaracion", df.columns)
        self.assertEqual(df["Título"].iloc[0], "El título")
        self.assertEqual(df["Resumen - Aclaracion"].iloc[0], "El cuerpo")


if __name__ == "__main__":
    unittest.main()
