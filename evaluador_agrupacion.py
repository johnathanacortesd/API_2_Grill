# -*- coding: utf-8 -*-
"""Clausifier 2026-11-08: agente evaluador de agrupación (modo Claude Haiku).

Antes de que corran las unificaciones deterministas (subtemas de noticias
similares, títulos similares, coherencia por hecho, unificación por
noticia…), se arman GRUPOS CANDIDATOS con las filas cuyo Título, Resumen -
Aclaración, texto o Contexto analizado son iguales o parecidos (rapidfuzz).
Un agente Haiku aparte, `evaluador-agrupacion` (sin herramientas: recibe los
grupos en el prompt), decide por cada candidato:

  - "agrupar": mismo hecho (mismo evento, entidad, lugar y momento) => un
    subtema y un tono comunes;
  - "separar": hechos distintos => cada id conserva un subtema propio.

Las decisiones se aplican ANTES de las unificaciones (`aplicar_inicial`) y
se hacen cumplir al final, justo antes de volcar (`hacer_cumplir`): un par
"separar" nunca termina con el mismo subtema por un pase determinista, y un
grupo "agrupar" termina con el mismo subtema y el mismo tono.

Costo: una llamada `query()` por lote de candidatos (max_turns=1, sin
herramientas); los lotes corren en paralelo.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence

LOTE_CANDIDATOS = 15
MAX_CANDIDATOS = 120
MARGEN_CANDIDATO = 15     # puntos por debajo del umbral de agrupación determinista
TOPE_CTX = 600

PROMPT_EVALUADOR = (
    "Eres el agente evaluador-agrupacion de un monitoreo de medios en español de Colombia. "
    "Recibes GRUPOS CANDIDATOS: notas de distintos grupos cuyo título, resumen o contexto se "
    "parecen. Para cada candidato decides si TODAS sus notas cuentan el MISMO hecho.\n"
    "- \"agrupar\" solo si es el mismo evento, de la misma entidad, en el mismo lugar y el mismo "
    "momento. Entonces das UN subtema común y UN tono común (Positivo, Neutro o Negativo, para el "
    "cliente). Si solo una parte de las notas es el mismo hecho, pon en \"ids\" solo esas.\n"
    "- \"separar\" si son hechos distintos; das un motivo corto y un subtema propio para cada id.\n"
    "Sé ESTRICTO: compartir el tema general, el medio, el texto de agencia (boilerplate), la "
    "entidad o palabras genéricas NO basta para agrupar. Dos titulares parecidos sobre lugares, "
    "fechas o cifras distintas son hechos distintos.\n"
    "Analiza la columna Contexto analizado completa (incluye título, resumen y el extracto de la "
    "marca/alias/vocero); el subtema y el tono salen de ese contenido, no de una frase suelta del "
    "titular. SUBTEMA: frase nominal de 3 a 7 palabras de contenido, específica del hecho; nunca "
    "empieza con verbo ni contiene un verbo conjugado; nunca copia ni recorta el título.\n"
    "Responde SOLO con JSON compacto, sin texto alrededor: "
    '{"grupos":[{"id":"c1","decision":"agrupar","ids":["..."],"subtema":"...","tono":"Neutro"},'
    '{"id":"c2","decision":"separar","motivo":"...","subtemas":{"<id>":"..."}}]}'
)

# Último resultado (lo copia el analizador al resumen).
ULTIMO: Dict[str, Any] = {}
# query() inyectable (tests $0); None = el SDK real.
QUERY_FN = None


def _n(s: str) -> str:
    t = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


# ─── grupos candidatos (rapidfuzz) ─────────────────────────────────────────
# La unidad que se evalúa es la NOTA (índice de fila representante): las
# filas idénticas (mismo título y mismo Contexto analizado) son una sola
# unidad. Así el evaluador también puede separar notas que la agrupación
# determinista puso en el mismo grupo por un titular parecido.

def construir_candidatos(rows: List[dict], mapa: Dict[int, int], km: dict,
                         umbral_titulo: int = 92, umbral_cuerpo: int = 85,
                         margen: int = MARGEN_CANDIDATO) -> Dict[str, Any]:
    """{"comps": [[unidad…] >=2], "miembros": {unidad: [filas]}}. Enlaza
    las notas del mismo grupo determinista y las de grupos distintos con
    Título, Resumen - Aclaración, texto o Contexto analizado parecidos
    (rapidfuzz token_set_ratio con los umbrales del repo menos `margen`)."""
    from rapidfuzz import fuzz
    km = km or {}
    tit_col = km.get("titulo", "Título")
    res_col = km.get("resumen", "Resumen - Aclaracion")
    rep: Dict[int, dict] = {}
    miembros: Dict[int, List[int]] = {}
    firma: Dict[tuple, int] = {}
    for i in sorted(mapa or {}):
        gid = mapa[i]
        if not gid or i >= len(rows) or rows[i].get("is_duplicate"):
            continue
        r = rows[i]
        d = {"g": gid,
             "t": _n(r.get(tit_col) or r.get("Título") or ""),
             "r": _n(r.get(res_col) or r.get("Resumen - Aclaracion") or "")[:TOPE_CTX],
             "x": _n(r.get("CuerpoEs") or r.get("Texto completo") or "")[:TOPE_CTX],
             "c": _n(r.get("Contexto analizado") or "")[:TOPE_CTX]}
        k = (gid, d["t"], d["c"])
        if k in firma:
            miembros[firma[k]].append(i)
            continue
        firma[k] = i
        rep[i] = d
        miembros[i] = [i]
    us = sorted(rep)
    padre = {u: u for u in us}

    def raiz(u):
        while padre[u] != u:
            padre[u] = padre[padre[u]]
            u = padre[u]
        return u

    ut, uc = umbral_titulo - margen, umbral_cuerpo - margen
    for a_i, a in enumerate(us):
        A = rep[a]
        for b in us[a_i + 1:]:
            B = rep[b]
            ok = A["g"] == B["g"]
            ok = ok or bool(A["t"] and B["t"] and fuzz.token_set_ratio(A["t"], B["t"]) >= ut)
            for k in ("r", "x", "c"):
                if ok:
                    break
                if A[k] and B[k] and len(A[k]) > 40 and len(B[k]) > 40:
                    ok = fuzz.token_set_ratio(A[k], B[k]) >= uc
            if ok:
                padre[raiz(a)] = raiz(b)
    comp: Dict[int, List[int]] = defaultdict(list)
    for u in us:
        comp[raiz(u)].append(u)
    comps = [sorted(v) for v in comp.values() if len(v) >= 2][:MAX_CANDIDATOS]
    return {"comps": comps, "miembros": miembros}


# ─── llamada al agente ─────────────────────────────────────────────────────

def _payload(comps: Sequence[List[int]], rows: List[dict], mapa: Dict[int, int],
             etiquetas: Dict[int, dict], km: dict, offset: int = 0) -> List[dict]:
    tit_col = (km or {}).get("titulo", "Título")
    out = []
    for k, comp in enumerate(comps):
        notas = []
        for u in comp:
            r = rows[u]
            e = etiquetas.get(mapa.get(u)) or {}
            notas.append({"id": str(u),
                          "titulo_solo_para_agrupar": str(r.get(tit_col) or r.get("Título") or "")[:90],
                          "contexto": str(r.get("Contexto analizado") or r.get("CuerpoEs") or "")[:TOPE_CTX],
                          "medio": str(r.get("Medio") or "")[:40],
                          "subtema_actual": e.get("sub_tema") or e.get("subtema") or "",
                          "tono_actual": e.get("tono") or ""})
        out.append({"id": "c%d" % (offset + k + 1), "notas": notas})
    return out


def evaluar(cands: Dict[str, Any], rows: List[dict], mapa: Dict[int, int],
            etiquetas: Dict[int, dict], km: Optional[dict] = None, query_fn=None,
            lote: int = LOTE_CANDIDATOS, modelo: Optional[str] = None) -> List[dict]:
    """Llama al agente (una vez por lote, lotes en paralelo). Devuelve
    decisiones normalizadas [{agrupar:[u], separar:[u], subtema, tono,
    subtemas:{u:str}, motivo}] con u = nota representante. Nunca lanza."""
    import motor_haiku as mh
    comps = list((cands or {}).get("comps") or [])
    ULTIMO.clear()
    ULTIMO.update(candidatos=len(comps), lotes=0, costo_usd=0.0, error=None, decisiones=[])
    if not comps:
        return []
    try:
        sdk = mh._sdk()
        query_fn = query_fn or QUERY_FN
        if query_fn is None:
            mh._preparar_credencial()
            query_fn = sdk.query
    except Exception as e:  # noqa: BLE001
        ULTIMO["error"] = str(e)
        return []
    candidatos = comps
    lotes = [list(candidatos[i:i + lote]) for i in range(0, len(candidatos), lote)]
    ULTIMO["lotes"] = len(lotes)
    textos: List[str] = [""] * len(lotes)
    costos: List[float] = [0.0] * len(lotes)

    async def _uno(n: int, chunk):
        prompt = json.dumps({"candidatos": _payload(chunk, rows, mapa, etiquetas, km, n * lote)},
                            ensure_ascii=False)
        with tempfile.TemporaryDirectory(prefix="evaluador_agrupacion_") as cwd:
            op = sdk.ClaudeAgentOptions(
                model=modelo or mh.MODELO_HAIKU, system_prompt=PROMPT_EVALUADOR, tools=[],
                allowed_tools=[], setting_sources=[], permission_mode="default", cwd=cwd,
                max_turns=1, max_budget_usd=round(min(1.0, 0.03 + 0.004 * len(chunk)), 2),
                disallowed_tools=["Bash", "Edit", "Read", "Grep", "Glob", "Write", "WebSearch",
                                  "WebFetch", "Agent"],
                stderr=lambda linea: None)
            async for m in query_fn(prompt=prompt, options=op):
                if isinstance(m, sdk.ResultMessage):
                    costos[n] = float(m.total_cost_usd or 0.0)
                    textos[n] = m.result or ""

    async def _todos():
        await asyncio.gather(*[_uno(n, c) for n, c in enumerate(lotes)])

    try:
        mh._ejecutar(_todos())
    except Exception as e:  # noqa: BLE001
        ULTIMO["error"] = "%s: %s" % (type(e).__name__, e)
    ULTIMO["costo_usd"] = round(sum(costos), 6)
    por_cid = {"c%d" % (k + 1): comp for k, comp in enumerate(candidatos)}
    decisiones: List[dict] = []
    for txt in textos:
        ini, fin = txt.find("{"), txt.rfind("}")
        try:
            data = json.loads(txt[ini:fin + 1]) if ini >= 0 else {}
        except ValueError:
            continue
        for d in data.get("grupos") or []:
            comp = por_cid.get(str(d.get("id")))
            if not comp:
                continue
            decisiones.append(normalizar_decision(d, comp))
    ULTIMO["decisiones"] = [{k: (sorted(v) if isinstance(v, set) else v) for k, v in d.items()}
                            for d in decisiones]
    return decisiones


def normalizar_decision(d: dict, comp: Sequence[int]) -> dict:
    """{agrupar:[gid…], separar:[gid…], subtema, tono, subtemas:{gid:str}, motivo}."""
    import motor_haiku as mh
    comp = list(comp)
    dec = str(d.get("decision") or "").strip().lower()
    subs = {}
    for k, v in (d.get("subtemas") or {}).items():
        try:
            subs[int(k)] = str(v or "").strip().rstrip(".")
        except ValueError:
            pass
    tono = d.get("tono") if d.get("tono") in mh.TONOS else None
    if dec == "agrupar":
        ids = []
        for x in (d.get("ids") or comp):
            try:
                if int(x) in comp:
                    ids.append(int(x))
            except (TypeError, ValueError):
                pass
        ids = ids if len(ids) >= 2 else []
        resto = [g for g in comp if g not in ids]
        return {"agrupar": ids, "separar": resto if ids else comp,
                "subtema": str(d.get("subtema") or "").strip().rstrip("."), "tono": tono,
                "subtemas": subs, "motivo": str(d.get("motivo") or "")}
    return {"agrupar": [], "separar": comp, "subtema": "", "tono": None, "subtemas": subs,
            "motivo": str(d.get("motivo") or "")}


# ─── aplicación ────────────────────────────────────────────────────────────

def _sub_valido(s: str) -> bool:
    import motor_haiku as mh
    return bool(s) and not mh.problemas_subtema(s)


def _pares_separados(decisiones: Sequence[dict]):
    pares = set()
    for d in decisiones:
        bloques = ([d["agrupar"]] if d["agrupar"] else []) + [[g] for g in d["separar"]]
        for i, a in enumerate(bloques):
            for b in bloques[i + 1:]:
                for x in a:
                    for y in b:
                        pares.add(frozenset((x, y)))
    return pares


def aplicar_inicial(decisiones: Sequence[dict], cands: Dict[str, Any], mapa: Dict[int, int],
                    etiquetas: Dict[int, dict]) -> dict:
    """Antes de las unificaciones deterministas (nivel grupo): "agrupar"
    fija subtema y tono comunes en los grupos de esas notas; "separar"
    le da a cada grupo su subtema propio cuando el grupo no mezcla notas
    de bloques distintos. Devuelve el estado que usa `hacer_cumplir`."""
    miembros = (cands or {}).get("miembros") or {}
    propios: Dict[int, str] = {}
    for d in decisiones:
        bloque_de: Dict[int, int] = {}
        for u in d["agrupar"]:
            bloque_de[u] = -1
        for u in d["separar"]:
            bloque_de[u] = u
        gid_bloques: Dict[int, set] = defaultdict(set)
        for u, b in bloque_de.items():
            gid_bloques[mapa.get(u)].add(b)
        if d["agrupar"]:
            sub = d["subtema"] if _sub_valido(d["subtema"]) else Counter(
                (etiquetas.get(mapa.get(u)) or {}).get("sub_tema") or "" for u in d["agrupar"]).most_common(1)[0][0]
            d["subtema_final"] = sub
            for g in {mapa.get(u) for u in d["agrupar"]}:
                if gid_bloques[g] != {-1}:
                    continue          # grupo mixto: se resuelve por fila al final
                e = etiquetas.setdefault(g, {})
                if sub:
                    e["sub_tema"] = sub
                if d["tono"]:
                    e["tono"] = d["tono"]
                e.setdefault("flags", {})["evaluador_agrupar"] = True
        usados = Counter(_n((etiquetas.get(mapa.get(u)) or {}).get("sub_tema")) for u in d["separar"])
        for u in d["separar"]:
            g = mapa.get(u)
            e = etiquetas.setdefault(g, {})
            cand = d["subtemas"].get(u) or ""
            if not _sub_valido(cand):
                cand = ""
            actual = e.get("sub_tema") or ""
            choca = (usados[_n(actual)] > 1 or _n(actual) == _n(d.get("subtema_final"))
                     or gid_bloques[g] != {u})
            propios[u] = cand or actual
            if cand and choca and gid_bloques[g] == {u}:
                e["sub_tema"] = cand
            e.setdefault("flags", {})["evaluador_separar"] = True
    return {"propios": propios, "pares": _pares_separados(decisiones), "miembros": miembros}


def hacer_cumplir(decisiones: Sequence[dict], estado: dict, rows: List[dict],
                  mapa: Dict[int, int]) -> Dict[str, Any]:
    """Último paso sobre las filas ya volcadas (después de TODAS las
    unificaciones deterministas, también las que trabajan por fila): deshace
    lo que una unificación haya fusionado contra un "separar" y re-aplica
    cada "agrupar" (mismo Subtema_IA; Tono_IA = el mayoritario tras las
    reglas de tono, o el del evaluador si hay empate). Devuelve un informe."""
    miembros = estado.get("miembros") or {}
    propios = estado.get("propios") or {}

    def filas(u):
        return [i for i in miembros.get(u, [u]) if i < len(rows) and not rows[i].get("is_duplicate")]

    def subs(u):
        return {_n(rows[i].get("Subtema_IA")) for i in filas(u)}

    def poner(u, sub):
        for i in filas(u):
            rows[i]["Subtema_IA"] = sub

    restaurados, unificados = [], []
    for d in decisiones:
        if not d["agrupar"]:
            continue
        sub = d.get("subtema_final") or ""
        tonos = Counter(rows[i].get("Tono_IA") for u in d["agrupar"] for i in filas(u))
        top = tonos.most_common()
        tono = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else (d["tono"] or (top[0][0] if top else None))
        for u in d["agrupar"]:
            if sub:
                poner(u, sub)
            if tono:
                for i in filas(u):
                    rows[i]["Tono_IA"] = tono
        unificados.append(sorted(d["agrupar"]))
    for par in sorted(estado.get("pares") or (), key=sorted):
        a, b = sorted(par)
        if not (subs(a) & subs(b)):
            continue
        hecho = False
        for u, otro in ((a, b), (b, a)):
            p = propios.get(u)
            if u in propios and p and _n(p) not in subs(otro):
                poner(u, p)
                hecho = True
        if hecho:
            restaurados.append([a, b])
    return {"pares_restaurados": restaurados, "grupos_unificados": unificados}
