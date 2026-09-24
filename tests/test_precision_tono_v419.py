"""v4.19: precisión de tono — la marca como sede del evento + cita experta.

Regla del cliente (2026-09-23): «eventos en la marca/alias o participación
de voceros es positivo».

Caso real que la motivó (ID 60761392, dossier Unisimón): «Salud Consciencia
2026, realizado el 26 de agosto en la Universidad Simón Bolívar» quedó
Neutro. La guarda no tenía patrón para la marca como SEDE («en» ≠ «por»):
el participio pasivo F3 exige «por + marca» y el evento no estaba en la
lista cerrada.

- Sede: verbo de realización (realizado/celebrado/organizado/llevado a cabo,
  en varias formas) o sustantivo de evento + «en + marca/alias» en la misma
  oración → Positivo. No aplica en tragedia sin acción, ni en menciones
  biográficas («realiza sus estudios en la Universidad» se excluye), ni en
  alianzas («en alianza con … la Universidad» no es sede: el «en» no precede
  a la marca). «encuentro» no cuenta como evento tras «me » (verbo).
- HABLA_PAT suma «de acuerdo con»: «de acuerdo con Hernando Sánchez,
  biólogo y docente de la Universidad Simón Bolívar, …» → Positivo
  (vocero citado como fuente experta). «según» se excluyó a propósito:
  cero verdaderos positivos en el dossier real y riesgo de marcar la marca
  como simple punto de referencia («según la Policía, ocurrió frente a
  la Universidad»).
"""
import re
import unittest

from analyzer_tono_tema import HABLA_PAT, aplicar_guarda_positiva

BRAND = "Universidad Simón Bolívar"
ALIASES = ["Unisimón"]


def _tono(contexto, titulo="T"):
    grupos = [{"grupo": 1, "titulo": titulo, "titulos_alt": [], "texto": "",
               "contexto": contexto, "contexto_marca": contexto}]
    etiquetas = {1: {"sub_tema": "x", "tono": "Neutro"}}
    aplicar_guarda_positiva(grupos, etiquetas, BRAND, ALIASES)
    return etiquetas[1]["tono"]


class TestSedeDelEvento(unittest.TestCase):
    def test_caso_real_salud_consciencia(self):
        ctx = ("Esa fue una de las principales conclusiones de Salud Consciencia 2026, "
               "realizado el 26 de agosto en la Universidad Simón Bolívar de Barranquilla, "
               "donde especialistas en medicina analizaron los cambios.")
        self.assertEqual(_tono(ctx), "Positivo")

    def test_llevara_a_cabo_en_alias(self):
        self.assertEqual(
            _tono("En la capital se llevará a cabo en Unisimón, y reunirá a cuidadores."),
            "Positivo")

    def test_sustantivo_evento_mas_en_marca(self):
        self.assertEqual(
            _tono("Un simposio científico en la Universidad Simón Bolívar, de 8 a 12."),
            "Positivo")

    def test_futuro_sin_sustantivo(self):
        self.assertEqual(
            _tono("El encuentro se realizará en la Universidad Simón Bolívar."),
            "Positivo")

    def test_alianza_no_es_sede(self):
        ctx = ("La Asociación realizará la Semana de Prevención del Suicidio, en alianza "
               "con la Alcaldía y la Universidad Simón Bolívar.")
        self.assertEqual(_tono(ctx), "Neutro")

    def test_biografico_no_es_sede(self):
        self.assertEqual(
            _tono("Juan realiza sus estudios de medicina en la Universidad Simón Bolívar."),
            "Neutro")

    def test_tragedia_no_sube(self):
        self.assertEqual(
            _tono("La velación se realizará en la Universidad tras la tragedia."),
            "Neutro")

    def test_encuentro_verbo_no_cuenta(self):
        self.assertEqual(
            _tono("Me encuentro en la Universidad Simón Bolívar desde ayer."),
            "Neutro")

    def test_nombre_corto_no_alcanza(self):
        # Consistente con el resto de la guarda: se exige marca o alias.
        self.assertEqual(
            _tono("El informe fue realizado en la Universidad por consultores."),
            "Neutro")


class TestHablaDeAcuerdoCon(unittest.TestCase):
    def test_patron_incluye_de_acuerdo_con(self):
        self.assertTrue(HABLA_PAT.search("de acuerdo con el experto"))

    def test_patron_no_incluye_segun(self):
        # Decisión de precisión: «según» es ambiguo (punto de referencia).
        self.assertFalse(HABLA_PAT.search("según el informe"))

    def test_docente_citado_como_experto(self):
        ctx = ("De acuerdo con Hernando Sánchez, biólogo y docente de la Universidad "
               "Simón Bolívar, la recuperación no debe centrarse solo en retirar vegetación.")
        self.assertEqual(_tono(ctx), "Positivo")

    def test_de_acuerdo_con_sin_actor_no_sube(self):
        self.assertEqual(
            _tono("De acuerdo con el informe del DANE, el desempleo bajó."),
            "Neutro")

    def test_de_acuerdo_con_en_tragedia_no_sube(self):
        ctx = ("De acuerdo con el médico de la Universidad Simón Bolívar, la víctima "
               "falleció tras el accidente en la vía.")
        self.assertEqual(_tono(ctx), "Neutro")


if __name__ == "__main__":
    unittest.main()
