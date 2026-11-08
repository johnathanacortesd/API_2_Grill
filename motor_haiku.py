"""Motor alterno de Tono/Subtema/Tema con un agente Claude Haiku (Claude Agent SDK).

Es una OPCIÓN junto al motor OpenAI del analizador: el pipeline arma los grupos
de noticias y su "Contexto analizado" igual que siempre, y en lugar de
`etiquetar_grupos` (OpenAI) llama a `clasificar_con_haiku`. El agente:

- lee las noticias por páginas con una herramienta MCP en proceso (solo id,
  titulo y contexto; los títulos repetidos llegan con `igual_a`),
- guarda tono/subtema (y tema, si se pide) con otra herramienta que valida el
  tono y hereda los valores en los duplicados,
- revisa la consistencia con el subagente `revisor-subtemas` sobre los grupos
  candidatos que arma `candidatos_consistencia` (rapidfuzz, sin IA).

Todo pasa EN MEMORIA: las herramientas leen y escriben un dict, no un xlsx.
La credencial es solo ANTHROPIC_API_KEY (la fija app.py desde st.secrets).
Diseño tomado de clausifier/agente.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Sequence

MODELO_HAIKU = "claude-haiku-5-5"
POR_PAGINA = 25           # noticias por llamada a leer_noticias
TOPE_TEXTO = 1200         # caracteres de contexto por noticia
TOPE_TITULO = 160
TOPE_ETIQUETA_TITULO = 90  # título en leer_noticias: solo etiqueta de agrupación
UMBRAL_TITULO = 0.92
UMBRAL_CONTEXTO = 0.85
UMBRAL_SUBTEMA = 0.90
UMBRAL_SOBRE = 0.35
MIN_CTX = 120
GRUPOS_POR_PAGINA = 30
TOPE_CTX_GRUPO = 300
TONOS = ("Positivo", "Neutro", "Negativo")

# Progreso para la app (las tools corren en este mismo proceso).
PROGRESO: Dict[str, Any] = {}

# Estado de UNA corrida. `_CANDADO` serializa corridas concurrentes del mismo proceso.
_ESTADO: Dict[str, Any] = {}
_CANDADO = threading.Lock()


class ErrorMotorHaiku(RuntimeError):
    """Falta la credencial o el SDK no está instalado."""


# ─── utilidades ───────────────────────────────────────────────────────────

def _simple(texto: Any) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t.lower()).split())


try:
    from rapidfuzz.fuzz import ratio as _rf_ratio

    def _similitud(a: str, b: str) -> float:
        return _rf_ratio(a, b) / 100.0
except ImportError:  # pragma: no cover - respaldo sin dependencia
    from difflib import SequenceMatcher

    def _similitud(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()


# ─── forma del subtema (validador determinista, sin IA) ───────────────────
# Regla del usuario 2026-11-07: el subtema es una frase nominal de 3 a 7
# palabras, nunca empieza con verbo (infinitivo, conjugado, gerundio) y
# nunca es un extracto o casi-copia del título o del contexto.

MIN_PAL_SUBTEMA, MAX_PAL_SUBTEMA = 3, 7
MAX_RECHAZOS = 2          # tras 2 rechazos de un id se acepta con marca
_VACIAS_SUBTEMA = {"de", "del", "la", "las", "el", "los", "en", "y", "e", "a", "al", "para", "por",
                   "con", "un", "una", "o", "u"}

_NO_VERBO = {
    "mar", "lugar", "hogar", "taller", "mujer", "poder", "deber", "placer", "ser", "cancer",
    "bienestar", "militar", "popular", "ejecucion", "altar", "collar", "azucar", "dolar", "par",
    "bar", "martir", "lider", "caracter", "alquiler", "cadaver", "master", "primer", "familiar",
    "particular", "escolar", "titular", "celular", "solar", "polar", "pilar", "hangar", "lunar",
    "auxiliar", "regular", "similar", "singular", "nuclear", "molecular", "circular", "muscular",
    "ejemplar", "peculiar", "estelar", "secular", "nectar", "radar", "menester", "sueter",
    "alfiler", "quehacer", "haber", "parecer", "amanecer", "atardecer", "saber", "elixir",
    "escobar", "aguilar", "bolivar", "salazar", "villamizar", "cesar", "gaspar", "oscar",
    "baltasar", "omar", "edgar", "vilar", "linares", "palmar", "manzanar", "olivar", "pinar",
    "comando", "mando", "bando", "contrabando", "fernando", "orlando", "rolando", "armando",
    "guayaba", "haba", "traba", "baba", "aaron", "super", "ester", "esther", "chofer", "cuadrar",
    "mujeres", "ayer", "mayor", "menor", "exterior", "interior", "ingeniero", "tener",
}
# Lemas frecuentes en titulares: se generan sus formas de 3.ª persona.
_LEMAS = """anunciar realizar presentar buscar entregar lanzar firmar denunciar alertar advertir
pedir exigir rechazar criticar señalar asegurar confirmar solicitar recibir obtener lograr liderar
adelantar ejecutar mejorar aumentar disminuir reducir destinar aprobar celebrar visitar recorrer
supervisar verificar capacitar inaugurar instalar abrir cerrar iniciar terminar culminar llegar
morir matar atacar capturar revelar responder negar acusar perder ganar caer subir bajar crecer
proponer querer poner hacer tener ir pasar cumplir enfrentar ordenar prohibir suspender amenazar
desaparecer huir volver regresar llamar invitar promover impulsar fortalecer acompañar atender
defender proteger garantizar reclamar insistir afirmar explicar indicar informar declarar
reportar registrar dejar mantener seguir trabajar participar evaluar estudiar analizar
investigar sancionar condenar destituir anular radicar""".split()
_FORMAS_VERBO = set()
for _l in _LEMAS:
    _r, _t = _l[:-2], _l[-2:]
    _r = _r.replace("ñ", "n")
    if _t == "ar":
        _FORMAS_VERBO |= {_r + s for s in ("a", "an", "o", "aron", "aba", "aban", "ara", "aran")}
    else:
        _FORMAS_VERBO |= {_r + s for s in ("e", "en", "io", "ieron", "ia", "ian", "era", "eran")}
_FORMAS_VERBO |= set("""quiere quieren quiso quisieron pide piden pidio pidieron sigue siguen
siguio siguieron dice dicen dijo dijeron va van fue fueron es son esta estan hay hubo tiene
tienen tuvo hace hacen hizo pone ponen puso propone proponen muere mueren murio murieron vuelve
vuelven volvio cae caen cayo huye huyen huyo pierde pierden niega niegan cierra cierran
advierte advierten mantiene mantienen obtiene obtienen dio dieron vio trajo pudo puede pueden
debe deben""".split())
_FIN_VERBO = ("aron", "ieron", "aban", "ando", "iendo", "yendo", "abamos", "iamos")
# Clausifier 2026-11-08: formas modales/auxiliares y conjugadas frecuentes
# que la heurística de terminaciones no ve ("Puede agendar un turno",
# "Podrán acceder", "Tenía información"). Se comparan sin tildes.
_FORMAS_VERBO |= set("""puede pueden podra podran podria podrian pudieron podia podian
debe deben debera deberan deberia deberian debia debian quiere quieren queria querian
va van iba iban hay habra habria habia habian ha han hubo tiene tienen tenia tenian tendra
tendran sera seran seria serian eran es son fue fueron esta estan estaba estaban estuvo
hace hacen hacia hacian hara haran haria se sigue siguen dice dicen decia dijo dara daran
llega llegan sale salen viene vienen vino tendria pide piden ofrece ofrecen permite permiten
busca buscan""".split())
# Formas finitas inequívocas (con tilde, tal como se escriben) que tampoco
# pueden aparecer DENTRO del subtema: "Presunto hacker tenía información".
# Se excluyen las ambiguas con sustantivos o demostrativos (esta, ha, se).
# Clausifier 2026-11-08: sustantivos que coinciden con una forma generada
# de los lemas (anuncio, entrega, denuncia, alerta…). El prompt los pide
# como inicio correcto del subtema; el verbo ("Anuncia", "Entregó") sigue
# rechazado porque su forma es otra.
_NO_VERBO |= set("""anuncio entrega denuncia alerta firma visita captura mejora condena
amenaza rechazo aumento inicio registro baja reclamo critica apoyo pedido cambio control
trabajo estudio paso regreso llamado termino adelanto respuesta llegada""".split())
_FORMAS_VERBO -= _NO_VERBO

_FINITOS_INTERNOS = set("""tenía tenían había habían podía podían debía debían quería querían
hacía hacían decía decían estaba estaban eran fue fueron es son está están hay tiene
tienen puede pueden podrá podrán podría deberá debe deben será serán habrá va van iba iban
hizo dijo dio pidió murió""".split())


def contiene_verbo_finito(subtema: str) -> Optional[str]:
    """Primera forma verbal finita que aparece después de la primera palabra
    (la inicial ya la revisa `empieza_con_verbo`). Solo formas inequívocas:
    lista cerrada + pretérito -ó de 5+ letras + -aron/-ieron."""
    w = [x.strip("\"'«»“”¡¿()!?.,;:").lower() for x in str(subtema or "").split()[1:]]
    for t in w:
        n = _norm_tok(t)
        if not n or n in _NO_VERBO:
            continue
        if t in _FINITOS_INTERNOS or (len(t) >= 5 and t.endswith("ó"))                 or (len(n) > 5 and n.endswith(("aron", "ieron"))):
            return t
    return None



def _norm_tok(tok: str) -> str:
    t = unicodedata.normalize("NFKD", str(tok or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", t.lower())


def empieza_con_verbo(subtema: str) -> bool:
    """True si la primera palabra del subtema es un verbo (infinitivo,
    conjugado o gerundio). Usa las listas de Grill y una heurística de
    terminaciones con excepciones de sustantivos frecuentes."""
    w = str(subtema or "").split()
    if not w:
        return False
    crudo = w[0].strip("\"'«»“”¡¿()").lower()
    n = _norm_tok(crudo)
    if not n or n in _NO_VERBO:
        return False
    try:  # misma regla que validar() de Grill
        import analyzer_tono_tema as _a
        if _a._empieza_con_verbo(w):
            return True
    except Exception:  # noqa: BLE001
        pass
    if n in _FORMAS_VERBO:
        return True
    if crudo.endswith(("ían", "íamos")) or (len(crudo) >= 4 and crudo.endswith("ó")):
        return True
    if len(n) > 4 and n.endswith(_FIN_VERBO):
        return True
    if len(n) >= 4 and n.endswith(("ar", "er", "ir")):
        return True  # infinitivo
    return False


def _toks(s: str) -> List[str]:
    return _simple(s).split()


def _corrida_contigua(sub: List[str], fuente: List[str]) -> int:
    """Largo de la corrida contigua más larga de `sub` que aparece tal cual en `fuente`."""
    mejor = 0
    if not sub or not fuente:
        return 0
    for i in range(len(sub)):
        for j in range(len(fuente)):
            k = 0
            while i + k < len(sub) and j + k < len(fuente) and sub[i + k] == fuente[j + k]:
                k += 1
            mejor = max(mejor, k)
    return mejor


def es_copia(subtema: str, titulo: str, contexto: str = "") -> Optional[str]:
    """Motivo si el subtema es extracto/casi-copia del título o del contexto."""
    sub = _toks(subtema)
    if not sub:
        return None
    try:
        import analyzer_tono_tema as _a
        if titulo and "copia_titular" in [x.split("(")[0] for x in
                                          _a.validar(subtema, "Neutro", [titulo])]:
            return "copia del título"
    except Exception:  # noqa: BLE001
        pass
    if titulo and _similitud(" ".join(sub), _simple(titulo)) >= 0.85:
        return "casi igual al título"
    for nombre, fuente in (("título", titulo), ("contexto", contexto)):
        if fuente and _corrida_contigua(sub, _toks(fuente)) >= max(3, 0.7 * len(sub)):
            return "extracto literal del %s" % nombre
    return None


def problemas_subtema(subtema: str, titulo: str = "", contexto: str = "") -> List[str]:
    """Problemas de forma del subtema. Lista vacía = válido."""
    s = str(subtema or "").strip()
    if not s:
        return ["vacío"]
    p = []
    # Clausifier 2026-11-08: el largo 3-7 cuenta palabras de contenido; los
    # artículos y preposiciones no suman ("Jornada de esterilización gratuita
    # de mascotas en Engativá" = 5). Tope duro de 10 palabras en total.
    n = len([x for x in s.split() if _norm_tok(x) not in _VACIAS_SUBTEMA])
    if n < MIN_PAL_SUBTEMA or n > MAX_PAL_SUBTEMA or len(s.split()) > 10:
        p.append("tiene %d palabras de contenido (debe tener 3 a 7)" % n)
    if empieza_con_verbo(s):
        p.append("empieza con verbo (%s)" % s.split()[0])
    vf = contiene_verbo_finito(s)
    if vf:
        p.append("contiene verbo conjugado (%s)" % vf)
    c = es_copia(s, titulo, contexto)
    if c:
        p.append(c)
    return p


def _texto(contenido: str, error: bool = False) -> Dict[str, Any]:
    r: Dict[str, Any] = {"content": [{"type": "text", "text": contenido}]}
    if error:
        r["is_error"] = True
    return r


def _avisar(pct: int, msg: str) -> None:
    cb = _ESTADO.get("progreso_cb")
    if cb:
        try:
            cb(pct, msg)
        except Exception:  # noqa: BLE001 - la UI nunca tumba al agente
            pass


def preparar_estado(items: Sequence[dict], incluir_tema: bool,
                    taxonomia: Optional[Sequence[str]] = None,
                    progreso_cb: Optional[Callable[[int, str], None]] = None) -> None:
    """Carga los items en memoria. Cada item: {id, titulo, contexto, duplicado_de?}.

    `duplicado_de` (opcional) marca un duplicado explícito; además, los títulos
    idénticos se juntan con `igual_a`. Ninguno de los dos se manda a clasificar:
    heredan los valores de su original.
    """
    orden: List[str] = []
    datos: Dict[str, dict] = {}
    hereda: Dict[str, str] = {}
    explicitos = set()
    por_titulo: Dict[str, str] = {}
    for it in items:
        fid = str(it["id"])
        orden.append(fid)
        titulo = str(it.get("titulo") or "")
        datos[fid] = {"id": fid, "titulo": titulo, "contexto": str(it.get("contexto") or "")}
        dup = it.get("duplicado_de")
        if dup not in (None, "") and str(dup) != fid:
            hereda[fid] = str(dup)
            explicitos.add(fid)
            continue
        clave = _simple(titulo)
        if clave and clave in por_titulo:
            hereda[fid] = por_titulo[clave]
        elif clave:
            por_titulo[clave] = fid
    tax = [str(t).strip() for t in (taxonomia or []) if str(t).strip()]
    _ESTADO.clear()
    _ESTADO.update(orden=orden, datos=datos, hereda=hereda, explicitos=explicitos, res={},
                   incluir_tema=bool(incluir_tema), taxonomia=tax,
                   tax_norm={_simple(t): t for t in tax}, progreso_cb=progreso_cb)
    PROGRESO.clear()
    PROGRESO.update(paso="Clasificando", total=len(orden) - len(hereda), leidas=0,
                    guardadas=0, faltan=None, revisado=False, cambios_revision=0, grupos=0)


def _raiz(fid: str) -> str:
    vistos = set()
    while fid in _ESTADO["hereda"] and fid not in vistos:
        vistos.add(fid)
        fid = _ESTADO["hereda"][fid]
    return fid


def _propagar() -> None:
    res = _ESTADO["res"]
    for fid in _ESTADO["hereda"]:
        o = res.get(_raiz(fid))
        if o:
            res[fid] = dict(o)


def _faltantes() -> List[str]:
    res, incluir = _ESTADO["res"], _ESTADO["incluir_tema"]
    out = []
    for fid in _ESTADO["orden"]:
        if fid in _ESTADO["hereda"]:
            continue
        r = res.get(fid) or {}
        if not r.get("tono") or not r.get("subtema") or (incluir and not r.get("tema")):
            out.append(fid)
    return out


# ─── las herramientas (lógica pura, probada sin el SDK) ────────────────────

async def _leer_noticias(args: Dict[str, Any]) -> Dict[str, Any]:
    salida = []
    for fid in _ESTADO["orden"]:
        d = _ESTADO["datos"][fid]
        dup = _ESTADO["hereda"].get(fid)
        if fid in _ESTADO["explicitos"]:
            continue                    # duplicado explícito: ni se envía
        # Clausifier 2026-11-08: el campo principal es el Contexto analizado
        # completo (título + resumen + extracto de marca/alias/vocero, >=403
        # caracteres); el título va solo como etiqueta corta para agrupar.
        reg = {"id": fid, "titulo_solo_para_agrupar": d["titulo"][:TOPE_ETIQUETA_TITULO]}
        if dup is not None:
            reg["igual_a"] = _raiz(fid)
        else:
            reg["contexto"] = d["contexto"][:TOPE_TEXTO]
        salida.append(reg)
    desde = max(0, int(args.get("desde") or 0))
    pagina = salida[desde:desde + POR_PAGINA]
    siguiente = desde + POR_PAGINA if desde + POR_PAGINA < len(salida) else None
    omitidos = len(_ESTADO["orden"]) - len(salida)
    PROGRESO.update(leidas=max(PROGRESO.get("leidas", 0), desde + len(pagina)))
    _avisar(76, "Haiku leyendo noticias %d-%d de %d…" % (desde + 1, desde + len(pagina), len(salida)))
    return _texto(json.dumps({"total": len(salida), "desde": desde, "siguiente": siguiente,
                              "duplicados_omitidos": omitidos, "noticias": pagina},
                             ensure_ascii=False, separators=(",", ":")))


async def _guardar_clasificacion(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        nuevas = json.loads(args["filas_json"])
        if isinstance(nuevas, dict):
            nuevas = nuevas.get("filas") or nuevas.get("correcciones") or []
        nuevas = {str(f["id"]): f for f in nuevas}
        malos = [i for i, f in nuevas.items() if f.get("tono") not in TONOS]
        if malos:
            raise ValueError("tono inválido en ids %s: usa Positivo, Neutro o Negativo" % malos[:10])
        desconocidos = [i for i in nuevas if i not in _ESTADO["datos"]]
        if desconocidos:
            raise ValueError("ids desconocidos %s" % desconocidos[:10])
        tax = _ESTADO["tax_norm"]
        if _ESTADO["incluir_tema"] and tax:
            fuera = [i for i, f in nuevas.items()
                     if f.get("tema") and _simple(f["tema"]) not in tax]
            if fuera:
                raise ValueError("tema fuera de la lista en ids %s: elige uno de %s"
                                 % (fuera[:10], list(tax.values())))
    except Exception as e:  # noqa: BLE001 - el error vuelve como contexto
        return _texto("Error guardando: %s" % e, error=True)

    # Validador determinista de forma: rechaza (no guarda) los subtemas con
    # verbo inicial, copia del título/contexto o fuera de 3-7 palabras. Tras
    # MAX_RECHAZOS rechazos de un id se acepta con marca (la reparación
    # posterior de Grill lo corrige).
    rechazos = _ESTADO.setdefault("rechazos", {})
    marcados = _ESTADO.setdefault("marcados", {})
    rechazadas: Dict[str, List[str]] = {}
    for fid in list(nuevas):
        f = nuevas[fid]
        sub = str(f.get("subtema") or f.get("sub_tema") or "").strip()
        if not sub:
            continue
        d = _ESTADO["datos"].get(_raiz(fid)) or {}
        if _simple(sub) in _ESTADO.get("permitidos", set()):
            continue   # rótulo exacto que fija el perfil del cliente
        prob = problemas_subtema(sub, d.get("titulo", ""), d.get("contexto", ""))
        if not prob:
            marcados.pop(_raiz(fid), None)
            continue
        if rechazos.get(fid, 0) >= MAX_RECHAZOS:
            marcados[_raiz(fid)] = prob
            continue
        rechazos[fid] = rechazos.get(fid, 0) + 1
        _ESTADO.setdefault("log_rechazos", []).append({"id": fid, "subtema": sub, "motivos": prob})
        rechazadas[fid] = prob
        del nuevas[fid]

    res = _ESTADO["res"]
    cambios = 0
    for fid, f in nuevas.items():
        fid = _raiz(fid)
        antes = res.get(fid) or {}
        ahora = {"tono": f["tono"], "subtema": str(f.get("subtema")
                                                  or f.get("sub_tema") or "").strip()}
        if not ahora["subtema"]:
            ahora["subtema"] = antes.get("subtema", "")
        if _ESTADO["incluir_tema"]:
            t = str(f.get("tema") or "").strip() or antes.get("tema", "")
            ahora["tema"] = _ESTADO["tax_norm"].get(_simple(t), t) if t else ""
        cambios += bool(antes.get("subtema")) and any(antes.get(k) != v for k, v in ahora.items())
        res[fid] = ahora
    _propagar()
    faltan = _faltantes()
    PROGRESO.update(guardadas=PROGRESO.get("guardadas", 0) + len(nuevas), faltan=len(faltan))
    if PROGRESO.get("revisado"):
        PROGRESO["cambios_revision"] = PROGRESO.get("cambios_revision", 0) + cambios
    total = max(1, PROGRESO.get("total") or 1)
    _avisar(76 + int(12 * (total - len(faltan)) / total),
            "Haiku clasificó %d de %d noticias" % (total - len(faltan), total))
    if rechazadas:
        det = "; ".join("id %s: %s" % (i, ", ".join(m)) for i, m in list(rechazadas.items())[:40])
        return _texto("SUBTEMAS RECHAZADOS (no se guardaron): %s. Rehaz SOLO esos ids con una frase "
                      "nominal de 3 a 7 palabras que no empiece con verbo y no copie el título ni el "
                      "contexto, y vuelve a llamar guardar_clasificacion. Faltan por clasificar: %d."
                      % (det, len(faltan)), error=True)
    if faltan:
        ids = ", ".join(faltan[:40]) + (", ..." if len(faltan) > 40 else "")
        return _texto("Guardado parcial. Faltan por clasificar: %d (ids: %s). Si quedan páginas, "
                      "sigue leyendo; si ya leíste todas, clasifica esos ids y vuelve a llamar."
                      % (len(faltan), ids))
    return _texto("Guardado. Filas recibidas: %d. Faltan por clasificar: 0." % len(nuevas))


def construir_candidatos() -> List[dict]:
    """Grupos que PODRÍAN ser el mismo hecho y no comparten tono/subtema, más pares con el
    mismo subtema y contextos muy distintos (posible sobreagrupación). Sin IA."""
    regs = []
    for fid in _ESTADO["orden"]:
        if fid in _ESTADO["hereda"]:
            continue
        d, r = _ESTADO["datos"][fid], _ESTADO["res"].get(fid) or {}
        regs.append({"id": fid, "titulo": d["titulo"], "contexto": d["contexto"],
                     "tono": r.get("tono"), "subtema": r.get("subtema") or "",
                     "_t": _simple(d["titulo"]), "_c": _simple(d["contexto"])[:600],
                     "_s": _simple(r.get("subtema"))})
    padre = list(range(len(regs)))

    def raiz(x: int) -> int:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    motivos: Dict[tuple, str] = {}
    sobre: List[tuple] = []
    for a in range(len(regs)):
        for b in range(a + 1, len(regs)):
            ra, rb = regs[a], regs[b]
            motivo = None
            if ra["_t"] and rb["_t"] and _similitud(ra["_t"], rb["_t"]) >= UMBRAL_TITULO:
                motivo = "título casi igual"
            elif min(len(ra["_c"]), len(rb["_c"])) >= MIN_CTX and \
                    _similitud(ra["_c"], rb["_c"]) >= UMBRAL_CONTEXTO:
                motivo = "contexto casi igual"
            elif ra["_s"] and rb["_s"] and ra["_s"] != rb["_s"] and \
                    _similitud(ra["_s"], rb["_s"]) >= UMBRAL_SUBTEMA:
                motivo = "subtema con redacción casi idéntica"
            if motivo:
                motivos[(a, b)] = motivo
                padre[raiz(a)] = raiz(b)
            elif ra["_s"] and ra["_s"] == rb["_s"] and min(len(ra["_c"]), len(rb["_c"])) >= MIN_CTX:
                sim = _similitud(ra["_c"], rb["_c"])
                if sim < UMBRAL_SOBRE:
                    sobre.append((a, b, sim))

    def fila(i: int) -> dict:
        r = regs[i]
        return {"id": r["id"], "tono": r["tono"], "subtema": r["subtema"],
                "titulo": r["titulo"][:TOPE_TITULO], "contexto": r["contexto"][:TOPE_CTX_GRUPO]}

    grupos: Dict[int, List[int]] = {}
    for i in range(len(regs)):
        grupos.setdefault(raiz(i), []).append(i)
    salida = []
    for miembros in grupos.values():
        if len(miembros) < 2 or len({(regs[i]["tono"], regs[i]["_s"]) for i in miembros}) == 1:
            continue
        razones = sorted({m for (a, b), m in motivos.items() if a in miembros})
        salida.append({"tipo": "posible_mismo_hecho", "motivo": ", ".join(razones),
                       "filas": [fila(i) for i in miembros]})
    por_subtema: Dict[str, tuple] = {}
    for a, b, sim in sobre:
        s = regs[a]["_s"]
        if s not in por_subtema or sim < por_subtema[s][2]:
            por_subtema[s] = (a, b, sim)
    for a, b, sim in por_subtema.values():
        salida.append({"tipo": "posible_sobreagrupacion",
                       "motivo": "mismo subtema, contextos poco parecidos (%.2f)" % sim,
                       "filas": [fila(a), fila(b)]})
    for n, g in enumerate(salida):
        g["grupo"] = n
    return salida


async def _candidatos_consistencia(args: Dict[str, Any]) -> Dict[str, Any]:
    grupos = construir_candidatos()
    desde = max(0, int(args.get("desde") or 0))
    pagina = grupos[desde:desde + GRUPOS_POR_PAGINA]
    siguiente = desde + GRUPOS_POR_PAGINA if desde + GRUPOS_POR_PAGINA < len(grupos) else None
    PROGRESO.update(paso="Revisando consistencia", revisado=True, grupos=len(grupos))
    _avisar(89, "Revisor de subtemas: %d grupos candidatos" % len(grupos))
    return _texto(json.dumps({"total": len(grupos), "desde": desde, "siguiente": siguiente,
                              "grupos": pagina}, ensure_ascii=False, separators=(",", ":")))


# ─── prompts ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """<rol>
Eres un analista de monitoreo de medios que clasifica noticias en español de Colombia. Tu trabajo lo usa un equipo que entrega informes a instituciones, así que la consistencia entre filas importa tanto como acertar cada una.
</rol>

<objetivo>
Recibes el CLIENTE, sus ALIAS y VOCEROS. A cada noticia le asignas TONO y SUBTEMA (y TEMA cuando el pedido lo indique) y los guardas con guardar_clasificacion. Las noticias ya vienen con su "contexto" (las oraciones literales donde aparece el cliente). No supongas un cliente que el pedido no nombre.
</objetivo>

<cliente_alias_voceros>
- Una mención de cualquier ALIAS cuenta como mención del cliente.
- Una mención de un VOCERO, o lo que él dice o se dice de él en su rol, cuenta como mención del cliente y pesa en el TONO.
- Si una noticia no menciona al cliente, a sus alias ni a sus voceros: TONO Neutro, salvo que la noticia afecte claramente al cliente.
</cliente_alias_voceros>

<como_trabajas>
Analiza la columna Contexto analizado completa (incluye título, resumen y el extracto de la marca/alias/vocero); el subtema y el tono salen de ese contenido, no de una frase suelta del titular. En leer_noticias ese contenido llega en "contexto"; "titulo_solo_para_agrupar" es solo una etiqueta corta para reconocer notas del mismo hecho: no lo uses para redactar el subtema ni copies fragmentos suyos. MAL "Presunto hacker tenía información inteligencia" (recorte del titular con verbo conjugado) -> BIEN "Captura de hacker con información de inteligencia".

SUBTEMA: clasificación precisa y específica del hecho que cuenta el "contexto" de la noticia. Reglas obligatorias (guardar_clasificacion RECHAZA el subtema que no las cumpla):
- Frase NOMINAL de 3 a 7 palabras que define el hecho (quién, qué) y lo distingue de otros.
- NUNCA empieza con un verbo en ninguna forma: ni infinitivo (Realizar, Volver), ni conjugado (Anuncia, Querían, Denunció), ni gerundio (Realizando), ni participio (Anunciado). Empieza con un sustantivo: Anuncio, Riesgo, Entrega, Denuncia, Alerta.
- NUNCA es un extracto, recorte o casi-copia del título ni del contexto: sintetiza el hecho con tus palabras.
- Específico, no genérico: nada de "Gestión de la alcaldía" ni "Situación de orden público".
- Nunca el nombre del medio, sin comas ni punto final.
Reglas de Grill (literales):
__REGLAS_SUBTEMA__
Ejemplos:
- MAL "querían volver a la guerra" (verbo y extracto) -> BIEN "Riesgo de reactivación del conflicto armado"
- MAL "Anuncia nuevas medidas" (verbo) -> BIEN "Nuevas medidas de protección a líderes sociales"
- MAL "Realizando jornada" (gerundio y genérico) -> BIEN "Jornada de atención a víctimas en Tumaco"
- MAL "Denunció amenazas contra líderes en el Cauca" (verbo y extracto) -> BIEN "Amenazas a líderes sociales del Cauca"
- MAL "La Defensoría del Pueblo emitió una alerta temprana" (copia y verbo) -> BIEN "Alerta temprana por reclutamiento de menores"
- BIEN "Bienestar de la población migrante", "Taller de liderazgo juvenil"

TEMA (solo si el pedido dice INCLUIR TEMA): categoría amplia de 1 a 4 palabras que agrupa varios subtemas (por ejemplo "Seguridad", "Movilidad", "Derechos humanos"). Pocos temas para todo el lote. Si el pedido trae <lista_temas>, el tema DEBE ser exactamente uno de esa lista. Si el pedido dice SIN TEMA, no envíes "tema".

TONO: Positivo, Neutro o Negativo, solo desde la perspectiva del cliente.
- Positivo: gestiones a favor, logros, reconocimientos, inversiones u obras del cliente.
- Negativo: críticas, denuncias, sanciones o afectación directa al cliente.
- Neutro: menciones informativas sin efecto claro para el cliente.
Si el pedido trae un bloque <criterio_tono>, ese criterio del cliente MANDA: aplícalo a cada fila por encima de estas definiciones y pásaselo completo y literal al ayudante revisor-subtemas cuando lo lances.

Consistencia, en este orden:
1. Agrupa las noticias que cuentan el mismo hecho ("igual_a" ya junta títulos idénticos: esas filas no se clasifican, heredan).
2. A cada grupo dale exactamente el mismo SUBTEMA y TONO. Reutiliza un subtema existente cuando el hecho sea el mismo.
3. Cuando guardar_clasificacion diga "Faltan por clasificar: 0", llama candidatos_consistencia con desde=0. Si total es 0, terminas. Si no, lanza el ayudante revisor-subtemas con Agent pasándole el rango de grupos (desde y cuántos) y el criterio del tono; si hay más de 30 grupos, lanza dos en paralelo, cada uno con su mitad. Con sus correcciones llama UNA vez guardar_clasificacion solo con las filas que cambian. No repitas la revisión.
</como_trabajas>

<herramientas>
- mcp__medios__leer_noticias(desde): páginas de noticias; empieza en desde=0, clasifica y guarda esa página, y pide la siguiente con "siguiente" hasta que sea null.
- mcp__medios__guardar_clasificacion(filas_json): lista JSON [{"id":"…","subtema":"…","tono":"Positivo|Neutro|Negativo","tema":"…"}]. Puedes llamarla por tandas; las filas repetidas se corrigen. Dice cuántas filas faltan.
- mcp__medios__candidatos_consistencia(desde): solo al final (paso 3).
- Agent (ayudante revisor-subtemas): solo en el paso 3.
No tienes terminal ni puedes leer ni escribir archivos.
</herramientas>

<formato_de_salida>
Al terminar responde en una sola línea: filas clasificadas y cuántas por tono. Paras cuando no falten filas y la revisión haya terminado.
</formato_de_salida>"""

try:
    from catalogo_tono_tema import REGLAS_SUBTEMA as _REGLAS_GRILL
except Exception:  # pragma: no cover
    _REGLAS_GRILL = ""
SYSTEM_PROMPT = SYSTEM_PROMPT.replace("__REGLAS_SUBTEMA__", _REGLAS_GRILL)

REGLAS_FORMA_REVISOR = (
    "FORMA DEL SUBTEMA (obligatoria; las correcciones que no la cumplan se rechazan): frase nominal "
    "de 3 a 7 palabras; NUNCA empieza con verbo (ni infinitivo, ni conjugado, ni gerundio, ni "
    "participio); NUNCA es extracto ni casi-copia del título o del contexto; específica del hecho "
    "(quién, qué), no genérica. MAL 'querían volver a la guerra' -> BIEN 'Riesgo de reactivación del "
    "conflicto armado'. MAL 'Anuncia nuevas medidas' -> BIEN 'Nuevas medidas de protección a líderes "
    "sociales'. Si ves en un grupo un subtema que viola esta forma, corrígelo aunque el grupo no sea "
    "el mismo hecho.\nReglas de Grill (literales):\n" + _REGLAS_GRILL + "\n"
)

PROMPT_REVISOR = (
    "Eres revisor de consistencia de una clasificación de noticias en español. Recibes un rango de "
    "grupos (desde y cuántos) y quizá un CRITERIO DEL TONO. Lee los grupos con "
    "mcp__medios__candidatos_consistencia (desde), solo los de tu rango.\n"
    "Para cada grupo decide si sus filas cuentan el MISMO hecho: el mismo evento, la misma entidad y "
    "el mismo momento. Mismo tema general o mismo medio NO es el mismo hecho.\n"
    "- Si es el mismo hecho: todas las filas llevan un solo subtema (reutiliza el más específico) y "
    "un solo tono (el que mejor sostiene el contexto).\n"
    "- Si no lo es: déjalas separadas y haz cada subtema lo bastante específico (3 a 7 palabras). "
    "Los grupos 'posible_sobreagrupacion' casi siempre son hechos distintos con un subtema genérico.\n"
    "Nunca fusiones solo porque los subtemas comparten palabras. Tono solo Positivo, Neutro o "
    "Negativo, para el cliente; si te pasan un CRITERIO DEL TONO, decide con él.\n"
    "Responde SOLO con JSON compacto, sin texto alrededor: "
    '{"correcciones":[{"id":"...","subtema":"...","tono":"..."}],'
    '"grupos":[{"grupo":0,"mismo_hecho":true,"razon":"una línea"}]}. '
    "En correcciones incluye solo las filas cuyo subtema o tono cambia."
)


def armar_prompt(brand: str, aliases: Sequence[str], voceros: Sequence[str],
                 criterio_texto: str, incluir_tema: bool,
                 taxonomia: Optional[Sequence[str]] = None, guia_subtema: str = "",
                 tono_pkl: bool = False) -> str:
    partes = ["CLIENTE: %s" % brand]
    if tono_pkl:
        # Clausifier: con PKL de tono el modelo del cliente pone el TONO; el
        # agente no lo analiza (envía Neutro) y se concentra en el SUBTEMA.
        partes.append('TONO POR MODELO DEL CLIENTE: no analices el tono; envía siempre '
                      '"tono":"Neutro" y dedica el trabajo al SUBTEMA.')
        criterio_texto = ""
    if aliases:
        partes.append("ALIAS: %s" % ", ".join(aliases))
    if voceros:
        partes.append("VOCEROS: %s" % ", ".join(voceros))
    partes.append("INCLUIR TEMA" if incluir_tema else "SIN TEMA")
    if incluir_tema and taxonomia:
        partes.append("<lista_temas>\n%s\n</lista_temas>" % "\n".join("- %s" % t for t in taxonomia))
    if (criterio_texto or "").strip():
        partes.append("<criterio_tono>\n%s\n</criterio_tono>" % criterio_texto.strip())
    if (guia_subtema or "").strip():
        partes.append("<guia_subtema>\n%s\n</guia_subtema>" % guia_subtema.strip())
    partes.append("Clasifica todas las noticias y guárdalas con guardar_clasificacion.")
    return "\n".join(partes)


# ─── el agente ────────────────────────────────────────────────────────────

def _sdk():
    try:
        import claude_agent_sdk as sdk
    except ImportError as e:  # pragma: no cover
        raise ErrorMotorHaiku("Falta el paquete claude-agent-sdk (pip install -r requirements.txt).") from e
    return sdk


def _servidor(sdk):
    leer = sdk.tool(
        "leer_noticias",
        "Devuelve PÁGINAS de %d noticias con id, 'titulo' (etiqueta corta) y 'contexto' (oraciones "
        "donde aparece el cliente). 'desde' = 0 la primera vez y luego 'siguiente' hasta null. Las "
        "filas con 'igual_a' repiten el título de otra fila y heredan su clasificación." % POR_PAGINA,
        {"desde": int})(_leer_noticias)
    guardar = sdk.tool(
        "guardar_clasificacion",
        "Guarda subtema, tono (Positivo|Neutro|Negativo) y tema (si se pidió). 'filas_json': lista "
        'JSON [{"id":"1","subtema":"...","tono":"Neutro","tema":"..."}]. Se puede llamar por tandas. '
        "Devuelve cuántas filas faltan.",
        {"filas_json": str})(_guardar_clasificacion)
    candidatos = sdk.tool(
        "candidatos_consistencia",
        "Revisión de consistencia (sin IA), después de guardar todo: grupos de filas que PODRÍAN ser "
        "el mismo hecho con distinto tono/subtema y pares con el mismo subtema y contextos muy "
        "distintos. 'desde': 0 y luego 'siguiente' (%d grupos por llamada)." % GRUPOS_POR_PAGINA,
        {"desde": int})(_candidatos_consistencia)
    return sdk.create_sdk_mcp_server(name="medios", version="1.0.0",
                                     tools=[leer, guardar, candidatos])


def construir_opciones(sdk, cwd: str, max_turns: int, max_budget_usd: float, modelo: str):
    return sdk.ClaudeAgentOptions(
        model=modelo,
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["mcp__medios__leer_noticias", "mcp__medios__guardar_clasificacion",
                       "mcp__medios__candidatos_consistencia", "Agent"],
        mcp_servers={"medios": _servidor(sdk)},
        setting_sources=[],              # sin CLAUDE.md, ~/.claude ni skills del proyecto
        agents={
            "revisor-subtemas": sdk.AgentDefinition(
                description=("Revisa la consistencia de tono y subtema: decide si cada grupo candidato "
                             "es el MISMO hecho y devuelve correcciones en JSON. Úsalo una vez al "
                             "final, después de guardar todas las páginas; pásale el rango de grupos."),
                prompt=PROMPT_REVISOR + "\n" + REGLAS_FORMA_REVISOR,
                tools=["mcp__medios__candidatos_consistencia"],
                model="haiku",
                maxTurns=6,
            ),
        },
        permission_mode="default",
        disallowed_tools=["Bash", "Edit", "Read", "Grep", "Glob", "Write", "WebSearch", "WebFetch"],
        cwd=cwd,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        stderr=lambda linea: None,
    )


def _preparar_credencial() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ErrorMotorHaiku("Falta ANTHROPIC_API_KEY (configúrala en los Secrets de Streamlit).")
    for otra in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_PROFILE"):
        os.environ.pop(otra, None)


async def _correr(sdk, prompt: str, opciones, query_fn) -> Dict[str, Any]:
    final: Dict[str, Any] = {"costo_usd": 0.0, "turnos": 0, "subtype": None, "error": None}
    try:
        async for m in query_fn(prompt=prompt, options=opciones):
            if isinstance(m, sdk.ResultMessage):
                final.update(costo_usd=float(m.total_cost_usd or 0.0), turnos=int(m.num_turns or 0),
                             subtype=m.subtype, is_error=bool(m.is_error))
    except Exception as e:  # noqa: BLE001 - ClaudeSDKError, timeout del CLI, etc.
        final["error"] = "%s: %s" % (type(e).__name__, e)
    return final


def _ejecutar(coro):
    """asyncio.run en el hilo del callback (Streamlit no tiene loop activo); si ya hay
    un loop corriendo (p. ej. un notebook), se corre en un hilo aparte."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    caja: Dict[str, Any] = {}

    def hilo():
        try:
            caja["r"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            caja["e"] = e
    t = threading.Thread(target=hilo)
    t.start()
    t.join()
    if "e" in caja:
        raise caja["e"]
    return caja["r"]


PROMPT_REFORMULAR = (
    "Eres editor de subtemas de un monitoreo de medios en español de Colombia. Recibes subtemas "
    "MAL formados con su título, contexto y problemas. Devuelve para cada id un subtema NUEVO.\n"
    + REGLAS_FORMA_REVISOR +
    "Responde SOLO con JSON compacto, sin texto alrededor: "
    '{"resultados":[{"id":"...","subtema":"..."}]}'
)

# Costo acumulado de la reformulación (lo suma el analizador al resumen).
REFORMULACION: Dict[str, Any] = {}


def reformular_subtemas(fallos: Sequence[dict], modelo: str = MODELO_HAIKU,
                        query_fn=None, max_budget_usd: float = 0.0) -> Dict[str, str]:
    """Equivalente Haiku de la reformulación LLM de `reparar_copias_finales`.

    `fallos`: [{id, titulo, texto, sub_tema, problemas}]. Una sola llamada
    `query()` sin herramientas (max_turns=1). Devuelve {id: subtema} solo con
    los que pasan el validador determinista; los demás quedan al respaldo
    determinista de Grill. Nunca lanza: ante cualquier error devuelve {}.
    """
    REFORMULACION.clear()
    REFORMULACION.update(costo_usd=0.0, pedidos=len(fallos or []), aceptados=0, error=None)
    if not fallos:
        return {}
    try:
        sdk = _sdk()
        if query_fn is None:
            _preparar_credencial()
            query_fn = sdk.query
    except Exception as e:  # noqa: BLE001
        REFORMULACION["error"] = str(e)
        return {}
    detalle = [{"id": str(f.get("id")), "titulo": str(f.get("titulo") or "")[:200],
                "contexto": str(f.get("texto") or "")[:700], "subtema_actual": f.get("sub_tema"),
                "problemas": list(f.get("problemas") or [])} for f in fallos[:60]]
    prompt = json.dumps({"subtemas": detalle}, ensure_ascii=False)
    salida: Dict[str, Any] = {"texto": ""}

    async def _uno():
        with tempfile.TemporaryDirectory(prefix="motor_haiku_ref_") as cwd:
            op = sdk.ClaudeAgentOptions(
                model=modelo, system_prompt=PROMPT_REFORMULAR, tools=[], allowed_tools=[],
                setting_sources=[], permission_mode="default", cwd=cwd, max_turns=1,
                max_budget_usd=max_budget_usd or round(min(1.0, 0.03 + 0.003 * len(detalle)), 2),
                disallowed_tools=["Bash", "Edit", "Read", "Grep", "Glob", "Write", "WebSearch",
                                  "WebFetch", "Agent"],
                stderr=lambda linea: None)
            async for m in query_fn(prompt=prompt, options=op):
                if isinstance(m, sdk.ResultMessage):
                    REFORMULACION["costo_usd"] = float(m.total_cost_usd or 0.0)
                    salida["texto"] = m.result or ""
    try:
        _ejecutar(_uno())
        txt = salida["texto"]
        ini, fin = txt.find("{"), txt.rfind("}")
        data = json.loads(txt[ini:fin + 1]) if ini >= 0 else {}
    except Exception as e:  # noqa: BLE001
        REFORMULACION["error"] = "%s: %s" % (type(e).__name__, e)
        return {}
    por_id = {str(f.get("id")): f for f in fallos}
    out: Dict[str, str] = {}
    for r in (data.get("resultados") or []):
        fid, sub = str(r.get("id")), str(r.get("subtema") or "").strip().rstrip(".")
        f = por_id.get(fid)
        if f and sub and not problemas_subtema(sub, f.get("titulo") or "", f.get("texto") or ""):
            out[fid] = sub
    REFORMULACION["aceptados"] = len(out)
    return out


def rotulos_permitidos_de(guia_subtema: str = "", extra: Sequence[str] = ()) -> set:
    """Rótulos exactos que el perfil impone (subtemas fijos y los que la guía
    pide 'EXACTAMENTE'): el validador de forma no los rechaza."""
    out = {_simple(x) for x in (extra or []) if str(x or "").strip()}
    for m in re.finditer(r"EXACTAMENTE\s+['\"«“]([^'\"»”]{3,80})['\"»”]", guia_subtema or ""):
        out.add(_simple(m.group(1)))
    return out


def clasificar_con_haiku(items: Sequence[dict], brand: str, aliases: Sequence[str] = (),
                         voceros: Sequence[str] = (), criterio_texto: str = "",
                         incluir_tema: bool = False, taxonomia: Optional[Sequence[str]] = None,
                         progreso_cb: Optional[Callable[[int, str], None]] = None,
                         guia_subtema: str = "", modelo: str = MODELO_HAIKU,
                         max_turns: int = 0, max_budget_usd: float = 0.0,
                         query_fn=None, tono_pkl: bool = False,
                         rotulos_permitidos: Sequence[str] = ()) -> Dict[str, Any]:
    """Clasifica `items` ({id, titulo, contexto, duplicado_de?}) con el agente Haiku.

    Devuelve {"etiquetas": {id: {"tono","subtema"[, "tema"]}}, "costo_usd", "turnos",
    "filas", "faltan", "cambios_revision", "grupos_revision", "subtype", "error"}.
    Las filas que el agente no alcanzó a clasificar quedan fuera de "etiquetas"
    (el analizador les pone su respaldo determinista). `query_fn` permite un
    `query` falso en las pruebas.
    """
    sdk = _sdk()
    if query_fn is None:
        _preparar_credencial()
        query_fn = sdk.query
    n = len(items)
    paginas = -(-n // POR_PAGINA) or 1
    max_turns = max_turns or (2 * paginas + 14)
    max_budget_usd = max_budget_usd or round(min(5.0, 0.10 + 0.004 * n), 2)
    with _CANDADO:
        preparar_estado(items, incluir_tema, taxonomia, progreso_cb)
        _ESTADO["permitidos"] = rotulos_permitidos_de(guia_subtema, rotulos_permitidos)
        if not items:
            return {"etiquetas": {}, "costo_usd": 0.0, "turnos": 0, "filas": 0, "faltan": 0,
                    "cambios_revision": 0, "grupos_revision": 0, "subtype": None, "error": None}
        prompt = armar_prompt(brand, list(aliases or []), list(voceros or []), criterio_texto,
                              incluir_tema, _ESTADO["taxonomia"], guia_subtema,
                              tono_pkl=tono_pkl)
        with tempfile.TemporaryDirectory(prefix="motor_haiku_") as cwd:
            opciones = construir_opciones(sdk, cwd, max_turns, max_budget_usd, modelo)
            _avisar(75, "Clasificando con Claude Haiku (agente + revisor)…")
            final = _ejecutar(_correr(sdk, prompt, opciones, query_fn))
        _propagar()
        etiquetas = {fid: dict(r) for fid, r in _ESTADO["res"].items() if r.get("tono")}
        final.update(rechazos=list(_ESTADO.get("log_rechazos") or []),
                     marcados={k: list(v) for k, v in (_ESTADO.get("marcados") or {}).items()})
        final.update(etiquetas=etiquetas, filas=len(etiquetas), faltan=len(_faltantes()),
                     cambios_revision=PROGRESO.get("cambios_revision", 0),
                     grupos_revision=PROGRESO.get("grupos", 0))
        _avisar(90, "Haiku listo: %d filas · $%.4f · %d turnos"
                % (final["filas"], final["costo_usd"], final["turnos"]))
        return final
