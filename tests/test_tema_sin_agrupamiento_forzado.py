# -*- coding: utf-8 -*-
"""Sin agrupamiento forzado: si el tema de la familia no describe la noticia,
el miembro recibe un tema propio generado desde su subtema/titular/contexto.

Caso reportado: "Prevención del suicidio" aparecía en una noticia de
"Ascenso político de Gutiérrez" que no habla de suicidio en ninguna parte.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from analyzer_tono_tema import (
    _tema_distinto_de_subtema,
    _tema_relevante_para_miembro,
    asignar_temas,
    nz,
    tema_frase_natural,
)


def _grupo(gid, titulo, texto="", contexto=""):
    return {
        "grupo": gid,
        "n": 1,
        "idxs": [gid - 1],
        "titulo": titulo,
        "titulos_alt": [],
        "texto": texto or titulo,
        "contexto": contexto or texto or titulo,
    }


def _familia_forzada(subtemas):
    """Simula el agrupamiento forzado: todos los subtemas en UNA familia."""
    return [[{"sub_tema": s, "clave": nz(s)} for s in subtemas]]


SUICIDIO = ("Intentos de suicidio en jóvenes",
            "Aumentan los intentos de suicidio en jóvenes",
            "El informe de salud alerta por el aumento de intentos de "
            "suicidio en la población joven del departamento.")


class TestSinAgrupamientoForzado(unittest.TestCase):
    def test_miembro_ajeno_no_hereda_tema_de_la_familia(self):
        sub2, tit2, ctx2 = ("Feria gastronómica del Caribe",
                            "Anuncian feria gastronómica del Caribe en Barranquilla",
                            "La feria reunirá a 50 restaurantes de la región Caribe.")
        grupos = [_grupo(1, SUICIDIO[1], SUICIDIO[2]),
                  _grupo(2, tit2, ctx2)]
        etiquetas = {
            1: {"sub_tema": SUICIDIO[0], "tono": "Neutro"},
            2: {"sub_tema": sub2, "tono": "Neutro"},
        }
        with patch("analyzer_tono_tema.cluster_familias_subtema",
                   return_value=_familia_forzada([SUICIDIO[0], sub2])):
            temas, origen = asignar_temas({}, grupos, etiquetas, {"temas": []})
        # El miembro genuino conserva el tema canónico de la familia.
        self.assertEqual(nz(temas[1]), nz("Prevención del suicidio"))
        self.assertTrue(str(origen[1]).startswith("familia:"))
        # El miembro ajeno NO hereda el tema: recibe uno propio acorde a su noticia.
        self.assertNotEqual(nz(temas[2]), nz("Prevención del suicidio"))
        self.assertEqual(origen[2], "tema_propio_sin_agrupar")
        self.assertTrue(tema_frase_natural(temas[2]), temas[2])
        self.assertTrue(_tema_distinto_de_subtema(temas[2], sub2))

    def test_caso_reportado_gutierrez(self):
        sub2, tit2, ctx2 = ("Ascenso político de Gutiérrez",
                            "Gutiérrez anuncia su candidatura a la alcaldía",
                            "El exconcejal Gutiérrez presentó su aspiración a la "
                            "alcaldía con una propuesta centrada en empleo y seguridad.")
        grupos = [_grupo(1, SUICIDIO[1], SUICIDIO[2]),
                  _grupo(2, tit2, ctx2)]
        etiquetas = {
            1: {"sub_tema": SUICIDIO[0], "tono": "Neutro"},
            2: {"sub_tema": sub2, "tono": "Neutro"},
        }
        with patch("analyzer_tono_tema.cluster_familias_subtema",
                   return_value=_familia_forzada([SUICIDIO[0], sub2])):
            temas, origen = asignar_temas({}, grupos, etiquetas, {"temas": []})
        self.assertNotEqual(nz(temas[2]), nz("Prevención del suicidio"))
        self.assertTrue(tema_frase_natural(temas[2]), temas[2])
        self.assertFalse(str(origen[2]).startswith("familia:"),
                         "no debe heredar el tema de la familia forzada")

    def test_familia_legitima_no_se_fragmenta(self):
        grupos = [
            _grupo(1, "Más de la mitad de los intentos de suicidio son de jóvenes",
                   "El informe alerta por intentos de suicidio en jóvenes."),
            _grupo(2, "Congreso iberoamericano de suicidología en la universidad",
                   "Expertos se reúnen en el congreso iberoamericano de suicidología."),
        ]
        etiquetas = {
            1: {"sub_tema": "Intentos de suicidio en jóvenes", "tono": "Neutro"},
            2: {"sub_tema": "Congreso iberoamericano de suicidología", "tono": "Neutro"},
        }
        temas, origen = asignar_temas({}, grupos, etiquetas, {"temas": []})
        self.assertEqual(nz(temas[1]), nz("Prevención del suicidio"))
        self.assertEqual(nz(temas[2]), nz("Prevención del suicidio"))
        self.assertTrue(str(origen[1]).startswith("familia:"))
        self.assertTrue(str(origen[2]).startswith("familia:"))

    def test_singleton_con_tema_propio_valido(self):
        grupos = [
            _grupo(1, "Gutiérrez anuncia su candidatura a la alcaldía",
                   "El exconcejal Gutiérrez presentó su aspiración a la alcaldía."),
        ]
        etiquetas = {
            1: {"sub_tema": "Ascenso político de Gutiérrez", "tono": "Neutro"},
        }
        temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
        self.assertTrue(tema_frase_natural(temas[1]), temas[1])
        self.assertNotEqual(nz(temas[1]), nz("Prevención del suicidio"))


if __name__ == "__main__":
    unittest.main()
