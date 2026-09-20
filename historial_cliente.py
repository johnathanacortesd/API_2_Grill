# -*- coding: utf-8 -*-
"""Historial de corridas por cliente.

``pipeline.process_dossier`` llama a :func:`guardar_resultado` al final de cada
corrida (best-effort: nunca interrumpe el proceso) y ``app.py`` usa
:func:`listar_historial` para mostrar las corridas previas del cliente.

El historial vive en JSONL: un archivo por cliente en la carpeta indicada por
la variable de entorno ``HISTORIAL_DIR`` (o ``./historial`` junto a este módulo).
Cada línea es un dict con: fecha, brand, total_rows, unique_rows, duplicates,
process_duration, temas (lista) y archivo.
"""

import json
import os
import re
import unicodedata
from datetime import datetime

_MAX_LINEAS = 200


def _dir_historial(extra=None) -> str:
    base = None
    if isinstance(extra, dict):
        base = extra.get("historial_dir")
    base = base or os.environ.get("HISTORIAL_DIR")
    if not base:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "historial")
    os.makedirs(base, exist_ok=True)
    return base


def _slug(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.lower()).strip("_")
    return s or "cliente"


def _ruta(brand: str, extra=None) -> str:
    return os.path.join(_dir_historial(extra), _slug(brand) + ".jsonl")


def guardar_resultado(brand: str, output_data, result: dict, extra=None) -> bool:
    """Agrega una línea al historial del cliente. Devuelve True si se guardó."""
    try:
        analisis = (result or {}).get("analisis") or {}
        registro = {
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "brand": brand,
            "total_rows": (result or {}).get("total_rows"),
            "unique_rows": (result or {}).get("unique_rows"),
            "duplicates": (result or {}).get("duplicates"),
            "process_duration": (result or {}).get("process_duration"),
            "temas": list(analisis.get("taxonomia") or []),
            "archivo": (result or {}).get("output_filename"),
        }
        ruta = _ruta(brand, extra)
        lineas = []
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                lineas = [ln for ln in f.read().splitlines() if ln.strip()]
        lineas.append(json.dumps(registro, ensure_ascii=False))
        lineas = lineas[-_MAX_LINEAS:]
        with open(ruta, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")
        return True
    except Exception:
        return False


def listar_historial(brand: str, extra=None) -> list:
    """Devuelve los registros del cliente, del más reciente al más antiguo."""
    try:
        ruta = _ruta(brand, extra)
        if not os.path.exists(ruta):
            return []
        registros = []
        with open(ruta, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    registros.append(json.loads(ln))
                except ValueError:
                    continue
        return list(reversed(registros))
    except Exception:
        return []
