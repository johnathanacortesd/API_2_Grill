"""v4.66 (regla del usuario 2026-10-29, dos partes):

1. Contexto analizado incluye el título cuando el título menciona la
   marca, un alias o un vocero (revoca parcialmente v4.28: desde v4.63
   el prompt usa el cuerpo para el subtema y el contexto solo para el
   tono, así que el título con marca vuelve como evidencia de tono).
2. La columna texto (Contexto sin el Título) se extiende hasta el
   siguiente punto cuando es corta: 60 palabras o menos, o 370
   caracteres o menos, según sus fórmulas de Excel sobre la celda de
   texto (palabras = LARGO(ESPACIOS(Y)) - LARGO(SUSTITUIR(ESPACIOS(Y);
   " "; "")) + 1; caracteres = LARGO(Y)). Si es mayor, se deja tal cual.
"""
import unittest

import analyzer_tono_tema as A


def texto_de(contexto, titulo):
    return A._texto_desde_contexto(contexto, titulo)


class TestTituloEnContexto(unittest.TestCase):
    def test_titulo_con_marca_se_incluye(self):
        ctx = A._contexto_con_titulo_y_extension(
            "Cotelco anunció la feria. Cotelco anunció el foro.",
            "Cotelco abre feria", "cuerpo", "Cotelco", [], [])
        self.assertTrue(ctx.startswith("Cotelco abre feria"))

    def test_titulo_con_alias_y_vocero_se_incluye(self):
        self.assertTrue(A._titulo_menciona_marca(
            "Asotelco celebra aniversario", "Cotelco", ["Asotelco"], []))
        self.assertTrue(A._titulo_menciona_marca(
            "Edwin Bernal habla del turismo", "Cotelco", [],
            ["Edwin Bernal"]))

    def test_titulo_sin_marca_no_se_incluye(self):
        ctx = A._contexto_con_titulo_y_extension(
            "Cotelco anunció la feria. Cotelco anunció el foro.",
            "El turismo crece en el Valle", "cuerpo", "Cotelco", [], [])
        self.assertNotIn("El turismo crece", ctx)
        self.assertFalse(A._titulo_menciona_marca(
            "El turismo crece", "Cotelco", [], []))

    def test_marca_como_subcadena_de_otra_palabra_no_cuenta(self):
        self.assertFalse(A._titulo_menciona_marca(
            "Comfenalcos regionales", "Comfenalco", [], []))

    def test_titulo_no_se_duplica_en_fila_solo_titulo(self):
        ctx = A._contexto_con_titulo_y_extension(
            "", "Cotelco abre feria", "", "Cotelco", [], [])
        self.assertEqual(ctx, "Cotelco abre feria")


class TestExtensionTextoCorto(unittest.TestCase):
    EXTRACTO = ("Cotelco anunció la feria. Cotelco anunció el foro. "
                "Cotelco anunció la jornada. Cotelco anunció el congreso. "
                "Cotelco anunció la gala.")

    def test_texto_corto_se_extiende_al_siguiente_punto(self):
        # Sin título con marca: texto = 3 oraciones (12 palabras) -> 4.
        ctx = A._contexto_con_titulo_y_extension(
            self.EXTRACTO, "El turismo crece", "cuerpo", "Cotelco", [], [])
        self.assertEqual(texto_de(ctx, "El turismo crece"),
                         "Cotelco anunció la feria. Cotelco anunció el foro. "
                         "Cotelco anunció la jornada. "
                         "Cotelco anunció el congreso.")

    def test_texto_corto_con_titulo_tambien_se_extiende(self):
        # Con título con marca: Contexto lleva título + 4 oraciones;
        # texto (sin título) queda en 4 oraciones.
        ctx = A._contexto_con_titulo_y_extension(
            self.EXTRACTO, "Cotelco abre feria", "cuerpo", "Cotelco", [], [])
        self.assertTrue(ctx.startswith("Cotelco abre feria"))
        self.assertEqual(texto_de(ctx, "Cotelco abre feria"),
                         "Cotelco anunció la feria. Cotelco anunció el foro. "
                         "Cotelco anunció la jornada. "
                         "Cotelco anunció el congreso.")

    def test_texto_largo_se_deja_tal_cual(self):
        larga = ("Cotelco presentó un informe extenso sobre la reactivación "
                 "del turismo regional con cifras detalladas de ocupación "
                 "hotelera, llegada de visitantes y derrama económica en "
                 "los principales destinos del departamento durante todo "
                 "el primer semestre del año en curso. "
                 "El gremio explicó además que las proyecciones para la "
                 "segunda mitad del año se mantienen al alza gracias a la "
                 "recuperación de la conectividad aérea y a la promoción "
                 "internacional sostenida en ferias especializadas. "
                 "Finalmente, el director ejecutivo anunció nuevas alianzas "
                 "con operadores locales para fortalecer la oferta turística "
                 "y la formalización de prestadores en todo el territorio.")
        ctx = A._contexto_con_titulo_y_extension(
            larga + " Cuarta oración que no debe entrar.",
            "El turismo crece", "cuerpo", "Cotelco", [], [])
        self.assertNotIn("Cuarta oración", ctx)
        self.assertEqual(len(texto_de(ctx, "El turismo crece").split()) > 60,
                         True)

    def test_umbral_por_palabras_extiende_aunque_pase_370_caracteres(self):
        # 3 oraciones de 20 palabras largas: 60 palabras (<=60) pero más
        # de 370 caracteres. La medida en palabras dice "corto" y se extiende.
        larga = " ".join(["internacionalización"] * 20) + ". "
        extracto = larga + larga + larga + "Cuarta oración corta."
        ctx = A._contexto_con_titulo_y_extension(
            extracto, "El turismo crece", "cuerpo", "Cotelco", [], [])
        txt = texto_de(ctx, "El turismo crece")
        self.assertEqual(len(txt.split()), 60 + 3)
        self.assertIn("Cuarta oración corta", txt)

    def test_umbral_por_caracteres_extiende_aunque_pase_60_palabras(self):
        # 3 oraciones de 21 palabras de una letra: 63 palabras (>60) pero
        # menos de 370 caracteres. La medida en caracteres dice "corto"
        # y se extiende.
        corta = " ".join("abcdefghijklmnopqrstu") + ". "
        extracto = corta + corta + corta + "Cuarta oración corta."
        ctx = A._contexto_con_titulo_y_extension(
            extracto, "El turismo crece", "cuerpo", "Cotelco", [], [])
        txt = texto_de(ctx, "El turismo crece")
        self.assertLess(len(txt), 370 + 30)
        self.assertIn("Cuarta oración corta", txt)

    def test_sin_cuarta_oracion_no_hay_extension(self):
        extracto = "Cotelco anunció la feria. Cotelco anunció el foro."
        ctx = A._contexto_con_titulo_y_extension(
            extracto, "El turismo crece", "cuerpo", "Cotelco", [], [])
        self.assertEqual(texto_de(ctx, "El turismo crece"), extracto)


class TestExtensionConOracionSiguienteDelCuerpo(unittest.TestCase):
    def test_extracto_agotado_agrega_la_siguiente_del_cuerpo(self):
        cuerpo = ("Cotelco anunció la feria. Cotelco anunció el foro. "
                  "Siguiente oración del cuerpo. Otra más.")
        extracto = ("Cotelco anunció la feria. Cotelco anunció el foro.")
        ctx = A._contexto_con_titulo_y_extension(
            extracto, "El turismo crece", cuerpo, "Cotelco", [], [])
        self.assertEqual(texto_de(ctx, "El turismo crece"),
                         extracto + " Siguiente oración del cuerpo.")

    def test_extracto_agotado_sin_siguiente_no_cambia(self):
        cuerpo = "Cotelco anunció la feria. Cotelco anunció el foro."
        extracto = cuerpo
        ctx = A._contexto_con_titulo_y_extension(
            extracto, "El turismo crece", cuerpo, "Cotelco", [], [])
        self.assertEqual(texto_de(ctx, "El turismo crece"), extracto)

    def test_ancla_en_ultima_aparicion_de_mencion_repetida(self):
        # Extracto deduplica la mención repetida; el ancla es la última
        # aparición en el cuerpo, así que la siguiente es "Final."
        cuerpo = ("Cotelco anunció la feria. Oración intermedia. "
                  "Cotelco anunció la feria. Final.")
        extracto = "Cotelco anunció la feria."
        ctx = A._contexto_con_titulo_y_extension(
            extracto, "El turismo crece", cuerpo, "Cotelco", [], [])
        self.assertEqual(texto_de(ctx, "El turismo crece"),
                         "Cotelco anunció la feria. Final.")


if __name__ == '__main__':
    unittest.main()
