# -*- coding: utf-8 -*-
"""v4.31: título igual o similar ⇒ misma noticia ⇒ mismo tono, tema y subtema.

`unificar_etiquetas_titulos_similares` es el pase FINAL del pipeline (tras
guardas, temas y PKL) y une a nivel de FILA: las unificaciones anteriores
comparan representantes de grupo y no ven el par cuando un grupo grande
absorbe por cuerpo/contexto un titular casi idéntico al de otro grupo.

Casos reales del dossier Colpensiones 2026-09-28 (input
Base_Modelo_-_Colpensiones_-_jc.xlsx):
- TP1: «Gobierno avanza en ruta para garantizar el pago de pensiones»
  (dentro de un grupo de 50) vs «Gobierno avanza en soluciones para cumplir
  con el pago de las pensiones de final de año» (grupo de 6): token_set 84.6
  → quedaban con distinto tono, tema y subtema.
- TP2: «¿Se pueden comprar semanas de cotización para lograr la pensión por
  vejez? Esto señala la ley» vs «¿Se pueden comprar semanas para pensionarse
  en Colombia? Esto dice la reforma pensional»: token_set 71.2, 4 palabras
  de contenido en común → banda 70–80.

Anti-casos (no deben unirse):
- «De la Espriella nombra a Beatriz Vélez…» vs «De la Espriella pide
  renuncia al presidente…»: token_set 69.7, 2 palabras → fuera.
- «Director de Colpensiones» vs «Relevo en Colpensiones»: 1 palabra en
  común → fuera.
- «Noticias del 26 de septiembre» vs «Esto es lo que cambia con la reforma
  pensional: Casa Blu del 26 de septiembre»: token_set 81.6 pero el puente
  son solo {26, septiembre} (fecha) → fuera.
"""
from __future__ import annotations

import unittest

from analyzer_tono_tema import unificar_etiquetas_titulos_similares


def _run(titulos, por_grupo_etiquetas, por_grupo_temas, gids,
         incluir_tema=True, duplicates=()):
    rows = [{"Título": t} for t in titulos]
    for d in duplicates:
        rows[d]["is_duplicate"] = True
    mapa = {i: g for i, g in enumerate(gids)}
    etiquetas = {g: dict(e) for g, e in por_grupo_etiquetas.items()}
    temas = dict(por_grupo_temas)
    n = unificar_etiquetas_titulos_similares(
        rows, mapa, etiquetas, temas, {}, incluir_tema=incluir_tema)
    return n, etiquetas, temas


TP1_A = "Gobierno avanza en ruta para garantizar el pago de pensiones"
TP1_B = ("Gobierno avanza en soluciones para cumplir con el pago de las "
         "pensiones de final de año")
TP2_A = ("¿Se pueden comprar semanas de cotización para lograr la pensión "
         "por vejez? Esto señala la ley")
TP2_B = ("¿Se pueden comprar semanas para pensionarse en Colombia? Esto dice "
         "la reforma pensional")
ANTI_A = ("De la Espriella nombra a Beatriz Vélez como nueva presidenta "
          "de Colpensiones")
ANTI_B = ("De La Espriella pide renuncia al presidente de Colpensiones "
          "Carlos René Montoya")


class TestUnificarEtiquetasTitulosSimilaresV431(unittest.TestCase):
    def test_tp1_colpensiones_unifica_tono_tema_subtema(self):
        # 1 fila en grupo 1 (minoría) + 2 filas en grupo 2 (mayoría).
        n, et, te = _run(
            [TP1_A, TP1_B, TP1_B],
            {1: {"tono": "Positivo", "sub_tema": "Garantía de pagos pensionales"},
             2: {"tono": "Neutro", "sub_tema": "Soluciones para pago de pensiones"}},
            {1: "Entorno Pensional", 2: "Tipos de Pensión"},
            [1, 2, 2])
        self.assertGreater(n, 0)
        for g in (1, 2):
            self.assertEqual(et[g]["tono"], "Neutro")
            self.assertEqual(et[g]["sub_tema"], "Soluciones para pago de pensiones")
            self.assertEqual(te[g], "Tipos de Pensión")

    def test_tp2_banda_70_80_con_3_palabras_unifica(self):
        # Mayoría por FILA: 1 fila (grupo 1) vs 2 filas (grupo 2) → gana el
        # bloque mayoritario.
        n, et, te = _run(
            [TP2_A, TP2_B, TP2_B],
            {1: {"tono": "Neutro", "sub_tema": "Compra de semanas cotizadas"},
             2: {"tono": "Positivo", "sub_tema": "Reforma pensional y compra de semanas"}},
            {1: "Normativa y Regulación", 2: "Afiliación y Cotización"},
            [1, 2, 2])
        self.assertGreater(n, 0)
        for g in (1, 2):
            self.assertEqual(et[g]["sub_tema"], "Reforma pensional y compra de semanas")
            self.assertEqual(te[g], "Afiliación y Cotización")
            self.assertEqual(et[g]["tono"], "Positivo")

    def test_anti_nombramiento_vs_renuncia_no_se_une(self):
        n, et, te = _run(
            [ANTI_A, ANTI_B],
            {1: {"tono": "Positivo", "sub_tema": "Nominación de nueva presidenta"},
             2: {"tono": "Neutro", "sub_tema": "Renuncia del presidente de Colpensiones"}},
            {1: "Institucional", 2: "Institucional"},
            [1, 2])
        self.assertEqual(n, 0)
        self.assertEqual(et[1]["sub_tema"], "Nominación de nueva presidenta")
        self.assertEqual(et[2]["sub_tema"], "Renuncia del presidente de Colpensiones")

    def test_anti_una_palabra_en_comun_no_se_une(self):
        n, et, _te = _run(
            ["Director de Colpensiones", "Relevo en Colpensiones"],
            {1: {"tono": "Neutro", "sub_tema": "Director de Colpensiones"},
             2: {"tono": "Neutro", "sub_tema": "Relevo en Colpensiones"}},
            {1: "Institucional", 2: "Institucional"},
            [1, 2])
        self.assertEqual(n, 0)
        self.assertEqual(et[1]["sub_tema"], "Director de Colpensiones")
        self.assertEqual(et[2]["sub_tema"], "Relevo en Colpensiones")

    def test_anti_fecha_no_es_puente(self):
        n, et, _te = _run(
            ["Noticias del 26 de septiembre",
             "Esto es lo que cambia con la reforma pensional: "
             "Casa Blu del 26 de septiembre"],
            {1: {"tono": "Neutro", "sub_tema": "Cobertura de noticias del día"},
             2: {"tono": "Neutro", "sub_tema": "Cambios en reforma pensional"}},
            {1: "Institucional", 2: "Normativa y Regulación"},
            [1, 2])
        self.assertEqual(n, 0)
        self.assertEqual(et[1]["sub_tema"], "Cobertura de noticias del día")
        self.assertEqual(et[2]["sub_tema"], "Cambios en reforma pensional")

    def test_negativo_no_se_borra_por_voto_pero_subtema_si(self):
        n, et, _te = _run(
            [TP1_A, TP1_B],
            {1: {"tono": "Negativo", "sub_tema": "Garantía de pagos pensionales"},
             2: {"tono": "Neutro", "sub_tema": "Soluciones para pago de pensiones"}},
            {1: "Entorno Pensional", 2: "Tipos de Pensión"},
            [1, 2])
        self.assertGreater(n, 0)
        # El señalamiento deliberado no se borra por voto…
        self.assertEqual(et[1]["tono"], "Negativo")
        self.assertEqual(et[2]["tono"], "Neutro")
        # …pero el hecho sí se unifica.
        self.assertEqual(et[1]["sub_tema"], et[2]["sub_tema"])

    def test_empate_subtema_gana_mas_largo_y_tono_neutro(self):
        n, et, _te = _run(
            [TP1_A, TP1_B],
            {1: {"tono": "Positivo", "sub_tema": "Pagos"},
             2: {"tono": "Neutro", "sub_tema": "Soluciones para pago de pensiones"}},
            {1: "Entorno Pensional", 2: "Tipos de Pensión"},
            [1, 2])
        self.assertGreater(n, 0)
        self.assertEqual(et[1]["sub_tema"], "Soluciones para pago de pensiones")
        self.assertEqual(et[2]["sub_tema"], "Soluciones para pago de pensiones")
        self.assertEqual(et[1]["tono"], "Neutro")
        self.assertEqual(et[2]["tono"], "Neutro")

    def test_duplicadas_no_participan(self):
        n, et, _te = _run(
            [TP1_A, TP1_A],
            {1: {"tono": "Positivo", "sub_tema": "Garantía de pagos pensionales"},
             2: {"tono": "Neutro", "sub_tema": "Soluciones para pago de pensiones"}},
            {1: "Entorno Pensional", 2: "Tipos de Pensión"},
            [1, 2],
            duplicates=(1,))
        self.assertEqual(n, 0)
        self.assertEqual(et[1]["tono"], "Positivo")
        self.assertEqual(et[2]["tono"], "Neutro")

    def test_sin_tema_no_falla(self):
        n, et, te = _run(
            [TP1_A, TP1_B],
            {1: {"tono": "Positivo", "sub_tema": "Garantía de pagos pensionales"},
             2: {"tono": "Positivo", "sub_tema": "Soluciones para pago de pensiones"}},
            {},
            [1, 2],
            incluir_tema=False)
        self.assertGreater(n, 0)
        self.assertEqual(te, {})
        self.assertEqual(et[1]["sub_tema"], et[2]["sub_tema"])


if __name__ == "__main__":
    unittest.main()
