# -*- coding: utf-8 -*-
"""Motor de Tono, Tema y Sub-tema (reemplaza el analisis de ai_analyzer.enrich_rows_with_ai).

CONTRATO (identico al motor anterior, para no tocar pipeline.py ni el formato de salida):
    entra -> rows (lista de dicts), km, brand, aliases, api_key, model, progress_callback,
             tone_model, theme_model, extra
    sale  -> los mismos rows con: 'Contexto analizado', 'Tono_IA', 'Tema_IA', 'Subtema_IA'
             (las filas duplicadas conservan 'Duplicada', '-', '-').

POR QUE ESTE MOTOR DA MEJOR RESULTADO QUE UN PROMPT SUELTO
  1. La etiqueta se decide por GRUPO de notas equivalentes, nunca fila por fila: dos notas
     iguales no pueden salir con tono distinto.
  2. Primero el SUB-TEMA (sintesis del hecho) y despues el TONO, con rubrica ordenada P1/P2/P3.
  3. VALIDADOR duro (3-7 palabras, sin verbo conjugado al inicio, sin terminar en preposicion,
     sin rotulos vacios) + ciclo de reparacion contra el propio modelo.
  4. El TEMA se arma BOTTOM-UP en ESTE LOTE: se canonizan subtemas del mismo hecho y se agrupan
     subtemas afines bajo un nombre mas general. No hay lista cerrada ni memoria entre corridas.
     Un subtema canonico tiene exactamente un tema. Nunca "Otros". Nunca un Tema vacio:
     si el gate rechaza, se repara o se usa un fallback no vacio (frase nominal del
     subtema/titulo). Rechazo ≠ celda en blanco.
     EXCEPCION: si el cliente sube un PKL de tema, las clases de ese modelo son la
     fuente de Tema_IA. El gate del lote NO las reescribe. Si sube un PKL de tono,
     ese modelo es la fuente de Tono_IA (la guarda LLM no lo pisa).
  5. Los sub-temas ya usados viajan en cada lote como CANDIDATOS: un mismo hecho reutiliza el
     mismo texto en vez de generar variantes.

Regla central del tono: el tema no decide el tono. Una nota triste o grave (desempleo, salud
mental, muertes, robos) NO es negativa para la marca; Negativo exige critica, denuncia o
señalamiento DIRIGIDO a la marca o a su vocero.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import requests

from catalogo_tono_tema import (
    CRITERIOS_TONO, CUBO_PROHIBIDO, EJEMPLOS, EJEMPLOS_SECTOR, EJEMPLOS_TEMA, MAX_PAL, META_CUBO,
    MIN_PAL, REGLAS_SUBTEMA, REGLAS_TEMA, TEMA_MAX_PAL, TEMA_MIN_PAL, TEMAS_EJEMPLO_BUENOS,
    TEMAS_EJEMPLO_MALOS, TONOS, taxonomia_por_nombre,
)


def _regex_coincidencia(brand: str, aliases: Sequence[str], voceros: Sequence[str]) -> List[str]:
    """Regexes de marca + alias + voceros para localizar la evidencia en el texto."""
    rx = []
    alineados = [x for x in aliases if x and len(str(x).strip()) > 2]
    vocs = [v for v in voceros if v and len(str(v).strip()) > 2]
    for nombre in [brand] + list(alineados) + list(vocs):
        n = nz(nombre)
        if not n or len(n) < 3:
            continue
        # frase exacta con bordes de palabra, sobre texto normalizado
        rx.append(r'(?<![a-z0-9])' + re.escape(n) + r'(?![a-z0-9])')
    return rx


def _mención_normalizada_en(p_norm: str, rx) -> bool:
    return bool(rx) and any(re.search(r, p_norm) for r in rx)


def _mención_bruta(texto: str, brand: str, aliases, voceros) -> Optional[int]:
    """Devuelve el offset (en chars del texto crudo) de la primera mención de
    la marca, un alias o un vocero, comparando sin acentos ni mayusculas.
    Es el offset que sí se puede usar para recortar el texto original."""
    palabras = ' '.join(texto.split()).split()  # crudas, sin saltos
    for nombre in [brand] + [a for a in aliases if a] + [v for v in voceros if v]:
        target = nz(nombre).split()
        if not target or len(target) < 1:
            continue
        tlen = len(target)
        for k in range(len(palabras) - tlen + 1):
            if [nz(w) for w in palabras[k:k + tlen]] == target:
                pos = sum(len(palabras[q]) + 1 for q in range(k))
                return pos
    return None


def _texto_hasta_terminal(texto: str, n: int) -> str:
    """Recorta el texto terminando despues del n-esimo punto final (. ! ? …)."""
    if not texto:
        return ""
    idx = -1
    for _ in range(n):
        m = re.search(r'[.!?\u2026]', texto[idx + 1:])
        if not m:
            break
        idx = idx + 1 + m.end()
    return texto[:idx + 1] if idx >= 0 else texto


def _contexto_minimo_util(ctx: str, titulo: str, texto: str) -> str:
    """Garantiza un contexto informativo mínimo para la columna de auditoría.

    Si la extracción por mención devolvió solo un fragmento (p. ej. la firma
    "Edwin Bernal, director ejecutivo de Cotelco."), se completa con el
    titular y el texto más completo disponible (resumen). Un fragmento no
    resume la noticia y degrada el Excel entregable.
    """
    c = (ctx or '').strip()
    if len(c) >= 140 and len(c.split()) >= 18:
        return c
    fb = ' '.join(x.strip() for x in [titulo or '', (texto or '')[:1200]]
                  if x and x.strip())
    fb = re.sub(r'\s+', ' ', fb).strip()[:6000]
    return fb if len(fb) > len(c) else c


def _contexto_marca(texto: str, titulo: str, brand: str, aliases: Sequence[str],
                    voceros: Optional[Sequence[str]] = None) -> str:
    """Extrae contexto literal y amplio, sin resumir ni inventar texto.

    Conserva cada párrafo de ``CuerpoEs`` que menciona explícitamente la marca,
    alias o vocero. Una sola oración suele omitir el verbo que explica la
    participación (por ejemplo, ``con la colaboración de``) o el objeto del
    estudio, por eso no se devuelve solo el fragmento posterior a la mención.
    """
    def limpiar(s) -> str:
        if not s:
            return ""
        s = ctrl(s)
        s = re.sub(r'https?://\S+|www\.\S+', '', s)
        s = re.sub(r'^[ \t]*link[ \t]*', '', s, flags=re.I)
        s = re.sub(r'[ \t]+', ' ', s)
        return s.strip()

    cuerpo = limpiar(texto)
    titulo_limpio = limpiar(titulo)
    rx = _regex_coincidencia(brand, aliases, voceros or [])
    if not rx:
        return titulo_limpio[:6000]

    parrafos = [p.strip() for p in re.split(r'\n+', cuerpo) if p.strip()]
    relevantes = [p for p in parrafos if _mención_normalizada_en(nz(p), rx)]
    if relevantes:
        salida = '\n\n'.join(relevantes)
        if len(salida) <= 6000:
            return salida
        # Si el export pegó todo el artículo en un solo párrafo, conservar las
        # oraciones con evidencia y sus vecinas inmediatas, literalmente.
        trozos = []
        for p in relevantes:
            oraciones = [x.strip() for x in re.split(r'(?<=[.!?…])\s+', p) if x.strip()]
            hits = [i for i, o in enumerate(oraciones) if _mención_normalizada_en(nz(o), rx)]
            indices = sorted({j for i in hits for j in (i - 1, i, i + 1)
                              if 0 <= j < len(oraciones)})
            trozos.extend(oraciones[j] for j in indices)
        return '\n'.join(dict.fromkeys(trozos))[:6000]

    if _mención_normalizada_en(nz(titulo_limpio), rx):
        return (titulo_limpio + ('\n\n' + parrafos[0] if parrafos else '')).strip()[:6000]
    return titulo_limpio[:6000]

# Tipos de medio de radiodifusión (comparación con nz(): minúsculas, sin
# tildes). Los transcripts son largos y repiten menciones: el extracto se
# acota más. Calibración 2026-09-23: el contexto debe ser el extracto exacto
# de las menciones, no párrafos completos.
MEDIOS_RADIODIFUSION = {'radio', 'television', 'tv', 'aire', 'cable', 'am', 'fm'}

# Topes del extracto exacto de menciones (caracteres). Antes: 6000 parejo.
TOPE_CTX_GENERAL = 2000
TOPE_CTX_RADIODIFUSION = 1200


def _contexto_exacto_marca(texto: str, titulo: str, brand: str,
                           aliases: Sequence[str], voceros: Sequence[str] = (),
                           tipo_medio: str = '') -> str:
    """Extracto exacto de las menciones de marca, alias o vocero.

    Devuelve las oraciones que contienen la mención, literales y sin resumir,
    deduplicadas y en orden de aparición. No se devuelven párrafos completos:
    en prensa y sobre todo en radio/televisión los párrafos y transcripts son
    largos y el contexto se volvía inmanejable. Se acumulan oraciones
    completas hasta el tope (1200 caracteres en radiodifusión, 2000 en el
    resto); el texto conserva literalmente ortografía, tildes y puntuación.
    """
    fuente = str(texto or '')
    nombres = [str(x).strip() for x in [brand, *(aliases or []), *(voceros or [])]
               if str(x or '').strip() and len(nz(x)) >= 3]
    if not fuente or not nombres:
        return str(titulo or '').strip()[:TOPE_CTX_GENERAL]

    patrones = [re.compile(r'(?<![a-z0-9])' + re.escape(nz(x)) + r'(?![a-z0-9])')
                for x in nombres]

    def menciona(fragmento: str) -> bool:
        return any(p.search(nz(fragmento)) for p in patrones)

    # Oraciones: los transcripts de radio/tv no traen párrafos; partir por
    # puntuación y por saltos de línea cubre ambos casos.
    oraciones = [o.strip() for o in re.split(r'(?<=[.!?…])\s+|\r?\n+', fuente)
                 if o.strip()]
    hits = [o for o in oraciones if menciona(o)]
    if not hits:
        # La marca solo aparece en el titular o no aparece: no se inventa.
        return str(titulo or '').strip()[:TOPE_CTX_GENERAL]

    es_rtv = nz(tipo_medio) in MEDIOS_RADIODIFUSION
    tope = TOPE_CTX_RADIODIFUSION if es_rtv else TOPE_CTX_GENERAL
    vistas = []
    for o in hits:
        o = re.sub(r'\s+', ' ', o).strip()
        if o and o not in vistas:
            vistas.append(o)
    # Oraciones completas hasta el tope; al menos la primera mención va
    # entera (si una sola oración supera el tope, se corta ahí).
    salida, total = [], 0
    for o in vistas:
        if salida and total + len(o) + 1 > tope:
            break
        salida.append(o)
        total += len(o) + 1
    texto_out = ' '.join(salida).strip()
    return texto_out[:tope] if len(texto_out) > tope else texto_out
BASE_URL_DEFECTO = "https://api.openai.com/v1"
MODELO_DEFECTO = "gpt-4.1-nano-2025-04-14"
JEV_URL_DEFECTO = "https://api.typesafe.ai/v1/systemone"
TAM_LOTE_DEFECTO = 10
WORKERS_DEFECTO = 8
UMBRAL_TITULO_DEFECTO = 92
UMBRAL_CUERPO_DEFECTO = 85
K_BODY, MIN_GRAMAS, MIN_PALABRAS_TITULO = 5, 30, 3

_ULTIMO_RESUMEN: Dict[str, object] = {}


# Tarifas USD por millón de tokens: (entrada, salida).
# nano: tarifas indicadas por el cliente. luna/sol: anuncio OpenAI 2026-09-22.
PRECIOS_MODELO_USD: Dict[str, Tuple[float, float]] = {
    'gpt-4.1-nano-2025-04-14': (0.10, 0.40),
    'gpt-6-luna': (0.10, 0.50),
    'gpt-6-sol': (2.00, 10.00),
}

_USO_LOCK = threading.Lock()


def _precios_modelo(modelo: Optional[str]) -> Tuple[float, float]:
    """(USD/millón entrada, USD/millón salida) para el modelo dado."""
    m = (str(modelo or '')).lower()
    for prefijo, precios in PRECIOS_MODELO_USD.items():
        if m.startswith(prefijo):
            return precios
    return (0.10, 0.40)


def _sumar_uso(uso: Optional[dict], prompt_tokens: object, completion_tokens: object) -> None:
    """Acumula tokens de una llamada LLM exitosa (seguro entre hilos)."""
    if uso is None:
        return
    try:
        pin, pout = int(prompt_tokens or 0), int(completion_tokens or 0)
    except (TypeError, ValueError):
        pin, pout = 0, 0
    with _USO_LOCK:
        uso['input'] = uso.get('input', 0) + pin
        uso['output'] = uso.get('output', 0) + pout
        uso['llamadas'] = uso.get('llamadas', 0) + 1


def _costo_aprox_usd(modelo: Optional[str], uso: dict) -> Tuple[float, float, float]:
    """(costo_usd, precio_input_millon, precio_output_millon)."""
    pin, pout = _precios_modelo(modelo)
    costo = (uso.get('input', 0) / 1_000_000) * pin + (uso.get('output', 0) / 1_000_000) * pout
    return round(costo, 4), pin, pout


def ultimo_resumen() -> Dict[str, object]:
    """Estadisticas de la ultima corrida (la interfaz las muestra al terminar)."""
    return dict(_ULTIMO_RESUMEN)


# ============================================================================
# 1. Utilidades de texto
# ============================================================================
def ctrl(s) -> str:
    return ''.join(ch for ch in str(s or '') if (ch >= ' ' or ch in '\n\t') and ch not in '\ufffe\uffff')


def nz(s) -> str:
    s = unicodedata.normalize('NFKD', ctrl(s))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9ñ ]+', ' ', s)).strip()


def words(s) -> List[str]:
    s = unicodedata.normalize('NFKD', ctrl(s))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    return re.findall(r'[a-z0-9ñ]+', s)


def grams(w: List[str], k: int) -> set:
    return set(tuple(w[i:i + k]) for i in range(max(0, len(w) - k + 1)))


def raiz(t: str) -> str:
    t = nz(t)
    if len(t) > 4 and t.endswith('es'):
        return t[:-2]
    if len(t) > 3 and t.endswith('s'):
        return t[:-1]
    return t


def sq(s) -> str:
    return re.sub(r'\s+', ' ', ctrl(s)).strip()


GENERIC_TITULO = set("""a al algo algunos ante antes aqui asi aun aunque bien cada como con contra cual cuando
de del desde donde dos el ella ellas ellos en entre era eran es esa esas ese eso esos esta estaba
estan este esto estos fue fueron ha hace hacia hasta hay la las le le los lo los mas me mi mis
mucho muy nos o os otra otras otro otros para pero poco por porque que quien se sea segun ser si
sin sobre son su sus tal tambien tan tanto te tiene tienen todo todos tu tus un una uno unos y ya""".split())


# ============================================================================
# 2. Grupo de notas equivalentes (republicaciones y duplicados de contenido)
# ============================================================================
def _texto_fila(row: dict, km: dict) -> str:
    """Devuelve el texto más completo disponible para juzgar el contexto.

    En los dossiers de Grill el cuerpo suele estar en CuerpoEs y el resumen puede
    ser un extracto vacío o demasiado corto. Escoger el primer campo disponible
    hacía que el tono se decidiera por el titular y por el tono general de la
    nota, no por el párrafo donde aparece la marca.
    """
    candidatos = [
        row.get('CuerpoEs'), row.get('Cuerpo'), row.get('Texto'),
        row.get('Texto completo'), row.get('Resumen - Aclaracion'),
        row.get('resumen corto'), row.get('Resumen'),
        row.get(km.get('resumen', 'Resumen - Aclaracion')),
    ]
    return max((str(v) for v in candidatos if v and str(v).strip()), key=len, default='')


def _titulo_fila(row: dict, km: dict) -> str:
    return str(row.get(km.get('titulo', 'Título')) or row.get('Título') or '')


def construir_grupos(
    rows: List[dict],
    km: dict,
    umbral_titulo: int = UMBRAL_TITULO_DEFECTO,
    umbral_cuerpo: int = UMBRAL_CUERPO_DEFECTO,
) -> Tuple[List[dict], Dict[int, int]]:
    """Agrupa filas no duplicadas por similitud de titulo (palabras de contenido) y de resumen.

    Devuelve (grupos, mapa_indice_fila -> id de grupo). Las filas 'is_duplicate' se excluyen:
    heredan la etiqueta de su original por 'ID duplicada' en el flujo de limpieza.
    """
    from rapidfuzz import fuzz, process

    idx_validos = [i for i, r in enumerate(rows) if not r.get('is_duplicate')]
    if not idx_validos:
        return [], {}

    # Orden determinista A–Z: facilita inspección, hace estable el representante
    # del grupo y evita que el orden de llegada cambie la canonización.
    idx_validos = sorted(
        idx_validos,
        key=lambda i: (nz(_titulo_fila(rows[i], km)), i),
    )
    base = []
    for i in idx_validos:
        tit = _titulo_fila(rows[i], km)
        txt = _texto_fila(rows[i], km)
        ctx = str(rows[i].get('Contexto analizado') or '')
        ctx = '' if ctx.strip() in ('', '-') else ctx
        base.append({
            'idx': i,
            'titulo': sq(tit),
            'texto': sq(txt),
            'ctit': set(w for w in words(tit) if w not in GENERIC_TITULO),
            'g5': grams(words(txt), K_BODY),
            # Contexto analizado de la marca: párrafos donde aparece la marca.
            # Dos noticias del mismo hecho suelen compartir pasajes citados
            # (declaraciones, cifras) aunque título y cuerpo difieran.
            'g5ctx': grams(words(ctx), K_BODY),
        })

    par = list(range(len(base)))

    # --- freno anti-encadenamiento (v4.16): las palabras omnipresentes del
    # dossier (p. ej. 'terremoto', 'hoteles', 'cali' en una cobertura sísmica)
    # no sirven como puente distintivo entre hechos. Sin esto, '36 hoteles
    # resultaron afectados' se encadenaba con 'traslado de adultos mayores
    # a hoteles' por compartir solo {cali, hoteles, terremoto}. El umbral es
    # adaptativo al dossier: >12% de los titulares (mínimo 8).
    _dfq = Counter()
    for b in base:
        for w in b['ctit']:
            _dfq[w] += 1
    _omni_corte = max(8, int(len(base) * 0.12))
    OMNI = {w for w, c in _dfq.items() if c > _omni_corte}

    def _puente_distintivo(a, b, minimo):
        """True si a y b comparten >= `minimo` palabras de contenido
        DISTINTIVAS (no omnipresentes en el dossier)."""
        return len((a - OMNI) & (b - OMNI)) >= minimo

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    def uni(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            par[max(ra, rb)] = min(ra, rb)

    tit = [' '.join(sorted(b['ctit'])) for b in base]
    if len(base) > 1:
        t1 = process.cdist(tit, tit, scorer=fuzz.ratio, workers=-1) / 100.0
        t2 = process.cdist(tit, tit, scorer=fuzz.token_sort_ratio, workers=-1) / 100.0
        t3 = process.cdist(tit, tit, scorer=fuzz.token_set_ratio, workers=-1) / 100.0
        import numpy as np
        T = np.maximum(t1, np.maximum(t2, t3))
        for i in range(len(base)):
            for j in np.where(T[i] >= umbral_titulo / 100.0)[0]:
                if j > i and _puente_distintivo(base[i]['ctit'], base[j]['ctit'],
                                               MIN_PALABRAS_TITULO):
                    uni(i, j)

        # --- Señal de bolsa de palabras (orden-independiente): dos noticias
        # parecidas pueden ordenar las palabras distinto. Se fusionan si Jaccard
        # de las palabras de CONTENIDO supera un suelo y comparten mínimo de
        # tokens. Cubre el caso real "Soledad fortalece la nutrición ... PAE" vs
        # "Soledad pone la nutrición ... el PAE fortalece el seguimiento ...".
        for i in range(len(base)):
            wi = base[i]['ctit']
            for j in range(i + 1, len(base)):
                if find(i) == find(j):
                    continue
                wj = base[j]['ctit']
                inter = wi & wj
                if len(inter) < MIN_PALABRAS_TITULO:
                    continue
                union = wi | wj
                if not union:
                    continue
                jac = len(inter) / len(union)
                # piso de Jaccard para evtar fusionar hechos distintos que solo
                # comparten pocas palabras comunes de la ciudad/entidad; además
                # el puente debe ser distintivo (v4.16: freno anti-encadenamiento).
                if (jac >= 0.42 or (jac >= 0.32 and t3[i, j] >= 0.88)) \
                        and _puente_distintivo(wi, wj, MIN_PALABRAS_TITULO):
                    uni(i, j)

        # Titulares cortos casi iguales: 2 palabras distintivas + token_set alto.
        # (Pase unico: antes quedo anidado por error dentro del loop anterior y
        # se ejecutaba len(base) veces; los merges son idempotentes asi que el
        # resultado es identico pero 445x mas rapido en dossiers medianos.)
        for i in range(len(base)):
            for j in range(i + 1, len(base)):
                if find(i) == find(j):
                    continue
                if _puente_distintivo(base[i]['ctit'], base[j]['ctit'], 2) \
                        and t3[i, j] >= 0.90:
                    uni(i, j)

    inv = defaultdict(set)
    for j, b in enumerate(base):
        for g in b['g5']:
            inv[g].add(j)
    for i, b in enumerate(base):
        if len(b['g5']) < MIN_GRAMAS:
            continue
        hits = Counter()
        for g in b['g5']:
            for j in inv.get(g, ()):
                if j != i:
                    hits[j] += 1
        for j, inter in hits.items():
            if j > i and min(len(b['g5']), len(base[j]['g5'])) >= MIN_GRAMAS and \
                    inter / min(len(b['g5']), len(base[j]['g5'])) >= umbral_cuerpo / 100.0:
                uni(i, j)

    # --- bolsa de palabras del CUERPO: mismo hecho con título muy distinto ---
    # Solo se evaluan pares que comparten AL MENOS un 5-gramo (via el indice inv),
    # asi el costo queda cerca de lineal en lugar de O(n^2) en dossiers grandes.
    for i, b in enumerate(base):
        if len(b['g5']) < 8:
            # cuerpos muy cortos usan token_set_ratio del texto plano
            continue
        candidatos = set()
        for g in b['g5']:
            for j in inv.get(g, ()):
                if j != i:
                    candidatos.add(j)
        for j in candidatos:
            if j < i or par[j] != j or find(i) == find(j):
                continue
            bj = base[j]
            if len(bj['g5']) < 8:
                continue
            same_g = len(b['g5'] & bj['g5']) / max(1, min(len(b['g5']), len(bj['g5'])))
            if same_g >= 0.70 and _puente_distintivo(b['ctit'], bj['ctit'],
                                                    MIN_PALABRAS_TITULO):
                uni(i, j)

    # --- señal de CONTEXTO ANALIZADO: mismo hecho, título y cuerpo distintos ---
    # Se fusionan si comparten >= 6 5-gramas del contexto de marca y el
    # solapamiento sobre el menor supera 0.55. Conservador: boilerplates
    # genéricos no alcanzan ese solapamiento porque los contextos son largos.
    inv_c = defaultdict(set)
    for j, b in enumerate(base):
        for g in b['g5ctx']:
            inv_c[g].add(j)
    for i, b in enumerate(base):
        if len(b['g5ctx']) < 6:
            continue
        hits = Counter()
        for g in b['g5ctx']:
            for j in inv_c.get(g, ()):
                if j != i:
                    hits[j] += 1
        for j, inter in hits.items():
            if j > i:
                den = min(len(b['g5ctx']), len(base[j]['g5ctx']))
                if den >= 6 and inter >= 6 and inter / den >= 0.55:
                    uni(i, j)

    por_raiz = defaultdict(list)
    for k in range(len(base)):
        por_raiz[find(k)].append(k)

    grupos, mapa = [], {}
    for gid, miembros in enumerate(sorted(por_raiz.values(), key=lambda v: -len(v)), 1):
        idxs = [base[k]['idx'] for k in miembros]
        reps = Counter(base[k]['titulo'] for k in miembros)
        rep = reps.most_common(1)[0][0] or base[miembros[0]]['texto'][:120]
        cuerpo = max((base[k]['texto'] for k in miembros), key=len)
        contextos = [str(rows[base[k]['idx']].get('Contexto analizado') or '')
                     for k in miembros]
        contexto = '\n\n'.join(dict.fromkeys(x.strip() for x in contextos if x.strip() and x.strip() != '-'))
        alt = [t for t in sorted(set(base[k]['titulo'] for k in miembros)) if t and t != rep][:3]
        grupos.append({'grupo': gid, 'n': len(idxs), 'idxs': idxs, 'titulo': rep,
                       'titulos_alt': alt, 'texto': cuerpo[:9000],
                       'contexto': contexto[:9000]})
        for i in idxs:
            mapa[i] = gid
    return grupos, mapa


# ============================================================================
# 3. Validador de etiquetas
# ============================================================================
PREP_FIN = {'de', 'del', 'la', 'el', 'los', 'las', 'un', 'una', 'unos', 'unas', 'para', 'en', 'con',
            'por', 'y', 'o', 'a', 'al', 'que', 'su', 'sus', 'sin', 'sobre', 'entre', 'tras', 'ni'}
CONECT = PREP_FIN | {'se', 'lo', 'le', 'les', 'es', 'son', 'como', 'mas', 'más', 'muy'}
# Preposiciones que nunca pueden abrir un TEMA ("Para Carnaval 2027").
PREP_INICIO_TEMA = {'para', 'de', 'del', 'en', 'con', 'por', 'sobre', 'entre', 'tras',
                    'desde', 'hacia', 'segun', 'según', 'sin', 'a', 'al', 'ante'}
VERBOS1 = set("""entregan anuncian avanza avanzan instalan reconocen firman inauguran denuncian alertan
piden exigen rechazan critican senalan señalan aseguran confirman advierten solicitan denunciaron
anunciaron instalaron avanzaron reconocieron inauguraron entregaron logran obtienen reciben presentan
lideran realizan adelantan ejecutan mejoran aumentan disminuyen reducen destinan aprueban celebran
conmemoran rinden posesionan nombran designan ratifican reafirman garantizan benefician participan
visitan recorren supervisan verifican socializan capacitan forman graduan certifican arranca culmina
inicia termina sigue siguen mantiene mantienen trabaja trabajan llevan deja dejan abre abren obtiene
recibe recibio recibieron pidieron exigieron superviso entrego anuncio advirtio aseguro confirmo
denuncio critico lidero presento aprobo celebro conmemoro designo nombro""".split())
RE_VERBO = re.compile(r'(aron|ieron|ió|eó|arán|erán|irán|aba|aban|amos|imos)$')
ROTULO_GEN = {
    'gestion gubernamental', 'gestion institucional', 'gestion departamental', 'actividad institucional',
    'actividad gubernamental', 'noticias de la entidad', 'noticias generales', 'temas generales',
    'cobertura informativa', 'panorama regional', 'informacion general', 'actualidad departamental',
    'eventos institucionales', 'otros', 'varios', 'general', 'miscelaneo', 'miscelanea',
}
MARCO = {'noticias', 'informacion', 'información', 'cobertura', 'actualidad', 'eventos', 'actividades',
         'destacados', 'panorama', 'menciones', 'informe'}
FILLER = MARCO | {'importantes', 'relevantes', 'generales', 'varias', 'diversos', 'diversas',
                  'departamental', 'institucional', 'gubernamental', 'regional', 'recientes', 'varios'}


def validar(sub_tema, tono, fuentes, min_pal=MIN_PAL, max_pal=MAX_PAL,
            _con_copia=True) -> List[str]:
    """Problemas encontrados. Lista vacia = etiqueta valida. Los avisos 'revisar_anclaje' son blandos.

    v4.21: `copia_titular` solo se marca cuando el subtema NO es ya una
    etiqueta valida por si misma. Que una etiqueta nominal bien formada
    aparezca dentro del titular («Estado de salud de Yamid Amat» en
    «Actualización sobre el estado de salud…») no es pereza del modelo:
    es lo esperado. Sin este filtro, docenas de etiquetas buenas iban a
    la vuelta de reparacion LLM, desperdiciando llamadas y arriesgando
    que el modelo «arregle» lo que estaba bien.
    """
    p = []
    sub_tema = str(sub_tema or '').strip()
    if not sub_tema:
        return ['vacio']
    if re.search(r'[:;|"\'«»—–]', sub_tema):
        p.append('caracter_marcador')
    w = sub_tema.split()
    if len(w) < min_pal:
        p.append('corto(%d)' % len(w))
    if len(w) > max_pal:
        p.append('largo(%d)' % len(w))
    if nz(w[-1]) in PREP_FIN:
        p.append('termina_preposicion')
    prim = nz(w[0])
    nexo2 = len(w) > 1 and nz(w[1]) in PREP_FIN
    if not nexo2 and (prim in VERBOS1 or (len(prim) > 4 and RE_VERBO.search(prim))):
        p.append('verbo_inicial(%s)' % prim)
    if nz(sub_tema) in ROTULO_GEN:
        p.append('rotulo_generico')
    toks = [nz(t) for t in w]
    if toks and toks[0] in MARCO and all(t in FILLER for t in toks[1:]):
        p.append('marco_vacio')
    # El subtema no debe recortar el titular en el mismo orden: es reformulación,
    # no copia ("Medicina multimodal y personalizada" ← "La medicina que viene
    # será multimodal y personalizada…"). El titular es fuentes[0].
    titulo = (fuentes or [''])[0] or ''
    if _con_copia and _subtema_copia_titular(sub_tema, titulo) \
            and not _es_etiqueta_valida(sub_tema):
        p.append('copia_titular')
    # Vago: solo evento genérico + sujeto genérico, sin objeto distintivo
    # ("Reunión de expertos en crimen").
    if _subtema_vago(sub_tema):
        p.append('subtema_vago')
    if tono not in TONOS:
        p.append('tono_invalido(%s)' % tono)
    fuente = ' '.join(nz(f) for f in (fuentes or []))
    ft = set(re.findall(r'[a-z0-9ñ]+', fuente))
    fr = {raiz(t) for t in ft}
    faltan = [t for t in toks if len(t) >= 4 and t not in CONECT and t not in ft and raiz(t) not in ft]
    if len(faltan) > 1:
        p.append('revisar_anclaje(%s)' % ','.join(faltan[:4]))
    return p


# ============================================================================
# 3b. Calidad del subtema: anti-copia del titular y anti-vaguedad
# ============================================================================
_VAGO_SUBTEMA = {
    'reunion', 'encuentro', 'evento', 'jornada', 'cita',           # evento genérico
    'experto', 'especialista', 'lider', 'actor', 'representante',  # sujeto genérico
}
# Nombres de evento: el subtema puede (y suele) repetirlos tal cual.
_EVENTO_NOMBRE = {
    'congreso', 'cumbre', 'foro', 'seminario', 'encuentro', 'feria', 'festival',
    'simposio', 'jornada', 'conversatorio', 'concurso', 'premio', 'asamblea',
    'inauguracion', 'lanzamiento', 'presentacion',
}


def _subtema_vago(sub_tema: str) -> bool:
    """True si el subtema es solo evento genérico + sujeto genérico.

    "Reunión de expertos en crimen" no dice el asunto; "Cumbre internacional
    de criminología" sí (tiene objeto distintivo).
    """
    toks = [raiz(w) for w in words(sub_tema) if nz(w) not in CONECT]
    if len(toks) < 2:
        return False
    n = sum(1 for w in toks if w in _VAGO_SUBTEMA)
    return n >= 2 and n / len(toks) >= 0.5


def _subtema_copia_titular(sub_tema: str, titulo: str) -> bool:
    """True si el subtema recorta el titular conservando el orden.

    "Medicina multimodal y personalizada" ← "La medicina que viene será
    multimodal y personalizada…" es copia; "Desafíos de la criminología con IA"
    ← "Criminología frente a la IA: …nuevos desafíos…" es reformulación válida
    (el orden cambia) y no se marca.
    """
    s, t = nz(sub_tema), nz(titulo or '')
    if not s or not t:
        return False
    sw = [w for w in s.split() if w not in CONECT]
    if any(w in _EVENTO_NOMBRE for w in sw):
        # Los nombres de evento se repiten tal cual por naturaleza
        # ("Cumbre internacional de criminología"); no es copia perezosa.
        return False
    if s in t:
        return True
    if len(sw) < 3:
        return False
    tw = t.split()
    j = 0
    for w in sw:
        while j < len(tw) and tw[j] != w:
            j += 1
        if j >= len(tw):
            return False
        j += 1
    return True


def _es_etiqueta_valida(sub_tema: str) -> bool:
    """True si el subtema ya es una etiqueta tematica valida por si misma,
    sin mirar el titular.

    v4.21: distingue «etiqueta que coincide con (parte de) el titular»
    de «titular copiado como subtema». Reusa validar() con _con_copia=False
    (sin ese flag habria recursion infinita: validar -> _es_etiqueta_valida
    -> validar). Los avisos blandos 'revisar_anclaje' se excluyen porque sin
    fuentes siempre saltarian. Una etiqueta nunca lleva ¡!¿?: un titular
    copiado con interjecciones («¡Atención! …») no es etiqueta valida.
    """
    s = str(sub_tema or '')
    if re.search(r'[¡!¿?]', s):
        return False
    pr = validar(s, 'Neutro', [], _con_copia=False)
    duros = [x for x in pr if not x.startswith('revisar_anclaje')]
    return not duros


# ============================================================================
# 4. Taxonomia de Temas (lista cerrada + propuesta de cubo nuevo especifico)
# ============================================================================
def patron(kw: str) -> str:
    if '(' in kw or '?' in kw:
        return r'(?<![a-z])' + kw
    if kw.endswith('*'):
        return r'(?<![a-z])' + re.escape(nz(kw[:-1])) + r'[a-z]*'
    return r'(?<![a-z])' + re.escape(nz(kw)) + r'(?![a-z])'


GEOGRAFIA_GENERICA = {
    'colombia', 'colombiano', 'colombiana', 'pais', 'nacional', 'region', 'regional',
    'barranquilla', 'cartagena', 'bogota', 'medellin', 'cali', 'sincelejo', 'monteria',
    'valledupar', 'santa marta', 'riohacha', 'soledad', 'atlantico', 'bolivar',
    'sucre', 'cordoba', 'magdalena', 'cesar', 'guajira', 'antioquia', 'bogotá',
}


def _es_geografia(token: str) -> bool:
    return nz(token) in {nz(x) for x in GEOGRAFIA_GENERICA}


def tema_de(sub_tema: str, titulo: str, tax: dict):
    """Dos pasadas: manda el sub-tema (sintesis limpia); el titulo solo si parece titular corto."""
    for txt in (nz(sub_tema), nz(titulo) if len(str(titulo or '')) <= 160 else ''):
        if not txt:
            continue
        for r in tax['reglas']:
            for k in r['claves']:
                if _es_geografia(k):
                    continue
                if re.search(patron(k), txt) and _tema_distinto_de_subtema(r['tema'], sub_tema):
                    return r['tema'], k
    return None, None


ARTICULOS = {'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas'}
PREP_SINTAXIS = {'de', 'del', 'en', 'para', 'por', 'con', 'sobre', 'a', 'al', 'y', 'e',
                 'entre', 'contra', 'desde', 'sin'}
ADJ_PRENOMINAL = {
    'nuevo', 'nueva', 'nuevos', 'nuevas', 'gran', 'grande', 'grandes', 'alto', 'alta',
    'altos', 'altas', 'primer', 'primera', 'primeros', 'primeras', 'buen', 'buena',
    'buenos', 'buenas', 'mejor', 'mayor', 'menor',
}
ADJETIVOS_TEMA = {
    'escolar', 'juvenil', 'laboral', 'mental', 'social', 'publico', 'publica', 'publicos',
    'publicas', 'rural', 'urbano', 'urbana', 'forestal', 'ambiental', 'nutricional',
    'deportivo', 'deportiva', 'cultural', 'economico', 'economica', 'politico', 'politica',
    'comunitaria', 'comunitario', 'avicola', 'medica', 'medico', 'sanitaria', 'sanitario',
    'educativo', 'educativa', 'tecnologico', 'tecnologica', 'nacional', 'municipal',
    'local', 'internacional', 'familiar', 'industrial', 'comercial', 'alimentaria',
    'alimentario', 'temprana', 'temprano', 'iberoamericano', 'iberoamericana',
    'multimodal', 'personalizada', 'personalizado', 'ciudadana', 'ciudadano',
    'universitario', 'universitaria', 'universitarios', 'universitarias',
    'ecologica', 'ecologico', 'ecologicas', 'ecologicos', 'estudiantil',
    'profesional', 'tecnica', 'tecnico', 'superior', 'inferiores', 'anterior',
    'posterior',
}
_RE_ADJETIVO = re.compile(
    r'(ales|iles|icos|icas|ivos|ivas|osos|osas|entes|antes|bles|al|il|ico|ica|ivo|iva|'
    r'oso|osa|ente|ante|ble)$'
)
# Terminaciones adjetivales que _RE_ADJETIVO no cubre; solo se usan para no
# marcar como "mash" un sustantivo seguido de adjetivos ("urbana", "costeña").
_RE_ADJ_EXTRA = re.compile(r'(ano|ana|ense|ensa|eño|eña|ino|ina)$')
_MARCO_HEAD = MARCO | {'estudio', 'estudios', 'reporte', 'reportes', 'cifra', 'cifras',
                       'balance', 'balances'}


def _capitalizar_etiqueta(s: str) -> str:
    s = sq(s)
    if not s:
        return s
    return s[0].upper() + s[1:]


# Sustantivos que el regex de adjetivos confunde por su terminación
# ("carnaval" termina en -al). Como cabeza de tema son sustantivos plenos:
# "Carnaval de Barranquilla", "Festival de cine".
_SUSTANTIVOS_NO_ADJETIVO = frozenset({
    'carnaval', 'carnavales', 'festival', 'festivales', 'hospital', 'hospitales',
    'canal', 'canales', 'animal', 'animales', 'portal', 'portales',
})


def _es_adjetivo_tematico(tok: str) -> bool:
    t = nz(tok)
    if t in _SUSTANTIVOS_NO_ADJETIVO:
        return False
    if t in ADJ_PRENOMINAL or t in ADJETIVOS_TEMA:
        return True
    return bool(t) and len(t) >= 5 and _RE_ADJETIVO.search(t)


def _token_es_lugar(w: str) -> bool:
    if _es_geografia(w):
        return True
    if not w or nz(w) in CONECT:
        return False
    if w.isupper() and 2 <= len(w) <= 5:
        return False
    return w[0].isupper() and not w.isupper()


def _pp_es_lugar(pp_toks: Sequence[str]) -> bool:
    if not pp_toks:
        return False
    prep = nz(pp_toks[0])
    rest = list(pp_toks[1:])
    i = 0
    while i < len(rest) and nz(rest[i]) in ARTICULOS:
        i += 1
    names = rest[i:]
    if not names:
        return False
    if prep in {'en', 'desde'}:
        return _token_es_lugar(names[0]) or _es_geografia(names[0])
    if prep in {'de', 'del'}:
        return all(_es_geografia(n) or _token_es_lugar(n) for n in names) and any(
            _es_geografia(n) for n in names)
    return False


def _quitar_marco_inicial(frase: str) -> str:
    toks = sq(frase).split()
    if len(toks) >= 4 and nz(toks[0]) in _MARCO_HEAD and nz(toks[1]) in {'sobre', 'de', 'del'}:
        rest = toks[2:]
        if len(rest) >= 2:
            return ' '.join(rest)
    return sq(frase)


def _quitar_cola_lugar(frase: str) -> str:
    toks = sq(frase).split()
    while len(toks) >= 3:
        prep_i = None
        for i in range(len(toks) - 1, 0, -1):
            if nz(toks[i]) in {'en', 'desde', 'de', 'del'} and _pp_es_lugar(toks[i:]):
                prep_i = i
                break
        if prep_i is None or prep_i < 2:
            break
        left = toks[:prep_i]
        if len(left) < 2 or nz(left[-1]) in PREP_FIN:
            break
        toks = left
    return ' '.join(toks)


def _quitar_ultimo_pp(frase: str) -> str:
    toks = sq(frase).split()
    last = None
    for i, t in enumerate(toks):
        if i > 0 and nz(t) in PREP_SINTAXIS:
            last = i
    if last is None or last < 2:
        return sq(frase)
    left = toks[:last]
    if len(left) < 2 or nz(left[-1]) in PREP_FIN:
        return sq(frase)
    return ' '.join(left)


VERBOS_CLAUSULA = VERBOS1 | set("""
hace hacen hacia hacian hizo hicieron
viene vienen vino vinieron
cuida cuidan cuidaba cuidaron
estudia estudian estudio estudiaron estudiar
consigue consiguen conseguir
lleva llevan llevar
va van fue fueron ir
gana ganan gano ganaron
hace visible
es son era eran
esta estan
tiene tienen
queda quedan
pasa pasan
dice dicen
pide piden
pone ponen
quiere quieren
sabe saben
sale salen
sigue siguen
viene
cuida
""".split())
INFINITIVOS_TEMA = set("""
estudiar conseguir hacer venir cuidar llevar ver ir ser estar tener haber
decir dar saber querer llegar pasar deber poner parecer quedar hablar
dejar seguir encontrar llamar pensar salir volver tomar conocer vivir
sentir tratar mirar contar empezar esperar buscar existir entrar trabajar
escribir perder producir ocurrir entender pedir recibir recordar terminar
permitir aparecer comenzar servir sacar necesitar mantener resultar leer
caer cambiar presentar crear abrir considerar oir acabar convertir ganar
formar traer partir morir aceptar realizar suponer comprender lograr
explicar preguntar tocar reconocer alcanzar nacer dirigir correr utilizar
pagar ofrecer descubrir decidir intentar visibilizar
""".split())
PRONOMBRES_BASURA = {
    'quien', 'quienes', 'cual', 'cuales', 'donde', 'adonde', 'cuando',
    'quien', 'quienes',
}
SIGLAS_PROHIBIDAS = {'ia', 'ai'}
EVENTOS_PERSONA = {
    'muerte', 'fallecimiento', 'asesinato', 'homicidio', 'velorio', 'funeral',
    'homenaje', 'deceso',
}
EVENTOS_PUNTUALES = {
    'graduacion', 'grado', 'titulacion', 'diplomado',
}
HECHOS_INCIDENTALES = {'intento', 'intentos', 'caso', 'casos'}
OBJETOS_INCOMPLETOS = {
    'nutricion', 'manejo', 'salud', 'conexion', 'ia',
}
ROTULOS_VAGOS_TEMA = {
    'reunion de expertos', 'reunion de especialistas', 'encuentro de expertos',
    'encuentro de especialistas', 'mesa de expertos', 'panel de expertos',
    'ayuda en salud', 'ayuda de salud', 'reunion de trabajo',
}
RE_INFINITIVO = re.compile(r'(ar|er|ir)$')
NOMBRES_PERSONA_FREQ = {
    'juliana', 'juan', 'maria', 'pedro', 'luis', 'ana', 'carlos', 'sofia',
    'andres', 'camila', 'diego', 'laura', 'felipe', 'valentina', 'sebastian',
    'daniela', 'alejandro', 'isabella', 'santiago', 'mariana',
}


def _es_verbo_clausula(tok: str) -> bool:
    t = nz(tok)
    if not t:
        return False
    if t in VERBOS_CLAUSULA or t in INFINITIVOS_TEMA:
        return True
    if t in ADJETIVOS_TEMA or t in ADJ_PRENOMINAL or t in CONECT:
        return False
    return len(t) > 4 and bool(RE_VERBO.search(t))


def _parece_infinitivo(tok: str) -> bool:
    return nz(tok) in INFINITIVOS_TEMA


def _tema_copia_o_prefijo_titulo(tema: str, titulos: Optional[Sequence[str]]) -> bool:
    """True si el tema es el titular, un near-copy o un prefijo de sus primeras palabras."""
    from rapidfuzz import fuzz
    t = nz(tema)
    if not t:
        return False
    tw = t.split()
    if not tw:
        return False

    def sin_art(toks: List[str]) -> List[str]:
        out = list(toks)
        while out and out[0] in ARTICULOS:
            out = out[1:]
        return out

    def tras_verbo_inicial(toks: List[str]) -> List[str]:
        out = sin_art(toks)
        if out and _parece_verbo_finito(out[0]):
            out = out[1:]
            while out and out[0] in ARTICULOS | {'y', 'e', 'o'}:
                out = out[1:]
        return out

    for tit in titulos or []:
        ntit = nz(tit)
        if not ntit:
            continue
        titw = ntit.split()
        if not titw:
            continue
        if t == ntit:
            return True
        if fuzz.ratio(t, ntit) >= 86:
            return True
        if len(tw) >= 3 and fuzz.token_sort_ratio(t, ntit) >= 92:
            return True
        if len(tw) >= 2 and len(titw) >= len(tw) and titw[:len(tw)] == tw:
            return True
        ta, ti = sin_art(tw), sin_art(titw)
        if len(ta) >= 2 and len(ti) >= len(ta) and ti[:len(ta)] == ta:
            return True
        lead = tras_verbo_inicial(titw)
        if len(ta) >= 2 and len(lead) >= len(ta) and lead[:len(ta)] == ta:
            return True
        # mismas palabras de contenido en la apertura del titular
        def contenido(toks: List[str]) -> List[str]:
            return [x for x in toks if x not in CONECT and x not in ARTICULOS]
        tc, tic = contenido(tw), contenido(titw)
        if len(tc) >= 2 and len(tic) >= len(tc) and tic[:len(tc)] == tc:
            return True
    return False


def _es_etiqueta_tematica_canonica(nombre: str) -> bool:
    """Frases temáticas de la tabla de significado / ejemplos buenos: no son un clip del titular."""
    clave = nz(nombre)
    if clave in {nz(x) for x in TEMAS_EJEMPLO_BUENOS}:
        return True
    reglas = globals().get('_REGLAS_TEMA_SEGURO') or ()
    return any(clave == nz(r[3]) for r in reglas)


def _parece_verbo_finito(tok: str) -> bool:
    """Detecta infinitivo, cláusula y 3ª persona presente de verbos conocidos.

    El gate lingüístico ya usa `_es_verbo_clausula`; este helper es más cubriente
    para el fallback (anuncia/presenta/roban) y no se usa para rechazar temas
    buenos del LLM.
    """
    if _es_verbo_clausula(tok) or _parece_infinitivo(tok):
        return True
    t = nz(tok)
    if t in VERBOS1 or t in VERBOS_CLAUSULA or t in {'roba', 'roban', 'hurtan'}:
        return True
    stems = []
    for v in VERBOS1 | VERBOS_CLAUSULA | INFINITIVOS_TEMA:
        vv = nz(v)
        if len(vv) < 4:
            continue
        if vv.endswith(('ar', 'er', 'ir', 'an', 'en')):
            stems.append(vv[:-2])
        else:
            stems.append(vv)
    for st in stems:
        if len(st) < 4:
            continue
        if t == st or t == st + 'a' or t == st + 'e' or t == st + 'o':
            return True
        if t == st + 'an' or t == st + 'en':
            return True
    return False


def problemas_calidad_tema(nombre: str, permitir_vago: bool = False,
                           titulos: Optional[Sequence[str]] = None) -> List[str]:
    """Problemas duros de un TEMA. Lista vacia = frase nominal tematica completa."""
    p: List[str] = []
    nombre = sq(nombre)
    if not nombre:
        return ['vacio']
    clave = nz(nombre)
    if clave in {nz(x) for x in TEMAS_EJEMPLO_MALOS}:
        p.append('ejemplo_malo_lote')
    if re.search(r'[:;,|"\'«»—–¿?¡!]', nombre):
        p.append('caracter_marcador')
    w = nombre.split()
    if len(w) < TEMA_MIN_PAL:
        p.append('corto(%d)' % len(w))
    if len(w) > TEMA_MAX_PAL:
        p.append('largo(%d)' % len(w))
    if not w:
        return p or ['vacio']
    toks = [nz(t) for t in w]
    if any(len(t) <= 1 and t not in {'a', 'y', 'o', 'e', 'u'} for t in toks):
        p.append('token_truncado')
    if any(t in SIGLAS_PROHIBIDAS for t in toks):
        p.append('sigla_suelta')
    if any(len(t) == 2 and t.isalpha() and t not in CONECT and t not in ARTICULOS
           and t not in {'un', 'al', 'de', 'en', 'el', 'la', 'lo', 'su', 'ya'}
           for t in toks):
        p.append('sigla_o_fragmento')
    if toks[-1] in PREP_FIN:
        p.append('termina_preposicion')
    if toks[0] in PREP_INICIO_TEMA:
        # "Para Carnaval 2027", "De cuidado territorial": el tema no puede
        # ser un sintagma preposicional truncado.
        p.append('empieza_preposicion')
    if toks[0] in PRONOMBRES_BASURA or any(t in PRONOMBRES_BASURA for t in toks):
        p.append('pronombre_basura')
    if _es_verbo_clausula(w[0]) or _parece_infinitivo(w[0]):
        p.append('verbo_inicial(%s)' % toks[0])
    if any(_es_verbo_clausula(t) or _parece_infinitivo(t) for t in w[1:]
           if nz(t) not in CONECT):
        # "Estudiar y conseguir empleo", "Cuidado para llevar"
        if any(_parece_infinitivo(t) for t in w[1:]) or any(
                _es_verbo_clausula(t) for t in w[1:]):
            p.append('clausula_verbal')
    for i, t in enumerate(toks[:-1]):
        if t == 'para' and _parece_infinitivo(w[i + 1]):
            p.append('para_infinitivo')
            break
    if 'entre' in toks:
        after = toks[toks.index('entre') + 1:]
        if 'y' not in after and 'e' not in after:
            p.append('entre_incompleto')
    if toks[-1] in OBJETOS_INCOMPLETOS and not any(
            _es_adjetivo_tematico(x) for x in w[1:]):
        p.append('objeto_incompleto(%s)' % toks[-1])
    if toks[0] in EVENTOS_PERSONA:
        p.append('evento_de_persona')
    if any(t in NOMBRES_PERSONA_FREQ for t in toks):
        p.append('nombre_de_persona')
    if toks[0] in EVENTOS_PUNTUALES:
        p.append('evento_puntual')
    if toks[0] in HECHOS_INCIDENTALES:
        p.append('hecho_incidental')
    if not permitir_vago and (clave in ROTULOS_VAGOS_TEMA or clave in ROTULO_GEN
                              or clave in CUBO_PROHIBIDO):
        p.append('rotulo_vago')
    if (not permitir_vago and len(w) == 3 and toks[0] in {'reunion', 'encuentro', 'mesa', 'panel'}
            and toks[1] in {'de', 'del'} and toks[2] in {'expertos', 'especialistas', 'tecnicos'}):
        p.append('rotulo_vago')
    if toks[0] in MARCO and all(t in FILLER or t in CONECT for t in toks[1:]):
        p.append('marco_vacio')
    contenido_i = [i for i, t in enumerate(toks) if t not in CONECT and t not in ARTICULOS]
    contenido = [toks[i] for i in contenido_i]
    nexos = [t for t in toks if t in PREP_SINTAXIS]
    if not contenido:
        p.append('sin_contenido')
    if contenido and all(_es_adjetivo_tematico(w[i]) for i in contenido_i):
        p.append('solo_adjetivos')
    if contenido and _es_adjetivo_tematico(w[0]) and toks[0] not in ADJ_PRENOMINAL:
        p.append('empieza_por_adjetivo')
    if len(contenido) >= 3 and not nexos:
        # Sustantivo + adjetivos ("Movilidad urbana sostenible") es sintaxis,
        # no un mash de keywords: se acepta si lo que sigue al núcleo son
        # adjetivos (el regex no cubre -ano/-ana, -ense ni -eño/-eña).
        resto = [w[i] for i in contenido_i[1:]]
        if not all(_es_adjetivo_tematico(x) or _RE_ADJ_EXTRA.search(x) for x in resto):
            p.append('mash_keywords')
    if len(contenido) == 2 and not nexos:
        w0, w1 = w[contenido_i[0]], w[contenido_i[1]]
        # "Carnaval 2027", "Elecciones 2026": sustantivo + año no es mash.
        es_ano = bool(re.fullmatch(r'(19|20)\d{2}', w1))
        if not es_ano and not (_es_adjetivo_tematico(w1) or nz(w0) in ADJ_PRENOMINAL):
            p.append('mash_keywords')
    if titulos and _tema_copia_o_prefijo_titulo(nombre, titulos):
        if not _es_etiqueta_tematica_canonica(nombre):
            p.append('copia_titular')
    return p


def tema_frase_natural(nombre: str, permitir_vago: bool = False,
                       titulos: Optional[Sequence[str]] = None) -> bool:
    """True si el tema es una frase nominal tematica COMPLETA, apta para Power BI."""
    return not problemas_calidad_tema(nombre, permitir_vago=permitir_vago, titulos=titulos)


def _tema_en_blanco(valor) -> bool:
    """True si el tema está vacío, None, solo espacios o es un nulo textual."""
    s = sq(valor)
    if not s:
        return True
    return nz(s) in {'nan', 'none', 'null', 'na'}


def _tema_util(valor) -> bool:
    """Tema usable en una fila no duplicada (no blanco y no el marcador '-')."""
    return not _tema_en_blanco(valor) and sq(valor) != '-'


def cubo_valido(nombre, tax, permitir_nuevos=True):
    nombre = sq(nombre)
    if not nombre:
        return None
    n = nz(nombre)
    if n in CUBO_PROHIBIDO or n in ROTULO_GEN:
        return None
    en_lista = next((t for t in tax['temas'] if nz(t) == n), None)
    if en_lista:
        return en_lista
    if not permitir_nuevos or not (TEMA_MIN_PAL <= len(nombre.split()) <= TEMA_MAX_PAL):
        return None
    toks = [t for t in n.split() if t]
    if any(t in META_CUBO for t in toks):
        return None
    if not tema_frase_natural(nombre):
        return None
    contenido = [t for t in toks if t not in CONECT and t not in FILLER and t not in MARCO and len(t) > 3]
    return nombre if contenido else None


def _cubo_mas_cercano(sub_tema: str, titulo: str, tax: dict) -> Optional[str]:
    """Devuelve un cubo relacionado sin fabricar etiquetas."""
    from rapidfuzz import fuzz
    objetivo = {raiz(t) for t in words(sub_tema)
                if t not in CONECT and t not in FILLER and t not in MARCO and len(t) > 3}
    candidatos = []
    for nombre in tax.get('temas', []):
        if nz(nombre) in CUBO_PROHIBIDO or not _tema_distinto_de_subtema(nombre, sub_tema):
            continue
        claves = {raiz(t) for t in words(nombre)
                  if t not in CONECT and t not in FILLER and t not in MARCO and len(t) > 3}
        comun = objetivo & claves
        if comun:
            candidatos.append((len(comun), len(claves), nombre))
    if candidatos:
        return max(candidatos, key=lambda x: (x[0], x[1]))[2]

    # Respaldo semántico sobre la taxonomía existente. No crea un tema nuevo
    # ni usa una etiqueta default: elige el cubo más relacionado con toda la
    # evidencia disponible.
    evidencia = nz('%s %s %s' % (sub_tema, titulo, ' '.join(tax.get('evidencia', []) or [])))
    posibles = []
    for nombre in tax.get('temas', []):
        if nz(nombre) in CUBO_PROHIBIDO or not _tema_distinto_de_subtema(nombre, sub_tema):
            continue
        score = fuzz.token_set_ratio(nz(nombre), evidencia)
        posibles.append((score, nombre))
    return max(posibles, key=lambda x: x[0])[1] if posibles else None



# ============================================================================
# 5. Prompts (rubrica ordenada + ejemplos + contrato JSON)
# ============================================================================
def prompt_sistema(cfg: dict) -> str:
    crit = cfg.get('criterio') or list(CRITERIOS_TONO)[0]
    # Criterio personalizado del perfil de cliente: gana sobre el catálogo.
    regla = (cfg.get('criterio_texto') or '').strip() or CRITERIOS_TONO.get(crit) \
        or list(CRITERIOS_TONO.values())[0]
    lineas = [
        'Eres analista senior de monitoreo de medios en Colombia. Etiquetas cada GRUPO de notas',
        '(una nota publicada por varios medios = un grupo) y devuelves JSON.',
        '',
        'MARCA / ENTIDAD OBJETIVO: %s' % (cfg.get('brand') or 'la entidad'),
        'VOCERO(S): %s' % (', '.join(cfg.get('voceros') or []) or 'no definido'),
        'ALIAS Y FORMAS DE NOMBRARLA: %s' % (', '.join(cfg.get('aliases') or []) or 'ninguno'),
        '',
        'REGLA DE TONO',
        regla,
        '',
        'REGLA DE SUB-TEMA',
        REGLAS_SUBTEMA,
        '',
        'EJEMPLOS YA ETIQUETADOS (imita el criterio, la brevedad y las mayusculas)',
    ]
    ejemplos = list(EJEMPLOS) + EJEMPLOS_TEMA
    if crit.startswith('Favorabilidad'):
        ejemplos += EJEMPLOS_SECTOR
    for e in ejemplos:
        lineas.append('  TITULAR: %s' % e['titulo'])
        lineas.append('  ->  sub_tema: "%s"  |  tono: %s' % (e['sub_tema'], e['tono']))
    lineas += [
        '',
        'SALIDA: solo JSON, sin markdown y sin explicaciones:',
        '{"resultados":[{"id":<numero de grupo>,"sub_tema":"<3 a 5 palabras>",'
        '"tono":"Positivo|Neutro|Negativo"}]}',
        'Un objeto por cada grupo recibido, con su id exacto.',
    ]
    return '\n'.join(lineas)


def prompt_lote(grupos_lote: Sequence[dict], candidatos: Sequence[str]) -> str:
    bloques = []
    for g in grupos_lote:
        b = ['GRUPO id=%d (%d menciones)' % (g['grupo'], g['n']),
             'TITULAR: %s' % sq(g['titulo'])[:220]]
        if g.get('titulos_alt'):
            b.append('OTROS TITULARES DEL MISMO GRUPO: %s'
                     % ' // '.join(sq(t)[:120] for t in g['titulos_alt']))
        b.append('CONTEXTO LITERAL DE LA MARCA (fuente principal para el tono): %s'
                 % sq(g.get('contexto') or g.get('texto', ''))[:6000])
        bloques.append('\n'.join(b))
    msg = '\n\n'.join(bloques)
    msg += '\n\nRecuerda: el sub_tema de cada grupo debe tener entre 3 y 5 palabras, y solo JSON.'
    if candidatos:
        msg += ('\n\nCANDIDATOS (sub-temas ya usados; reutiliza el texto exacto si es el mismo hecho):\n'
                + '\n'.join('- %s' % c for c in list(candidatos)[-120:]))
    return msg


def prompt_reparacion(fallos: Sequence[dict]) -> str:
    detalle = []
    for f in fallos:
        detalle.append('GRUPO id=%d\nTITULAR: %s\nTEXTO: %s\nSUB-TEMA ACTUAL: "%s"\nPROBLEMAS: %s'
                       % (f['grupo'], sq(f['titulo'])[:180], sq(f['texto'])[:450],
                          f['sub_tema'], '; '.join(f['problemas'])))
    return ('Corrige SOLO estos sub-temas. Devuelve el mismo tono salvo que el tono no sea valido.\n'
            'Un sub-tema valido tiene 3 a 5 palabras (maximo 7) en frase nominal, no empieza con verbo\n'
            'conjugado, no termina en preposicion y no lleva marcadores ni rotulos vacios. Si el problema\n'
            'dice largo(N), recorta a 5 palabras sin perder el hecho. Si dice copia_titular, reformula\n'
            'con tus palabras e incluye el actor o lugar distintivo. Si dice subtema_vago, agrega el\n'
            'objeto o asunto concreto del que se trata.\n\n'
            + '\n\n'.join(detalle)
            + '\n\nResponde UNICAMENTE con {"resultados":[{"id":<grupo>,"sub_tema":"...","tono":"..."}]}')


def prompt_cubos(pendientes: Sequence[dict], tax: dict, permitir_nuevos: bool) -> str:
    lista = '\n'.join('- %s' % t for t in tax['temas'] if nz(t) not in CUBO_PROHIBIDO)
    extra = ('Si ningun cubo sirve, propón uno NUEVO en 2 a 5 palabras que describa el asunto concreto\n'
             '(por ejemplo "Tramite de pasaportes"). No se acepta un cubo generico.\n'
             if permitir_nuevos else 'No propongas cubos nuevos: elige siempre uno de la lista.\n')
    bloques = ['GRUPO id=%d\nSUB-TEMA: %s\nTITULAR: %s\nCONTEXTO: %s' %
               (p['grupo'], p['sub_tema'], sq(p['titulo'])[:180],
                sq(p.get('contexto') or '')[:5000])
               for p in pendientes]
    return ('Clasificas notas de prensa en cubos tematicos cerrados.\nCubos disponibles:\n%s\n\n%s'
            'Responde UNICAMENTE con {"resultados":[{"id":<grupo>,"cubo":"<nombre del cubo>"}]}\n\n%s'
            % (lista, extra, '\n\n'.join(bloques)))


# ============================================================================
# 6. Capa LLM
# ============================================================================
def _json_loose(txt):
    txt = re.sub(r'^```(?:json)?|```$', '', str(txt or '').strip(), flags=re.M).strip()
    try:
        return json.loads(txt)
    except Exception:
        pass
    m = re.search(r'\{.*\}', txt, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _tono_con_jev(cfg: dict, grupo: dict) -> Optional[str]:
    """Clasifica solo el tono dirigido a la marca, alias o vocero con Jev."""
    api_key = (cfg.get('typesafe_api_key') or '').strip()
    if not api_key:
        return None
    contexto = sq(grupo.get('contexto') or grupo.get('texto') or '')[:9000]
    payload = {
        'model': cfg.get('typesafe_model') or 'jev-latest',
        'state': {
            'marca_objetivo': cfg.get('brand') or 'la entidad objetivo',
            'alias': list(cfg.get('aliases') or []),
            'voceros': list(cfg.get('voceros') or []),
            'titular': sq(grupo.get('titulo') or '')[:500],
            'contexto_literal': contexto,
        },
        'questions': {
            'tono': {
                'type': 'choice',
                'instructions': (
                    'Clasifica exclusivamente el tono expresado sobre la marca, '
                    'sus alias o sus voceros. No clasifiques el tono general de la '
                    'noticia ni un problema que afecte a terceros.'
                ),
                'criteria': {
                    'Positivo': 'La marca o vocero realiza, recibe o expresa una acción favorable, beneficio, apoyo, alianza, reconocimiento o logro.',
                    'Negativo': 'Existe crítica, denuncia, reclamo, sanción, acusación, falla o señalamiento dirigido explícitamente a la marca o vocero.',
                    'Neutro': 'La marca o vocero aparece de forma informativa, incidental o sin valoración dirigida.',
                },
            }
        },
    }
    try:
        response = requests.post(
            cfg.get('typesafe_url') or JEV_URL_DEFECTO,
            headers={'Authorization': 'Bearer %s' % api_key, 'Content-Type': 'application/json'},
            json=payload,
            timeout=int(cfg.get('typesafe_timeout', 60)),
        )
        if response.status_code != 200:
            raise RuntimeError('HTTP %s: %s' % (response.status_code, response.text[:250]))
        value = str(response.json().get('answers', {}).get('tono', {}).get('choice') or '').strip().capitalize()
        return value if value in TONOS else None
    except Exception as exc:
        _ULTIMO_RESUMEN.setdefault('errores_jev', []).append(str(exc)[:200])
        return None


def _param_limite(modelo: str) -> str:
    """Nombre del parametro de limite de tokens segun la familia del modelo.

    Los modelos nuevos (GPT-5/6, serie o) rechazan 'max_tokens' con HTTP 400 y
    exigen 'max_completion_tokens'; los anteriores usan 'max_tokens'.
    """
    m = str(modelo or '').strip().lower()
    if re.match(r'^(gpt-[5-9]|o[0-9])', m):
        return 'max_completion_tokens'
    return 'max_tokens'


_SESION_HTTP = None


def _http_post(url, headers, payload, timeout):
    """POST con sesion reutilizada: evita renegociar TLS en cada llamada.

    La sesion (y su pool de conexiones) es segura para uso concurrente desde
    los workers del ThreadPoolExecutor.
    """
    global _SESION_HTTP
    if _SESION_HTTP is None:
        _SESION_HTTP = requests.Session()
    return _SESION_HTTP.post(url, headers=headers, json=payload, timeout=timeout)


def llamar_llm(cfg: dict, mensajes: List[dict], json_mode: bool = True,
               max_tokens: int = 4000, temperatura: float = 0.0, intentos: int = 3,
               uso: Optional[dict] = None) -> str:
    url = (cfg.get('base_url') or BASE_URL_DEFECTO).rstrip('/') + '/chat/completions'
    modelo = cfg.get('model') or MODELO_DEFECTO
    clave_limite = _param_limite(modelo)
    payload = {'model': modelo, 'messages': mensajes,
               'temperature': temperatura, clave_limite: max_tokens}
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    cab = {'Authorization': 'Bearer %s' % cfg.get('api_key', ''), 'Content-Type': 'application/json'}
    ultimo = ''
    limite_ajustado = False
    temp_ajustada = False
    for k in range(intentos):
        try:
            r = _http_post(url, cab, payload, cfg.get('timeout', 120))
            if r.status_code in (429, 500, 502, 503):
                ultimo = 'HTTP %s' % r.status_code
                time.sleep(2 + 3 * k)
                continue
            if r.status_code == 400:
                cuerpo = (r.text or '').lower()
                if (not limite_ajustado and 'max_completion_tokens' in cuerpo
                        and clave_limite == 'max_tokens'):
                    # El modelo rechaza max_tokens: autocorregir y reintentar.
                    payload['max_completion_tokens'] = payload.pop('max_tokens')
                    clave_limite = 'max_completion_tokens'
                    limite_ajustado = True
                    ultimo = 'HTTP 400 (max_tokens no soportado; reintentando con max_completion_tokens)'
                    continue
                if (not temp_ajustada and 'temperature' in cuerpo
                        and 'temperature' in payload):
                    # El modelo no acepta temperature distinta del default.
                    payload.pop('temperature', None)
                    temp_ajustada = True
                    ultimo = 'HTTP 400 (temperature no soportada; reintentando sin temperature)'
                    continue
            if r.status_code != 200:
                raise RuntimeError('HTTP %s: %s' % (r.status_code, r.text[:300]))
            data = r.json()
            _sumar_uso(uso, (data.get('usage') or {}).get('prompt_tokens'),
                       (data.get('usage') or {}).get('completion_tokens'))
            return data['choices'][0]['message']['content']
        except requests.RequestException as e:
            ultimo = str(e)[:200]
            time.sleep(2 + 3 * k)
    raise RuntimeError('Fallo la llamada al modelo: %s' % ultimo)


def _normaliza_label(d: dict, ids_lote: Sequence[int]) -> Dict[int, dict]:
    salida = {}
    for r in (d or {}).get('resultados', []) or []:
        try:
            gid = int(r.get('id'))
        except Exception:
            continue
        if gid in ids_lote:
            tono = sq(r.get('tono')).capitalize()
            salida[gid] = {'sub_tema': sq(r.get('sub_tema')),
                           'tono': tono if tono in TONOS else 'Neutro'}
    return salida


def _voto_mayoria(por_grupo: List[Dict[int, dict]], ids_lote: Sequence[int]) -> Dict[int, dict]:
    """Combina las pasadas de votacion: gana el tono mas votado (empate -> Neutro) y el sub-tema
    mas repetido (empate -> el mas corto). Reduce el ruido de los modelos pequenos en los casos
    limite: si una nota sale Negativa en una pasada y Neutra en otra, queda Neutra."""
    salida = {}
    for gid in ids_lote:
        tonos = [v[gid]['tono'] for v in por_grupo if gid in v and v[gid].get('tono')]
        subs = [v[gid]['sub_tema'] for v in por_grupo if gid in v and v[gid].get('sub_tema')]
        if not tonos and not subs:
            continue
        c = Counter(tonos)
        top = c.most_common()
        if not top:
            tono = 'Neutro'
        elif len(top) > 1 and top[0][1] == top[1][1]:
            tono = 'Neutro' if 'Neutro' in [top[0][0], top[1][0]] else top[0][0]
        else:
            tono = top[0][0]
        cs = Counter(nz(s) for s in subs)
        if not cs:
            sub = ''
        else:
            maxrep = max(cs.values())
            candidatos = [s for s in subs if cs[nz(s)] == maxrep]
            # En empate gana el MAS LARGO (más específico): es el mismo grupo
            # y el mismo hecho, así que el largo detalla mejor. El empate-corto
            # amplificaba un voto cruzado del modelo (caso Unisimón: un voto
            # ajeno y corto como «Investigación sobre Carnaval 2027» le ganaba
            # a «Creación de la Universidad de Atalaya»). Criterio v4.15.
            sub = max(candidatos, key=len)
        salida[gid] = {'sub_tema': sub, 'tono': tono}
    return salida


def etiquetar_grupos(cfg: dict, grupos: List[dict], progress: Optional[Callable] = None,
                     tam_lote: int = TAM_LOTE_DEFECTO, workers: int = WORKERS_DEFECTO,
                     max_reparaciones: int = 2, votos: int = 2,
                     uso: Optional[dict] = None) -> Dict[int, dict]:
    """Etiqueta todos los grupos: lotes en paralelo -> votacion -> validacion -> reparacion.

    Con `votos=2` cada lote se etiqueta dos veces y se toma la mayoria: los casos limite dejan de
    bailar entre corridas (un empate en el tono cae a Neutro, que es la regla de prudencia).
    La canonizacion de sub-temas se hace despues, de forma determinista, porque los lotes corren
    en paralelo y no ven las etiquetas de los demas mientras trabajan.
    """
    if not grupos:
        return {}
    votos = max(1, int(votos))
    lotes = [grupos[i:i + tam_lote] for i in range(0, len(grupos), tam_lote)]
    etiquetas: Dict[int, dict] = {}
    fallidos: List[int] = []
    hechos = [0]

    def trabajo(lote):
        ids = [g['grupo'] for g in lote]
        sys_msg = [{'role': 'system', 'content': prompt_sistema(cfg)},
                   {'role': 'user', 'content': prompt_lote(lote, [])}]
        for intento in range(2):
            try:
                txt = llamar_llm(cfg, sys_msg, uso=uso)
                labels = _normaliza_label(_json_loose(txt), ids)
                if labels:
                    return lote, labels
            except Exception:
                if intento == 1:
                    raise
                time.sleep(2)
        return lote, {}

    resultados = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futuros = {}
        for lote in lotes:
            for _v in range(votos):
                futuros[ex.submit(trabajo, lote)] = lote
        for fut in as_completed(futuros):
            lote = futuros[fut]
            try:
                resultados.append(fut.result())
            except Exception as e:
                resultados.append((lote, {'__error__': str(e)[:200]}))
            hechos[0] += len(lote)
            if progress and hechos[0] % max(1, len(lotes)) == 0:
                avance = min(1.0, hechos[0] / max(1, len(grupos) * votos))
                progress(min(92, 70 + int(18 * avance)),
                         'Analizando con IA… %d/%d grupos%s'
                         % (min(hechos[0], len(grupos) * votos), len(grupos) * votos,
                            ' (doble verificación)' if votos > 1 else ''))

    con_error = []
    if votos > 1:
        por_lote: Dict[int, List[Dict[int, dict]]] = defaultdict(list)
        for lote, labels in resultados:
            if '__error__' in labels:
                con_error.append(labels['__error__'])
                continue
            por_lote[lote[0]['grupo']].append(labels)
        for lote in lotes:
            comb = _voto_mayoria(por_lote.get(lote[0]['grupo'], []), [g['grupo'] for g in lote])
            for g in lote:
                etiquetas[g['grupo']] = comb.get(g['grupo'], {'sub_tema': '', 'tono': ''})
    else:
        for lote, labels in resultados:
            if '__error__' in labels:
                con_error.append(labels['__error__'])
                labels = {}
            for g in lote:
                etiquetas[g['grupo']] = labels.get(g['grupo'], {'sub_tema': '', 'tono': ''})

    # ---- reparacion secuencial de etiquetas invalidas (segunda vuelta al modelo) ----
    por_grupo = {g['grupo']: g for g in grupos}
    for ronda in range(max_reparaciones):
        fallos = []
        for g in grupos:
            e = etiquetas[g['grupo']]
            pr = validar(e['sub_tema'], e['tono'],
                         [g['titulo']] + g.get('titulos_alt', []) + [g['texto']])
            duros = [x for x in pr if not x.startswith('revisar_anclaje')]
            if duros or not e['sub_tema']:
                fallos.append({'grupo': g['grupo'], 'titulo': g['titulo'], 'texto': g['texto'],
                               'sub_tema': e['sub_tema'], 'problemas': duros or ['vacio']})
        # v4.21: reparacion determinista primero para los problemas mecanicos
        # (titular copiado como subtema, cita o pregunta como etiqueta). Lo
        # que se repara aqui no gasta llamada LLM; el resto sigue a la vuelta
        # de reparacion con el modelo.
        pendientes = []
        for f in fallos:
            base = [p.split('(')[0] for p in f['problemas']]
            if base and all(b in _PROBLEMAS_MECANICOS for b in base):
                nuevo = reparar_subtema_determinista(f['sub_tema'], f['titulo'])
                if nuevo:
                    etiquetas[f['grupo']]['sub_tema'] = nuevo
                    _ULTIMO_RESUMEN['subtemas_reparados_determinista'] = \
                        _ULTIMO_RESUMEN.get('subtemas_reparados_determinista', 0) + 1
                    continue
            pendientes.append(f)
        fallos = pendientes
        if not fallos:
            break
        if progress:
            progress(min(93, 92), 'Reparando %d etiquetas…' % len(fallos))
        def _reparar(trozo):
            try:
                txt = llamar_llm(cfg, [{'role': 'system', 'content': prompt_sistema(cfg)},
                                       {'role': 'user', 'content': prompt_reparacion(trozo)}],
                                 uso=uso)
                return _normaliza_label(_json_loose(txt), [f['grupo'] for f in trozo])
            except Exception:
                return {}

        # Los trozos son independientes (cada grupo aparece en uno solo): se
        # reparan en paralelo con los mismos workers. Mismo resultado, menos
        # tiempo cuando hay muchas etiquetas por corregir.
        trozos = [fallos[i:i + 12] for i in range(0, len(fallos), 12)]
        if len(trozos) > 1:
            with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex_rep:
                correcciones = list(ex_rep.map(_reparar, trozos))
        else:
            correcciones = [_reparar(t) for t in trozos]
        for corr in correcciones:
            for gid, v in corr.items():
                if v.get('sub_tema'):
                    etiquetas[gid] = v

    # ---- fallback determinista para lo que quedo vacio: nunca dejamos una fila sin etiqueta ----
    # v4.21: el rótulo se deriva del titular con _subtema_desde_titulo (rótulo
    # honesto y corto) en vez del recorte crudo de 5 palabras, que dejaba
    # medio titular como subtema.
    for g in grupos:
        e = etiquetas[g['grupo']]
        if not e.get('sub_tema'):
            e['sub_tema'] = _subtema_desde_titulo(g.get('titulo')) or 'Hecho informativo'
            fallidos.append(g['grupo'])
        if not e.get('tono'):
            e['tono'] = 'Neutro'

    # Jev decide el tono aspectual sobre la entidad; el LLM existente conserva
    # la generación de subtema y el resto del flujo.
    if cfg.get('typesafe_api_key'):
        for g in grupos:
            tono_jev = _tono_con_jev(cfg, g)
            if tono_jev:
                etiquetas[g['grupo']]['tono'] = tono_jev
        _ULTIMO_RESUMEN['motor_tono'] = 'jev'
    else:
        _ULTIMO_RESUMEN['motor_tono'] = 'modelo_configurado'

    _ULTIMO_RESUMEN['grupos'] = len(grupos)
    _ULTIMO_RESUMEN['errores_api'] = con_error[:5]
    _ULTIMO_RESUMEN['grupos_con_fallback'] = fallidos
    return etiquetas


def _contenido_discriminante(s: str) -> set:
    """Tokens de contenido (sin nexos, relleno ni geografía genérica).

    v4.20: los tokens se normalizan por clase de evento («hospitalización»,
    «internación», «ingreso», «uci» → 'salud'; «lanzamiento»/«presentación» →
    'lanzamiento'...) para que las paráfrasis del mismo hecho se reconozcan
    como tal. Los nombres propios siguen discriminando: dos pacientes o dos
    ciudades distintas no se unen aunque compartan clase.
    """
    toks = [w for w in words(s)
            if w not in CONECT and w not in FILLER and w not in MARCO
            and len(w) >= 3 and not _es_geografia(w)]
    r = [raiz(w) for w in toks]
    sal = set()
    usados = set()
    for k in range(len(r) - 1):
        cid = _BIGRAM_CLASE.get((r[k], r[k + 1]))
        if cid:
            sal.add(cid)
            usados.add(k)
            usados.add(k + 1)
    for k, t in enumerate(r):
        if k in usados:
            continue
        sal.add(_CLASE_DE.get(t, _EQUIV_STEMS.get(t, t)))
    return sal


# Familias morfológicas que el stemmer simple no une y sí comparten asunto.
# Es el mecanismo probado (v4.4): se canoniza ANTES de comparar, y el
# clustering usa igualdad exacta. La afinidad por prefijo generaba
# uniones sorpresa ('empleo'~'desempleo', 'medica'~'medicina'...).
_EQUIV_STEMS = {
    'juvenil': 'joven', 'juventud': 'joven', 'jovenes': 'joven',
    'criminalidad': 'crimen', 'criminologia': 'crimen',
    'suicidologia': 'suicidio', 'suicidiologia': 'suicidio',
    'educativo': 'educacion', 'educativa': 'educacion',
}
# Palabras de evento tan genéricas que no prueban asunto común
# ("congreso" aparece en el de criminología, el de psicología y el de suicidología).
GENERICO_NO_UNEN = {
    'congreso', 'internacional', 'nacional', 'evento', 'encuentro', 'jornada',
    'seminario', 'simposio', 'conferencia', 'feria', 'festival', 'reunion', 'cumbre',
}


# ============================================================================
# v4.20: clases de evento (vocabulario de dominio, no de cliente).
#
# Caso real (Fundación Santa Fe, dossier 2026-09-24): la atención a un mismo
# paciente salió con 10+ subtemas («Estado de salud de Yamid Amat»,
# «Hospitalización de Yamid Amat», «Yamid Amat en UCI», «Ingreso a UCI de
# Yamid»...) porque ninguna comparación de cadenas une «hospitalización» con
# «estado de salud». Las clases normalizan paráfrasis del MISMO evento antes
# de comparar: «hospitalización», «internación», «ingreso a UCI» → 'salud'.
# Los nombres propios siguen discriminando: dos pacientes distintos no se
# unen aunque compartan clase. Para clientes de salud esto además garantiza
# «ver los pacientes tratados»: el hecho se agrupa por el paciente.
# ============================================================================
# Clases de evento asistencial separadas por etapa (v4.20): el ingreso a
# clínica, el nacimiento/parto y la cirugía son hechos distintos aunque
# compartan paciente («Ingreso de Lina Tejeiro a clínica» ≠ «Nacimiento de
# Gael en Santa Fe»). «salud» queda como clase genérica para el estado
# clínico general (pronóstico, complicación, tratamiento...).
_CLASES_EVENTO = {
    'salud': (
        'hospitalizacion hospitalizado hospitalizada hospitalizan '
        'internacion internado internada '
        'ingreso ingresado ingresada ingresa ingresan '
        'uci cuidado cuidados intensivo intensivos '
        'salud medico medica '
        'pronostico '
        'complicacion complicaciones '
        'tratamiento tratamientos '
        'consulta diagnostico recuperacion emergencia urgencia urgencias'
    ).split(),
    'nacimiento': (
        'nacimiento nacio nacen parto alumbramiento cesarea'
    ).split(),
    'cirugia': (
        'cirugia operacion operado operada operan'
    ).split(),
    'lanzamiento': (
        'lanzamiento lanzamientos presentacion debut estreno estrenos '
        'revelacion unveiling'
    ).split(),
    'reunion': (
        'reunion reuniones encuentro conversatorio mesa foro foros cumbre '
        'dialogo junta asamblea'
    ).split(),
    'reconocimiento': (
        'reconocimiento premio premios galardon distincion homenaje '
        'condecoracion'
    ).split(),
    'ranking': 'ranking listado listados clasificacion escalafon'.split(),
    'inauguracion': 'inauguracion apertura reapertura inaugura inauguran'.split(),
    'firma': 'firma firmas alianza alianzas convenio acuerdo pacto firman'.split(),
}
# Mapa stem → clase (se normaliza con raiz() igual que el resto de tokens).
_CLASE_DE = {}
for _cid, _pals in _CLASES_EVENTO.items():
    for _pal in _pals:
        _CLASE_DE.setdefault(raiz(_pal), _cid)
del _cid, _pals, _pal
# Bigramas que solo significan evento de salud juntos («reporte» o «parte»
# solos son genéricos: «reporte policial» no es un evento de salud).
_BIGRAM_CLASE = {
    ('reporte', 'medico'): 'salud',
    ('parte', 'medico'): 'salud',
    ('estado', 'salud'): 'salud',
    ('cuidado', 'intensivo'): 'salud',
    ('trabajo', 'parto'): 'salud',
    ('labor', 'parto'): 'salud',
    ('dar', 'luz'): 'salud',
    ('atencion', 'medica'): 'salud',
}
# Condiciones/enfermedades: NO son eventos. Veto para no unir dos atenciones
# distintas del mismo paciente («hospitalizado por fiebre» vs «nacimiento»).
_CONDICION_NO_EVENTO = {
    raiz(p) for p in (
        'epoc cancer diabetes fiebre covid coronavirus infarto tumor tumores '
        'neumonia asma hipertension alzheimer parkinson artritis lupus anemia '
        'bronquitis hepatitis rubeola varicela sarampion'
    ).split()
}


# Modificadores tan genéricos que no prueban asunto común por sí solos
# ("prevención" aparece en suicidio y en delitos tecnológicos; "desafío" en
# criminología y en juventud; "impacto"/"debate" igual). Se excluyen del
# núcleo que une familias; el asunto lo ponen los stems temáticos.
MODIFICADOR_GENERICO_NO_UNE = {
    'prevencion', 'prevenir', 'desafio', 'impacto', 'debate', 'reto',
    'perspectiva', 'analisis', 'balance', 'panorama', 'frente',
}


# Atributos demográficos: describen A QUIÉN, no el asunto. No bastan para unir
# familias en la regla combinada (1 núcleo + 1 evidencia): "jóvenes" une
# desempleo juvenil con suicidio juvenil, que son asuntos distintos.
# Siguen contando en las reglas clásicas de 2+ pares.
ATRIBUTO_DEMOGRAFICO = {
    'joven', 'mujer', 'hombre', 'nino', 'nina', 'adulto', 'adulta',
    'adolescente', 'infantil', 'ninez', 'juventud',
}


def _solapamiento_contexto(a: str, b: str, min_gramas: int = 6) -> float:
    """Solapamiento de 5-gramas entre dos contextos (0.0 si no hay base).

    Mide cuántos pasajes literales comparten: 1.0 = pasajes casi idénticos.
    """
    ga, gb = grams(words(nz(a or '')), 5), grams(words(nz(b or '')), 5)
    if len(ga) < min_gramas or len(gb) < min_gramas:
        return 0.0
    inter = len(ga & gb)
    if inter < min_gramas:
        return 0.0
    return inter / min(len(ga), len(gb))


def _contextos_mismo_hecho(a: str, b: str, umbral: float = 0.55,
                           min_gramas: int = 6) -> bool:
    """True si dos textos de contexto comparten pasajes del mismo hecho.

    Comparación por solapamiento de 5-gramas (orden-sensible): confirma que es
    el mismo hecho contado por dos notas, no solo palabras clave en común.
    """
    return _solapamiento_contexto(a, b, min_gramas) >= umbral


# Solapamiento de contexto que autoriza a un grupo a adoptar el subtema de
# otro aunque la redacción difiera: pasajes casi idénticos (no boilerplate
# parcial). Calibrado: mismo hecho real ≈0.83, boilerplate compartido ≈0.62.
SOLAPAMIENTO_CTX_FUERTE = 0.75


def _puede_adoptar_canon(gi: dict, si: str, gj: dict, sj: str,
                         umbral: int = 80) -> bool:
    """True si el grupo gi puede adoptar el subtema de gj (mismo hecho).

    La unión por señales débiles no basta para renombrar: se exige evidencia
    fuerte y directa entre la pareja — subtemas ya similares, titulares casi
    duplicados, o pasajes de contexto casi idénticos. Caso real Unisimón:
    «La Universidad de Atalaya» no adopta «Investigación sobre Carnaval 2027».
    """
    from rapidfuzz import fuzz
    if _subtemas_mismo_hecho(si, sj):
        return True
    ti, tj = nz(gi.get('titulo')), nz(gj.get('titulo'))
    if ti and tj and fuzz.token_set_ratio(ti, tj) >= umbral:
        wi = {w for w in words(ti) if w not in GENERIC_TITULO}
        wj = {w for w in words(tj) if w not in GENERIC_TITULO}
        if len(wi & wj) >= 2:
            return True
    if _solapamiento_contexto(gi.get('contexto_marca') or '',
                              gj.get('contexto_marca') or '') >= SOLAPAMIENTO_CTX_FUERTE:
        return True
    return False


def _subtemas_mismo_hecho(a: str, b: str, umbral: float = 0.82) -> bool:
    """True si dos subtemas describen el mismo hecho (no solo el mismo asunto)."""
    from rapidfuzz import fuzz
    na, nb = nz(a), nz(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ca, cb = _contenido_discriminante(a), _contenido_discriminante(b)
    if not ca or not cb:
        return fuzz.token_sort_ratio(na, nb) >= umbral * 100
    inter = ca & cb
    union = ca | cb
    jac = len(inter) / max(1, len(union))
    diferencia = ca ^ cb
    contenida = ca <= cb or cb <= ca
    misma_bolsa = len(diferencia) <= 2 and len(inter) >= 2
    if fuzz.token_sort_ratio(na, nb) >= umbral * 100:
        return True
    if contenida and misma_bolsa:
        return True
    if jac >= 0.55 and len(inter) >= 2:
        return True
    if fuzz.token_set_ratio(na, nb) >= 88 and len(inter) >= 2:
        return True
    return False


def canonizar_subtemas(etiquetas: Dict[int, dict], umbral: float = 0.82) -> int:
    """Unifica variantes inequívocas y conserva un texto canónico.

    No basta con que dos cadenas se parezcan: ``Obras en Sincelejo`` y
    ``Obras en Montería`` no son el mismo hecho. Se permite similitud alta o
    contención de tokens cuando la diferencia es pequeña y no introduce una
    ciudad/entidad distinta. El representante es el más frecuente y, en empate,
    el más corto.
    """
    conteo = Counter(nz(e.get('sub_tema')) for e in etiquetas.values() if e.get('sub_tema'))
    if len(conteo) <= 1:
        return 0
    representantes = []
    asignacion = {}
    for texto, _frecuencia in conteo.most_common():
        destino = None
        for rep in representantes:
            if _subtemas_mismo_hecho(texto, rep, umbral=umbral):
                destino = rep
                break
        if destino is None:
            representantes.append(texto)
            destino = texto
        asignacion[texto] = destino

    originales = {nz(e.get('sub_tema')): e.get('sub_tema') for e in etiquetas.values()}
    cambios = 0
    for e in etiquetas.values():
        clave = nz(e.get('sub_tema'))
        destino = asignacion.get(clave, clave)
        if destino != clave:
            e['sub_tema'] = originales.get(destino, destino).strip()
            cambios += 1
    return cambios


# ---------------------------------------------------------------------------
# v4.18: reparación de etiquetas ajenas (cruce del modelo entre grupos).
# El etiquetador trabaja por lotes y a veces le pega a un grupo el subtema de
# OTRO grupo del lote («La Universidad de Atalaya» -> «Investigación sobre
# Carnaval 2027»; «La IA y la reconversión laboral» -> «Medicina multimodal y
# personalizada»). Ninguna unificación determinista lo produce: hay que
# detectarlo por coherencia. Criterio: el subtema comparte >=2 palabras
# distintivas (no genéricas) con el TITULAR de otro grupo y <=1 con el
# contenido propio (título+texto+contexto). Ante la duda no se toca nada.
# El grupo afectado vuelve a un rótulo honesto derivado de su propio titular.
# v4.21: interjecciones con las que arrancan muchos titulares y que nunca deben
# quedar en un subtema («¡Atención! …», «Última hora: …»).
_INTERJECCION_TITULAR = re.compile(
    r'^(atenci[oó]n|[úu]ltima hora|urgente|en vivo|lo [úu]ltimo|'
    r'de [úu]ltimo minuto|[úu]ltimo minuto|breaking|exclusivo|exclusiva|'
    r'atentos?)\s*[:!¡,.\-–—]?\s*', re.I)
# Verbo de habla/información en 3a persona del singular: tras recortar un
# titular largo, lo que sigue al verbo es el asunto («…revela detalles del
# nacimiento…» → «detalles del nacimiento…»).
_VERBO_INFO_SING = {
    'revela', 'devela', 'cuenta', 'detalla', 'narra', 'explica', 'anuncia',
    'presenta', 'confirma', 'asegura', 'dice', 'afirma', 'relata', 'describe',
    'muestra', 'entrega', 'revelan', 'cuentan', 'detallan',
}
_CITA_ENVOLVENTE = re.compile(r'^\s*["\'«»“”].*["\'«»“”]\s*$')
_PREP_INICIAL = {
    'para', 'por', 'de', 'del', 'en', 'y', 'e', 'o', 'con', 'sin', 'sobre',
    'entre', 'hacia', 'desde', 'hasta', 'que',
}


def _empieza_con_verbo(pals) -> bool:
    """True si la primera palabra es verbo conjugado (misma regla que validar)."""
    if not pals:
        return False
    prim = nz(pals[0])
    nexo2 = len(pals) > 1 and nz(pals[1]) in PREP_FIN
    return bool(not nexo2 and (prim in VERBOS1 or
                               (len(prim) > 4 and RE_VERBO.search(prim))))


def _es_frase_destacada(ultimo: str, primero: str) -> bool:
    """True si el segmento tras ':' parece frase destacada y el previo, el hecho.

    v4.21: «Lina Tejeiro revela detalles del nacimiento de su hijo Gael:
    Septiembre era el momento perfecto» — el destacado es corto y sin clase
    de evento; el hecho es sustancialmente más largo y sí tiene clase.
    Sin esta regla, dos titulares casi idénticos (con y sin comillas) tomaban
    segmentos distintos y quedaban con subtema inconsistente.
    """
    wu, wp = ultimo.split(), primero.split()
    return (len(wu) <= 6 and len(wp) >= 2 * len(wu)
            and not _clases_de_texto(ultimo) and bool(_clases_de_texto(primero)))


def _subtema_desde_titulo(titulo: str) -> str:
    """Rótulo de emergencia a partir del propio titular (nunca inventado).

    v4.21: además del recorte honesto, limpia marcas de titular-noticia que
    nunca pertenecen a una etiqueta: interjecciones («¡Atención!»), signos
    «¡!¿?» en los bordes y comillas envolventes. Si el segmento tras los dos
    puntos es una cita («…: "Septiembre era el momento perfecto"»), el hecho
    está en el segmento previo y se prefiere ese. Tras recortar a 7 palabras
    también se quitan verbos iniciales («revela detalles…» → «detalles…»).
    """
    t = sq(titulo or '').strip()
    if not t:
        return 'Hecho informativo'
    # v4.21: titular-pregunta («¿Cuántos años tiene…? Inició en la radio…»):
    # las oraciones-pregunta no aportan el hecho; se eliminan y se conserva
    # el contenido declarativo.
    if '?' in t or '¿' in t:
        t = re.sub(r'¿[^?¿]*\?', ' ', t)
        t = t.replace('¿', ' ').replace('?', ' ')
        t = re.sub(r'\s+', ' ', t).strip(' .')
        if not t:
            return 'Hecho informativo'
    # Titular en dos partes («…: así fue el ascenso político de X»): el hecho
    # suele estar después de los dos puntos… salvo que ese segmento sea una
    # cita destacada; entonces el hecho está antes («Lina Tejeiro revela…:
    # "Septiembre era el momento perfecto"»).
    if ':' in t:
        segs = [s.strip() for s in t.split(':') if s.strip()]
        if segs:
            ultimo = segs[-1]
            if _CITA_ENVOLVENTE.match(ultimo):
                previos = [s for s in segs[:-1]
                           if not _CITA_ENVOLVENTE.match(s)]
                t = previos[0] if previos else ultimo
            elif len(segs) > 1 and _es_frase_destacada(ultimo, segs[0]):
                t = segs[0]
            else:
                t = ultimo
    t = t.strip('"\'«»“”').strip()
    t = re.sub(r'^[¡!¿?]+', '', t).strip()
    t = _INTERJECCION_TITULAR.sub('', t).strip()
    t = re.sub(r'[¡!¿?]+$', '', t).strip()
    if not t:
        return 'Hecho informativo'
    t = re.sub(r'^(as[ií]\s+fue\s+el|as[ií]\s+fue\s+la|esto\s+es\s+lo\s+que)\s+',
               '', t, flags=re.I).strip()
    t = re.sub(r'^(el|la|los|las|un|una)\s+', '', t, flags=re.I).strip()
    pals = t.split()
    if len(pals) > 7:
        # Titular largo sin dos puntos: el asunto distintivo suele ir al
        # final («…el VIII Congreso Internacional de … Psicológica»).
        pals = pals[-7:]
        while len(pals) > 3 and (nz(pals[0]) in _PREP_INICIAL
                                 or nz(pals[0]) in _VERBO_INFO_SING
                                 or _empieza_con_verbo(pals)):
            pals = pals[1:]
        t = ' '.join(pals)
    if t and t == t.upper() and any(c.isalpha() for c in t):
        # Conserva siglas (IA, PAE); nexos y resto a frase normal: primera
        # palabra con mayúscula inicial, las demás en minúsculas.
        stop = {'Y', 'O', 'E', 'DE', 'LA', 'EL', 'EN', 'LOS', 'LAS', 'DEL', 'AL',
                'UN', 'UNA', 'UNO', 'CON', 'POR', 'PARA', 'QUE', 'SE', 'SU'}
        pals_m = []
        for i, w in enumerate(t.split()):
            if w.isupper() and 2 <= len(w) <= 4 and w not in stop:
                pals_m.append(w)  # sigla
            elif i == 0:
                pals_m.append(w.capitalize())
            else:
                pals_m.append(w.lower())
        t = ' '.join(pals_m)
    if t:
        t = t[0].upper() + t[1:]
    return t or 'Hecho informativo'


def _etiqueta_ajena(g: dict, subtema: str, grupos: Sequence[dict]) -> bool:
    """True si `subtema` describe mejor el titular de OTRO grupo que el propio."""
    ws = _contenido_discriminante(subtema) - GENERICO_NO_UNEN
    if len(ws) < 2:
        return False
    propio = _contenido_discriminante(' '.join([
        str(g.get('titulo') or ''),
        ' '.join(str(x or '') for x in (g.get('titulos_alt') or [])),
        str(g.get('texto') or ''),
        str(g.get('contexto') or g.get('contexto_marca') or ''),
    ]))
    if len(ws & propio) > 1:
        return False
    for h in grupos:
        if h.get('grupo') == g.get('grupo'):
            continue
        wh = _contenido_discriminante(str(h.get('titulo') or '')) - GENERICO_NO_UNEN
        if len(ws & wh) >= 2:
            return True
    return False


def reparar_subtemas_ajenos(grupos: Sequence[dict],
                            etiquetas: Dict[int, dict]) -> int:
    """Repara etiquetas cruzadas del modelo (v4.18).

    Corre tras todas las unificaciones de subtema y antes del voto de tono
    por subtema, para que el voto no use subtemas ajenos. Solo actúa con
    evidencia fuerte (>=2 palabras distintivas con el titular ajeno y <=1 con
    el propio); ante la duda, separar = no tocar.
    """
    cambios = 0
    for g in grupos:
        e = etiquetas.get(g.get('grupo'))
        if not e or not (e.get('sub_tema') or '').strip():
            continue
        if _etiqueta_ajena(g, e['sub_tema'], grupos):
            e['sub_tema'] = _subtema_desde_titulo(g.get('titulo'))
            cambios += 1
    return cambios


# ============================================================================
# v4.21: el subtema nunca es el titular (reparación determinista).
#
# Cuando el modelo devuelve el titular tal cual como subtema, o usa una cita
# o una pregunta como etiqueta, la reparación es mecánica y no necesita una
# llamada LLM: se deriva un rótulo honesto del propio titular. Esto ahorra
# llamadas y evita que la vuelta de reparación «arregle» etiquetas buenas.
# ============================================================================
# Problemas de validar() con reparación mecánica obvia.
_PROBLEMAS_MECANICOS = {'copia_titular', 'caracter_marcador'}


def _subtema_envuelto_en_cita(s: str) -> bool:
    """True si TODO el subtema es una cita («"Septiembre era el momento…"»).

    Una cita parcial dentro de una etiqueta nominal («Lanzamiento del álbum
    'Arriba La L'») sí es informativa y no se toca.
    """
    return bool(_CITA_ENVOLVENTE.match(str(s or '').strip()))


def reparar_subtema_determinista(sub_tema: str, titulo: str) -> str:
    """Repara sin LLM un subtema copiado del titular, cita o pregunta.

    Devuelve el rótulo nuevo (validado) o '' si el caso no es mecánico y debe
    resolverlo la vuelta LLM de reparación.
    """
    s = str(sub_tema or '').strip()
    t = str(titulo or '').strip()
    if not s or not t:
        return ''
    mecanico = (
        _subtema_envuelto_en_cita(s)
        or s.rstrip().endswith('?')
        or (_subtema_copia_titular(s, t) and not _es_etiqueta_valida(s))
    )
    if not mecanico:
        return ''
    nuevo = _subtema_desde_titulo(t)
    if not nuevo or nuevo == 'Hecho informativo':
        return ''
    pr = validar(nuevo, 'Neutro', [t])
    if [x for x in pr if not x.startswith('revisar_anclaje')]:
        return ''
    return nuevo


# ============================================================================
# v4.20: mismo hecho por ancla de persona + evento compatible.
#
# El modelo redacta el mismo hecho con paráfrasis que ninguna comparación de
# cadenas une («Hospitalización por complicaciones pulmonares» vs «Estado de
# salud de Yamid Amat»; «"Septiembre era el momento perfecto"» como subtema
# de una noticia del nacimiento de Gael). La evidencia fuerte aquí es otra y
# es general/paramétrica (no atada a ningún cliente): el MISMO nombre propio
# —paciente o persona, DETECTADO en el texto, no configurado— más palabras de
# evento compatibles (las clases de dominio de v4.20).
#
# Reglas (todas deben cumplirse):
#  1. Ancla compartida: el mismo nombre propio en ambos grupos. Si los dos
#     subtemas nombran personas distintas («Yamid Amat» vs «Lina Tejeiro»),
#     no se unen aunque compartan clase de evento. Se admite referencia
#     cruzada (la madre en un subtema, el hijo en el otro, ambos nombrados en
#     los titulares: mismo episodio de atención).
#  2. Evento compatible: alguna clase de evento en común entre (subtema ∪
#     titular) de ambos. Sin clase común no hay unión («Edad y trayectoria
#     de Yamid Amat», «Vargas se pronuncia sobre Yamid», «Información sobre
#     el EPOC» quedan separados: son otro asunto).
#  3. Paráfrasis, no otro hecho: las firmas de evento de los subtemas
#     comparten contenido y difieren en a lo sumo 1 token; una condición o
#     enfermedad distinta veta la unión («hospitalizado por fiebre» no es
#     «nacimiento»). Los subtemas degenerados (una cita, sin ancla ni evento:
#     «"Septiembre era el momento perfecto"») adoptan el canon si cumplen 1
#     y 2, porque no afirman un hecho competing.
# La marca/alias/voceros nunca son ancla (son el cliente, no el paciente) y
# la geografía tampoco.
# ============================================================================
_ANCLA_MULTI_PAT = re.compile(
    r'\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})+)\b')
_ANCLA_UNO_PAT = re.compile(r'\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{3,})\b')
_ANCLA_DESCARTA = {
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
    'septiembre', 'octubre', 'noviembre', 'diciembre',
    'lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo',
    'clinica', 'hospital', 'fundacion', 'universidad', 'doctor', 'doctora',
    'presidente', 'presidenta', 'ministro', 'ministra', 'alcalde', 'alcaldesa',
    'gobernador', 'director', 'directora', 'noticiero', 'noticieros',
    'estado', 'estados',
}


def _tokens_marca(brand: str, aliases: Sequence[str],
                  voceros: Sequence[str] = ()) -> set:
    toks = set()
    for x in [brand] + list(aliases or []) + list(voceros or []):
        toks.update(w for w in words(x) if len(w) >= 3)
    return toks


# Tokens que vetan un ancla: si el "nombre propio" contiene vocabulario de
# evento («Salud» en «Sistema de Salud Colombiano») o genéricos, no es una
# persona y no puede anclar un hecho (v4.20).
def _ancla_vetada(toks: tuple) -> bool:
    for t in toks:
        if raiz(t) in _CLASE_DE:
            return True
        if t in GENERICO_NO_UNEN or t in MODIFICADOR_GENERICO_NO_UNE:
            return True
    return False


# Nombre propio largo con conectores en minúscula («Así Vamos en Salud»,
# «Universidad de los Andes»): si contiene vocabulario de evento, ninguno de
# sus tokens puede ser ancla de persona (v4.20). El patrón básico de anclas
# solo ve «Así Vamos» porque «en» va en minúscula; este lo ve completo.
_ORG_PAT = re.compile(
    r'\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}'
    r'(?:\s+(?:de|del|en|y|e)\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})+)\b')


def _anclas_en(texto: str, tokens_marca: set,
               solo_subtema: bool = False) -> set:
    """Nombres propios candidatos como tuplas de tokens normalizados."""
    t = texto or ''
    # Veto organizacional: tokens de un nombre con conectores que contiene
    # vocabulario de evento («asi», «vamos» en «Así Vamos en Salud»).
    veto_org = set()
    for m in _ORG_PAT.finditer(t):
        toks = tuple(x for x in words(m.group(1)) if len(x) >= 3)
        if any(raiz(x) in _CLASE_DE for x in toks):
            veto_org.update(toks)
    anclas = set()
    for m in _ANCLA_MULTI_PAT.finditer(t):
        toks = tuple(t for t in words(m.group(1)) if len(t) >= 3)
        if len(toks) < 2:
            continue
        if any(t in tokens_marca for t in toks):
            continue
        if all(_es_geografia(t) for t in toks):
            continue
        if _ancla_vetada(toks):
            continue
        if any(x in veto_org for x in toks):
            continue
        anclas.add(toks)
    if solo_subtema:
        # Ancla de un token solo si va en el subtema («Gael», «Yamid»): en
        # titulares suelta es demasiado ruidosa.
        for m in _ANCLA_UNO_PAT.finditer(t):
            wt = words(m.group(1))
            if not wt:
                continue
            tok = wt[0]
            if (tok in tokens_marca or _es_geografia(tok)
                    or tok in _ANCLA_DESCARTA or _ancla_vetada((tok,))):
                continue
            anclas.add((tok,))
    return anclas


def _ancla_compartida(a: tuple, b: tuple) -> bool:
    if a == b:
        return True
    if len(a) == 1 and a[0] in b:
        return True
    if len(b) == 1 and b[0] in a:
        return True
    return False


def _clases_de_texto(texto: str) -> set:
    """Clases de evento presentes en el texto (unigramas + bigramas)."""
    toks = [w for w in words(texto or '') if len(w) >= 3]
    r = [raiz(w) for w in toks]
    sal = set()
    for k in range(len(r) - 1):
        cid = _BIGRAM_CLASE.get((r[k], r[k + 1]))
        if cid:
            sal.add(cid)
    for t in r:
        cid = _CLASE_DE.get(t)
        if cid:
            sal.add(cid)
    return sal


def _sig_evento_sub(subtema: str, anclas: set, tokens_marca: set) -> set:
    """Firma de evento del subtema: contenido menos anclas, marca y
    geografía, normalizado por clase de evento."""
    ancla_toks = set()
    for a in anclas:
        ancla_toks.update(a)
    toks = [w for w in words(subtema or '')
            if w not in CONECT and w not in FILLER and w not in MARCO
            and len(w) >= 3 and not _es_geografia(w)
            and w not in ancla_toks and w not in tokens_marca]
    r = [raiz(w) for w in toks]
    sal = set()
    usados = set()
    for k in range(len(r) - 1):
        cid = _BIGRAM_CLASE.get((r[k], r[k + 1]))
        if cid:
            sal.add(cid)
            usados.add(k)
            usados.add(k + 1)
    for k, t in enumerate(r):
        if k in usados:
            continue
        sal.add(_CLASE_DE.get(t, t))
    return sal


def _hecho_compatible_por_ancla(fi: dict, fj: dict) -> bool:
    """True si dos grupos son el mismo hecho por ancla + evento (v4.20)."""
    ai_sub, aj_sub = fi['a_sub'], fj['a_sub']
    # 1. Ancla compartida.
    if ai_sub and aj_sub:
        # Los dos subtemas nombran persona: deben coincidir, o estar
        # cruz-referenciados (madre/hijo nombrados en los titulares).
        directa = any(_ancla_compartida(a, b) for a in ai_sub for b in aj_sub)
        cruzada = (all(any(_ancla_compartida(a, b) for b in fj['a_all'])
                       for a in ai_sub)
                   and all(any(_ancla_compartida(b, a) for a in fi['a_all'])
                           for b in aj_sub))
        if not (directa or cruzada):
            return False
    else:
        # Al menos un lado es degenerado (cita sin ancla): el ancla
        # compartida debe estar nombrada en el subtema del otro lado.
        ok = False
        for a in fi['a_all']:
            for b in fj['a_all']:
                if not _ancla_compartida(a, b):
                    continue
                toks = set(a) | set(b)
                if toks <= fi['toks_sub'] or toks <= fj['toks_sub']:
                    ok = True
                    break
            if ok:
                break
        if not ok:
            return False
    # 2. Evento compatible: alguna clase en común.
    if not (fi['clases_all'] & fj['clases_all']):
        return False
    # 3. Paráfrasis, no otro hecho (los degenerados no afirman hecho propio).
    if fi['degenerado'] or fj['degenerado']:
        return True
    si, sj = fi['sig'], fj['sig']
    if not (si & sj):
        return False
    diff = si ^ sj
    if len(diff) > 1:
        return False
    if diff & _CONDICION_NO_EVENTO:
        return False
    return True


def unificar_hecho_por_ancla(grupos: Sequence[dict], etiquetas: Dict[int, dict],
                             brand: str, aliases: Sequence[str],
                             voceros: Sequence[str] = ()) -> int:
    """Une grupos que son el mismo hecho por ancla de persona + evento (v4.20).

    Corre tras reparar_subtemas_ajenos y antes del voto de tono: el voto y las
    guardas trabajan ya sobre subtemas unificados. El canon del hecho es el
    subtema más frecuente (empate: con ancla nombrada, luego el más largo).
    """
    tokens_marca = _tokens_marca(brand, aliases, voceros)
    n = len(grupos)
    if n < 2:
        return 0
    infos = []
    for g in grupos:
        gid = g.get('grupo')
        e = etiquetas.get(gid) or {}
        sub_orig = str(e.get('sub_tema') or '')
        sub = nz(sub_orig)
        tit = str(g.get('titulo') or '')
        # Los anclas se detectan sobre el texto ORIGINAL (con mayúsculas):
        # nz() normaliza a minúsculas y el patrón de nombres propios no
        # calzaría nunca (bug v4.20: a_sub quedaba siempre vacío).
        a_sub = _anclas_en(sub_orig, tokens_marca, solo_subtema=True)
        a_all = (_anclas_en(sub_orig, tokens_marca)
                 | _anclas_en(tit, tokens_marca))
        sig = _sig_evento_sub(sub, a_sub, tokens_marca)
        clases_sub = _clases_de_texto(sub)
        infos.append({
            'gid': gid,
            'sub': sub,
            'sub_orig': sub_orig,
            'a_sub': a_sub,
            'a_all': a_all,
            'toks_sub': set(words(sub)),
            'sig': sig,
            'clases_all': clases_sub | _clases_de_texto(tit),
            'degenerado': (not a_sub) and not clases_sub,
        })
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    def uni(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            par[max(ra, rb)] = min(ra, rb)

    for i in range(n):
        if not infos[i]['sub']:
            continue
        for j in range(i + 1, n):
            if not infos[j]['sub'] or find(i) == find(j):
                continue
            if _hecho_compatible_por_ancla(infos[i], infos[j]):
                uni(i, j)

    cambios = 0
    buckets = {}
    for i in range(n):
        buckets.setdefault(find(i), []).append(i)
    for miembros in buckets.values():
        if len(miembros) < 2:
            continue
        conteo = Counter(infos[k]['sub'] for k in miembros)
        # Canon: más frecuente (comparación normalizada); en empate, el que
        # nombra el ancla y luego el más largo (más específico, v4.15/v4.18).
        # Se escribe la forma ORIGINAL más frecuente del canon, nunca la
        # normalizada en minúsculas.
        def _clave(s):
            k0 = next(k for k in miembros if infos[k]['sub'] == s)
            con_ancla = 1 if infos[k0]['a_sub'] else 0
            return (conteo[s], con_ancla, len(s.split()), len(s))
        canon_norm = max(conteo, key=_clave)
        canon_orig = Counter(infos[k]['sub_orig'] for k in miembros
                             if infos[k]['sub'] == canon_norm).most_common(1)[0][0]
        for k in miembros:
            gid = infos[k]['gid']
            e = etiquetas.get(gid)
            if e and str(e.get('sub_tema') or '') != canon_orig:
                e['sub_tema'] = canon_orig
                cambios += 1
    return cambios


def unificar_subtemas_noticias_similares(grupos: Sequence[dict], etiquetas: Dict[int, dict],
                                         umbral: int = 80) -> int:
    """Si dos grupos siguen separados pero son el mismo hecho, comparten subtema."""
    from rapidfuzz import fuzz
    n = len(grupos)
    if n < 2:
        return 0
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    def uni(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            par[max(ra, rb)] = min(ra, rb)

    for i in range(n):
        ei = etiquetas.get(grupos[i]['grupo'], {})
        for j in range(i + 1, n):
            ej = etiquetas.get(grupos[j]['grupo'], {})
            if _subtemas_mismo_hecho(ei.get('sub_tema', ''), ej.get('sub_tema', '')):
                uni(i, j)
                continue
            ti, tj = nz(grupos[i].get('titulo')), nz(grupos[j].get('titulo'))
            if not ti or not tj:
                continue
            if fuzz.token_set_ratio(ti, tj) < umbral:
                # Mismo hecho con titular distinto: lo confirma el contexto de
                # marca (pasajes citados compartidos), no palabras clave sueltas.
                if _contextos_mismo_hecho(grupos[i].get('contexto_marca') or '',
                                         grupos[j].get('contexto_marca') or ''):
                    uni(i, j)
                continue
            wi = set(w for w in words(ti) if w not in GENERIC_TITULO)
            wj = set(w for w in words(tj) if w not in GENERIC_TITULO)
            if len(wi & wj) >= 2:
                uni(i, j)

    cambios = 0
    buckets = defaultdict(list)
    for i in range(n):
        buckets[find(i)].append(i)
    for miembros in buckets.values():
        if len(miembros) < 2:
            continue
        validos = [(etiquetas.get(grupos[k]['grupo'], {}).get('sub_tema') or '')
                   for k in miembros]
        validos = [s for s in validos if s]
        if not validos:
            continue
        c = Counter(nz(s) for s in validos)
        maxrep = max(c.values())
        cand = [s for s in validos if c[nz(s)] == maxrep]
        # En empate de frecuencia, el canon es el MAS LARGO (más específico),
        # mismo criterio que v4.15 para el pase LLM: «Conversatorio» no le gana
        # a «Participación en el conversatorio de salud mental».
        canon = max(cand, key=lambda s: (len(s.split()), len(s)))
        # Quiénes ya llevan el canon (antes de reescribir): solo se adopta el
        # canon si hay evidencia FUERTE de mismo hecho con alguno de ellos.
        # La unión por señales débiles no autoriza a renombrar. Caso real
        # Unisimón: «La Universidad de Atalaya» absorbida por «Investigación
        # sobre Carnaval 2027». Ante la duda, separar.
        holders = [m for m in miembros
                   if nz((etiquetas.get(grupos[m]['grupo']) or {}).get('sub_tema')) == nz(canon)]
        for k in miembros:
            gid = grupos[k]['grupo']
            e = etiquetas.get(gid)
            sk = (e or {}).get('sub_tema') or ''
            if not (e and sk and nz(sk) != nz(canon)):
                continue
            if any(_puede_adoptar_canon(grupos[k], sk, grupos[m], canon, umbral)
                   for m in holders if m != k):
                e['sub_tema'] = canon
                cambios += 1
    return cambios


def _sanitizar_fusiones(fusiones, n):
    """Limpia la respuesta del modelo: >=2 índices distintos por grupo y sin
    repetir un índice en dos grupos (mapeo determinista).

    Tolera índices 0-based o 1-based: si el mínimo es 0 y el máximo < n se
    interpretan como 0-based; si están en [1, n], como 1-based. Sin esto, una
    respuesta 0-based se descartaba en silencio y no se fusionaba nada.
    """
    limpias = []
    usados = set()
    for f in fusiones or []:
        crudos = []
        for x in f or []:
            try:
                crudos.append(int(x))
            except Exception:
                continue
        if not crudos:
            continue
        if min(crudos) >= 1 and max(crudos) <= n:
            idxs = [c - 1 for c in crudos]
        elif min(crudos) >= 0 and max(crudos) < n:
            idxs = list(crudos)
        else:
            continue
        unicos = []
        for i in idxs:
            if i not in unicos and i not in usados:
                unicos.append(i)
        if len(unicos) >= 2:
            limpias.append(unicos)
            usados.update(unicos)
    return limpias


def _canonico_de_fusion(idxs, textos, conteo):
    """Representante del grupo fusionado: el más frecuente; en empate, el MÁS
    LARGO (más específico). El empate corto-elegido degradaba la especificidad
    del subtema («Conversatorio» ganaba a «Participación en el conversatorio
    de salud mental»). Devuelve el texto original (verbatim) del elegido."""
    mejor = None
    for i in idxs:
        t = textos[i]
        clave = (conteo.get(nz(t), 0), len(t))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, t)
    return mejor[1]


def unificar_subtemas_llm(cfg, grupos, etiquetas, uso=None):
    """Pase final (1 sola llamada LLM): fusiona subtemas que son el mismo
    asunto aunque la redaccion difiera entre lotes.

    Los lotes de etiquetado corren en paralelo sin verse entre si y la
    canonizacion determinista solo caza variantes de redaccion parecida
    («Inauguración del laboratorio» vs «Inaguración del laboratorio»), no
    parafrasis («Apertura del laboratorio»). Esta pasada recibe la lista
    completa de subtemas unicos y pide al modelo agrupar solo los que son
    EXACTAMENTE el mismo hecho/asunto. Conservadora por diseño: ante la
    duda no fusiona (un tema incorrecto es peor que dos subtemas pequenos);
    en empate de frecuencia conserva el subtema MAS especifico (el mas
    largo), y nunca fusiona subtemas con tonos distintos (evita que el voto
    de tono por subtema voltee un Positivo a Neutro).
    Si la llamada falla, no rompe el pipeline: devuelve 0.
    """
    vistos = {}
    for e in etiquetas.values():
        s = (e.get('sub_tema') or '').strip()
        if s and nz(s) not in vistos:
            vistos[nz(s)] = s
    textos = list(vistos.values())
    if len(textos) <= 1:
        return 0
    # Un titular corto de ejemplo por subtema, para desambiguar.
    gid_por_sub = {}
    for gid, e in etiquetas.items():
        k = nz(e.get('sub_tema'))
        if k and k not in gid_por_sub:
            gid_por_sub[k] = gid
    grupo_por_gid = {g.get('grupo'): g for g in grupos}
    lineas = []
    for i, t in enumerate(textos, 1):
        g = grupo_por_gid.get(gid_por_sub.get(nz(t))) or {}
        tit = str(g.get('titulo') or '')[:110].strip()
        lineas.append('%d. «%s»%s' % (i, t, ' — ej.: «%s»' % tit if tit else ''))
    mensajes = [
        {'role': 'system',
         'content': 'Unificas nombres de subtemas de un dossier de prensa. Solo agrupas '
                    'los que describen EXACTAMENTE el mismo hecho o asunto específico. '
                    'Respondes únicamente en JSON.'},
        {'role': 'user',
         'content': (
             'SUBTEMAS:\n' + '\n'.join(lineas) + '\n\n'
             'Reglas:\n'
             '1. Fusiona SOLO los que describen exactamente el mismo hecho o asunto '
             'específico (p. ej. «Inauguración del laboratorio» y «Apertura del laboratorio»).\n'
             '2. ANTE LA DUDA, NO FUSIONES: es preferible dejar dos subtemas separados '
             'que unir dos asuntos distintos.\n'
             '3. No fusiones por palabras genéricas (prevención, impacto, crisis, jóvenes, '
             'mujeres) ni porque mencionen la misma marca, persona o ciudad.\n'
             '4. Si difieren en ciudad, persona, fecha, entidad o alcance, NO los fusiones.\n'
             'Responde {"fusiones": [[i, j], ...]} con los números de la lista (empezando '
             'en 1) que sí son el mismo asunto. Si ninguno, {"fusiones": []}.')},
    ]
    try:
        data = _json_loose(llamar_llm(cfg, mensajes, json_mode=True,
                                      max_tokens=2000, uso=uso)) or {}
    except Exception:
        return 0
    fusiones = _sanitizar_fusiones(data.get('fusiones'), len(textos))
    if not fusiones:
        return 0
    conteo = Counter(nz(e.get('sub_tema')) for e in etiquetas.values()
                     if e.get('sub_tema'))
    cambios = 0
    for idxs in fusiones:
        miembros = {nz(textos[i]) for i in idxs}
        # Nunca fusionar subtemas con tonos distintos: el voto de tono por
        # subtema (`unificar_tono_mismo_hecho`) podria voltear un Positivo
        # legitimo a Neutro. Ante la duda, separar.
        tonos = {((etiquetas.get(g) or {}).get('tono') or 'Neutro')
                 for g, e in etiquetas.items() if nz(e.get('sub_tema')) in miembros}
        if len(tonos) > 1:
            continue
        canon = _canonico_de_fusion(idxs, textos, conteo)
        canon_k = nz(canon)
        for e in etiquetas.values():
            k = nz(e.get('sub_tema'))
            if k in miembros and k != canon_k:
                e['sub_tema'] = canon
                cambios += 1
    if cambios:
        _ULTIMO_RESUMEN['subtemas_unificados_llm'] = cambios
    return cambios


def elegir_cubos(cfg: dict, pendientes: Sequence[dict], tax: dict,
                 permitir_nuevos: bool = True) -> Dict[int, str]:
    """El modelo elige dentro de la lista cerrada (o propone un cubo nuevo especifico)."""
    out: Dict[int, str] = {}
    if not pendientes:
        return out
    try:
        txt = llamar_llm(cfg, [{'role': 'system',
                                'content': 'Clasificas notas en cubos tematicos cerrados. Respondes en JSON.'},
                               {'role': 'user', 'content': prompt_cubos(pendientes, tax, permitir_nuevos)}])
        data = _json_loose(txt) or {}
    except Exception:
        data = {}
    for r in data.get('resultados', []) or []:
        try:
            gid = int(r.get('id'))
        except Exception:
            continue
        c = cubo_valido(r.get('cubo'), tax, permitir_nuevos)
        if c:
            out[gid] = c
    return out


# ============================================================================
# 7. Asignacion final de Tema
# ============================================================================
def _mismo_cubo(a: str, b: str) -> bool:
    """Dos nombres de cubo son el mismo si son casi iguales o si uno es el otro con un añadido.

    Caso real: 'Prevencion del suicidio' y 'Prevencion del suicidio en Barranquilla' son el mismo
    cubo, y la similitud por token_sort no llega al umbral porque la segunda agrega palabras.
    """
    from rapidfuzz import fuzz
    if fuzz.token_sort_ratio(nz(a), nz(b)) >= 90:
        return True
    ta, tb = set(nz(a).split()), set(nz(b).split())
    chico, grande = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return chico <= grande and len(chico) >= 2 and 0 < len(grande) - len(chico) <= 3


def canonizar_cubos(temas: Dict[int, str], tax: dict) -> int:
    """Unifica cubos NUEVOS que son variantes del mismo nombre. Los de la lista del cliente no se tocan."""
    en_lista = {nz(t) for t in tax['temas']}
    conteo = Counter(t for t in temas.values() if nz(t) not in en_lista)
    if len(conteo) <= 1:
        return 0
    reps, canon = [], {}
    for nombre, _n in conteo.most_common():
        destino = next((r for r in reps if _mismo_cubo(nombre, r)), None)
        if destino is None:
            reps.append(nombre)
            canon[nombre] = nombre
        else:
            canon[nombre] = destino
    cambios = 0
    for k, v in list(temas.items()):
        nuevo = canon.get(v, v)
        if nz(nuevo) != nz(v):
            temas[k] = nuevo
            cambios += 1
    return cambios


def derivar_reglas(cubos: Sequence[str]) -> List[dict]:
    """Convierte una lista de cubos en reglas lexicas para el primer pase determinista.

    Cada cubo aporta su nombre completo y sus palabras de contenido como claves exactas. Las reglas
    se ordenan del cubo mas especifico (mas palabras) al mas general, para que gane el que mas
    describe el hecho.
    """
    reglas = []
    for nombre in cubos:
        toks = [t for t in nz(nombre).split() if t]
        claves = [nz(nombre)]
        for t in toks:
            if (_es_geografia(t) or len(t) < 4 or t in CONECT or t in FILLER or
                    t in MARCO or t in claves):
                continue
            claves.append(t)
        if len(claves) > 1:
            reglas.append({'tema': nombre, 'claves': claves})
    reglas.sort(key=lambda r: -len(nz(r['tema']).split()))
    return reglas


def _muestreo_grupos(grupos: Sequence[dict], etiquetas: Dict[int, dict], por_bloque: int = 35,
                     max_bloques: int = 12) -> List[List[str]]:
    lineas = []
    for g in grupos:
        st_ = (etiquetas.get(g['grupo']) or {}).get('sub_tema') or ''
        lineas.append('- Subtema: %s | Titular: %s | Evidencia: %s' %
                      (st_[:100], sq(g['titulo'])[:110],
                       sq(g.get('contexto_marca') or g.get('contexto') or g.get('texto', ''))[:350]))
    if len(lineas) > por_bloque * max_bloques:
        paso = max(1, len(lineas) // (por_bloque * max_bloques))
        lineas = lineas[::paso]
    return [lineas[i:i + por_bloque] for i in range(0, len(lineas), por_bloque)]


def proponer_taxonomia(cfg: dict, grupos: List[dict], etiquetas: Dict[int, dict],
                       objetivo: int = 16, progress: Optional[Callable] = None) -> dict:
    """Construye la lista de Temas A PARTIR DEL CONTENIDO del archivo, sin lista fija.

    Los clientes son muy distintos (universidades, sector publico, privado, marcas), asi que los
    cubos se proponen leyendo los hechos del propio dossier: primero por bloques y despues con una
    consolidacion que elimina duplicados y solapamientos.
    """
    if progress:
        progress(93, 'Proponiendo la lista de Temas a partir del archivo…')
    bloques = _muestreo_grupos(grupos, etiquetas)
    propuestas: List[str] = []
    for bloque in bloques:
        msgs = [{'role': 'system', 'content':
                 'Eres analista de medios en Colombia. Agrupas hechos en cubos tematicos. Respondes en JSON.'},
                {'role': 'user', 'content':
                 'Estos son hechos de un dossier de prensa:\n\n' + '\n'.join(bloque) +
                 '\n\nPropón entre 10 y 14 CUBOS TEMATICOS que agrupen los SUBTEMAS anteriores.\n'
                 'El SUBTEMA es la fuente principal para definir cada cubo. Usa Titular y Evidencia '
                 'solo para comprobar que los subtemas pertenecen al mismo asunto y no mezclar hechos '
                 'distintos. No copies ningún subtema como nombre de cubo.\n'
                 'Reglas: nombres de 2 a 5 palabras; macrotemas específicos de ESTOS subtemas; '
                 'relacionados con la evidencia; sin solaparse entre sí; sin contar el nombre de la marca; '
                 'nada de "Otros", "Varios", "General" ni "Informacion".\n'
                 'Responde solo JSON: {"cubos":["Cubo uno","Cubo dos"]}'}]
        try:
            data = _json_loose(llamar_llm(cfg, msgs)) or {}
        except Exception:
            data = {}
        for c in data.get('cubos', []) or []:
            v = cubo_valido(c, {'temas': propuestas}, permitir_nuevos=True)
            if v and not any(_mismo_cubo(v, p) for p in propuestas):
                propuestas.append(v)

    if not propuestas:
        return taxonomia_por_nombre('Gobierno territorial')

    # --- consolidacion final: una sola lista sin solapamientos ---
    msgs = [{'role': 'system', 'content':
             'Eres analista de medios en Colombia. Consolidas listas de cubos tematicos en JSON.'},
            {'role': 'user', 'content':
             'Estos cubos fueron propuestos por varios analistas para el mismo dossier:\n\n'
             + '\n'.join('- %s' % p for p in propuestas) +
             '\n\nDevuelve la LISTA FINAL de %d cubos (puede ser menos si no hay materia): sin '
             'duplicados, sin solaparse, de 2 a 5 palabras, especificos, y sin "Otros" ni genericos.\n'
             'Responde solo JSON: {"cubos":["..."]}' % objetivo}]
    try:
        data = _json_loose(llamar_llm(cfg, msgs)) or {}
    except Exception:
        data = {}
    finales: List[str] = []
    for c in data.get('cubos', []) or propuestas:
        if not isinstance(c, str):
            continue
        v = cubo_valido(c, {'temas': finales}, permitir_nuevos=True)
        if v and not any(_mismo_cubo(v, f) for f in finales):
            finales.append(v)
    if len(finales) < 3:
        finales = propuestas[:max(3, objetivo)]
    tax = {'nota': 'Cubos generados automaticamente a partir del contenido de este archivo.',
           'temas': finales, 'reglas': derivar_reglas(finales)}
    if progress:
        progress(94, 'Lista de Temas generada: %d cubos' % len(finales))
    return tax


def _tema_distinto_de_subtema(tema: str, sub_tema: str) -> bool:
    """Tema más general que el subtema: no igual, no casi igual, no copia el hecho.

    Un subconjunto propio de tokens (p. ej. «Alimentación escolar» frente a
    «Inicio de clases con alimentación escolar») SÍ es más general y se acepta.
    """
    from rapidfuzz import fuzz
    t = nz(tema)
    s = nz(sub_tema)
    if not t or not s:
        return False
    if t == s:
        return False
    tw = [w for w in t.split() if w not in CONECT]
    sw = [w for w in s.split() if w not in CONECT]
    if not tw or set(tw) == set(sw):
        return False
    if s in t:
        return False
    if abs(len(tw) - len(sw)) <= 1 and fuzz.token_sort_ratio(t, s) >= 88:
        return False
    if len(tw) >= len(sw) and fuzz.token_set_ratio(t, s) >= 90:
        return False
    return True


def _tallo_lexico(t: str) -> str:
    t = raiz(t)
    for suf in ('tud', 'nil', 'dad', 'cion', 'sion'):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[:-len(suf)]
    return t


def _tokens_relacionados(a: str, b: str) -> bool:
    a, b = raiz(a), raiz(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return True
    ta, tb = _tallo_lexico(a), _tallo_lexico(b)
    return bool(ta) and ta == tb and len(ta) >= 4


def _tema_es_relevante(tema: str, sub_tema: str, contexto: str = '') -> bool:
    """Valida relación con subtema/evidencia sin copiar el subtema.

    El subtema es la señal principal. CuerpoEs, Título y Contexto analizado
    confirman que el cubo describe el mismo hecho o una familia válida.
    """
    if not _tema_distinto_de_subtema(tema, sub_tema):
        return False
    tema_tokens = {raiz(t) for t in words(tema) if t not in CONECT and len(t) >= 3}
    if not tema_tokens:
        return False
    sub_tokens = {raiz(t) for t in words(sub_tema) if t not in CONECT and len(t) >= 3}
    contexto_tokens = {raiz(t) for t in words(contexto) if t not in CONECT and len(t) >= 3}

    def hay_afines(src):
        return any(_tokens_relacionados(x, y) for x in tema_tokens for y in src)

    if hay_afines(sub_tokens):
        return True
    return hay_afines(contexto_tokens)


def _prefijo_comun_largo(a: str, b: str, minimo: int = 6) -> bool:
    """Afinidad morfológica relajada: 'suicidio' ~ 'suicidología'.

    Solo se usa en el guard anti-agrupamiento-forzado, donde el criterio
    léxico estricto fragmentaría familias legítimas con variantes
    morfológicas del mismo asunto.
    """
    a, b = nz(a), nz(b)
    if len(a) < minimo or len(b) < minimo:
        return False
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    return i >= minimo


def _stems_entidad(cfg: dict) -> set:
    """Stems de la marca, sus alias y voceros: dicen QUIÉN, no el asunto.

    En un dossier de Unisimón, 'unisimon' aparece en titulares de asuntos
    distintos y no puede usarse para unir familias de temas.
    """
    partes = [str(cfg.get('brand') or '')]
    partes += [str(a) for a in (cfg.get('aliases') or [])]
    partes += [str(v) for v in (cfg.get('voceros') or [])]
    return {raiz(w) for w in words(' '.join(partes)) if len(w) >= 3}


def _tema_relevante_para_miembro(tema: str, sub_tema: str, evidencia: str = '') -> bool:
    """El tema describe a ESTA noticia (no al agregado de la familia).

    Comparación exacta sobre tokens canonizados y distintivos: evita
    fragmentar familias legítimas ('suicidio'/'suicidología' canonizan al
    mismo stem) sin dejar pasar agrupamientos forzados. Un tema basado en
    un genérico ('prevención') no basta para retener a un miembro.
    """
    if not _tema_distinto_de_subtema(tema, sub_tema):
        return False

    def toks(texto: str) -> set:
        return {_EQUIV_STEMS.get(raiz(t), raiz(t))
                for t in words(texto)
                if t not in CONECT and len(t) >= 3
                and t not in GENERICO_NO_UNEN
                and t not in MODIFICADOR_GENERICO_NO_UNE}

    tema_tokens = toks(tema)
    if not tema_tokens:
        return False
    if tema_tokens & toks(sub_tema):
        return True
    return bool(tema_tokens & toks(evidencia))


def cluster_familias_subtema(items: Sequence[dict],
                           excluir_stems: Optional[set] = None) -> List[List[dict]]:
    """Une subtemas afines de ESTE lote (asunto compartido, hechos distintos).

    Comparación EXACTA sobre stems canonizados: `_EQUIV_STEMS` une
    'suicidología' con 'suicidio', 'criminalidad' con 'criminología',
    'educativo' con 'educación', 'juvenil' con 'joven'. La afinidad por
    prefijo se descartó porque generaba uniones sorpresa.

    Reglas:
    - A1: mismo hecho (subtemas casi idénticos) o 2+ stems distintivos
      compartidos en los núcleos.
    - A2: un único stem compartido en ambos núcleos, solo si es raro en el
      lote (df_nucleo<=3) y no es demográfico: 'joven' no une suicidio
      juvenil con desempleo juvenil.
    - B: 2+ stems distintivos compartidos en la evidencia (titulares y
      contexto).

    No cuentan para unir: stems omnipresentes en el lote, genéricos
    ('congreso', 'internacional'...), modificadores genéricos ('prevención',
    'desafío'...), marca/alias/voceros (dicen QUIÉN, no el asunto) ni
    atributos demográficos por sí solos.
    """
    items = [it for it in items if it and it.get('sub_tema')]
    if not items:
        return []
    excluir_stems = set(excluir_stems or [])
    n = len(items)
    disc_sub, disc_evi = [], []
    df = Counter()
    df_nucleo = Counter()
    for it in items:
        ds = _contenido_discriminante(it['sub_tema'])
        de = _contenido_discriminante(it.get('evidencia') or it['sub_tema'])
        disc_sub.append(ds)
        disc_evi.append(de)
        df.update(ds | de)
        df_nucleo.update(ds)
    # Un stem que aparece en muchos items del lote no distingue asuntos
    # (en un dossier de IA, "inteligencia artificial" no une nada).
    limite_df = max(3, int(n * 0.15))

    def dist(s: set) -> set:
        return {t for t in s
                if df[t] <= limite_df
                and t not in GENERICO_NO_UNEN
                and t not in MODIFICADOR_GENERICO_NO_UNE
                and t not in excluir_stems}

    def misma_familia(i: int, j: int) -> bool:
        a, b = items[i]['sub_tema'], items[j]['sub_tema']
        if _subtemas_mismo_hecho(a, b):
            return True
        comunes = dist(disc_sub[i]) & dist(disc_sub[j])
        # Los demográficos no unen por sí solos (pero sí acompañan).
        comunes = {t for t in comunes if t not in ATRIBUTO_DEMOGRAFICO}
        if len(comunes) >= 2:
            return True
        if len(comunes) == 1:
            return df_nucleo[next(iter(comunes))] <= 3
        return False

    def evidencia_comun(i: int, j: int) -> bool:
        return len(dist(disc_evi[i]) & dist(disc_evi[j])) >= 2

    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if misma_familia(i, j) or evidencia_comun(i, j):
                ra, rb = find(i), find(j)
                if ra != rb:
                    par[max(ra, rb)] = min(ra, rb)

    buckets = defaultdict(list)
    for i in range(n):
        buckets[find(i)].append(i)
    return [[items[k] for k in idxs] for idxs in buckets.values()]


def _sin_articulo_inicial(frase: str) -> str:
    toks = sq(frase).split()
    if toks and nz(toks[0]) in ARTICULOS:
        return ' '.join(toks[1:])
    return sq(frase)


def _complemento_nominal_tras_verbo(frase: str) -> str:
    """Si la fuente es una cláusula, conserva el SN objeto completo. No recorta el objeto."""
    toks = sq(frase).split()
    for i, t in enumerate(toks):
        if not _parece_verbo_finito(t):
            continue
        rest = toks[i + 1:]
        if rest and nz(rest[0]) in ARTICULOS | {'y', 'e', 'o'}:
            rest = rest[1:]
        if len(rest) < TEMA_MIN_PAL:
            continue
        cand = _capitalizar_etiqueta(' '.join(rest))
        if tema_frase_natural(cand):
            return cand
    return ''


def _generar_candidatos_frase(frase: str) -> List[str]:
    """Solo frases nominales completas. Prohibido ventanas que suelten el núcleo o el objeto."""
    frase = sq(frase)
    if not frase:
        return []
    out, vistos = [], set()

    def add(s: str) -> None:
        s = _capitalizar_etiqueta(_sin_articulo_inicial(s))
        k = nz(s)
        if s and k not in vistos and tema_frase_natural(s):
            vistos.add(k)
            out.append(s)

    base = _quitar_cola_lugar(_quitar_marco_inicial(frase))
    add(base)
    recortado = _quitar_ultimo_pp(base)
    # Solo si el recorte conserva un complemento preposicional. Si no, es un
    # fragmento tipo "Congreso iberoamericano" (se cayó "de suicidología").
    if recortado != base and any(nz(t) in PREP_SINTAXIS for t in recortado.split()):
        add(recortado)
    add(_complemento_nominal_tras_verbo(base) or _complemento_nominal_tras_verbo(frase))
    return out


def _score_tema_candidato(cand: str, subtemas: Sequence[str]) -> float:
    w = cand.split()
    toks = [nz(t) for t in w]
    contenido = [t for t in toks if t not in CONECT and t not in ARTICULOS]
    nexos = [t for t in toks if t in PREP_SINTAXIS]
    score = 0.0
    if 3 <= len(w) <= 5:
        score += 6
    elif len(w) == 2:
        score += 2
    elif len(w) == 6:
        score += 4
    if nexos:
        score += 3
    if any(_es_adjetivo_tematico(x) for x in w[1:]):
        score += 4
    elif len(contenido) == 2 and toks and toks[0] in ADJ_PRENOMINAL:
        score += 3
    cov = 0.0
    ct = {raiz(t) for t in contenido if len(t) >= 3}
    for s in subtemas:
        st = {raiz(t) for t in words(s) if t not in CONECT and len(t) >= 3}
        if any(_tokens_relacionados(a, b) for a in ct for b in st):
            cov += 1
        cov += 0.2 * sum(1 for a in ct for b in st if _tokens_relacionados(a, b))
    score += cov * 3
    for i, (t, ow) in enumerate(zip(toks, w)):
        if _es_geografia(t) or (i > 0 and _token_es_lugar(ow)):
            score -= 3
    if toks and toks[0] in {nz(x) for x in _MARCO_HEAD}:
        score -= 4
    score -= 2 * len(problemas_calidad_tema(cand))
    return score


def _hit_stems(stems: set, needles: Sequence[str]) -> bool:
    for n in needles:
        rn = raiz(n)
        if any(_tokens_relacionados(s, rn) or s.startswith(rn) or rn.startswith(s)
               for s in stems if s):
            return True
    return False


# (stems requeridos, stems extra opcionales, stems prohibidos, frase temática)
# El título es EVIDENCIA de stems, nunca una cadena a recortar.
_REGLAS_TEMA_SEGURO = (
    (['suicid'], None, None, 'Prevención del suicidio'),
    (['nutricion', 'alimentacion'], ['escolar', 'pae'], None, 'Alimentación escolar'),
    (['pae'], None, ['sancion', 'retraso', 'demora', 'procuradur'], 'Alimentación escolar'),
    (['campus'], ['universitari'], None, 'Creación de nuevos campus universitarios'),
    (['sede'], ['universitari'], None, 'Inauguración de nuevas sedes universitarias'),
    (['inauguracion', 'inaugur'], ['sede'], None, 'Inauguración de sede'),
    (['criminolog'], None, None, 'Congreso internacional de criminología'),
    (['medio', 'medios'], ['crisi'], None, 'Medios en crisis'),
    (['ecolog'], ['estudiantil', 'estudiante'], None,
     'Participación estudiantil en iniciativas ecológicas'),
    (['criminalidad', 'crimen'], ['inteligencia', 'ia'], None,
     'Criminalidad e inteligencia artificial'),
    (['robotic'], ['concurso', 'competencia', 'olimpiada'], None, 'Concurso de robótica'),
    (['acreditacion'], None, None, 'Acreditación universitaria'),
    (['graduacion'], None, None, 'Formación técnica'),
    (['empleo', 'desempleo'], ['joven', 'juventud', 'juvenil'], None, 'Empleo juvenil'),
    (['empleo'], ['estudiar', 'estudio', 'formacion'], None, 'Formación y empleo'),
    (['juventud'], ['desempleo', 'empleo'], None, 'Empleo juvenil'),
    (['manejo'], ['obra', 'ambiental', 'cienaga'], None, 'Obras de manejo ambiental'),
    (['salud'], ['ayuda', 'atencion', 'cuidado'], None, 'Atención en salud pública'),
    (['muerte', 'fallec', 'muere'], None, None, 'Salud y prevención'),
    (['reunion', 'encuentro'], ['experto', 'especialista'], None,
     'Encuentro académico de especialistas'),
    (['obra'], ['vial', 'carretera', 'via', 'infraestructur'], None, 'Obras viales'),
    (['obra'], ['alcalde', 'municipi', 'anuncia'], None, 'Obras de infraestructura'),
    (['huevo'], ['hurto', 'robo', 'roban', 'granja', 'avicol'], None, 'Hurto de alimentos'),
    (['hurto', 'robo', 'roban'], None, None, 'Hurto y seguridad'),
    (['captura', 'alias', 'operativ'], None, None, 'Operativos de seguridad'),
    (['fallo', 'judicial', 'tribunal'], None, None, 'Decisiones judiciales'),
    (['gestion'], ['alcaldia', 'municipi', 'informe'], None, 'Gestión municipal'),
)


def _frase_segura_desde_familia(subtemas: Sequence[str], titulos: Sequence[str],
                                contextos: Optional[Sequence[str]] = None) -> str:
    """Frase nominal temática derivada del SIGNIFICADO (stems). Nunca un recorte del titular."""
    textos = [sq(x) for x in list(subtemas) + list(titulos or []) +
              [sq(c)[:400] for c in (contextos or []) if c] if sq(x)]
    stems = {raiz(w) for w in words(' '.join(textos)) if len(w) >= 3}
    for need, extra, forbid, frase in _REGLAS_TEMA_SEGURO:
        if not _hit_stems(stems, need):
            continue
        if extra and not _hit_stems(stems, extra):
            continue
        if forbid and _hit_stems(stems, forbid):
            continue
        if not tema_frase_natural(frase):
            continue
        if subtemas and any(not _tema_distinto_de_subtema(frase, s) for s in subtemas if s):
            continue
        return frase
    return ''


def _np_completa_desde_fuente(frase: str, titulos: Optional[Sequence[str]] = None) -> str:
    """Frase nominal completa derivada de un SUBTEMA (síntesis), nunca recorte de titular.

    `titulos` solo sirven para RECHAZAR un candidato que copie el titular.
    Puede devolver '' si no hay materia — el llamador cae a `_tema_minimo_no_vacio`.
    """
    s = sq(frase)
    if not s:
        return ''
    malos = {nz(x) for x in TEMAS_EJEMPLO_MALOS}

    def ok(cand: str, vago: bool = False) -> bool:
        cand = sq(cand)
        if not _tema_util(cand) or nz(cand) in malos:
            return False
        if _tema_copia_o_prefijo_titulo(cand, titulos) and not _es_etiqueta_tematica_canonica(cand):
            return False
        return tema_frase_natural(cand, permitir_vago=vago, titulos=titulos)

    cap = _capitalizar_etiqueta(_sin_articulo_inicial(s))
    if ok(cap):
        return cap
    if ok(cap, vago=True):
        return cap
    comp = _complemento_nominal_tras_verbo(s)
    if ok(comp):
        return comp
    if ok(comp, vago=True):
        return comp

    toks = s.split()
    while toks and nz(toks[0]) in ARTICULOS:
        toks = toks[1:]
    while toks and _parece_verbo_finito(toks[0]):
        toks = toks[1:]
        while toks and nz(toks[0]) in ARTICULOS | {'y', 'e', 'o'}:
            toks = toks[1:]
    while toks and nz(toks[-1]) in PREP_FIN:
        toks = toks[:-1]
    if not toks:
        return ''

    n = len(toks)
    for vago in (False, True):
        for length in range(min(TEMA_MAX_PAL, n), TEMA_MIN_PAL - 1, -1):
            for i in range(0, n - length + 1):
                if _parece_verbo_finito(toks[i]):
                    continue
                cand = _capitalizar_etiqueta(
                    _sin_articulo_inicial(' '.join(toks[i:i + length])))
                if ok(cand, vago=vago):
                    return cand

    skip_num = {'mil', 'millon', 'millones', 'ciento', 'cien'}
    contenido = []
    for t in toks:
        nt = nz(t)
        if (nt in CONECT or nt in ARTICULOS or nt in SIGLAS_PROHIBIDAS
                or nt in NOMBRES_PERSONA_FREQ or nt in skip_num):
            continue
        if _parece_verbo_finito(t) or t.isdigit():
            continue
        contenido.append(t)
    if len(contenido) >= 2:
        a, b = contenido[0], contenido[1]
        if _es_adjetivo_tematico(b) or nz(a) in ADJ_PRENOMINAL:
            cand = _capitalizar_etiqueta('%s %s' % (a, b))
        else:
            cand = _capitalizar_etiqueta('%s de %s' % (a, b))
        if ok(cand) or ok(cand, vago=True):
            return cand
        if _tema_util(cand) and nz(cand) not in malos and not _parece_verbo_finito(a) and not _parece_verbo_finito(b):
            return cand
    nominales = [t for t in toks if not _parece_verbo_finito(t) and not t.isdigit()
                 and nz(t) not in skip_num]
    if len(nominales) >= TEMA_MIN_PAL:
        cand = _capitalizar_etiqueta(' '.join(nominales[:min(TEMA_MAX_PAL, len(nominales))]))
        if ok(cand) or ok(cand, vago=True):
            return cand
    if contenido:
        cand = _capitalizar_etiqueta(contenido[0])
        if ok(cand, vago=True):
            return cand
    return ''


def _tema_minimo_no_vacio(subtemas: Sequence[str],
                          titulos: Optional[Sequence[str]] = None,
                          contextos: Optional[Sequence[str]] = None) -> str:
    """Último recurso temático no vacío. Nunca recorta el titular ni devuelve ''."""
    titulos = [sq(t) for t in (titulos or []) if sq(t)]
    subtemas = [sq(s) for s in (subtemas or []) if sq(s)]
    seguro = _frase_segura_desde_familia(subtemas, titulos, contextos)
    if _tema_util(seguro):
        return seguro
    for sub in subtemas:
        if _tema_copia_o_prefijo_titulo(sub, titulos) and not _es_etiqueta_tematica_canonica(sub):
            continue
        np = _np_completa_desde_fuente(sub, titulos=titulos)
        if _tema_util(np) and not (
                _tema_copia_o_prefijo_titulo(np, titulos)
                and not _es_etiqueta_tematica_canonica(np)):
            return np
    # X de Y desde dos sustantivos del SUBTEMA (significado), no title[:N].
    for sub in subtemas:
        contenido = []
        for t in sq(sub).split():
            nt = nz(t)
            if (nt in CONECT or nt in ARTICULOS or _parece_verbo_finito(t)
                    or t.isdigit() or nt in NOMBRES_PERSONA_FREQ):
                continue
            contenido.append(t)
        if len(contenido) >= 2:
            a, b = contenido[0], contenido[1]
            if _es_adjetivo_tematico(b) or nz(a) in ADJ_PRENOMINAL:
                cruzado = _capitalizar_etiqueta('%s %s' % (a, b))
            else:
                cruzado = _capitalizar_etiqueta('%s de %s' % (a, b))
            if (_tema_util(cruzado)
                    and not (_tema_copia_o_prefijo_titulo(cruzado, titulos)
                             and not _es_etiqueta_tematica_canonica(cruzado))):
                return cruzado
    for sub in subtemas:
        cap = _capitalizar_etiqueta(_sin_articulo_inicial(sub))
        if (_tema_util(cap)
                and not (_tema_copia_o_prefijo_titulo(cap, titulos)
                         and not _es_etiqueta_tematica_canonica(cap))):
            return cap
    return 'Hecho informativo'


def generalizar_tema_desde_subtemas(subtemas: Sequence[str], titulos: Optional[Sequence[str]] = None,
                                    contextos: Optional[Sequence[str]] = None) -> str:
    """Nombre más general: frase nominal temática. Título = evidencia, nunca title[:N]."""
    subtemas = [sq(s) for s in (subtemas or []) if sq(s)]
    titulos = [sq(t) for t in (titulos or []) if sq(t)]
    evidencia = ' '.join(list(subtemas) + list(titulos) +
                         [sq(c)[:400] for c in (contextos or []) if c])
    seguro = _frase_segura_desde_familia(subtemas, titulos, contextos)
    if seguro:
        return seguro

    def aceptable(cand: str, exigir_distinto: bool = True) -> bool:
        if not _tema_util(cand):
            return False
        if _tema_copia_o_prefijo_titulo(cand, titulos) and not _es_etiqueta_tematica_canonica(cand):
            return False
        if not tema_frase_natural(cand, titulos=titulos):
            return False
        if exigir_distinto and subtemas and any(
                not _tema_distinto_de_subtema(cand, s) for s in subtemas if s):
            return False
        return True

    candidatos: List[str] = []
    for fuente in list(subtemas):  # nunca recortar el titular
        for cand in _generar_candidatos_frase(fuente):
            if not aceptable(cand):
                continue
            if not cubo_valido(cand, {'temas': []}, True):
                continue
            if subtemas and not any(_tema_es_relevante(cand, s, evidencia) for s in subtemas):
                continue
            candidatos.append(cand)
    if candidatos:
        return max(candidatos, key=lambda c: (_score_tema_candidato(c, subtemas), -len(c.split())))
    relajados = []
    for fuente in list(subtemas):
        np = _np_completa_desde_fuente(fuente, titulos=titulos)
        if _tema_util(np) and aceptable(np, exigir_distinto=False):
            relajados.append(np)
    if relajados:
        distintos = [c for c in relajados
                     if not subtemas or all(_tema_distinto_de_subtema(c, s)
                                            for s in subtemas if s)]
        con_gate_dist = [c for c in distintos if tema_frase_natural(c, titulos=titulos)]
        con_gate_all = [c for c in relajados if tema_frase_natural(c, titulos=titulos)]
        pool = con_gate_dist or con_gate_all or distintos or relajados
        return max(pool, key=lambda c: (_score_tema_candidato(c, subtemas),
                                        -len(c.split())))
    return _tema_minimo_no_vacio(subtemas, titulos, contextos)


# Flags cosméticos que la reparación de inicio preposicional puede ignorar:
# quitar "Para"/"De" del inicio no los introduce; suelen ser falsos positivos
# del gate ("Carnaval 2027": el -al confunde al detector de adjetivos).
_COSMETICOS_REPARACION = {'mash_keywords', 'empieza_por_adjetivo'}


def _reparar_inicio_preposicional(tema: str, titulos: Optional[Sequence[str]] = None) -> str:
    """"Para Carnaval 2027" → "Carnaval 2027"; "De cuidado territorial" → "Cuidado territorial".

    Quita el sintagma preposicional inicial solo si lo que queda es un tema
    válido por sí mismo. Si no, devuelve '' y el llamador sigue su ruta normal.
    """
    toks = sq(tema).split()
    i = 0
    while i < len(toks) and nz(toks[i]) in PREP_INICIO_TEMA:
        i += 1
    if i == 0 or i >= len(toks):
        return ''
    cand = _capitalizar_etiqueta(' '.join(toks[i:]))
    if not _tema_util(cand):
        return ''
    problemas = problemas_calidad_tema(cand, titulos=titulos)
    # Quitar el SP inicial no puede crear un "mash" ni un arranque adjetival
    # nuevos: si solo quedan esos flags cosméticos (p. ej. "Carnaval 2027",
    # donde el -al de "carnaval" confunde al detector de adjetivos), se acepta.
    if any(p not in _COSMETICOS_REPARACION for p in problemas):
        return ''
    if _tema_copia_o_prefijo_titulo(cand, titulos or []):
        return ''
    return cand


def _asegurar_tema_texto(tema, subtemas=None, titulos=None, contextos=None) -> str:
    """Nunca vacío ni prefijo del titular. Gate rechaza → repara o fallback de significado."""
    t = sq(tema)
    titulos = list(titulos or [])
    reparado = _reparar_inicio_preposicional(t, titulos)
    if reparado:
        return reparado
    if (_tema_util(t) and tema_frase_natural(t, titulos=titulos)
            and not (_tema_copia_o_prefijo_titulo(t, titulos)
                     and not _es_etiqueta_tematica_canonica(t))):
        return t
    nuevo = generalizar_tema_desde_subtemas(subtemas or [], titulos, contextos)
    if (_tema_util(nuevo) and tema_frase_natural(nuevo, titulos=titulos)
            and not (_tema_copia_o_prefijo_titulo(nuevo, titulos)
                     and not _es_etiqueta_tematica_canonica(nuevo))):
        return nuevo
    if (_tema_util(nuevo) and tema_frase_natural(nuevo, permitir_vago=True, titulos=titulos)
            and not (_tema_copia_o_prefijo_titulo(nuevo, titulos)
                     and not _es_etiqueta_tematica_canonica(nuevo))):
        return nuevo
    minimo = _tema_minimo_no_vacio(subtemas or [], titulos, contextos)
    if _tema_util(minimo) and not (
            _tema_copia_o_prefijo_titulo(minimo, titulos)
            and not _es_etiqueta_tematica_canonica(minimo)):
        return minimo
    if _tema_util(t) and not _tema_copia_o_prefijo_titulo(t, titulos):
        return t
    return minimo if _tema_util(minimo) else 'Hecho informativo'


def _mejor_candidato_tema(subtemas: Sequence[str], titulos: Sequence[str],
                          contextos: Sequence[str], candidatos: Sequence[str]) -> Optional[str]:
    if not candidatos:
        return None
    from rapidfuzz import fuzz
    evidencia = ' '.join(list(subtemas) + list(titulos or []) +
                         [str(c)[:300] for c in (contextos or [])])
    best, score = None, 0
    for c in candidatos:
        if nz(c) in CUBO_PROHIBIDO or nz(c) in ROTULO_GEN:
            continue
        if not tema_frase_natural(c, titulos=titulos):
            continue
        if _tema_copia_o_prefijo_titulo(c, titulos) and not _es_etiqueta_tematica_canonica(c):
            continue
        if any(not _tema_distinto_de_subtema(c, s) for s in subtemas if s):
            continue
        if not any(_tema_es_relevante(c, s, evidencia) for s in subtemas if s):
            continue
        sc = fuzz.token_set_ratio(nz(c), nz(evidencia))
        if sc > score:
            best, score = c, sc
    return best if best and score >= 45 else None


def prompt_temas_familias(familias: Sequence[dict]) -> str:
    bloques = []
    for f in familias:
        bloques.append(
            'FAMILIA id=%d\nSUBTEMAS:\n%s\nTITULARES:\n%s\nCONTEXTOS:\n%s' % (
                f['id'],
                '\n'.join('- %s' % s for s in f.get('subtemas') or []),
                '\n'.join('- %s' % t for t in (f.get('titulos') or [])[:6]),
                # El contexto (párrafos reales) desambigua mejor que los
                # subtemas solos: el tema debe corresponder a la noticia.
                '\n'.join('- %s' % sq(c)[:400]
                          for c in (f.get('contextos') or [])[:2]),
            )
        )
    buenos = ', '.join('"%s"' % x for x in TEMAS_EJEMPLO_BUENOS)
    malos = ', '.join('"%s"' % x for x in TEMAS_EJEMPLO_MALOS)
    return (
        'Nombras UN TEMA por familia de SUBTEMAS del lote del día.\n'
        'El TEMA debe ser estrictamente MÁS GENERAL que cada uno de los subtemas: '
        'abstrae el asunto común, no repitas ni parafrasees un subtema. '
        'Si la familia tiene un solo subtema, nombra su gran asunto '
        '(p. ej. subtema "Conversatorio sobre Alzheimer" → tema "Salud y bienestar").\n'
        'ESTILO: escribe como un analista senior, no como un robot: español natural, '
        'sobrio y preciso. Prefiere lo concreto ("Empleo juvenil") a lo abstracto '
        '("Fortalecimiento de la empleabilidad juvenil"). Si dudas entre dos opciones, '
        'elige la más corta y clara.\n'
        '%s\n'
        'BIEN (imita esta calidad): %s.\n'
        'MAL (se rechaza siempre): %s.\n'
        'Prohibido recortar la frase y dejar un fragmento, unir keywords, usar siglas sueltas '
        '(escribe "inteligencia artificial", no "IA"), verbos, nombres de persona o rótulos vacíos.\n'
        'Prohibido copiar el titular o usar las primeras palabras del titular como tema.\n'
        '%d a %d palabras. No copies un subtema. Sin "Otros" ni "General".\n'
        'Responde SOLO JSON: {"resultados":[{"id":<familia>,"tema":"..."}]}\n\n'
        % (REGLAS_TEMA, buenos, malos, TEMA_MIN_PAL, TEMA_MAX_PAL)
        + '\n\n'.join(bloques)
    )


def prompt_reparacion_tema(fallos: Sequence[dict]) -> str:
    """Segunda (y última) pasada: reescribe temas que el validador rechazó."""
    detalle = []
    for f in fallos:
        detalle.append(
            'FAMILIA id=%d\nTEMA RECHAZADO: "%s"\nPROBLEMAS: %s\nSUBTEMAS:\n%s\nTITULARES:\n%s'
            % (f['id'], f.get('tema') or '', '; '.join(f.get('problemas') or ['invalido']),
               '\n'.join('- %s' % s for s in f.get('subtemas') or []),
               '\n'.join('- %s' % t for t in (f.get('titulos') or [])[:6]))
        )
    buenos = ', '.join('"%s"' % x for x in TEMAS_EJEMPLO_BUENOS[:6])
    return (
        'Reescribe SOLO estos temas. El resultado debe ser una frase nominal española COMPLETA,\n'
        'un nivel más general que los subtemas, lista para Power BI, con estilo de analista senior:\n'
        'español natural, sobrio y preciso; lo concreto antes que lo abstracto.\n'
        'Ejemplos válidos: %s.\n'
        'No reutilices el texto rechazado. No recortes el núcleo ni el objeto.\n'
        'No copies el titular ni uses las primeras palabras del titular como tema.\n'
        'Si la familia habla de suicidio, usa "Prevención del suicidio".\n'
        'Si habla de IA, escribe "inteligencia artificial", nunca "IA".\n\n'
        % buenos
        + '\n\n'.join(detalle)
        + '\n\nResponde SOLO JSON: {"resultados":[{"id":<familia>,"tema":"..."}]}'
    )


def _aceptar_tema_familia(nombre: Optional[str], fam: dict) -> Optional[str]:
    titulos = fam.get('titulos') or []
    nombre = cubo_valido(nombre, {'temas': []}, True)
    if not nombre or not tema_frase_natural(nombre, titulos=titulos):
        return None
    if _tema_copia_o_prefijo_titulo(nombre, titulos) and not _es_etiqueta_tematica_canonica(nombre):
        return None
    subs = [s for s in (fam.get('subtemas') or []) if s]
    if any(not _tema_distinto_de_subtema(nombre, s) for s in subs):
        return None
    evidencia = ' '.join(subs + list(titulos))
    if subs and not all(_tema_es_relevante(nombre, s, evidencia) for s in subs[:4]):
        return None
    return nombre


def _propuestas_tema_llm(txt: str, familias: Sequence[dict]) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Devuelve (aceptados, crudos) para poder reparar lo que el gate tumba."""
    data = _json_loose(txt) or {}
    aceptados: Dict[int, str] = {}
    crudos: Dict[int, str] = {}
    for r in data.get('resultados', []) or []:
        try:
            fid = int(r.get('id'))
        except Exception:
            continue
        fam = next((f for f in familias if f['id'] == fid), None)
        if not fam:
            continue
        crudo = sq(r.get('tema'))
        if crudo:
            crudos[fid] = crudo
        aceptado = _aceptar_tema_familia(crudo, fam)
        if aceptado:
            aceptados[fid] = aceptado
    return aceptados, crudos


def nombrar_familias_tema(cfg: dict, familias: Sequence[dict],
                          candidatos: Optional[Sequence[str]] = None,
                          uso: Optional[dict] = None) -> Dict[int, str]:
    """Nombra cada familia: LLM → gate → una reparación → frase segura. Nunca basura."""
    out: Dict[int, str] = {}
    if not familias:
        return out
    sys_tema = (
        'Eres un analista senior de medios en Colombia con excelente redacción. '
        'Escribes UN tema por familia: una frase nominal española clara, sobria y '
        'COMPLETA — la que pondrías en un dashboard ejecutivo. Nada de jerga, '
        'nada de fragmentos, nada de adornos burocráticos. JSON.'
    )
    if cfg.get('api_key'):
        crudos: Dict[int, str] = {}
        try:
            txt = llamar_llm(cfg, [
                {'role': 'system', 'content': sys_tema},
                {'role': 'user', 'content': prompt_temas_familias(familias)},
            ], uso=uso)
            aceptados, crudos = _propuestas_tema_llm(txt, familias)
            out.update(aceptados)
        except Exception:
            pass
        fallos = []
        for fam in familias:
            if fam['id'] in out:
                continue
            rechazado = crudos.get(fam['id'], '')
            fallos.append({
                'id': fam['id'],
                'tema': rechazado,
                'problemas': problemas_calidad_tema(rechazado) or ['no_supero_el_gate'],
                'subtemas': fam.get('subtemas') or [],
                'titulos': fam.get('titulos') or [],
            })
        if fallos:
            try:
                txt = llamar_llm(cfg, [
                    {'role': 'system', 'content': sys_tema},
                    {'role': 'user', 'content': prompt_reparacion_tema(fallos)},
                ], uso=uso)
                aceptados, _ = _propuestas_tema_llm(txt, familias)
                out.update(aceptados)
            except Exception:
                pass
    for fam in familias:
        tits = fam.get('titulos') or []
        if fam['id'] in out and tema_frase_natural(out[fam['id']], titulos=tits):
            continue
        cand = _mejor_candidato_tema(fam.get('subtemas') or [], tits,
                                     fam.get('contextos') or [], candidatos or [])
        if cand and tema_frase_natural(cand, titulos=tits):
            out[fam['id']] = cand
            continue
        seguro = generalizar_tema_desde_subtemas(
            fam.get('subtemas') or [], fam.get('titulos'), fam.get('contextos'))
        if seguro and tema_frase_natural(seguro, titulos=tits):
            out[fam['id']] = seguro
        elif cand and tema_frase_natural(cand, permitir_vago=True, titulos=tits):
            out[fam['id']] = cand
        else:
            # Rechazo del gate ≠ vacío: fallback temático, nunca title[:N].
            out[fam['id']] = _asegurar_tema_texto(
                seguro or cand or '',
                fam.get('subtemas') or [],
                fam.get('titulos'),
                fam.get('contextos'),
            )
    return out


def forzar_un_tema_por_subtema(temas: Dict[int, str], etiquetas: Dict[int, dict]) -> int:
    """Invariante: un subtema canónico ⇒ exactamente un tema en el lote."""
    por_sub = defaultdict(list)
    for gid, e in etiquetas.items():
        if gid not in temas:
            continue
        por_sub[nz(e.get('sub_tema'))].append(gid)
    cambios = 0
    for gids in por_sub.values():
        usados = [temas[g] for g in gids if temas.get(g)]
        if len({nz(t) for t in usados}) <= 1:
            continue
        ganador_nz = Counter(nz(t) for t in usados).most_common(1)[0][0]
        display = next(t for t in usados if nz(t) == ganador_nz)
        for g in gids:
            if nz(temas.get(g, '')) != ganador_nz:
                temas[g] = display
                cambios += 1
    return cambios


def fusionar_temas_casi_identicos(temas: Dict[int, str], origen: Dict[int, str],
                                  etiquetas: Dict[int, dict],
                                  umbral: int = 90) -> int:
    """Fusiona temas con nombre casi idéntico ('Impacto de educación' ~
    'Impacto de inteligencia artificial en educación').

    El clustering agrupa por asunto; cuando dos familias vecinas quedaron
    separadas pero el LLM les dio el mismo nombre con otra redacción, se
    unifican. Gana el nombre de la familia más grande; en empate, el más
    corto (más general). Conservador: no toca CUBO_PROHIBIDO ni fusiones
    que violen que el tema sea más general que los subtemas absorbidos.
    """
    from rapidfuzz import fuzz
    nombres = sorted({nz(t) for t in temas.values() if nz(t) and nz(t) not in CUBO_PROHIBIDO})
    if len(nombres) < 2:
        return 0
    par = {t: t for t in nombres}

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for i in range(len(nombres)):
        for j in range(i + 1, len(nombres)):
            a, b = nombres[i], nombres[j]
            if a == b:
                continue
            if fuzz.token_set_ratio(a, b) >= umbral:
                ra, rb = find(a), find(b)
                if ra != rb:
                    par[max(ra, rb)] = min(ra, rb)
    buckets = defaultdict(list)
    for t in nombres:
        buckets[find(t)].append(t)
    cambios = 0
    for grupo in buckets.values():
        if len(grupo) < 2:
            continue
        miembros = [g for g in temas if nz(temas[g]) in set(grupo)]
        conteo = Counter(nz(temas[g]) for g in miembros)
        # Gana la familia más grande; empate → nombre más corto (más general).
        ganador_nz = sorted(grupo, key=lambda t: (-conteo[t], len(t.split()), t))[0]
        display = next(temas[g] for g in miembros if nz(temas[g]) == ganador_nz)
        perdedores = [g for g in miembros if nz(temas[g]) != ganador_nz]
        # No absorber si el ganador no es más general que algún subtema ajeno.
        ok = True
        for g in perdedores:
            s = (etiquetas.get(g) or {}).get('sub_tema') or ''
            if s and not _tema_distinto_de_subtema(display, s):
                ok = False
                break
        if not ok:
            continue
        for g in perdedores:
            temas[g] = display
            origen[g] = 'fusion_tema:%s' % ganador_nz
            cambios += 1
    return cambios


def _confianza_jev(answer: dict) -> float:
    if not isinstance(answer, dict):
        return 0.0
    for k in ('confidence', 'confianza', 'score', 'probability'):
        try:
            v = float(answer.get(k))
            if v > 1:
                v = v / 100.0
            return max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            continue
    if any(k in answer for k in ('boolean', 'choice', 'value')):
        return 0.8
    return 0.0


def _bool_jev(answer: dict) -> Optional[bool]:
    if not isinstance(answer, dict):
        return None
    if 'boolean' in answer:
        return bool(answer.get('boolean'))
    ch = str(answer.get('choice') or answer.get('value') or '').strip().lower()
    if ch in ('true', 'si', 'sí', 'yes', '1'):
        return True
    if ch in ('false', 'no', '0'):
        return False
    return None


def _tema_con_jev(cfg: dict, tema: str, sub_tema: str, titulo: str,
                  contexto: str) -> Optional[dict]:
    """Corrector Jev del TEMA (boolean/choice). No genera subtemas ni toca el tono."""
    api_key = (cfg.get('typesafe_api_key') or '').strip()
    if not api_key:
        return None
    payload = {
        'model': cfg.get('typesafe_model') or 'jev-latest',
        'state': {
            'tema': tema,
            'subtema': sub_tema,
            'titular': sq(titulo or '')[:500],
            'contexto': sq(contexto or '')[:4000],
        },
        'questions': {
            'cubre': {
                'type': 'boolean',
                'instructions': (
                    '¿El TEMA es una frase nominal española COMPLETA, apta para Power BI, '
                    'que cubre el SUBTEMA y el titular? False si es un fragmento, verbo, '
                    'nombre de persona, sigla suelta (IA), pila de adjetivos, mash de '
                    'keywords o rótulo vacío (Reunión de expertos, Ayuda en salud).'
                ),
            },
            'demasiado_especifico': {
                'type': 'boolean',
                'instructions': (
                    '¿El TEMA es demasiado específico o casi igual al SUBTEMA? '
                    'True si copia el hecho, nombra a una persona o no es más general.'
                ),
            },
        },
    }
    try:
        response = requests.post(
            cfg.get('typesafe_url') or JEV_URL_DEFECTO,
            headers={'Authorization': 'Bearer %s' % api_key, 'Content-Type': 'application/json'},
            json=payload,
            timeout=int(cfg.get('typesafe_timeout', 60)),
        )
        if response.status_code != 200:
            raise RuntimeError('HTTP %s: %s' % (response.status_code, response.text[:250]))
        answers = response.json().get('answers') or {}
        cubre_a = answers.get('cubre') or {}
        spec_a = answers.get('demasiado_especifico') or {}
        return {
            'cubre': _bool_jev(cubre_a),
            'demasiado_especifico': _bool_jev(spec_a),
            'confianza_cubre': _confianza_jev(cubre_a),
            'confianza_especifico': _confianza_jev(spec_a),
        }
    except Exception as exc:
        _ULTIMO_RESUMEN.setdefault('errores_jev', []).append(str(exc)[:200])
        return None


def corregir_temas_con_jev(cfg: dict, grupos: Sequence[dict], etiquetas: Dict[int, dict],
                           temas: Dict[int, str]) -> List[int]:
    """Alta confianza ⇒ corrige el tema. Baja confianza ⇒ deja y marca revisión.

    Jev no genera subtemas y no reemplaza el pipeline de tono.
    """
    if not (cfg.get('typesafe_api_key') or '').strip():
        return []
    por_sub = {}
    por_gid = {g['grupo']: g for g in grupos}
    for g in grupos:
        e = etiquetas.get(g['grupo']) or {}
        clave = nz(e.get('sub_tema'))
        if clave and clave not in por_sub:
            por_sub[clave] = g['grupo']
    umbral = float(cfg.get('jev_confianza_min', 0.75) or 0.75)
    revisar, corregidos = [], []
    for gid in por_sub.values():
        tema = temas.get(gid)
        e = etiquetas.get(gid) or {}
        g = por_gid.get(gid) or {}
        if not _tema_util(tema):
            lleno = _asegurar_tema_texto(
                '', [e.get('sub_tema', '')], [g.get('titulo')],
                [g.get('contexto') or g.get('texto')])
            temas[gid] = lleno
            tema = lleno
        if not tema_frase_natural(tema, titulos=[g.get('titulo')]):
            malo, conf = True, 1.0
            ver = None
        else:
            ver = _tema_con_jev(cfg, tema, e.get('sub_tema', ''), g.get('titulo'),
                                g.get('contexto') or g.get('texto'))
            if not ver:
                if not _tema_util(temas.get(gid)):
                    temas[gid] = _asegurar_tema_texto(
                        temas.get(gid), [e.get('sub_tema', '')], [g.get('titulo')],
                        [g.get('contexto') or g.get('texto')])
                continue
            cubre, spec = ver.get('cubre'), ver.get('demasiado_especifico')
            conf = max(ver.get('confianza_cubre') or 0.0, ver.get('confianza_especifico') or 0.0)
            malo = (cubre is False) or (spec is True)
        if not malo:
            continue
        if conf < umbral:
            if not _tema_util(temas.get(gid)):
                temas[gid] = _asegurar_tema_texto(
                    temas.get(gid), [e.get('sub_tema', '')], [g.get('titulo')],
                    [g.get('contexto') or g.get('texto')])
            revisar.append(gid)
            continue
        nuevo = generalizar_tema_desde_subtemas(
            [e.get('sub_tema', '')], [g.get('titulo')], [g.get('contexto') or g.get('texto')])
        if (nuevo and tema_frase_natural(nuevo, titulos=[g.get('titulo')])
                and _tema_distinto_de_subtema(nuevo, e.get('sub_tema', ''))):
            clave = nz(e.get('sub_tema'))
            for g2 in grupos:
                e2 = etiquetas.get(g2['grupo']) or {}
                if nz(e2.get('sub_tema')) == clave:
                    temas[g2['grupo']] = nuevo
            corregidos.append(gid)
        else:
            if not _tema_util(temas.get(gid)):
                temas[gid] = _asegurar_tema_texto(
                    nuevo or temas.get(gid), [e.get('sub_tema', '')],
                    [g.get('titulo')], [g.get('contexto') or g.get('texto')])
            revisar.append(gid)
    if revisar:
        _ULTIMO_RESUMEN['temas_para_revision'] = revisar
    if corregidos:
        _ULTIMO_RESUMEN['temas_corregidos_por_jev'] = corregidos
    return corregidos


def asignar_temas(cfg: dict, grupos: List[dict], etiquetas: Dict[int, dict], tax: dict,
                  progress: Optional[Callable] = None,
                  uso: Optional[dict] = None) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Asigna temas BOTTOM-UP en este lote: un subtema canónico ⇒ un tema.

    `tax['temas']` solo aporta nombres candidatos (opcional). No hay memoria
    entre corridas ni clasificación independiente por fila o por grupo.
    """
    temas, origen = {}, {}
    if not grupos:
        return temas, origen
    if progress:
        progress(93, 'Agrupando subtemas del lote en temas…')
    candidatos = [t for t in list((tax or {}).get('temas') or [])
                  if nz(t) not in CUBO_PROHIBIDO]
    por_sub = defaultdict(list)
    meta_sub: Dict[str, dict] = {}
    for g in grupos:
        e = etiquetas.get(g['grupo']) or {}
        sub = e.get('sub_tema') or ''
        clave = nz(sub)
        por_sub[clave].append(g['grupo'])
        if clave not in meta_sub:
            meta_sub[clave] = {'sub_tema': sub, 'titulos': [], 'contextos': []}
        meta_sub[clave]['titulos'].append(g.get('titulo') or '')
        meta_sub[clave]['contextos'].append(
            '%s\n%s' % (g.get('contexto') or '', g.get('texto') or ''))

    items = []
    for clave, meta in meta_sub.items():
        if not clave:
            continue
        items.append({
            'sub_tema': meta['sub_tema'],
            'evidencia': ' '.join([meta['sub_tema']] + meta['titulos'][:4]),
            'clave': clave,
        })
    familias_items = cluster_familias_subtema(items,
                                              excluir_stems=_stems_entidad(cfg or {}))
    familias = []
    for i, miembros in enumerate(familias_items, 1):
        subs, titulos, contextos, gids = [], [], [], []
        for it in miembros:
            m = meta_sub[it['clave']]
            subs.append(m['sub_tema'])
            titulos.extend(m['titulos'])
            contextos.extend(m['contextos'])
            gids.extend(por_sub[it['clave']])
        familias.append({
            'id': i, 'subtemas': subs, 'titulos': titulos,
            'contextos': contextos, 'gids': gids,
        })
    nombres = nombrar_familias_tema(cfg or {}, familias, candidatos=candidatos, uso=uso)
    grupo_por_id = {g['grupo']: g for g in grupos}
    # Sin agrupamiento forzado: el tema de la familia solo se asigna a los
    # miembros que sí describe; los demás se nombran como singletons, con
    # tema propio generado desde SU subtema/titular/contexto.
    sueltos: List[dict] = []
    for fam in familias:
        nombre = nombres.get(fam['id']) or generalizar_tema_desde_subtemas(
            fam['subtemas'], fam['titulos'], fam['contextos'])
        if not nombre or not tema_frase_natural(nombre, titulos=fam['titulos']):
            nombre = generalizar_tema_desde_subtemas(
                fam['subtemas'], fam['titulos'], fam['contextos'])
        # Rechazo del gate ≠ vacío: nunca se descarta dejando la etiqueta en blanco.
        nombre = _asegurar_tema_texto(
            nombre, fam['subtemas'], fam['titulos'], fam['contextos'])
        # Sin agrupamiento forzado: el tema de la familia se asigna a cada
        # miembro solo si describe SU noticia (verificado contra su subtema
        # y su evidencia). El miembro ajeno se nombra aparte con tema
        # propio: el tema debe corresponder con el subtema y la noticia.
        for gid in fam['gids']:
            e = etiquetas.get(gid) or {}
            s = e.get('sub_tema') or ''
            g = grupo_por_id.get(gid) or {}
            ev = ' '.join([s, g.get('titulo') or '', g.get('contexto') or '',
                           g.get('texto') or ''])
            if _tema_relevante_para_miembro(nombre, s, ev):
                temas[gid] = nombre
                origen[gid] = 'familia:%d' % fam['id']
                continue
            sueltos.append({
                'id': 900000 + len(sueltos),
                'gid': gid,
                'subtemas': [s],
                'titulos': [g.get('titulo') or ''],
                'contextos': [g.get('contexto') or g.get('texto') or ''],
            })
        continue
    if sueltos:
        # Una sola pasada (un solo llamado LLM si hay api_key) para todos.
        nombres_sueltos = nombrar_familias_tema(cfg or {}, sueltos,
                                                candidatos=candidatos, uso=uso)
        for fam_uno in sueltos:
            gid = fam_uno['gid']
            tit = fam_uno['titulos']
            ctx = fam_uno['contextos']
            sub = fam_uno['subtemas']
            propio = nombres_sueltos.get(fam_uno['id']) or ''
            if not propio or not tema_frase_natural(propio, titulos=tit):
                propio = generalizar_tema_desde_subtemas(sub, tit, ctx)
            propio = _asegurar_tema_texto(
                propio if _tema_util(propio) else '', sub, tit, ctx)
            temas[gid] = propio
            origen[gid] = 'tema_propio_sin_agrupar'

    for gid, e in etiquetas.items():
        t = temas.get(gid)
        s = e.get('sub_tema') or ''
        g = next((x for x in grupos if x['grupo'] == gid), {})
        if t and (not tema_frase_natural(t, titulos=[g.get('titulo')])
                  or not _tema_distinto_de_subtema(t, s)):
            nuevo = generalizar_tema_desde_subtemas(
                [s], [g.get('titulo')], [g.get('contexto') or g.get('texto')])
            temas[gid] = _asegurar_tema_texto(
                nuevo if _tema_util(nuevo) else t,
                [s], [g.get('titulo')], [g.get('contexto') or g.get('texto')])
            origen[gid] = 'guarda_generalidad'
        elif not _tema_util(t):
            temas[gid] = _asegurar_tema_texto(
                t, [s], [g.get('titulo')], [g.get('contexto') or g.get('texto')])
            origen[gid] = 'fallback_lote'

    # Último recurso: si el tema sigue sin ser más general que el subtema
    # (p. ej. familia de un solo miembro donde el determinista se rinde y
    # repite el subtema), se pide UNA generalización dirigida al LLM.
    pendientes = []
    for gid, e in etiquetas.items():
        t = temas.get(gid)
        s = e.get('sub_tema') or ''
        if t and s and not _tema_distinto_de_subtema(t, s):
            g = next((x for x in grupos if x['grupo'] == gid), {})
            pendientes.append({
                'id': gid, 'tema': t, 'subtemas': [s],
                'titulos': [g.get('titulo') or ''],
                'problemas': ['tema_igual_o_parafrasis_del_subtema'],
            })
    if pendientes and (cfg or {}).get('api_key'):
        try:
            txt = llamar_llm(cfg, [
                {'role': 'system',
                 'content': ('Eres analista de medios en Colombia. Generalizas etiquetas: '
                             'el TEMA debe abarcar el subtema como categoría amplia, '
                             'nunca repetirlo ni parafrasearlo. JSON.')},
                {'role': 'user', 'content': prompt_reparacion_tema(pendientes)},
            ], uso=uso)
            fams = [{'id': p['id'], 'subtemas': p['subtemas'],
                     'titulos': p['titulos']} for p in pendientes]
            aceptados, _ = _propuestas_tema_llm(txt, fams)
            for gid, nuevo in aceptados.items():
                temas[gid] = nuevo
                origen[gid] = 'llm_generaliza'
        except Exception:
            pass

    cambios = forzar_un_tema_por_subtema(temas, etiquetas)
    cambios += fusionar_temas_casi_identicos(temas, origen, etiquetas)
    _ULTIMO_RESUMEN['cubos_nuevos'] = sorted(set(temas.values()) - set(candidatos))
    _ULTIMO_RESUMEN['temas_por_llm'] = sum(1 for v in origen.values() if v == 'llm')
    _ULTIMO_RESUMEN['temas_por_regla'] = sum(1 for v in origen.values() if str(v).startswith('regla'))
    _ULTIMO_RESUMEN['familias_tema'] = len(familias)
    _ULTIMO_RESUMEN['temas_unificados_por_subtema'] = cambios
    if progress:
        progress(94, 'Temas del lote: %d familias' % len(familias))
    return temas, origen


def _texto_para_pkl(grupo: dict, rows: List[dict]) -> str:
    """Texto que ve el PKL: contexto de marca, o título + cuerpo del grupo."""
    idxs = grupo.get('idxs') or []
    if idxs:
        ctx = str(rows[idxs[0]].get('Contexto analizado') or '').strip()
        if ctx and ctx not in ('-', 'nan', 'None'):
            return ctx
    for key in ('contexto', 'texto', 'titulo'):
        val = str(grupo.get(key) or '').strip()
        if val and val not in ('-', 'nan', 'None'):
            return val[:800]
    return ''


def aplicar_pkl_del_cliente(
    grupos: List[dict],
    rows: List[dict],
    etiquetas: Dict[int, dict],
    temas: Dict[int, str],
    origen: Dict[int, str],
    tone_model=None,
    theme_model=None,
) -> Dict[str, int]:
    """Aplica PKL de tono y/o tema. Gana sobre LLM/lote. Nunca toca el subtema.

    Las clases del PKL de tema NO se pasan por el quality-gate del lote
    (`tema_frase_natural`, `forzar_un_tema_por_subtema`, `_asegurar_tema_texto`):
    ese gate nombra frases libres; el PKL trae las clases del cliente.
    """
    applied = {'tono': 0, 'tema': 0}
    if tone_model is None and theme_model is None:
        return applied
    from pkl_classifier import _safe_predict, format_theme_label, map_tone_label
    for g in grupos:
        gid = g['grupo']
        e = etiquetas.setdefault(gid, {})
        ctx = _texto_para_pkl(g, rows)
        if tone_model is not None:
            p_tone = map_tone_label(_safe_predict(tone_model, [ctx], 'tono')[0])
            if p_tone:
                e['tono'] = p_tone
                applied['tono'] += 1
        if theme_model is not None:
            p_theme = format_theme_label(_safe_predict(theme_model, [ctx], 'tema')[0])
            if p_theme:
                temas[gid] = p_theme
                origen[gid] = 'pkl'
                applied['tema'] += 1
    return applied


def volcar_analisis_en_filas(rows: List[dict], mapa: Dict[int, int],
                             etiquetas: Dict[int, dict], temas: Dict[int, str],
                             preservar_tema: bool = False,
                             incluir_tema: bool = True) -> List[dict]:
    """Propaga etiqueta de GRUPO. No reasigna tema por fila.

    `preservar_tema=True` (PKL de tema): solo rellena si la celda quedó vacía.
    No reescribe clases del cliente con el gate de frases del lote.
    `incluir_tema=False` (v4.8): deja Tema_IA vacío y omite el fallback, porque
    la columna no se exporta.
    """
    for i, row in enumerate(rows):
        if row.get('is_duplicate'):
            row['Tono_IA'] = 'Duplicada'
            row['Tema_IA'] = '-' if incluir_tema else ''
            row['Subtema_IA'] = '-'
            continue
        gid = mapa.get(i)
        e = etiquetas.get(gid, {}) if gid else {}
        row['Tono_IA'] = e.get('tono') or 'Neutro'
        row['Tema_IA'] = (temas.get(gid) if gid is not None else '') if incluir_tema else ''
        row['Subtema_IA'] = e.get('sub_tema') or 'Hecho informativo'
        if not incluir_tema:
            continue
        falta = not _tema_util(row['Tema_IA'])
        copia_titulo = _tema_copia_o_prefijo_titulo(row['Tema_IA'], [_titulo_fila(row, {})])
        if falta or (copia_titulo and not preservar_tema):
            row['Tema_IA'] = _asegurar_tema_texto(
                row['Tema_IA'],
                [row['Subtema_IA']],
                [_titulo_fila(row, {})],
                [row.get('Contexto analizado') or row.get('CuerpoEs') or ''],
            )
    return rows


# ============================================================================
# 8. Entrada compatible con el pipeline
# ============================================================================
def enrich_rows_with_ai(
    rows: List[dict],
    km: dict,
    brand: str,
    aliases: List[str],
    api_key: str,
    model: str = MODELO_DEFECTO,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    tone_model=None,
    theme_model=None,
    extra: Optional[dict] = None,
) -> List[dict]:
    """Llena 'Contexto analizado', 'Tono_IA', 'Tema_IA' y 'Subtema_IA' con el motor de tono/tema/subtema."""
    extra = dict(extra or {})
    cfg = {
        'brand': brand,
        'aliases': list(aliases or []),
        'voceros': list(extra.get('voceros') or []),
        'criterio': extra.get('criterio') or list(CRITERIOS_TONO)[0],
        'criterio_texto': (extra.get('criterio_texto') or '').strip(),
        'api_key': api_key,
        'typesafe_api_key': extra.get('typesafe_api_key') or '',
        'typesafe_model': extra.get('typesafe_model') or 'jev-latest',
        'typesafe_url': extra.get('typesafe_url') or JEV_URL_DEFECTO,
        'typesafe_timeout': int(extra.get('typesafe_timeout', 60)),
        'model': model or MODELO_DEFECTO,
        'base_url': extra.get('base_url') or BASE_URL_DEFECTO,
        'timeout': int(extra.get('timeout', 120)),
    }
    modo_tax = extra.get('taxonomia')
    candidatos_tax: List[str] = []
    if isinstance(modo_tax, dict):
        candidatos_tax = list(modo_tax.get('temas') or [])
    elif modo_tax and not str(modo_tax).lower().startswith('autom'):
        candidatos_tax = list(taxonomia_por_nombre(modo_tax).get('temas') or [])
    candidatos_tax = [c for c in candidatos_tax if nz(c) not in CUBO_PROHIBIDO]
    tam_lote = int(extra.get('tam_lote') or TAM_LOTE_DEFECTO)
    workers = int(extra.get('workers') or WORKERS_DEFECTO)
    votos = int(extra.get('votos') or 2)
    umbral_titulo = int(extra.get('umbral_titulo') or UMBRAL_TITULO_DEFECTO)
    umbral_cuerpo = int(extra.get('umbral_cuerpo') or UMBRAL_CUERPO_DEFECTO)
    # v4.8: el cliente puede pedir solo tono + subtema. Con incluir_tema=False se
    # omite por completo la etapa de asignación de temas (asignar_temas,
    # corrección con Jev y PKL de tema) y el Excel sale sin la columna Tema_IA.
    incluir_tema = bool(extra.get('incluir_tema', True))
    # v4.13: el PKL de tema del cliente manda. La clasificación es local (sin
    # llamadas LLM ni demora), así que siempre se aplica aunque el checkbox
    # "Generar columna Tema_IA" venga desmarcado.
    if theme_model is not None:
        incluir_tema = True
    progreso = progress_callback or (lambda pct, msg: None)

    # --- contexto de marca (funcion existente, se conserva para la columna de auditoria) ---
    progreso(71, 'Extrayendo contexto de la marca y sus variantes…')
    for row in rows:
        if row.get('is_duplicate'):
            row['Contexto analizado'] = '-'
        else:
            ctx = _contexto_exacto_marca(
                _texto_fila(row, km), _titulo_fila(row, km), brand, aliases,
                voceros=cfg.get('voceros') or [],
                tipo_medio=str(row.get(km.get('tipodemedio', 'Tipo de Medio'), '')
                               or ''))
            # v4.16: si el extracto es solo la mención (fragmento), completar
            # con titular + resumen en vez de dejar "Edwin Bernal, director…".
            row['Contexto analizado'] = _contexto_minimo_util(
                ctx, _titulo_fila(row, km), _texto_fila(row, km))

    # --- agrupacion ---
    progreso(73, 'Agrupando notas equivalentes…')
    grupos, mapa = construir_grupos(rows, km, umbral_titulo, umbral_cuerpo)
    for g in grupos:
        contexto = [str(rows[i].get('Contexto analizado') or '') for i in g.get('idxs', [])]
        g['contexto_marca'] = '\n\n'.join(dict.fromkeys(x for x in contexto if x and x != '-'))[:12000]
    progreso(75, '%d grupos (notas equivalentes comparten etiqueta)' % len(grupos))

    # --- etiquetado ---
    # `uso` acumula tokens de TODAS las llamadas OpenAI de la corrida
    # (etiquetado + reparaciones + temas) para la tarjeta de costo aprox.
    uso = {'input': 0, 'output': 0, 'llamadas': 0}
    etiquetas = etiquetar_grupos(cfg, grupos, progreso, tam_lote=tam_lote, workers=workers,
                                 votos=votos, uso=uso)
    cambios = canonizar_subtemas(etiquetas)
    extra_uni = unificar_subtemas_noticias_similares(grupos, etiquetas)
    # v4.14: pase final entre lotes (1 llamada LLM): caza paráfrasis que la
    # canonización determinista no ve («Apertura…» vs «Inauguración…»).
    # Corre antes de las guardas de tono para que el voto por subtema use
    # los subtemas ya unificados.
    extra_llm = unificar_subtemas_llm(cfg, grupos, etiquetas, uso=uso)
    # v4.18: repara etiquetas cruzadas del modelo (el subtema de un grupo
    # describe la noticia de otro). Corre antes del voto de tono por subtema.
    ajenos = reparar_subtemas_ajenos(grupos, etiquetas)
    if ajenos:
        _ULTIMO_RESUMEN['subtemas_ajenos_reparados'] = ajenos
    # v4.20: mismo hecho por ancla de persona + evento compatible («Estado de
    # salud de Yamid Amat» = «Hospitalización de Yamid Amat» = «Yamid Amat en
    # UCI»; el nacimiento de Gael = el ingreso de Lina Tejeiro). Corre tras
    # las reparaciones y antes del voto de tono.
    extra_ancla = unificar_hecho_por_ancla(
        grupos, etiquetas, brand, aliases, voceros=cfg.get('voceros') or [])
    if extra_ancla:
        _ULTIMO_RESUMEN['subtemas_unificados_por_ancla'] = extra_ancla
    if (cambios or extra_uni or extra_llm or ajenos or extra_ancla) and progress_callback:
        progreso(93, 'Sub-temas unificados: %d' % (cambios + extra_uni + extra_llm + ajenos + extra_ancla))

    # Con PKL de tono el modelo del cliente es la autoridad: no se aplica la
    # guarda LLM (degradar Negativo / subir a Positivo) porque pisaría el PKL.
    if tone_model is None:
        # El voto por hecho corre ANTES que las guardas deterministas: las
        # reglas de criterio del cliente (guarda positiva, tragedia, crítica
        # con respuesta...) tienen la última palabra y el voto no puede
        # deshacerlas. Caso real Unisimón: la guarda detectaba Positivo en
        # «La Universidad de Atalaya» y el voto posterior lo revertía a
        # Neutro por mayoría del subtema ajeno.
        unificados = unificar_tono_mismo_hecho(grupos, etiquetas)
        if unificados:
            _ULTIMO_RESUMEN['tono_unificado_mismo_hecho'] = unificados
        corregidos = aplicar_guarda_tono(grupos, etiquetas, brand, aliases)
        # Crítica con respuesta de la marca: la información se equilibra → Neutro.
        equilibrio = aplicar_regla_critica_con_respuesta(
            grupos, etiquetas, brand, aliases, voceros=cfg.get('voceros') or [])
        if equilibrio:
            _ULTIMO_RESUMEN['tono_critica_con_respuesta'] = equilibrio
            if progress_callback:
                progreso(93, 'Crítica con respuesta: %d Negativos pasaron a Neutro'
                         % len(equilibrio))
        # v4.16: el Negativo debe estar dirigido a la marca; la mención
        # incidental no sostiene un Negativo (caso fotomultas/Cotelco).
        sin_blanco = aplicar_regla_negativo_sin_blanco(
            grupos, etiquetas, brand, aliases, voceros=cfg.get('voceros') or [])
        if sin_blanco:
            _ULTIMO_RESUMEN['tono_negativo_sin_blanco'] = sin_blanco
            if progress_callback:
                progreso(93, 'Negativo sin blanco: %d pasaron a Neutro' % len(sin_blanco))
        positivos = aplicar_guarda_positiva(grupos, etiquetas, brand, aliases,
                                            voceros=cfg.get('voceros') or [])
        tragedia = aplicar_regla_tragedia(grupos, etiquetas, brand, aliases,
                                          voceros=cfg.get('voceros') or [])
        if tragedia:
            _ULTIMO_RESUMEN['tono_tragedia_a_neutro'] = tragedia
            if progress_callback:
                progreso(93, 'Regla tragedia: %d Positivos con experto citado pasaron a Neutro'
                         % len(tragedia))
        # v4.20: el Positivo también se evalúa hacia la marca: una mención
        # incidental en una noticia positiva no es un Positivo (espejo de la
        # regla v4.16 para el Negativo).
        incidental = aplicar_regla_positivo_incidental(
            grupos, etiquetas, brand, aliases, voceros=cfg.get('voceros') or [])
        if incidental:
            _ULTIMO_RESUMEN['tono_positivo_incidental'] = incidental
            if progress_callback:
                progreso(93, 'Positivo incidental: %d pasaron a Neutro' % len(incidental))
        # v4.20: los Neutros que bajaron las guardas deterministas son
        # "pegajosos": el voto final no puede revertirlos (crítica con
        # respuesta equilibrada, tragedia con experto de la casa, Negativo sin
        # blanco dirigido ni señalamiento real, Positivo incidental).
        for gid in (set(corregidos) | set(equilibrio) | set(sin_blanco)
                    | set(tragedia) | set(incidental)):
            e = etiquetas.get(gid)
            if e is not None:
                e.setdefault('flags', {})['neutro_pegajoso'] = True
        # v4.20: voto FINAL de tono dentro de cada subtema ya unificado.
        # Reconcilia las divisiones que las guardas dejan dentro del mismo
        # hecho («Estado de salud de Yamid Amat» 194 Positivo / 67 Neutro)
        # sin deshacer ninguna regla superior.
        voto_final = voto_final_tono_por_subtema(grupos, etiquetas)
        if voto_final:
            _ULTIMO_RESUMEN['tono_voto_final_por_subtema'] = voto_final
            if progress_callback:
                progreso(93, 'Tono final unificado por subtema: %d' % voto_final)
        if positivos:
            _ULTIMO_RESUMEN['tono_corregido_positivo'] = positivos
            _ULTIMO_RESUMEN['tono_subido_por_guarda'] = positivos
        if corregidos:
            _ULTIMO_RESUMEN['tono_corregido_por_guarda'] = corregidos
            if progress_callback:
                progreso(93, 'Guarda del tono: %d Negativos sin señalamiento pasaron a Neutro' % len(corregidos))

    # --- tema: PKL del cliente = clases del modelo; si no hay PKL, bottom-up de ESTE lote ---
    # v4.8: con incluir_tema=False se salta toda la etapa (ni LLM de temas, ni
    # Jev, ni PKL de tema). Ahorra las llamadas secuenciales de nombrar_familias_tema.
    temas: Dict[int, str] = {}
    origen: Dict[int, str] = {}
    if incluir_tema and theme_model is None:
        tax_lote = {'temas': candidatos_tax, 'reglas': derivar_reglas(candidatos_tax) if candidatos_tax else []}
        temas, origen = asignar_temas(cfg, grupos, etiquetas, tax_lote, progreso, uso=uso)
        corregir_temas_con_jev(cfg, grupos, etiquetas, temas)

    # PKL gana sobre LLM/lote. NUNCA reemplaza el subtema. El gate de frases
    # del lote no reescribe las clases del cliente (antes las sustituía).
    pkl_counts = aplicar_pkl_del_cliente(
        grupos, rows, etiquetas, temas, origen,
        tone_model=tone_model, theme_model=theme_model if incluir_tema else None,
    )

    volcar_analisis_en_filas(
        rows, mapa, etiquetas, temas,
        preservar_tema=(theme_model is not None) and incluir_tema,
        incluir_tema=incluir_tema,
    )

    temas_lote: List[str] = []
    vistos = set()
    for t in temas.values():
        k = nz(t)
        if t and k not in vistos:
            vistos.add(k)
            temas_lote.append(t)
    _ULTIMO_RESUMEN['taxonomia'] = temas_lote
    if not incluir_tema:
        # Sin etapa de temas: el resumen no reporta taxonomía del lote.
        _ULTIMO_RESUMEN['modo_taxonomia'] = 'omitido'
        _ULTIMO_RESUMEN['taxonomia_detalle'] = {
            'temas': [],
            'reglas': [],
            'nota': 'Etapa de temas omitida por configuración (solo tono + subtema).',
        }
    elif theme_model is not None:
        _ULTIMO_RESUMEN['modo_taxonomia'] = 'pkl'
        _ULTIMO_RESUMEN['temas_por_pkl'] = pkl_counts.get('tema', 0)
        _ULTIMO_RESUMEN['taxonomia_detalle'] = {
            'temas': temas_lote,
            'reglas': [],
            'nota': ('Temas clasificados con el modelo PKL del cliente. '
                     'Las etiquetas son las clases del modelo, no nombres inventados del lote.'),
        }
    else:
        _ULTIMO_RESUMEN['modo_taxonomia'] = 'lote'
        _ULTIMO_RESUMEN['taxonomia_detalle'] = {
            'temas': temas_lote,
            'reglas': [],
            'nota': ('Temas generados bottom-up a partir de los subtemas de este lote. '
                     'Sin memoria entre corridas.'),
        }
    if tone_model is not None:
        _ULTIMO_RESUMEN['tonos_por_pkl'] = pkl_counts.get('tono', 0)
    _ULTIMO_RESUMEN['votos_tono'] = votos
    _ULTIMO_RESUMEN['filas'] = len(rows)
    _ULTIMO_RESUMEN['duplicadas'] = sum(1 for r in rows if r.get('is_duplicate'))
    # Costo aproximado de IA para la tarjeta de resultados finales.
    costo, pin, pout = _costo_aprox_usd(model, uso)
    _ULTIMO_RESUMEN['uso_tokens'] = {'input': uso['input'], 'output': uso['output'],
                                     'llamadas': uso['llamadas']}
    _ULTIMO_RESUMEN['costo_aprox_usd'] = costo
    _ULTIMO_RESUMEN['costo_modelo'] = model
    _ULTIMO_RESUMEN['costo_precios'] = {'input_por_millon': pin, 'output_por_millon': pout}
    if progress_callback:
        if incluir_tema:
            progreso(93, 'Etiquetado listo: %d grupos, %d temas del lote' % (len(grupos), len(temas_lote)))
        else:
            progreso(93, 'Etiquetado listo: %d grupos (solo tono + subtema)' % len(grupos))
    return rows

# ============================================================================
# 9b. Un hecho = un tono: reconcilia el tono entre grupos que comparten
# subtema canonizado (el mismo hecho contado por varios medios no puede
# salir Positivo en uno y Neutro en otro).
# ============================================================================
def unificar_tono_mismo_hecho(grupos: Sequence[dict],
                             etiquetas: Dict[int, dict]) -> int:
    """Voto de tono por hecho. Empate → Neutro. Nunca crea un Negativo por
    voto: si algún grupo marcó Negativo (señalamiento), no se toca el hecho.

    Dos grupos con subtemas distintos pero con el MISMO contexto de marca
    (pasajes citados compartidos) votan juntos: es el mismo hecho contado
    por varios medios aunque el subtema haya salido con otra redacción.
    """
    por_sub: Dict[str, List[int]] = defaultdict(list)
    for g in grupos:
        e = etiquetas.get(g.get('grupo')) or {}
        s = nz(e.get('sub_tema') or '')
        if s:
            por_sub[s].append(g.get('grupo'))
    hechos: List[List[int]] = [list(v) for v in por_sub.values()]
    # Union-find sobre los buckets: unir hechos cuyo contexto de marca sea
    # casi el mismo (bar estricto: 8+ 5-gramas compartidos, solapamiento 0.65).
    ctx = {}
    for g in grupos:
        ctx[g.get('grupo')] = g.get('contexto_marca') or ''
    par = list(range(len(hechos)))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    def uni(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            par[max(ra, rb)] = min(ra, rb)

    for i in range(len(hechos)):
        for j in range(i + 1, len(hechos)):
            if find(i) == find(j):
                continue
            ci = ' '.join(ctx.get(g, '') for g in hechos[i])
            cj = ' '.join(ctx.get(g, '') for g in hechos[j])
            if _contextos_mismo_hecho(ci, cj, umbral=0.65, min_gramas=10):
                uni(i, j)
    buckets = defaultdict(list)
    for i, gids in enumerate(hechos):
        buckets[find(i)].extend(gids)
    cambios = 0
    for gids in buckets.values():
        if len(gids) < 2:
            continue
        tonos = [(etiquetas.get(g) or {}).get('tono') for g in gids]
        if any(t == 'Negativo' for t in tonos):
            continue
        c = Counter(t for t in tonos if t in TONOS)
        if not c:
            continue
        top = c.most_common()
        ganador = top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else 'Neutro'
        for g in gids:
            if etiquetas[g].get('tono') != ganador:
                etiquetas[g]['tono'] = ganador
                cambios += 1
    return cambios


# ============================================================================
# 9. Guarda determinista del tono: "el tema negativo no es tono negativo"
# ============================================================================
# El modelo pequeno tiende a marcar Negativo todo hecho tragico (un robo, El Nino, una protesta,
# una cifra de suicidios). Esta guarda aplica en codigo la regla del criterio: Negativo SOLO si hay
# un señalamiento dirigido a la marca, a su vocero o a una empresa del sector.
# NOTA v4.22: «señalar» como verbo de habla («la Fundación señaló que…») NO
# es crítica: es el verbo más común para introducir declaraciones en prensa
# y ya está en HABLA_PAT como verbo de habla. Solo el sustantivo
# «señalamiento(s)» conserva sentido acusatorio inequívoco. Las formas
# verbales acusatorias con blanco explícito («señalaron a la Fundación»)
# las sigue cazando _marca_blanco_de_critica por construcción direccional.
CRITICA_PAT = re.compile(
    r'(denunci|cuestion|sancion|critic|rechaz|exig|acusa|se[ñn]alamientos?|demand|investiga|irregular|'
    r'sobrecosto|corrup|incumpl|multa|reclam|responsabiliz|se le atribuye|'
    r'atac|esc[áa]ndal|crisis|fraude|malvers|despilfarr)', re.I)
VICTIMA_PAT = re.compile(
    r'(\brobo\b|roban|rob[oa]ron|hurto|atrac|asalt|accidente|\bmuert|fallec|herid|inundaci|'
    r'deslizamiento|incendio|sequ[ií]a|apag[oó]n|el ni[nñ]o|desempleo|suicid|\bprecio|alza|'
    r'aumento|protesta|delincuencia|homicid|violencia|aguas residuales en)', re.I)
BLANCO_EMPRESA = re.compile(r'(una empresa|una compa[nñ][ií]a|una firma|una industria|un frigor[ií]fico|'
                            r'una planta|un matadero|una av[ií]cola|la empresa|la compa[nñ][ií]a)', re.I)
NOMBRE_PROPIO = re.compile(r'(?<![.!?]\s)(?<![.!?])\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}')


# Marcadores de respuesta/descargo de la marca ante un señalamiento.
_RESPUESTA_PAT = re.compile(
    r'\b(respondi[óo]|responde|respuesta|descargo|pronunciamiento|comunicado|'
    r'versi[óo]n de|se defendi[óo]|defendi[óo]|neg[óo]|desminti[óo]|aclar[óo]|'
    r'rechaz[óo]\s+(?:las|los)\s+(?:acusaciones|se[ñn]alamientos|denuncias|cr[íi]ticas|cargos)|'
    r'cuestion[óo]\s+la\s+denuncia|en\s+respuesta\s+a)\b', re.I)


def _respuesta_marca(texto: str, actores: Sequence[str]) -> bool:
    """True si un actor de la marca aparece en una oración con marcadores de
    respuesta/descargo (respondió, descargo, pronunciamiento, versión de...).

    Se evalúa por oración para que un descargo al final de una frase no se
    confunda con el sujeto de la siguiente.
    """
    t = ctrl(texto)
    if not t:
        return False
    acts = [a for a in (actores or []) if a and len(a) >= 4]
    if not acts:
        return False
    for oracion in re.split(r'(?<=[.!?;:])\s+|\n+', t):
        n = nz(oracion)
        if not n or not _RESPUESTA_PAT.search(oracion):
            continue
        if any(a in n for a in acts):
            return True
    return False


def voto_final_tono_por_subtema(grupos: Sequence[dict],
                                etiquetas: Dict[int, dict]) -> int:
    """Voto final de tono dentro de cada subtema unificado (v4.20).

    Corre al final de la tubería, DESPUÉS de todas las guardas deterministas
    (positiva, crítica con respuesta, tragedia). Caso real: 268 noticias de
    «Estado de salud de Yamid Amat» quedaron 194 Positivo / 67 Neutro porque
    las guardas corren por noticia y pueden dividir el tono dentro del mismo
    hecho. Este voto reconcilia: si una mayoría clara (>=60%) del subtema
    comparte un tono, las minorías lo adoptan.

    No deshace las reglas superiores: respeta los Neutros pegajosos (una
    noticia que una guarda bajó de Negativo a Neutro por crítica con
    respuesta, o de Positivo a Neutro por tragedia con experto de la casa,
    lleva la marca y el voto no la toca), y nunca toca un Negativo.
    """
    cambios = 0
    por_sub = defaultdict(list)
    for g in grupos:
        e = etiquetas.get(g.get('grupo'))
        if e and (e.get('sub_tema') or '').strip():
            por_sub[e['sub_tema'].strip()].append(g.get('grupo'))
    for miembros in por_sub.values():
        if len(miembros) < 2:
            continue
        protegidos = {gid for gid in miembros
                      if (etiquetas[gid].get('flags') or {}).get('neutro_pegajoso')}
        votan = [etiquetas[gid]['tono'] for gid in miembros
                 if gid not in protegidos
                 and etiquetas[gid].get('tono') != 'Negativo']
        if not votan:
            continue
        c = Counter(votan)
        ganador, nvotos = c.most_common(1)[0]
        if nvotos < 0.6 * len(votan):
            continue
        for gid in miembros:
            if gid in protegidos:
                continue
            e = etiquetas[gid]
            if e.get('tono') == 'Negativo':
                continue
            if e.get('tono') != ganador:
                e['tono'] = ganador
                cambios += 1
    return cambios


def aplicar_regla_critica_con_respuesta(grupos: Sequence[dict],
                                       etiquetas: Dict[int, dict],
                                       brand: str, aliases: Sequence[str],
                                       voceros: Sequence[str] = ()) -> List[int]:
    """Crítica con respuesta de la marca = Neutro (la información se equilibra).

    Baja Negativo a Neutro cuando la nota trae el señalamiento dirigido Y la
    respuesta, descargo, pronunciamiento o versión de la marca/vocero. No toca
    Positivos ni críticas sin respuesta.
    """
    actores = [nz(x) for x in [brand] + list(aliases or []) + list(voceros or [])
               if x and len(nz(x)) >= 4]
    if not actores:
        return []
    bajados = []
    for g in grupos:
        e = etiquetas.get(g.get('grupo'))
        if not e or e.get('tono') != 'Negativo':
            continue
        texto = '%s. %s' % (g.get('titulo', ''), g.get('contexto') or g.get('texto', ''))
        if _critica_dirigida(texto, brand, aliases) and _respuesta_marca(texto, actores):
            e['tono'] = 'Neutro'
            bajados.append(g.get('grupo'))
    return bajados


def _tema_negativo(texto: str) -> bool:
    return bool(VICTIMA_PAT.search(ctrl(texto)))


def _critica_dirigida(texto: str, brand: str, aliases: Sequence[str]) -> bool:
    """True si el texto contiene un señalamiento con un blanco identificable (marca o nombre propio)."""
    t = ctrl(texto)
    if not t:
        return False
    nt = nz(t)
    marcas = [m for m in [brand] + list(aliases) if m and len(str(m)) > 3]
    for m in CRITICA_PAT.finditer(t):
        cerca = nt[max(0, m.start() - 60): m.end() + 90]
        if any(nz(x) and nz(x) in cerca for x in marcas):
            return True
        # el blanco tiene que estar pegado al verbo: si no, es una mencion incidental
        # ('la denuncia oportuna de la comunidad permitio recuperar...' NO es un señalamiento).
        ventana = t[m.end(): m.end() + 35]
        if BLANCO_EMPRESA.search(ventana) or NOMBRE_PROPIO.search(ventana):
            return True
    return False


# Verbos de acusación/señalamiento: solo en construcción direccional hacia la
# marca cuentan como crítica dirigida ("cuestionaron a Cotelco",
# "Cotelco fue sancionada"). Un sustantivo como 'multa' dentro de 'fotomultas'
# es el tema de la noticia, no un señalamiento contra la marca.
_VERBO_ACUSACION_PAT = (r'(denunci\w*|cuestion\w*|acus\w*|sancion\w*|critic\w*|'
                        r'atac\w*|señal\w*|censur\w*|reproch\w*|conden\w*|'
                        r'demand\w*|imput\w*)')


def _marca_blanco_de_critica(texto: str, brand: str, aliases: Sequence[str]) -> bool:
    """True si un verbo de acusación apunta a la marca/alias en construcción
    direccional ("cuestionaron a Cotelco", "Cotelco fue sancionada").

    A diferencia de `_critica_dirigida` (que acepta cualquier nombre propio
    como blanco y cualquier palabra del patrón cerca de la marca), aquí el
    blanco debe ser la marca y el señalamiento debe estar gramaticalmente
    dirigido a ella: el tono se evalúa hacia la marca, no hacia terceros ni
    hacia el tema de la noticia.
    """
    t = nz(texto)
    marcas = [nz(x) for x in [brand] + list(aliases or []) if x and len(nz(x)) >= 4]
    if not t or not marcas:
        return False
    mpat = '(?:' + '|'.join(re.escape(x) for x in marcas) + ')'
    verbo = _VERBO_ACUSACION_PAT
    patrones = [
        # "cuestionaron a Cotelco", "denuncias contra la marca"
        verbo + r'(?:\s+\w+){0,3}?\s+(?:a|al|contra|hacia)\s+' + mpat,
        # "Cotelco fue sancionada"
        mpat + r'\s+(?:fue|fueron|ha sido|sería|será)\s+' + verbo,
    ]
    return any(re.search(p, t) for p in patrones)


# Predicados que delatan que un alias ambiguo («Santa Fe») se refiere a una
# entidad deportiva y no a la marca. Solo se usan para desambiguar la forma
# corta cuando el alias es subcadena del nombre de la marca; si el cliente ES
# el club, su alias no es ambiguo y esto no aplica.
_DEPORTE_PAT = re.compile(
    r'\b(partido|estadio|cancha|hinchada|golead\w*|campeonato|torneo|liga|'
    r'clasificaci\w+|descens\w+|empate|empat\w+|penal\w*|arbitr\w*|f[úu]tbol|'
    r'camp[ií]n|gol(es)?|jugad\w+|t[eé]cnico|alineaci\w+|derrota|victoria|'
    r'triunfo)\b', re.I)


def _forma_corta_ambigua(forma: str, formas: Sequence[str]) -> bool:
    """True si `forma` es subcadena de otra forma más larga («santa fe» en
    «fundación santa fe»): puede referirse a otra entidad con el mismo alias."""
    return any(b != forma and forma in b for b in formas)


def _menciones_resolubles(titulo: str, texto: str, formas: Sequence[str]) -> int:
    """Cuenta menciones de la marca resolviendo la forma más larga primero.

    Las menciones sueltas de la forma corta ambigua no cuentan cuando el
    texto mezcla entidades (aparece la forma larga: «Fundación Santa Fe» +
    el club «Santa Fe») o van con predicado deportivo: un alias compartido
    no fabrica protagonismo (caso real v4.20: dos Negativos por noticias del
    equipo de fútbol).
    """
    cuerpo = nz('%s\n%s' % (titulo or '', texto or ''))
    if not cuerpo:
        return 0
    pat = re.compile('(%s)' % '|'.join(re.escape(f) for f in formas))
    total = 0
    for m in pat.finditer(cuerpo):
        f = m.group(1)
        if _forma_corta_ambigua(f, formas):
            if any(b != f and f in b and b in cuerpo for b in formas):
                continue
            ventana = cuerpo[max(0, m.start() - 80): m.end() + 80]
            if _DEPORTE_PAT.search(ventana):
                continue
        total += 1
    return total


def _marca_protagonista(titulo: str, texto: str, actores: Sequence[str]) -> bool:
    """True si la marca protagoniza la noticia: aparece en el titular o tiene
    al menos dos menciones resolubles en el texto. Una mención incidental
    aislada no basta (criterio 'mención breve/sin protagonismo = Neutro'), y
    un alias ambiguo compartido con otra entidad no cuenta como protagonismo.
    """
    formas = sorted({a for a in actores if a}, key=len, reverse=True)
    if not formas:
        return False
    nt = nz(titulo or '')
    for f in formas:
        if f in nt:
            if _forma_corta_ambigua(f, formas) and _DEPORTE_PAT.search(nt):
                continue
            return True
    return _menciones_resolubles(titulo, texto, formas) >= 2


def aplicar_regla_negativo_sin_blanco(grupos: Sequence[dict], etiquetas: Dict[int, dict],
                                      brand: str, aliases: Sequence[str],
                                      voceros: Sequence[str] = ()) -> List[int]:
    """Calibra el Negativo: el sentimiento se evalúa hacia la marca.

    Baja Negativo a Neutro cuando el señalamiento no apunta a la marca/alias/
    vocero Y la marca no protagoniza la noticia (mención incidental). No toca
    tragedias con experto citado (regla aparte) ni críticas con respuesta de
    la marca (ya quedan en Neutro). Devuelve los grupos corregidos.
    """
    actores = [nz(x) for x in [brand] + list(aliases or []) + list(voceros or [])
               if x and len(nz(x)) >= 4]
    if not actores:
        return []
    bajados = []
    for g in grupos:
        e = etiquetas.get(g.get('grupo'))
        if not e or e.get('tono') != 'Negativo':
            continue
        texto = '%s. %s' % (g.get('titulo', ''), g.get('contexto') or g.get('texto', ''))
        if _marca_blanco_de_critica(texto, brand, aliases):
            continue
        if _marca_protagonista(g.get('titulo', ''), texto, actores):
            continue
        e['tono'] = 'Neutro'
        bajados.append(g.get('grupo'))
    return bajados


def aplicar_regla_positivo_incidental(grupos: Sequence[dict], etiquetas: Dict[int, dict],
                                      brand: str, aliases: Sequence[str],
                                      voceros: Sequence[str] = ()) -> List[int]:
    """Calibra el Positivo: el sentimiento se evalúa hacia la marca.

    Baja Positivo a Neutro cuando la marca no protagoniza la noticia (mención
    incidental) Y no hay evidencia positiva sobre ella. Es el espejo de
    aplicar_regla_negativo_sin_blanco (v4.16): el LLM hereda el tono general
    de la noticia («mención en nota positiva ≠ positiva»). La guarda positiva
    ya valida la evidencia real sobre la marca; si ella no subiría este grupo
    desde Neutro, el Positivo es incidental. Casos reales v4.20 (Serena del
    Mar): la reapertura era del OTRO hospital; la predicción era de Mhoni
    Vidente. Devuelve los grupos corregidos.
    """
    actores = [nz(x) for x in [brand] + list(aliases or []) + list(voceros or [])
               if x and len(nz(x)) >= 4]
    if not actores:
        return []
    bajados = []
    for g in grupos:
        e = etiquetas.get(g.get('grupo'))
        if not e or e.get('tono') != 'Positivo':
            continue
        titulo = g.get('titulo', '')
        texto = '%s. %s' % (titulo, g.get('contexto') or g.get('texto', ''))
        if _marca_protagonista(titulo, texto, actores):
            continue
        # ¿La guarda positiva encontraría evidencia real sobre la marca en
        # este contenido? Se prueba sobre una copia en Neutro para reutilizar
        # toda su lógica (fuente experta, sede, participación, autoría...).
        copia_g = dict(g)
        copia_e = dict(e, tono='Neutro')
        copia_e.pop('flags', None)
        if aplicar_guarda_positiva([copia_g], {copia_g.get('grupo'): copia_e},
                                   brand, aliases, voceros):
            continue
        e['tono'] = 'Neutro'
        bajados.append(g.get('grupo'))
    return bajados


def aplicar_guarda_tono(grupos: Sequence[dict], etiquetas: Dict[int, dict],
                        brand: str, aliases: Sequence[str]) -> List[int]:
    """Degrada a Neutro los Negativos que solo describen un hecho tragico, sin señalamiento dirigido.

    Devuelve la lista de grupos corregidos (para la auditoria de la interfaz).
    """
    corregidos = []
    for g in grupos:
        e = etiquetas.get(g['grupo'])
        if not e or e.get('tono') != 'Negativo':
            continue
        texto = '%s %s' % (g['titulo'], g.get('contexto') or g.get('texto', ''))
        if _tema_negativo(texto) and not _critica_dirigida(texto, brand, aliases):
            e['tono'] = 'Neutro'
            corregidos.append(g['grupo'])
    return corregidos


# Verbos de habla que marcan cita como fuente ("dijo el rector…", "señaló la vocera…").
# Se excluye "reveló": suele introducir hallazgos alarmantes, no voz experta.
HABLA_PAT = re.compile(
    r'\b(dijo|afirm[oó]|señal[oó]|advirti[oó]|consider[oó]|explic[oó]|asegur[oó]|'
    r'indic[oó]|destac[oó]|manifest[oó]|sostu?vo|sostiene|precis[oó]|coment[oó]|'
    r'expres[oó]|declar[oó]|puntualiz[oó]|inform[oó]|report[oó]|de\s+acuerdo\s+con)\b')


# Regla del usuario (2026-09-20): "tragedia con experto de la casa = neutral".
# Si la nota es una tragedia (muerte) y el experto/docente de la marca solo
# aparece citado como fuente, el tono es Neutro aunque sea voz experta.
# No aplica si la marca ACTÚA frente al problema (ayuda, dona, organiza,
# propone solución): eso sigue siendo Positivo.
_TRAGEDIA_PAT = re.compile(
    r'\b(muertes?|muertos?|muertas?|fallec\w*|decesos?|asesinat\w*|homicidios?|'
    r'feminicidios?|suicidios?|tragedias?|tr[áa]gic[oa]s?|v[íi]ctima mortal|'
    r'accidente fatal|muri[óo]|perdi[óo] la vida)\b', re.I)
_ROL_EXPERTO_PAT = re.compile(
    r'\b(investigador(?:a|es)?|docente(?:s)?|profesor(?:a|es)?|expert[oa]s?|'
    r'especialista(?:s)?|rector(?:a)?|decan[oa]s?|cient[íi]fic[oa]s?|'
    r'acad[ée]mic[oa]s?)\b', re.I)
# La marca actúa frente al problema o lidera la acción: no es "solo citada".
_MARCA_ACTORA_PAT = re.compile(
    r'\b(ayud\w*|don[óo]|apoy[óo]|atiend\w*|socorr\w*|bec[óo]as?|propuso|propone|'
    r'articul[óo]|acompa[ñn]\w*|solidariz\w*|organiz\w*|convoc\w*|lider\w*|'
    r'present[óo]|lanz[óo]|inaugur\w*|firm[óo]|anunci\w*|realiz[óo]|impuls\w*|'
    r'entreg[óo])\b', re.I)


def _experto_casa_citado(texto: str, actores: Sequence[str]) -> bool:
    """True si un actor de la marca aparece cerca (~100 caracteres) de un rol experto."""
    t = nz(texto)
    if not t:
        return False
    for m in _ROL_EXPERTO_PAT.finditer(t):
        ventana = t[max(0, m.start() - 100):m.end() + 100]
        if any(a in ventana for a in actores):
            return True
    return False


def _marca_actora(texto: str, actores: Sequence[str]) -> bool:
    """True si un actor de la marca aparece cerca (~120 caracteres) de un verbo de acción."""
    t = nz(texto)
    if not t:
        return False
    for m in _MARCA_ACTORA_PAT.finditer(t):
        ventana = t[max(0, m.start() - 120):m.end() + 120]
        if any(a in ventana for a in actores):
            return True
    return False


# La marca como autora del conocimiento: estudio, informe, investigación,
# encuesta, diagnóstico, documento, artículo o columna ELABORADO/PUBLICADO por
# ella. Criterio del usuario: gestiones, estudios y acciones propias = Positivo.
_AUTORIA_PROPIA_PAT = re.compile(
    r'(elaborad[oa] por|realizad[oa] por|estudio de|informe de|investigaci[óo]n de|'
    r'encuesta de|diagn[óo]stico de|documento de|art[íi]culo de|columna de|ponencia de|'
    r'publicad[oa] por|liderad[oa] por|coordinad[oa] por)', re.I)


# Marca mencionada como alma máter de alguien («su formación en la
# Universidad…», «egresado de…», «estudió en…»): lo que sigue («…donde
# participó…») es participación de la persona, no acción de la marca.
_ALMA_MATER_PAT = re.compile(
    r'formaci[oó]n|egresad[oa]s?|graduad[oa]s?|alma m[aá]ter|estudi[oó]\s+en', re.I)


def _mencion_biografica(oracion: str, actores: Sequence[str]) -> bool:
    """True si un actor aparece en la oración como alma máter de alguien.

    Evita que «recordó su formación en la Universidad Simón Bolívar, donde
    participó en…» suba a Positivo: quien participó fue la persona, no la marca
    (caso real Unisimón, v4.18). Solo cuenta si el actor va DESPUÉS de la marca
    biográfica («estudió en la Universidad»); «la Universidad estudió…» es
    acción propia y no se excluye. «estudio» como sustantivo («el estudio de
    la Universidad») tampoco cuenta: se exige «estudió en».
    """
    n = nz(oracion)
    m = _ALMA_MATER_PAT.search(n)
    if not m:
        return False
    return any(a in n[m.end():] for a in actores)


# Clases de evento asistencial: cualquiera de ellas en el texto indica un
# episodio de atención en salud (v4.20).
_CLASES_ASISTENCIALES = frozenset({'salud', 'nacimiento', 'cirugia'})


def _atencion_paciente_en_marca(texto: str, brand: str,
                               aliases: Sequence[str]) -> bool:
    """True si el texto describe la atención de un paciente en la marca.

    Señales (todas a nivel de grupo): un ancla de persona (el paciente,
    detectada en el texto, no configurada), vocabulario de evento de salud
    (las clases de dominio: hospitalización, UCI, pronóstico...) y la
    marca/alias como lugar de la atención («en/de/a la Fundación Santa Fe»,
    «en la Clínica Santa Fe»). Para clientes de salud, el episodio
    asistencial es contenido propio de la marca (v4.20): no es una mención
    incidental.
    """
    t = nz(texto or '')
    if not t:
        return False
    tokens_marca = _tokens_marca(brand, aliases)
    # Solo anclas multi-palabra: un sustantivo común capitalizado («Conversatorio»)
    # no es un paciente. Los nombres de una palabra («Gael») se resuelven por
    # la unificación por ancla del subtema, no aquí.
    if not _anclas_en(texto, tokens_marca):
        return False
    if not (_CLASES_ASISTENCIALES & _clases_de_texto(texto)):
        return False
    actores = [nz(x) for x in [brand] + list(aliases or [])
               if x and len(nz(x)) >= 4]
    sede = (r'(en|de|del|a|al|hacia)\s+(la\s+|el\s+)?'
            r'(cl[ií]nica|hospital|fundaci[oó]n|instituto|centro|sede)?\s*')
    return any(re.search(sede + re.escape(a) + r'(?=\W|$)', t)
               for a in actores)


def aplicar_guarda_positiva(grupos: Sequence[dict], etiquetas: Dict[int, dict],
                            brand: str, aliases: Sequence[str],
                            voceros: Sequence[str] = ()) -> List[int]:
    """Sube Neutro a Positivo solo cuando la marca es autora del hecho favorable.

    Evita el sesgo opuesto del modelo pequeño: una marca mencionada en una nota
    positiva no es automáticamente positiva. La ventana se evalúa por oración
    para que una marca al final de una frase no se convierta en sujeto de la
    siguiente.
    """
    actores = [nz(x) for x in [brand] + list(aliases or []) + list(voceros or [])
               if x and len(nz(x)) >= 4]
    if not actores:
        return []
    # Verbos de acción propia de la marca. Incluyen participios pasivos
    # («organizado por la Universidad…»): en pasiva el actor va después del
    # verbo, introducido por «por». El participio va primero en cada grupo
    # para que «organizado» no calce solo con «organiza».
    verbos = (r'entreg(?:ad[oa]s?|a|ó|aron|an)|inaugur(?:ad[oa]s?|a|ó|aron|an)|'
              r'constru(?:id[oa]s?|ye|yó|yeron|yen)|'
              r'pone en marcha|puso en marcha|lanza|lanzó|lanzad[oa]s?|'
              r'impulsa|impulsó|impulsad[oa]s?|aprueba|aprobó|aprobad[oa]s?|'
              r'destina|destinó|destinad[oa]s?|invierte|invirtió|invertid[oa]s?|'
              r'dona|donó|donad[oa]s?|respalda|respaldó|respaldad[oa]s?|'
              r'apoya|apoyó|apoyad[oa]s?|'
              r'capacita|capacitó|capacitad[oa]s?|organiza|organizó|organizad[oa]s?|'
              r'convoca|convocó|convocad[oa]s?|celebra|celebró|celebrad[oa]s?|'
              r'realiza|realizó|realizad[oa]s?|presenta|presentó|presentad[oa]s?|'
              r'anuncia|anunció|anunciad[oa]s?|publica|publicó|publicad[oa]s?|'
              r'socializa|socializó|socializad[oa]s?|'
              r'gan(?:a|ó|aron|ará|arán)|recib(?:e|ió|ieron|irá|irán)|ocup(?:a|ó|aron|ará|arán)|'
              r'abr(?:e|ió|irá|irán)|firm(?:a|ó|aron|ará|arán)|atend(?:e|ió|erá|erán)|'
              r'pone en servicio|habilita|habilitó|habilitad[oa]s?')
    eventos = r'(congreso|foro|feria|cumbre|seminario|jornada|conferencia|encuentro|festival|reuni[oó]n|reuniones|conversatorio)'
    peticion = re.compile(r'\b(pidió|pide|solicitó|solicita|exigió|exige|debería)\b', re.I)
    critica = re.compile(r'\b(denunci|cuestion|sancion|acus|incumpl|sobrecost|corrup|'
                         r'retras|paraliz|rechaz|investiga(?!ción|cion))\w*', re.I)
    corregidos = []
    for g in grupos:
        e = etiquetas.get(g.get('grupo'))
        if not e or e.get('tono') != 'Neutro':
            continue
        texto = '%s. %s' % (g.get('titulo', ''), g.get('contexto') or g.get('texto', ''))
        # Crítica con respuesta de la marca: queda Neutro (la información se
        # equilibra). Esta guarda no la sube a Positivo aunque el vocero hable.
        if _critica_dirigida(texto, brand, aliases) and _respuesta_marca(texto, actores):
            continue
        # Excepción tragedia: experto de la casa solo citado como fuente en una
        # tragedia = Neutro. No bloquea la marca que actúa (dona, organiza…).
        tragedia_sin_accion = (bool(_TRAGEDIA_PAT.search(texto))
                                and not _marca_actora(texto, actores))
        # v4.20: atención a un paciente en la marca (ancla de persona + evento
        # de salud + marca como lugar de la atención). Para clientes de salud
        # el episodio asistencial es contenido propio: Positivo. No aplica en
        # tragedia sin acción ni con crítica dirigida a la marca.
        if (not tragedia_sin_accion
                and not _critica_dirigida(texto, brand, aliases)
                and _atencion_paciente_en_marca(texto, brand, aliases)):
            e['tono'] = 'Positivo'
            corregidos.append(g.get('grupo'))
            continue
        for oracion in re.split(r'(?<=[.!?;:])\s+|\n+', ctrl(texto)):
            n = nz(oracion)
            if (not n or peticion.search(oracion) or critica.search(oracion) or
                    (re.search(r'(informe|estudio|alerta|cifra|panorama|diagnóstico|diagnostico)', oracion, re.I)
                     and not re.search(r'(obra|inversi|beca|premio|convenio|bloque|aula|colabor|particip|elabor|realiz|investig|intervenci|opini[oó]n|ponencia|publica|publicó|socializa|socializ)', oracion, re.I))):
                continue
            voceros_norm = [nz(v) for v in (voceros or []) if v]
            es_columna_vocero = bool(voceros_norm and any(v in n for v in voceros_norm)
                                     and (re.search(r'^\s*(por|de)\s+', oracion, re.I)
                                          or re.search(r'(columna|opini[oó]n|an[aá]lisis|intervenci[oó]n|art[ií]culo)', oracion, re.I)))
            if es_columna_vocero:
                e['tono'] = 'Positivo'
                corregidos.append(g.get('grupo'))
                break
            # Vocero o marca citado como fuente experta: verbo de habla cerca
            # del actor ("consideró el rector de la Universidad Simón Bolívar").
            # No aplica si la oración trae petición o crítica (ya filtradas
            # arriba): una declaración en contexto adverso no suma.
            m_habla = HABLA_PAT.search(n)
            if m_habla and not tragedia_sin_accion:
                ventana = n[max(0, m_habla.start() - 90):m_habla.end() + 70]
                if any(a in ventana for a in actores):
                    e['tono'] = 'Positivo'
                    corregidos.append(g.get('grupo'))
                    break
            if (not tragedia_sin_accion
                    and not _mencion_biografica(oracion, actores)
                    and re.search(r'(colaboraci[oó]n|colabor[oó]|participa|particip[oó]|coautor|coautora|'
                          r'intervenci[oó]n|vocero|vocera|'
                          r'fuente experta|ponencia|present[oó] una)', oracion, re.I)
                    and any(a in n for a in actores)) \
                    or (not tragedia_sin_accion
                        and _AUTORIA_PROPIA_PAT.search(oracion)
                        and any(a in n for a in actores)):
                e['tono'] = 'Positivo'
                corregidos.append(g.get('grupo'))
                break
            # Participación de la marca en reuniones/conversatorios/foros: es
            # Positivo (regla del cliente, 2026-09-22). Requiere verbo de
            # participación + nombre del evento + actor en la misma oración.
            # No aplica en tragedia sin acción de la marca (la regla tragedia
            # corre después y sigue mandando) ni toca Negativos.
            if (not tragedia_sin_accion
                    and not _mencion_biografica(oracion, actores)
                    and re.search(r'\b(participa|participó|participaron|participará|'
                                  r'asiste|asistió|asistieron|hizo parte|hace parte|'
                                  r'formó parte|tomó parte|estuvo presente|'
                                  r'interviene|intervino)\b', oracion, re.I)
                    and re.search(r'\b(reuni[oó]n|reuniones|conversatorio|'
                                  r'mesa de trabajo|mesa redonda|encuentro|'
                                  r'foro|congreso|seminario|jornada|panel|'
                                  r'simposio)\b', oracion, re.I)
                    and any(a in n for a in actores)):
                e['tono'] = 'Positivo'
                corregidos.append(g.get('grupo'))
                break
            # La marca/alias como SEDE del evento («realizado … en la Universidad»,
            # «se llevará a cabo en Unisimón», «un congreso … en la Universidad»).
            # Regla del cliente (2026-09-23): eventos en la marca/alias son
            # Positivo — la marca es anfitriona. No aplica en tragedia sin
            # acción de la marca ni en menciones biográficas («estudió en la
            # Universidad» no trae verbo de evento).
            if not tragedia_sin_accion and not _mencion_biografica(oracion, actores):
                m_sede = re.search(
                    r'\b((realiz|celebr|organiz|desarroll)(ad[oa]s?|ara|an|a|o|aron)|'
                    r'llev(ad[oa]s?|ara|a)\s+a\s+cabo|(tuvo|tiene|tendra)\s+lugar|'
                    r'congreso|foro|feria|cumbre|seminario|jornada|conferencia|'
                    r'(?<!\bme\s)encuentro|festival|reuni[oó]n|reuniones|conversatorio|simposio|panel)\b', n)
                if m_sede:
                    despues_sede = n[m_sede.end():m_sede.end() + 110]
                    # «realiza sus estudios en la Universidad» es formación de
                    # la persona, no un evento de la marca: se excluye.
                    if re.search(r'\b(estudios?|carrera|doctorado|maestr[íi]a|pregrado|tesis|curso)\b',
                                 despues_sede):
                        pass
                    elif any(re.search(r'\ben\s+(?:el\s+|la\s+|los\s+|las\s+)?'
                                       + re.escape(a) + r'(?=\W|$)', despues_sede)
                             for a in actores):
                        e['tono'] = 'Positivo'
                        corregidos.append(g.get('grupo'))
                        break
                else:
                    # v4.20: la marca como lugar del evento en construcción de
                    # sujeto («Serena del Mar vivió una gran fiesta deportiva»,
                    # «la Universidad fue sede del encuentro»): el evento
                    # ocurre EN la marca aunque la gramática la ponga como
                    # sujeto. Se exige sustantivo de evento + verbo de sede.
                    if (re.search(r'\b(fiesta|festejo|carrera|evento|espect[aá]culo|'
                                  r'concierto|encuentro|festival)\b', n)
                            and re.search(r'\b(vivi[oó]|vivieron|acogi[oó]|acogieron|'
                                          r'alberg[oó]|albergaron|fue\s+sede|'
                                          r'sirvi[oó]\s+de\s+sede)\b', n)
                            and any(a in n for a in actores)):
                        e['tono'] = 'Positivo'
                        corregidos.append(g.get('grupo'))
                        break
                # v4.20: alianza/convenio con la marca («alianza entre Morphy y
                # la Fundación…», «convenio con la Universidad…»): la marca es
                # parte del hecho, no mención incidental. Se excluye «de
                # acuerdo con» (atribución de fuente, no alianza).
                m_alianza = re.search(
                    r'\b(alianzas?|convenios?|pactos?|(?<!de\s)acuerdo)\b', n)
                if m_alianza and any(
                        re.search(r'\b(entre|con)\b.{0,80}?'
                                  + re.escape(a) + r'(?=\W|$)',
                                  n[m_alianza.end():m_alianza.end() + 120])
                        for a in actores):
                    e['tono'] = 'Positivo'
                    corregidos.append(g.get('grupo'))
                    break
            if re.search(r'\b(recib(?:ió|e|ieron)|atend(?:erá|ió|e))\b', oracion, re.I) and re.search(
                    r'(premio|acreditaci|reconocimiento|pacientes|benefici)', oracion, re.I):
                e['tono'] = 'Positivo'
                corregidos.append(g.get('grupo'))
                break
            for m in re.finditer(verbos, oracion, re.I):
                antes = nz(oracion[max(0, m.start() - 80):m.start()])
                despues = nz(oracion[m.end():m.end() + 110])
                actor_antes = any(a in antes for a in actores)
                # Pasiva con participio («organizado por la Universidad…»): el
                # actor va después del verbo, introducido por «por». Sin esto,
                # «El encuentro, organizado por la Universidad Simón Bolívar»
                # no subía a Positivo (caso real Unisimón, v4.18).
                actor_por = (not actor_antes and any(
                    re.search(r'\bpor\s+(?:el\s+|la\s+|los\s+|las\s+)?'
                              + re.escape(a) + r'(?=\W|$)', despues)
                    for a in actores))
                if actor_antes or actor_por:
                    # Verbos de organizar/realizar solo son positivos si hay un
                    # evento en la oración (puede estar antes del verbo, como en
                    # «El encuentro, organizado por la Universidad…»).
                    if re.search(r'organiz|convoc|realiz|celebr', m.group(0), re.I) and not re.search(eventos, antes + ' ' + despues):
                        continue
                    if re.search(r'anunci|present', m.group(0), re.I):
                        menciona_informe = bool(re.search(r'(informe|estudio|alerta|cifra|panorama|diagnóstico|diagnostico)', oracion, re.I))
                        autoria = bool(_AUTORIA_PROPIA_PAT.search(oracion))
                        if menciona_informe and not autoria and not any(a in n for a in actores):
                            continue
                        if (not re.search(r'(obra|inversi|programa|beca|sede|congreso|foro|feria|premio|convenio|bloque|aula|programa|paciente)', despues, re.I)
                                and not autoria):
                            continue
                    e['tono'] = 'Positivo'
                    corregidos.append(g.get('grupo'))
                    break
            if e.get('tono') == 'Positivo':
                break
    return corregidos


def aplicar_regla_tragedia(grupos: Sequence[dict], etiquetas: Dict[int, dict],
                           brand: str, aliases: Sequence[str],
                           voceros: Sequence[str] = ()) -> List[int]:
    """Tragedia con experto de la casa citado como fuente = Neutro.

    Baja Positivo a Neutro cuando la nota es una tragedia (muerte) y el
    experto/docente de la marca solo aparece citado como fuente, sin que la
    marca actúe frente al problema (ayudar, donar, organizar, proponer).
    No toca Negativos: un señalamiento dirigido se respeta.
    """
    actores = [nz(x) for x in [brand] + list(aliases or []) + list(voceros or [])
               if x and len(nz(x)) >= 4]
    if not actores:
        return []
    bajados = []
    for g in grupos:
        e = etiquetas.get(g.get('grupo'))
        if not e or e.get('tono') != 'Positivo':
            continue
        texto = '%s. %s' % (g.get('titulo', ''), g.get('contexto') or g.get('texto', ''))
        if not _TRAGEDIA_PAT.search(texto):
            continue
        if _marca_actora(texto, actores):
            continue
        if _experto_casa_citado(texto, actores):
            e['tono'] = 'Neutro'
            bajados.append(g.get('grupo'))
    return bajados


# ---------------------------------------------------------------------------
# v4.23 — Prominencia de marca (métrica determinista, sin LLM)
# ---------------------------------------------------------------------------
# Columna "Prominencia" en el xlsx de resultados: mide la presencia de la
# marca en cada noticia contando menciones de la marca y sus alias — tal como
# el usuario los digitó en "Marca o Cliente Principal" y en
# "Alias o términos relacionados" (separados por coma o punto y coma) —
# en las columnas Título y CuerpoEs ("Resumen - Aclaracion" como respaldo).
#
# Es una búsqueda por palabras y similitudes: insensible a mayúsculas y
# tildes, y tolera variantes de separación ("santa fe" = "santa-fe" =
# "santafe"). No usa la API: es un conteo mecánico.
#
# Reglas (definidas por el usuario, 2026-09-24). Solo tres categorías:
#   - Exclusiva:   4 o más menciones en total; o marca en el Título con
#                  2 o más menciones en el cuerpo.
#   - Compartida:  2 o 3 menciones en total; o marca en el Título con 0-1
#                  menciones en el cuerpo (el titular solo no basta).
#   - Referencial: 0 o 1 menciones en total y sin presencia en el título.
# Nota: "marca mencionada junto a otras marcas" (comparativos) no se puede
# detectar sin una lista de competidores; la banda de 2-3 menciones la cubre
# de forma mecánica.

def _terminos_prominencia(brand, aliases):
    """Términos de marca/alias normalizados, sin duplicados ni ruido.

    Acepta aliases como lista o como texto separado por coma o punto y coma.
    El término más largo va primero para no contar dos veces
    ("fundación santa fe de bogotá" antes que "santa fe").
    """
    if isinstance(aliases, str):
        aliases = [a.strip() for a in re.split(r'[,;]', aliases) if a.strip()]
    terms = []
    for t in [brand] + list(aliases or []):
        n = nz(t)
        if len(n) >= 2 and n not in terms:
            terms.append(n)
    terms.sort(key=len, reverse=True)
    return terms


def _patron_prominencia(terms):
    """Un solo regex de alternancia no solapada sobre texto normalizado."""
    if not terms:
        return None
    alts = []
    for t in terms:
        ws = t.split()
        alt = r'[\s\-]*'.join(re.escape(w) for w in ws) if len(ws) > 1 else re.escape(t)
        alts.append(alt)
    return re.compile(r'(?<!\w)(?:%s)(?!\w)' % '|'.join(alts))


def contar_menciones_prominencia(texto, patron):
    """Ocurrencias no solapadas de los términos en el texto (0 si no hay)."""
    if patron is None:
        return 0
    return len(patron.findall(nz(texto or '')))


def clasificar_prominencia(n_titulo, n_cuerpo):
    """Etiqueta de prominencia a partir de los conteos de título y cuerpo."""
    if n_titulo >= 1:
        # La marca está en el titular: es Exclusiva salvo que el cuerpo
        # apenas la mencione (0-1) — el titular solo no basta.
        return 'Exclusiva' if n_cuerpo >= 2 else 'Compartida'
    total = n_titulo + n_cuerpo
    if total >= 4:
        return 'Exclusiva'
    if total >= 2:
        return 'Compartida'
    return 'Referencial'


def calcular_prominencia(titulo, cuerpo, brand, aliases=()):
    """Prominencia de una noticia: 'Exclusiva' | 'Compartida' | 'Referencial'."""
    patron = _patron_prominencia(_terminos_prominencia(brand, aliases))
    n_tit = contar_menciones_prominencia(titulo, patron)
    n_cue = contar_menciones_prominencia(cuerpo, patron)
    return clasificar_prominencia(n_tit, n_cue)


def aplicar_prominencia(rows, km, brand, aliases=()):
    """Agrega row['Prominencia'] a cada fila. Determinista, sin LLM."""
    patron = _patron_prominencia(_terminos_prominencia(brand, aliases))
    k_tit = (km or {}).get('titulo', 'Título')
    k_cue = (km or {}).get('resumen', 'Resumen - Aclaracion')
    for r in rows:
        n_tit = contar_menciones_prominencia(r.get(k_tit), patron)
        n_cue = contar_menciones_prominencia(r.get(k_cue), patron)
        r['Prominencia'] = clasificar_prominencia(n_tit, n_cue)
    return rows
