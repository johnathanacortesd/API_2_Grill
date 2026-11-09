# -*- coding: utf-8 -*-
"""Aislamiento de las pruebas: el historial de corridas va a una carpeta
temporal. Sin esto, cada prueba que corre `pipeline.process_dossier` agrega
líneas falsas a `historial/` del repositorio (el historial real por cliente).
"""
import pytest


@pytest.fixture(autouse=True)
def _historial_temporal(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("HISTORIAL_DIR", str(tmp_path_factory.mktemp("historial")))
