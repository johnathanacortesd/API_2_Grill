# -*- coding: utf-8 -*-
"""Pruebas de perfiles de cliente e historial (sin llamadas a API)."""
from __future__ import annotations

import os
import tempfile
import unittest

from perfil_cliente import (
    cargar_perfil,
    guardar_perfil,
    listar_perfiles,
    normalizar_perfil,
    perfil_a_resumen,
    validar_perfil,
)
from historial_cliente import guardar_resultado, listar_historial


def _perfil_valido(**kw):
    base = {
        "nombre": "Cliente Prueba",
        "brand": "Marca Prueba",
        "aliases": ["MP", "la marca"],
        "voceros": ["Vocero Uno"],
        "criterio": "Aspectual estricto (recomendado)",
        "criterio_custom": "",
        "taxonomia": None,
        "notas": "",
    }
    base.update(kw)
    return base


class TestValidacion(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(validar_perfil(_perfil_valido()), [])

    def test_sin_brand(self):
        errores = validar_perfil(_perfil_valido(brand="  "))
        self.assertTrue(any("brand" in e for e in errores))

    def test_criterio_invalido(self):
        errores = validar_perfil(_perfil_valido(criterio="No existe"))
        self.assertTrue(any("criterio" in e for e in errores))

    def test_aliases_no_lista(self):
        errores = validar_perfil(_perfil_valido(aliases="MP"))
        self.assertTrue(any("aliases" in e for e in errores))

    def test_taxonomia_invalida(self):
        errores = validar_perfil(_perfil_valido(taxonomia={"temas": "no-lista"}))
        self.assertTrue(any("taxonomia" in e for e in errores))

    def test_taxonomia_ok(self):
        p = _perfil_valido(taxonomia={"temas": ["Salud y red hospitalaria"]})
        self.assertEqual(validar_perfil(p), [])
        n = normalizar_perfil(p)
        self.assertEqual(n["taxonomia"]["temas"], ["Salud y red hospitalaria"])


class TestPersistencia(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLIENTES_DIR"] = self.tmp.name
        os.environ["HISTORIAL_DIR"] = os.path.join(self.tmp.name, "hist")

    def tearDown(self):
        del os.environ["CLIENTES_DIR"]
        del os.environ["HISTORIAL_DIR"]
        self.tmp.cleanup()

    def test_roundtrip(self):
        pid = guardar_perfil(_perfil_valido())
        self.assertEqual(pid, "cliente_prueba")
        p = cargar_perfil(pid)
        self.assertEqual(p["brand"], "Marca Prueba")
        self.assertEqual(p["aliases"], ["MP", "la marca"])
        self.assertEqual(p["voceros"], ["Vocero Uno"])
        self.assertIn("Marca Prueba", [x["brand"] for x in listar_perfiles()])

    def test_guardar_invalido_lanza(self):
        with self.assertRaises(ValueError):
            guardar_perfil(_perfil_valido(brand=""))

    def test_resumen(self):
        r = perfil_a_resumen(normalizar_perfil(_perfil_valido()))
        self.assertIn("Vocero Uno", r)
        self.assertIn("Aspectual", r)

    def test_historial(self):
        self.assertEqual(listar_historial("Marca Prueba"), [])
        ok = guardar_resultado("Marca Prueba", b"x", {
            "total_rows": 10, "unique_rows": 8, "duplicates": 2,
            "process_duration": "3 s", "output_filename": "out.xlsx",
            "analisis": {"taxonomia": ["Salud", "Educación"]},
        })
        self.assertTrue(ok)
        h = listar_historial("Marca Prueba")
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["unique_rows"], 8)
        self.assertEqual(h[0]["temas"], ["Salud", "Educación"])


class TestCriterioCustom(unittest.TestCase):
    def test_prompt_sistema_usa_criterio_texto(self):
        from analyzer_tono_tema import prompt_sistema
        from catalogo_tono_tema import CRITERIOS_TONO
        custom = "REGLA_PROPIA_DEL_CLIENTE_123"
        txt = prompt_sistema({"brand": "X", "criterio": list(CRITERIOS_TONO)[0],
                              "criterio_texto": custom})
        self.assertIn(custom, txt)

    def test_prompt_sistema_sin_custom_usa_catalogo(self):
        from analyzer_tono_tema import prompt_sistema
        from catalogo_tono_tema import CRITERIOS_TONO
        txt = prompt_sistema({"brand": "X", "criterio": "Aspectual estricto (recomendado)",
                              "criterio_texto": ""})
        self.assertIn("PREGUNTA CLAVE", txt)


if __name__ == "__main__":
    unittest.main()
