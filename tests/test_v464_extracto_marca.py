"""v4.64/v4.65: el extracto de la marca no debe ser el artículo entero.

Caso real Comfenalco 2026-10-29, nota 61636572 (secuestro en Guacarí):
la única «mención» de Comfenalco en todo el cuerpo era el crédito de foto
«Guacarí Foto:Comfenalco Valle del Cauca.». El extracto exacto quedaba en
esa frase (40 caracteres) y _contexto_minimo_util lo expandía al cuerpo
completo (6000): Contexto analizado 4751 caracteres y columna texto 4633.
"""
import unittest

import analyzer_tono_tema as A

# Cuerpo que imita la estructura real: lead + párrafos de la noticia +
# crédito de foto a mitad + más párrafos, en total más de 4000 caracteres.
PARRAFO_NEUTRO = ("Las autoridades investigan las circunstancias del hecho "
                  "y anunciaron operativos conjuntos en el municipio para "
                  "garantizar la seguridad de la comunidad. ")
# El crédito va DESPUÉS de 1500 caracteres, como en la fila real (posición
# ~2300): la ventana de lead no debe alcanzarlo, y el exacto no debe
# contar el crédito aunque esté en el cuerpo.
CUERPO_REPORTE = (
    "Hombres armados secuestraron a una joven de 19 años en una panadería "
    "del casco urbano de Guacarí, Valle del Cauca. "
    "El hecho ocurrió aproximadamente a las 5:12 a. m., cuando la víctima "
    "se disponía a abrir el local. "
    + PARRAFO_NEUTRO * 10
    + "Guacarí Foto:Comfenalco Valle del Cauca. "
    + PARRAFO_NEUTRO * 20)
TIT_REPORTE = ("Video | Secuestran a una joven de 19 años en una panadería "
               "de Guacarí, Valle del Cauca: estos serían los responsables")


class TestCreditoFotoNoEsMencion(unittest.TestCase):
    # v4.65 REVOCA v4.64 (decisión del usuario 2026-10-29): el crédito de
    # foto SÍ cuenta como mención. El texto ya no se alarga porque el
    # extracto corto se conserva y se recorta a 3 oraciones, no se expande.
    def test_solo_credito_no_cuenta_como_mencion(self):
        ctx = A._contexto_exacto_marca(CUERPO_REPORTE, TIT_REPORTE,
                                       "Comfenalco",
                                       ["Comfenalco Valle"])
        self.assertIn("Foto:Comfenalco", ctx)
        final = A._contexto_minimo_util(ctx, TIT_REPORTE, CUERPO_REPORTE)
        self.assertIn("Foto:Comfenalco", final)
        self.assertLess(len(final), 100)

    def test_variantes_de_credito_pegado(self):
        for credito in ("Foto:Comfenalco", "Foto: Comfenalco",
                        "Imagen:Comfenalco", "Foto cortesía Comfenalco",
                        "Fotografía: Comfenalco"):
            cuerpo = ("Ocurrió un hecho en Guacarí. Guacarí %s Valle del "
                      "Cauca. Más texto sin mención." % credito)
            ctx = A._contexto_exacto_marca(cuerpo, "T", "Comfenalco", [])
            self.assertIn("Comfenalco", ctx, credito)

    def test_mencion_real_antes_del_credito_si_cuenta(self):
        cuerpo = ("Comfenalco anunció vacantes para la región, "
                  "Foto: Juan Pérez. Otro párrafo sin mención.")
        ctx = A._contexto_exacto_marca(cuerpo, "T", "Comfenalco", [])
        self.assertIn("Comfenalco anunció vacantes", ctx)

    def test_credito_no_esconde_mencion_real_en_otra_oracion(self):
        cuerpo = ("Guacarí Foto:Comfenalco Valle del Cauca. "
                  "La agencia de empleo Comfenalco abrió convocatoria.")
        ctx = A._contexto_exacto_marca(cuerpo, "T", "Comfenalco", [])
        self.assertIn("Comfenalco abrió convocatoria", ctx)
        self.assertIn("Foto:Comfenalco", ctx)  # v4.65: el crédito cuenta

    def test_mencion_real_sin_credito_igual_que_antes(self):
        cuerpo = ("El turismo creció. Cotelco presentó sus propuestas. "
                  "Fin del texto.")
        ctx = A._contexto_exacto_marca(cuerpo, "T", "Cotelco", [])
        self.assertIn("Cotelco presentó sus propuestas", ctx)


class TestContextoMinimoVentanaLead(unittest.TestCase):
    def test_caso_reportado_no_llega_al_cuerpo_completo(self):
        # v4.65: el crédito SÍ es el extracto y se conserva corto (una
        # oración), sin ventana de lead ni cuerpo completo.
        ctx = A._contexto_exacto_marca(CUERPO_REPORTE, TIT_REPORTE,
                                       "Comfenalco",
                                       ["Comfenalco Valle"])
        final = A._contexto_minimo_util(ctx, TIT_REPORTE, CUERPO_REPORTE)
        self.assertEqual(final, "Guacarí Foto:Comfenalco Valle del Cauca.")

    def test_fragmento_tiny_se_completa_con_lead_no_con_cuerpo(self):
        # v4.65: el fragmento de mención se conserva; el lead (recortado
        # a 3 oraciones) solo se usa cuando NO hay mención.
        cuerpo_largo = "Lead de la noticia sobre el hecho principal. " \
            + PARRAFO_NEUTRO * 40
        final = A._contexto_minimo_util("Edwin Bernal, director ejecutivo "
                                        "de Cotelco.", "Titular", cuerpo_largo)
        self.assertEqual(final, "Edwin Bernal, director ejecutivo "
                                "de Cotelco.")
        final2 = A._contexto_minimo_util("", "Titular", cuerpo_largo)
        self.assertTrue(final2.startswith("Lead de la noticia"))
        self.assertLessEqual(len(final2), 1600)

    def test_contexto_util_largo_se_conserva(self):
        bueno = ("Cotelco participó en el conversatorio sobre turismo con "
                 "200 asistentes. El gremio presentó sus propuestas de "
                 "reactivación y el director ejecutivo explicó el plan de "
                 "trabajo para el segundo semestre del año.")
        self.assertEqual(A._contexto_minimo_util(bueno, "T", "Texto"), bueno)


if __name__ == '__main__':
    unittest.main()
