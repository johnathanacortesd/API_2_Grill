# -*- coding: utf-8 -*-
"""Coherencia de subtema y tema contra el TEXTO de la noticia (v4.70).

Regla del usuario (2026-11-02):
  * Noticias iguales o realmente similares (Título y sobre todo CuerpoEs)
    deben quedar con el MISMO subtema.
  * Un subtema no se le pone a una noticia que no tiene nada que ver con él:
    no se agrupa "por agrupar".
  * Los subtemas se pueden agrupar en un Tema solo si guardan relación con
    el texto de la columna Contexto analizado.

Este módulo no llama a ningún LLM. Calcula similitud TF-IDF (palabras y
bigramas, sin tildes, sin palabras vacías y sin los tokens de la marca,
alias y voceros, que aparecen en todas las notas y no distinguen hechos)
entre los textos de las noticias (unigramas: separan mejor que bigramas
en la calibración), y ofrece:

  * ``matriz_similitud``: coseno entre textos.
  * ``componentes``: componentes conexos con un umbral.
  * ``texto_sin_titulo``: columna «texto» = Contexto analizado sin el
    Título, tolerante a diferencias de espacios, comillas, entidades HTML
    y limpieza del título.
  * ``variantes_vocero``: formas cortas de un vocero (nombre + primer
    apellido) para encontrarlo en el CuerpoEs.

Umbrales (calibrados con notas reales de prensa colombiana reescritas por
varios medios; ver tests/test_v470_coherencia_texto.py):
  * SIM_MISMA_NOTICIA = 0.45: misma noticia contada por otro medio con
    redacción propia (≈ 0.55-0.70; copias del mismo cable ≈ 0.85-1.0).
  * SIM_RELACION_SUBTEMA = 0.28: por debajo, dos notas NO comparten
    subtema salvo que el rótulo esté en el titular/entrada de ambas
    (hechos distintos del mismo asunto ≈ 0.15-0.20; ajenas ≈ 0.00-0.05).
  * SIM_RELACION_TEMA = 0.08: dos subtemas pueden ir al mismo tema si
    sus Contexto analizado tienen relación textual mínima (mismo asunto,
    hechos distintos ≈ 0.15; ajenas ≈ 0.00-0.05).
"""
from __future__ import annotations

import html
import re
import unicodedata
from typing import Iterable, List, Optional, Sequence

SIM_MISMA_NOTICIA = 0.45
SIM_RELACION_SUBTEMA = 0.28
SIM_RELACION_TEMA = 0.08

# Palabras vacías del español (sin tildes, minúsculas) + verbos de habla y
# muletillas de prensa que no distinguen hechos.
STOP_ES = set("""
a al algo algun alguna algunas alguno algunos ante antes aqui asi aun aunque bajo bien cada casi
como con contra cual cuales cuando cuanto de del desde donde dos durante e el ella ellas ellos
en entre era eran eres es esa esas ese eso esos esta estaba estaban estado estan estar este esto
estos fue fueron fui gran ha haber habia habian han hasta hay hace hacen hacer hacia hizo la las
le les lo los mas me mi mientras mis mismo mucho muchos muy nada ni no nos nosotros nuestra
nuestro nuestros nuevo nueva o otra otras otro otros para pero poco por porque pues que quien
quienes se sea sean segun ser si sido siempre sin sino sobre solo son su sus tal tambien tan
tanto te tiene tienen todo toda todas todos tras tu tus un una uno unos unas y ya yo
dijo dice afirmo aseguro explico indico senalo agrego manifesto anuncio informo destaco
segun ademas asimismo luego ahora hoy ayer manana tarde noche semana mes ano anos dia dias
lunes martes miercoles jueves viernes sabado domingo enero febrero marzo abril mayo junio julio
agosto septiembre octubre noviembre diciembre primer primera primero segundo tercer tres cuatro
cinco seis siete ocho nueve diez mil millones millon pesos ciento cientos parte tipo forma caso
puede pueden podria deben debe hacerlo esto aquello cual vez veces traves medio medios noticia
noticias nota informacion foto fotos video imagen imagenes redaccion fuente fuentes leer mas
tambien entonces mediante cerca lejos dentro fuera sido siendo seran sera va van vamos
""".split())


def _sin_tildes(s: str) -> str:
    s = unicodedata.normalize('NFKD', str(s or ''))
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


def tokens(texto: str) -> List[str]:
    """Tokens normalizados (sin tildes, minúsculas) de 3+ letras o números
    con letras; sin palabras vacías."""
    return [w for w in re.findall(r'[a-z0-9ñ]+', _sin_tildes(texto))
            if len(w) >= 3 and w not in STOP_ES and not w.isdigit()]


def tokens_marca(brand: str = '', aliases: Sequence[str] = (),
                 voceros: Sequence[str] = ()) -> set:
    """Tokens de marca, alias y voceros: dicen QUIÉN, no QUÉ pasó."""
    out = set()
    for x in [brand, *(aliases or []), *(voceros or [])]:
        out.update(tokens(x or ''))
    return out


def _stem(w: str) -> str:
    if len(w) > 5 and w.endswith('es'):
        return w[:-2]
    if len(w) > 4 and w.endswith('s'):
        return w[:-1]
    return w


def _analizador(excluir: Iterable[str]):
    excl = {_stem(w) for w in (excluir or ())}

    def analizar(doc: str) -> List[str]:
        toks = [_stem(w) for w in tokens(doc)]
        return [t for t in toks if t not in excl]
    return analizar


def matriz_similitud(textos: Sequence[str], excluir: Iterable[str] = ()):
    """Matriz (numpy) de coseno TF-IDF entre textos.

    Las palabras presentes en más del 60 % de los textos (con 6 o más
    textos) se descartan: en un dossier todo habla de la marca o de la
    ciudad y eso no distingue hechos.
    """
    import numpy as np
    n = len(textos)
    if n == 0:
        return np.zeros((0, 0))
    if n == 1:
        return np.ones((1, 1))
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(analyzer=_analizador(excluir), sublinear_tf=True,
                          max_df=(0.6 if n >= 6 else 1.0), min_df=1)
    docs = [str(t or '') for t in textos]
    try:
        X = vec.fit_transform(docs)
    except ValueError:
        # Vocabulario vacío (todo eran palabras vacías o de marca).
        return np.eye(n)
    S = (X @ X.T).toarray()
    # Textos vacíos: sin relación con nada salvo consigo mismos.
    vacios = [i for i, d in enumerate(docs) if not tokens(d)]
    for i in vacios:
        S[i, :] = 0.0
        S[:, i] = 0.0
        S[i, i] = 1.0
    return S


def componentes(indices: Sequence[int], S, umbral: float) -> List[List[int]]:
    """Componentes conexos de `indices` con aristas S[i, j] >= umbral."""
    idx = list(indices)
    par = {i: i for i in idx}

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            i, j = idx[a], idx[b]
            if S[i, j] >= umbral:
                ri, rj = find(i), find(j)
                if ri != rj:
                    par[max(ri, rj)] = min(ri, rj)
    grupos = {}
    for i in idx:
        grupos.setdefault(find(i), []).append(i)
    return list(grupos.values())


def texto_noticia(titulo: str, cuerpo: str, contexto: str = '',
                  max_cuerpo: int = 4000) -> str:
    """Texto que representa la noticia para compararla: Título (pesa
    doble), Contexto analizado y CuerpoEs (recortado)."""
    t = str(titulo or '')
    return ' '.join([t, t, str(contexto or ''), str(cuerpo or '')[:max_cuerpo]])


# ---------------------------------------------------------------------------
# Columna «texto»: Contexto analizado sin el Título.
# ---------------------------------------------------------------------------
_COMILLAS = str.maketrans({'“': '"', '”': '"', '«': '"', '»': '"', '‘': "'",
                           '’': "'", ' ': ' '})


def _norm_cmp(s: str) -> str:
    """Normalización para comparar: sin tildes, sin signos, minúsculas."""
    s = _sin_tildes(html.unescape(str(s or '')).translate(_COMILLAS))
    return re.sub(r'[^a-z0-9ñ]+', '', s)


def _quitar_prefijo_tolerante(texto: str, titulo: str) -> Optional[str]:
    """Si `texto` empieza por `titulo` (ignorando tildes, mayúsculas,
    espacios, comillas y signos), devuelve el resto; si no, None."""
    objetivo = _norm_cmp(titulo)
    if len(objetivo) < 8:
        return None
    acumulado = ''
    for k, ch in enumerate(texto):
        acumulado += _norm_cmp(ch)
        if len(acumulado) >= len(objetivo):
            if acumulado[:len(objetivo)] != objetivo:
                return None
            resto = texto[k + 1:]
            # El título termina en medio de una palabra: no es el título.
            if resto[:1].isalnum() and ch.isalnum():
                return None
            return resto
        if not objetivo.startswith(acumulado):
            return None
    return None


def texto_sin_titulo(contexto: str, titulo: str) -> str:
    """Columna «texto»: Contexto analizado sin el Título.

    1. Quita el título exacto donde aparezca (cálculo histórico de v4.56).
    2. Si el Contexto EMPIEZA por el título con diferencias de forma
       (entidades HTML, comillas tipográficas, espacios dobles, título
       limpiado por el exportador, punto final), también se quita.
    3. Limpia espacios y la puntuación suelta que deja el recorte.
    """
    ctx = html.unescape(str(contexto or ''))
    tit = html.unescape(str(titulo or '')).strip()
    if not ctx.strip() or ctx.strip() == '-':
        return ' '.join(ctx.split())
    base = ' '.join(ctx.split())
    if tit:
        tit1 = ' '.join(tit.split())
        resto = _quitar_prefijo_tolerante(base, tit1)
        if resto is not None:
            base = resto
        # Título exacto en otra posición (cálculo histórico v4.56), solo
        # como frase completa: no se corta dentro de otra palabra.
        base = re.sub(r'(?<!\w)' + re.escape(tit1) + r'(?!\w)', ' ', base)
    base = ' '.join(base.split())
    base = re.sub(r'^[\s.,;:\-–—|"\']+', '', base)
    return base.strip()


# ---------------------------------------------------------------------------
# Voceros: formas cortas (nombre + primer apellido).
# ---------------------------------------------------------------------------
_PARTICULAS = {'de', 'del', 'la', 'las', 'los', 'y', 'san', 'van', 'von'}
# Nombres de pila frecuentes: con tres palabras, si la segunda es un nombre
# de pila («Juan Carlos Pérez») la forma corta es nombre + apellido
# («Juan Pérez»), nunca «Juan Carlos» (coincidiría con cualquier Juan Carlos).
NOMBRES_PILA = set("""
adriana alberto alejandra alejandro alexander alfonso alicia alvaro ana andrea andres angela
antonio armando arturo beatriz camila camilo carlos carolina catalina cesar clara claudia
cristian cristina daniel daniela david diana diego eduardo elena enrique ernesto esteban
eugenia federico felipe fernanda fernando francisco gabriel gabriela german gloria gonzalo
guillermo gustavo hector helena hernan hernando ignacio isabel jaime javier jesus jorge jose
juan juana juliana julian julio laura leonardo liliana lina lucia luis luisa manuel marcela
marco margarita maria mariana mario marta martha mauricio miguel monica natalia nelson nicolas
oscar pablo patricia paola paula pedro rafael ramiro ricardo roberto rodrigo rosa sandra
santiago sara sebastian sergio sofia susana teresa tomas valentina valeria victor vivian
william ximena yolanda
""".split())


def variantes_vocero(nombre: str) -> List[str]:
    """Formas con las que la prensa nombra a un vocero.

    «Miguel Silva Moyano» -> ['Miguel Silva Moyano', 'Miguel Silva'].
    «Juan Carlos Pérez» -> ['Juan Carlos Pérez', 'Juan Pérez'].
    «Carlos Fernando Galán Sarmiento» -> [..., 'Carlos Fernando Galán',
    'Carlos Galán']. Nunca devuelve el apellido solo (homónimos) ni el
    nombre de pila solo. Nombres de 1-2 palabras quedan tal cual. Frases
    que no son nombres («el alcalde») se devuelven sin cambios.
    """
    n = ' '.join(str(nombre or '').split())
    if not n:
        return []
    salida = [n]
    contenido = [p for p in n.split() if p.lower() not in _PARTICULAS]
    es_nombre = all(p[:1].isupper() for p in contenido)
    if es_nombre and len(contenido) == 3:
        if _sin_tildes(contenido[1]) in NOMBRES_PILA:
            salida.append('%s %s' % (contenido[0], contenido[2]))
        else:
            salida.append('%s %s' % (contenido[0], contenido[1]))
    elif es_nombre and len(contenido) == 4:
        salida.append(' '.join(contenido[:3]))
        salida.append('%s %s' % (contenido[0], contenido[2]))
    out, vistos = [], set()
    for s in salida:
        k = _sin_tildes(s)
        if k not in vistos:
            vistos.add(k)
            out.append(s)
    return out


def expandir_voceros(voceros: Sequence[str]) -> List[str]:
    out, vistos = [], set()
    for v in voceros or []:
        for x in variantes_vocero(v):
            k = _sin_tildes(x)
            if k not in vistos:
                vistos.add(k)
                out.append(x)
    return out
