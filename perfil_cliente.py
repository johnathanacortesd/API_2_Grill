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
        "guia_subtema": "",
        "subtemas_fijos": [],
        "clasificar_tipo_genero": False,
        "tono_distrito_positivo": False,
        "agrupacion_estricta": False,
        "tono_alertas_tempranas": False,
        "tono_labor_positiva": False,
        "formato_boletin": False,
        "hora_boletin_corte_manana": 13,
        "hora_boletin_corte_tarde": 18,
        "hora_boletin_manana": "1:00 p.m.",
        "hora_boletin_tarde": "6:00 p.m.",
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
    if not isinstance(d["clasificar_tipo_genero"], bool):
        errores.append("'clasificar_tipo_genero' debe ser true/false")
    if not isinstance(d["tono_distrito_positivo"], bool):
        errores.append("'tono_distrito_positivo' debe ser true/false")
    if not isinstance(d["agrupacion_estricta"], bool):
        errores.append("'agrupacion_estricta' debe ser true/false")
    if not isinstance(d["tono_alertas_tempranas"], bool):
        errores.append("'tono_alertas_tempranas' debe ser true/false")
    if not isinstance(d["tono_labor_positiva"], bool):
        errores.append("'tono_labor_positiva' debe ser true/false")
    if not isinstance(d["formato_boletin"], bool):
        errores.append("'formato_boletin' debe ser true/false")
    _cm, _ct = d["hora_boletin_corte_manana"], d["hora_boletin_corte_tarde"]
    _cm_ok = isinstance(_cm, int) and not isinstance(_cm, bool) and 0 <= _cm <= 23
    _ct_ok = isinstance(_ct, int) and not isinstance(_ct, bool) and 0 <= _ct <= 23
    if not _cm_ok:
        errores.append("'hora_boletin_corte_manana' debe ser una hora entera 0-23")
    if not _ct_ok:
        errores.append("'hora_boletin_corte_tarde' debe ser una hora entera 0-23")
    if _cm_ok and _ct_ok and _cm >= _ct:
        errores.append("'hora_boletin_corte_manana' debe ser menor que 'hora_boletin_corte_tarde'")
    if not isinstance(d["hora_boletin_manana"], str) or not isinstance(d["hora_boletin_tarde"], str):
        errores.append("'hora_boletin_manana' y 'hora_boletin_tarde' deben ser textos como '4:00 p.m.'")
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
    d["clasificar_tipo_genero"] = bool(d["clasificar_tipo_genero"])
    d["tono_distrito_positivo"] = bool(d["tono_distrito_positivo"])
    d["agrupacion_estricta"] = bool(d["agrupacion_estricta"])
    d["tono_alertas_tempranas"] = bool(d["tono_alertas_tempranas"])
    d["tono_labor_positiva"] = bool(d["tono_labor_positiva"])
    d["formato_boletin"] = bool(d["formato_boletin"])
    # v1.18/v1.14: compatibilidad con perfiles guardados con el modelo
    # viejo de un solo corte (hora_boletin_corte/antes/despues): si el
    # perfil no trae los campos nuevos, se mapean (corte_tarde=corte,
    # tarde=antes, manana=despues, corte_manana=0 reproduce el modelo
    # viejo exacto). Los JSON del repo ya traen los campos nuevos.
    _crudo = data or {}
    if "hora_boletin_corte_manana" not in _crudo and "hora_boletin_corte" in _crudo:
        d["hora_boletin_corte_tarde"] = _crudo.get("hora_boletin_corte")
        d["hora_boletin_corte_manana"] = 0
    if "hora_boletin_tarde" not in _crudo and "hora_boletin_antes" in _crudo:
        d["hora_boletin_tarde"] = _crudo.get("hora_boletin_antes")
    if "hora_boletin_manana" not in _crudo and "hora_boletin_despues" in _crudo:
        d["hora_boletin_manana"] = _crudo.get("hora_boletin_despues")
    _cm = d["hora_boletin_corte_manana"]
    d["hora_boletin_corte_manana"] = _cm if (isinstance(_cm, int)
        and not isinstance(_cm, bool) and 0 <= _cm <= 23) else 13
    _ct = d["hora_boletin_corte_tarde"]
    d["hora_boletin_corte_tarde"] = _ct if (isinstance(_ct, int)
        and not isinstance(_ct, bool) and 0 <= _ct <= 23) else 18
    d["hora_boletin_manana"] = (str(d["hora_boletin_manana"] or "1:00 p.m.").strip()
                                or "1:00 p.m.")
    d["hora_boletin_tarde"] = (str(d["hora_boletin_tarde"] or "6:00 p.m.").strip()
                               or "6:00 p.m.")
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
