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


# ============================================================================
# v1.30: referencia de subtemas desde la hoja de notas ya analizadas
# (Google Sheets de Defensoría). De cada nota con TEMA se guarda el TÍTULO,
# el RESUMEN (texto de la noticia) y el TEMA (= Subtema_IA). En la corrida,
# una noticia del dossier que es la MISMA noticia (mismo titular o casi, o
# el mismo comienzo del texto) retoma ese TEMA; las demás se clasifican con
# las reglas de siempre. Portado de Secretaría General v1.42, solo subtema.
# ============================================================================
DIAS_REFERENCIA = 10     # notas de la hoja de los últimos días
UMBRAL_TITULO = 0.80     # Jaccard de palabras de contenido del titular
UMBRAL_TEXTO = 0.70      # Jaccard de 3-gramas del comienzo del texto
MIN_PAL_TITULO = 4       # palabras de contenido para comparar titulares
MIN_GRAMAS_TEXTO = 15    # 3-gramas mínimos para comparar textos

_VACIAS = set("""a al ante con contra de del desde el en entre es fue ha han hay la las le les lo
los mas no o para pero por que se sin sobre su sus un una uno unos unas y ya este esta estos
estas ese esa como tras segun hoy""".split())
_NO_VALE = frozenset({"no aplica", "noaplica", "n a", "na", "duplicada", "duplicado",
                      "duplicadas", "x", "ninguno", "sin subtema", "sin tema"})
_COLS_TITULO = ("titulo",)
_COLS_TEMA = ("tema", "subtema ia", "subtema")
_COLS_RESUMEN = ("resumen", "resumen aclaracion", "texto", "cuerpoes", "cuerpo")
_COLS_TONO = ("tono", "tono ia", "impacto")
_COLS_FECHA = ("fecha informe", "fecha")
_COLS_HORA = ("hora b", "hora boletin", "boletin")


def _contenido(titulo: str) -> set:
    return {w for w in _n(titulo).split() if len(w) >= 3 and w not in _VACIAS}


def _propios(titulo: str) -> set:
    """Palabras con mayúscula inicial que no abren el titular (lugares,
    personas): dos notas del mismo molde con otro lugar o persona no son la
    misma noticia."""
    toks = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñü]+", str(titulo or ""))
    return {_n(w) for w in toks[1:] if w[:1].isupper() and len(w) >= 3} - _VACIAS


def _cifras(titulo: str) -> set:
    return set(re.findall(r"\d+", _n(titulo)))


def _en_mayusculas(titulo: str) -> bool:
    letras = [ch for ch in str(titulo or "") if ch.isalpha()]
    return len(letras) >= 8 and sum(ch.isupper() for ch in letras) >= 0.8 * len(letras)


def _gramas(texto: str, k: int = 3) -> set:
    w = _n(texto).split()[:80]
    return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))}


def _jac(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _celda(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    return "" if s.lower() in ("nan", "none", "-") else s


def _fecha_boletin(fecha: str, hora: str = "") -> str:
    """ISO de la nota (FECHA INFORME o FECHA + HORA B); '' si no se entiende."""
    f = str(fecha or "").strip()
    if not f:
        return ""
    d = None
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", f)
    if m:
        d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", f)
        if m:
            y = int(m.group(3))
            d = (y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
    if not d:
        return ""
    hh = 12
    m = re.match(r"(\d{1,2})(?:\s+(\d{2}))?\s*(a m|am|p m|pm)?", _n(hora))
    if m:
        hh = int(m.group(1)) % 12 if m.group(3) else int(m.group(1))
        if m.group(3) in ("p m", "pm"):
            hh += 12
        hh = min(hh, 23)
    try:
        return datetime(d[0], d[1], d[2], hh).isoformat(timespec="seconds")
    except ValueError:
        return ""


def referencias_de_tabla(cabecera, filas, ahora: Optional[str] = None) -> list:
    """Referencias [{titulo, texto, subtema, fecha}] de la hoja de notas
    analizadas: necesita TÍTULO y TEMA; usa RESUMEN como texto y la fecha
    (FECHA INFORME o FECHA, + HORA B) para quedarse con los últimos
    DIAS_REFERENCIA días y, por titular, con la nota más reciente.
    No cuentan las filas con «No aplica», «Duplicada» o sin TEMA, ni un
    titular con TEMAS distintos en la misma fecha (ambiguo)."""
    cab = [_n(c) for c in cabecera]

    def col(opciones):
        for o in opciones:
            if o in cab:
                return cab.index(o)
        return None
    i_t, i_s = col(_COLS_TITULO), col(_COLS_TEMA)
    if i_t is None or i_s is None:
        return []
    i_f, i_h, i_o = col(_COLS_FECHA), col(_COLS_HORA), col(_COLS_TONO)
    i_x = [cab.index(o) for o in _COLS_RESUMEN if o in cab]
    ahora = ahora or datetime.now().isoformat(timespec="seconds")
    limite = (datetime.now() - timedelta(days=DIAS_REFERENCIA)).isoformat(timespec="seconds")
    por_tit: Dict[str, dict] = {}
    temas: Dict[tuple, set] = {}
    for fila in filas:
        fila = list(fila or [])
        get = (lambda i: _celda(fila[i]) if i is not None and i < len(fila) else "")
        tit, sub = get(i_t), get(i_s)
        k = _n(tit)
        if not k or not sub or _n(sub) in _NO_VALE:
            continue
        if i_o is not None and _n(get(i_o)) in _NO_VALE:
            continue
        fecha = (_fecha_boletin(get(i_f), get(i_h)) if i_f is not None else "") or ahora
        if fecha < limite:
            continue
        texto = max((get(i) for i in i_x), key=len, default="")
        e = {"titulo": tit[:300], "texto": _n(texto)[:500], "subtema": sub.strip(),
             "fecha": min(fecha, ahora)}
        temas.setdefault((k, e["fecha"]), set()).add(_n(sub))
        if k not in por_tit or e["fecha"] >= por_tit[k]["fecha"]:
            por_tit[k] = e
    return [e for k, e in por_tit.items() if len(temas.get((k, e["fecha"]), ())) == 1]


def buscar_referencia(entradas: list, titulo: str, textos=()) -> Optional[dict]:
    """Referencia de la MISMA noticia, o None.

    - Titular: igual o casi igual (mismas palabras de contenido y nombres
      propios), solo si es informativo (4+ palabras de contenido) y con las
      mismas cifras («deja 2 muertos» no es «deja 5 muertos»).
    - Texto: el comienzo del texto de la nota (Resumen o cuerpo) casi igual
      al RESUMEN de la hoja (la misma nota con otro titular)."""
    tn = _n(titulo)
    c, p, cifras = _contenido(titulo), _propios(titulo), _cifras(titulo)
    gs = [g for g in (_gramas(t) for t in (textos or []) if t) if len(g) >= MIN_GRAMAS_TEXTO]
    mejor, mejor_s = None, 0.0
    for e in entradas or []:
        et = e.get("titulo") or ""
        s = 0.0
        ec = _contenido(et)
        if tn and len(c) >= MIN_PAL_TITULO and len(ec) >= MIN_PAL_TITULO \
                and _cifras(et) == cifras:
            if _n(et) == tn:
                return e
            if _propios(et) == p or _en_mayusculas(et) or _en_mayusculas(titulo):
                jt = _jac(c, ec)
                if jt >= UMBRAL_TITULO:
                    s = jt
        eg = _gramas(e.get("texto") or "")
        if len(eg) >= MIN_GRAMAS_TEXTO:
            for g in gs:
                jg = _jac(g, eg)
                if jg >= UMBRAL_TEXTO:
                    s = max(s, jg)
        if s > mejor_s:
            mejor, mejor_s = e, s
    return mejor
