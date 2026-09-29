# -*- coding: utf-8 -*-
"""v4.35: similitud por título Y cuerpo + guarda de fidelidad de etiquetas.

Intención del usuario (2026-09-29):
- Noticias iguales o similares (por Título o por CuerpoEs) comparten tono,
  tema y subtema, con o sin PKL.
- El subtema debe corresponder al título y/o cuerpo de CADA noticia: sin
  agrupación forzada; la noticia específica conserva su especificidad.
- El Contexto analizado es la fuente prioritaria del análisis.

Caso real que motiva la guarda (2026-09-28): «Designación de Vélez en
Colpensiones» apareció como subtema en noticias cuyo título y resumen no
mencionaban a Vélez.
"""
from __future__ import annotations

import sys
import types
import unittest

# Stub del paquete `openai` (mismo patrón que test_v434_subtema_identidad):
# pipeline lo importa vía ai_analyzer y ningún test de este archivo hace
# llamadas reales.
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

from analyzer_tono_tema import (
    _elegir_canon_fiel,
    _etiqueta_fiel_a_texto,
    _nombres_propios_en_etiqueta,
    _subtema_desde_titulo,
    aplicar_guarda_fidelidad_subtema,
    construir_grupos,
    unificar_etiquetas_titulos_similares,
    volcar_analisis_en_filas,
)
from pipeline import renombrar_columnas_xlsx, leer_columnas_xlsx
from analyzer_tono_tema import _contexto_exacto_marca

BRAND = "Colpensiones"

TIT_CON_NOMBRE = "Beatriz Vélez, nueva presidenta de Colpensiones"
CUERPO_CON_NOMBRE = ("La abogada Beatriz Vélez fue designada como nueva "
                     "presidenta de Colpensiones por el Gobierno nacional.")
TIT_SIN_NOMBRE = "Nueva presidenta de Colpensiones"
CUERPO_SIN_NOMBRE = ("El Gobierno nacional designó una nueva presidenta de "
                     "Colpensiones en medio de la polémica por las mesadas.")
SUB_VELEZ = "Designación de Vélez en Colpensiones"
SUB_SIN_NOMBRE = "Nueva presidencia de Colpensiones"


def _fila(titulo, cuerpo="", gid=1):
    return {"Título": titulo, "CuerpoEs": cuerpo,
            "Contexto analizado": titulo}


class TestNombresPropiosEnEtiqueta(unittest.TestCase):
    def test_detecta_apellido(self):
        self.assertEqual(
            _nombres_propios_en_etiqueta(SUB_VELEZ, BRAND), ["velez"])

    def test_marca_exenta(self):
        self.assertEqual(
            _nombres_propios_en_etiqueta("Gestión de Colpensiones", BRAND), [])

    def test_sin_nombres(self):
        self.assertEqual(
            _nombres_propios_en_etiqueta("Pago de mesadas de fin de año",
                                         BRAND), [])

    def test_primera_palabra_no_cuenta(self):
        # "Vélez" como primera palabra no se trata como nombre intercalado.
        self.assertEqual(
            _nombres_propios_en_etiqueta("Vélez asume presidencia", BRAND), [])


class TestEtiquetaFielATexto(unittest.TestCase):
    def test_infiel_sin_mencion(self):
        self.assertFalse(_etiqueta_fiel_a_texto(
            SUB_VELEZ, TIT_SIN_NOMBRE, CUERPO_SIN_NOMBRE, BRAND))

    def test_fiel_con_mencion(self):
        self.assertTrue(_etiqueta_fiel_a_texto(
            SUB_VELEZ, TIT_CON_NOMBRE, CUERPO_CON_NOMBRE, BRAND))

    def test_fiel_sin_nombres_propios(self):
        self.assertTrue(_etiqueta_fiel_a_texto(
            "Pago de mesadas de fin de año", "Otro título", "otro cuerpo",
            BRAND))

    def test_tolerante_a_plural(self):
        # «Tipos de Pensión» es fiel si el texto habla de «pensiones».
        self.assertTrue(_etiqueta_fiel_a_texto(
            "Tipos de Pensión",
            "Gobierno avanza en ruta para garantizar el pago de pensiones",
            "", BRAND))


class TestCanonFiel(unittest.TestCase):
    def _textos(self):
        return [(TIT_CON_NOMBRE, CUERPO_CON_NOMBRE),
                (TIT_CON_NOMBRE, CUERPO_CON_NOMBRE),
                (TIT_SIN_NOMBRE, CUERPO_SIN_NOMBRE)]

    def test_mayoria_infiel_se_salta(self):
        # 2 votos por el subtema con nombre (infiel a la 3a fila) vs 1 voto
        # por el fiel: gana el fiel.
        canon = _elegir_canon_fiel(
            [SUB_VELEZ, SUB_VELEZ, SUB_SIN_NOMBRE],
            self._textos(), BRAND)
        self.assertEqual(canon, SUB_SIN_NOMBRE)

    def test_mayoria_fiel_gana(self):
        canon = _elegir_canon_fiel(
            [SUB_SIN_NOMBRE, SUB_SIN_NOMBRE, "Otro subtema fiel"],
            [(TIT_SIN_NOMBRE, CUERPO_SIN_NOMBRE)] * 3, BRAND)
        self.assertEqual(canon, SUB_SIN_NOMBRE)

    def test_ninguno_fiel_no_unifica(self):
        canon = _elegir_canon_fiel(
            [SUB_VELEZ, "Renuncia de Vélez en Colpensiones"],
            [(TIT_SIN_NOMBRE, CUERPO_SIN_NOMBRE)] * 2, BRAND)
        self.assertEqual(canon, "")


class TestPaseFinalConFidelidad(unittest.TestCase):
    def test_bloque_converge_en_canon_fiel(self):
        # Dos grupos con titulares similares: el subtema mayoritario nombra
        # a Vélez pero una fila no la menciona → el bloque converge en el
        # subtema fiel a todas las filas.
        rows = [_fila(TIT_CON_NOMBRE, CUERPO_CON_NOMBRE),
                _fila(TIT_CON_NOMBRE, CUERPO_CON_NOMBRE),
                _fila(TIT_SIN_NOMBRE, CUERPO_SIN_NOMBRE)]
        mapa = {0: 1, 1: 1, 2: 2}
        etiquetas = {1: {"tono": "Neutro", "sub_tema": SUB_VELEZ},
                     2: {"tono": "Neutro", "sub_tema": SUB_SIN_NOMBRE}}
        temas = {1: "Institucional", 2: "Institucional"}
        n = unificar_etiquetas_titulos_similares(
            rows, mapa, etiquetas, temas, {}, brand=BRAND)
        self.assertGreater(n, 0)
        self.assertEqual(etiquetas[1]["sub_tema"], SUB_SIN_NOMBRE)
        self.assertEqual(etiquetas[2]["sub_tema"], SUB_SIN_NOMBRE)

    def test_sin_candidato_fiel_no_se_unifica(self):
        rows = [_fila(TIT_CON_NOMBRE, CUERPO_CON_NOMBRE),
                _fila(TIT_SIN_NOMBRE, CUERPO_SIN_NOMBRE)]
        mapa = {0: 1, 1: 2}
        etiquetas = {1: {"tono": "Neutro", "sub_tema": SUB_VELEZ},
                     2: {"tono": "Neutro",
                         "sub_tema": "Salida de Vélez de Colpensiones"}}
        temas = {1: "Institucional", 2: "Institucional"}
        unificar_etiquetas_titulos_similares(
            rows, mapa, etiquetas, temas, {}, brand=BRAND)
        # Ningún candidato es fiel a ambas filas: cada grupo conserva el suyo.
        self.assertEqual(etiquetas[1]["sub_tema"], SUB_VELEZ)
        self.assertEqual(etiquetas[2]["sub_tema"],
                         "Salida de Vélez de Colpensiones")


class TestGuardaFidelidadPorFila(unittest.TestCase):
    def test_repara_fila_infiel(self):
        rows = [_fila(TIT_SIN_NOMBRE, CUERPO_SIN_NOMBRE)]
        mapa = {0: 1}
        etiquetas = {1: {"tono": "Neutro", "sub_tema": SUB_VELEZ}}
        ajustes = aplicar_guarda_fidelidad_subtema(
            rows, mapa, etiquetas, {}, brand=BRAND)
        self.assertEqual(ajustes,
                         {0: _subtema_desde_titulo(TIT_SIN_NOMBRE)})
        self.assertNotIn("lez", ajustes[0].lower().replace("velez", ""))

    def test_no_toca_fila_fiel(self):
        rows = [_fila(TIT_CON_NOMBRE, CUERPO_CON_NOMBRE)]
        mapa = {0: 1}
        etiquetas = {1: {"tono": "Neutro", "sub_tema": SUB_VELEZ}}
        ajustes = aplicar_guarda_fidelidad_subtema(
            rows, mapa, etiquetas, {}, brand=BRAND)
        self.assertEqual(ajustes, {})

    def test_volcar_aplica_ajuste_por_fila(self):
        rows = [_fila(TIT_SIN_NOMBRE, CUERPO_SIN_NOMBRE),
                _fila(TIT_CON_NOMBRE, CUERPO_CON_NOMBRE)]
        mapa = {0: 1, 1: 1}
        etiquetas = {1: {"tono": "Neutro", "sub_tema": SUB_VELEZ}}
        ajustes = aplicar_guarda_fidelidad_subtema(
            rows, mapa, etiquetas, {}, brand=BRAND)
        volcar_analisis_en_filas(rows, mapa, etiquetas, {1: "Institucional"},
                                 ajustes_subtema=ajustes)
        self.assertEqual(rows[0]["Subtema_IA"],
                         _subtema_desde_titulo(TIT_SIN_NOMBRE))
        self.assertEqual(rows[1]["Subtema_IA"], SUB_VELEZ)


class TestSimilitudPorCuerpo(unittest.TestCase):
    CUERPO = ("El gobierno anunció una ruta técnica y presupuestal para "
              "garantizar el pago de las mesadas de noviembre y diciembre de "
              "2026, según informó la ministra del Trabajo en una rueda de "
              "prensa ofrecida este martes en la ciudad de Bogotá.")

    def test_cuerpo_identico_distinto_titulo_mismo_grupo(self):
        rows = [_fila("Ruta del gobierno para las mesadas", self.CUERPO),
                _fila("Anuncian ruta para pago de pensiones", self.CUERPO)]
        grupos, mapa = construir_grupos(rows, {}, 92, 85)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(mapa[0], mapa[1])

    def test_cuerpo_contenido_mismo_grupo(self):
        largo = ("Encabezado del agregador. " + self.CUERPO +
                 " Cola del agregador con más texto.")
        rows = [_fila("Ruta del gobierno para las mesadas", self.CUERPO),
                _fila("Otro titular distinto", largo)]
        grupos, mapa = construir_grupos(rows, {}, 92, 85)
        self.assertEqual(len(grupos), 1)

    def test_cuerpos_distintos_no_se_unen(self):
        otro = ("El equipo local ganó el partido con un marcador de tres "
                "goles a uno en el estadio municipal ante miles de "
                "aficionados que celebraron hasta tarde en la noche.")
        rows = [_fila("Ruta del gobierno para las mesadas", self.CUERPO),
                _fila("Victoria del equipo local", otro)]
        grupos, _mapa = construir_grupos(rows, {}, 92, 85)
        self.assertEqual(len(grupos), 2)


class TestContextoAnalizadoPreciso(unittest.TestCase):
    def test_titular_antepuesto_si_menciona_marca(self):
        titulo = "Colpensiones anuncia nueva ruta de pagos"
        cuerpo = ("En un comunicado, Colpensiones informó que la ruta "
                  "técnica está lista. Otros párrafos sin mención.")
        ctx = _contexto_exacto_marca(cuerpo, titulo, BRAND, [], [],
                                     tipo_medio="prensa")
        self.assertTrue(ctx.startswith(titulo))

    def test_dedup_normalizado(self):
        titulo = "Colpensiones anuncia pagos"
        cuerpo = ("Colpensiones anuncia pagos. "
                  "Colpensiones anuncia pagos! "
                  "Otro texto de Colpensiones.")
        ctx = _contexto_exacto_marca(cuerpo, titulo, BRAND, [], [],
                                     tipo_medio="prensa")
        self.assertEqual(ctx.count("Colpensiones anuncia pagos"), 1)


class TestRenombrarApartaCuerpos(unittest.TestCase):
    def test_cuerpo_elegido_manda(self):
        import io
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["MiTitulo", "CuerpoEs", "MiCuerpo"])
        ws.append(["t1", "TEXTO LARGO DE CUERPOES " * 20, "corto"])
        bio = io.BytesIO()
        wb.save(bio)
        out = renombrar_columnas_xlsx(bio.getvalue(), "MiTitulo", "MiCuerpo")
        cols = [c for c, _ in leer_columnas_xlsx(out)]
        self.assertIn("CuerpoEs (original)", cols)
        self.assertIn("Resumen - Aclaracion", cols)


if __name__ == "__main__":
    unittest.main()
