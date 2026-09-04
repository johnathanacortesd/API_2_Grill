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

def clean_text_simple(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()

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
    """Retorna conjunto de palabras clave con peso semántico."""
    return {w for w in text_norm.split() if len(w) > 2 and w not in STOPWORDS_ES}

def _get_target_regexes(brand: str, aliases: List[str]) -> List[str]:
    """Genera expresiones regulares robustas para la marca y sus alias (sin tildes, con siglas)."""
    raw_targets = [brand] + [a for a in aliases if a.strip()]
    regexes = []
    for t in raw_targets:
        clean_t = unidecode(t.lower().strip())
        if not clean_t:
            continue
        # Manejo de siglas: UAO permite también U.A.O.
        if len(clean_t) <= 5 and clean_t.isalpha():
            pattern = r"\b" + r"\.?\s*".join(list(clean_t)) + r"\.?\b"
            regexes.append(pattern)
        else:
            regexes.append(rf"\b{re.escape(clean_t)}\b")
    return regexes

def extract_brand_context(text: str, title: str, brand: str, aliases: List[str]) -> str:
    """
    Extrae el texto que contiene la marca o alias sin agregar la palabra 'Título:'.
    Busca de forma estricta tanto en Título como en Resumen - Aclaración.
    """
    t_clean = clean_text_simple(title)
    r_clean = clean_text_simple(text)
    target_regexes = _get_target_regexes(brand, aliases)
    
    # 1. Verificar si el Título contiene la marca/alias
    t_norm = unidecode(t_clean.lower())
    title_matches = any(re.search(rx, t_norm) for rx in target_regexes)
    
    # 2. Separar Resumen en oraciones y buscar menciones
    sentences = [s.strip() for s in re.split(r'(?<=[.!?\n])\s+', r_clean) if s.strip()]
    matched_sentences = []
    
    for idx, s in enumerate(sentences):
        s_norm = unidecode(s.lower())
        if any(re.search(rx, s_norm) for rx in target_regexes):
            block = s
            # Si la oración es muy corta (menos de 7 palabras), agregar la contigua para contexto
            if len(s.split()) < 7 and idx + 1 < len(sentences):
                block = f"{s} {sentences[idx + 1]}"
            if block not in matched_sentences:
                matched_sentences.append(block)
                
    # 3. Consolidar el texto del contexto
    parts = []
    if title_matches:
        parts.append(t_clean)
    if matched_sentences:
        parts.extend(matched_sentences)
        
    if parts:
        return " ".join(parts)[:750].strip()
        
    # Si NO se encontró mención directa en ningún lado, devolver Título + inicio de Resumen sin prefijos
    if t_clean and r_clean:
        return f"{t_clean}. {r_clean[:400]}".strip()
    return t_clean or r_clean[:500]

def is_byline_or_student_author(context_text: str, brand: str, aliases: List[str]) -> bool:
    """Detecta si la mención es de autoría o redacción por un estudiante/egresado."""
    ctx_norm = unidecode(context_text.lower())
    target_regexes = _get_target_regexes(brand, aliases)
    
    for rx in target_regexes:
        p1 = rf"(?:egresad[oa]|estudiante|graduad[oa]|practicante|redactor[a]?|periodista)\s+(?:de|en|del)?\s+(?:la\s+)?{rx}"
        if re.search(p1, ctx_norm):
            return True
        p2 = rf"(?:por|autor[a]?):\s*[\w\s]+,?\s*(?:egresad[oa]|estudiante|periodista).*{rx}"
        if re.search(p2, ctx_norm):
            return True
    return False

def clean_subtema(text: str, brand: str, title_fallback: str) -> str:
    """Garantiza especificidad y prohíbe 'Mención de...'."""
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
    
    if not res or res_words.issubset(brand_words) or res_lower in ["universidad", "autonoma", "occidente", "institucion"]:
        return _fallback_from_title(title_fallback)
        
    return res.capitalize()

def _fallback_from_title(title: str) -> str:
    if not title:
        return "Hecho Informativo"
    t = re.sub(r"^(?:imagenes|video|en fotos)\s*\|\s*", "", title, flags=re.IGNORECASE).strip()
    words = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', t).split()
    clean_words = words[:6]
    while clean_words and clean_words[-1].lower() in FORBIDDEN_TRAILING_WORDS:
        clean_words.pop()
    return " ".join(clean_words).capitalize() if clean_words else "Hecho Informativo"

def cluster_similar_rows(rows: List[dict], km: dict, brand: str, aliases: List[str]) -> Dict[int, int]:
    """
    Agrupa noticias que traten del mismo hecho.
    Detecta de forma inteligente notas con redacciones ligeramente diferentes (ej: UAO y DIAN).
    """
    n = len(rows)
    cluster_map = {}
    clusters_rep = {}  # cid: {"title_norm": str, "content_words": set, "body_norm": str}
    current_cluster = 0
    
    target_stems = set()
    for t in [brand] + aliases:
        target_stems.update(normalize_text_for_matching(t).split())
        
    for i in range(n):
        if rows[i].get("is_duplicate"):
            continue
            
        t_raw = str(rows[i].get(km.get("titulo", "Título"), ""))
        r_raw = str(rows[i].get(km.get("resumen", "Resumen - Aclaracion"), ""))
        
        t_norm = normalize_text_for_matching(t_raw)
        c_words = get_content_words_set(t_norm)
        r_norm = normalize_text_for_matching(r_raw[:350])
        
        assigned = False
        for cid, rep in clusters_rep.items():
            rep_t = rep["title_norm"]
            rep_words = rep["content_words"]
            rep_r = rep["body_norm"]
            
            # 1. Coincidencia por conjunto de palabras clave (Content Word Overlap)
            overlap = c_words & rep_words
            has_brand_in_overlap = any(b in overlap for b in target_stems)
            
            # Si comparten 4 o más palabras clave, O comparten 3 incluyendo la marca/alias: ES LA MISMA NOTICIA
            if len(overlap) >= 4 or (len(overlap) >= 3 and has_brand_in_overlap):
                cluster_map[i] = cid
                assigned = True
                break
                
            # 2. Coincidencia difusa de título
            if t_norm and rep_t:
                sim_title = fuzz.token_set_ratio(t_norm, rep_t)
                if sim_title >= 72:
                    cluster_map[i] = cid
                    assigned = True
                    break
            
            # 3. Coincidencia por resumen (cables de noticias)
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
                "body_norm": r_norm
            }
            current_cluster += 1
            
    return cluster_map

def canonicalize_subtopics(cluster_results: Dict[int, Tuple[str, str, str]]) -> Dict[int, Tuple[str, str, str]]:
    """Unifica variaciones de subtemas al más frecuente."""
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
            if norm1 == norm2 or fuzz.token_set_ratio(norm1, norm2) >= 75 or fuzz.token_sort_ratio(norm1, norm2) >= 75:
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
    ctx: str,
    title_ref: str
) -> Tuple[str, str, str]:
    """Llamada a la API con reglas reputacionales estrictas."""
    if is_byline_or_student_author(ctx, brand, aliases):
        return "Neutro", "Estudiantes", "Redacción de artículo"

    prompt = f"""Analiza esta noticia para el cliente: "{brand}" (Alias: {', '.join(aliases) if aliases else 'Ninguno'}).

Contexto analizado:
\"\"\"{ctx}\"\"\"

Instrucciones:
1. "tono": Evalúa el impacto directo en la reputación del cliente ("{brand}"):
   - "Positivo": Exalta logros, convenios, servicios a la comunidad, vocería o reconocimientos.
   - "Negativo": Crítica, denuncia, sanción o perjuicio directo.
   - "Neutro": Noticia informativa, técnica u objetiva.
   Valores válidos: "Positivo", "Negativo", "Neutro".

2. "subtema": Especifica el HECHO O SUCESO CONCRETO de la noticia:
   - Máximo 6 palabras.
   - Sin signos de puntuación, comas ni puntos.
   - PROHIBIDO terminar en preposiciones (de, en, para, por, con), artículos o verbos.
   - PROHIBIDO usar la palabra "Mención" o nombrar únicamente al cliente (JAMÁS devuelvas "Mención de {brand}" ni "Mención de la universidad").

Responde únicamente un JSON: {{"tono": "...", "subtema": "..."}}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Auditor senior de monitoreo de medios. Responde estrictamente en JSON válido."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=80
        )
        data = json.loads(resp.choices[0].message.content)
        tono_raw = str(data.get("tono", "Neutro")).strip().capitalize()
        tono = tono_raw if tono_raw in ["Positivo", "Negativo", "Neutro"] else "Neutro"
        subtema = clean_subtema(data.get("subtema", ""), brand, title_ref)
        return tono, "", subtema
    except Exception as e:
        logger.error(f"Error en llamada OpenAI: {e}")
        return "Neutro", "", _fallback_from_title(title_ref)

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
    
    # 1. Extraer y poblar la columna 'Contexto analizado' limpia
    if progress_callback:
        progress_callback(71, "Extrayendo contexto de la marca y alias para auditoría…")
    for row in rows:
        if row.get("is_duplicate"):
            row["Contexto analizado"] = "-"
        else:
            ctx = extract_brand_context(
                str(row.get(km.get("resumen", "Resumen - Aclaracion"), "")),
                str(row.get(km.get("titulo", "Título"), "")),
                brand,
                aliases
            )
            row["Contexto analizado"] = ctx

    # 2. Agrupamiento semántico con detección de entidades
    if progress_callback:
        progress_callback(74, "Agrupando noticias similares y hechos compartidos…")
    cluster_map = cluster_similar_rows(rows, km, brand, aliases)
    
    unique_clusters = sorted(set(cluster_map.values()))
    total_clusters = len(unique_clusters)
    
    cluster_to_sample_idx = {}
    for row_idx, cid in cluster_map.items():
        if cid not in cluster_to_sample_idx:
            cluster_to_sample_idx[cid] = row_idx
            
    cluster_results: Dict[int, Tuple[str, str, str]] = {}
    
    # 3. Clasificación concurrente
    if progress_callback:
        progress_callback(77, f"Analizando {total_clusters} hechos únicos con {model}…")
        
    completed = 0
    with ThreadPoolExecutor(max_workers=14) as executor:
        future_to_cid = {}
        for cid, row_idx in cluster_to_sample_idx.items():
            ctx = rows[row_idx]["Contexto analizado"]
            t_ref = str(rows[row_idx].get(km.get("titulo", "Título"), ""))
            fut = executor.submit(_call_openai_cluster, client, model, brand, aliases, ctx, t_ref)
            future_to_cid[fut] = cid
            
        for fut in as_completed(future_to_cid):
            cid = future_to_cid[fut]
            tono, tema_pre, subtema = fut.result()
            cluster_results[cid] = (tono, tema_pre, subtema)
            completed += 1
            if progress_callback and (completed % 15 == 0 or completed == total_clusters):
                pct = 77 + int((completed / total_clusters) * 14)
                progress_callback(pct, f"Analizando con IA… {completed}/{total_clusters} procesados")

    # 4. Unificar subtemas casi idénticos
    cluster_results = canonicalize_subtopics(cluster_results)

    # 5. Generación de Macro-temas específicos (sin 'Otros')
    if progress_callback:
        progress_callback(92, "Consolidando Temas macro específicos…")
        
    all_subtemas = list({res[2] for res in cluster_results.values() if res[2] and res[2] != "Redacción de artículo"})
    temas_map = {"Redacción de artículo": "Estudiantes"}
    
    if all_subtemas:
        macro_prompt = f"""Aquí tienes una lista de subtemas sobre "{brand}":
{json.dumps(all_subtemas, ensure_ascii=False)}

Tu tarea es categorizar cada subtema bajo un "Tema" macro formal y representativo (1 a 4 palabras).
Reglas:
1. PROHIBIDO usar "Otros", "General", "Varios", "Miscelánea" o "Sin clasificar".
2. Si un subtema no se agrupa con los demás, asígnale un Tema propio de su sector (ejemplos: "Gestión Tributaria", "Educación Superior", "Gestión de Emergencias", "Infraestructura").

Devuelve exclusivamente un JSON objeto key-value. Ejemplo:
{{"Asesoría en trámites fiscales y aduaneros": "Gestión Tributaria"}}"""

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Experto en taxonomía sectorial de medios. Devuelve estrictamente un JSON key-value sin usar 'Otros'."},
                    {"role": "user", "content": macro_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            data_temas = json.loads(resp.choices[0].message.content)
            temas_map.update(data_temas)
        except Exception as e:
            logger.error(f"Error generando macro-temas: {e}")
            for st in all_subtemas:
                temas_map[st] = st

    # 6. Mapear a todas las filas en el orden original
    for i, row in enumerate(rows):
        if row.get("is_duplicate"):
            row["Tono_IA"] = "Duplicada"
            row["Tema_IA"] = "-"
            row["Subtema_IA"] = "-"
            continue
            
        cid = cluster_map.get(i)
        if cid is not None and cid in cluster_results:
            tono, tema_pre, subtema = cluster_results[cid]
            tema_final = tema_pre if tema_pre else temas_map.get(subtema, subtema)
            if str(tema_final).lower() in ["otros", "otro", "general", "varios", "sin clasificar"]:
                tema_final = subtema
                
            row["Tono_IA"] = tono
            row["Tema_IA"] = tema_final
            row["Subtema_IA"] = subtema
        else:
            row["Tono_IA"] = "Neutro"
            row["Tema_IA"] = "Informativo"
            row["Subtema_IA"] = "Hecho Noticioso"
            
    return rows
