# -*- coding: utf-8 -*-
"""El PKL de tono/tema del cliente se invoca y gana sobre el lote/LLM."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from analyzer_tono_tema import (
    aplicar_pkl_del_cliente,
    enrich_rows_with_ai,
    ultimo_resumen,
    volcar_analisis_en_filas,
    tema_frase_natural,
)
from pkl_classifier import (
    apply_pkl_classifiers,
    classification_plan,
    load_sklearn_estimator,
)


KM = {"titulo": "Título"}

# Etiqueta típica de un PKL de cliente: 1 palabra, falla el gate de frases del lote.
PKL_TEMA = "Educación"
PKL_TEMA_B = "Salud"
PKL_TONO = "Negativo"
LOTE_TEMA = "Alimentación escolar y nutrición del PAE"
LOTE_TONO = "Positivo"
LOTE_SUB = "Fortalecimiento del PAE escolar"

PAE_CUERPO = (
    "La Alcaldía de Soledad fortalece la nutrición escolar con el PAE. "
    "El programa de alimentación escolar entrega desayunos y almuerzos a los "
    "estudiantes desde el primer día de clases y hace seguimiento nutricional."
)


class FakeClf:
    """Estimador duck-typed: expone predict como un PKL sklearn."""

    def __init__(self, label, by_text=None, classes=None):
        self.label = label
        self.by_text = by_text or {}
        self.classes_ = list(classes or [label])
        self.calls = []

    def predict(self, texts):
        texts = list(texts)
        self.calls.append(texts)
        out = []
        for t in texts:
            hit = None
            for needle, lab in self.by_text.items():
                if needle.lower() in (t or "").lower():
                    hit = lab
                    break
            out.append(hit if hit is not None else self.label)
        return out


def _row(titulo, cuerpo, dup=False):
    return {
        "Título": titulo,
        "CuerpoEs": cuerpo,
        "is_duplicate": dup,
    }


def _grupo(gid, titulo, texto="", contexto="", idx=None):
    return {
        "grupo": gid,
        "n": 1,
        "idxs": [idx if idx is not None else gid - 1],
        "titulo": titulo,
        "titulos_alt": [],
        "texto": texto or titulo,
        "contexto": contexto or texto or titulo,
    }


def _fake_etq_pae(cfg, grupos, *args, **kwargs):
    out = {}
    for g in grupos:
        out[g["grupo"]] = {"sub_tema": LOTE_SUB, "tono": LOTE_TONO}
    return out


class TestPklClaseFallaQualityGate(unittest.TestCase):
    def test_educacion_es_clase_pkl_tipica_rechazada_por_el_gate(self):
        self.assertFalse(tema_frase_natural(PKL_TEMA), PKL_TEMA)
        self.assertFalse(tema_frase_natural(PKL_TEMA_B), PKL_TEMA_B)


class TestAplicarPklDelCliente(unittest.TestCase):
    def test_pkl_escribe_clases_sin_pasar_por_gate(self):
        rows = [_row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO)]
        grupos = [_grupo(1, rows[0]["Título"], PAE_CUERPO)]
        etiquetas = {1: {"sub_tema": LOTE_SUB, "tono": LOTE_TONO}}
        temas = {1: LOTE_TEMA}
        origen = {1: "familia:1"}
        tone = FakeClf(PKL_TONO)
        theme = FakeClf(PKL_TEMA)
        applied = aplicar_pkl_del_cliente(
            grupos, rows, etiquetas, temas, origen,
            tone_model=tone, theme_model=theme,
        )
        self.assertEqual(applied["tono"], 1)
        self.assertEqual(applied["tema"], 1)
        self.assertEqual(etiquetas[1]["tono"], PKL_TONO)
        self.assertEqual(temas[1], PKL_TEMA)
        self.assertEqual(origen[1], "pkl")
        self.assertEqual(etiquetas[1]["sub_tema"], LOTE_SUB)
        self.assertEqual(len(tone.calls), 1)
        self.assertEqual(len(theme.calls), 1)

    def test_sin_modelos_no_toca_etiquetas(self):
        etiquetas = {1: {"sub_tema": LOTE_SUB, "tono": LOTE_TONO}}
        temas = {1: LOTE_TEMA}
        origen = {1: "familia:1"}
        applied = aplicar_pkl_del_cliente(
            [_grupo(1, "t")], [_row("t", "c")], etiquetas, temas, origen,
        )
        self.assertEqual(applied, {"tono": 0, "tema": 0})
        self.assertEqual(etiquetas[1]["tono"], LOTE_TONO)
        self.assertEqual(temas[1], LOTE_TEMA)


class TestVolcarPreservaPkl(unittest.TestCase):
    def test_preservar_tema_no_reescribe_clase_corta(self):
        rows = [_row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO)]
        volcar_analisis_en_filas(
            rows, {0: 1},
            {1: {"sub_tema": LOTE_SUB, "tono": PKL_TONO}},
            {1: PKL_TEMA},
            preservar_tema=True,
        )
        self.assertEqual(rows[0]["Tema_IA"], PKL_TEMA)
        self.assertEqual(rows[0]["Tono_IA"], PKL_TONO)

    def test_sin_preservar_el_gate_sigue_pudiendo_reescribir(self):
        rows = [_row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO)]
        volcar_analisis_en_filas(
            rows, {0: 1},
            {1: {"sub_tema": LOTE_SUB, "tono": LOTE_TONO}},
            {1: PKL_TEMA},
            preservar_tema=False,
        )
        # "Educación" es usable (no vacío); el volcado solo reescribe vacío o copia de titular.
        # Esta aserción documenta que preservar_tema es lo que protege frente a
        # _asegurar_tema_texto cuando además falla el gate aguas arriba.
        self.assertTrue(rows[0]["Tema_IA"])


class TestEnrichHonraPkl(unittest.TestCase):
    def test_tema_pkl_gana_sobre_lote_y_se_invoca_predict(self):
        rows = [
            _row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO),
            _row("Estudiantes ganan el concurso nacional de robótica",
                 "El equipo de estudiantes ganó el concurso nacional de robótica."),
        ]
        theme = FakeClf(
            PKL_TEMA,
            by_text={"robót": PKL_TEMA_B, "robot": PKL_TEMA_B},
            classes=[PKL_TEMA, PKL_TEMA_B],
        )

        with patch("analyzer_tono_tema.etiquetar_grupos", side_effect=_fake_etq_pae), \
             patch("analyzer_tono_tema.asignar_temas") as mock_asig, \
             patch("analyzer_tono_tema.corregir_temas_con_jev") as mock_jev, \
             patch("analyzer_tono_tema.llamar_llm", side_effect=RuntimeError("sin api")):
            mock_asig.return_value = ({1: LOTE_TEMA, 2: LOTE_TEMA}, {1: "llm", 2: "llm"})
            enrich_rows_with_ai(
                rows, KM, "Soledad", [], api_key="",
                theme_model=theme,
                extra={"votos": 1, "taxonomia": "Automática según el archivo"},
            )
            mock_asig.assert_not_called()
            mock_jev.assert_not_called()

        self.assertGreaterEqual(len(theme.calls), 1)
        unicos = [r for r in rows if not r.get("is_duplicate")]
        temas = {r["Tema_IA"] for r in unicos}
        self.assertTrue(temas <= set(theme.classes_), temas)
        self.assertTrue(temas <= {PKL_TEMA, PKL_TEMA_B}, temas)
        self.assertNotIn(LOTE_TEMA, temas)
        for r in unicos:
            self.assertIn(r["Tema_IA"], theme.classes_)
        # v4.18: el fake etiqueta la nota de robótica como PAE (cruce); la
        # reparación de etiquetas ajenas la devuelve a su propio titular.
        self.assertEqual(rows[0]["Subtema_IA"], LOTE_SUB)
        self.assertEqual(rows[1]["Subtema_IA"],
                         "Estudiantes ganan el concurso nacional de robótica")
        resumen = ultimo_resumen()
        self.assertEqual(resumen.get("modo_taxonomia"), "pkl")
        self.assertGreaterEqual(resumen.get("temas_por_pkl") or 0, 1)

    def test_tono_pkl_gana_sobre_llm_y_guarda(self):
        rows = [_row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO)]
        tone = FakeClf(PKL_TONO)

        def fake_guarda(*args, **kwargs):
            raise AssertionError("la guarda del tono no debe correr si hay PKL de tono")

        with patch("analyzer_tono_tema.etiquetar_grupos", side_effect=_fake_etq_pae), \
             patch("analyzer_tono_tema.aplicar_guarda_tono", side_effect=fake_guarda), \
             patch("analyzer_tono_tema.aplicar_guarda_positiva", side_effect=fake_guarda), \
             patch("analyzer_tono_tema.asignar_temas", return_value=({1: LOTE_TEMA}, {1: "llm"})), \
             patch("analyzer_tono_tema.corregir_temas_con_jev"), \
             patch("analyzer_tono_tema.llamar_llm", side_effect=RuntimeError("sin api")):
            enrich_rows_with_ai(
                rows, KM, "Soledad", [], api_key="",
                tone_model=tone,
                extra={"votos": 1, "taxonomia": "Automática según el archivo"},
            )

        self.assertEqual(len(tone.calls), 1)
        self.assertEqual(rows[0]["Tono_IA"], PKL_TONO)
        self.assertNotEqual(rows[0]["Tono_IA"], LOTE_TONO)
        self.assertEqual(rows[0]["Subtema_IA"], LOTE_SUB)
        self.assertGreaterEqual(ultimo_resumen().get("tonos_por_pkl") or 0, 1)

    def test_solo_tono_pkl_no_bloquea_tema_del_lote(self):
        rows = [_row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO)]
        tone = FakeClf(PKL_TONO)

        with patch("analyzer_tono_tema.etiquetar_grupos", side_effect=_fake_etq_pae), \
             patch("analyzer_tono_tema.asignar_temas", return_value=({1: LOTE_TEMA}, {1: "llm"})) as mock_asig, \
             patch("analyzer_tono_tema.corregir_temas_con_jev"), \
             patch("analyzer_tono_tema.llamar_llm", side_effect=RuntimeError("sin api")):
            enrich_rows_with_ai(
                rows, KM, "Soledad", [], api_key="",
                tone_model=tone,
                extra={"votos": 1, "taxonomia": "Automática según el archivo"},
            )
            mock_asig.assert_called()

        self.assertEqual(rows[0]["Tono_IA"], PKL_TONO)
        self.assertEqual(rows[0]["Tema_IA"], LOTE_TEMA)

    def test_sin_pkl_sigue_el_lote(self):
        rows = [_row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO)]

        with patch("analyzer_tono_tema.etiquetar_grupos", side_effect=_fake_etq_pae), \
             patch("analyzer_tono_tema.asignar_temas", return_value=({1: LOTE_TEMA}, {1: "llm"})) as mock_asig, \
             patch("analyzer_tono_tema.corregir_temas_con_jev"), \
             patch("analyzer_tono_tema.llamar_llm", side_effect=RuntimeError("sin api")):
            enrich_rows_with_ai(
                rows, KM, "Soledad", [], api_key="",
                extra={"votos": 1, "taxonomia": "Automática según el archivo"},
            )
            mock_asig.assert_called()

        self.assertEqual(rows[0]["Tema_IA"], LOTE_TEMA)
        self.assertEqual(rows[0]["Tono_IA"], LOTE_TONO)
        self.assertNotEqual(ultimo_resumen().get("modo_taxonomia"), "pkl")

    def test_tema_pkl_no_unifica_clases_distintas_del_mismo_subtema(self):
        rows = [
            _row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO),
            # Comparte subtema legítimamente (PAE) pero el PKL la clasifica
            # en otra clase: las clases no deben unificarse. v4.18: la
            # reparación de etiquetas ajenas no debe tocarla (el subtema
            # describe su propio contenido).
            _row("Hospital municipal articula vacunación con el PAE escolar",
                 "El hospital municipal adelanta una jornada de vacunación infantil "
                 "y articula con el PAE la nutrición escolar."),
        ]
        theme = FakeClf(PKL_TEMA, by_text={"vacun": PKL_TEMA_B, "hospital": PKL_TEMA_B})

        with patch("analyzer_tono_tema.etiquetar_grupos", side_effect=_fake_etq_pae), \
             patch("analyzer_tono_tema.asignar_temas") as mock_asig, \
             patch("analyzer_tono_tema.corregir_temas_con_jev"), \
             patch("analyzer_tono_tema.llamar_llm", side_effect=RuntimeError("sin api")):
            enrich_rows_with_ai(
                rows, KM, "Soledad", [], api_key="",
                theme_model=theme,
                extra={"votos": 1, "taxonomia": "Automática según el archivo"},
            )
            mock_asig.assert_not_called()

        self.assertEqual(rows[0]["Tema_IA"], PKL_TEMA)
        self.assertEqual(rows[1]["Tema_IA"], PKL_TEMA_B)
        self.assertEqual(rows[0]["Subtema_IA"], rows[1]["Subtema_IA"])


class TestApplyPklClassifiersPath(unittest.TestCase):
    """Camino sin IA: apply_pkl_classifiers escribe las clases del PKL."""

    def test_apply_pkl_classifiers_invoca_predict_y_escribe(self):
        rows = [
            {
                "Título": "Soledad fortalece la nutrición escolar con el PAE",
                "CuerpoEs": PAE_CUERPO,
                "Contexto analizado": PAE_CUERPO,
                "is_duplicate": False,
            }
        ]
        tone = FakeClf("Positivo")
        theme = FakeClf(PKL_TEMA)
        apply_pkl_classifiers(
            rows, KM, tone_model=tone, theme_model=theme, unify_similar=False,
        )
        self.assertEqual(rows[0]["Tono_IA"], "Positivo")
        self.assertEqual(rows[0]["Tema_IA"], PKL_TEMA)
        self.assertEqual(len(tone.calls), 1)
        self.assertEqual(len(theme.calls), 1)

    def test_classification_plan_pkl_apaga_llm_del_eje(self):
        plan = classification_plan(True, tone_model=FakeClf("x"), theme_model=FakeClf("y"))
        self.assertTrue(plan["use_pkl_tone"])
        self.assertTrue(plan["use_pkl_theme"])
        self.assertFalse(plan["use_llm_tone"])
        self.assertFalse(plan["use_llm_theme"])
        self.assertTrue(plan["use_llm_subtema"])

        plan_none = classification_plan(True)
        self.assertTrue(plan_none["use_llm_tone"])
        self.assertTrue(plan_none["use_llm_theme"])
        self.assertFalse(plan_none["use_pkl_tone"])
        self.assertFalse(plan_none["use_pkl_theme"])


def _joblib_bytes(estimator) -> bytes:
    import io
    import joblib
    buf = io.BytesIO()
    joblib.dump(estimator, buf)
    return buf.getvalue()


def _fit_tema_pipeline():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.naive_bayes import MultinomialNB
    from sklearn.pipeline import make_pipeline

    texts = [
        "alimentación escolar pae soledad nutrición desayunos almuerzos",
        "programa de alimentación escolar y seguimiento nutricional",
        "jornada de vacunación hospital municipal infantil",
        "vacunación en el hospital y puesto de salud",
    ]
    labels = [PKL_TEMA, PKL_TEMA, PKL_TEMA_B, PKL_TEMA_B]
    clf = make_pipeline(TfidfVectorizer(), MultinomialNB())
    clf.fit(texts, labels)
    return clf


class TestPklUploadLoadClassify(unittest.TestCase):
    """Bytes como los del uploader → load_sklearn_estimator → enrich usa esas clases."""

    def test_bytes_de_tema_pkl_se_cargan_y_tema_pertenece_a_classes(self):
        raw = _joblib_bytes(_fit_tema_pipeline())
        theme, classes = load_sklearn_estimator(raw, "tema")
        self.assertIsNotNone(theme)
        pkl_classes = {str(c) for c in (classes if classes is not None else theme.named_steps["multinomialnb"].classes_)}
        self.assertEqual(pkl_classes, {PKL_TEMA, PKL_TEMA_B})

        rows = [
            _row("Soledad fortalece la nutrición escolar con el PAE", PAE_CUERPO),
            _row("Jornada de vacunación en el hospital municipal",
                 "El hospital municipal adelanta una jornada de vacunación infantil."),
        ]
        with patch("analyzer_tono_tema.etiquetar_grupos", side_effect=_fake_etq_pae), \
             patch("analyzer_tono_tema.asignar_temas") as mock_asig, \
             patch("analyzer_tono_tema.corregir_temas_con_jev"), \
             patch("analyzer_tono_tema.llamar_llm", side_effect=RuntimeError("sin api")):
            enrich_rows_with_ai(
                rows, KM, "Soledad", [], api_key="",
                theme_model=theme,
                extra={"votos": 1, "taxonomia": "Automática según el archivo"},
            )
            mock_asig.assert_not_called()

        for r in rows:
            self.assertIn(r["Tema_IA"], pkl_classes, r["Tema_IA"])
            self.assertNotEqual(r["Tema_IA"], LOTE_TEMA)
        self.assertEqual({r["Tema_IA"] for r in rows}, pkl_classes)

    def test_pipeline_carga_theme_pkl_bytes_del_ai_config(self):
        from pipeline import _load_optional_pkl_models

        raw = _joblib_bytes(_fit_tema_pipeline())
        tone, theme = _load_optional_pkl_models({
            "theme_pkl_bytes": raw,
            "tone_pkl_bytes": None,
        })
        self.assertIsNone(tone)
        self.assertIsNotNone(theme)
        preds = theme.predict([PAE_CUERPO])
        self.assertIn(str(preds[0]), {PKL_TEMA, PKL_TEMA_B})

    def test_sin_bytes_no_carga_modelos(self):
        from pipeline import _load_optional_pkl_models

        tone, theme = _load_optional_pkl_models({"enabled": True})
        self.assertIsNone(tone)
        self.assertIsNone(theme)


if __name__ == "__main__":
    unittest.main()
