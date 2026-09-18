# -*- coding: utf-8 -*-
"""Invariantes de tema/subtema del lote del día (sin llamadas a API)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from catalogo_tono_tema import TAX_GOBIERNO, TEMAS_EJEMPLO_BUENOS, TEMAS_EJEMPLO_MALOS
from analyzer_tono_tema import (
    _tema_distinto_de_subtema,
    asignar_temas,
    canonizar_subtemas,
    construir_grupos,
    corregir_temas_con_jev,
    cubo_valido,
    enrich_rows_with_ai,
    forzar_un_tema_por_subtema,
    generalizar_tema_desde_subtemas,
    nombrar_familias_tema,
    nz,
    problemas_calidad_tema,
    taxonomia_por_nombre,
    tema_frase_natural,
    unificar_subtemas_noticias_similares,
    volcar_analisis_en_filas,
)


KM = {"titulo": "Título"}


def _row(titulo, cuerpo, dup=False):
    return {
        "Título": titulo,
        "CuerpoEs": cuerpo,
        "is_duplicate": dup,
    }


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


PAE_CUERPO = (
    "La Alcaldía de Soledad fortalece la nutrición escolar con el PAE. "
    "El programa de alimentación escolar entrega desayunos y almuerzos a los "
    "estudiantes desde el primer día de clases y hace seguimiento nutricional "
    "en todas las sedes educativas del municipio durante el calendario escolar. "
    "Las secretarías de Educación y de Salud coordinan la operación del PAE "
    "con operadores, rectores y madres comunitarias para garantizar cobertura."
)


class TestClusteringNoticiasSimilares(unittest.TestCase):
    def test_noticias_similares_comparten_grupo(self):
        rows = [
            _row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO),
            _row("Soledad pone la nutrición escolar y el PAE fortalece el seguimiento", PAE_CUERPO),
            _row("Roban 180 mil huevos en una granja del Atlántico",
                 "Hombres armados se llevaron huevos de una granja avícola del Atlántico. "
                 "La Policía recuperó dos camiones horas después del hurto en Sabanalarga."),
        ]
        grupos, mapa = construir_grupos(rows, KM)
        self.assertEqual(mapa[0], mapa[1])
        self.assertNotEqual(mapa[0], mapa[2])
        self.assertEqual(len(grupos), 2)


class TestCanonizacionSubtema(unittest.TestCase):
    def test_variantes_del_mismo_hecho_quedan_en_un_subtema(self):
        etiquetas = {
            1: {"sub_tema": "Sanción a exsecretario de Educación", "tono": "Negativo"},
            2: {"sub_tema": "Sancion a exsecretario de educacion", "tono": "Negativo"},
            3: {"sub_tema": "Sanción al exsecretario de Educación", "tono": "Negativo"},
        }
        canonizar_subtemas(etiquetas)
        self.assertEqual(len({nz(e["sub_tema"]) for e in etiquetas.values()}), 1)

    def test_hechos_distintos_por_ciudad_no_se_fusionan(self):
        etiquetas = {
            1: {"sub_tema": "Obras en Sincelejo", "tono": "Neutro"},
            2: {"sub_tema": "Obras en Montería", "tono": "Neutro"},
        }
        canonizar_subtemas(etiquetas)
        self.assertEqual(len({nz(e["sub_tema"]) for e in etiquetas.values()}), 2)

    def test_noticias_similares_separadas_unifican_subtema(self):
        grupos = [
            _grupo(1, "Soledad fortalece la nutrición escolar con el PAE"),
            _grupo(2, "Soledad pone la nutrición escolar y el PAE fortalece el seguimiento"),
        ]
        etiquetas = {
            1: {"sub_tema": "Fortalecimiento del PAE en Soledad", "tono": "Positivo"},
            2: {"sub_tema": "Seguimiento nutricional del PAE", "tono": "Positivo"},
        }
        unificar_subtemas_noticias_similares(grupos, etiquetas, umbral=80)
        self.assertEqual(nz(etiquetas[1]["sub_tema"]), nz(etiquetas[2]["sub_tema"]))


class TestTemaBottomUp(unittest.TestCase):
    def test_mismo_subtema_canonico_nunca_tiene_dos_temas(self):
        grupos = [
            _grupo(1, "Procuraduría suspende a exsecretario por demoras en el PAE",
                   "La Procuraduría suspende al exsecretario de Educación por demoras en el PAE."),
            _grupo(2, "Sancionan a exsecretario de Educación por retraso en el PAE",
                   "Sancionan al exsecretario de Educación de Sucre por demora en el PAE."),
        ]
        etiquetas = {
            1: {"sub_tema": "Sanción por retraso en el PAE", "tono": "Negativo"},
            2: {"sub_tema": "Sanción por retraso en el PAE", "tono": "Negativo"},
        }
        with patch("analyzer_tono_tema.llamar_llm", side_effect=RuntimeError("sin api")):
            with patch("analyzer_tono_tema.taxonomia_por_nombre") as mock_tax:
                temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
                mock_tax.assert_not_called()
        self.assertEqual(nz(temas[1]), nz(temas[2]))
        self.assertTrue(_tema_distinto_de_subtema(temas[1], etiquetas[1]["sub_tema"]))

    def test_forzar_un_tema_por_subtema_repara_asignacion_partida(self):
        etiquetas = {
            1: {"sub_tema": "Exportación de pollo a Japón", "tono": "Positivo"},
            2: {"sub_tema": "Exportación de pollo a Japón", "tono": "Positivo"},
        }
        temas = {1: "Mercados internacionales", 2: "Agenda avícola"}
        cambios = forzar_un_tema_por_subtema(temas, etiquetas)
        self.assertGreaterEqual(cambios, 1)
        self.assertEqual(nz(temas[1]), nz(temas[2]))

    def test_tema_no_es_igual_ni_casi_igual_al_subtema(self):
        sub = "Inicio de clases con alimentación escolar"
        grupos = [_grupo(1, "40 mil niños inician clases con alimentación escolar desde el primer día",
                         "El PAE entrega alimentación escolar desde el primer día de clases.")]
        etiquetas = {1: {"sub_tema": sub, "tono": "Positivo"}}
        temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
        self.assertTrue(_tema_distinto_de_subtema(temas[1], sub))
        self.assertNotEqual(nz(temas[1]), nz(sub))

    def test_subtemas_afines_comparten_tema_en_el_lote(self):
        grupos = [
            _grupo(1, "Inicio de clases con alimentación escolar del PAE",
                   "El PAE cubre alimentación escolar desde el primer día."),
            _grupo(2, "Fortalecimiento del PAE escolar en Soledad",
                   "Soledad fortalece el PAE y la nutrición escolar."),
        ]
        etiquetas = {
            1: {"sub_tema": "Inicio de clases con alimentación escolar", "tono": "Positivo"},
            2: {"sub_tema": "Fortalecimiento del PAE escolar", "tono": "Positivo"},
        }
        temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
        self.assertEqual(nz(temas[1]), nz(temas[2]))
        self.assertNotEqual(nz(etiquetas[1]["sub_tema"]), nz(etiquetas[2]["sub_tema"]))

    def test_lote_no_reutiliza_vocabulario_de_otra_corrida(self):
        g1 = [_grupo(1, "Inicio de clases con alimentación escolar del PAE",
                     "Alimentación escolar y PAE desde el primer día.")]
        e1 = {1: {"sub_tema": "Inicio de clases con alimentación escolar", "tono": "Positivo"}}
        with patch("analyzer_tono_tema.taxonomia_por_nombre") as mock_tax:
            t1, _ = asignar_temas({}, g1, e1, {"temas": []})
            mock_tax.assert_not_called()
        tema_pae = t1[1]

        g2 = [_grupo(1, "Estudiantes ganan el concurso nacional de robótica",
                     "El equipo de robótica ganó el concurso nacional.")]
        e2 = {1: {"sub_tema": "Estudiantes ganan concurso de robótica", "tono": "Positivo"}}
        with patch("analyzer_tono_tema.taxonomia_por_nombre") as mock_tax:
            t2, _ = asignar_temas({}, g2, e2, {"temas": []})
            mock_tax.assert_not_called()
        self.assertNotEqual(nz(t2[1]), nz(tema_pae))
        cerrados = {nz(t) for t in TAX_GOBIERNO["temas"]}
        self.assertNotIn(nz(t2[1]), cerrados)

    def test_subset_general_es_valido_como_tema(self):
        self.assertTrue(_tema_distinto_de_subtema(
            "Alimentación escolar", "Inicio de clases con alimentación escolar"))
        self.assertFalse(_tema_distinto_de_subtema(
            "Inicio de clases con alimentación escolar",
            "Inicio de clases con alimentación escolar"))
        self.assertFalse(_tema_distinto_de_subtema(
            "Sanción por retraso en el PAE de Sucre",
            "Sanción por retraso en el PAE"))


class TestVolcadoSinReasignacionPorFila(unittest.TestCase):
    def test_filas_del_mismo_grupo_conservan_el_mismo_tema(self):
        rows = [
            _row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO),
            _row("Soledad pone la nutrición escolar y el PAE fortalece el seguimiento", PAE_CUERPO),
            _row("Duplicada PAE", PAE_CUERPO, dup=True),
        ]
        mapa = {0: 1, 1: 1}
        etiquetas = {1: {"sub_tema": "Fortalecimiento del PAE escolar", "tono": "Positivo"}}
        temas = {1: "Alimentación escolar y PAE"}
        volcar_analisis_en_filas(rows, mapa, etiquetas, temas)
        self.assertEqual(rows[0]["Subtema_IA"], rows[1]["Subtema_IA"])
        self.assertEqual(rows[0]["Tema_IA"], rows[1]["Tema_IA"])
        self.assertEqual(rows[0]["Tema_IA"], "Alimentación escolar y PAE")
        self.assertEqual(rows[2]["Tono_IA"], "Duplicada")
        self.assertEqual(rows[2]["Tema_IA"], "-")
        self.assertEqual(rows[2]["Subtema_IA"], "-")


class TestJevNoTocaSubtemaNiTono(unittest.TestCase):
    def test_corrector_jev_cambia_tema_pero_no_subtema(self):
        grupos = [_grupo(1, "Acreditación de alta calidad por ocho años",
                         "La universidad recibió acreditación de alta calidad.")]
        etiquetas = {1: {"sub_tema": "Acreditación de alta calidad", "tono": "Positivo"}}
        temas = {1: "Acreditación de alta calidad"}
        cfg = {"typesafe_api_key": "test"}
        with patch("analyzer_tono_tema._tema_con_jev", return_value={
            "cubre": False,
            "demasiado_especifico": True,
            "confianza_cubre": 0.95,
            "confianza_especifico": 0.95,
        }):
            corregir_temas_con_jev(cfg, grupos, etiquetas, temas)
        self.assertEqual(etiquetas[1]["sub_tema"], "Acreditación de alta calidad")
        self.assertEqual(etiquetas[1]["tono"], "Positivo")
        self.assertTrue(_tema_distinto_de_subtema(temas[1], etiquetas[1]["sub_tema"]))

    def test_baja_confianza_deja_tema_y_marca_revision(self):
        grupos = [_grupo(1, "Acreditación de alta calidad por ocho años")]
        etiquetas = {1: {"sub_tema": "Acreditación de alta calidad", "tono": "Positivo"}}
        temas = {1: "Educación superior"}
        original = temas[1]
        with patch("analyzer_tono_tema._tema_con_jev", return_value={
            "cubre": False,
            "demasiado_especifico": False,
            "confianza_cubre": 0.20,
            "confianza_especifico": 0.10,
        }):
            from analyzer_tono_tema import ultimo_resumen
            corregir_temas_con_jev({"typesafe_api_key": "test"}, grupos, etiquetas, temas)
            self.assertEqual(temas[1], original)
            self.assertIn(1, ultimo_resumen().get("temas_para_revision") or [])


class TestEnrichLoteInvariantes(unittest.TestCase):
    def test_enrich_propaga_subtema_y_un_solo_tema(self):
        rows = [
            _row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO),
            _row("Soledad pone la nutrición escolar y el PAE fortalece el seguimiento", PAE_CUERPO),
            _row("Estudiantes ganan el concurso nacional de robótica",
                 "El equipo de estudiantes ganó el concurso nacional de robótica con un prototipo autónomo."),
        ]

        def fake_etq(cfg, grupos, *args, **kwargs):
            out = {}
            for g in grupos:
                t = nz(g["titulo"])
                if "pae" in t or "nutricion" in t:
                    out[g["grupo"]] = {
                        "sub_tema": "Fortalecimiento del PAE escolar",
                        "tono": "Positivo",
                    }
                else:
                    out[g["grupo"]] = {
                        "sub_tema": "Estudiantes ganan concurso de robótica",
                        "tono": "Positivo",
                    }
            return out

        with patch("analyzer_tono_tema.etiquetar_grupos", side_effect=fake_etq), \
             patch("analyzer_tono_tema.llamar_llm", side_effect=RuntimeError("sin api")), \
             patch("analyzer_tono_tema.taxonomia_por_nombre", wraps=taxonomia_por_nombre) as mock_tax, \
             patch("analyzer_tono_tema.proponer_taxonomia") as mock_prop:
            enrich_rows_with_ai(
                rows, KM, "Soledad", [], api_key="", extra={"votos": 1, "taxonomia": "Automática según el archivo"},
            )
            mock_prop.assert_not_called()
            mock_tax.assert_not_called()

        pae = [r for r in rows if "PAE" in r["Título"] or "nutrición" in r["Título"].lower()]
        robot = [r for r in rows if "robótica" in r["Título"]]
        self.assertEqual(len({r["Subtema_IA"] for r in pae}), 1)
        self.assertEqual(len({r["Tema_IA"] for r in pae}), 1)
        self.assertTrue(_tema_distinto_de_subtema(pae[0]["Tema_IA"], pae[0]["Subtema_IA"]))
        self.assertNotEqual(nz(pae[0]["Tema_IA"]), nz(robot[0]["Tema_IA"]))
        self.assertNotEqual(nz(pae[0]["Subtema_IA"]), nz(robot[0]["Subtema_IA"]))

    def test_generalizar_no_copia_el_subtema(self):
        sub = "Sanción por retraso en el PAE"
        tema = generalizar_tema_desde_subtemas(
            [sub],
            ["Sancionan a exsecretario de Educación por demora en el PAE"],
            ["La Procuraduría suspende al exsecretario por retraso en el PAE."],
        )
        self.assertTrue(_tema_distinto_de_subtema(tema, sub))


class TestCalidadLinguisticaTema(unittest.TestCase):
    MALOS = ("Jóvenes empleo", "Sede Puerto Inauguración", "Nueva Puerto sede n",
             "Desempleo juventud")
    BUENOS = ("Empleo juvenil", "Inauguración de sede", "Alimentación escolar",
              "Concurso de robótica")

    def test_rechaza_uniones_de_keywords(self):
        for malo in self.MALOS:
            self.assertFalse(tema_frase_natural(malo), malo)
            self.assertIsNone(cubo_valido(malo, {"temas": []}, True), malo)

    def test_acepta_frases_nominales_naturales(self):
        for bueno in self.BUENOS:
            self.assertTrue(tema_frase_natural(bueno), bueno)
            self.assertEqual(cubo_valido(bueno, {"temas": []}, True), bueno)

    def test_generalizar_no_emite_jovenes_empleo(self):
        tema = generalizar_tema_desde_subtemas(
            ["Informe sobre juventud y desempleo"],
            ["Cifras de desempleo en jóvenes de la región"],
        )
        self.assertNotEqual(nz(tema), nz("Jóvenes empleo"))
        self.assertNotEqual(nz(tema), nz("Desempleo juventud"))
        self.assertTrue(tema_frase_natural(tema), tema)
        self.assertTrue(_tema_distinto_de_subtema(tema, "Informe sobre juventud y desempleo"))

    def test_generalizar_no_emite_sede_puerto_inauguracion(self):
        tema = generalizar_tema_desde_subtemas(
            ["Inauguración de sede en Puerto"],
            ["Inauguran la nueva sede en Puerto"],
        )
        self.assertNotEqual(nz(tema), nz("Sede Puerto Inauguración"))
        self.assertNotEqual(nz(tema), nz("Inauguración Puerto"))
        self.assertTrue(tema_frase_natural(tema), tema)
        self.assertIn("de", tema.lower())
        self.assertTrue(_tema_distinto_de_subtema(tema, "Inauguración de sede en Puerto"))

    def test_asignar_temas_jovenes_no_es_keyword_union(self):
        grupos = [_grupo(1, "Cifras de desempleo en jóvenes de la región",
                         "El informe presenta cifras de desempleo en jóvenes.")]
        etiquetas = {1: {"sub_tema": "Informe sobre juventud y desempleo", "tono": "Neutro"}}
        temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
        self.assertTrue(tema_frase_natural(temas[1]), temas[1])
        self.assertNotEqual(nz(temas[1]), nz("Jóvenes empleo"))

    def test_asignar_temas_inauguracion_no_es_keyword_union(self):
        grupos = [_grupo(1, "Inauguran la nueva sede en Puerto",
                         "Quedó inaugurada la nueva sede en Puerto.")]
        etiquetas = {1: {"sub_tema": "Inauguración de sede en Puerto", "tono": "Positivo"}}
        temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
        self.assertTrue(tema_frase_natural(temas[1]), temas[1])
        self.assertNotEqual(nz(temas[1]), nz("Sede Puerto Inauguración"))
        self.assertIn("inaugur", nz(temas[1]))

    def test_llm_keyword_union_se_descarta(self):
        familias = [{
            "id": 1,
            "subtemas": ["Informe sobre juventud y desempleo"],
            "titulos": ["Cifras de desempleo en jóvenes de la región"],
            "contextos": ["El informe presenta cifras de desempleo en jóvenes."],
        }]
        with patch("analyzer_tono_tema.llamar_llm",
                   return_value='{"resultados":[{"id":1,"tema":"Jóvenes empleo"}]}'):
            out = nombrar_familias_tema({"api_key": "test"}, familias)
        self.assertNotEqual(nz(out[1]), nz("Jóvenes empleo"))
        self.assertTrue(tema_frase_natural(out[1]), out[1])


class TestCalidadTemaLote24(unittest.TestCase):
    """Barra de calidad del lote real: los malos no salen; los buenos sí pasan."""

    def test_rechaza_exactos_inaceptables(self):
        for malo in TEMAS_EJEMPLO_MALOS:
            self.assertFalse(tema_frase_natural(malo), malo)
            self.assertTrue(problemas_calidad_tema(malo), malo)
            self.assertIsNone(cubo_valido(malo, {"temas": []}, True), malo)

    def test_acepta_exactos_aceptables(self):
        for bueno in TEMAS_EJEMPLO_BUENOS:
            self.assertFalse(problemas_calidad_tema(bueno), bueno)
            self.assertTrue(tema_frase_natural(bueno), bueno)
            self.assertEqual(cubo_valido(bueno, {"temas": []}, True), bueno)

    def test_no_recorta_obras_de_manejo(self):
        tema = generalizar_tema_desde_subtemas(
            ["Obras de manejo ambiental en la ciénaga"],
            ["Obras de manejo ambiental no dan espera en la ciénaga del Totumo"],
        )
        self.assertNotEqual(nz(tema), nz("Obras de manejo"))
        self.assertTrue(tema_frase_natural(tema), tema)
        self.assertIn("ambiental", nz(tema))

    def test_suicidios_alinean_prevencion(self):
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
        temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
        self.assertEqual(nz(temas[1]), nz("Prevención del suicidio"))
        self.assertEqual(nz(temas[2]), nz("Prevención del suicidio"))
        self.assertNotIn(nz("Intentos de suicidio"), {nz(temas[1]), nz(temas[2])})
        self.assertNotIn(nz("Iberoamericano de suicidología"), {nz(temas[1]), nz(temas[2])})

    def test_ia_no_queda_como_sigla(self):
        tema = generalizar_tema_desde_subtemas(
            ["Criminalidad y uso de IA en investigaciones"],
            ["Expertos debaten criminalidad e inteligencia artificial"],
        )
        self.assertNotEqual(nz(tema), nz("Criminalidad y IA"))
        self.assertNotIn(" ia", " " + nz(tema) + " ")
        self.assertTrue(tema_frase_natural(tema), tema)

    def test_persona_no_es_tema(self):
        tema = generalizar_tema_desde_subtemas(
            ["Muerte de Juliana en competencia"],
            ["Muere Juliana y los especialistas reaccionan"],
        )
        self.assertNotEqual(nz(tema), nz("Muerte de Juliana"))
        self.assertTrue(tema_frase_natural(tema), tema)
        self.assertFalse(any(x in nz(tema) for x in ("juliana",)))

    def test_llm_malo_se_repara_o_cae_a_seguro(self):
        familias = [{
            "id": 1,
            "subtemas": ["Intentos de suicidio en jóvenes"],
            "titulos": ["Cifras de intentos de suicidio en el país"],
            "contextos": ["El estudio mide intentos de suicidio."],
        }]
        with patch("analyzer_tono_tema.llamar_llm",
                   return_value='{"resultados":[{"id":1,"tema":"Intentos de suicidio"}]}'):
            out = nombrar_familias_tema({"api_key": "test"}, familias)
        self.assertEqual(nz(out[1]), nz("Prevención del suicidio"))
        self.assertTrue(tema_frase_natural(out[1]), out[1])

    def test_llm_repara_en_segunda_pasada(self):
        familias = [{
            "id": 1,
            "subtemas": ["Apertura de sede universitaria en el norte"],
            "titulos": ["Inauguran nueva sede universitaria"],
            "contextos": ["La universidad abre un campus y una sede universitaria nueva."],
        }]
        respuestas = [
            '{"resultados":[{"id":1,"tema":"Fortalecimiento de nutrición"}]}',
            '{"resultados":[{"id":1,"tema":"Inauguración de nuevas sedes universitarias"}]}',
        ]
        with patch("analyzer_tono_tema.llamar_llm", side_effect=respuestas):
            out = nombrar_familias_tema({"api_key": "test"}, familias)
        self.assertEqual(nz(out[1]), nz("Inauguración de nuevas sedes universitarias"))

    def test_export_nunca_lleva_inaceptables(self):
        casos = [
            ("Fortalecimiento de la nutrición escolar con el PAE",
             "Soledad fortalece la nutrición escolar con el PAE",
             "Alimentación escolar"),
            ("Reunión de expertos en el campus",
             "Expertos universitarios se reúnen en un foro académico de educación superior",
             None),
            ("Estudiar y conseguir empleo en la región",
             "Jóvenes buscan estudiar y conseguir empleo formal",
             None),
        ]
        for i, (sub, tit, esperado) in enumerate(casos, 1):
            grupos = [_grupo(i, tit, tit)]
            etiquetas = {i: {"sub_tema": sub, "tono": "Neutro"}}
            temas, _ = asignar_temas({}, grupos, etiquetas, {"temas": []})
            self.assertTrue(tema_frase_natural(temas[i]), temas[i])
            self.assertNotIn(nz(temas[i]), {nz(x) for x in TEMAS_EJEMPLO_MALOS})
            if esperado:
                self.assertEqual(nz(temas[i]), nz(esperado))


if __name__ == "__main__":
    unittest.main()
