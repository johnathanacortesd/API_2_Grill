"""Motor alterno de Tono/Subtema/Tema con un agente Claude Haiku (Claude Agent SDK).

Es una OPCIÓN junto al motor OpenAI del analizador: el pipeline arma los grupos
de noticias y su "Contexto analizado" igual que siempre, y en lugar de
`etiquetar_grupos` (OpenAI) llama a `clasificar_con_haiku`. El agente:

- lee las noticias por páginas con una herramienta MCP en proceso (solo id,
  titulo y contexto; los títulos repetidos llegan con `igual_a`),
- guarda rol/tono/subtema (y tema, si se pide) con otra herramienta que
  valida la forma del subtema, que el tono corresponda al rol del cliente en
  la nota (`ROLES`) y hereda los valores en los duplicados,
- revisa la consistencia con el subagente `revisor-subtemas` sobre los grupos
  candidatos que arma `candidatos_consistencia` (rapidfuzz, sin IA). El
  subagente recibe en su propio prompt el cliente, alias, voceros y criterio.

Al terminar el agente, `auditar_tono` (agente aparte, lanzado por código)
verifica las filas con tono de riesgo: Negativos, tonos sin mención literal
del cliente, Positivos sin el cliente en el título y subtemas con tonos
distintos.

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

# Papel del cliente (marca, alias o voceros) en la nota. El tono NO es el
# ánimo general de la noticia: es cómo la noticia evalúa al cliente, y sale
# del rol. guardar_clasificacion rechaza la fila si rol y tono no coinciden.
ROLES = {
    "gestion": "Positivo",       # gestión, acción, obra o pronunciamiento propio
    "logro": "Positivo",         # reconocimiento o impacto favorable que se le atribuye
    "critica": "Negativo",       # crítica, denuncia, sanción o cuestionamiento dirigido
    "afectacion": "Negativo",    # falla, crisis o impacto adverso que se le atribuye
    "equilibrada": "Neutro",     # señalamiento con respuesta o descargo del cliente
    "referencial": "Neutro",     # mención de paso: dato, fuente, antecedente, contexto
}
LOTE_AUDITOR = 20         # filas por llamada del auditor de tono
MAX_AUDITOR = 160         # tope de filas auditadas por corrida
TOPE_CTX_AUDITOR = 700

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


def terminos_cliente(brand: str = "", aliases: Sequence[str] = (),
                     voceros: Sequence[str] = ()) -> List[str]:
    """Marca, alias y voceros normalizados (sin tildes ni mayúsculas)."""
    out: List[str] = []
    for t in [brand, *(aliases or []), *(voceros or [])]:
        n = _simple(t)
        if n and n not in out:
            out.append(n)
    return out


def menciona_cliente(texto: str, terminos: Optional[Sequence[str]] = None) -> bool:
    """True si el texto nombra literalmente a la marca, un alias o un vocero
    (palabra completa). Sin términos configurados no se puede saber: True."""
    terminos = _ESTADO.get("terminos") if terminos is None else terminos
    if not terminos:
        return True
    t = " %s " % _simple(texto)
    return any(" %s " % x in t for x in terminos)


def preparar_estado(items: Sequence[dict], incluir_tema: bool,
                    taxonomia: Optional[Sequence[str]] = None,
                    progreso_cb: Optional[Callable[[int, str], None]] = None,
                    terminos: Sequence[str] = (), tono_pkl: bool = False) -> None:
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
                   tax_norm={_simple(t): t for t in tax}, progreso_cb=progreso_cb,
                   terminos=list(terminos or []), tono_pkl=bool(tono_pkl))
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
            if not menciona_cliente(d["titulo"] + " " + d["contexto"]):
                reg["sin_mencion_literal"] = True
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
        for f in nuevas.values():
            if f.get("rol") not in (None, ""):
                f["rol"] = _simple(f["rol"])
        sin_rol = [i for i, f in nuevas.items() if f.get("rol") and f["rol"] not in ROLES]
        if sin_rol:
            raise ValueError("rol inválido en ids %s: usa %s" % (sin_rol[:10], ", ".join(ROLES)))
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
        d = _ESTADO["datos"].get(_raiz(fid)) or {}
        prob = []
        if sub and _simple(sub) not in _ESTADO.get("permitidos", set()):
            # (los rótulos exactos que fija el perfil del cliente no se validan)
            prob = problemas_subtema(sub, d.get("titulo", ""), d.get("contexto", ""))
        # Coherencia rol ↔ tono: el tono sale del papel del cliente en la nota.
        rol = f.get("rol")
        if rol and not _ESTADO.get("tono_pkl") and ROLES[rol] != f["tono"]:
            prob.append("rol '%s' corresponde a tono %s, no a %s (decide primero el rol del "
                        "cliente en la nota y pon el tono de ese rol)" % (rol, ROLES[rol], f["tono"]))
        if not prob:
            marcados.pop(_raiz(fid), None)
            continue
        if rechazos.get(fid, 0) >= MAX_RECHAZOS:
            marcados[_raiz(fid)] = prob
            if any(x.startswith("rol ") for x in prob):
                _ESTADO.setdefault("tono_dudoso", set()).add(_raiz(fid))
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
        # El rol se conserva si sigue siendo coherente con el tono guardado
        # (una corrección del revisor puede traer solo id, subtema y tono).
        rol = f.get("rol") or antes.get("rol")
        if rol and (_ESTADO.get("tono_pkl") or ROLES.get(rol) == ahora["tono"]):
            ahora["rol"] = rol
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
        return _texto("FILAS CON SUBTEMAS RECHAZADOS o rol/tono incoherente (no se guardaron): %s. "
                      "Rehaz SOLO esos ids: subtema = frase nominal de 3 a 7 palabras que no empiece "
                      "con verbo y no copie el título ni el contexto; tono = el que corresponde al "
                      "rol. Vuelve a llamar guardar_clasificacion. Faltan por clasificar: %d."
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

RUBRICA_TONO = """El TONO mide cómo la noticia EVALÚA o deja al CLIENTE (la marca, sus alias o sus voceros). No es el ánimo general de la noticia ni la gravedad del asunto. Se decide en dos pasos.
Paso 1, el "rol": qué papel tiene el cliente en la nota.
- gestion: el cliente o su vocero HACE algo que la nota presenta a su favor: gestión, obra, programa, inversión, alianza, estudio, informe, pronunciamiento, o actúa frente a un problema.
- logro: la nota le atribuye un resultado o impacto favorable: reconocimiento, premio, cifra buena, respaldo o elogio de terceros.
- critica: hay crítica, denuncia, queja, sanción, investigación o cuestionamiento DIRIGIDO al cliente, a su administración, a sus funcionarios o a su vocero.
- afectacion: la nota le atribuye un hecho adverso que daña su imagen u operación: falla, crisis, escándalo, incumplimiento, pérdida.
- equilibrada: hay un señalamiento contra el cliente Y la nota trae su respuesta, descargo o versión.
- referencial: el cliente solo aparece mencionado: dato, antecedente, fuente citada entre varias, lugar, lista o contexto. La nota no lo evalúa ni le atribuye una acción.
Paso 2, el "tono" sale del rol: gestion y logro = Positivo; critica y afectacion = Negativo; equilibrada y referencial = Neutro.
Comprobaciones:
- ¿De quién habla la nota? Un asunto grave (muertes, violencia, crisis, quejas contra otros) no es Negativo si el cliente no es el señalado ni el responsable. Una buena noticia de un tercero no es Positivo si el cliente solo está mencionado.
- Lo que hace o dice un VOCERO en su cargo cuenta como del cliente. Si el cliente o su vocero denuncia, advierte o critica a OTRO, es su labor (gestion), no una crítica contra él.
- Una entidad con nombre parecido (otro país, otra ciudad, otra institución) no es el cliente: referencial.
- Ante duda entre Positivo o Negativo y Neutro, elige referencial (Neutro)."""

RUBRICA_SUBTEMA = """Cómo se redacta un SUBTEMA preciso:
1. Identifica el HECHO principal de la nota: qué pasó (no el papel del cliente, no un párrafo lateral). Si el cliente solo opina sobre un asunto, el subtema es ese asunto.
2. Redáctalo como: sustantivo del hecho (Entrega, Denuncia, Convenio, Informe, Captura, Jornada…) + su objeto + el dato que lo distingue (actor, lugar, programa o población).
3. Prueba de precisión: si el rótulo le sirve igual a OTRA noticia distinta del lote, le falta el dato distintivo; si solo le sirve a UN medio (trae una cita, una cifra menor o un detalle anecdótico), le sobra detalle. Dos notas del mismo hecho deben poder llevarlo palabra por palabra."""

SYSTEM_PROMPT = """<rol>
Eres un analista de monitoreo de medios que clasifica noticias en español de Colombia. Tu trabajo lo usa un equipo que entrega informes a instituciones, así que la consistencia entre filas importa tanto como acertar cada una.
</rol>

<objetivo>
Recibes el CLIENTE, sus ALIAS y VOCEROS. A cada noticia le asignas ROL, TONO y SUBTEMA (y TEMA cuando el pedido lo indique) y los guardas con guardar_clasificacion. Las noticias ya vienen con su "contexto" (título, resumen y las oraciones literales donde aparece el cliente). No supongas un cliente que el pedido no nombre.
</objetivo>

<cliente_alias_voceros>
- Una mención de cualquier ALIAS cuenta como mención del cliente.
- Una mención de un VOCERO, o lo que él dice o se dice de él en su rol, cuenta como mención del cliente y pesa en el TONO.
- Si leer_noticias marca "sin_mencion_literal": el texto no nombra al cliente, a sus alias ni a sus voceros. Comprueba si de verdad se habla de él (por su cargo o por "la entidad"); si no, el rol es referencial y el tono Neutro.
</cliente_alias_voceros>

<como_trabajas>
Analiza la columna Contexto analizado completa (incluye título, resumen y el extracto de la marca/alias/vocero); el subtema y el tono salen de ese contenido, no de una frase suelta del titular. En leer_noticias ese contenido llega en "contexto"; "titulo_solo_para_agrupar" es solo una etiqueta corta para reconocer notas del mismo hecho: no lo uses para redactar el subtema ni copies fragmentos suyos. MAL "Presunto hacker tenía información inteligencia" (recorte del titular con verbo conjugado) -> BIEN "Captura de hacker con información de inteligencia".

SUBTEMA: clasificación precisa y específica del hecho que cuenta el "contexto" de la noticia. Reglas obligatorias (guardar_clasificacion RECHAZA el subtema que no las cumpla):
- Frase NOMINAL de 3 a 7 palabras que define el hecho (quién, qué) y lo distingue de otros.
- NUNCA empieza con un verbo en ninguna forma: ni infinitivo (Realizar, Volver), ni conjugado (Anuncia, Querían, Denunció), ni gerundio (Realizando), ni participio (Anunciado). Empieza con un sustantivo: Anuncio, Riesgo, Entrega, Denuncia, Alerta.
- NUNCA es un extracto, recorte o casi-copia del título ni del contexto: sintetiza el hecho con tus palabras.
- Específico, no genérico: nada de "Gestión de la alcaldía" ni "Situación de orden público".
- Nunca el nombre del medio, sin comas ni punto final.
__RUBRICA_SUBTEMA__
Si el pedido trae un bloque <guia_subtema>, esa guía del cliente MANDA sobre estas reglas en los casos que nombre.
Reglas de Grill (literales):
__REGLAS_SUBTEMA__
Ejemplos:
- MAL "querían volver a la guerra" (verbo y extracto) -> BIEN "Riesgo de reactivación del conflicto armado"
- MAL "Anuncia nuevas medidas" (verbo) -> BIEN "Nuevas medidas de protección a líderes sociales"
- MAL "Realizando jornada" (gerundio y genérico) -> BIEN "Jornada de atención a víctimas en Tumaco"
- MAL "Denunció amenazas contra líderes en el Cauca" (verbo y extracto) -> BIEN "Amenazas a líderes sociales del Cauca"
- MAL "La Defensoría del Pueblo emitió una alerta temprana" (copia y verbo) -> BIEN "Alerta temprana por reclutamiento de menores"
- MAL "Jornada de salud" (le sirve a cualquier jornada) -> BIEN "Jornada de vacunación infantil en Soacha"
- BIEN "Bienestar de la población migrante", "Taller de liderazgo juvenil"

TEMA (solo si el pedido dice INCLUIR TEMA): categoría amplia de 1 a 4 palabras que agrupa varios subtemas (por ejemplo "Seguridad", "Movilidad", "Derechos humanos"). Pocos temas para todo el lote. Si el pedido trae <lista_temas>, el tema DEBE ser exactamente uno de esa lista. Si el pedido dice SIN TEMA, no envíes "tema".

TONO y ROL: Positivo, Neutro o Negativo, solo desde la perspectiva del cliente.
__RUBRICA_TONO__
Ejemplos (cliente: una alcaldía):
- "La Alcaldía entregó 300 viviendas" -> rol gestion, Positivo.
- "Concejales denuncian sobrecostos en contrato de la Alcaldía" -> rol critica, Negativo.
- "Denuncian sobrecostos; la Alcaldía respondió que el contrato cumple la ley" -> rol equilibrada, Neutro.
- "Homicidios aumentaron 12 %, según cifras de la Alcaldía" -> rol referencial, Neutro (asunto grave, pero la nota no evalúa a la Alcaldía).
- "Empresa privada ganó premio; al acto asistió un delegado de la Alcaldía" -> rol referencial, Neutro.
guardar_clasificacion RECHAZA la fila si el rol y el tono no coinciden.
Si el pedido trae un bloque <criterio_tono>, ese criterio del cliente MANDA en los casos que nombre: usa el tono que indique y el rol que corresponde a ese tono.

Consistencia, en este orden:
1. Agrupa las noticias que cuentan el mismo hecho ("igual_a" ya junta títulos idénticos: esas filas no se clasifican, heredan).
2. A cada grupo dale exactamente el mismo SUBTEMA y TONO. Reutiliza un subtema existente cuando el hecho sea el mismo.
3. Cuando guardar_clasificacion diga "Faltan por clasificar: 0", llama candidatos_consistencia con desde=0. Si total es 0, terminas. Si no, lanza el ayudante revisor-subtemas con Agent pasándole solo el rango de grupos (desde y cuántos): él ya conoce al cliente, sus alias, voceros y el criterio del tono. Si hay más de 30 grupos, lanza dos en paralelo, cada uno con su mitad. Con sus correcciones llama UNA vez guardar_clasificacion solo con las filas que cambian. No repitas la revisión. (Después, un auditor de tono independiente revisa por su cuenta los tonos de riesgo: no es tarea tuya.)
</como_trabajas>

<herramientas>
- mcp__medios__leer_noticias(desde): páginas de noticias; empieza en desde=0, clasifica y guarda esa página, y pide la siguiente con "siguiente" hasta que sea null.
- mcp__medios__guardar_clasificacion(filas_json): lista JSON [{"id":"…","subtema":"…","rol":"gestion|logro|critica|afectacion|equilibrada|referencial","tono":"Positivo|Neutro|Negativo","tema":"…"}]. Puedes llamarla por tandas; las filas repetidas se corrigen. Dice cuántas filas faltan.
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
SYSTEM_PROMPT = (SYSTEM_PROMPT.replace("__REGLAS_SUBTEMA__", _REGLAS_GRILL)
                 .replace("__RUBRICA_SUBTEMA__", RUBRICA_SUBTEMA)
                 .replace("__RUBRICA_TONO__", RUBRICA_TONO))

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
    "Eres revisor de consistencia y precisión de los SUBTEMAS de una clasificación de noticias en "
    "español. Recibes un rango de grupos (desde y cuántos). Lee los grupos con "
    "mcp__medios__candidatos_consistencia (desde), solo los de tu rango.\n"
    "TAREA 1, mismo hecho. Para cada grupo decide si sus filas cuentan el MISMO hecho. Lo son solo "
    "si coinciden las cuatro cosas: el mismo evento, los mismos actores, el mismo lugar y el mismo "
    "momento (fecha, edición o cifra). Mismo tema general, misma entidad o mismo medio NO es el "
    "mismo hecho. Nunca fusiones solo porque los subtemas comparten palabras.\n"
    "TAREA 2, subtema preciso.\n"
    "- Si es el mismo hecho: todas las filas llevan UN solo subtema, el que mejor pasa la prueba de "
    "precisión (reutiliza uno existente antes de escribir otro), y un solo tono.\n"
    "- Si no lo es: déjalas separadas y dale a cada una el dato que la distingue (actor, lugar, "
    "programa). Los grupos 'posible_sobreagrupacion' casi siempre son hechos distintos con un "
    "subtema genérico.\n"
    + RUBRICA_SUBTEMA + "\n"
    "TAREA 3, tono del grupo. Solo cuando las filas del mismo hecho traen tonos distintos, elige "
    "el que sostiene el contexto con esta regla (un auditor de tono aparte revisa el resto, no "
    "cambies tonos de filas que no son del mismo hecho):\n" + RUBRICA_TONO + "\n"
    "Responde SOLO con JSON compacto, sin texto alrededor: "
    '{"correcciones":[{"id":"...","subtema":"...","tono":"..."}],'
    '"grupos":[{"grupo":0,"mismo_hecho":true,"razon":"una línea"}]}. '
    "En correcciones incluye solo las filas cuyo subtema o tono cambia."
)

PROMPT_AUDITOR_TONO = (
    "Eres el agente auditor-tono de un monitoreo de medios en español de Colombia. Tu única tarea "
    "es verificar el TONO de las filas que recibes, mirando SOLO cómo la nota evalúa al CLIENTE "
    "(marca, alias o voceros) que trae el pedido. No cambias subtemas.\n"
    + RUBRICA_TONO + "\n"
    "Cada fila trae su titulo, su contexto (las oraciones donde aparece el cliente), el tono y rol "
    "actuales y los \"motivos\" por los que se audita. Para cada fila:\n"
    "1. Ubica en el contexto la mención del cliente, alias o vocero y qué se dice de él.\n"
    "2. Decide el rol y, con él, el tono. Un Negativo exige que la crítica o el hecho adverso recaiga "
    "en el cliente; un Positivo exige que la acción o el resultado favorable sea del cliente.\n"
    "3. Las filas con el mismo \"subtema\" que cuentan el mismo hecho llevan el mismo tono.\n"
    "Si el pedido trae \"criterio_tono\", ese criterio del cliente MANDA en los casos que nombre: "
    "usa el tono que indique y el rol que corresponde a ese tono.\n"
    "Responde SOLO con JSON compacto, sin texto alrededor, con TODAS las filas recibidas: "
    '{"resultados":[{"id":"...","rol":"gestion|logro|critica|afectacion|equilibrada|referencial",'
    '"tono":"Positivo|Neutro|Negativo","razon":"máximo 12 palabras"}]}'
)


def limpiar_criterio(texto: str) -> str:
    """Criterio de tono listo para el prompt: los '\\n' literales del
    catálogo pasan a saltos de línea reales."""
    return str(texto or "").replace("\\n", "\n").strip()


def bloque_cliente(brand: str, aliases: Sequence[str] = (), voceros: Sequence[str] = (),
                   criterio_texto: str = "") -> str:
    """Contexto del cliente que se incrusta en el prompt de cada subagente:
    así no depende de que el agente principal se lo copie al lanzarlo."""
    partes = ["<cliente>", "CLIENTE: %s" % (brand or "")]
    if aliases:
        partes.append("ALIAS: %s" % ", ".join(aliases))
    if voceros:
        partes.append("VOCEROS: %s" % ", ".join(voceros))
    partes.append("</cliente>")
    crit = limpiar_criterio(criterio_texto)
    if crit:
        partes.append("<criterio_tono>\n%s\n</criterio_tono>\nEl criterio_tono del cliente MANDA "
                      "sobre la regla general del tono en los casos que nombre." % crit)
    return "\n".join(partes)


def armar_prompt(brand: str, aliases: Sequence[str], voceros: Sequence[str],
                 criterio_texto: str, incluir_tema: bool,
                 taxonomia: Optional[Sequence[str]] = None, guia_subtema: str = "",
                 tono_pkl: bool = False) -> str:
    partes = ["CLIENTE: %s" % brand]
    if tono_pkl:
        # Clausifier: con PKL de tono el modelo del cliente pone el TONO; el
        # agente no lo analiza (envía Neutro) y se concentra en el SUBTEMA.
        partes.append('TONO POR MODELO DEL CLIENTE: no analices el tono ni el rol; envía siempre '
                      '"tono":"Neutro", sin "rol", y dedica el trabajo al SUBTEMA.')
        criterio_texto = ""
    if aliases:
        partes.append("ALIAS: %s" % ", ".join(aliases))
    if voceros:
        partes.append("VOCEROS: %s" % ", ".join(voceros))
    partes.append("INCLUIR TEMA" if incluir_tema else "SIN TEMA")
    if incluir_tema and taxonomia:
        partes.append("<lista_temas>\n%s\n</lista_temas>" % "\n".join("- %s" % t for t in taxonomia))
    if (criterio_texto or "").strip():
        partes.append("<criterio_tono>\n%s\n</criterio_tono>" % limpiar_criterio(criterio_texto))
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
        "Devuelve PÁGINAS de %d noticias con id, 'titulo_solo_para_agrupar' (etiqueta corta) y "
        "'contexto' (título, resumen y oraciones donde aparece el cliente). 'desde' = 0 la primera "
        "vez y luego 'siguiente' hasta null. Las filas con 'igual_a' repiten el título de otra fila "
        "y heredan su clasificación. 'sin_mencion_literal' = el texto no nombra al cliente, sus "
        "alias ni sus voceros." % POR_PAGINA,
        {"desde": int})(_leer_noticias)
    guardar = sdk.tool(
        "guardar_clasificacion",
        "Guarda subtema, rol del cliente, tono (Positivo|Neutro|Negativo) y tema (si se pidió). "
        "'filas_json': lista JSON "
        '[{"id":"1","subtema":"...","rol":"referencial","tono":"Neutro","tema":"..."}]. '
        "Rechaza subtemas mal formados y filas cuyo rol no corresponde al tono. Se puede llamar por "
        "tandas. Devuelve cuántas filas faltan.",
        {"filas_json": str})(_guardar_clasificacion)
    candidatos = sdk.tool(
        "candidatos_consistencia",
        "Revisión de consistencia (sin IA), después de guardar todo: grupos de filas que PODRÍAN ser "
        "el mismo hecho con distinto tono/subtema y pares con el mismo subtema y contextos muy "
        "distintos. 'desde': 0 y luego 'siguiente' (%d grupos por llamada)." % GRUPOS_POR_PAGINA,
        {"desde": int})(_candidatos_consistencia)
    return sdk.create_sdk_mcp_server(name="medios", version="1.0.0",
                                     tools=[leer, guardar, candidatos])


def construir_opciones(sdk, cwd: str, max_turns: int, max_budget_usd: float, modelo: str,
                       contexto_cliente: str = ""):
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
                prompt=(PROMPT_REVISOR + "\n" + REGLAS_FORMA_REVISOR
                        + ("\n" + contexto_cliente if contexto_cliente else "")),
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


# ─── auditor de tono (agente aparte, orquestado por código) ───────────────
# Después del agente principal y su revisor de subtemas, las filas con tono
# de riesgo pasan por un agente que solo mira una cosa: cómo evalúa la nota
# al cliente. Lo lanza el código (no depende de que el agente principal se
# acuerde), por lotes en paralelo, sin herramientas y con la salida validada.

AUDITORIA: Dict[str, Any] = {}


def candidatos_tono() -> List[dict]:
    """Filas cuyo tono conviene verificar, con el motivo. Sin IA."""
    res, datos = _ESTADO["res"], _ESTADO["datos"]
    dudosos = _ESTADO.get("tono_dudoso") or set()
    tonos_sub: Dict[str, set] = {}
    for fid in _ESTADO["orden"]:
        r = res.get(fid) or {}
        if fid not in _ESTADO["hereda"] and r.get("tono") and r.get("subtema"):
            tonos_sub.setdefault(_simple(r["subtema"]), set()).add(r["tono"])
    out = []
    for fid in _ESTADO["orden"]:
        r = res.get(fid) or {}
        if fid in _ESTADO["hereda"] or r.get("tono") not in TONOS:
            continue
        d = datos[fid]
        motivos, peso = [], 0
        if fid in dudosos:
            motivos.append("el rol y el tono guardados no coincidían")
            peso = 4
        if r["tono"] != "Neutro" and not menciona_cliente(d["titulo"] + " " + d["contexto"]):
            motivos.append("tono %s sin mención literal del cliente, alias ni voceros" % r["tono"])
            peso = max(peso, 4)
        if len(tonos_sub.get(_simple(r.get("subtema")), ())) > 1:
            motivos.append("su subtema tiene filas con tonos distintos")
            peso = max(peso, 3)
        if r["tono"] == "Negativo":
            motivos.append("Negativo: confirma que la crítica o el hecho adverso recae en el cliente")
            peso = max(peso, 2)
        elif r["tono"] == "Positivo" and not menciona_cliente(d["titulo"]):
            motivos.append("Positivo y el cliente no está en el título: confirma que la acción o "
                           "el resultado favorable es suyo y no una mención de paso")
            peso = max(peso, 1)
        if motivos:
            out.append({"id": fid, "titulo": d["titulo"][:TOPE_TITULO],
                        "contexto": d["contexto"][:TOPE_CTX_AUDITOR],
                        "subtema": r.get("subtema") or "", "tono_actual": r["tono"],
                        "rol_actual": r.get("rol") or "", "motivos": motivos, "_peso": peso})
    # Los de más riesgo primero si hay que recortar; dentro del lote, las
    # filas del mismo subtema quedan juntas para que se decidan igual.
    out.sort(key=lambda c: -c["_peso"])
    out = out[:MAX_AUDITOR]
    out.sort(key=lambda c: _simple(c["subtema"]))
    for c in out:
        del c["_peso"]
    return out


def auditar_tono(brand: str, aliases: Sequence[str] = (), voceros: Sequence[str] = (),
                 criterio_texto: str = "", modelo: str = MODELO_HAIKU, query_fn=None,
                 lote: int = LOTE_AUDITOR) -> Dict[str, Any]:
    """Audita el tono de `candidatos_tono()` y aplica los cambios en el estado.

    Una llamada `query()` por lote (max_turns=1, sin herramientas), lotes en
    paralelo. Solo se aplica un resultado con tono válido y rol coherente.
    Nunca lanza: ante un error deja los tonos como estaban.
    """
    AUDITORIA.clear()
    AUDITORIA.update(candidatos=0, lotes=0, costo_usd=0.0, cambios=[], confirmados=0, error=None)
    cands = candidatos_tono()
    AUDITORIA["candidatos"] = len(cands)
    if not cands:
        return dict(AUDITORIA)
    try:
        sdk = _sdk()
        if query_fn is None:
            _preparar_credencial()
            query_fn = sdk.query
    except Exception as e:  # noqa: BLE001
        AUDITORIA["error"] = str(e)
        return dict(AUDITORIA)
    _avisar(89, "Auditor de tono: %d filas de riesgo" % len(cands))
    PROGRESO.update(paso="Auditando tono", auditoria_tono=len(cands))
    lotes = [cands[i:i + lote] for i in range(0, len(cands), lote)]
    AUDITORIA["lotes"] = len(lotes)
    textos = [""] * len(lotes)
    costos = [0.0] * len(lotes)
    cliente = {"marca": brand, "alias": list(aliases or []), "voceros": list(voceros or [])}
    criterio = limpiar_criterio(criterio_texto)

    async def _uno(n: int, chunk):
        pedido: Dict[str, Any] = {"cliente": cliente}
        if criterio:
            pedido["criterio_tono"] = criterio
        pedido["filas"] = chunk
        with tempfile.TemporaryDirectory(prefix="motor_haiku_tono_") as cwd:
            op = sdk.ClaudeAgentOptions(
                model=modelo, system_prompt=PROMPT_AUDITOR_TONO, tools=[], allowed_tools=[],
                setting_sources=[], permission_mode="default", cwd=cwd, max_turns=1,
                max_budget_usd=round(min(1.0, 0.03 + 0.004 * len(chunk)), 2),
                disallowed_tools=["Bash", "Edit", "Read", "Grep", "Glob", "Write", "WebSearch",
                                  "WebFetch", "Agent"],
                stderr=lambda linea: None)
            async for m in query_fn(prompt=json.dumps(pedido, ensure_ascii=False), options=op):
                if isinstance(m, sdk.ResultMessage):
                    costos[n] = float(m.total_cost_usd or 0.0)
                    textos[n] = m.result or ""

    async def _todos():
        await asyncio.gather(*[_uno(n, c) for n, c in enumerate(lotes)], return_exceptions=True)

    try:
        _ejecutar(_todos())
    except Exception as e:  # noqa: BLE001
        AUDITORIA["error"] = "%s: %s" % (type(e).__name__, e)
    AUDITORIA["costo_usd"] = round(sum(costos), 6)
    pedidos = {c["id"] for c in cands}
    res = _ESTADO["res"]
    for txt in textos:
        ini, fin = txt.find("{"), txt.rfind("}")
        try:
            data = json.loads(txt[ini:fin + 1]) if ini >= 0 else {}
        except ValueError:
            continue
        for r in (data.get("resultados") or []) if isinstance(data, dict) else []:
            if not isinstance(r, dict):
                continue
            fid, tono, rol = str(r.get("id")), r.get("tono"), _simple(r.get("rol"))
            if fid not in pedidos or tono not in TONOS or ROLES.get(rol) != tono:
                continue
            pedidos.discard(fid)            # un veredicto por fila
            antes = res.get(fid) or {}
            if antes.get("tono") == tono:
                AUDITORIA["confirmados"] += 1
                antes["rol"] = rol
                continue
            AUDITORIA["cambios"].append({"id": fid, "de": antes.get("tono"), "a": tono, "rol": rol,
                                         "razon": str(r.get("razon") or "")[:160]})
            antes.update(tono=tono, rol=rol)
            res[fid] = antes
    _propagar()
    return dict(AUDITORIA)


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
                         rotulos_permitidos: Sequence[str] = (),
                         auditor_tono: bool = True) -> Dict[str, Any]:
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
        preparar_estado(items, incluir_tema, taxonomia, progreso_cb,
                        terminos=terminos_cliente(brand, aliases, voceros), tono_pkl=tono_pkl)
        _ESTADO["permitidos"] = rotulos_permitidos_de(guia_subtema, rotulos_permitidos)
        if not items:
            return {"etiquetas": {}, "costo_usd": 0.0, "turnos": 0, "filas": 0, "faltan": 0,
                    "cambios_revision": 0, "grupos_revision": 0, "subtype": None, "error": None}
        prompt = armar_prompt(brand, list(aliases or []), list(voceros or []), criterio_texto,
                              incluir_tema, _ESTADO["taxonomia"], guia_subtema,
                              tono_pkl=tono_pkl)
        with tempfile.TemporaryDirectory(prefix="motor_haiku_") as cwd:
            opciones = construir_opciones(
                sdk, cwd, max_turns, max_budget_usd, modelo,
                contexto_cliente=bloque_cliente(brand, list(aliases or []), list(voceros or []),
                                                "" if tono_pkl else criterio_texto))
            _avisar(75, "Clasificando con Claude Haiku (agente + revisor)…")
            final = _ejecutar(_correr(sdk, prompt, opciones, query_fn))
        _propagar()
        # Auditor de tono: con PKL de tono no corre (el tono lo pone el PKL).
        final["auditor_tono"] = None
        if auditor_tono and not tono_pkl and _ESTADO["res"]:
            aud = auditar_tono(brand, aliases, voceros, criterio_texto, modelo=modelo,
                               query_fn=query_fn)
            final["auditor_tono"] = aud
            final["costo_usd"] = round(float(final.get("costo_usd") or 0.0)
                                       + float(aud.get("costo_usd") or 0.0), 6)
        etiquetas = {fid: dict(r) for fid, r in _ESTADO["res"].items() if r.get("tono")}
        final["roles"] = {}
        for fid, r in _ESTADO["res"].items():
            if fid not in _ESTADO["hereda"] and r.get("tono"):
                k = r.get("rol") or "sin_rol"
                final["roles"][k] = final["roles"].get(k, 0) + 1
        final.update(rechazos=list(_ESTADO.get("log_rechazos") or []),
                     marcados={k: list(v) for k, v in (_ESTADO.get("marcados") or {}).items()})
        final.update(etiquetas=etiquetas, filas=len(etiquetas), faltan=len(_faltantes()),
                     cambios_revision=PROGRESO.get("cambios_revision", 0),
                     grupos_revision=PROGRESO.get("grupos", 0))
        _avisar(90, "Haiku listo: %d filas · $%.4f · %d turnos"
                % (final["filas"], final["costo_usd"], final["turnos"]))
        return final
