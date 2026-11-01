"""v4.69: entidades HTML en texto, Contexto analizado y Resumen - Aclaracion.

Caso del usuario: un cuerpo con entidades ("podr&#xE1;", "c&#xF3;digo")
las arrastraba a Contexto (que se construye del CuerpoEs crudo, no del
resumen limpio) y, desde ahí, a texto, que se deriva de Contexto. La
corrección es solo decodificar (sin cambiar ninguna palabra, sin API)
en el escritor, antes de derivar texto para que texto también salga
limpio. El Resumen ya se decodificaba por su camino; aquí se garantiza
también por su encabezado, en cualquier pestaña.
"""
import io
import unittest

from openpyxl import load_workbook

from pipeline import KEY_MAP, generate_output_excel


TITULO = "El servicio podrá calificarse como un indicador para futuros viajeros"
# Contexto = título + cuerpo (el cuerpo NO repite el título: la
# derivación de texto quita TODAS las apariciones del título, igual que
# la fórmula SUSTITUIR de v4.55).
CONTEXTO_CRUDO = (
    TITULO + " En el mismo c&#xF3;digo QR hay descuentos para el viajero. "
    "La iniciativa podr&#xE1; beneficiar a miles de turistas."
)
CONTEXTO_LIMPIO = (
    TITULO + " En el mismo código QR hay descuentos para el viajero. "
    "La iniciativa podrá beneficiar a miles de turistas."
)
TEXTO_LIMPIO = (
    "En el mismo código QR hay descuentos para el viajero. "
    "La iniciativa podrá beneficiar a miles de turistas."
)
RESUMEN_CRUDO = ("En el mismo c&#xF3;digo QR hay descuentos. "
                 "El servicio podr&#xE1; calificarse.")
RESUMEN_LIMPIO = ("En el mismo código QR hay descuentos. "
                  "El servicio podrá calificarse.")


def _leer(data, columnas):
    out = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    filas = list(out.active.iter_rows(values_only=True))
    out.close()
    header = list(filas[0])
    return {h: filas[1][header.index(h)] for h in columnas}


class TestEntidadesColumnas(unittest.TestCase):
    def test_contexto_texto_y_resumen_salen_decodificados(self):
        rows = [{
            "Título": TITULO,
            "Resumen - Aclaracion": RESUMEN_CRUDO,
            "Contexto analizado": CONTEXTO_CRUDO,
        }]
        data = generate_output_excel(
            rows, KEY_MAP,
            columns_to_use=["Título", "Resumen - Aclaracion",
                            "Contexto analizado", "texto"],
        )
        vals = _leer(data, ["Resumen - Aclaracion", "Contexto analizado",
                            "texto"])
        self.assertEqual(vals["Contexto analizado"], CONTEXTO_LIMPIO)
        self.assertEqual(vals["texto"], TEXTO_LIMPIO)
        self.assertEqual(vals["Resumen - Aclaracion"], RESUMEN_LIMPIO)
        for v in vals.values():
            self.assertNotIn("&#x", v)

    def test_sin_entidades_no_cambia_nada(self):
        rows = [{
            "Título": "Feria Laboral de Cali",
            "Resumen - Aclaracion": "La feria reunió cuarenta empresas.",
            "Contexto analizado": "Feria Laboral de Cali La feria reunió cuarenta empresas.",
        }]
        data = generate_output_excel(
            rows, KEY_MAP,
            columns_to_use=["Título", "Resumen - Aclaracion",
                            "Contexto analizado", "texto"],
        )
        vals = _leer(data, ["Resumen - Aclaracion", "Contexto analizado",
                            "texto"])
        self.assertEqual(vals["Resumen - Aclaracion"],
                         "La feria reunió cuarenta empresas.")
        self.assertEqual(vals["Contexto analizado"],
                         "Feria Laboral de Cali La feria reunió cuarenta empresas.")
        self.assertEqual(vals["texto"], "La feria reunió cuarenta empresas.")

    def test_doble_codificado_y_ampersand(self):
        rows = [{
            "Título": "Nota",
            "Resumen - Aclaracion": "Creci&amp;#xF3; y tom&amp; siguió.",
            "Contexto analizado": "Nota Creci&amp;#xF3; y tom&amp; siguió.",
        }]
        data = generate_output_excel(
            rows, KEY_MAP,
            columns_to_use=["Título", "Resumen - Aclaracion",
                            "Contexto analizado", "texto"],
        )
        vals = _leer(data, ["Resumen - Aclaracion", "Contexto analizado",
                            "texto"])
        self.assertEqual(vals["Contexto analizado"],
                         "Nota Creció y tom& siguió.")
        self.assertEqual(vals["texto"], "Creció y tom& siguió.")


if __name__ == '__main__':
    unittest.main()
