# -*- coding: utf-8 -*-
"""v4.70: coherencia de subtema/tema con el texto de la noticia, Contexto
analizado desde CuerpoEs, columna «texto» sin el Título y «Link Ext» como
texto plano."""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook

import analyzer_tono_tema as att
from analyzer_tono_tema import (
    _texto_contexto_fila, coherencia_subtemas_por_texto, construir_grupos, nz,
    refinar_familias_por_contexto,
)
from coherencia_texto import (
    SIM_MISMA_NOTICIA, SIM_RELACION_SUBTEMA, matriz_similitud, texto_noticia,
    texto_sin_titulo, tokens_marca, variantes_vocero,
)
from pipeline import KEY_MAP, generate_output_excel

BRAND = "Secretaría General de la Alcaldía de Bogotá"
ALIASES = ["Alcaldía de Bogotá", "Secretaría General"]
VOCEROS = ["Miguel Silva Moyano", "Carlos Fernando Galán"]

NOTAS = {
    "metro_a": (
        "Avanza construcción del Metro de Bogotá: viaducto llega a la avenida Primero de Mayo",
        "La Alcaldía de Bogotá informó que la construcción de la primera línea del Metro de "
        "Bogotá alcanzó un avance del 62 %. El viaducto ya llega a la avenida Primero de Mayo y "
        "en las próximas semanas se instalarán nuevas vigas. El alcalde Carlos Fernando Galán "
        "recorrió las obras y aseguró que los trenes llegarán al patio taller de Bosa en marzo. "
        "La Empresa Metro indicó que se trabaja en tres frentes simultáneos."),
    "metro_b": (
        "Metro de Bogotá supera el 62 % de avance; viaducto alcanza la Primero de Mayo",
        "El alcalde Carlos Fernando Galán recorrió este martes las obras de la primera línea del "
        "metro, que ya supera el 62 % de ejecución. Según la Empresa Metro, el viaducto llegó a "
        "la avenida Primero de Mayo y se instalarán nuevas vigas en las próximas semanas. Los "
        "primeros trenes llegarán al patio taller en Bosa en marzo, indicó la Alcaldía de Bogotá."),
    "metro_c": (
        "Comerciantes de la Caracas denuncian pérdidas por obras del metro",
        "Comerciantes de la avenida Caracas aseguran que sus ventas cayeron hasta un 40 % por los "
        "cierres de las obras de la primera línea del metro. Piden a la Alcaldía de Bogotá alivios "
        "tributarios y mejor señalización. La Secretaría de Desarrollo Económico anunció una mesa "
        "de trabajo con los afectados."),
    "agua_a": (
        "Bogotá termina el racionamiento de agua tras un año",
        "La Alcaldía de Bogotá anunció el fin del racionamiento de agua que rigió durante un año "
        "en la ciudad. El alcalde Galán explicó que los embalses del sistema Chingaza superaron "
        "el 70 % de su capacidad. La Empresa de Acueducto pidió mantener el ahorro en los hogares."),
    "agua_b": (
        "Se acabó el racionamiento de agua en Bogotá: embalses de Chingaza al 70 %",
        "Tras un año de cortes por turnos, el alcalde Carlos Fernando Galán anunció que termina el "
        "racionamiento de agua en Bogotá. Los embalses de Chingaza están por encima del 70 % de su "
        "capacidad, según el Acueducto, que pidió a los ciudadanos no bajar la guardia con el ahorro."),
    "sec_a": (
        "Secretaría General lanza ventanilla única de trámites digitales",
        "La Secretaría General de la Alcaldía de Bogotá lanzó la ventanilla única de trámites "
        "digitales, que permitirá a los ciudadanos hacer más de 200 gestiones en línea. Miguel "
        "Silva Moyano, secretario general, dijo que la herramienta reduce tiempos y costos. La "
        "plataforma estará disponible en el portal Bogotá te escucha."),
    "sec_c": (
        "Secretaría General conmemora a víctimas del conflicto en el Centro de Memoria",
        "La Secretaría General de la Alcaldía de Bogotá, a través de la Alta Consejería para las "
        "Víctimas, realizó un acto de conmemoración en el Centro de Memoria, Paz y Reconciliación. "
        "Familiares de víctimas participaron en una jornada de memoria con ofrendas florales."),
}


def _grupo(gid, clave, n=1):
    t, c = NOTAS[clave]
    return {"grupo": gid, "n": n, "idxs": [gid], "titulo": t, "titulos_alt": [],
            "texto": c, "contexto": c, "contexto_marca": c}


def _sim(a, b):
    S = matriz_similitud([texto_noticia(*NOTAS[a]), texto_noticia(*NOTAS[b])],
                         excluir=tokens_marca(BRAND, ALIASES, VOCEROS))
    return S[0, 1]


class TestCalibracionSimilitud(unittest.TestCase):
    def test_misma_noticia_reescrita_supera_umbral(self):
        self.assertGreaterEqual(_sim("metro_a", "metro_b"), SIM_MISMA_NOTICIA)
        self.assertGreaterEqual(_sim("agua_a", "agua_b"), SIM_MISMA_NOTICIA)

    def test_hechos_distintos_no_comparten_subtema(self):
        self.assertLess(_sim("metro_a", "metro_c"), SIM_RELACION_SUBTEMA)
        self.assertLess(_sim("sec_a", "sec_c"), SIM_RELACION_SUBTEMA)
        self.assertLess(_sim("metro_a", "agua_a"), 0.08)


class TestTextoSinTitulo(unittest.TestCase):
    def test_titulo_con_diferencias_de_forma(self):
        ctx = "“Bogotá  avanza” en obras: la Alcaldía de Bogotá entregó colegios."
        self.assertEqual(texto_sin_titulo(ctx, '"Bogotá avanza" en obras:'),
                         "la Alcaldía de Bogotá entregó colegios.")

    def test_titulo_con_entidades_html(self):
        ctx = "Avanza la obra del colegio. La Alcaldía de Bogotá entregó la sede."
        self.assertEqual(texto_sin_titulo(ctx, "Avanza la obra del colegio"),
                         "La Alcaldía de Bogotá entregó la sede.")
        ctx2 = "Gesti&#xF3;n del metro La Alcaldía de Bogotá avanzó."
        self.assertEqual(texto_sin_titulo(ctx2, "Gestión del metro"),
                         "La Alcaldía de Bogotá avanzó.")

    def test_sin_titulo_en_contexto_no_cambia(self):
        self.assertEqual(texto_sin_titulo("La Alcaldía entregó 3 colegios.",
                                          "Bogotá avanza en obras"),
                         "La Alcaldía entregó 3 colegios.")

    def test_no_corta_dentro_de_palabra(self):
        self.assertEqual(texto_sin_titulo("Bogotá avanza en obrasmaestras",
                                          "Bogotá avanza en obras"),
                         "Bogotá avanza en obrasmaestras")


class TestVoceros(unittest.TestCase):
    def test_nombre_y_primer_apellido(self):
        self.assertIn("Miguel Silva", variantes_vocero("Miguel Silva Moyano"))
        self.assertIn("Carlos Galán", variantes_vocero("Carlos Fernando Galán Sarmiento"))

    def test_dos_nombres_de_pila_no_quedan_solos(self):
        v = variantes_vocero("Juan Carlos Pérez")
        self.assertIn("Juan Pérez", v)
        self.assertNotIn("Juan Carlos", v)

    def test_frases_y_apellido_solo_sin_cambios(self):
        self.assertEqual(variantes_vocero("el alcalde"), ["el alcalde"])
        self.assertEqual(variantes_vocero("Galán"), ["Galán"])


class TestContextoDesdeCuerpoEs(unittest.TestCase):
    def test_prefiere_cuerpoes_con_mencion(self):
        row = {"CuerpoEs": "Texto corto. La Secretaría General habló del plan.",
               "Resumen - Aclaracion": "Resumen mucho más largo " * 20}
        self.assertTrue(_texto_contexto_fila(row, KEY_MAP, BRAND, ALIASES, VOCEROS)
                        .startswith("Texto corto."))

    def test_si_cuerpoes_no_menciona_usa_texto_con_mencion(self):
        row = {"CuerpoEs": "Nada de la marca aquí. " * 5,
               "Resumen - Aclaracion": "La Alcaldía de Bogotá anunció obras."}
        self.assertEqual(_texto_contexto_fila(row, KEY_MAP, BRAND, ALIASES, VOCEROS),
                         "La Alcaldía de Bogotá anunció obras.")

    def test_vocero_por_nombre_y_apellido_en_contexto(self):
        cuerpo = ("El plan de trámites arrancó en enero. Miguel Silva explicó que habrá "
                  "200 trámites en línea. El clima fue lluvioso.")
        rows = [{"Título": "Trámites en línea", "CuerpoEs": cuerpo,
                 "Tipo de Medio": "Online"}]
        ctx = att._contexto_exacto_marca(cuerpo, "Trámites en línea", BRAND, ALIASES,
                                         voceros=att.expandir_voceros(VOCEROS))
        self.assertIn("Miguel Silva explicó", ctx)
        self.assertNotIn("clima", ctx)
        self.assertTrue(rows)


class TestGruposVetoTextual(unittest.TestCase):
    def _rows(self, claves, titulos=None):
        rows = []
        for k, clave in enumerate(claves):
            t, c = NOTAS[clave]
            rows.append({"Título": (titulos or {}).get(k, t), "CuerpoEs": c,
                         "Contexto analizado": c})
        return rows

    def test_titulares_parecidos_cuerpos_distintos_no_se_agrupan(self):
        # Dos hechos distintos con titulares que comparten palabras.
        titulos = {0: "Secretaría General de Bogotá presenta nueva estrategia de trámites digitales",
                   1: "Secretaría General de Bogotá presenta nueva estrategia de memoria para víctimas"}
        rows = self._rows(["sec_a", "sec_c"], titulos)
        grupos, mapa = construir_grupos(
            rows, KEY_MAP, excluir_similitud=tokens_marca(BRAND, ALIASES, VOCEROS))
        self.assertNotEqual(mapa[0], mapa[1])

    def test_titular_casi_identico_se_agrupa(self):
        t = "Secretaría General lanza ventanilla única de trámites digitales"
        rows = self._rows(["sec_a", "sec_a"], {0: t, 1: t + " en Bogotá"})
        grupos, mapa = construir_grupos(rows, KEY_MAP)
        self.assertEqual(mapa[0], mapa[1])


class TestCoherenciaSubtemas(unittest.TestCase):
    def test_noticia_ajena_sale_del_subtema_forzado(self):
        grupos = [_grupo(1, "metro_a", n=3), _grupo(2, "metro_b"), _grupo(3, "sec_c")]
        etiquetas = {1: {"sub_tema": "Avance de obras del metro", "tono": "Positivo"},
                     2: {"sub_tema": "Avance de obras del metro", "tono": "Positivo"},
                     3: {"sub_tema": "Avance de obras del metro", "tono": "Neutro"}}
        originales = {1: "Avance de obras del metro", 2: "Avance del viaducto del metro",
                      3: "Conmemoración de víctimas del conflicto"}
        res = coherencia_subtemas_por_texto(grupos, etiquetas, originales,
                                            brand=BRAND, aliases=ALIASES, voceros=VOCEROS)
        self.assertEqual(etiquetas[1]["sub_tema"], "Avance de obras del metro")
        self.assertEqual(etiquetas[2]["sub_tema"], "Avance de obras del metro")
        self.assertEqual(etiquetas[3]["sub_tema"], "Conmemoración de víctimas del conflicto")
        self.assertEqual(res["separados"], 1)

    def test_sin_rotulo_propio_fiel_se_genera_uno(self):
        grupos = [_grupo(1, "agua_a", n=2), _grupo(2, "metro_c")]
        etiquetas = {1: {"sub_tema": "Fin del racionamiento de agua", "tono": "Positivo"},
                     2: {"sub_tema": "Fin del racionamiento de agua", "tono": "Neutro"}}
        coherencia_subtemas_por_texto(grupos, etiquetas, {}, brand=BRAND,
                                      aliases=ALIASES, voceros=VOCEROS)
        self.assertEqual(etiquetas[1]["sub_tema"], "Fin del racionamiento de agua")
        self.assertNotEqual(nz(etiquetas[2]["sub_tema"]), nz("Fin del racionamiento de agua"))
        self.assertTrue(etiquetas[2]["sub_tema"].strip())

    def test_misma_noticia_con_subtemas_distintos_se_unifica(self):
        grupos = [_grupo(1, "agua_a", n=3), _grupo(2, "agua_b")]
        etiquetas = {1: {"sub_tema": "Fin del racionamiento de agua", "tono": "Positivo"},
                     2: {"sub_tema": "Embalses de Chingaza al 70 %", "tono": "Positivo"}}
        res = coherencia_subtemas_por_texto(grupos, etiquetas, {}, brand=BRAND,
                                            aliases=ALIASES, voceros=VOCEROS)
        self.assertEqual(etiquetas[2]["sub_tema"], "Fin del racionamiento de agua")
        self.assertEqual(res["unidos"], 1)

    def test_hechos_distintos_conservan_subtemas_distintos(self):
        grupos = [_grupo(1, "metro_a"), _grupo(2, "metro_c")]
        etiquetas = {1: {"sub_tema": "Avance de obras del metro", "tono": "Positivo"},
                     2: {"sub_tema": "Pérdidas de comerciantes por obras del metro",
                         "tono": "Neutro"}}
        coherencia_subtemas_por_texto(grupos, etiquetas, {}, brand=BRAND,
                                      aliases=ALIASES, voceros=VOCEROS)
        self.assertEqual(etiquetas[1]["sub_tema"], "Avance de obras del metro")
        self.assertEqual(etiquetas[2]["sub_tema"],
                         "Pérdidas de comerciantes por obras del metro")


class TestTemaPorContexto(unittest.TestCase):
    def test_familia_sin_relacion_de_contexto_se_separa(self):
        items = [{"sub_tema": "Avance de obras del metro", "clave": "a"},
                 {"sub_tema": "Obras de memoria para víctimas", "clave": "b"}]
        fams = refinar_familias_por_contexto(
            items, [items], {"a": [NOTAS["metro_a"][1]], "b": [NOTAS["sec_c"][1]]},
            excluir=tokens_marca(BRAND, ALIASES, VOCEROS))
        self.assertEqual(len(fams), 2)

    def test_mismo_asunto_se_mantiene_y_contexto_muy_parecido_une(self):
        items = [{"sub_tema": "Avance de obras del metro", "clave": "a"},
                 {"sub_tema": "Viaducto en la Primero de Mayo", "clave": "b"},
                 {"sub_tema": "Pérdidas de comerciantes de la Caracas", "clave": "c"}]
        ctx = {"a": [NOTAS["metro_a"][1]], "b": [NOTAS["metro_b"][1]],
               "c": [NOTAS["metro_c"][1]]}
        # Familias por rótulo: a+c (palabra «obras»), b sola.
        fams = refinar_familias_por_contexto(
            items, [[items[0], items[2]], [items[1]]], ctx,
            excluir=tokens_marca(BRAND, ALIASES, VOCEROS))
        claves = sorted(sorted(it["clave"] for it in f) for f in fams)
        self.assertEqual(claves, [["a", "b", "c"]])


class TestLinkExtTextoPlano(unittest.TestCase):
    def test_link_ext_sin_hipervinculo_ni_formato(self):
        url = "https://news.globalnews.com.co/Validar.aspx?id=123"
        rows = [{"Título": "Nota", "Tipo de Medio": "Online",
                 "URL (Streaming - Imagen)": url}]
        data = generate_output_excel(rows, KEY_MAP, columns_to_use=["Título", "Link Ext"])
        wb = load_workbook(io.BytesIO(data))
        celda = wb.active.cell(row=2, column=2)
        self.assertEqual(celda.value, url)
        self.assertIsNone(celda.hyperlink)
        self.assertFalse(celda.font.u)
        self.assertNotIn("0563C1", str(celda.font.color.rgb if celda.font.color else ""))

    def test_link_ext_encabezado_en_mayusculas(self):
        url = "https://news.globalnews.com.co/Validar.aspx?id=9"
        rows = [{"Título": "Nota", "Tipo de Medio": "Online",
                 "URL (Streaming - Imagen)": url}]
        data = generate_output_excel(rows, KEY_MAP, columns_to_use=["Título", "LINK EXT"])
        celda = load_workbook(io.BytesIO(data)).active.cell(row=2, column=2)
        self.assertEqual(celda.value, url)
        self.assertIsNone(celda.hyperlink)

    def test_texto_sin_titulo_en_excel(self):
        rows = [{"Título": "Avanza el metro",
                 "Contexto analizado": "Avanza  el metro. La Alcaldía de Bogotá informó avances."}]
        data = generate_output_excel(
            rows, KEY_MAP, columns_to_use=["Título", "Contexto analizado", "texto"])
        wb = load_workbook(io.BytesIO(data))
        self.assertEqual(wb.active.cell(row=2, column=3).value,
                         "La Alcaldía de Bogotá informó avances.")


if __name__ == "__main__":
    unittest.main()
