# ======================================
# Pipeline de limpieza y análisis (pipeline.py)
# ======================================
import datetime
import gc
import html
import io
import logging
import math
import os
import re
import time
from typing import Callable, Dict, List, Optional, Tuple
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd
import xlsxwriter
from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, range_boundaries
from unidecode import unidecode

from analyzer_tono_tema import enrich_rows_with_ai, ultimo_resumen, aplicar_prominencia, _quitar_nombre_la_opinion
from pkl_classifier import (
    apply_pkl_classifiers,
    fill_classification_context,
    load_sklearn_estimator,
)

logger = logging.getLogger("limpieza_grill")

ProgressCb = Optional[Callable[[int, str], None]]
TIPOS_AV = frozenset({"Radio", "Televisión"})
TIPO_MEDIO_MAP = {
    "online": "Internet", "internet": "Internet",
    "diario": "Prensa",
    "am": "Radio", "fm": "Radio", "radio": "Radio",
    "aire": "Televisión", "cable": "Televisión", "tv": "Televisión",
    "television": "Televisión", "televisión": "Televisión",
    "revista": "Revistas", "revistas": "Revistas",
}

# v4.44: tipos de medio (ya normalizados) cuyos enlaces de streaming van a la
# columna "Audio". "Aire"/"Cable" -> "Televisión"; "AM"/"FM" -> "Radio".
TIPOS_MEDIO_AUDIO = frozenset({"Radio", "Televisión"})

# Columnas base (sin las 11 manuales obsoletas).
# No exportar `revalorización` ni `resumen corto`; este último sí puede leerse
# como fallback de entrada si el dossier fuente lo trae.
BASE_OUTPUT_COLUMNS = [
    "ID Noticia", "Fecha", "Hora", "Medio", "Tipo de Medio",
    "Sección - Programa", "Región", "Título", "Autor - Conductor",
    "Nro. Pagina", "Dimensión", "Duración - Nro. Caracteres",
    "CPE", "Tier", "Audiencia",
    "Link Nota", "Resumen - Aclaracion", "Link (Streaming - Imagen)", "Menciones - Empresa",
    "ID duplicada",
]
# Tono/Tema/Subtema se insertan después de Audiencia; Contexto analizado va al final.
AI_COLUMNS_AFTER_AUDIENCIA = ["Tono_IA", "Tema_IA", "Subtema_IA"]
CONTEXTO_ANALIZADO_COL = "Contexto analizado"



TEXTO_MIN_PALABRAS = 16  # v1.30: "más de 15 palabras"


def _oraciones(texto: str):
    return [o.strip() for o in re.split(r"(?<=[.!?…])\s+", " ".join(str(texto or "").split())) if o.strip()]


def texto_con_minimo(texto: str, resumen: str, titulo: str = "",
                     minimo: int = TEXTO_MIN_PALABRAS) -> str:
    """Columna «texto» con más de 15 palabras (v1.30, pedido del usuario).

    Si el texto derivado de Contexto analizado tiene menos de `minimo`
    palabras, se completa con oraciones COMPLETAS y LITERALES del
    «Resumen - Aclaracion» de la misma fila, sin agregar palabras que no
    estén ahí: primero las oraciones que siguen al texto dentro del
    resumen (si el texto aparece en él); si no aparece, las primeras
    oraciones del resumen que no estén ya incluidas. Se omite el titular
    (igual que en el cálculo original). Si el resumen no alcanza, queda
    lo que haya: nunca se rellena con texto inventado.
    """
    texto = " ".join(str(texto or "").split())
    if len(texto.split()) >= minimo:
        return texto
    res = " ".join(str(resumen or "").split())
    titulo = " ".join(str(titulo or "").split())
    if titulo:
        res = " ".join(res.replace(titulo, " ").split())
    if not res:
        return texto
    oraciones = _oraciones(res)
    if not texto:
        inicio, partes = 0, []
    else:
        partes = [texto]
        inicio = None
        pos = res.find(texto)
        if pos >= 0:
            fin = pos + len(texto)
            acum = 0
            for k, o in enumerate(oraciones):
                acum = res.find(o, acum)
                if acum >= fin or acum + len(o) > fin:
                    inicio = k if acum >= fin else k + 1
                    break
                acum += len(o)
            if inicio is None:
                inicio = len(oraciones)
        else:
            inicio = 0
    for o in oraciones[inicio:]:
        if len(" ".join(partes).split()) >= minimo:
            break
        if o in " ".join(partes):
            continue
        partes.append(o)
    # v1.33: si el texto está al FINAL del resumen (p. ej. el crédito
    # «Fuente: Alcaldía de Bogotá.»), no hay oraciones después: se toman
    # las oraciones inmediatamente ANTERIORES, literales y en su orden.
    if len(" ".join(partes).split()) < minimo and texto and res.find(texto) >= 0:
        fin_previas = max(0, (inicio or 0) - 1)
        previas = []
        for o in reversed(oraciones[:fin_previas]):
            if len(" ".join(previas + partes).split()) >= minimo:
                break
            if o in " ".join(partes):
                continue
            previas.insert(0, o)
        partes = previas + partes
    return " ".join(" ".join(partes).split())


def _audiencia_antes_de_tier(cols) -> List[str]:
    """v4.52: mueve "Audiencia" justo antes de "Tier" (si ambas están).

    Orden por defecto para todos los clientes, EXCEPTO Fenavi (que mantiene
    Tier antes de Audiencia). No toca ninguna otra columna.
    """
    cols = list(cols)
    if "Audiencia" in cols and "Tier" in cols:
        cols.remove("Audiencia")
        cols.insert(cols.index("Tier"), "Audiencia")
    return cols


def _es_perfil_fenavi(ai_config) -> bool:
    """True si el cliente es Fenavi (marca normalizada == "fenavi").

    Solo ese perfil conserva el orden anterior (Tier antes de Audiencia).
    """
    brand = ((ai_config or {}).get("brand") or "")
    return norm_key(brand) == "fenavi"


def output_columns_for_export(include_ai: bool = False, include_tema: bool = True,
                              include_prominencia: bool = False,
                              include_subtema: bool = True,
                              include_tipo_genero: bool = False,
                              audiencia_antes_de_tier: bool = True) -> List[str]:
    """Columnas del xlsx de salida.

    Con IA/PKL: inserta Tono_IA, Tema_IA, Subtema_IA después de Audiencia y deja
    Contexto analizado como última columna. Sin IA: solo BASE_OUTPUT_COLUMNS.
    v4.8: con include_tema=False se omite la columna Tema_IA (solo tono + subtema).
    v4.32: con include_subtema=False se omite Subtema_IA (modo solo-PKL sin IA:
    el subtema nunca lo genera un PKL y la columna saldría vacía).
    v4.23: con include_prominencia=True se agrega la columna Prominencia
    (métrica determinista de presencia de marca, sin LLM).
    v4.26: la columna Prominencia va al final del todo, después de
    "Contexto analizado" (con o sin IA).
    v4.44: la columna "Audio" (URL de streaming solo para Radio/Televisión)
    va al final de todo, en todos los modos.
    v4.55: la columna "texto" se agregó al final de todo, después de "Audio".
    v4.56: orden final ..., "texto", "Audio" ("Audio" vuelve a ser la última
    columna, después de "texto", en todos los modos). "texto" ya NO lleva
    fórmula: queda como texto plano con el mismo cálculo (Contexto analizado
    sin el Título, con espacios sobrantes limpiados).
    v4.57: al final de todo, después de "Audio", van "Link Ext" y "Web Ext"
    (enlace completo a la noticia; "Web Ext" solo para Online/Internet).
    v4.58: "Web Ext" toma su valor de la columna "URL Nota" (no de "Link Ext").
    """
    cols = list(BASE_OUTPUT_COLUMNS)
    if not include_ai:
        if include_prominencia:
            cols.append("Prominencia")
        # v4.56: "texto" antes de "Audio"; "Audio" siempre la última.
        if "texto" not in cols:
            cols.append("texto")
        cols.append("Audio")
        # v4.57: "Link Ext" y "Web Ext" al final de todo.
        cols.append("Link Ext")
        cols.append("Web Ext")
        # v4.52: Audiencia antes de Tier, salvo Fenavi.
        cols = _audiencia_antes_de_tier(cols) if audiencia_antes_de_tier else cols
        return cols
    audiencia_idx = cols.index("Audiencia")
    for offset, col in enumerate(AI_COLUMNS_AFTER_AUDIENCIA):
        if col not in cols:
            cols.insert(audiencia_idx + 1 + offset, col)
    if not include_tema and "Tema_IA" in cols:
        cols.remove("Tema_IA")
    if not include_subtema and "Subtema_IA" in cols:
        cols.remove("Subtema_IA")
    # v1.6: TIPO y GÉNERO justo antes de "Link Nota" (solo con IA y flag).
    if include_ai and include_tipo_genero:
        if "Link Nota" in cols:
            _idx_link = cols.index("Link Nota")
            for _off, _col in enumerate(("TIPO", "GÉNERO")):
                if _col not in cols:
                    cols.insert(_idx_link + _off, _col)
        elif "Subtema_IA" in cols:
            _idx_sub = cols.index("Subtema_IA")
            for _off, _col in enumerate(("TIPO", "GÉNERO")):
                if _col not in cols:
                    cols.insert(_idx_sub + 1 + _off, _col)
        else:
            for _col in ("TIPO", "GÉNERO"):
                if _col not in cols:
                    cols.append(_col)
    if CONTEXTO_ANALIZADO_COL in cols:
        cols = [c for c in cols if c != CONTEXTO_ANALIZADO_COL]
    cols.append(CONTEXTO_ANALIZADO_COL)
    # v4.26: la prominencia va al final, después de "Contexto analizado".
    if include_prominencia:
        cols.append("Prominencia")
    # v4.56: "texto" antes de "Audio"; "Audio" siempre la última.
    if "texto" not in cols:
        cols.append("texto")
    cols.append("Audio")
    # v4.57: "Link Ext" y "Web Ext" al final de todo.
    cols.append("Link Ext")
    cols.append("Web Ext")
    # v4.52: Audiencia antes de Tier, salvo Fenavi.
    cols = _audiencia_antes_de_tier(cols) if audiencia_antes_de_tier else cols
    return cols


def _columnas_preservadas(columnas_entrada, include_ai: bool = False,
                          include_tema: bool = True,
                          include_subtema: bool = True,
                          include_prominencia: bool = False,
                          include_tipo_genero: bool = False,
                          audiencia_antes_de_tier: bool = True) -> List[str]:
    """Columnas de salida para la pestaña "Columnas personalizadas" (v4.33).

    Conserva el xlsx como está: todas las columnas originales en su orden
    (incluida la primera columna y las de enlaces con hipervínculos, que el
    exportador reescribe como palabra "Link" clicable) y al final agrega las
    columnas nuevas del análisis. Nunca usa el esquema fijo
    BASE_OUTPUT_COLUMNS, que recortaba columnas personalizadas.
    """
    cols = [c for c in (columnas_entrada or []) if c]
    vistos = set(cols)
    if include_ai:
        for col in ("Tono_IA", "Tema_IA", "Subtema_IA"):
            if col == "Tema_IA" and not include_tema:
                continue
            if col == "Subtema_IA" and not include_subtema:
                continue
            if col not in vistos:
                cols.append(col)
                vistos.add(col)
        if CONTEXTO_ANALIZADO_COL not in vistos:
            cols.append(CONTEXTO_ANALIZADO_COL)
            vistos.add(CONTEXTO_ANALIZADO_COL)
    # v1.6: TIPO y GÉNERO justo antes de "Link Nota" (solo con IA y flag);
    # sin Link Nota, quedan tras las columnas de IA (antes de texto/Audio).
    if include_ai and include_tipo_genero:
        if "Link Nota" in cols:
            _idx_link = cols.index("Link Nota")
            for _off, _col in enumerate(("TIPO", "GÉNERO")):
                if _col not in cols:
                    cols.insert(_idx_link + _off, _col)
        else:
            for _col in ("TIPO", "GÉNERO"):
                if _col not in cols:
                    cols.append(_col)
    if include_prominencia and "Prominencia" not in vistos:
        cols.append("Prominencia")
    # v4.56: "texto" y "Audio" siempre al final, en ese orden (aunque el
    # xlsx de entrada ya trajera alguna de las dos, se reubica al final).
    # v4.57: "Link Ext" y "Web Ext" después de "Audio", al final de todo.
    cols = [c for c in cols if c not in ("texto", "Audio", "Link Ext", "Web Ext")]
    cols.append("texto")
    cols.append("Audio")
    cols.append("Link Ext")
    cols.append("Web Ext")
    # v4.52: Audiencia antes de Tier, salvo Fenavi (igual que la pestaña estándar).
    cols = _audiencia_antes_de_tier(cols) if audiencia_antes_de_tier else cols
    return cols


KEY_MAP = {
    "idnoticia": "ID Noticia",
    "fecha": "Fecha",
    "hora": "Hora",
    "medio": "Medio",
    "tipodemedio": "Tipo de Medio",
    "seccion_programa": "Sección - Programa",
    "region": "Región",
    "titulo": "Título",
    "autor_conductor": "Autor - Conductor",
    "nro_pagina": "Nro. Pagina",
    "dimension": "Dimensión",
    "duracion_caracteres": "Duración - Nro. Caracteres",
    "cpe": "CPE",
    "tier": "Tier",
    "audiencia": "Audiencia",
    "revalorizacion": "revalorización",
    "resumen_corto": "resumen corto",
    "link_nota": "Link Nota",
    "resumen": "Resumen - Aclaracion",
    "link_streaming": "Link (Streaming - Imagen)",
    "menciones": "Menciones - Empresa",
    "idduplicada": "ID duplicada",
}

THOUSANDS_COLS = {"Nro. Pagina", "Dimensión", "Tamaño", "Duración - Nro. Caracteres", "Tier", "Audiencia"}
CURRENCY_COLS = {"CPE"}
NUMERIC_COLS = {"ID Noticia", "ID duplicada"} | THOUSANDS_COLS | CURRENCY_COLS
# Display "Link" as black, non-underlined text while keeping the hyperlink.
PLAIN_HYPERLINK_COLUMNS = frozenset({"Link Nota", "Link (Streaming - Imagen)"})
PLAIN_HYPERLINK_COLUMNS_NORM = frozenset(c.strip().lower() for c in PLAIN_HYPERLINK_COLUMNS)

REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
HYPERLINK_TAIL_BYTES = 4 * 1024 * 1024

# v4.45 — Capa de seguridad: máximo de filas leídas por dossier. Evita
# llamadas masivas a la API si se sube un XLSX demasiado grande; el usuario
# debe dividir el archivo en partes de hasta este número de filas.
MAX_FILAS_POR_DOSSIER = 1000



# v1.9 (por perfil "formato_boletin"): orden exacto del
# xlsx final para el boletín, más dos columnas nuevas. FECHA INFORME es
# la fecha de procesamiento (Bogotá), NO la Fecha de la nota; HORA B es
# la hora del boletín según el horario del perfil y la hora de Bogotá
# en que se corre. En la pestaña estándar
# la salida es EXACTAMENTE las columnas del boletín; en la personalizada van
# estas primero y después cualquier columna personalizada extra (para
# no perder datos propios de esa pestaña). Tema_IA no está en la lista
# y por tanto no se exporta en este formato, aunque se haya calculado.
BOLETIN_COLUMNAS = [
    # v4.78 (Grill): «Hora» del dossier de origen, justo después de Fecha.
    "ID Noticia", "Fecha", "Hora", "Medio", "Región", "Tipo de Medio",
    # v4.81 (Grill): «Nro. Pagina» del origen, justo antes de la duración.
    "Sección - Programa", "Título", "Nro. Pagina", "Duración - Nro. Caracteres",
    # v4.79 (Grill): «Tamaño» = Dimensioncm2 del origen, justo antes de Audiencia.
    "CPE", "Tamaño", "Audiencia", "Tier", "Tono_IA", "Subtema_IA", "TIPO", "GÉNERO",
    # v4.81 (Grill): «Menciones - Empresa» (radio/TV) o «Empresa rel.»
    # (internet, prensa, revistas), justo después del enlace de streaming.
    "Link Nota", "texto", "Link (Streaming - Imagen)", "Menciones - Empresa", "Link Ext",
    # v4.74 (versión general): sin «HORA B» (hora del boletín de Defensoría).
    "FECHA INFORME",
    "ID duplicada", "Contexto analizado", "Resumen - Aclaracion", "Audio",
    "Web Ext"
]


def columnas_boletin(include_tipo_genero=True):
    # Lista del boletín: completa para Secretaría (con TIPO y GÉNERO) y
    # sin esas dos para Defensoría (que no las clasifica). v1.11: "Link
    # (Streaming - Imagen)" en vez de "Web Ext" (corrección del usuario
    # 2026-10-31: el xlsx final lleva el enlace de streaming, no la web
    # externa).
    if include_tipo_genero:
        return list(BOLETIN_COLUMNAS)
    return [c for c in BOLETIN_COLUMNAS if c not in ("TIPO", "GÉNERO")]


def _zona_bogota():
    """Zona horaria America/Bogota; si no hay tzdata, UTC-5 fijo
    (Bogotá no tiene horario de verano, así que es equivalente)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Bogota")
    except Exception:
        return datetime.timezone(datetime.timedelta(hours=-5))


def hora_boletin_bogota(ahora=None, corte_manana=13, corte_tarde=18,
                        manana="1:00 p.m.", tarde="6:00 p.m."):
    """Hora del boletín según la hora de Bogotá y el horario del perfil.

    Dos boletines al día: el de la mañana (`manana`) y el de la tarde
    (`tarde`). La etiqueta es la del PRÓXIMO boletín: desde
    `corte_manana` y antes de `corte_tarde` -> `tarde`; resto del día
    (madrugada y después de `corte_tarde`) -> `manana`. Límites
    inclusivos en los cortes, igual que antes.
    Secretaría (por defecto): mañana "1:00 p.m." (corte 13), tarde
    "6:00 p.m." (corte 18). Defensoría: mañana "8:00 a.m." (corte 8),
    tarde "4:00 p.m." (corte 16). Si `ahora` viene sin zona se asume
    Bogotá; con zona se convierte a Bogotá (la app corre en un
    servidor en UTC, no en hora local). Corte inválido -> su defecto.
    """
    zona = _zona_bogota()
    if ahora is None:
        ahora = datetime.datetime.now(zona)
    elif ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=zona)
    else:
        ahora = ahora.astimezone(zona)
    _cm = corte_manana if (isinstance(corte_manana, int) and not isinstance(corte_manana, bool)
                           and 0 <= corte_manana <= 23) else 13
    _ct = corte_tarde if (isinstance(corte_tarde, int) and not isinstance(corte_tarde, bool)
                         and 0 <= corte_tarde <= 23) else 18
    _man = (str(manana or "1:00 p.m.").strip() or "1:00 p.m.")
    _tar = (str(tarde or "6:00 p.m.").strip() or "6:00 p.m.")
    return _tar if (_cm <= ahora.hour < _ct) else _man

class LimiteFilasError(ValueError):
    """El dossier supera MAX_FILAS_POR_DOSSIER filas leídas."""

# v1.7 (Defensoría): notas ya analizadas en Google Sheets. La app
# compara el ID del dossier (columna "NoticiaId", normalizada a
# "ID Noticia") con la columna "ID" de la hoja de notas analizadas
# de Defensoría (primera hoja) y solo procesa las notas que NO están
# en la hoja. La hoja se lee en cada corrida vía exportación CSV
# pública (sin inicio de sesión), igual que los mapas de región/
# internet. Si la hoja no se puede leer y el filtro está activado,
# la app se detiene con un mensaje claro en vez de reprocesar en
# silencio (el usuario puede desactivar el filtro y procesar todo).
SHEETS_ANALIZADAS_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1lJkddJkGcJYqZ3byHRcwy2Iku2udFgDhUTayu_ykw3E/export?format=csv"
)


class ErrorSheetsAnalizadas(ValueError):
    """No se pudo leer la hoja de notas ya analizadas (o no trae columna ID)."""


def normalizar_id(v):
    """ID normalizado para comparar dossier vs Sheets; None si no hay ID.

    Acepta int/float/str: 11714750, 11714750.0 y " 11714750 " comparan
    igual. Un ID vacío, NaN o "nan"/"None" no se puede emparejar, así
    que devuelve None y la fila se procesa (no se omite por duda).
    """
    if v is None:
        return None
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _columna_id_dossier(df):
    """Nombre de la columna de ID del dossier, o None si no la trae.

    Acepta "NoticiaId" y "ID Noticia" (exactos) y, como respaldo,
    cualquier columna cuyo nombre normalizado (sin espacios, en
    minúsculas) sea "noticiaid" o "idnoticia".
    """
    for cand in ("NoticiaId", "ID Noticia"):
        if cand in df.columns:
            return cand
    for c in df.columns:
        k = str(c).replace(" ", "").replace("_", "").lower()
        if k in ("noticiaid", "idnoticia"):
            return c
    return None


def cargar_ids_analizados_desde_sheets(url=None):
    """Lee la columna ID de la hoja de notas ya analizadas y devuelve
    el conjunto de IDs normalizados. Sin caché: cada corrida lee la
    hoja actual (el usuario puede agregar notas analizadas entre
    corridas). Lanza ErrorSheetsAnalizadas si no se puede leer o si
    no trae columna ID."""
    url = url or SHEETS_ANALIZADAS_CSV_URL
    # v1.8-rendimiento: la hoja trae 32 mil filas y 21 columnas, pero solo
    # hace falta la columna ID. Se pide solo esa columna (usecols) para
    # no parsear ni cargar en memoria títulos y resúmenes de 32 mil
    # notas; la descarga en sí es la misma (Google no permite descargar
    # una sola columna), pero el parseo pasa de 21 columnas a 1. Si la
    # columna no se llama exactamente "ID", se hace la lectura completa
    # y la búsqueda tolerante de antes.
    try:
        try:
            df = pd.read_csv(url, dtype=str, usecols=["ID"])
            id_col = "ID"
        except ValueError:
            df = pd.read_csv(url, dtype=str)
            id_col = None
            for c in df.columns:
                _k = str(c).replace(" ", "").replace("_", "").lower()
                if _k in ("id", "idnoticia", "noticiaid"):
                    id_col = c
                    break
            if id_col is None:
                raise ErrorSheetsAnalizadas(
                    "La hoja de notas ya analizadas no trae una columna 'ID'."
                )
    except ErrorSheetsAnalizadas:
        raise
    except Exception as exc:
        raise ErrorSheetsAnalizadas(
            "No se pudo leer la hoja de notas ya analizadas en Google Sheets. "
            "Revisa que esté compartida como 'Cualquier persona con el enlace "
            "puede ver' o desactiva el filtro para procesar todas las notas."
        ) from exc
    ids = set()
    for v in df[id_col]:
        n = normalizar_id(v)
        if n is not None:
            ids.add(n)
    return ids


def cargar_hoja_analizadas(url=None, con_referencias=True):
    """v1.30: una sola lectura de la hoja de notas ya analizadas para dos
    usos: los IDs ya analizados (filtro) y la referencia de subtemas ya
    publicados (TÍTULO, RESUMEN y TEMA de las notas recientes), que la
    corrida usa para que la misma noticia retome el TEMA de la hoja.
    Devuelve (ids, referencias). Lanza ErrorSheetsAnalizadas si no se puede
    leer o no trae columna ID."""
    if not con_referencias:
        return cargar_ids_analizados_desde_sheets(url), []
    url = url or SHEETS_ANALIZADAS_CSV_URL
    try:
        df = pd.read_csv(url, dtype=str, keep_default_na=False)
    except Exception as exc:
        raise ErrorSheetsAnalizadas(
            "No se pudo leer la hoja de notas ya analizadas en Google Sheets. "
            "Revisa que esté compartida como 'Cualquier persona con el enlace "
            "puede ver' o desactiva el filtro para procesar todas las notas."
        ) from exc
    id_col = None
    for c in df.columns:
        if str(c).replace(" ", "").replace("_", "").lower() in ("id", "idnoticia", "noticiaid"):
            id_col = c
            break
    if id_col is None:
        raise ErrorSheetsAnalizadas("La hoja de notas ya analizadas no trae una columna 'ID'.")
    ids = {n for n in (normalizar_id(v) for v in df[id_col]) if n is not None}
    referencias = []
    try:
        import memoria_cliente
        # Solo las últimas filas (las notas recientes); además se filtra por fecha.
        cola = df.tail(8000)
        referencias = memoria_cliente.referencias_de_tabla(
            list(cola.columns), cola.itertuples(index=False, name=None))
    except Exception:
        logging.getLogger(__name__).exception(
            "No se pudieron leer los subtemas de la hoja; se sigue sin ellos.")
    return ids, referencias


def filtrar_ids_analizados(df, ids_set):
    """Quita del dossier las filas cuyo ID ya está analizado.

    Devuelve (df_filtrado, n_omitidas, aplicado, tiene_col_id). Si el
    dossier no trae columna de ID, no se omite nada (aplicado=False)
    y se procesa todo: sin ID no se puede probar que una nota ya
    esté analizada. Las filas sin ID tampoco se omiten nunca."""
    col = _columna_id_dossier(df)
    if col is None:
        return df, 0, False, False
    normas = df[col].map(normalizar_id)
    mask = normas.map(lambda x: x is not None and x in ids_set)
    n_omit = int(mask.sum())
    if n_omit:
        df = df[~mask].reset_index(drop=True)
    return df, n_omit, True, True



def _fmt_miles(n: int) -> str:
    """Formatea un entero con punto como separador de miles (es)."""
    return f"{int(n):,}".replace(",", ".")


def emit_progress(progress: ProgressCb, pct: int, msg: str) -> None:
    pct = max(0, min(100, int(pct)))
    logger.info("%s%% %s", pct, msg)
    if progress:
        progress(pct, msg)


def file_to_bytes(file_obj) -> bytes:
    if isinstance(file_obj, (bytes, bytearray)):
        return bytes(file_obj)
    if hasattr(file_obj, "getvalue"):
        return file_obj.getvalue()
    if hasattr(file_obj, "read"):
        pos = file_obj.tell() if hasattr(file_obj, "tell") else None
        data = file_obj.read()
        if pos is not None and hasattr(file_obj, "seek"):
            try:
                file_obj.seek(pos)
            except Exception:
                pass
        if isinstance(data, bytes):
            return data
        return str(data).encode("utf-8")
    with open(file_obj, "rb") as fh:
        return fh.read()


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _workbook_rel_path(target: str) -> str:
    t = (target or "").lstrip("/")
    if t.startswith("xl/"):
        return t
    if t.startswith("worksheets/") or t.startswith("theme/") or t == "styles.xml":
        return "xl/" + t
    return t


def extract_hyperlinks_from_xlsx(xlsx_bytes: bytes, sheet_title: str) -> Dict[Tuple[int, int], str]:
    result: Dict[Tuple[int, int], str] = {}
    try:
        with ZipFile(io.BytesIO(xlsx_bytes)) as zf:
            wb_tree = ET.fromstring(zf.read("xl/workbook.xml"))
            rels_tree = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            rid_to_target = {
                rel.attrib.get("Id"): rel.attrib.get("Target")
                for rel in rels_tree
                if rel.attrib.get("Id") and rel.attrib.get("Target")
            }

            sheet_path = None
            for el in wb_tree.iter():
                if _local_tag(el.tag) != "sheet":
                    continue
                if el.attrib.get("name") != sheet_title:
                    continue
                rid = el.attrib.get(REL_NS + "id") or el.attrib.get("r:id")
                target = rid_to_target.get(rid, "")
                sheet_path = _workbook_rel_path(target)
                break

            if not sheet_path:
                return result
            if sheet_path not in zf.namelist():
                alt = sheet_path[3:] if sheet_path.startswith("xl/") else "xl/" + sheet_path
                if alt in zf.namelist():
                    sheet_path = alt
                else:
                    return result

            dirname, fname = sheet_path.rsplit("/", 1)
            rels_path = f"{dirname}/_rels/{fname}.rels"
            url_by_rid = {}
            if rels_path in zf.namelist():
                st_rels = ET.fromstring(zf.read(rels_path))
                for rel in st_rels:
                    rel_type = rel.attrib.get("Type", "")
                    if rel.attrib.get("TargetMode") == "External" or "hyperlink" in rel_type:
                        rid = rel.attrib.get("Id")
                        target = rel.attrib.get("Target")
                        if rid and target:
                            url_by_rid[rid] = target

            with zf.open(sheet_path) as sheet_fh:
                xml_tail = _read_xml_tail(sheet_fh, HYPERLINK_TAIL_BYTES)

            idx = xml_tail.rfind(b"<hyperlinks")
            if idx < 0:
                return result
            end = xml_tail.find(b"</hyperlinks>", idx)
            chunk = xml_tail[idx:] if end < 0 else xml_tail[idx:end + len(b"</hyperlinks>")]
            try:
                hyper_tree = ET.fromstring(chunk)
            except ET.ParseError:
                logger.warning("No se pudo parsear el bloque de hipervínculos; se continúa sin ellos.")
                return result

            for el in hyper_tree.iter():
                if _local_tag(el.tag) != "hyperlink":
                    continue
                ref = el.attrib.get("ref")
                rid = el.attrib.get(REL_NS + "id") or el.attrib.get("r:id")
                target = url_by_rid.get(rid) if rid else None
                if not target:
                    target = el.attrib.get("location")
                if not ref or not target:
                    continue
                _assign_hyperlink_ref(result, ref, target)
    except Exception:
        logger.exception("Fallo al extraer hipervínculos; se continúa con los valores de celda.")
    return result


def _read_xml_tail(sheet_fh, tail_bytes: int) -> bytes:
    try:
        sheet_fh.seek(0, os.SEEK_END)
        size = sheet_fh.tell()
        start = max(0, size - tail_bytes)
        sheet_fh.seek(start)
        return sheet_fh.read()
    except (OSError, AttributeError):
        return sheet_fh.read()


def _assign_hyperlink_ref(result: dict, ref: str, target: str) -> None:
    if ":" in ref:
        min_col, min_row, max_col, max_row = range_boundaries(ref)
        for r in range(min_row, max_row + 1):
            for c in range(min_col, max_col + 1):
                result[(r, c)] = target
        return
    col_letter, row = coordinate_from_string(ref)
    result[(row, column_index_from_string(col_letter))] = target


# ======================================
# Utilidades de Limpieza de Texto
# ======================================
_RX_FECHA_ISO = re.compile(r"^\s*\d{4}-\d{1,2}-\d{1,2}(?:[ T].*)?$")


def _leer_fechas(serie) -> "pd.Series":
    """v4.78: fechas del dossier. Día primero (dd/mm/aaaa, formato local),
    salvo el formato ISO «aaaa-mm-dd», que se lee como tal: con dayfirst
    pandas convertía «2026-09-01» en el 9 de enero. Las fechas que el Excel
    ya guarda como fecha no cambian."""
    serie = pd.Series(serie)
    iso = serie.map(lambda v: isinstance(v, str) and bool(_RX_FECHA_ISO.match(v)))
    out = pd.to_datetime(serie.where(~iso), dayfirst=True, errors="coerce", format="mixed")
    if iso.any():
        out = out.where(~iso, pd.to_datetime(serie.where(iso), yearfirst=True, dayfirst=False,
                                              errors="coerce", format="mixed"))
    return out.dt.normalize()


def norm_key(text):
    if text is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", unidecode(str(text).strip().lower()))


def get_column_robust(df, name):
    name_norm = norm_key(name)
    for col in df.columns:
        if norm_key(col) == name_norm:
            return df[col]
    return pd.Series([np.nan] * len(df), index=df.index)


def clean_text(text):
    if not isinstance(text, str):
        return text
    return re.sub(r"\s+", " ", text).strip()


def clean_cuerpo(text):
    if not isinstance(text, str) or text.strip() in ("", "nan", "None"):
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # v4.47: el cuerpo (Resumen - Aclaracion / CuerpoEs) se mantiene exacto:
    # la única limpieza es decodificar las entidades HTML, igual que en los
    # títulos (v4.46). No se quita, recorta ni reordena ninguna palabra.
    return decode_html_entities(text).strip()


def decode_html_entities(text):
    """Decodifica las entidades HTML de un texto (numéricas hex/dec y con
    nombre) a su carácter real: "&#x201C;" -> “ , "&#xF3;" -> ó, "&amp;" -> &.

    Los exports de algunas plataformas traen los títulos con las entidades
    sin decodificar. Solo se convierten las entidades: no se quita, recorta
    ni reordena nada más del texto (un "|" o un "-" del título se conserva).
    Hasta 3 pasadas por si la entidad viene doblemente codificada
    ("&amp;#xF3;" -> "&#xF3;" -> "ó").
    """
    if not isinstance(text, str):
        return text
    out = text
    for _ in range(3):
        decoded = html.unescape(out)
        if decoded == out:
            break
        out = decoded
    return out


def clean_title_for_output(title):
    if not isinstance(title, str):
        return ""
    return re.sub(r"\s+", " ", decode_html_entities(str(title))).strip()


def terminar_en_punto(text):
    """Deja el texto con un solo punto final (nunca elipsis '...')."""
    if not isinstance(text, str) or not text.strip():
        return text
    return text.rstrip(".") + "."


def corregir_texto(text):
    """Limpieza de salida del Resumen - Aclaracion.

    v4.47: el texto se mantiene EXACTO. La versión anterior buscaba la
    primera letra mayúscula y descartaba todo lo que hubiera antes, así
    que un resumen que empieza con un número o en minúscula perdía
    palabras (p. ej. "2026 arranca con ventas récord en Colombia" salía
    como "Colombia."), y borraba los "[...]" y colapsaba los párrafos.
    Ahora la limpieza es solo la misma de los títulos (v4.46):
    decodificar las entidades HTML ("creci&#xF3;" -> "creció"), sin
    quitar ni recortar ninguna palabra, y el cierre de siempre: el texto
    termina en UN solo punto, nunca en tres ("..."), con
    `terminar_en_punto`.
    """
    if not isinstance(text, str) or text.strip() in ("", "nan", "None"):
        return ""
    return terminar_en_punto(decode_html_entities(text).strip())


def normalizar_tipo_medio(tipo_raw):
    if not isinstance(tipo_raw, str):
        return str(tipo_raw)
    t = unidecode(tipo_raw.strip().lower())
    return TIPO_MEDIO_MAP.get(t, str(tipo_raw).strip().title() or "Otro")


# v4.80: columnas que pueden traer una duración (radio y TV).
COLS_DURACION = {"Duración - Nro. Caracteres", "Tamaño", "Dimensión"}
_RX_DURACION = re.compile(r"^\s*\d{1,3}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*$")


def _es_duracion(val) -> bool:
    if isinstance(val, (datetime.time, datetime.timedelta)):
        return True
    if isinstance(val, datetime.datetime):
        return False
    return isinstance(val, str) and bool(_RX_DURACION.match(val))


def _texto_duracion(val) -> str:
    if isinstance(val, datetime.timedelta):
        seg = int(round(val.total_seconds()))
        return "%02d:%02d:%02d" % (seg // 3600, (seg % 3600) // 60, seg % 60)
    if isinstance(val, datetime.time):
        return val.strftime("%H:%M:%S")
    return str(val).strip()


def parse_numeric(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val.is_integer():
            return int(val)
        return val
    s = str(val).strip()
    if not s:
        return None

    s = re.sub(r"[^\d.,\-eE]", "", s)
    if not s:
        return None

    num_dots = s.count(".")
    num_commas = s.count(",")

    if num_dots > 1 and num_commas == 0:
        s = s.replace(".", "")
    elif num_commas > 1 and num_dots == 0:
        s = s.replace(",", "")
    elif num_dots > 0 and num_commas > 0:
        dot_idx = s.rfind(".")
        comma_idx = s.rfind(",")
        if dot_idx > comma_idx:
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif num_dots == 1:
        parts = s.split(".")
        if len(parts[1]) == 3:
            s = s.replace(".", "")
    elif num_commas == 1:
        parts = s.split(",")
        if len(parts[1]) == 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")

    try:
        f_val = float(s)
        if f_val.is_integer():
            return int(f_val)
        return f_val
    except ValueError:
        return None


# ======================================
# Algoritmo de Duplicados
# ======================================
def _normalizar_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.rstrip("/")
    return url


def _extract_url(val) -> str:
    if val is None:
        return ""
    if isinstance(val, dict):
        return str(val.get("url") or "").strip()
    if isinstance(val, float) and pd.isna(val):
        return ""
    s = str(val).strip()
    if s.lower() in ("", "nan", "none", "link"):
        return ""
    return s


def _tipo_medio_norm(row) -> str:
    """Tipo de Medio normalizado de la fila (misma regla que la app)."""
    t = row.get("Tipo de Medio")
    if t is None:
        return ""
    s = str(t).strip()
    if not s:
        return ""
    return TIPO_MEDIO_MAP.get(s.lower(), s)


def _url_link_ext(row) -> str:
    """Enlace completo de la noticia para la columna "Link Ext" (v4.57).

    - Radio/Televisión (AM/FM/Aire/Cable): la URL incrustada en "Link Nota"
      (viene de "Link Nota AV": news.globalnews.com.co/Validar.aspx?...).
    - Internet/Online: la URL de "URL (Streaming - Imagen)" (con respaldo en
      "Link Nota", que para estas filas trae la misma URL).
    - Prensa/Revistas (Diario/Prensa/Revista): la URL de "Link (Streaming -
      Imagen)" (con respaldo en "Link Nota" y en "URL (Streaming - Imagen)",
      que para estas filas traen el mismo enlace Validar).
    - Otros tipos: vacío.
    """
    tipo = _tipo_medio_norm(row)
    if tipo in TIPOS_MEDIO_AUDIO:
        return _extract_url(row.get("Link Nota"))
    if tipo == "Internet":
        return _extract_url(row.get("URL (Streaming - Imagen)")) or _extract_url(row.get("Link Nota"))
    if tipo in ("Prensa", "Revistas"):
        return (_extract_url(row.get("Link (Streaming - Imagen)"))
                or _extract_url(row.get("Link Nota"))
                or _extract_url(row.get("URL (Streaming - Imagen)")))
    return ""


_EDITORIAL_URL_STEMS = ('editorial',)
_REPORTAJE_URL_STEMS = ('reportaje', 'especial', 'investigacion')
_OPINION_URL_STEMS = ('opinion', 'columna')


def aplicar_genero_por_url(rows) -> int:
    """GÉNERO por la URL web (v1.13/v1.9, 2026-10-31): si la Web Ext de la
    nota (URL Nota, solo Internet, el mismo valor que se exporta) trae
    /editorial/, /reportaje/, /especial/, /investigacion/, /opinion/ o
    /columna(s)/ y el grupo quedó como Noticia, la fila pasa a
    Editorial, Reportaje u Opinión en ese orden (v1.20). Solo sube Noticia a Opinión: nunca sobrescribe
    una Entrevista, una Opinión ya asignada ni una duplicada ('-'). Es el
    mismo criterio del rescate por Sección, pero con la evidencia de la
    URL (una columna de opinión como la de elpilon.com.co/opinion/...
    ya no queda como Noticia). Solo tiene efecto donde se clasifica
    género (Secretaría); donde GÉNERO no se clasifica queda vacío y no
    hace nada. Devuelve nº de filas cambiadas."""
    cambios = 0
    for row in rows:
        if row.get('is_duplicate'):
            continue
        if (row.get('GÉNERO') or '') != 'Noticia':
            continue
        # v1.19: el nombre del diario La Opinión no cuenta como Opinión
        # (su dominio contiene "opinion" pero no es una columna de opinión).
        url = _quitar_nombre_la_opinion((_url_web_ext(row) or '').lower())
        # v1.20: orden Editorial -> Reportaje -> Opinión; solo desde
        # Noticia (las demás guardas no cambian).
        if any(s in url for s in _EDITORIAL_URL_STEMS):
            row['GÉNERO'] = 'Editorial'
            cambios += 1
        elif any(s in url for s in _REPORTAJE_URL_STEMS):
            row['GÉNERO'] = 'Reportaje'
            cambios += 1
        elif any(s in url for s in _OPINION_URL_STEMS):
            row['GÉNERO'] = 'Opinión'
            cambios += 1
    return cambios


def aplicar_genero_por_frase_entrevista(rows) -> int:
    """GÉNERO por la frase "En entrevista con" (v1.19, orden del usuario):
    si la noticia dice "En entrevista con" (en el título o en el cuerpo)
    y quedó como Noticia, pasa a Entrevista. Solo sube Noticia a
    Entrevista: nunca sobrescribe una Opinión, una Entrevista ya
    asignada ni una duplicada, y donde GÉNERO no se clasifica (vacío)
    no hace nada, igual que el rescate por URL. Devuelve nº de filas
    cambiadas."""
    cambios = 0
    for row in rows:
        if row.get('is_duplicate'):
            continue
        if (row.get('GÉNERO') or '') != 'Noticia':
            continue
        texto = ' '.join(str(row.get(k) or '') for k in
                         ('Título', 'CuerpoEs', 'Resumen - Aclaracion'))
        texto = re.sub(r'\s+', ' ', texto).lower()
        if 'en entrevista con' in texto:
            row['GÉNERO'] = 'Entrevista'
            cambios += 1
    return cambios


def _url_web_ext(row) -> str:
    """Enlace para la columna "Web Ext" (v4.58): solo Online/Internet
    (normalizado a "Internet") y tomado de la columna "URL Nota" (el enlace
    al artículo en el medio, distinto del Validar de GlobalNews que lleva
    "Link Ext"); los demás tipos quedan vacíos."""
    if _tipo_medio_norm(row) == "Internet":
        return _extract_url(row.get("URL Nota"))
    return ""


def _normalizar_hora(val) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and pd.isna(val):
        return ""
    if isinstance(val, pd.Timestamp):
        if pd.isna(val):
            return ""
        return val.strftime("%H:%M:%S")
    if isinstance(val, datetime.datetime):
        return val.strftime("%H:%M:%S")
    if isinstance(val, datetime.time):
        return val.strftime("%H:%M:%S")
    s = str(val).strip()
    if s.lower() in ("", "nan", "nat", "none"):
        return ""
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if 0 <= h < 24 and 0 <= mi < 60 and 0 <= se < 60:
            return f"{h:02d}:{mi:02d}:{se:02d}"
    return s


def detectar_duplicados_avanzado(rows, km):
    processed = rows
    seen_url = {}
    seen_bcast = {}

    for i, row in enumerate(processed):
        if row.get("is_duplicate"):
            continue

        tipo = normalizar_tipo_medio(str(row.get(km["tipodemedio"], "")))
        mencion = norm_key(row.get(km["menciones"], ""))
        if not mencion:
            continue

        if tipo in TIPOS_AV:
            medio = norm_key(row.get(km["medio"], ""))
            hora = _normalizar_hora(row.get(km["hora"]))
            if not medio or not hora:
                continue
            k = (mencion, medio, hora)
            if k in seen_bcast:
                row["is_duplicate"] = True
                row[km["idduplicada"]] = processed[seen_bcast[k]].get(km["idnoticia"], "")
            else:
                seen_bcast[k] = i
            continue

        url = _extract_url(row.get("URL Nota")) or _extract_url(row.get(km["link_streaming"]))
        if not url:
            continue
        url_norm = _normalizar_url(url)
        if not url_norm:
            continue
        k = (url_norm, mencion)
        if k in seen_url:
            row["is_duplicate"] = True
            row[km["idduplicada"]] = processed[seen_url[k]].get(km["idnoticia"], "")
        else:
            seen_url[k] = i

    return processed


# ======================================
# Lectura y Estructuración de Datos
# ======================================
def load_dossier_dataframe(file_bytes: bytes, progress: ProgressCb = None) -> pd.DataFrame:
    emit_progress(progress, 8, "Abriendo archivo Excel…")
    try:
        return _load_dossier_calamine(file_bytes, progress)
    except Exception:
        logger.exception("Calamine no pudo leer el xlsx; se usa openpyxl.")
        return _load_dossier_openpyxl(file_bytes, progress)


def _rows_to_dataframe(raw_headers, data_rows, hyperlinks, progress: ProgressCb = None) -> pd.DataFrame:
    rows = []
    empty_streak = 0
    for ridx, values in enumerate(data_rows, start=2):
        if all(v is None or v == "" for v in values):
            empty_streak += 1
            if empty_streak >= 50:
                break
            continue
        empty_streak = 0
        row_data = {}
        for i, h in enumerate(raw_headers):
            if not h:
                continue
            val = values[i] if i < len(values) else None
            url = hyperlinks.get((ridx, i + 1))
            if url:
                row_data[h] = {"value": val or "Link", "url": url}
            else:
                row_data[h] = val
        rows.append(row_data)
        if progress and (len(rows) % 800 == 0):
            emit_progress(
                progress,
                min(39, 16 + len(rows) // 400),
                f"Leyendo filas del dossier… {len(rows)}",
            )
    emit_progress(progress, 40, f"Leídas {len(rows)} filas. Normalizando columnas…")
    return pd.DataFrame(rows)


def _load_dossier_calamine(file_bytes: bytes, progress: ProgressCb = None) -> pd.DataFrame:
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_filelike(io.BytesIO(file_bytes))
    sheet_title = wb.sheet_names[0]
    emit_progress(progress, 12, f"Extrayendo hipervínculos de «{sheet_title}»…")
    hyperlinks = extract_hyperlinks_from_xlsx(file_bytes, sheet_title)
    emit_progress(progress, 16, "Leyendo celdas del Excel…")
    sheet = wb.get_sheet_by_name(sheet_title)
    data = sheet.to_python(skip_empty_area=False)
    emit_progress(progress, 22, f"Celdas leídas ({len(data)} filas). Armando tabla…")
    if not data:
        return pd.DataFrame()
    raw_headers = list(data[0])
    return _rows_to_dataframe(raw_headers, data[1:], hyperlinks, progress)


def _load_dossier_openpyxl(file_bytes: bytes, progress: ProgressCb = None) -> pd.DataFrame:
    emit_progress(progress, 8, "Abriendo archivo Excel (openpyxl)…")
    bio = io.BytesIO(file_bytes)
    wb = load_workbook(bio, read_only=True, data_only=True)
    try:
        sheet = wb.active
        sheet_title = sheet.title or "Sheet"
        emit_progress(progress, 12, f"Extrayendo hipervínculos de «{sheet_title}»…")
        hyperlinks = extract_hyperlinks_from_xlsx(file_bytes, sheet_title)
        emit_progress(progress, 16, "Leyendo filas del dossier…")
        it = sheet.iter_rows()
        header_row = next(it, None)
        if header_row is None:
            return pd.DataFrame()
        raw_headers = [c.value for c in header_row]
        data_rows = ([c.value for c in row] for row in it)
        return _rows_to_dataframe(raw_headers, data_rows, hyperlinks, progress)
    finally:
        wb.close()
        bio.close()


# v4.27 — Pestaña "Columnas personalizadas": permite elegir qué columnas del
# xlsx se usan como título y cuerpo (CuerpoEs) cuando no se llaman "Título" ni
# "Resumen - Aclaracion". El análisis (tono/tema/subtema) no cambia: solo se
# renombran los encabezados elegidos antes de entrar al pipeline normal.
def leer_columnas_xlsx(file_bytes: bytes) -> List[Tuple[str, int]]:
    """Encabezados de la primera hoja como [(etiqueta, indice)].

    La etiqueta es única (los duplicados se sufijan con su posición) y el
    índice es 1-based (para openpyxl). Devuelve [] si no se puede leer.
    """
    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        try:
            ws = wb.active
            raw = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), [])
        finally:
            wb.close()
    except Exception:
        logger.exception("No se pudieron leer las columnas del xlsx")
        return []
    vistos = {}
    out = []
    for i, h in enumerate(raw, start=1):
        nombre = str(h).strip() if h is not None else ""
        if not nombre:
            continue
        vistos[nombre] = vistos.get(nombre, 0) + 1
        etiqueta = nombre if vistos[nombre] == 1 else "%s (%d)" % (nombre, vistos[nombre])
        out.append((etiqueta, i))
    return out


def sugerir_columna(etiquetas: List[str], candidatos) -> Optional[str]:
    """Primera etiqueta cuyo nombre normalizado coincide con un candidato.

    v4.29: dos pasadas — primero coincidencia exacta (p. ej. "Título"/"Titulo"
    para el título, "Resumen"/"resumen" para el cuerpo), luego parcial. Así un
    "Subtitulo" no le gana a un "Título" que venga después en la lista.
    """
    cands = [unidecode(str(c)).lower().strip() for c in candidatos if str(c).strip()]
    norm = []
    for lbl in etiquetas:
        base = re.sub(r"\s*\(\d+\)$", "", str(lbl))
        norm.append((lbl, unidecode(base).lower().strip()))
    for lbl, nbase in norm:  # pasada 1: exacta
        if nbase and nbase in cands:
            return lbl
    for lbl, nbase in norm:  # pasada 2: parcial
        if not nbase:
            continue
        for cand in cands:
            if nbase in cand or cand in nbase:
                return lbl
    return None


def renombrar_columnas_xlsx(file_bytes: bytes, etiqueta_titulo: str,
                            etiqueta_cuerpo: str) -> bytes:
    """Copia del xlsx con los encabezados elegidos renombrados a 'Título' y
    'Resumen - Aclaracion'. El resto del libro queda intacto.

    Si otra columna ya se llamaba así, se aparta con el sufijo "(original)"
    para no crear duplicados. Lanza ValueError si las etiquetas no existen o
    si ambas apuntan a la misma columna.
    """
    cols = leer_columnas_xlsx(file_bytes)
    por_etiqueta = {lbl: idx for lbl, idx in cols}
    if etiqueta_titulo not in por_etiqueta or etiqueta_cuerpo not in por_etiqueta:
        raise ValueError("No se encontraron las columnas elegidas en el archivo.")
    idx_t, idx_c = por_etiqueta[etiqueta_titulo], por_etiqueta[etiqueta_cuerpo]
    if idx_t == idx_c:
        raise ValueError("La columna de título y la de cuerpo deben ser diferentes.")
    wb = load_workbook(io.BytesIO(file_bytes))
    try:
        ws = wb.active
        # v4.35: además de 'Título' y 'Resumen - Aclaracion', se apartan otras
        # columnas conocidas de cuerpo ('CuerpoEs', 'Cuerpo', 'Texto', ...).
        # Así `_texto_fila` (que elige el texto más largo disponible) honra la
        # columna de cuerpo ELEGIDA por el usuario en vez de una preexistente
        # más larga: el análisis de la pestaña personalizada usa exactamente
        # las columnas elegidas, igual que la estándar usa las suyas.
        _APARTAR_CUERPO = {'CuerpoEs', 'Cuerpo', 'Texto', 'Texto completo',
                           'Resumen', 'resumen corto'}
        for celda in ws[1]:
            if celda.column in (idx_t, idx_c):
                continue
            v = str(celda.value).strip() if celda.value is not None else ""
            if v == "Título":
                celda.value = "Título (original)"
            elif v == "Resumen - Aclaracion":
                celda.value = "Resumen - Aclaracion (original)"
            elif v in _APARTAR_CUERPO:
                celda.value = v + " (original)"
        ws.cell(row=1, column=idx_t).value = "Título"
        ws.cell(row=1, column=idx_c).value = "Resumen - Aclaracion"
        bio = io.BytesIO()
        wb.save(bio)
        return bio.getvalue()
    finally:
        wb.close()


def construir_ai_config_custom(brand, alias_txt, voceros_txt, criterio,
                               incluir_tema, incluir_prominencia, enable_ai,
                               api_key, typesafe_api_key, historial_dir,
                               tone_pkl_bytes, theme_pkl_bytes,
                               model="gpt-6-luna",
                               criterio_texto="", taxonomia=None, guia_subtema="",
                               subtemas_fijos=None,
                               clasificar_tipo_genero=False,
                               tono_distrito_positivo=False,
                               agrupacion_estricta=False,
                               tono_alertas_tempranas=False,
                               tono_labor_positiva=False,
                               formato_boletin=False,
                               hora_boletin_corte_manana=13,
                               hora_boletin_corte_tarde=18,
                               hora_boletin_manana="1:00 p.m.",
                               hora_boletin_tarde="6:00 p.m.",
                               excluir_analizados=True,
                               cubos_objetivo=16, votos=2, tam_lote=10,
                               workers=8, umbral_titulo=92, umbral_cuerpo=85):
    """ai_config de la pestaña "Columnas personalizadas" (v4.27/v4.28).

    Misma forma que el de la pestaña estándar. Devuelve None si no hay
    análisis que hacer (sin IA, sin PKL y sin prominencia).

    v4.34: paridad total con la pestaña estándar — criterio personalizado,
    taxonomía (dict de JSON o nombre), cubos/votos/lote/workers/umbrales y
    modelo son parámetros en vez de valores fijos.
    """
    if not (enable_ai or incluir_prominencia or tone_pkl_bytes or theme_pkl_bytes):
        return None
    return {
        "enabled": bool(enable_ai),
        "brand": (brand or "").strip(),
        "aliases": [a.strip() for a in re.split(r"[,;]", alias_txt or "") if a.strip()],
        "voceros": [v.strip() for v in re.split(r"[,;]", voceros_txt or "") if v.strip()],
        "criterio": criterio,
        "criterio_texto": (criterio_texto or "").strip(),
        "guia_subtema": (guia_subtema or "").strip(),
        "subtemas_fijos": list(subtemas_fijos or []),
        "clasificar_tipo_genero": bool(clasificar_tipo_genero),
        "tono_distrito_positivo": bool(tono_distrito_positivo),
        "agrupacion_estricta": bool(agrupacion_estricta),
        "tono_alertas_tempranas": bool(tono_alertas_tempranas),
        "tono_labor_positiva": bool(tono_labor_positiva),
        "formato_boletin": bool(formato_boletin),
        "hora_boletin_corte_manana": hora_boletin_corte_manana if (isinstance(hora_boletin_corte_manana, int)
            and not isinstance(hora_boletin_corte_manana, bool) and 0 <= hora_boletin_corte_manana <= 23) else 13,
        "hora_boletin_corte_tarde": hora_boletin_corte_tarde if (isinstance(hora_boletin_corte_tarde, int)
            and not isinstance(hora_boletin_corte_tarde, bool) and 0 <= hora_boletin_corte_tarde <= 23) else 18,
        "hora_boletin_manana": (str(hora_boletin_manana or "1:00 p.m.").strip() or "1:00 p.m."),
        "hora_boletin_tarde": (str(hora_boletin_tarde or "6:00 p.m.").strip() or "6:00 p.m."),
        "excluir_analizados": bool(excluir_analizados),
        "taxonomia": taxonomia if taxonomia is not None
                     else "Automática según el archivo (recomendada)",
        "cubos_objetivo": int(cubos_objetivo),
        "votos": int(votos),
        "incluir_tema": bool(incluir_tema),
        "incluir_prominencia": bool(incluir_prominencia),
        "permitir_cubos_nuevos": True,
        "tam_lote": int(tam_lote),
        "workers": int(workers),
        "umbral_titulo": int(umbral_titulo),
        "umbral_cuerpo": int(umbral_cuerpo),
        "api_key": api_key if enable_ai else None,
        "typesafe_api_key": typesafe_api_key if enable_ai else None,
        "typesafe_model": "jev-latest",
        "model": model,
        "historial_dir": historial_dir,
        "tone_pkl_bytes": tone_pkl_bytes,
        "theme_pkl_bytes": theme_pkl_bytes,
        # v4.33: la pestaña "Columnas personalizadas" conserva el xlsx como
        # está (todas las columnas originales en su orden) y agrega al final
        # las columnas del análisis, en vez del esquema fijo de la estándar.
        "preservar_columnas": True,
    }


# v4.82: columnas del dossier que el proceso lee por nombre. Un encabezado
# escrito de otra forma («Empresa Rel.», «MENCIONES - EMPRESA», «Hora »,
# «DimensionCm2») se reconoce sin importar mayúsculas, tildes, espacios ni
# signos. Antes se ignoraba: sin «Menciones - Empresa» / «Empresa rel.» la
# fila no se duplicaba por marca y la columna salía vacía.
COLUMNAS_CONOCIDAS = (
    "ID Noticia", "Fecha", "Hora", "Medio", "Tipo de Medio", "Sección - Programa", "Región",
    "Título", "Titulo", "Autor - Conductor", "Nro. Pagina", "Dimensión", "Dimensioncm2",
    "Duración - Nro. Caracteres", "CPE", "Valor de Nota", "Tier", "Audiencia", "Link Nota",
    "Resumen - Aclaracion", "CuerpoEs", "Link (Streaming - Imagen)", "Menciones - Empresa",
    "Empresa rel.", "Link Ext", "Web Ext", "ID duplicada",
)


def canonizar_columnas(df):
    """Agrega con su nombre estándar las columnas conocidas que vienen
    escritas de otra forma (la original también se queda, para la pestaña de
    columnas personalizadas). No toca las que ya traen el nombre exacto."""
    canon = {norm_key(c): c for c in COLUMNAS_CONOCIDAS}
    for col in list(df.columns):
        c = canon.get(norm_key(col))
        if c and c != col and c not in df.columns:
            df[c] = df[col]
    return df


def normalize_dossier_dataframe(df, region_map, internet_map, progress: ProgressCb = None):
    if df is None or df.empty:
        return pd.DataFrame()
    df = canonizar_columnas(df)

    emit_progress(progress, 42, "Normalizando tipo de medio, región y columnas…")

    if "Tipo de Medio" in df.columns:
        df["Tipo de Medio"] = (
            df["Tipo de Medio"].astype(str).str.lower().str.strip()
            .map(TIPO_MEDIO_MAP)
            .fillna(df["Tipo de Medio"].astype(str).str.strip())
        )
    else:
        df["Tipo de Medio"] = "Otro"

    is_av = df["Tipo de Medio"].isin(list(TIPOS_AV))
    is_grafica = df["Tipo de Medio"].isin(["Prensa", "Internet", "Revistas"])
    is_internet = df["Tipo de Medio"] == "Internet"

    # BÚSQUEDA ROBUSTA DEL RESUMEN
    cuerpo_series = get_column_robust(df, "CuerpoEs")
    if cuerpo_series.dropna().empty:
        cuerpo_series = get_column_robust(df, "Resumen - Aclaracion")
    if cuerpo_series.dropna().empty:
        cuerpo_series = get_column_robust(df, "Resumen")
    if cuerpo_series.dropna().empty:
        cuerpo_series = get_column_robust(df, "Cuerpo")
    if cuerpo_series.dropna().empty:
        cuerpo_series = get_column_robust(df, "resumen corto")

    raw_resumen_orig = cuerpo_series

    if "Medio" in df.columns:
        raw_medios_clean = df["Medio"].astype(str).str.lower().str.strip()
        df["Región"] = raw_medios_clean.map(region_map).fillna("N/A")
    else:
        df["Medio"] = "N/A"
        df["Región"] = "N/A"

    if "Medio" in df.columns:
        df.loc[is_internet, "Medio"] = (
            df.loc[is_internet, "Medio"]
            .astype(str).str.lower().str.strip()
            .map(internet_map)
            .fillna(df.loc[is_internet, "Medio"])
        )

    df["ID Noticia"] = df.get("NoticiaId", df.get("ID Noticia", pd.Series(dtype=str)))
    df["Fecha"] = _leer_fechas(df.get("Fecha", pd.Series(dtype=str)))
    df["Hora"] = df.get("Hora", pd.Series(dtype=str))
    df["Sección - Programa"] = df.get("Sección - Programa", pd.Series(dtype=str)).astype(str).apply(clean_text)

    titulo_col = "Título" if "Título" in df.columns else "Titulo"
    # v4.46: el título además decodifica entidades HTML (clean_title_for_output).
    df["Título"] = df.get(titulo_col, pd.Series(dtype=str)).astype(str).apply(clean_title_for_output)
    df["Autor - Conductor"] = df.get("Autor - Conductor", pd.Series(dtype=str)).astype(str).apply(clean_text)
    df["Nro. Pagina"] = df.get("Nro. Pagina", pd.Series(dtype=str))

    dim_col = "Dimensioncm2" if "Dimensioncm2" in df.columns else "Dimensión"
    df["Dimensión"] = df.get(dim_col, pd.Series(dtype=str))
    df["Duración - Nro. Caracteres"] = df.get("Duración - Nro. Caracteres", pd.Series(dtype=str))

    df.loc[is_av, "Dimensión"] = df.loc[is_av, "Duración - Nro. Caracteres"]
    # v4.80 (orden del usuario): «Tamaño» = Dimensioncm2 del origen para
    # Internet, Prensa y Revistas; para Radio y Televisión, el valor de
    # «Duración - Nro. Caracteres» (que también se queda en su columna).
    df["Tamaño"] = df["Dimensión"].copy()
    # Sec v1.24 / Def v1.21 (orden del usuario): para aire, cable, AM, FM,
    # Televisión y Radio (todos caen en TIPOS_AV vía TIPO_MEDIO_MAP), el
    # valor de Duración - Nro. Caracteres TAMBIÉN queda en su propia
    # columna. Antes se ponía en 0 y solo vivía en Dimensión. Dimensión
    # sigue recibiendo el mismo valor, como antes; no se quita nada.

    cpe_input = get_column_robust(df, "CPE")
    valor_nota_input = get_column_robust(df, "Valor de Nota")

    df["CPE"] = np.where(is_av, cpe_input, np.where(is_grafica, valor_nota_input, np.nan))

    df["Tier"] = df.get("Tier", pd.Series(dtype=str))
    df["Audiencia"] = df.get("Audiencia", pd.Series(dtype=str))

    emit_progress(progress, 48, "Limpiando cuerpos y enlaces…")

    cuerpo_cleaned = raw_resumen_orig.astype(str).apply(clean_cuerpo)

    # v4.47: el Resumen - Aclaracion queda tal como viene del cuerpo (solo
    # con las entidades HTML decodificadas por clean_cuerpo), para TODOS
    # los tipos de medio. Antes, a Prensa/Internet/Revistas se les
    # rearmaban los párrafos y se les forzaba un punto final.
    df["Resumen - Aclaracion"] = cuerpo_cleaned

    url_nota_av = df.get("URL Nota AV", df.get("Link Nota AV", pd.Series([""] * len(df))))
    url_streaming = df.get("URL (Streaming - Imagen)", pd.Series([""] * len(df)))

    link_nota_final = []
    for val_av, val_str, is_av_row in zip(url_nota_av, url_streaming, is_av):
        if is_av_row:
            if isinstance(val_av, dict):
                url_t = val_av.get("url", "")
                link_nota_final.append({"value": "Link", "url": url_t.replace(".com.ar", ".com.co") if url_t else None})
            else:
                url_t = str(val_av or "")
                link_nota_final.append({"value": "Link", "url": url_t.replace(".com.ar", ".com.co") if url_t else None})
        else:
            if isinstance(val_str, dict):
                link_nota_final.append(val_str)
            else:
                link_nota_final.append({"value": "Link", "url": val_str if val_str else None})

    df["Link Nota"] = link_nota_final

    url_nota_raw = df.get("URL Nota", pd.Series([""] * len(df)))
    link_stream_final = []
    for val_url, is_int in zip(url_nota_raw, is_internet):
        if is_int:
            if isinstance(val_url, dict):
                link_stream_final.append(val_url)
            else:
                link_stream_final.append({"value": "Link", "url": val_url if val_url else None})
        else:
            link_stream_final.append(None)

    df["Link (Streaming - Imagen)"] = link_stream_final

    # v4.44: columna "Audio" — lleva la URL (Streaming - Imagen) solo para los
    # tipos de medio de audio/video (Radio, Televisión); el resto queda vacío.
    df["Audio"] = np.where(
        df["Tipo de Medio"].isin(TIPOS_MEDIO_AUDIO),
        url_streaming.map(_extract_url),
        "",
    )

    menciones_av = get_column_robust(df, "Menciones - Empresa").fillna("").astype(str).apply(clean_text)
    menciones_grafica = get_column_robust(df, "Empresa rel.").fillna("").astype(str).apply(clean_text)
    menciones_av = menciones_av.where(~menciones_av.str.lower().isin(["nan", "none"]), "")
    menciones_grafica = menciones_grafica.where(~menciones_grafica.str.lower().isin(["nan", "none"]), "")
    # Radio/TV: «Menciones - Empresa»; Internet, Prensa y Revistas: «Empresa
    # rel.». v4.82: si la que corresponde viene vacía, se usa la otra (y en
    # un tipo de medio no reconocido, la que traiga datos).
    _prim = np.where(is_grafica, menciones_grafica, menciones_av)
    _sec = np.where(is_grafica, menciones_av, menciones_grafica)
    df["Menciones - Empresa"] = np.where(pd.Series(_prim, index=df.index).str.strip() != "", _prim, _sec)

    emit_progress(progress, 52, "Columnas normalizadas.")
    return df


def expand_menciones(df) -> List[dict]:
    records = df.to_dict("records")
    rows_expanded = []
    _marca_nk = {norm_key("Menciones - Empresa"), norm_key("Empresa rel.")}
    _claves_marca = [c for c in df.columns if norm_key(c) in _marca_nk and c != "Menciones - Empresa"]
    for idx, rec in enumerate(records):
        menciones = [m.strip() for m in str(rec.get("Menciones - Empresa", "")).split(";") if m.strip()]
        # v4.82: la misma marca dos veces en la celda no duplica la fila.
        _vistas = set()
        menciones = [m for m in menciones
                     if not (norm_key(m) in _vistas or _vistas.add(norm_key(m)))]
        if not menciones:
            row_dict = dict(rec)
            row_dict["Menciones - Empresa"] = ""
            row_dict["original_index"] = idx
            row_dict["is_duplicate"] = False
            rows_expanded.append(row_dict)
            continue
        for m in menciones:
            row_dict = dict(rec)
            row_dict["Menciones - Empresa"] = m
            # v4.82: las columnas de marca originales («Menciones - Empresa»
            # / «Empresa rel.» con cualquier escritura), que se conservan en
            # la pestaña de columnas personalizadas, también quedan con la
            # marca de esta fila y no con la lista completa.
            for _k in _claves_marca:
                _v = rec.get(_k)
                if isinstance(_v, str) and ";" in _v and norm_key(m) in {
                        norm_key(x) for x in _v.split(";")}:
                    row_dict[_k] = m
            row_dict["original_index"] = idx
            row_dict["is_duplicate"] = False
            rows_expanded.append(row_dict)
    return rows_expanded


# ======================================
# Exportar a Excel (XlsxWriter, streaming)
# ======================================
def generate_output_excel(rows, km, progress: ProgressCb = None, columns_to_use: List[str] = None):
    cols = columns_to_use or BASE_OUTPUT_COLUMNS
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(
        buf,
        {
            "constant_memory": True,
            "strings_to_urls": False,
            "nan_inf_to_errors": False,
        },
    )
    ws = wb.add_worksheet("Resultado")
    fmt_header = wb.add_format({"bold": True})
    fmt_link = wb.add_format({"font_color": "#0563C1", "underline": 1, "align": "left"})
    fmt_plain_hlink = wb.add_format({"font_color": "#000000", "underline": False, "align": "left"})
    fmt_date = wb.add_format({"num_format": "dd/mm/yyyy", "align": "center"})
    fmt_currency = wb.add_format({"num_format": "$#,##0"})
    fmt_thousands = wb.add_format({"num_format": "#,##0"})
    # Formato entero plano sin puntos de miles ni decimales para IDs
    fmt_plain_id = wb.add_format({"num_format": "0"})

    for i, col_name in enumerate(cols):
        if col_name in ["Título", "Resumen - Aclaracion", "Contexto analizado", "Audio", "texto", "Link Ext", "Web Ext"]:
            ws.set_column(i, i, 55)
        elif col_name in ["Link Nota", "Link (Streaming - Imagen)"]:
            ws.set_column(i, i, 15)
        elif col_name in ["Subtema_IA", "Tema_IA"]:
            ws.set_column(i, i, 28)
        else:
            ws.set_column(i, i, 20)
        ws.write(0, i, col_name, fmt_header)

    n = len(rows)
    step = max(1, n // 50) if n else 1
    emit_progress(progress, 0, f"Generando archivo de resultado… 0/{n} filas")

    try:
        _write_xlsx_rows(
            ws, rows, km, n, step, progress,
            fmt_link, fmt_plain_hlink, fmt_date, fmt_currency, fmt_thousands, fmt_plain_id, cols,
        )
        emit_progress(progress, 100, "Guardando archivo Excel…")
    finally:
        wb.close()
    return buf.getvalue()


def _write_xlsx_rows(ws, rows, km, n, step, progress, fmt_link, fmt_plain_hlink, fmt_date, fmt_currency, fmt_thousands, fmt_plain_id, cols):
    for i, row in enumerate(rows):
        tk = km.get("titulo")
        if tk and tk in row:
            row[tk] = clean_title_for_output(row.get(tk))
        rk = km.get("resumen")
        if rk and rk in row:
            row[rk] = corregir_texto(row.get(rk))
        # v4.47: si el xlsx trae su propia columna CuerpoEs (p. ej. en la
        # pestaña de columnas personalizadas, que conserva las columnas de
        # entrada), recibe la misma limpieza del resumen: texto exacto,
        # solo entidades HTML decodificadas y cierre en un solo punto.
        if "CuerpoEs" in row and isinstance(row.get("CuerpoEs"), str) and row["CuerpoEs"].strip():
            row["CuerpoEs"] = corregir_texto(row["CuerpoEs"])

        # v1.19/v1.15: decodificar entidades HTML en Contexto analizado y en
        # Resumen - Aclaracion, además de la limpieza que ya reciben
        # Título, Resumen y CuerpoEs. Solo se decodifica: no se cambia
        # ninguna palabra, no se recorta nada y no se usa API. Sin esto,
        # un cuerpo con entidades ("podr&#xE1;") las arrastraba a
        # Contexto (que se construye del CuerpoEs crudo, no del resumen
        # limpio) y, desde ahí, a texto, que se deriva de Contexto.
        # Va antes de la derivación de texto para que texto también salga
        # limpio.
        if isinstance(row.get(CONTEXTO_ANALIZADO_COL), str):
            row[CONTEXTO_ANALIZADO_COL] = decode_html_entities(
                row[CONTEXTO_ANALIZADO_COL])
        if isinstance(row.get("Resumen - Aclaracion"), str):
            row["Resumen - Aclaracion"] = decode_html_entities(
                row["Resumen - Aclaracion"])

        excel_row = i + 1
        for cidx, h in enumerate(cols):
            # v1.17/v1.13: columna "ENLACE" como TEXTO PLANO (por pedido
            # del usuario, para ambos repos): el enlace se escribe tal
            # cual, sin hipervínculo. Aplica a cualquier capitalización
            # ("ENLACE", "Enlace", "enlace"). Se usa write_string a
            # propósito: write() convertiría la URL en hipervínculo
            # automáticamente. No toca Link Nota, Link Ext, Web Ext ni
            # Audio, que siguen como hipervínculos.
            # v1.18/v1.14: además con formato explícito negro sin
            # subrayado, para que ningún visor lo pinte como enlace.
            if isinstance(h, str) and h.strip().lower() == "enlace":
                _v = row.get(h)
                if isinstance(_v, dict):
                    _t = _v.get("url") or _v.get("value") or ""
                else:
                    _t = _v
                if _t is None or str(_t).strip() in ("", "nan", "None", "-"):
                    ws.write_blank(excel_row, cidx, None)
                else:
                    ws.write_string(excel_row, cidx, str(_t), fmt_plain_hlink)
                continue
            # v4.57: "Link Ext" y "Web Ext" con el enlace completo a la
            # noticia, mostrado completo y clicable (como "Audio").
            # Link Ext / Web Ext por encabezado NORMALIZADO (sin espacios
            # ni mayúsculas, igual que ENLACE): en personalizada los
            # encabezados del dossier se preservan tal cual, y una variante
            # ("Link Ext ", "LINK EXT") no coincidía con la comparación
            # exacta, caía en la rama genérica y salía azul con
            # hipervínculo aunque el código desplegado fuera correcto.
            _h_norm = h.strip().lower() if isinstance(h, str) else ""
            if _h_norm in ("link ext", "web ext"):
                url_ext = _url_link_ext(row) if _h_norm == "link ext" else _url_web_ext(row)
                if url_ext:
                    # Link Ext SIEMPRE como TEXTO PLANO (orden del usuario,
                    # Secretaría y Defensoría): color negro NORMAL, sin
                    # negrilla, sin subrayado, sin hipervínculo (la
                    # negrilla es solo para Link Nota / Streaming, no para
                    # Link Ext). Web Ext sigue clicable, como antes.
                    if _h_norm == "link ext":
                        ws.write_string(excel_row, cidx, url_ext, fmt_plain_hlink)
                    else:
                        ws.write_url(excel_row, cidx, url_ext, fmt_link, string=url_ext)
                else:
                    ws.write_blank(excel_row, cidx, None)
                continue
            # v4.56: columna "texto" como TEXTO PLANO (sin fórmula, por
            # pedido del usuario): Contexto analizado sin el Título, con
            # espacios sobrantes limpiados. Es el mismo cálculo que hacía la
            # fórmula =ESPACIOS(SUSTITUIR(Contexto; Título; "")) de v4.55.
            # Sin "Contexto analizado" (modo sin IA) queda vacía.
            if h == "texto":
                if "Título" in cols and CONTEXTO_ANALIZADO_COL in cols:
                    ctx_v = row.get(CONTEXTO_ANALIZADO_COL) or ""
                    tit_v = row.get("Título") or ""
                    base = str(ctx_v).replace(str(tit_v), "") if str(tit_v) else str(ctx_v)
                    # v1.27 (igual que Secretaría v1.30): más de 15 palabras,
                    # completando con oraciones LITERALES del Resumen -
                    # Aclaracion (sin inventar).
                    base = texto_con_minimo(" ".join(base.split()),
                                            row.get("Resumen - Aclaracion") or "",
                                            str(tit_v or ""))
                    ws.write(excel_row, cidx, base)
                else:
                    ws.write_blank(excel_row, cidx, None)
                continue
            val = row.get(h)
            cv = None
            url = None

            if h in ("Fecha", "FECHA INFORME") and val is not None and not isinstance(val, dict) and pd.notna(val):
                if isinstance(val, pd.Timestamp):
                    cv = val.to_pydatetime()
                elif isinstance(val, (datetime.datetime, datetime.date)):
                    cv = val
                else:
                    cv = str(val)
            # ID Noticia e ID duplicada se procesan como enteros puros sin separadores
            elif h in ("ID Noticia", "ID duplicada"):
                if val is not None and str(val).strip() not in ("", "nan", "None", "-"):
                    clean_id = re.sub(r"[^\d.]", "", str(val)).strip()
                    if clean_id:
                        try:
                            cv = int(float(clean_s if (clean_s := clean_id) else 0))
                        except ValueError:
                            cv = str(val)
                else:
                    cv = None
            elif h in COLS_DURACION and _es_duracion(val):
                # v4.80: una duración «00:02:30» (texto u hora de Excel) sale
                # tal cual; antes parse_numeric la volvía 230.
                cv = _texto_duracion(val)
            elif h in NUMERIC_COLS:
                cv = parse_numeric(val)
            elif isinstance(val, dict) and "url" in val:
                cv = val.get("value", "Link")
                if val.get("url"):
                    url = val["url"]
            elif h == "Audio":
                # v4.44: la URL se muestra completa (clicable), no colapsada a "Link".
                audio_url = _extract_url(val)
                if audio_url:
                    cv = audio_url
                    url = audio_url
                else:
                    cv = None
            elif val is not None:
                if isinstance(val, str) and val.startswith("http"):
                    cv = "Link"
                    url = val
                elif isinstance(val, (int, float)) and not isinstance(val, bool):
                    # v1.42: un número de entrada (p. ej. la columna «ID» de la
                    # pestaña personalizada, que pandas lee como 61142010.0)
                    # sale como número y sin «.0», no como el texto
                    # «61142010.0».
                    if isinstance(val, float) and math.isfinite(val) and val.is_integer():
                        cv = int(val)
                    else:
                        cv = val
                else:
                    cv = str(val)

            if url:
                display = str(cv or "Link")
                url_fmt = fmt_plain_hlink if _h_norm in PLAIN_HYPERLINK_COLUMNS_NORM else fmt_link
                try:
                    ws.write_url(excel_row, cidx, str(url), url_fmt, string=display)
                except Exception:
                    ws.write(excel_row, cidx, display, url_fmt)
            elif h in ("ID Noticia", "ID duplicada") and isinstance(cv, int):
                ws.write_number(excel_row, cidx, cv, fmt_plain_id)
            elif h in ("Fecha", "FECHA INFORME") and isinstance(cv, datetime.datetime):
                ws.write_datetime(excel_row, cidx, cv, fmt_date)
            elif h in ("Fecha", "FECHA INFORME") and isinstance(cv, datetime.date):
                ws.write_datetime(
                    excel_row,
                    cidx,
                    datetime.datetime(cv.year, cv.month, cv.day),
                    fmt_date,
                )
            elif h in CURRENCY_COLS and isinstance(cv, (int, float)) and math.isfinite(cv):
                ws.write_number(excel_row, cidx, cv, fmt_currency)
            elif h in THOUSANDS_COLS and isinstance(cv, (int, float)) and math.isfinite(cv):
                ws.write_number(excel_row, cidx, cv, fmt_thousands)
            elif cv is None or cv == "":
                ws.write_blank(excel_row, cidx, None)
            elif isinstance(cv, float) and not math.isfinite(cv):
                ws.write_blank(excel_row, cidx, None)
            else:
                ws.write(excel_row, cidx, cv)

        if progress and (i % step == 0 or i == n - 1):
            emit_progress(
                progress,
                int((i + 1) / n * 100) if n else 100,
                f"Generando archivo de resultado… {i + 1}/{n} filas",
            )


# ======================================
# Modelos PKL opcionales
# ======================================
def _contar_tonos(rows) -> dict:
    from collections import Counter
    c = Counter()
    for r in rows or []:
        if r.get("is_duplicate"):
            continue
        c[r.get("Tono_IA") or "?"] += 1
    return dict(c)


def _load_optional_pkl_models(ai_config: Optional[dict]):
    if not ai_config:
        return None, None
    tone_model = None
    theme_model = None
    tone_bytes = ai_config.get("tone_pkl_bytes")
    theme_bytes = ai_config.get("theme_pkl_bytes")
    if tone_bytes:
        tone_model, _ = load_sklearn_estimator(tone_bytes, "tono")
    if theme_bytes:
        theme_model, _ = load_sklearn_estimator(theme_bytes, "tema")
    return tone_model, theme_model


def _ai_extra_con_pkl(ai_config: Optional[dict], theme_model) -> dict:
    """Extra para enrich: si hay PKL de tema, fuerza incluir_tema=True.

    La clasificación del PKL es local (sin llamadas LLM ni demora), así que
    siempre se aplica aunque el checkbox "Generar columna Tema_IA" del
    cliente venga desmarcado: el modelo del cliente manda.
    """
    extra = dict(ai_config or {})
    if theme_model is not None:
        extra["incluir_tema"] = True
    return extra


# ======================================
# v4.74: enfoque por fila (marca de «Menciones - Empresa» / «Empresa rel.»)
# ======================================
# El dossier ya trae una fila por marca mencionada: «Menciones - Empresa» en
# radio/TV y web, «Empresa rel.» en prensa (ver normalize_dossier_dataframe).
# Si la marca de la fila es el cliente (o viene vacía), el tono se mide hacia
# la marca, alias y voceros escritos en la app, como siempre. Si es otra marca
# (p. ej. un competidor), el tono de ESA fila se mide hacia esa marca: el
# motor corre aparte para cada marca, con lo que cada nota recibe el tono que
# le corresponde a la marca de su fila.
COL_MARCA_ANALIZADA = "Marca analizada"


def _terminos_cliente(brand: str, aliases) -> List[str]:
    from analyzer_tono_tema import nz
    return sorted({nz(x) for x in [brand] + list(aliases or []) if len(nz(x)) >= 2},
                  key=len, reverse=True)


def _contiene_termino(texto_nz: str, termino: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9ñ])" + re.escape(termino) + r"(?![a-z0-9ñ])", texto_nz))


def separar_marcas(texto) -> List[str]:
    """«GSK; Sanofi», «GSK, Sanofi», «GSK / Sanofi» => ['GSK', 'Sanofi']."""
    out, vistos = [], set()
    for m in re.split(r"\s*(?:[;,|\n]|\s/\s)\s*", str(texto or "")):
        m = m.strip()
        if m and norm_key(m) not in vistos:
            vistos.add(norm_key(m))
            out.append(m)
    return out


def _texto_fila_nz(row: dict, km: dict) -> Tuple[str, str]:
    from analyzer_tono_tema import nz
    tit = nz(row.get(km.get("titulo", "Título")) or "")
    cue = nz(" ".join(str(row.get(k) or "") for k in (
        km.get("resumen", "Resumen - Aclaracion"), "CuerpoEs", "Resumen - Aclaracion")))
    return tit, cue


def repartir_terminos(marcas: List[str], terminos, rows: List[dict], km: dict) -> Dict[str, List[str]]:
    """Reparte alias (o voceros) entre las marcas escritas.

    - Con una sola marca, todos son de ella.
    - «Marca: término» los asigna a esa marca; los términos que siguen sin
      prefijo van a la misma marca, hasta el próximo prefijo.
    - Sin prefijo: a la marca con la que más aparece en las notas del dossier
      (la marca de «Menciones - Empresa» o su nombre en el texto) y, si no hay
      evidencia, a la marca con la que comparte una palabra. Si nada decide,
      a la primera marca."""
    from analyzer_tono_tema import nz
    marcas = list(marcas or [])
    out: Dict[str, List[str]] = {m: [] for m in marcas}
    if not marcas:
        return out
    terminos = [str(t).strip() for t in (terminos or []) if str(t).strip()]
    if len(marcas) == 1:
        # Una sola marca: todo es de ella (sin el prefijo «Marca:» si lo trae).
        for t in terminos:
            cab, _sep, resto = t.partition(":")
            out[marcas[0]].append(resto.strip() if _sep and nz(cab) == nz(marcas[0]) and resto.strip()
                                  else t)
        return out
    por_nz = {nz(m): m for m in marcas}
    actual = None
    sin_asignar: List[str] = []
    for t in terminos:
        if ":" in t:
            cab, resto = t.split(":", 1)
            if nz(cab) in por_nz:
                actual = por_nz[nz(cab)]
                if resto.strip():
                    out[actual].append(resto.strip())
                continue
        if actual:
            out[actual].append(t)
        else:
            sin_asignar.append(t)
    if not sin_asignar:
        return out
    # Evidencia del dossier: ¿con qué marca aparece cada término?
    mk = km.get("menciones", "Menciones - Empresa")
    marca_fila = []
    for r in rows:
        men = nz(r.get(mk) or "")
        tit, cue = _texto_fila_nz(r, km)
        dueñas = {m for m in marcas if (men and _contiene_termino(men, nz(m)))}
        if not dueñas:
            dueñas = {m for m in marcas if _contiene_termino(tit + " " + cue, nz(m))}
        if len(dueñas) == 1:
            marca_fila.append((next(iter(dueñas)), tit + " " + cue))
    for t in sin_asignar:
        tn = nz(t)
        votos = {m: 0 for m in marcas}
        for m, txt in marca_fila:
            if tn and _contiene_termino(txt, tn):
                votos[m] += 1
        mejor = max(marcas, key=lambda m: (votos[m], -marcas.index(m)))
        if votos[mejor] == 0 or list(votos.values()).count(votos[mejor]) > 1:
            palabras = {w for w in tn.split() if len(w) >= 3}
            comp = [m for m in marcas if palabras & {w for w in nz(m).split() if len(w) >= 3}]
            mejor = comp[0] if len(comp) == 1 else (mejor if votos[mejor] else marcas[0])
        out[mejor].append(t)
    return out


def marca_de_fila(row: dict, marcas: Dict[str, List[str]], km: dict) -> Tuple[str, bool]:
    """(marca, es_escrita): marca escrita a la que pertenece la fila, o la
    marca de su «Menciones - Empresa» si no es ninguna de las escritas."""
    from analyzer_tono_tema import nz
    nombres = list(marcas)
    crudo = str(row.get(km.get("menciones", "Menciones - Empresa")) or "").strip()
    men = nz(crudo)
    if men in ("nan", "none", "n a", "na"):
        men, crudo = "", ""
    if men:
        mejor = None
        for m in nombres:
            for t in _terminos_cliente(m, marcas[m]):
                if _contiene_termino(men, t) or _contiene_termino(t, men):
                    if mejor is None or len(t) > mejor[1]:
                        mejor = (m, len(t))
        if mejor:
            return mejor[0], True
        return crudo, False
    tit, cue = _texto_fila_nz(row, km)
    for txt in (tit, cue):
        pos = []
        for m in nombres:
            for t in _terminos_cliente(m, marcas[m]):
                mm = re.search(r"(?<![a-z0-9ñ])" + re.escape(t) + r"(?![a-z0-9ñ])", txt)
                if mm:
                    pos.append((mm.start(), nombres.index(m), m))
        if pos:
            return min(pos)[2], True
    return nombres[0], True


def enfoque_de_fila(row: dict, brand: str, aliases, km: Optional[dict] = None) -> Optional[str]:
    """Marca de la fila si es distinta de las marcas escritas; None si es una
    de ellas o viene vacía."""
    km = km or KEY_MAP
    marcas = separar_marcas(brand) or [brand]
    rep = repartir_terminos(marcas, aliases, [row], km)
    m, escrita = marca_de_fila(row, rep, km)
    return None if escrita else m


def enrich_con_enfoque_por_fila(rows: List[dict], km: dict, ai_config: dict, ai_extra: dict,
                                tone_model=None, theme_model=None, progress: ProgressCb = None):
    """Devuelve (rows, analisis). Con una sola marca escrita y sin filas de
    otras marcas es exactamente el camino de siempre (una sola corrida)."""
    from analyzer_tono_tema import nz
    marcas_txt = separar_marcas(ai_config.get("brand")) or [str(ai_config.get("brand") or "")]
    aliases = list(ai_config.get("aliases", []) or [])
    voceros = list(ai_extra.get("voceros") or [])
    rep_al = repartir_terminos(marcas_txt, aliases, rows, km)
    rep_vo = repartir_terminos(marcas_txt, voceros, rows, km)
    terms_fila = {m: rep_al[m] + rep_vo[m] for m in marcas_txt}
    grupos: Dict[str, List[dict]] = {}
    escrita: Dict[str, bool] = {}
    nombre: Dict[str, str] = {}
    for r in rows:
        m, es = marca_de_fila(r, terms_fila, km)
        k = nz(m)
        nombre.setdefault(k, m)
        escrita[k] = escrita.get(k, False) or es
        grupos.setdefault(k, []).append(r)

    def _correr(sub, marca, als, vocs, prog):
        return enrich_rows_with_ai(
            rows=sub, km=km, brand=marca, aliases=als,
            api_key=ai_config.get("api_key") or "",
            model=ai_config.get("model", "gpt-6-luna"),
            progress_callback=prog, tone_model=tone_model, theme_model=theme_model,
            extra=dict(ai_extra, brand=marca, aliases=als, voceros=vocs))

    if len(marcas_txt) == 1:
        principal = marcas_txt[0]
        ajenas = [k for k in grupos if not escrita[k]]
        if not ajenas:
            out = _correr(rows, principal, rep_al[principal], rep_vo[principal], progress)
            return out, ultimo_resumen()
        if len(grupos) == 1:
            # Todo el dossier es de una sola marca escrita de otra forma
            # («UNAL» / «Universidad Nacional de Colombia»): es el cliente.
            unica = nombre[ajenas[0]]
            out = _correr(rows, principal, rep_al[principal] + [unica], rep_vo[principal], progress)
            return out, ultimo_resumen()

    orden = [nz(m) for m in marcas_txt if nz(m) in grupos] + sorted(
        (k for k in grupos if not escrita[k]), key=lambda k: -len(grupos[k]))
    salida: List[dict] = []
    analisis: dict = {}
    marcas_inf: Dict[str, dict] = {}
    para_revisar: List[dict] = []
    costo = 0.0
    for n, k in enumerate(orden, 1):
        sub = grupos[k]
        marca = nombre[k]
        if escrita[k]:
            marca = next(m for m in marcas_txt if nz(m) == k)
            als, vocs = rep_al[marca], rep_vo[marca]
        else:
            als, vocs = [], []
        lo, hi = 70 + int(23 * (n - 1) / len(orden)), 70 + int(23 * n / len(orden))

        def _prog(pct, msg, _m=marca, _n=n, _lo=lo, _hi=hi, _f=len(sub)):
            loc = max(0.0, min(1.0, (float(pct) - 70) / 23.0))
            emit_progress(progress, int(_lo + (_hi - _lo) * loc),
                          "Marca %d de %d · tono hacia «%s» (%d filas) · %s"
                          % (_n, len(orden), _m, _f, msg))
        try:
            sub = _correr(sub, marca, als, vocs, _prog)
            res = ultimo_resumen()
            if not analisis:
                analisis = dict(res)
            costo += float(res.get("costo_aprox_usd") or 0)
            para_revisar.extend(dict(x, marca=marca) for x in (res.get("para_revisar") or []))
            marcas_inf[marca] = {"filas": len(sub), "ok": True, "escrita": escrita[k],
                                 "alias": als, "voceros": vocs}
        except Exception as exc:  # una marca que falla no tumba las demás
            logging.getLogger(__name__).exception("Falló el análisis hacia %s", marca)
            marcas_inf[marca] = {"filas": len(sub), "ok": False, "escrita": escrita[k],
                                 "error": str(exc)[:300]}
            for r in sub:
                r.setdefault("Contexto analizado", "-")
                r.setdefault("Tono_IA", "-")
                r.setdefault("Subtema_IA", "-")
                para_revisar.append({
                    "motivo": "No se pudo analizar el tono hacia «%s»" % marca,
                    "id": str(r.get(km.get("idnoticia", "ID Noticia")) or ""),
                    "titulo": str(r.get(km.get("titulo", "Título")) or ""),
                    "tono": "-", "subtema": "-", "detalle": str(exc)[:200], "marca": marca})
        for r in sub:
            r[COL_MARCA_ANALIZADA] = marca
            r["_alias_marca_analizada"] = list(als)
        salida.extend(sub)
    analisis["marcas_analizadas"] = marcas_inf
    analisis["costo_aprox_usd"] = round(costo, 4)
    analisis["para_revisar"] = para_revisar
    return salida, analisis


# ======================================
# Proceso Principal
# ======================================
MAX_VISTA = 5000


# v4.74: columnas del análisis que se entregan: tono y subtema (por
# defecto), solo tono o solo subtema.
COLUMNAS_IA = ("ambas", "tono", "subtema")


def columnas_ia_de(ai_config) -> str:
    v = str((ai_config or {}).get("columnas_ia") or "ambas").strip().lower()
    return v if v in COLUMNAS_IA else "ambas"


def quitar_columnas_ia(cols: List[str], columnas_ia: str) -> List[str]:
    """«tono»: sin Subtema_IA; «subtema»: sin Tono_IA; «ambas»: igual."""
    fuera = {"tono": {"Subtema_IA"}, "subtema": {"Tono_IA"}}.get(columnas_ia, set())
    return [c for c in cols if c not in fuera]


def vista_resultado(rows, km, columnas_ia: str = "ambas") -> List[dict]:
    """Filas únicas ya clasificadas, solo con lo que muestra la pantalla de
    resultados (la interfaz no vuelve a leer el xlsx)."""
    cols = (("ID", km.get("idnoticia", "ID Noticia")), ("Título", km.get("titulo", "Título")),
            ("Medio", km.get("medio", "Medio")), ("Tono", "Tono_IA"), ("Tema", "Tema_IA"),
            ("Subtema", "Subtema_IA"))
    if any(r.get(COL_MARCA_ANALIZADA) for r in rows[:50]):
        cols = cols[:3] + (("Marca", COL_MARCA_ANALIZADA),) + cols[3:]
    cols = tuple((n, c) for n, c in cols if c in quitar_columnas_ia([c], columnas_ia))
    out = []
    for r in rows:
        if r.get("is_duplicate"):
            continue
        fila = {}
        for nombre, col in cols:
            v = r.get(col)
            fila[nombre] = "" if v is None or str(v).strip() in ("", "-", "nan") else str(v)
        out.append(fila)
        if len(out) >= MAX_VISTA:
            break
    return out


def process_dossier(
    file_obj,
    region_map,
    internet_map,
    progress: ProgressCb = None,
    ai_config: Optional[dict] = None,
    t_inicio: Optional[float] = None,
) -> dict:
    # v4.56: si la app pasa t_inicio (el instante en que empezó el proceso,
    # antes de cargar la configuración desde Google Sheets), el tiempo
    # reportado cubre TODO el proceso y no solo lo que corre dentro de esta
    # función. Sin él, se mide desde aquí como antes.
    t0 = t_inicio if t_inicio is not None else time.time()
    # Medición por etapa (la interfaz la muestra al terminar).
    tiempos: List[dict] = []
    _reloj = [t0]

    def _etapa(nombre: str, sub: Optional[dict] = None, siempre: bool = False) -> None:
        # v1.33: las etapas principales se registran siempre (aunque duren
        # menos de 0,01 s con un dossier pequeño); las menores, solo si
        # tardan algo, para no llenar la tabla de ceros.
        ahora = time.time()
        seg = round(ahora - _reloj[0], 2)
        _reloj[0] = ahora
        if seg >= 0.01 or sub or siempre:
            tiempos.append({"etapa": nombre, "segundos": seg})
            for k, v in (sub or {}).items():
                tiempos.append({"etapa": k, "segundos": round(float(v), 2), "sub": True})

    if t_inicio is not None:
        _etapa("Carga de configuración")
    emit_progress(progress, 2, "Cargando archivo…")
    file_bytes = file_to_bytes(file_obj)

    df_normalized = load_dossier_dataframe(file_bytes, progress=progress)
    del file_bytes

    # v1.8: filtro de notas ya analizadas (Google Sheets), ANTES del
    # límite: el límite aplica a las notas NUEVAS, no al total del
    # archivo (las omitidas no consumen llamadas a la API).
    omitidas_analizadas = 0
    omitidas_aplicado = False
    omitidas_tiene_col_id = False
    if ai_config and ai_config.get("excluir_analizados") \
            and ai_config.get("ids_analizados") is not None:
        df_normalized, omitidas_analizadas, omitidas_aplicado, \
            omitidas_tiene_col_id = filtrar_ids_analizados(
                df_normalized, set(ai_config.get("ids_analizados") or set()))
        if omitidas_aplicado:
            emit_progress(
                progress, 2,
                f"Notas ya analizadas omitidas (Google Sheets): "
                f"{_fmt_miles(omitidas_analizadas)}; se procesan las nuevas.")
        elif not omitidas_tiene_col_id:
            emit_progress(
                progress, 2,
                "Sin columna de ID en el dossier: no se pudo aplicar el "
                "filtro de notas ya analizadas; se procesa todo el archivo.")

    # v4.45: límite de seguridad, antes de cualquier procesamiento o
    # análisis. Si el XLSX trae más filas de las permitidas, se detiene con
    # un mensaje claro en vez de disparar llamadas masivas a la API.
    n_filas = len(df_normalized)
    if n_filas > MAX_FILAS_POR_DOSSIER:
        raise LimiteFilasError(
            f"El archivo tiene {_fmt_miles(n_filas)} filas y el máximo por "
            f"corrida es {_fmt_miles(MAX_FILAS_POR_DOSSIER)}. Divide el XLSX "
            f"en partes de hasta {_fmt_miles(MAX_FILAS_POR_DOSSIER)} filas y "
            "procesa cada parte por separado. Este límite evita llamadas "
            "masivas a la API."
        )
    # v4.33: orden de columnas tal como venían en el archivo (antes de
    # normalizar, que puede agregar columnas como "Tipo de Medio"). Solo se
    # usa en la pestaña "Columnas personalizadas" (preservar_columnas=True).
    columnas_entrada = [c for c in list(df_normalized.columns) if c]
    df_normalized = normalize_dossier_dataframe(df_normalized, region_map, internet_map, progress=progress)

    medios_sin_region = []
    if not df_normalized.empty and "Región" in df_normalized.columns and "Medio" in df_normalized.columns:
        medios_sin_region = sorted(set(
            df_normalized.loc[df_normalized["Región"] == "N/A", "Medio"]
            .astype(str).str.strip()
        ) - {"", "nan", "None"})

    emit_progress(progress, 55, "Expandiendo menciones…")
    rows_expanded = expand_menciones(df_normalized)
    del df_normalized
    gc.collect()

    emit_progress(progress, 62, "Detectando duplicados…")
    rows = detectar_duplicados_avanzado(rows_expanded, KEY_MAP)

    _etapa("Lectura, limpieza y duplicados", siempre=True)
    has_ai = bool(ai_config and ai_config.get("enabled"))
    # v4.77: los PKL del cliente también se usan con Claude Haiku (el PKL
    # pone Tono y/o Tema; Haiku, el Subtema).
    tone_model, theme_model = _load_optional_pkl_models(ai_config)
    has_pkl = tone_model is not None or theme_model is not None
    analisis = {}
    # v4.32: el forzado de incluir_tema por PKL de tema (_ai_extra_con_pkl)
    # aplica en AMBOS caminos (con IA y solo-PKL). Antes, en modo solo-PKL
    # se leía el ai_config crudo y la columna Tema_IA desaparecía aunque el
    # PKL de tema hubiera clasificado todo (bug reportado 2026-09-28).
    ai_extra = _ai_extra_con_pkl(ai_config, theme_model)

    if has_ai:
        # v4.13: el PKL de tema del cliente manda. La clasificación es local
        # (sin llamadas LLM ni demora), así que siempre se aplica aunque el
        # checkbox "Generar columna Tema_IA" venga desmarcado.
        emit_progress(progress, 70, "Iniciando análisis de Tono, Tema y Sub-tema…")
        # v4.74: el tono de cada fila se mide hacia la marca de su
        # «Menciones - Empresa» / «Empresa rel.» (el cliente, si es él o
        # viene vacía).
        rows, analisis = enrich_con_enfoque_por_fila(
            rows, KEY_MAP, ai_config, ai_extra, tone_model=tone_model,
            theme_model=theme_model, progress=progress)
    elif has_pkl:
        emit_progress(progress, 70, "Preparando textos para clasificadores PKL…")
        rows = fill_classification_context(
            rows,
            KEY_MAP,
            brand=(ai_config or {}).get("brand", ""),
            aliases=(ai_config or {}).get("aliases", []),
        )
        emit_progress(progress, 88, "Aplicando modelos PKL del cliente (tono/tema)…")
        rows = apply_pkl_classifiers(
            rows,
            KEY_MAP,
            tone_model=tone_model,
            theme_model=theme_model,
            progress_callback=progress,
            brand=(ai_config or {}).get("brand", ""),
            aliases=(ai_config or {}).get("aliases", []),
        )

    # v4.23: prominencia de marca — métrica determinista por conteo de
    # menciones en Título y CuerpoEs (sin LLM). Se calcula siempre que haya
    # marca configurada, con o sin análisis IA.
    # v4.24: el usuario puede desactivarla con el checkbox "Agregar columna
    # Prominencia" en la app (incluir_prominencia; desde v4.26 default False,
    # checkbox desmarcado).
    if has_ai or has_pkl:
        _etapa("Análisis de tono, tema y subtema" if has_ai else "Modelos PKL del cliente",
               sub=(analisis or {}).get("tiempos"))
    _brand_prom = ((ai_config or {}).get("brand") or "").strip()
    _incluir_prominencia = bool(_brand_prom) and bool(
        (ai_config or {}).get("incluir_prominencia", False))
    if _incluir_prominencia:
        emit_progress(progress, 89, "Calculando prominencia de marca…")
        if any(r.get(COL_MARCA_ANALIZADA) for r in rows):
            # v4.74: varias marcas: la prominencia de cada fila es la de su marca.
            _por_marca: Dict[str, List[dict]] = {}
            for _r in rows:
                _por_marca.setdefault(str(_r.get(COL_MARCA_ANALIZADA) or _brand_prom), []).append(_r)
            for _m, _sub in _por_marca.items():
                aplicar_prominencia(_sub, KEY_MAP, brand=_m,
                                    aliases=_sub[0].get("_alias_marca_analizada") or [])
        else:
            rows = aplicar_prominencia(
                rows, KEY_MAP,
                brand=_brand_prom,
                aliases=(ai_config or {}).get("aliases", []),
            )

    # Orden editorial estable: primero por Título A–Z para revisar y agrupar
    # noticias iguales/similares en Excel. La clave ignora mayúsculas y tildes,
    # pero conserva el texto original en la salida.
    rows.sort(key=lambda r: (norm_key(r.get(KEY_MAP.get("titulo", "Título"), "")),
                             str(r.get(KEY_MAP.get("idnoticia", "ID Noticia"), ""))))

    # v1.16/v1.12: formato boletín (solo si el perfil lo trae). FECHA
    # INFORME es la fecha de procesamiento (Bogotá, mismo instante que
    # HORA B), NO la Fecha de la nota: todas las filas llevan la misma
    # fecha, la del día en que se corrió. HORA B es la del boletín según
    # el horario del perfil y la hora de Bogotá en que se corre; ambas
    # se fijan aquí, cuando las filas ya están finales y ordenadas,
    # justo antes de exportar.
    # Fecha de procesamiento (Bogotá) única para toda la corrida: la usa
    # el boletín y la corrección incondicional de FECHA INFORME abajo.
    _ahora_bogota = datetime.datetime.now(_zona_bogota())
    _fecha_informe = _ahora_bogota.replace(tzinfo=None, hour=0, minute=0,
                                           second=0, microsecond=0)
    if ai_extra.get("formato_boletin"):
        for _r in rows:
            _r["FECHA INFORME"] = _fecha_informe

    cols_to_export = output_columns_for_export(
        include_ai=has_ai or has_pkl,
        include_tema=ai_extra.get("incluir_tema", True),
        include_prominencia=_incluir_prominencia,
        # v4.32: sin IA no hay subtema (ningún PKL lo genera); la columna
        # saldría vacía ("-"), así que no se exporta en modo solo-PKL.
        include_subtema=has_ai,
        # v1.6: TIPO/GÉNERO solo con IA (sin IA no hay quien los clasifique).
        include_tipo_genero=has_ai and bool(ai_extra.get("clasificar_tipo_genero")),
        # v4.52: Audiencia antes de Tier para todos, salvo el perfil Fenavi.
        audiencia_antes_de_tier=not _es_perfil_fenavi(ai_config),
    )
    # v4.33: pestaña "Columnas personalizadas" — conservar el xlsx como está
    # (todas las columnas originales, incluidos ID inicial e hipervínculos de
    # la palabra "Link") y agregar al final las columnas del análisis.
    if (ai_config or {}).get("preservar_columnas"):
        cols_to_export = _columnas_preservadas(
            columnas_entrada,
            include_ai=has_ai or has_pkl,
            include_tema=ai_extra.get("incluir_tema", True),
            include_subtema=has_ai,
            include_prominencia=_incluir_prominencia,
            include_tipo_genero=has_ai and bool(ai_extra.get("clasificar_tipo_genero")),
            audiencia_antes_de_tier=not _es_perfil_fenavi(ai_config),
        )
    # v1.9: en formato boletín, la salida estándar es exactamente las 25
    # columnas; en la personalizada van las 25 primero y después las
    # columnas personalizadas extra que no estén en la lista.
    if ai_extra.get("formato_boletin"):
        _cols_boletin = columnas_boletin(
            bool(ai_extra.get("clasificar_tipo_genero")))
        if (ai_config or {}).get("preservar_columnas"):
            _extras = [c for c in cols_to_export if c not in _cols_boletin]
            cols_to_export = list(_cols_boletin) + _extras
        else:
            cols_to_export = list(_cols_boletin)
        # v4.77 (Grill): el orden del boletín no trae Tema_IA. Si se pidió el
        # tema (o hay PKL de tema, que lo fuerza), va entre Tono_IA y
        # Subtema_IA; sin IA no hay Subtema_IA (ningún PKL lo genera) y sin IA
        # ni PKL de tono no hay Tono_IA.
        if (has_ai or theme_model is not None) and ai_extra.get("incluir_tema") \
                and "Tema_IA" not in cols_to_export:
            _p = (cols_to_export.index("Subtema_IA") if "Subtema_IA" in cols_to_export
                  else cols_to_export.index("Tono_IA") + 1 if "Tono_IA" in cols_to_export
                  else len(cols_to_export))
            cols_to_export = cols_to_export[:_p] + ["Tema_IA"] + cols_to_export[_p:]
        if not has_ai:
            cols_to_export = [c for c in cols_to_export if c != "Subtema_IA"]
            if tone_model is None:
                cols_to_export = [c for c in cols_to_export if c != "Tono_IA"]

    # v4.74: solo tono, solo subtema o ambas columnas del análisis.
    cols_to_export = quitar_columnas_ia(cols_to_export, columnas_ia_de(ai_config))
    # v4.75: si hubo filas de otras marcas, la marca hacia la que se midió
    # cada fila va justo antes de «FECHA INFORME» (pedido del usuario); el
    # resto del orden no cambia. Sin FECHA INFORME, antes del tono/subtema.
    if has_ai and any(r.get(COL_MARCA_ANALIZADA) for r in rows) \
            and COL_MARCA_ANALIZADA not in cols_to_export:
        _ancla = next((c for c in ("FECHA INFORME", "Tono_IA", "Subtema_IA")
                       if c in cols_to_export), None)
        _pos = cols_to_export.index(_ancla) if _ancla else len(cols_to_export)
        cols_to_export = cols_to_export[:_pos] + [COL_MARCA_ANALIZADA] + cols_to_export[_pos:]

    emit_progress(progress, 94, "✓ Estructuración finalizada. Generando archivo Excel…")

    unique_rows = sum(1 for r in rows if not r.get("is_duplicate"))
    total_rows = len(rows)

    def export_progress(pct, msg):
        overall = 94 + int(pct * 0.06)
        emit_progress(progress, overall, msg)

    # v1.13/v1.9 (2026-10-31): género por URL web (ver
    # aplicar_genero_por_url). Corre antes de exportar para que el xlsx
    # ya lleve la Opinión; no afecta a filas sin género clasificado.
    aplicar_genero_por_url(rows)

    # v1.19 (orden del usuario): si la noticia dice "En entrevista con",
    # pasa a Entrevista. Corre después del rescate por URL y con la misma
    # guarda (solo sube Noticia); lo demás no cambia.
    aplicar_genero_por_frase_entrevista(rows)

    # v1.18/v1.14: FECHA INFORME siempre es la fecha de procesamiento
    # (Bogotá), en cualquier camino que la exporte — no solo en formato
    # boletín. Sin esto, una corrida personalizada sobre un xlsx ya
    # procesado arrastraba la FECHA INFORME vieja (en v1.13 se vio con
    # valores de la columna FECHA, de antes de v1.12).
    if "FECHA INFORME" in cols_to_export:
        for _r in rows:
            _r["FECHA INFORME"] = _fecha_informe

    _etapa("Prominencia y orden")
    output_data = generate_output_excel(rows, KEY_MAP, progress=export_progress, columns_to_use=cols_to_export)
    _etapa("Generación del Excel", siempre=True)
    _conteo_tonos = _contar_tonos(rows)
    _vista = vista_resultado(rows, KEY_MAP, columnas_ia_de(ai_config)) if (has_ai or has_pkl) else []
    del rows, rows_expanded
    gc.collect()
    duration = time.time() - t0
    emit_progress(progress, 100, "Limpieza y análisis completados")

    # Nombre del archivo con la primera marca buscada para orden
    if ai_config and ai_config.get("brand"):
        brand_raw = ai_config.get("brand", "")
        clean_tag = re.sub(r"[^\w\s-]", "", unidecode(brand_raw)).strip()
        brand_tag = re.sub(r"[-\s]+", "_", clean_tag)
        filename_prefix = f"Dossier_{brand_tag}" if brand_tag else "Dossier_Limpio"
    else:
        filename_prefix = "Dossier_Limpio"

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    final_filename = f"{filename_prefix}_{timestamp}.xlsx"

    result = {
        "output_data": output_data,
        "output_filename": final_filename,
        "total_rows": total_rows,
        "unique_rows": unique_rows,
        "duplicates": total_rows - unique_rows,
        "omitidas_analizadas": omitidas_analizadas,
        "omitidas_analizadas_aplicado": omitidas_aplicado,
        "process_duration": f"{duration:.2f}s",
        "medios_sin_mapear": medios_sin_region,
        "analisis": analisis,
        "_filas": _conteo_tonos,
        # Tiempo por etapa y filas clasificadas para la pantalla de resultados.
        "tiempos": tiempos,
        "vista": _vista,
    }

    # Auditoria de uso por correo (SMTP). Nunca interrumpe la corrida.
    if ai_config:
        try:
            from auditoria_mail import enviar_auditoria_desde_resultado
            enviar_auditoria_desde_resultado(result, ai_config)
        except Exception:
            logger.exception("Fallo al enviar la auditoria por correo (no interrumpe).")

    # v4.56: el tiempo reportado se recalcula AQUÍ (después del correo) para
    # que cubra todo el proceso real: configuración + lectura + análisis +
    # exportación + auditoría. Antes se calculaba justo después de exportar y
    # por eso salía menor que el tiempo que el usuario veía en el panel.
    duration = time.time() - t0
    result["process_duration"] = f"{duration:.2f}s"

    # Guardado del historial por cliente (best-effort, nunca interrumpe).
    if ai_config and ai_config.get("brand"):
        try:
            from historial_cliente import guardar_resultado
            guardar_resultado(ai_config["brand"], result["output_data"], result,
                              extra=ai_config)
        except Exception:
            logger.exception("Fallo al guardar el historial del cliente (no interrumpe).")

    return result
