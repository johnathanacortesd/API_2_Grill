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
    "hacia", "desde", "sin", "que", "se"
}

STOPWORDS_ES = {
    "de", "del", "la", "el", "los", "las", "en", "para", "por", "con", "a", "al",
    "y", "o", "u", "e", "un", "una", "unos", "unas", "sobre", "tras", "este", "esta",
    "estos", "estas", "fue", "fueron", "era", "eran", "como", "mas", "pero", "sus",
    "que", "se", "ha", "han", "hay", "les", "nos", "son"
}

INSTITUTIONAL_PREFIXES = [
    "fundacion", "clinica", "hospital", "universidad", "instituto", "institucion",
    "colegio", "banco", "aerolinea", "empresa", "grupo", "corporacion", "alcaldia",
    "gobernacion", "ministerio", "centro", "complejo", "organizacion", "sociedad",
    "asociacion", "proyecto", "urbanizacion"
]

def clean_text_simple(text: str) -> str:
    """Descarta vacíos, enlaces, diccionarios de hipervínculo y la palabra 'Link'."""
    if not text:
        return ""
    if isinstance(text, dict):
        val = text.get("value", "")
        if str(val).strip().lower() in ("link", "http", ""):
            return ""
        return re.sub(r"\s+", " ", str(val)).strip()

    s = str(text).strip()
    if s.lower() in ("nan", "none", "null", "link", "link nota", "http", "https"):
        return ""
    if re.match(r"^https?://\S+$", s):
        return ""
    return re.sub(r"\s+", " ", s).strip()

def normalize_text_for_matching(text: str) -> str:
    """Normaliza texto para comparación eliminando plurales y prefijos multimedia."""
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
    if len(parts) > 1 and len(parts[0].strip()) >= 10:
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
    """Extrae las oraciones relevantes del Resumen y Título."""
    t_clean = clean_text_simple(titulo)
    r_clean = clean_text_simple(resumen)
    
    r_norm = unidecode(r_clean.lower())
    t_norm = unidecode(t_clean.lower())
    
    matched_sentences = []
    
    if r_clean:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?\n])\s+', r_clean) if s.strip()]
        for idx, s in enumerate(sentences):
            s_clean_sub = clean_text_simple(s)
            if not s_clean_sub:
                continue
            s_norm = unidecode(s_clean_sub.lower())
            if any(re.search(rx, s_norm) for rx in brand_regexes):
                block = s_clean_sub
                if len(s_clean_sub.split()) < 10 and idx + 1 < len(sentences):
                    next_s = clean_text_simple(sentences[idx + 1])
                    if next_s:
                        block = f"{s_clean_sub} {next_s}"
                if block not in matched_sentences:
                    matched_sentences.append(block)

        if not matched_sentences:
            for rx in brand_regexes:
                for m in re.finditer(rx, r_norm):
                    start = max(0, m.start() - 120)
                    end = min(len(r_clean), m.end() + 150)
                    snippet = clean_text_simple(r_clean[start:end])
                    if snippet and snippet not in matched_sentences:
                        matched_sentences.append(f"...{snippet}..." if start > 0 else snippet)
                    if len(matched_sentences) >= 2:
                        break
                if matched_sentences:
                    break

    title_matches = any(re.search(rx, t_norm) for rx in brand_regexes)

    if matched_sentences:
        resumen_context = " ".join(matched_sentences).strip()
        if title_matches and t_clean and t_clean.lower() not in resumen_context.lower():
            return f"{t_clean}. {resumen_context}"[:800]
        return resumen_context[:800]

    if title_matches:
        if r_clean:
            return f"{t_clean}. {r_clean[:380]}".strip()[:800]
        return t_clean

    # Si la marca no está explícita, se toma Título + fragmento de Resumen
    if t_clean and r_clean:
        return f"{t_clean}. {r_clean[:400]}".strip()[:800]
    return t_clean or r_clean[:500]

def is_pure_byline_mention(title: str, context_text: str, brand_regexes: List[str]) -> bool:
    """
    Determina si la noticia es ÚNICAMENTE un crédito de autor/redactor.
    NUNCA devuelve True si la marca aparece en el titular o si es el sujeto de la noticia.
    """
    t_clean = clean_text_simple(title)
    t_norm = unidecode(t_clean.lower())
    
    # 1. Si el titular menciona a la marca (ej: "UAO y DIAN abren..."), NO es un simple byline
    if any(re.search(rx, t_norm) for rx in brand_regexes):
        return False

    ctx_norm = unidecode(context_text.lower())
    
    # 2. Patrón de créditos de redacción
    role_pattern = (
        r"(?:editor[a]?\s*(?:web)?|periodista|comunicador[a]?|redactor[a]?|reportero[a]?|"
        r"practicante|egresad[oa]|graduad[oa]|estudiante(?:\s+en\s+formacion)?|"
        r"periodista(?:\s+en\s+formacion)?)"
    )
    
    has_byline_phrase = False
    byline_match_span = None
    for rx in brand_regexes:
        m1 = re.search(rf"{role_pattern}.{{0,60}}?{rx}", ctx_norm)
        if m1:
            has_byline_phrase = True
            byline_match_span = m1.span()
            break
        m2 = re.search(rf"(?:por|autor[a]?):\s*[\w\s]+,?.{{0,45}}?{rx}", ctx_norm)
        if m2:
            has_byline_phrase = True
            byline_match_span = m2.span()
            break

    if not has_byline_phrase or not byline_match_span:
        return False

    # 3. Comprobar si la marca se menciona en el resto del texto fuera de la firma del redactor
    text_without_byline = ctx_norm[:byline_match_span[0]] + " " + ctx_norm[byline_match_span[1]:]
    if any(re.search(rx, text_without_byline) for rx in brand_regexes):
        # La marca actúa en el cuerpo de la noticia (ej: directores de programa, convenios, etc.)
        return False

    return True

def clean_subtema(text: str, brand: str, title_fallback: str) -> str:
    if not text:
        return _fallback_from_title(title_fallback)
        
    clean = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', str(text))
    words = [w for w in clean.split() if w]
    
    if len(words) > 6:
        words = words[:6]
        
    while words and words[-1].lower() in FORBIDDEN_TRAILING_WORDS:
        words.pop()
        
    res = " ".join(words).strip()
    res_lower = res.lower()
    
    forbidden_starts = [
        "mencion de", "mencion a", "mencion en", "mencion del", "presencia de",
        "declaraciones de", "noticia sobre", "alusion a", "referencia a"
    ]
    for fs in forbidden_starts:
        if res_lower.startswith(fs):
            res = res[len(fs):].strip()
            break
            
    brand_words = set(re.findall(r"\b[a-z0-9]+\b", unidecode(brand.lower())))
    res_words = set(re.findall(r"\b[a-z0-9]+\b", unidecode(res.lower())))
    
    if not res or res_words.issubset(brand_words) or res_lower in ["universidad", "autonoma", "fundacion", "clinica", "hospital", "institucion", "asociacion"]:
        return _fallback_from_title(title_fallback)
        
    return res.capitalize()

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
    s_clean = subtema.strip().capitalize()
    
    if t_clean.lower() == s_clean.lower() or fuzz.ratio(t_clean.lower(), s_clean.lower()) >= 80:
        c_low = f"{s_clean} {ctx}".lower()
        if any(w in c_low for w in ["salud", "hospital", "clinica", "medico", "medicina", "paciente", "quirurg", "enfermedad", "achc"]):
            return "Sector Salud"
        if any(w in c_low for w in ["aduan", "dian", "fiscal", "tributar", "impuesto", "arancel"]):
            return "Gestión Tributaria"
        if any(w in c_low for w in ["universidad", "estudiante", "academ", "carrera", "educacion", "profesor", "beca", "uao", "feria", "inspirate"]):
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
    """Verifica si el contexto contiene acciones explícitas de liderazgo o respaldo positivo."""
    c_low = unidecode(ctx.lower())
    positive_actions = [
        "celebra y respalda", "respalda el nombramiento", "respaldan el nombramiento",
        "acompanamos desde", "acompanamiento desde", "asesoria gratuita", "apoyo gratuito",
        "pusieron en marcha", "pone en marcha", "felicita a", "felicitamos a",
        "rinde homenaje", "reconocimiento destaca el compromiso"
    ]
    has_positive = any(p in c_low for p in positive_actions)
    has_negative_allegation = any(n in c_low for n in ["denuncia penal", "sancion fiscal", "investigacion por corrupcion", "plagio"])
    return has_positive and not has_negative_allegation

def _fallback_from_title(title: str) -> str:
    if not title:
        return "Hecho Informativo"
    t = re.sub(r"^(?:imagenes|video|en fotos)\s*\|\s*", "", title, flags=re.IGNORECASE).strip()
    words = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', t).split()
    clean_words = words[:6]
    while clean_words and clean_words[-1].lower() in FORBIDDEN_TRAILING_WORDS:
        clean_words.pop()
    return " ".join(clean_words).capitalize() if clean_words else "Hecho Informativo"

def cluster_similar_rows(rows: List[dict], km: dict, brand_regexes: List[str]) -> Dict[int, int]:
    n = len(rows)
    cluster_map = {}
    clusters_rep = {}
    current_cluster = 0
    
    active_indices = [i for i in range(n) if not rows[i].get("is_duplicate")]
    sorted_indices = sorted(
        active_indices,
        key=lambda idx: normalize_text_for_matching(str(rows[idx].get(km.get("titulo", "Título"), "")))
    )

    for i in sorted_indices:
        t_raw = str(rows[i].get(km.get("titulo", "Título"), ""))
        r_raw = str(rows[i].get("Resumen - Aclaracion") or rows[i].get("resumen corto") or "")
        
        t_norm = normalize_text_for_matching(t_raw)
        c_words = get_content_words_set(t_norm)
        lead_words = get_lead_content_words(t_norm, n_words=3)
        anchor = extract_event_anchor(t_raw)
        r_norm = normalize_text_for_matching(r_raw[:350])
        
        assigned = False
        for cid, rep in clusters_rep.items():
            rep_t = rep["title_norm"]
            rep_words = rep["content_words"]
            rep_lead = rep["lead_words"]
            rep_anchor = rep["anchor"]
            rep_r = rep["body_norm"]
            
            if anchor and rep_anchor and anchor == rep_anchor:
                cluster_map[i] = cid
                assigned = True
                break
                
            if len(lead_words) >= 3 and len(rep_lead) >= 3 and lead_words == rep_lead:
                cluster_map[i] = cid
                assigned = True
                break

            if len(lead_words) >= 2 and len(rep_lead) >= 2 and lead_words[:2] == rep_lead[:2]:
                combined_lead_len = len(" ".join(lead_words[:2]))
                if combined_lead_len >= 13:
                    cluster_map[i] = cid
                    assigned = True
                    break

            if t_norm and rep_t:
                if t_norm in rep_t or rep_t in t_norm:
                    cluster_map[i] = cid
                    assigned = True
                    break
                min_len = min(len(t_norm), len(rep_t))
                if min_len >= 18 and t_norm[:18] == rep_t[:18]:
                    cluster_map[i] = cid
                    assigned = True
                    break
                if fuzz.partial_ratio(t_norm, rep_t) >= 86:
                    cluster_map[i] = cid
                    assigned = True
                    break
            
            overlap = c_words & rep_words
            if len(overlap) >= 4 or (len(overlap) >= 3 and any(re.search(rx, " ".join(overlap)) for rx in brand_regexes)):
                cluster_map[i] = cid
                assigned = True
                break
                
            if t_norm and rep_t:
                if fuzz.token_set_ratio(t_norm, rep_t) >= 70:
                    cluster_map[i] = cid
                    assigned = True
                    break
            
            if r_norm and rep_r and len(r_norm) > 40 and len(rep_r) > 40:
                if fuzz.token_set_ratio(r_norm, rep_r) >= 82:
                    cluster_map[i] = cid
                    assigned = True
                    break
                    
        if not assigned:
            cluster_map[i] = current_cluster
            clusters_rep[current_cluster] = {
                "title_norm": t_norm,
                "content_words": c_words,
                "lead_words": lead_words,
                "anchor": anchor,
                "body_norm": r_norm
            }
            current_cluster += 1
            
    return cluster_map

def canonicalize_subtopics(cluster_results: Dict[int, Tuple[str, str, str]]) -> Dict[int, Tuple[str, str, str]]:
    subtemas_list = [sub for _, _, sub in cluster_results.values() if sub]
    counts = Counter(subtemas_list)
    unique_subs = list(counts.keys())
    
    mapping = {}
    for i in range(len(unique_subs)):
        s1 = unique_subs[i]
        norm1 = normalize_text_for_matching(s1)
        for j in range(i + 1, len(unique_subs)):
            s2 = unique_subs[j]
            norm2 = normalize_text_for_matching(s2)
            if norm1 == norm2 or fuzz.token_set_ratio(norm1, norm2) >= 70 or fuzz.token_sort_ratio(norm1, norm2) >= 70:
                chosen = s1 if counts[s1] >= counts[s2] else s2
                mapping[s1] = chosen
                mapping[s2] = chosen

    final_results = {}
    for cid, (tono, tema, sub) in cluster_results.items():
        canonical_sub = mapping.get(sub, sub)
        final_results[cid] = (tono, tema, canonical_sub)
        
    return final_results

def _call_openai_cluster(
    client: OpenAI,
    model: str,
    brand: str,
    aliases: List[str],
    brand_regexes: List[str],
    ctx: str,
    title_ref: str
) -> Tuple[str, str, str]:
    # 1. Filtro estricto: Solo si la noticia es PURAMENTE un crédito de autor sin protagonismo institucional
    if is_pure_byline_mention(title_ref, ctx, brand_regexes):
        return "Neutro", "Estudiantes", "Redacción de artículo"

    prompt = f"""Analiza esta noticia para el cliente: "{brand}" (Alias: {', '.join(aliases) if aliases else 'Ninguno'}).

Titular de referencia: "{title_ref}"
Contexto analizado:
\"\"\"{ctx}\"\"\"

EJEMPLOS DE REFERENCIA OBLIGATORIA DE TONO:
- Caso 1: "Sismo en la región: Acompañamos desde la Universidad Autónoma de Occidente a las familias afectadas..."
  -> Tono: "Positivo" (solidaridad, presencia y liderazgo institucional, aunque el suceso sea una tragedia).
- Caso 2: "Polémica política: La Universidad Autónoma de Occidente celebra y respalda el nombramiento del ministro..."
  -> Tono: "Positivo" (respaldo y posicionamiento gremial favorable de la entidad).
- Caso 3: "UAO y DIAN abren espacio de asesoría gratuita en trámites aduaneros..."
  -> Tono: "Positivo" (convenio institucional y servicio a la comunidad).
- Caso 4: "Denuncian quejas por cobros excesivos y mala atención en la entidad..."
  -> Tono: "Negativo" (crítica o perjuicio directo).
- Caso 5: "Boletín de cifras económicas donde la entidad aporta un indicador técnico..."
  -> Tono: "Neutro" (meramente informativo).

Instrucciones taxonómicas:
1. "tono": Impacto reputacional en el cliente ("{brand}"): "Positivo", "Negativo" o "Neutro".
   REGLA DE TONO POSITIVO: Siempre que el cliente exprese o reciba ACOMPAÑAMIENTO, RESPALDO, APOYO, FELICITACIONES, CELEBRACIÓN, ALIANZA, CONVENIO O SOLIDARIDAD, el tono es estrictamente "Positivo".
2. "tema": ÁREA O DOMINIO GENERAL (Nivel Macro, 1 a 3 palabras. Ej: "Educación Superior", "Gestión Tributaria", "Sector Salud", "Gestión Institucional"). PROHIBIDO usar "Otros".
3. "subtema": HECHO O SUCESO ESPECÍFICO (Nivel Micro, máximo 6 palabras. Sin comas ni puntos. PROHIBIDO usar la palabra "Mención" o nombrar únicamente al cliente).

REGLA INQUEBRANTABLE: "tema" y "subtema" DEBEN SER DIFERENTES.

Responde estrictamente en JSON:
{{"tono": "...", "tema": "...", "subtema": "..."}}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Auditor senior de monitoreo de medios. Clasifica el tono y los hechos con alta precisión institucional."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=100
        )
        data = json.loads(resp.choices[0].message.content)
        
        tono_raw = str(data.get("tono", "Neutro")).strip().capitalize()
        tono = tono_raw if tono_raw in ["Positivo", "Negativo", "Neutro"] else "Neutro"
        
        # Salvaguarda directa: Acompañamientos y respaldos nunca son negativos ni neutros
        if check_positive_institutional_override(ctx):
            tono = "Positivo"
            
        subtema = clean_subtema(data.get("subtema", ""), brand, title_ref)
        tema = clean_tema(data.get("tema", ""))
        
        tema = ensure_different_tema_subtema(tema, subtema, ctx)
        
        return tono, tema, subtema
    except Exception as e:
        logger.error(f"Error en llamada OpenAI: {e}")
        sub_fb = _fallback_from_title(title_ref)
        tema_fb = ensure_different_tema_subtema("Gestión Institucional", sub_fb, ctx)
        tono_fb = "Positivo" if check_positive_institutional_override(ctx) else "Neutro"
        return tono_fb, tema_fb, sub_fb

def enrich_rows_with_ai(
    rows: List[dict],
    km: dict,
    brand: str,
    aliases: List[str],
    api_key: str,
    model: str = "gpt-4.1-nano-2025-04-14",
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> List[dict]:
    client = OpenAI(api_key=api_key)
    
    brand_regexes = generate_brand_variants(brand, aliases)
    
    if progress_callback:
        progress_callback(71, "Extrayendo contexto de la marca y sus variantes para auditoría…")
    for row in rows:
        if row.get("is_duplicate"):
            row["Contexto analizado"] = "-"
        else:
            resumen_val = row.get("Resumen - Aclaracion") or row.get("resumen corto") or row.get("Resumen") or ""
            titulo_val = row.get(km.get("titulo", "Título")) or ""
            
            ctx = extract_brand_context(
                str(resumen_val),
                str(titulo_val),
                brand_regexes
            )
            row["Contexto analizado"] = ctx

    if progress_callback:
        progress_callback(74, "Agrupando eventos y noticias similares (ordenamiento por titular)…")
    cluster_map = cluster_similar_rows(rows, km, brand_regexes)
    
    unique_clusters = sorted(set(cluster_map.values()))
    total_clusters = len(unique_clusters)
    
    cluster_to_sample_idx = {}
    for row_idx, cid in cluster_map.items():
        if cid not in cluster_to_sample_idx:
            cluster_to_sample_idx[cid] = row_idx
            
    cluster_results: Dict[int, Tuple[str, str, str]] = {}
    
    if progress_callback:
        progress_callback(77, f"Analizando {total_clusters} hechos únicos con {model}…")
        
    completed = 0
    with ThreadPoolExecutor(max_workers=14) as executor:
        future_to_cid = {}
        for cid, row_idx in cluster_to_sample_idx.items():
            ctx = rows[row_idx]["Contexto analizado"]
            t_ref = str(rows[row_idx].get(km.get("titulo", "Título"), ""))
            fut = executor.submit(_call_openai_cluster, client, model, brand, aliases, brand_regexes, ctx, t_ref)
            future_to_cid[fut] = cid
            
        for fut in as_completed(future_to_cid):
            cid = future_to_cid[fut]
            tono, tema, subtema = fut.result()
            cluster_results[cid] = (tono, tema, subtema)
            completed += 1
            if progress_callback and (completed % 15 == 0 or completed == total_clusters):
                pct = 77 + int((completed / total_clusters) * 16)
                progress_callback(pct, f"Analizando con IA… {completed}/{total_clusters} procesados")

    cluster_results = canonicalize_subtopics(cluster_results)

    for i, row in enumerate(rows):
        if row.get("is_duplicate"):
            row["Tono_IA"] = "Duplicada"
            row["Tema_IA"] = "-"
            row["Subtema_IA"] = "-"
            continue
            
        cid = cluster_map.get(i)
        if cid is not None and cid in cluster_results:
            tono, tema, subtema = cluster_results[cid]
            tema_final = ensure_different_tema_subtema(tema, subtema, row.get("Contexto analizado", ""))
            
            row["Tono_IA"] = tono
            row["Tema_IA"] = tema_final
            row["Subtema_IA"] = subtema
        else:
            row["Tono_IA"] = "Neutro"
            row["Tema_IA"] = "Gestión Institucional"
            row["Subtema_IA"] = "Hecho Informativo"
            
    return rows
