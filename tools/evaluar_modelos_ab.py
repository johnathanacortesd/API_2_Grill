#!/usr/bin/env python3
"""Comparador A/B de modelos para tono/tema/subtema (v4.42).

Corre el MISMO flujo del motor (construir_grupos + etiquetar_grupos) con dos
modelos de OpenAI sobre un dossier y reporta, por modelo:

- precisión de tono vs la columna de tono esperado (si se indica)
- acuerdo tono/tema/subtema entre los dos modelos
- costo aproximado de cada corrida

Sirve para responder "¿gpt-6-luna me da igual o mejor calidad que
gpt-4.1-nano?" con números sobre sus propios dossiers, antes de que el
modelo viejo se apague el 2026-10-23.

Uso:
    python3 tools/evaluar_modelos_ab.py --xlsx dossier.xlsx \\
        --marca "Brigard & Urrutia" --alias "Brigard Urrutia" \\
        --col-titulo "Título" --col-cuerpo "Cuerpo" \\
        --col-tono-esperado "Tono_esperado" \\
        --modelos "gpt-4.1-nano-2025-04-14,gpt-6-luna" --max-grupos 25

La API key sale de --api-key o de la variable OPENAI_API_KEY.
No usa la revisión de Jev (typesafe): la comparación es solo del motor OpenAI.
"""
import argparse
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook  # noqa: E402

import analyzer_tono_tema as az  # noqa: E402


def _norm(s):
    return str(s or "").strip().lower()


def leer_dossier(xlsx, hoja, col_titulo, col_cuerpo, col_tono_esp, col_tema_esp):
    wb = load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb[hoja] if hoja else wb.active
    filas = list(ws.iter_rows(values_only=True))
    wb.close()
    headers = [_norm(h) for h in filas[0]]
    def col(nombre):
        if not nombre:
            return None
        n = _norm(nombre)
        return headers.index(n) if n in headers else None
    i_tit, i_cue = col(col_titulo), col(col_cuerpo)
    i_ton, i_tem = col(col_tono_esp), col(col_tema_esp)
    if i_tit is None or i_cue is None:
        raise SystemExit("No encontré las columnas de título/cuerpo: %s" % headers)
    rows = []
    for f in filas[1:]:
        tit = str(f[i_tit] or "").strip()
        cue = str(f[i_cue] or "").strip()
        if not tit and not cue:
            continue
        r = {"Título": tit, "Cuerpo": cue}
        if i_ton is not None:
            r["Tono_esperado"] = str(f[i_ton] or "").strip().capitalize()
        if i_tem is not None:
            r["Tema_esperado"] = str(f[i_tem] or "").strip()
        rows.append(r)
    return rows


def esperado_por_grupo(grupos, rows, campo):
    """Tono/tema esperado por grupo: el del representante (primer idx)."""
    out = {}
    for g in grupos:
        idxs = g.get("idxs") or []
        if isinstance(idxs, str):  # tolera grupos con idxs serializados
            try:
                idxs = ast.literal_eval(idxs)
            except Exception:
                idxs = []
        val = ""
        for i in idxs:
            v = rows[i].get(campo) or ""
            if v:
                val = v
                break
        out[g["grupo"]] = val
    return out


def correr(modelo, grupos, cfg_base, votos):
    cfg = dict(cfg_base)
    cfg["model"] = modelo
    uso = {}
    etiquetas = az.etiquetar_grupos(cfg, grupos, tam_lote=10, workers=4,
                                    votos=votos, uso=uso)
    costo, pin, pout = az._costo_aprox_usd(modelo, uso)
    return etiquetas, uso, costo


def acuerdo_subtema(a, b):
    from rapidfuzz import fuzz
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return a == b
    return fuzz.token_set_ratio(a, b) >= 85


def main():
    ap = argparse.ArgumentParser(description="Comparador A/B tono/tema/subtema")
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--hoja", default=None)
    ap.add_argument("--marca", required=True)
    ap.add_argument("--alias", default="")
    ap.add_argument("--voceros", default="")
    ap.add_argument("--criterio-texto", default="")
    ap.add_argument("--col-titulo", default="Título")
    ap.add_argument("--col-cuerpo", default="Cuerpo")
    ap.add_argument("--col-tono-esperado", default=None)
    ap.add_argument("--col-tema-esperado", default=None)
    ap.add_argument("--modelos", default="gpt-4.1-nano-2025-04-14,gpt-6-luna")
    ap.add_argument("--max-grupos", type=int, default=25)
    ap.add_argument("--votos", type=int, default=2,
                    help="igual que la app (2 = mayoría, empate cae a Neutro)")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--salida", default=None, help="xlsx con el detalle por grupo")
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise SystemExit("Falta la API key: --api-key o variable OPENAI_API_KEY")

    rows = leer_dossier(args.xlsx, args.hoja, args.col_titulo, args.col_cuerpo,
                        args.col_tono_esperado, args.col_tema_esperado)
    km = {"titulo": "Título"}
    grupos, _mapa = az.construir_grupos(rows, km)
    grupos = grupos[:args.max_grupos]
    print("filas=%d grupos=%d (evaluando %d)" % (len(rows), len(_mapa), len(grupos)))
    if not grupos:
        raise SystemExit("Sin grupos para evaluar.")

    cfg_base = {
        "api_key": api_key,
        "base_url": "https://api.openai.com/v1",
        "brand": args.marca,
        "aliases": [a.strip() for a in args.alias.split(",") if a.strip()],
        "voceros": [v.strip() for v in args.voceros.split(",") if v.strip()],
        "criterio": "Aspectual estricto",
        "criterio_texto": args.criterio_texto,
        "timeout": 60,
    }
    modelos = [m.strip() for m in args.modelos.split(",") if m.strip()]
    if len(modelos) != 2:
        raise SystemExit("--modelos debe traer exactamente 2 modelos separados por coma")

    res = {}
    for m in modelos:
        print("corriendo %s ..." % m, flush=True)
        etiquetas, uso, costo = correr(m, grupos, cfg_base, args.votos)
        res[m] = {"etiquetas": etiquetas, "uso": uso, "costo": costo}
        print("  tokens in=%s out=%s costo≈$%s" % (
            uso.get("input"), uso.get("output"), costo))

    tono_esp = esperado_por_grupo(grupos, rows, "Tono_esperado") if args.col_tono_esperado else {}
    tema_esp = esperado_por_grupo(grupos, rows, "Tema_esperado") if args.col_tema_esperado else {}
    eA, eB = res[modelos[0]]["etiquetas"], res[modelos[1]]["etiquetas"]

    detalle = []
    for g in grupos:
        gid = g["grupo"]
        a, b = eA.get(gid, {}), eB.get(gid, {})
        detalle.append({
            "grupo": gid, "titulo": g["titulo"],
            "tono_%s" % modelos[0]: a.get("tono"), "tono_%s" % modelos[1]: b.get("tono"),
            "tema_%s" % modelos[0]: a.get("tema"), "tema_%s" % modelos[1]: b.get("tema"),
            "subtema_%s" % modelos[0]: a.get("sub_tema"),
            "subtema_%s" % modelos[1]: b.get("sub_tema"),
            "tono_esperado": tono_esp.get(gid, ""),
            "tema_esperado": tema_esp.get(gid, ""),
        })

    n = len(detalle)
    agree_tono = sum(1 for d in detalle if d["tono_%s" % modelos[0]] == d["tono_%s" % modelos[1]])
    agree_tema = sum(1 for d in detalle
                     if _norm(d["tema_%s" % modelos[0]]) == _norm(d["tema_%s" % modelos[1]]))
    agree_sub = sum(1 for d in detalle
                    if acuerdo_subtema(d["subtema_%s" % modelos[0]], d["subtema_%s" % modelos[1]]))

    print("\n== ACUERDO %s vs %s ==" % (modelos[0], modelos[1]))
    print("tono:    %d/%d (%.1f%%)" % (agree_tono, n, 100.0 * agree_tono / n))
    print("tema:    %d/%d (%.1f%%)" % (agree_tema, n, 100.0 * agree_tema / n))
    print("subtema: %d/%d (%.1f%%, similitud>=85)" % (agree_sub, n, 100.0 * agree_sub / n))

    if tono_esp:
        for m in modelos:
            ok = sum(1 for d in detalle
                     if d["tono_esperado"] and d["tono_%s" % m] == d["tono_esperado"])
            tot = sum(1 for d in detalle if d["tono_esperado"])
            print("precisión tono %s vs esperado: %d/%d (%.1f%%)" % (m, ok, tot, 100.0 * ok / tot if tot else 0))
    if tema_esp:
        for m in modelos:
            ok = sum(1 for d in detalle
                     if d["tema_esperado"] and _norm(d["tema_%s" % m]) == _norm(d["tema_esperado"]))
            tot = sum(1 for d in detalle if d["tema_esperado"])
            print("precisión tema %s vs esperado: %d/%d (%.1f%%)" % (m, ok, tot, 100.0 * ok / tot if tot else 0))

    print("\n== DESACUERDOS DE TONO (revisar a mano) ==")
    for d in detalle:
        if d["tono_%s" % modelos[0]] != d["tono_%s" % modelos[1]]:
            print("[%s] %s=%s | %s=%s | esperado=%s | %s" % (
                d["grupo"], modelos[0], d["tono_%s" % modelos[0]],
                modelos[1], d["tono_%s" % modelos[1]],
                d["tono_esperado"] or "-", d["titulo"][:80]))

    if args.salida:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "comparacion"
        cols = list(detalle[0].keys())
        ws.append(cols)
        for d in detalle:
            ws.append([d[c] for c in cols])
        wb.save(args.salida)
        print("\ndetalle guardado en %s" % args.salida)


if __name__ == "__main__":
    main()
