"""v4.65 (decisión del usuario 2026-10-29): Contexto analizado y texto
nunca traen la noticia entera: se recortan al tercer punto de oración.
Y el crédito de foto SÍ cuenta como mención: la marca digitada para el
cliente debe encontrarse aunque vaya pegada a «Foto:».

Caso real que lo motivó: Comfenalco 61636572 (secuestro en Guacarí).
En v4.64 el crédito se descartaba, el extracto quedaba vacío y el
contexto se iba a la ventana de lead (1492 caracteres, sin la marca).
En v4.65 el extracto ES el crédito: una oración, marca encontrada,
texto corto.
"""
import unittest

import analyzer_tono_tema as A


class TestCorteTercerPunto(unittest.TestCase):
    def test_cinco_oraciones_quedan_tres(self):
        txt = ("Primera oración. Segunda oración. Tercera oración. "
               "Cuarta oración. Quinta oración.")
        self.assertEqual(
            A._cortar_tercer_punto(txt),
            "Primera oración. Segunda oración. Tercera oración.")

    def test_abreviaturas_no_cuentan_como_punto(self):
        txt = ("El hecho ocurrió a las 5:12 a. m. de este martes. "
               "La víctima abrió el local. Llegó la policía. "
               "Cuarta oración que debe quedar fuera.")
        self.assertEqual(
            A._cortar_tercer_punto(txt),
            "El hecho ocurrió a las 5:12 a. m. de este martes. "
            "La víctima abrió el local. Llegó la policía.")

    def test_doctor_y_decimales_no_cuentan(self):
        txt = ("El Dr. Pérez anunció 12.500 vacantes. Segunda oración. "
               "Tercera oración. Cuarta fuera.")
        self.assertEqual(
            A._cortar_tercer_punto(txt),
            "El Dr. Pérez anunció 12.500 vacantes. Segunda oración. "
            "Tercera oración.")

    def test_signos_interrogacion_y_exclamacion_cuentan(self):
        txt = "¿Qué pasó? Nadie lo sabe. Tercera oración. Cuarta fuera."
        self.assertEqual(
            A._cortar_tercer_punto(txt),
            "¿Qué pasó? Nadie lo sabe. Tercera oración.")

    def test_menos_de_tres_oraciones_no_se_recorta(self):
        self.assertEqual(A._cortar_tercer_punto("Solo una."),
                         "Solo una.")
        self.assertEqual(A._cortar_tercer_punto(""), "")


class TestCreditoCuentaYTextoCorto(unittest.TestCase):
    def test_credito_pegado_con_alias_confenalco(self):
        cuerpo = ("Ocurrió un hecho en Cali. Foto:Confenalco Valle. "
                  "Más texto sin mención.")
        ctx = A._contexto_exacto_marca(cuerpo, "T", "Comfenalco",
                                       ["Confenalco"])
        self.assertIn("Foto:Confenalco", ctx)

    def test_extracto_multimencion_se_recorta_a_tres_oraciones(self):
        cuerpo = ("Cotelco anunció la feria. Cotelco anunció el foro. "
                  "Cotelco anunció la jornada. Cotelco anunció el congreso. "
                  "Cotelco anunció la gala.")
        ctx = A._contexto_exacto_marca(cuerpo, "T", "Cotelco", [])
        final = A._contexto_minimo_util(ctx, "T", cuerpo)
        self.assertEqual(final, "Cotelco anunció la feria. "
                                "Cotelco anunció el foro. "
                                "Cotelco anunció la jornada.")

    def test_sin_mencion_lead_recortado_a_tres_oraciones(self):
        cuerpo = ("Primera del lead. Segunda del lead. Tercera del lead. "
                  "Cuarta del lead. Quinta del lead. Sexta del lead.")
        final = A._contexto_minimo_util("", "Titular", cuerpo)
        self.assertEqual(final, "Primera del lead. Segunda del lead. "
                                "Tercera del lead.")

    def test_sin_cuerpo_titulo_como_ultimo_recurso(self):
        self.assertEqual(A._contexto_minimo_util("", "Titular solo", ""),
                         "Titular solo")


class TestCasoReal61636572(unittest.TestCase):
    def test_fila_reportada_extracto_es_el_credito(self):
        import openpyxl
        wb = openpyxl.load_workbook(
            "/home/hatch/workspace/user/files/"
            "Dossier_comfenalco_20261029_2202.xlsx",
            read_only=True, data_only=True)
        ws = wb['Resultado']
        rows = list(ws.iter_rows(values_only=True))
        hdr = list(rows[0])
        data = {str(r[0]): dict(zip(hdr, r)) for r in rows[1:]}
        r = data['61636572']
        cuerpo = str(r['Resumen - Aclaracion'])
        tit = str(r['Título'])
        ctx = A._contexto_exacto_marca(
            cuerpo, tit, "Comfenalco", ["Confenalco", "Comfenalco Valle"])
        self.assertIn("Foto:Comfenalco", ctx)
        final = A._contexto_minimo_util(ctx, tit, cuerpo)
        self.assertEqual(final, "Guacarí Foto:Comfenalco Valle del Cauca.")
        texto = " ".join(final.replace(tit, "").split())
        self.assertEqual(texto, final)
        self.assertLess(len(texto), 100)  # antes: 4633 (v4.62/63), 1374 (v4.64)


if __name__ == '__main__':
    unittest.main()
