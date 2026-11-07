# -*- coding: utf-8 -*-
"""Perfiles de cliente para el motor de Tono/Tema/Subtema (modo multicliente).

Un perfil guarda, por cliente, todo lo que el análisis necesita adaptar:
marca principal, alias, voceros, criterio de tono (del catálogo o texto libre)
y una lista fija de Temas opcional. Los perfiles viven como JSON en
``clientes/`` (o en la ruta de la variable de entorno ``CLIENTES_DIR``).

Esquema del JSON::

    {
      "nombre": "Universidad Simón Bolívar",
      "brand": "Universidad Simón Bolívar",
      "aliases": ["Unisimón", "la Simón Bolívar"],
      "voceros": ["José Consuegra"],
      "criterio": "Aspectual estricto (recomendado)",
      "criterio_custom": "",
      "taxonomia": {"nota": "...", "temas": ["..."], "reglas": []},
      "notas": "texto libre"
    }

``criterio`` debe ser una clave de ``catalogo_tono_tema.CRITERIOS_TONO``.
Si ``criterio_custom`` trae texto, el motor lo usa en lugar del catálogo.
``taxonomia`` es opcional: si trae ``temas``, esos cubos se usan como lista
cerrada/candidata en lugar de armarlos bottom-up en el lote.
"""

import json
import os
import re
import unicodedata

from catalogo_tono_tema import CRITERIOS_TONO


def dir_clientes() -> str:
    """Carpeta donde viven los perfiles (se crea si no existe)."""
    base = os.environ.get("CLIENTES_DIR")
    if not base:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clientes")
    os.makedirs(base, exist_ok=True)
    return base


def slugify(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.lower()).strip("_")
    return s or "cliente"


def _defaults() -> dict:
    return {
        "nombre": "",
        "brand": "",
        "aliases": [],
        "voceros": [],
        "criterio": list(CRITERIOS_TONO)[0],
        "criterio_custom": "",
        "taxonomia": None,
        # Clausifier: flags de tono/subtema de los perfiles de Secretaría y
        # Defensoría. `agrupacion_estricta` None = default del analizador.
        # TIPO/GÉNERO y el formato boletín no se usan (se descartan).
        "guia_subtema": "",
        "subtemas_fijos": [],
        "tono_distrito_positivo": False,
        "tono_solo_gestion": False,
        "tono_alertas_tempranas": False,
        "tono_labor_positiva": False,
        "agrupacion_estricta": None,
        "notas": "",
    }


def validar_perfil(data: dict) -> list:
    """Devuelve la lista de errores; vacía = perfil válido."""
    errores = []
    if not isinstance(data, dict):
        return ["el perfil debe ser un objeto JSON"]
    d = _defaults()
    d.update({k: v for k, v in data.items() if k in d})
    if not str(d["brand"]).strip():
        errores.append("falta 'brand' (Marca o Cliente Principal)")
    if d["criterio"] not in CRITERIOS_TONO:
        errores.append("criterio '%s' no existe en el catálogo" % d["criterio"])
    for campo in ("aliases", "voceros"):
        if not isinstance(d[campo], list) or any(not isinstance(x, str) for x in d[campo]):
            errores.append("'%s' debe ser una lista de textos" % campo)
    sf = d["subtemas_fijos"]
    if sf is not None:
        if not isinstance(sf, list) or any(
                not isinstance(r, dict) or not str(r.get("contiene") or "").strip()
                or not str(r.get("subtema") or "").strip() for r in sf):
            errores.append("'subtemas_fijos' debe ser una lista de {contiene, subtema} con textos no vacíos")
    for campo in ("tono_distrito_positivo", "tono_solo_gestion",
                  "tono_alertas_tempranas", "tono_labor_positiva"):
        if not isinstance(d[campo], bool):
            errores.append("'%s' debe ser true/false" % campo)
    if d["agrupacion_estricta"] is not None and not isinstance(d["agrupacion_estricta"], bool):
        errores.append("'agrupacion_estricta' debe ser true/false")
    tax = d["taxonomia"]
    if tax is not None:
        if not isinstance(tax, dict) or not isinstance(tax.get("temas"), list) \
                or not all(isinstance(t, str) and t.strip() for t in tax["temas"]):
            errores.append("'taxonomia' debe ser un objeto con 'temas': [lista de textos]")
    return errores


def normalizar_perfil(data: dict) -> dict:
    """Completa valores por defecto y limpia listas."""
    d = _defaults()
    for k, v in (data or {}).items():
        if k in d:
            d[k] = v
    d["nombre"] = str(d["nombre"] or d["brand"]).strip()
    d["brand"] = str(d["brand"]).strip()
    d["aliases"] = [a.strip() for a in (d["aliases"] or []) if str(a).strip()]
    d["voceros"] = [v.strip() for v in (d["voceros"] or []) if str(v).strip()]
    d["criterio_custom"] = str(d["criterio_custom"] or "").strip()
    d["guia_subtema"] = str(d["guia_subtema"] or "").strip()
    d["subtemas_fijos"] = [
        {"contiene": str(r.get("contiene") or "").strip(),
         "subtema": str(r.get("subtema") or "").strip()}
        for r in (d["subtemas_fijos"] or [])
        if isinstance(r, dict) and str(r.get("contiene") or "").strip()
        and str(r.get("subtema") or "").strip()
    ]
    for campo in ("tono_distrito_positivo", "tono_solo_gestion",
                  "tono_alertas_tempranas", "tono_labor_positiva"):
        d[campo] = bool(d[campo])
    if d["agrupacion_estricta"] is not None:
        d["agrupacion_estricta"] = bool(d["agrupacion_estricta"])
    d["notas"] = str(d["notas"] or "")
    if d["taxonomia"]:
        d["taxonomia"] = {
            "nota": str(d["taxonomia"].get("nota") or "Lista fija del cliente."),
            "temas": [t.strip() for t in d["taxonomia"].get("temas", []) if str(t).strip()],
            "reglas": d["taxonomia"].get("reglas") or [],
        }
    return d


def guardar_perfil(data: dict) -> str:
    """Valida y guarda el perfil. Devuelve el id (slug). Lanza ValueError si es inválido."""
    errores = validar_perfil(data)
    if errores:
        raise ValueError("Perfil inválido: " + " | ".join(errores))
    d = normalizar_perfil(data)
    pid = slugify(d["nombre"])
    ruta = os.path.join(dir_clientes(), pid + ".json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return pid


def cargar_perfil(perfil_id: str) -> dict:
    """Lee un perfil por id (slug). Lanza FileNotFoundError si no existe."""
    ruta = os.path.join(dir_clientes(), perfil_id + ".json")
    with open(ruta, encoding="utf-8") as f:
        data = json.load(f)
    d = normalizar_perfil(data)
    d["id"] = perfil_id
    return d


def listar_perfiles() -> list:
    """Lista los perfiles disponibles: [{'id', 'nombre', 'brand'}], ordenados por nombre."""
    perfiles = []
    try:
        archivos = sorted(os.listdir(dir_clientes()))
    except OSError:
        return []
    for arch in archivos:
        if not arch.endswith(".json"):
            continue
        pid = arch[:-5]
        try:
            with open(os.path.join(dir_clientes(), arch), encoding="utf-8") as f:
                data = json.load(f)
            perfiles.append({
                "id": pid,
                "nombre": str(data.get("nombre") or data.get("brand") or pid),
                "brand": str(data.get("brand") or ""),
            })
        except (OSError, ValueError):
            continue
    return sorted(perfiles, key=lambda p: p["nombre"].lower())


def perfil_a_resumen(perfil: dict) -> str:
    """Línea descriptiva del perfil para la interfaz."""
    partes = []
    if perfil.get("aliases"):
        partes.append("alias: " + ", ".join(perfil["aliases"][:4]))
    if perfil.get("voceros"):
        partes.append("voceros: " + ", ".join(perfil["voceros"][:4]))
    partes.append("criterio: " + ("personalizado" if perfil.get("criterio_custom")
                                  else str(perfil.get("criterio"))))
    if (perfil.get("taxonomia") or {}).get("temas"):
        partes.append("%d temas fijos" % len(perfil["taxonomia"]["temas"]))
    return " · ".join(partes)
