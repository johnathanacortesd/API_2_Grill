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
        reg = {"id": fid, "titulo": d["titulo"][:TOPE_TITULO]}
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
TONO y SUBTEMA se juzgan desde el "contexto" de cada noticia, no desde el título: el título es solo una etiqueta para agrupar.

SUBTEMA: frase nominal específica de 3 a 6 palabras que defina el hecho y lo distinga de otros. Nunca copies el titular ni su recorte, nunca el nombre del medio, sin comas ni punto final. Evita subtemas genéricos como "Gestión de la alcaldía".

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

PROMPT_REVISOR = (
    "Eres revisor de consistencia de una clasificación de noticias en español. Recibes un rango de "
    "grupos (desde y cuántos) y quizá un CRITERIO DEL TONO. Lee los grupos con "
    "mcp__medios__candidatos_consistencia (desde), solo los de tu rango.\n"
    "Para cada grupo decide si sus filas cuentan el MISMO hecho: el mismo evento, la misma entidad y "
    "el mismo momento. Mismo tema general o mismo medio NO es el mismo hecho.\n"
    "- Si es el mismo hecho: todas las filas llevan un solo subtema (reutiliza el más específico) y "
    "un solo tono (el que mejor sostiene el contexto).\n"
    "- Si no lo es: déjalas separadas y haz cada subtema lo bastante específico (3 a 6 palabras). "
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
                prompt=PROMPT_REVISOR,
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


def clasificar_con_haiku(items: Sequence[dict], brand: str, aliases: Sequence[str] = (),
                         voceros: Sequence[str] = (), criterio_texto: str = "",
                         incluir_tema: bool = False, taxonomia: Optional[Sequence[str]] = None,
                         progreso_cb: Optional[Callable[[int, str], None]] = None,
                         guia_subtema: str = "", modelo: str = MODELO_HAIKU,
                         max_turns: int = 0, max_budget_usd: float = 0.0,
                         query_fn=None, tono_pkl: bool = False) -> Dict[str, Any]:
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
        final.update(etiquetas=etiquetas, filas=len(etiquetas), faltan=len(_faltantes()),
                     cambios_revision=PROGRESO.get("cambios_revision", 0),
                     grupos_revision=PROGRESO.get("grupos", 0))
        _avisar(90, "Haiku listo: %d filas · $%.4f · %d turnos"
                % (final["filas"], final["costo_usd"], final["turnos"]))
        return final
