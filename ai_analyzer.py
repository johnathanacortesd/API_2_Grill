# ======================================
# Motor de Análisis con IA (ai_analyzer.py)
# ======================================
import os
import re
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional, Callable, Set
from collections import Counter
import pandas as pd
from openai import OpenAI
from rapidfuzz import fuzz
from unidecode import unidecode

logger = logging.getLogger("ai_analyzer")

FORBIDDEN_TRAILING_WORDS = {
    "de", "del", "la", "el", "los", "las", "en", "para", "por", "con", "a", "al",
    "y", "o", "u", "e", "un", "una", "unos", "unas", "su", "sus", "sobre", "tras",
    "hacia", "desde", "sin", "que", "se", "ante", "bajo", "durante", "mediante", "contra"
}

STOPWORDS_ES = {
    "de", "del", "la", "el", "los", "las", "en", "para", "por", "con", "a", "al",
    "y", "o", "u", "e", "un", "una", "unos", "unas", "sobre", "tras", "este", "esta",
    "estos", "estas", "fue", "fueron", "era", "eran", "como", "mas", "pero", "sus",
    "que", "se", "ha", "han", "hay", "les", "nos", "son"
}

def clean_text_strictly_no_links(text: str) -> str:
    """Elimina URLs (http, https, www), diccionarios y la palabra 'Link'."""
    if not text:
        return ""
    if isinstance(text, dict):
        val = text.get("value", "")
        text = str(val)

    s = str(text).strip()
    if s.lower() in ("nan", "none", "null", "link", "link nota", "ver nota"):
        return ""

    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"www\.\S+", "", s)
    s = re.sub(r"\b(?:http|https)://\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    if s.lower() in ("link", "link nota", ""):
        return ""
    return s

def normalize_text_for_matching(text: str) -> str:
    if not text:
        return ""
    t = unidecode(str(text).lower().strip())
    t = re.sub(r"^(?:imagenes|en imagenes|fotos|en fotos|video|en video|en vivo)\s*\|\s*", "", t)
    words = re.findall(r"\b[a-z0-9]+\b", t)
    
    stemmed = []
    for w in words:
        if w in STOPWORDS_ES or len(w) < 2:
            continue
        if w.endswith("ces") and len(w) > 4:
            w = w[:-3] + "z"
        elif w.endswith("es") and len(w) > 4:
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("is") and len(w) > 3:
            w = w[:-1]
        stemmed.append(w)
        
    return " ".join(stemmed)

def get_content_words_set(text_norm: str) -> Set[str]:
    return {w for w in text_norm.split() if len(w) > 2 and w not in STOPWORDS_ES}

def get_lead_content_words(text_norm: str, n_words: int = 3) -> Tuple[str, ...]:
    words = [w for w in text_norm.split() if len(w) > 2 and w not in STOPWORDS_ES]
    return tuple(words[:n_words])

def extract_event_anchor(title_raw: str) -> str:
    if not title_raw:
        return ""
    parts = re.split(r"\s*[:|-]\s*", title_raw, 1)
    if len(parts) > 1 and len(parts[0].strip()) >= 12:
        return normalize_text_for_matching(parts[0])
    return ""

def generate_brand_variants(brand: str, aliases: List[str]) -> List[str]:
    raw_inputs = [brand] + [a for a in aliases if a.strip()]
    variants_set = set()

    for item in raw_inputs:
        base = unidecode(item.lower().strip())
        if not base:
            continue
        variants_set.add(base)

        acronym_match = re.search(r"\(([a-z0-9]{2,6})\)", base)
        if acronym_match:
            acronym = acronym_match.group(1)
            variants_set.add(acronym)
            variants_set.add(r"\b" + r"\.?\s*".join(list(acronym)) + r"\.?\b")
            base = re.sub(r"\([a-z0-9]{2,6}\)", "", base).strip()
            variants_set.add(base)

        if len(base) <= 5 and base.isalpha():
            variants_set.add(r"\b" + r"\.?\s*".join(list(base)) + r"\.?\b")
            continue

        if "santa fe" in base:
            variants_set.add(base.replace("santa fe", "santafe"))
            variants_set.add("santa fe")
            variants_set.add("santafe")
            variants_set.add("clinica santa fe")
            variants_set.add("hospital santa fe")
            variants_set.add("fundacion santa fe")

        if "serena del mar" in base:
            variants_set.add("serena")
            variants_set.add("hospital serena")
            variants_set.add("hospital serena del mar")
            variants_set.add("clinica serena")
            variants_set.add("clinica serena del mar")

        for prefix in ["fundacion", "clinica", "hospital", "universidad", "instituto", "asociacion"]:
            if base.startswith(prefix + " "):
                core = base[len(prefix):].strip()
                if len(core) >= 4:
                    variants_set.add(core)
                    for alt_p in ["clinica", "hospital", "fundacion", "centro"]:
                        variants_set.add(f"{alt_p} {core}")

    sorted_variants = sorted(list(variants_set), key=lambda x: len(x), reverse=True)
    compiled_regexes = []
    for v in sorted_variants:
        if v.startswith(r"\b"):
            compiled_regexes.append(v)
        else:
            compiled_regexes.append(rf"\b{re.escape(v)}\b")
            
    return compiled_regexes

def extract_brand_context(resumen: str, titulo: str, brand_regexes: List[str]) -> str:
    """Extrae el titular, el lead (suceso principal) y la mención institucional."""
    t_clean = clean_text_strictly_no_links(titulo)
    r_clean = clean_text_strictly_no_links(resumen)
    
    lead_sentence = ""
    brand_sentences = []
    
    if r_clean:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?\n])\s+', r_clean) if s.strip()]
        if sentences:
            lead_candidate = clean_text_strictly_no_links(sentences[0])
            if len(lead_candidate) > 25:
                lead_sentence = lead_candidate

        for s in sentences:
            s_clean_sub = clean_text_strictly_no_links(s)
            if not s_clean_sub or len(s_clean_sub) < 15:
                continue
            s_norm = unidecode(s_clean_sub.lower())
            if any(re.search(rx, s_norm) for rx in brand_regexes):
                if s_clean_sub not in brand_sentences and s_clean_sub != lead_sentence:
                    brand_sentences.append(s_clean_sub)
            if len(brand_sentences) >= 2:
                break

    parts = []
    if t_clean:
        parts.append(f"Titular: {t_clean}")
    if lead_sentence:
        parts.append(f"Hecho principal: {lead_sentence}")
    if brand_sentences:
        parts.append(f"Mención cliente: {' '.join(brand_sentences)}")
    elif r_clean and not lead_sentence:
        parts.append(f"Resumen: {r_clean[:350]}")

    full_context = " | ".join(parts) if parts else (t_clean or r_clean[:400])
    return clean_text_strictly_no_links(full_context)[:900]

def check_exact_byline_rule(text: str, brand: str, aliases: List[str]) -> bool:
    if not text:
        return False
        
    t_norm = unidecode(str(text).lower())
    targets = [unidecode(brand.lower().strip())] + [unidecode(a.lower().strip()) for a in aliases if a.strip()]
    
    for tgt in targets:
        if not tgt:
            continue
        tgt_esc = re.escape(tgt)
        if re.search(rf"\beditora?\s+web\s+y\s+periodista\s+egresad[oa]\s+(?:de\s+(?:la\s+)?)?{tgt_esc}\b", t_norm):
            return True
        if re.search(rf"\bestudiante\s+en\s+formacion\s+(?:de\s+(?:la\s+)?)?{tgt_esc}\b", t_norm):
            return True
        if re.search(rf"\b(?:periodista|editor[a]?|redactor[a]?)\s+egresad[oa]\s+(?:de\s+(?:la\s+)?)?{tgt_esc}\b", t_norm):
            return True

    return False

def clean_subtema(text: str, brand: str, title_fallback: str) -> str:
    """Limpia el subtema permitiendo de 5 a 10 palabras con coherencia gramatical."""
    if not text:
        return _fallback_from_title(title_fallback)
        
    clean = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', str(text))
    words = [w for w in clean.split() if w]
    
    # Rango ampliado de 5 a 10 palabras (evita cortar frases legítimas)
    if len(words) > 10:
        words = words[:10]
        
    while words and words[-1].lower() in FORBIDDEN_TRAILING_WORDS:
        words.pop()
        
    res = " ".join(words).strip()
    res_lower = res.lower()
    
    forbidden_starts = [
        "mencion de", "mencion a", "mencion en", "mencion del", "presencia de",
        "declaraciones de", "noticia sobre", "alusion a", "referencia a", "hecho sobre"
    ]
    for fs in forbidden_starts:
        if res_lower.startswith(fs):
            res = res[len(fs):].strip()
            break
            
    brand_words = set(re.findall(r"\b[a-z0-9]+\b", unidecode(brand.lower())))
    res_words = set(re.findall(r"\b[a-z0-9]+\b", unidecode(res.lower())))
    
    # Requiere al menos 3 palabras para ser un subtema informativo y no una palabra huérfana
    if len(res.split()) < 3 or not res or res_words.issubset(brand_words):
        return _fallback_from_title(title_fallback)
        
    return res[:1].upper() + res[1:]

def clean_tema(text: str) -> str:
    if not text:
        return "Gestión Institucional"
    clean = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', str(text)).strip()
    words = clean.split()[:4]
    res = " ".join(words).title()
    if res.lower() in ["otros", "otro", "general", "varios", "miscelanea", "sin clasificar", ""]:
        return "Gestión Institucional"
    return res

def ensure_different_tema_subtema(tema: str, subtema: str, ctx: str) -> str:
    t_clean = tema.strip().title()
    s_clean = subtema.strip()
    
    if t_clean.lower() == s_clean.lower() or fuzz.ratio(t_clean.lower(), s_clean.lower()) >= 80:
        c_low = f"{s_clean} {ctx}".lower()
        if any(w in c_low for w in ["salud", "hospital", "clinica", "medico", "medicina", "paciente", "quirurg", "enfermedad", "achc"]):
            return "Sector Salud"
        if any(w in c_low for w in ["aduan", "dian", "fiscal", "tributar", "impuesto", "arancel"]):
            return "Gestión Tributaria"
        if any(w in c_low for w in ["universidad", "estudiante", "academ", "carrera", "educacion", "profesor", "beca", "uao", "feria"]):
            return "Educación Superior"
        if any(w in c_low for w in ["aniversario", "celebracion", "decadas", "anos", "reconocimiento", "homenaje"]):
            return "Hitos y Aniversarios"
        if any(w in c_low for w in ["rescate", "bombero", "emergencia", "siniestro", "accidente", "desastre"]):
            return "Gestión de Emergencias"
        if any(w in c_low for w in ["obra", "construccion", "via", "infraestructura", "puente", "sede"]):
            return "Infraestructura"
        if any(w in c_low for w in ["seguridad", "policia", "captura", "hurto", "delito", "fiscalia", "crimen"]):
            return "Seguridad Ciudadana"
        if any(w in c_low for w in ["convenio", "acuerdo", "alianza", "gremio", "liderazgo"]):
            return "Relaciones Gremiales"
        return "Gestión Institucional"
        
    return t_clean

def check_positive_institutional_override(ctx: str) -> bool:
    c_low = unidecode(ctx.lower())
    positive_actions = [
        "celebra y respalda", "respalda el nombramiento", "respaldan el nombramiento",
        "acompanamos desde", "acompanamiento desde", "asesoria gratuita", "apoyo gratuito",
        "pusieron en marcha", "pone en marcha", "felicita a", "felicitamos a",
        "rinde homenaje", "reconocimiento destaca el compromiso", "abren espacio"
    ]
    has_positive = any(p in c_low for p in positive_actions)
    has_negative_allegation = any(n in c_low for n in ["denuncia penal", "sancion fiscal", "investigacion por corrupcion", "plagio"])
    return has_positive and not has_negative_allegation

def _fallback_from_title(title: str) -> str:
    if not title:
        return "Hecho Informativo"
    t = re.sub(r"^(?:imagenes|video|en fotos)\s*\|\s*", "", title, flags=re.IGNORECASE).strip()
    words = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', t).split()
    clean_words = words[:9]
    while clean_words and clean_words[-1].lower() in FORBIDDEN_TRAILING_WORDS:
        clean_words.pop()
    return " ".join(clean_words).capitalize() if len(clean_words) >= 3 else "Hecho Informativo"

def _distinctive_subset(words: Set[str], doc_freq: Counter, total_docs: int) -> Set[str]:
    if total_docs <= 0 or not words:
        return set(words)
    cap = max(2, round(total_docs * 0.06))
    return {w for w in words if doc_freq.get(w, 0) <= cap}

def cluster_similar_rows(rows: List[dict], km: dict, brand_regexes: List[str]) -> Dict[int, int]:
    """Clustering estricto para evitar fusionar noticias diferentes."""
    n = len(rows)
    cluster_map = {}
    clusters_rep = {}
    current_cluster = 0

    active_indices = [i for i in range(n) if not rows[i].get("is_duplicate")]

    features: Dict[int, dict] = {}
    title_doc_freq = Counter()
    for i in active_indices:
        t_raw = str(rows[i].get(km.get("titulo", "Título"), ""))
        r_raw = str(rows[i].get("Resumen - Aclaracion") or rows[i].get("resumen corto") or "")
        t_norm = normalize_text_for_matching(t_raw)
        c_words = get_content_words_set(t_norm)
        lead_words = get_lead_content_words(t_norm, n_words=3)
        anchor = extract_event_anchor(t_raw)
        r_norm = normalize_text_for_matching(r_raw[:300])

        features[i] = {
            "title_norm": t_norm,
            "content_words": c_words,
            "lead_words": lead_words,
            "anchor": anchor,
            "body_norm": r_norm,
        }
        for w in c_words:
            title_doc_freq[w] += 1

    total_docs = len(active_indices)
    sorted_indices = sorted(active_indices, key=lambda idx: features[idx]["title_norm"])

    for i in sorted_indices:
        f = features[i]
        t_norm, c_words, lead_words, anchor, r_norm = (
            f["title_norm"], f["content_words"], f["lead_words"], f["anchor"], f["body_norm"]
        )
        distinctive_c_words = _distinctive_subset(c_words, title_doc_freq, total_docs)

        assigned = False
        for cid, rep in clusters_rep.items():
            rep_t = rep["title_norm"]
            rep_words = rep["content_words"]
            rep_lead = rep["lead_words"]
            rep_anchor = rep["anchor"]
            rep_r = rep["body_norm"]

            # Ancla idéntica de evento (ej: "EN VIVO: debate presidencial")
            if anchor and rep_anchor and anchor == rep_anchor and len(anchor) >= 15:
                cluster_map[i] = cid
                assigned = True
                break
                
            # Coincidencia exacta del inicio (lead words)
            if len(lead_words) >= 3 and len(rep_lead) >= 3 and lead_words == rep_lead:
                cluster_map[i] = cid
                assigned = True
                break

            # Titular casi idéntico o contenido con alta similitud
            if t_norm and rep_t:
                if t_norm == rep_t:
                    cluster_map[i] = cid
                    assigned = True
                    break
                min_len = min(len(t_norm), len(rep_t))
                if min_len >= 24 and t_norm[:24] == rep_t[:24]:
                    cluster_map[i] = cid
                    assigned = True
                    break
                # Se eliminó partial_ratio y se elevó token_set_ratio a 85%
                if fuzz.token_set_ratio(t_norm, rep_t) >= 85 and fuzz.token_sort_ratio(t_norm, rep_t) >= 78:
                    cluster_map[i] = cid
                    assigned = True
                    break

            # Solapamiento estricto de palabras distintivas (no comunes en el lote)
            rep_distinctive = _distinctive_subset(rep_words, title_doc_freq, total_docs)
            overlap = distinctive_c_words & rep_distinctive
            if len(overlap) >= 5 and fuzz.token_sort_ratio(t_norm, rep_t) >= 72:
                cluster_map[i] = cid
                assigned = True
                break
            
            # Mismo cuerpo textual republicado entre medios
            if r_norm and rep_r and len(r_norm) > 80 and len(rep_r) > 80:
                if fuzz.token_sort_ratio(r_norm[:200], rep_r[:200]) >= 88:
                    cluster_map[i] = cid
                    assigned = True
                    break

        if not assigned:
            cluster_map[i] = current_cluster
            clusters_rep[current_cluster] = dict(f)
            current_cluster += 1

    return cluster_map

class _DSU:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

def canonicalize_subtopics(
    cluster_results: Dict[int, Tuple[str, str, str]],
    cluster_contexts: Optional[Dict[int, str]] = None,
) -> Dict[int, Tuple[str, str, str]]:
    """Unifica subtemas únicamente si la redacción de la IA es casi idéntica."""
    cids = list(cluster_results.keys())
    dsu = _DSU(cids)
    norm_subs = {cid: normalize_text_for_matching(cluster_results[cid][2] or "") for cid in cids}

    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            cid1, cid2 = cids[i], cids[j]
            if dsu.find(cid1) == dsu.find(cid2):
                continue
            n1, n2 = norm_subs[cid1], norm_subs[cid2]
            # Solo unificar si la cadena del subtema es prácticamente idéntica (>= 85%)
            if n1 and n2 and (n1 == n2 or fuzz.token_sort_ratio(n1, n2) >= 85):
                dsu.union(cid1, cid2)

    groups: Dict[int, List[int]] = {}
    for cid in cids:
        groups.setdefault(dsu.find(cid), []).append(cid)

    final_results = {}
    for _, members in groups.items():
        sub_counts = Counter(cluster_results[m][2] for m in members if cluster_results[m][2])
        tema_counts = Counter(cluster_results[m][1] for m in members if cluster_results[m][1])
        if sub_counts:
            max_count = max(sub_counts.values())
            best_sub = min(
                (s for s, c in sub_counts.items() if c == max_count),
                key=lambda s: (-len(s.split()), s),
            )
        else:
            best_sub = ""
        best_tema = tema_counts.most_common(1)[0][0] if tema_counts else ""

        for m in members:
            tono, _, _ = cluster_results[m]
            final_results[m] = (tono, best_tema or cluster_results[m][1], best_sub or cluster_results[m][2])

    return final_results

def _labels_too_close(a: str, b: str) -> bool:
    if not a or not b:
        return False
    na = normalize_text_for_matching(a)
    nb = normalize_text_for_matching(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return fuzz.ratio(na, nb) >= 78 or fuzz.token_set_ratio(na, nb) >= 82

def ensure_subtema_distinct_from_tema(
    tema: str,
    subtema: str,
    brand: str,
    title: str,
) -> str:
    """Si el subtema coincide con el tema macro, recurre al titular y no corta párrafos."""
    cleaned = clean_subtema(subtema or "", brand, title)
    if cleaned and not _labels_too_close(tema, cleaned) and len(cleaned.split()) >= 3:
        return cleaned

    fallback_sub = _fallback_from_title(title)
    if fallback_sub and not _labels_too_close(tema, fallback_sub):
        return fallback_sub
    return f"{cleaned} en la región" if cleaned else "Hecho Informativo"

def _call_openai_cluster(
    client: OpenAI,
    model: str,
    brand: str,
    aliases: List[str],
    brand_regexes: List[str],
    ctx: str,
    title_ref: str,
    request_tone: bool = True,
    request_theme: bool = True,
    pkl_theme: Optional[str] = None,
) -> Tuple[str, str, str]:
    search_scope = f"{title_ref} {ctx}"
    if check_exact_byline_rule(search_scope, brand, aliases):
        return "Neutro", "Estudiantes", "Redacción de artículo"

    json_fields = []
    steps = []
    n = 1
    if request_tone:
        steps.append(
            f'{n}. "tono": Sentimiento reputacional hacia "{brand}": "Positivo", "Negativo" o "Neutro".\n'
            '   REGLA DE ORO: Si expresa o recibe RESPALDO, RECONOCIMIENTO, CELEBRACIÓN, SOLIDARIDAD o ALIANZA, el tono es estrictamente "Positivo".'
        )
        json_fields.append('"tono": "..."')
        n += 1

    if request_theme:
        steps.append(
            f'{n}. "tema": Macro-dominio temático (1 a 3 palabras. Ej: "Educación Superior", "Gestión Tributaria", "Sector Salud").'
        )
        json_fields.append('"tema": "..."')
        n += 1

    subtema_rule = (
        f'{n}. "subtema": HECHO ESPECÍFICO de la noticia (frase informativa de 5 a 9 palabras que responda '
        f'¿Qué ocurrió, qué se anunció o qué hizo la entidad?). Sin puntos finales ni comillas.\n'
        '   - EJEMPLO CORRECTO: "Firma de convenio para becas de pregrado"\n'
        '   - EJEMPLO CORRECTO: "Inauguración de nuevo laboratorio de robótica"\n'
        '   - PROHIBIDO: Usar palabras huérfanas o repetir el macro-tema (ej: "Educación" o "Salud").\n'
        '   - PROHIBIDO: Frases cortadas o incompletas (ej: "La universidad anunció en").'
    )
    steps.append(subtema_rule)
    json_fields.append('"subtema": "..."')

    differ_rule = 'REGLA OBLIGATORIA: "subtema" debe ser mucho más específico y diferente a "tema".'

    prompt = f"""Audita y clasifica esta noticia para el cliente: "{brand}" (Alias: {', '.join(aliases) if aliases else 'Ninguno'}).

Titular: "{title_ref}"
Contexto noticioso:
\"\"\"{ctx}\"\"\"

Instrucciones:
{chr(10).join(steps)}

{differ_rule}

Responde estrictamente en formato JSON válido:
{{{", ".join(json_fields)}}}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Eres un auditor senior de medios de comunicación. Sintetizas hechos con precisión periodística."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=200
        )
        data = json.loads(resp.choices[0].message.content)

        if request_tone:
            tono_raw = str(data.get("tono", "Neutro")).strip().capitalize()
            tono = tono_raw if tono_raw in ["Positivo", "Negativo", "Neutro"] else "Neutro"
            if check_positive_institutional_override(ctx):
                tono = "Positivo"
        else:
            tono = "Neutro"

        subtema = clean_subtema(data.get("subtema", ""), brand, title_ref)

        if request_theme:
            tema = clean_tema(data.get("tema", ""))
            tema = ensure_different_tema_subtema(tema, subtema, ctx)
        else:
            tema = (pkl_theme or "").strip() or "Gestión Institucional"
            subtema = ensure_subtema_distinct_from_tema(tema, subtema, brand, title_ref)

        return tono, tema, subtema
    except Exception as e:
        logger.error(f"Error en llamada OpenAI: {e}")
        sub_fb = _fallback_from_title(title_ref)
        if request_theme:
            tema_fb = ensure_different_tema_subtema("Gestión Institucional", sub_fb, ctx)
        else:
            tema_fb = (pkl_theme or "").strip() or "Gestión Institucional"
            sub_fb = ensure_subtema_distinct_from_tema(tema_fb, sub_fb, brand, title_ref)
        tono_fb = "Positivo" if check_positive_institutional_override(ctx) else "Neutro"
        return tono_fb, tema_fb, sub_fb

def enrich_rows_with_ai(
    rows: List[dict],
    km: dict,
    brand: str,
    aliases: List[str],
    api_key: str,
    model: str = "gpt-4.1-mini",
    progress_callback: Optional[Callable[[int, str], None]] = None,
    tone_model=None,
    theme_model=None,
) -> List[dict]:
    from pkl_classifier import classification_plan, format_theme_label, map_tone_label, _safe_predict

    client = OpenAI(api_key=api_key)
    plan = classification_plan(True, tone_model, theme_model)
    brand_regexes = generate_brand_variants(brand, aliases)
    
    if progress_callback:
        progress_callback(71, "Extrayendo titular, lead y mención de marca para análisis…")
    for row in rows:
        if row.get("is_duplicate"):
            row["Contexto analizado"] = "-"
        else:
            resumen_val = row.get("Resumen - Aclaracion") or row.get("resumen corto") or row.get("Resumen") or ""
            titulo_val = row.get(km.get("titulo", "Título")) or ""
            ctx = extract_brand_context(str(resumen_val), str(titulo_val), brand_regexes)
            row["Contexto analizado"] = ctx

    if progress_callback:
        progress_callback(74, "Agrupando noticias idénticas y evitando sobre-agrupación…")
    cluster_map = cluster_similar_rows(rows, km, brand_regexes)
    
    unique_clusters = sorted(set(cluster_map.values()))
    total_clusters = len(unique_clusters)
    
    cluster_to_sample_idx = {}
    for row_idx, cid in cluster_map.items():
        if cid not in cluster_to_sample_idx:
            cluster_to_sample_idx[cid] = row_idx
            
    cluster_results: Dict[int, Tuple[str, str, str]] = {}
    cluster_pkl: Dict[int, Tuple[Optional[str], Optional[str]]] = {}

    if progress_callback:
        progress_callback(77, f"Analizando {total_clusters} hechos únicos con {model}…")
        
    completed = 0
    with ThreadPoolExecutor(max_workers=14) as executor:
        future_to_cid = {}
        for cid, row_idx in cluster_to_sample_idx.items():
            ctx = rows[row_idx]["Contexto analizado"]
            t_ref = str(rows[row_idx].get(km.get("titulo", "Título"), ""))
            pkl_tone = None
            pkl_theme = None
            if tone_model is not None:
                pkl_tone = map_tone_label(_safe_predict(tone_model, [ctx or ""], "tono")[0])
            if theme_model is not None:
                pkl_theme = format_theme_label(_safe_predict(theme_model, [ctx or ""], "tema")[0])
            cluster_pkl[cid] = (pkl_tone, pkl_theme)
            fut = executor.submit(
                _call_openai_cluster,
                client,
                model,
                brand,
                aliases,
                brand_regexes,
                ctx,
                t_ref,
                request_tone=plan["use_llm_tone"],
                request_theme=plan["use_llm_theme"],
                pkl_theme=pkl_theme,
            )
            future_to_cid[fut] = cid
            
        for fut in as_completed(future_to_cid):
            cid = future_to_cid[fut]
            tono, tema, subtema = fut.result()
            pkl_tone, pkl_theme = cluster_pkl.get(cid, (None, None))
            if pkl_tone:
                tono = pkl_tone
            if pkl_theme:
                tema = pkl_theme
                sample_idx = cluster_to_sample_idx[cid]
                subtema = ensure_subtema_distinct_from_tema(
                    tema,
                    subtema,
                    brand,
                    str(rows[sample_idx].get(km.get("titulo", "Título"), "")),
                )
            cluster_results[cid] = (tono, tema, subtema)
            completed += 1
            if progress_callback and (completed % 10 == 0 or completed == total_clusters):
                pct = 77 + int((completed / total_clusters) * 16)
                progress_callback(pct, f"Analizando con IA… {completed}/{total_clusters} procesados")

    cluster_contexts = {
        cid: rows[row_idx].get("Contexto analizado", "")
        for cid, row_idx in cluster_to_sample_idx.items()
    }
    cluster_results = canonicalize_subtopics(cluster_results, cluster_contexts)

    for i, row in enumerate(rows):
        if row.get("is_duplicate"):
            row["Tono_IA"] = "Duplicada"
            row["Tema_IA"] = "-"
            row["Subtema_IA"] = "-"
            continue

        cid = cluster_map.get(i)
        if cid is not None and cid in cluster_results:
            tono, tema, subtema = cluster_results[cid]
        else:
            tono, tema, subtema = "Neutro", "Gestión Institucional", "Hecho Informativo"

        row_full_text = f"{row.get(km.get('titulo', 'Título'), '')} {row.get('Contexto analizado', '')} {row.get('Resumen - Aclaracion', '')}"
        if check_exact_byline_rule(row_full_text, brand, aliases):
            if tone_model is None:
                tono = "Neutro"
            if theme_model is None:
                tema = "Estudiantes"
            subtema = "Redacción de artículo"

        if theme_model is None:
            tema = ensure_different_tema_subtema(tema, subtema, row.get("Contexto analizado", ""))
        else:
            subtema = ensure_subtema_distinct_from_tema(
                tema, subtema, brand,
                str(row.get(km.get("titulo", "Título"), "")),
            )

        row["Tono_IA"] = tono
        row["Tema_IA"] = tema
        row["Subtema_IA"] = subtema
            
    return rows
