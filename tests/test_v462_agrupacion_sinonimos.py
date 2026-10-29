"""v4.62: misma historia de riesgo con sinónimos + recorte de titular.

Caso real Unsimón 2026-10-29 (53 filas, entrada
29_USIMONAN_2026_10_29_10_59.xlsx, salida
Dossier_Universidad_Simon_Bolivar_20261029_1603.xlsx): 16 notas sobre
el riesgo para estudiantes cerca de Unisimón salieron con 7 subtemas
distintos («Intimidaciones…», «Alerta por engaños…», «Alerta por
intentos de secuestro», «Mesa sobre intimidaciones…», «Alertas por
acoso…», «Universidades en Barranquilla por acoso a mujeres»,
«Alerta por seguridad estudiantil») porque intimidación/acoso/engaño/
secuestro/trata no comparten stem y el puente de títulos exige
token_set >= 70-80. Con el pase de ancla de cuerpo (riesgo + grupo +
ancla compartida: Barrera/Moreno/Mariposa Púrpura/carrera 54/modo
desconocidos) las 16 quedan en un solo grupo, sin arrastrar a la nota
de tarifas que solo trae un «Lea también» sin ancla.
"""
import unittest

import analyzer_tono_tema as A


def fila(titulo, cuerpo):
    return {'Título': titulo, 'CuerpoEs': cuerpo}


class TestHistoriaRiesgoSinonimos(unittest.TestCase):
    def test_misma_historia_riesgo_exige_ancla_compartida(self):
        # El ancla debe COMPARTIRSE: Barrera sola no une con Moreno
        # sola; la conectividad en el dossier real es transitiva
        # (hay filas que traen Barrera + Moreno, como la de Emisora
        # Atlántico, y filas que traen Moreno + Púrpura).
        barrera = ("Alertan por intimidaciones a mujeres cerca de Unisimón",
                   "La concejal Heidi Barrera pidió investigar "
                   "intimidaciones contra mujeres estudiantes.")
        moreno = ("Investigadora alerta sobre engaños para secuestrar a estudiantes",
                  "Alejandra Moreno advirtió engaños de desconocidos para "
                  "abordar a jóvenes universitarias.")
        barrera_moreno = ("Heidi Barrera pide investigar denuncias de acoso",
                          "Barrera se sumó a las denuncias de Moreno sobre "
                          "intimidación contra estudiantes universitarias "
                          "en la carrera 54.")
        purpura = ("Alertan sobre intentos de secuestro cerca de universidades",
                   "Mariposa Púrpura alerta en la carrera 54 con calle 67 "
                   "sobre secuestro de estudiantes.")
        self.assertFalse(A._misma_historia_riesgo(*barrera, *moreno))
        self.assertTrue(A._misma_historia_riesgo(*barrera, *barrera_moreno))
        self.assertTrue(A._misma_historia_riesgo(*moreno, *barrera_moreno))
        self.assertTrue(A._misma_historia_riesgo(*barrera_moreno, *purpura))

    def test_lea_tambien_sin_ancla_no_une(self):
        # La nota de tarifas solo trae la línea «Lea también» sobre
        # intimidación, sin Barrera/Moreno/Mariposa/carrera 54/modo.
        self.assertFalse(A._misma_historia_riesgo(
            "Es inconcebible que el Caribe pague unas tarifas tan altas",
            "Las tarifas en la región serán un desafío. Lea también: Piden "
            "investigar presuntos casos de intimidación contra mujeres en "
            "alrededores de la Universidad Simón Bolívar.",
            "Alertan por intimidaciones a mujeres cerca de Unisimón",
            "La concejal Heidi Barrera pidió investigar intimidaciones "
            "contra mujeres estudiantes."))

    def test_construir_grupos_une_las_16_y_no_arrastra_tarifas(self):
        rows = [
            fila("Alertan por intimidaciones a mujeres cerca de Unisimón",
                 "Barrera se sumó a las denuncias de Moreno sobre "
                 "intimidaciones contra mujeres estudiantes cerca de la "
                 "Universidad Simón Bolívar."),
            fila("Alerta por engaños a universitarias",
                 "Moreno advirtió engaños de desconocidos para abordar a "
                 "estudiantes universitarias."),
            fila("Alertan sobre intentos de secuestro cerca de universidades",
                 "Mariposa Púrpura alerta en la carrera 54 con calle 67 "
                 "sobre secuestro de estudiantes."),
            fila("Policía y Gaula blindan dos universidades por acoso a mujeres",
                 "Patrullas púrpura vigilan. Moreno denunció acoso contra "
                 "mujeres estudiantes."),
            fila("Es inconcebible que el Caribe pague unas tarifas tan altas",
                 "Las tarifas en la región serán un desafío. Lea también: "
                 "Piden investigar intimidación contra mujeres."),
            fila("FILBAC 2026 abre las puertas del Caribe a la literatura",
                 "La feria contará con la Universidad Simón Bolívar y la "
                 "Red Cultural del Banco de la República."),
        ]
        grupos, mapa = A.construir_grupos(rows, {})
        # Las 4 de riesgo, en un solo grupo; tarifas y FILBAC, solas.
        g_riesgo = {mapa[i] for i in range(4)}
        self.assertEqual(len(g_riesgo), 1)
        self.assertNotIn(mapa[4], g_riesgo)
        self.assertNotIn(mapa[5], g_riesgo)


class TestGuardaSinonimosMendoza(unittest.TestCase):
    def test_nombramiento_no_se_parte_por_sinonimo(self):
        # v4.50 partía este par: el canon «Nombramiento del director de
        # Medicina» no pasaba la guarda en la fila que dice «asumirá la
        # dirección del programa de Medicina» (nombramiento/asumir y
        # director/dirección no compartían stem). Con las clases v4.62
        # pasa en ambas filas.
        canon = "Nombramiento del director de Medicina"
        self.assertTrue(A._asunto_fiel_a_texto(
            canon,
            "Humberto Mendoza asume dirección del programa de Medicina",
            "El médico Humberto Mendoza se posesionó como nuevo director "
            "del programa de Medicina ante el rector.",
            "Universidad Simón Bolívar", ["Unisimón"]))
        self.assertTrue(A._asunto_fiel_a_texto(
            canon,
            "Humberto Mendoza presenta experiencia en el Congreso Argentino "
            "de Diabetes y asume dirección del programa de Medicina",
            "En el ámbito académico, Humberto Mendoza asumirá la dirección "
            "del programa de Medicina de la Universidad Simón Bolívar.",
            "Universidad Simón Bolívar", ["Unisimón"]))


class TestRecorteTituloSeResuelvePorAgrupacionYGuarda(unittest.TestCase):
    def test_cola_titulo_no_es_el_problema_si_hay_bloque(self):
        # v4.62 NO intenta distinguir en validar() un segmento
        # contiguo «legítimo» («Estado de salud de Yamid Amat»,
        # «Conexión entre educación media y superior») de un recorte
        # genérico («Universidades en Barranquilla por acoso a mujeres»):
        # esa distinción no es determinista y rompía los tests v4.21 /
        # v4.34. Los casos del dossier se resuelven antes: la fila de
        # Policía/Gaula entra en el bloque de riesgo por ancla (ver
        # TestHistoriaRiesgoSinonimos) y la de Mendoza conserva el
        # canon por la guarda de sinónimos (ver
        # TestGuardaSinonimosMendoza), así que ninguna llega al Excel
        # con el recorte del titular.
        self.assertEqual(A.validar(
            "Estado de salud de Yamid Amat", 'Neutro',
            ["Actualización sobre el estado de salud de Yamid Amat "
             "en clínica"]), [])
        self.assertEqual(A.validar(
            "Alerta por engaños a universitarias", 'Neutro',
            ["Alerta crítica en Barranquilla por red de engaños dirigida "
             "a raptar estudiantes universitarias"]), [])


if __name__ == '__main__':
    unittest.main()
