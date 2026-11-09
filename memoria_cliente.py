# -*- coding: utf-8 -*-
"""Memoria de clasificaciones por cliente (modo Claude Haiku).

Una noticia que ya se clasificó para el mismo cliente, con la misma
configuración, no vuelve al modelo: hereda su tono, subtema y tema. Ahorra
tiempo y costo al repetir un dossier o cuando una nota reaparece otro día,
y da el mismo resultado entre corridas.

La memoria vive junto al historial (``HISTORIAL_DIR`` o ``./historial``), un
JSON por cliente: ``memoria_<cliente>.json``. Solo sirve si la *huella* de
la configuración coincide (marca, alias, voceros, criterio, guía, temas,
PKL y versión de las instrucciones del motor): si algo de eso cambia, la
memoria anterior se descarta. Todo es best-effort: ningún error de lectura
o escritura interrumpe la corrida.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from historial_cliente import _dir_historial, _slug

DIAS_MAX = 45          # una entrada sin usar más de esto se descarta
MAX_ENTRADAS = 20000


def _n(texto: Any) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t.lower()).split())


def clave(titulo: str, contexto: str) -> str:
    """Identidad de la noticia: título y comienzo del Contexto analizado."""
    return hashlib.sha1((_n(titulo) + "|" + _n(contexto)[:400]).encode()).hexdigest()[:20]


def huella(config: dict) -> str:
    """Huella de todo lo que cambia el resultado de la clasificación."""
    return hashlib.sha1(json.dumps(config, ensure_ascii=False, sort_keys=True,
                                   default=str).encode()).hexdigest()[:16]


def _ruta(brand: str, extra=None) -> str:
    return os.path.join(_dir_historial(extra), "memoria_%s.json" % _slug(brand))


def cargar(brand: str, huella_actual: str, extra=None) -> Dict[str, dict]:
    """{clave: {tono, subtema, tema, fecha}} vigente para esta configuración."""
    try:
        with open(_ruta(brand, extra), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("huella") != huella_actual:
            return {}
        limite = (datetime.now() - timedelta(days=DIAS_MAX)).isoformat(timespec="seconds")
        return {k: v for k, v in (data.get("entradas") or {}).items()
                if isinstance(v, dict) and str(v.get("fecha") or "") >= limite}
    except (OSError, ValueError, AttributeError):
        return {}


def guardar(brand: str, huella_actual: str, nuevas: Dict[str, dict], extra=None) -> int:
    """Fusiona `nuevas` con la memoria vigente. Devuelve cuántas quedaron."""
    try:
        entradas = cargar(brand, huella_actual, extra)
        ahora = datetime.now().isoformat(timespec="seconds")
        for k, v in (nuevas or {}).items():
            entradas[k] = dict(v, fecha=ahora)
        if len(entradas) > MAX_ENTRADAS:
            mas_nuevas = sorted(entradas.items(), key=lambda kv: kv[1].get("fecha") or "",
                                reverse=True)[:MAX_ENTRADAS]
            entradas = dict(mas_nuevas)
        ruta = _ruta(brand, extra)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(ruta), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"huella": huella_actual, "entradas": entradas}, f, ensure_ascii=False)
        os.replace(tmp, ruta)
        return len(entradas)
    except (OSError, ValueError):
        return 0


def borrar(brand: str, extra=None) -> bool:
    try:
        os.remove(_ruta(brand, extra))
        return True
    except OSError:
        return False
