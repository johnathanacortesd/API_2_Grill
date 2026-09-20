# -*- coding: utf-8 -*-
"""Calidad de agrupación v4.5: noticias similares ⇒ mismo tema.

Casos reales del dossier Unisimón 20260920_0455:
- 'Congreso iberoamericano de suicidología' quedaba fuera de 'Prevención del
  suicidio' (variante morfológica suicidio/suicidología).
- 'Debate sobre criminalidad y IA' quedaba fuera de 'Criminología y tecnología'.
- 'Retroceso educativo y IA' quedaba fuera de 'Impacto de IA en educación'.

Anti-casos (no deben unirse):
- suicidio con 'Delitos tecnológicos y prevención' ('prevención' es genérico).
- criminología con 'Desafíos para la juventud' ('desafío' es genérico).
- suicidio juvenil con desempleo juvenil ('joven' es demográfico, no asunto).
- asuntos distintos que mencionan la marca ('unisimon' no une familias).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from analyzer_tono_tema import (
    _stems_entidad,
    asignar_temas,
    cluster_familias_subtema,
    fusionar_temas_casi_identicos,
    nz,
)


def _item(subtema, titulos):
    return {
        "sub_tema": subtema,
        "evidencia": " ".join([subtema] + list(titulos)[:4]),
        "clave": nz(subtema),
    }


def _familias_de(subs_titulos, excluir=frozenset()):
    items = [_item(s, t) for s, t in subs_titulos]
    return [[m["sub_tema"] for m in fam]
            for fam in cluster_familias_subtema(items, excluir_stems=excluir)]


def _misma_familia(fams, a, b):
    fam_a = next(f for f in fams if a in f)
    return b in fam_a


SUICIDIO = [
    ("Semana de Prevención del Suicidio",
     ["Asociación de Psiquiatría del Atlántico promueve prevención del suicidio en Barranquilla",
      "Fundación Juntos por la Vida promueve prevención del suicidio"]),
    ("Prevención del suicidio juvenil",
     ["Más de la mitad de los intentos de suicidio en Colombia corresponden a jóvenes entre 15 y 29 años"]),
    ("Congreso iberoamericano de suicidología en Barranquilla",
     ["Fortalecen estrategias para prevenir el suicidio",
      "Barranquilla acoge congreso iberoamericano de suicidología"]),
]


class TestClusteringAfinidad(unittest.TestCase):
    def test_suicidio_con_suicidologia_se_unen(self):
        fams = _familias_de(SUICIDIO)
        self.assertTrue(_misma_familia(fams, SUICIDIO[0][0], SUICIDIO[2][0]),
                        fams)
        self.assertTrue(_misma_familia(fams, SUICIDIO[1][0], SUICIDIO[2][0]),
                        fams)

    def test_debate_criminalidad_se_une_a_criminologia(self):
        fams = _familias_de([
            ("Desafíos de la criminología con IA",
             ["Criminología frente a la IA: expertos advierten sobre los nuevos delitos",
              "La IA y la criminología, nuevos delitos en masa"]),
            ("Debate sobre criminalidad y IA",
             ["Cómo enfrentar la criminalidad en tiempos de la IA"]),
            ("Cumbre internacional de criminología en Unisimón",
             ["Cumbre internacional de criminología reúne a 50 académicos"]),
        ])
        self.assertTrue(_misma_familia(
            fams, "Desafíos de la criminología con IA",
            "Debate sobre criminalidad y IA"), fams)

    def test_retroceso_educativo_se_une_a_impacto_ia_educacion(self):
        fams = _familias_de([
            ("Impacto de IA en educación",
             ["Rector de la Universidad Simón Bolívar advierte que pantallas y mal uso de la IA afectan resultados PISA"]),
            ("Retroceso educativo y IA",
             ["Pruebas PISA evidencian retroceso educativo en Colombia y preocupación por uso de inteligencia artificial"]),
        ])
        self.assertTrue(_misma_familia(
            fams, "Impacto de IA en educación", "Retroceso educativo y IA"),
            fams)

    def test_prevencion_generica_no_une_suicidio_con_delitos(self):
        fams = _familias_de(SUICIDIO + [
            ("Delitos tecnológicos y prevención",
             ["Sexta Asamblea de la Red Iberoamericana de Criminología y Ciencias Forenses"]),
        ])
        fam_suicidio = next(f for f in fams if SUICIDIO[0][0] in f)
        self.assertNotIn("Delitos tecnológicos y prevención", fam_suicidio)

    def test_desafio_generico_no_une_criminologia_con_juventud(self):
        fams = _familias_de([
            ("Desafíos de la criminología con IA",
             ["Criminología frente a la IA: expertos advierten sobre los nuevos delitos"]),
            ("Desafíos para la juventud",
             ["Desafíos para la juventud en Barranquilla: empleo y educación"]),
        ])
        self.assertFalse(_misma_familia(
            fams, "Desafíos de la criminología con IA",
            "Desafíos para la juventud"), fams)

    def test_joven_demografico_no_une_suicidio_con_desempleo(self):
        fams = _familias_de([
            ("Prevención del suicidio juvenil",
             ["Más de la mitad de los intentos de suicidio en Colombia corresponden a jóvenes"]),
            ("Desempleo juvenil en Barranquilla",
             ["Desempleo juvenil en Barranquilla llega al 18,8 %, casi el doble del promedio"]),
        ])
        self.assertFalse(_misma_familia(
            fams, "Prevención del suicidio juvenil",
            "Desempleo juvenil en Barranquilla"), fams)

    def test_marca_no_une_familias(self):
        excl = _stems_entidad({"brand": "Universidad Simón Bolívar",
                               "aliases": ["Unisimón"], "voceros": []})
        self.assertIn("unisimon", excl)
        fams = _familias_de([
            ("Inauguración de nueva sede en Puerto Colombia",
             ["El 21 de septiembre, Unisimón inicia actividades académicas en su nueva sede",
              "Unisimón estrena campus en Puerto Colombia"]),
            ("Cumbre internacional de criminología en Unisimón",
             ["Universidad Simón Bolívar reúne en Barranquilla a académicos de criminología"]),
            ("Medios ante crisis climática",
             ["V Foro de Periodismo Científico de Unisimón analizará el papel de los medios"]),
        ], excluir=excl)
        for a, b in [("Inauguración de nueva sede en Puerto Colombia",
                      "Cumbre internacional de criminología en Unisimón"),
                     ("Medios ante crisis climática",
                      "Cumbre internacional de criminología en Unisimón")]:
            self.assertFalse(_misma_familia(fams, a, b), fams)

    def test_pisa_no_se_mezcla_con_criminologia(self):
        fams = _familias_de([
            ("Retroceso educativo y IA",
             ["Pruebas PISA evidencian retroceso educativo en Colombia"]),
            ("Desafíos de la criminología con IA",
             ["Criminología frente a la IA: expertos advierten"]),
        ])
        self.assertFalse(_misma_familia(
            fams, "Retroceso educativo y IA",
            "Desafíos de la criminología con IA"), fams)


class TestFusionTemasCasiIdenticos(unittest.TestCase):
    def _temas(self, pares):
        temas = {i + 1: t for i, (t, _) in enumerate(pares)}
        etiquetas = {i + 1: {"sub_tema": s} for i, (_, s) in enumerate(pares)}
        return temas, etiquetas

    def test_fusiona_nombres_casi_identicos(self):
        temas, etiquetas = self._temas([
            ("Impacto de inteligencia artificial en educación",
             "Retroceso educativo y IA"),
            ("Impacto de educación", "Impacto de IA en educación"),
        ])
        origen = {1: "familia:1", 2: "familia:2"}
        n = fusionar_temas_casi_identicos(temas, origen, etiquetas)
        self.assertGreater(n, 0)
        self.assertEqual(nz(temas[1]), nz(temas[2]))

    def test_no_fusiona_temas_distintos(self):
        temas, etiquetas = self._temas([
            ("Atención en salud pública", "Ruta Nacional del Cuidado"),
            ("Eventos de salud mental", "Simposio y Velatón en Barranquilla"),
            ("Prevención del suicidio", "Semana de Prevención del Suicidio"),
        ])
        origen = {1: "familia:1", 2: "familia:2", 3: "familia:3"}
        antes = dict(temas)
        n = fusionar_temas_casi_identicos(temas, origen, etiquetas)
        self.assertEqual(n, 0)
        self.assertEqual(temas, antes)


class TestMayoriaNoFragmenta(unittest.TestCase):
    def _grupo(self, gid, titulo, texto=""):
        return {"grupo": gid, "n": 1, "idxs": [gid - 1], "titulo": titulo,
                "titulos_alt": [], "texto": texto or titulo,
                "contexto": texto or titulo}

    def test_minoria_sin_respaldo_conserva_tema_de_la_familia(self):
        # La familia es de juventud/empleo; el guard léxico no respalda a la
        # minoría, pero el LLM nombró por semántica: no se fragmenta.
        subs = ["Desafíos para la juventud", "Desempleo juvenil en Barranquilla",
                "Barreras para estudiar y trabajar", "Ascenso político de Gutiérrez",
                "Proyecto de paz barrial"]
        grupos = [self._grupo(i + 1, s) for i, s in enumerate(subs)]
        etiquetas = {i + 1: {"sub_tema": s, "tono": "Neutro"}
                     for i, s in enumerate(subs)}
        fam = [[{"sub_tema": s, "clave": nz(s)} for s in subs]]
        with patch("analyzer_tono_tema.cluster_familias_subtema",
                   return_value=fam):
            with patch("analyzer_tono_tema.nombrar_familias_tema",
                       return_value={1: "Desafíos sociales y empleo juvenil"}):
                temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
        unicos = {nz(temas[i + 1]) for i in range(5)}
        self.assertEqual(len(unicos), 1, temas)
        self.assertEqual(unicos.pop(), nz("Desafíos sociales y empleo juvenil"))

    def test_mayoria_respaldada_separa_al_ajeno(self):
        # Caso Gutiérrez: 3 respaldan 'Prevención del suicidio', el 4º no.
        subs = ["Semana de Prevención del Suicidio",
                "Prevención del suicidio juvenil",
                "Intentos de suicidio en jóvenes",
                "Ascenso político de Gutiérrez"]
        tits = ["Asociación de Psiquiatría promueve prevención del suicidio",
                "Intentos de suicidio en jóvenes aumentan",
                "Aumentan intentos de suicidio en jóvenes",
                "Gutiérrez anuncia su candidatura a la alcaldía"]
        grupos = [self._grupo(i + 1, t) for i, t in enumerate(tits)]
        etiquetas = {i + 1: {"sub_tema": s, "tono": "Neutro"}
                     for i, s in enumerate(subs)}
        fam = [[{"sub_tema": s, "clave": nz(s)} for s in subs]]
        with patch("analyzer_tono_tema.cluster_familias_subtema",
                   return_value=fam):
            with patch("analyzer_tono_tema.nombrar_familias_tema",
                       return_value={1: "Prevención del suicidio"}):
                temas, origen = asignar_temas({}, grupos, etiquetas,
                                              {"temas": []})
        for i in range(3):
            self.assertEqual(nz(temas[i + 1]), nz("Prevención del suicidio"))
            self.assertTrue(str(origen[i + 1]).startswith("familia:"))
        self.assertNotEqual(nz(temas[4]), nz("Prevención del suicidio"))
        self.assertFalse(str(origen[4]).startswith("familia:"))


if __name__ == "__main__":
    unittest.main()
