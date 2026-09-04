# ======================================
# Motor de Análisis con IA (ai_analyzer.py)
# ======================================
import os
import re
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional, Callable
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
    "de", "la", "el", "los", "las", "en", "para", "por", "con", "a", "al", "del",
    "y", "o", "un", "una", "unos", "unas", "sobre", "tras", "este", "esta", "estos",
    "estas", "fue", "fueron", "era", "eran", "como", "mas", "pero", "sus", "que"
}

def normalize_text_for_matching(text: str) -> str:
    """Normaliza texto para comparación eliminando plurales y prefijos comunes."""
    if not text:
        return ""
    t = unidecode(str(text).lower().strip())
    # Quitar prefijos multimedia como "imagenes |", "video |", "en fotos |"
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

def extract_brand_context(text: str, title: str, brand: str, aliases: List[str]) -> Tuple[str, bool]:
    """
    Extrae el texto de análisis: Título + Resumen.
    Retorna: (contexto_analizado, es_mencion_directa)
    """
    title_clean = re.sub(r"\s+", " ", str(title or "")).strip()
    text_clean = re.sub(r"\s+", " ", str(text or "")).strip()
    
    targets = [brand.lower()] + [a.lower().strip() for a in aliases if a.strip()]
    full_text = f"{title_clean}. {text_clean}".strip()
    
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    matched_blocks = []
    
    for idx, sentence in enumerate(sentences):
        s_lower = sentence.lower()
        if any(re.search(rf"\b{re.escape(t)}\b", s_lower) for t in targets):
            prev_s = sentences[idx - 1] if idx > 0 else ""
            next_s = sentences[idx + 1] if idx + 1 < len(sentences) else ""
            block = f"{prev_s} {sentence} {next_s}".strip()
            if block and block not in matched_blocks:
                matched_blocks.append(block)
                
    if matched_blocks:
        extracted = f"Título: {title_clean} | Contexto extraído: " + " [...] ".join(matched_blocks)
        return extracted[:750], True
        
    # Si no hay mención directa, SIEMPRE analizar Título + Resumen
    fallback = f"Título: {title_clean}. Resumen: {text_clean[:450]}"
    return fallback.strip(), False

def is_byline_or_student_author(context_text: str, brand: str, aliases: List[str]) -> bool:
    """Detecta si la mención es de autoría/egresado/estudiante que redactó la noticia."""
    targets = [brand.lower()] + [a.lower().strip() for a in aliases if a.strip()]
    for t in targets:
        # Ej: "egresada de la Universidad Autónoma de Occidente", "estudiante de la UAO"
        p1 = rf"(?:egresad[oa]|estudiante|graduad[oa]|practicante|redactor[a]?|periodista)\s+(?:de|en|del)?\s+(?:la\s+)?{re.escape(t)}"
        if re.search(p1, context_text, re.IGNORECASE):
            return True
        # Ej: "Por: Laura Gómez, egresada..."
        p2 = rf"(?:por|autor[a]?):\s*[\w\s]+,?\s*(?:egresad[oa]|estudiante|periodista).*{re.escape(t)}"
        if re.search(p2, context_text, re.IGNORECASE):
            return True
    return False

def clean_subtema(text: str, brand: str, title_fallback: str) -> str:
    """Limpia el subtema y prohíbe terminantemente frases tipo 'Mención de...'."""
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
    
    # Lista de frases prohibidas
    forbidden_starts = [
        "mencion de", "mencion a", "mencion en", "mencion del", "presencia de",
        "declaraciones de", "noticia sobre", "alusion a", "referencia a"
    ]
    for fs in forbidden_starts:
        if res_lower.startswith(fs):
            res = res[len(fs):].strip()
            break
            
    # Si después de limpiar quedó solo el nombre de la marca o una palabra vacía
    brand_words = set(re.findall(r"\b[a-z0-9]+\b", unidecode(brand.lower())))
    res_words = set(re.findall(r"\b[a-z0-9]+\b", unidecode(res.lower())))
    
    if not res or res_words.issubset(brand_words) or res_lower in ["universidad", "autonoma", "occidente", "institucion"]:
        return _fallback_from_title(title_fallback)
        
    return res.capitalize()

def _fallback_from_title(title: str) -> str:
    """Extrae un subtema de emergencia a partir del título cuando la IA falla."""
    if not title:
        return "Hecho Informativo"
    t = re.sub(r"^(?:imagenes|video|en fotos)\s*\|\s*", "", title, flags=re.IGNORECASE).strip()
    words = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', t).split()
    clean_words = words[:6]
    while clean_words and clean_words[-1].lower() in FORBIDDEN_TRAILING_WORDS:
        clean_words.pop()
    return " ".join(clean_words).capitalize() if clean_words else "Hecho Informativo"

def cluster_similar_rows(rows: List[dict], km: dict) -> Dict[int, int]:
    """Agrupa filas que hablen del mismo hecho por título y resumen."""
    n = len(rows)
    cluster_map = {}
    clusters_rep = {}
    current_cluster = 0
    
    for i in range(n):
        if rows[i].get("is_duplicate"):
            continue
            
        t_raw = str(rows[i].get(km.get("titulo", "Título"), ""))
        r_raw = str(rows[i].get(km.get("resumen", "Resumen - Aclaracion"), ""))
        
        t_norm = normalize_text_for_matching(t_raw)
        r_norm = normalize_text_for_matching(r_raw[:350])
        
        assigned = False
        for cid, rep in clusters_rep.items():
            rep_t = rep["title_norm"]
            rep_r = rep["body_norm"]
            
            if t_norm and rep_t:
                if t_norm == rep_t or fuzz.token_set_ratio(t_norm, rep_t) >= 84:
                    cluster_map[i] = cid
                    assigned = True
                    break
            
            if r_norm and rep_r and len(r_norm) > 40 and len(rep_r) > 40:
                if fuzz.token_set_ratio(r_norm, rep_r) >= 86:
                    cluster_map[i] = cid
                    assigned = True
                    break
                    
        if not assigned:
            cluster_map[i] = current_cluster
            clusters_rep[current_cluster] = {"title_norm": t_norm, "body_norm": r_norm}
            current_cluster += 1
            
    return cluster_map

def canonicalize_subtopics(cluster_results: Dict[int, Tuple[str, str, str]]) -> Dict[int, Tuple[str, str, str]]:
    """Unifica variaciones mínimas entre subtemas al más frecuente."""
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
            if norm1 == norm2 or fuzz.token_sort_ratio(norm1, norm2) >= 85:
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
    """Clasificación con reglas estrictas."""
    # REGLA OBLIGATORIA: Autoría de egresado o estudiante
    if is_byline_or_student_author(ctx, brand, aliases):
        return "Neutro", "Estudiantes", "Redacción de artículo"

    prompt = f"""Analiza esta noticia para el cliente: "{brand}" (Alias: {', '.join(aliases) if aliases else 'Ninguno'}).

Contexto analizado:
\"\"\"{ctx}\"\"\"

Instrucciones estrictas:
1. "tono": Evalúa el impacto directo en la reputación del cliente ("{brand}"):
   - "Positivo": Se exalta su gestión, logros, vocería, convenios o premios.
   - "Negativo": Se cuestiona, critica, denuncia o perjudica directamente su imagen.
   - "Neutro": Es una mención objetiva o informativa (si la noticia trata de un hecho trágico o problema general pero el cliente no es causante ni culpable, el tono es "Neutro").
   Valores permitidos: "Positivo", "Negativo", "Neutro".

2. "subtema": Especifica el HECHO, ACCIÓN O SUCESO CONCRETO de la noticia:
   - Máximo 6 palabras.
   - Debe ser completo y coherente.
   - PROHIBIDO usar puntos, comas o signos de puntuación.
   - PROHIBIDO terminar en preposiciones (de, en, para, por, con), artículos o verbos.
   - PROHIBIDO ROTUNDAMENTE usar la palabra "Mención" o poner únicamente el nombre del cliente (JAMÁS devuelvas cosas como "Mención de {brand}", "Presencia de la universidad", "Noticia de {brand}"). Describe el hecho noticioso (ej: "Posesión presidencial en Cali", "Labores de rescate en Cali", "Acreditación de ingeniería", "Pronunciamiento sobre transporte").

Devuelve únicamente un JSON: {{"tono": "...", "subtema": "..."}}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Eres un auditor senior de medios. Responde estrictamente en JSON válido sin usar frases vacías ni la palabra Mención."},
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
    
    # 1. Extraer y poblar siempre la columna 'Contexto analizado'
    if progress_callback:
        progress_callback(71, "Extrayendo Título y Resumen para auditoría…")
    for row in rows:
        if row.get("is_duplicate"):
            row["Contexto analizado"] = "-"
        else:
            ctx, _ = extract_brand_context(
                str(row.get(km.get("resumen", "Resumen - Aclaracion"), "")),
                str(row.get(km.get("titulo", "Título"), "")),
                brand,
                aliases
            )
            row["Contexto analizado"] = ctx

    # 2. Agrupamiento semántico
    if progress_callback:
        progress_callback(74, "Agrupando noticias similares y cables de agencia…")
    cluster_map = cluster_similar_rows(rows, km)
    
    unique_clusters = sorted(set(cluster_map.values()))
    total_clusters = len(unique_clusters)
    
    cluster_to_sample_idx = {}
    for row_idx, cid in cluster_map.items():
        if cid not in cluster_to_sample_idx:
            cluster_to_sample_idx[cid] = row_idx
            
    cluster_results: Dict[int, Tuple[str, str, str]] = {}
    
    # 3. Clasificación paralela
    if progress_callback:
        progress_callback(77, f"Analizando {total_clusters} eventos únicos con {model}…")
        
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

    # 5. Generación de Macro-temas específicos (PROHIBIDO 'Otros')
    if progress_callback:
        progress_callback(92, "Consolidando Temas macro específicos…")
        
    all_subtemas = list({res[2] for res in cluster_results.values() if res[2] and res[2] != "Redacción de artículo"})
    temas_map = {"Redacción de artículo": "Estudiantes"}
    
    if all_subtemas:
        macro_prompt = f"""Aquí tienes una lista de subtemas sobre "{brand}":
{json.dumps(all_subtemas, ensure_ascii=False)}

Tu tarea es categorizar cada subtema bajo un "Tema" macro formal y representativo (1 a 4 palabras).
Reglas indispensables:
1. PROHIBIDO ROTUNDAMENTE usar "Otros", "General", "Varios", "Miscelánea", "Sin agrupar" o "Sin clasificar".
2. Si un subtema no se agrupa con los demás, asígnale un Tema propio de su sector (ejemplos: "Gestión Pública", "Emergencias y Rescate", "Educación Superior", "Seguridad Ciudadana", "Infraestructura").

Devuelve exclusivamente un JSON objeto key-value. Ejemplo:
{{"Labores de rescate en Cali": "Gestión de Emergencias", "Posesión presidencial en Cali": "Política Nacional"}}"""

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

    # 6. Mapear a todas las filas respetando el orden original
    for i, row in enumerate(rows):
        if row.get("is_duplicate"):
            row["Tono_IA"] = "Duplicada"
            row["Tema_IA"] = "-"
            row["Subtema_IA"] = "-"
            continue
            
        cid = cluster_map.get(i)
        if cid is not None and cid in cluster_results:
            tono, tema_pre, subtema = cluster_results[cid]
            if tema_pre:  # Caso especial predeterminado (ej: Estudiantes)
                tema_final = tema_pre
            else:
                tema_final = temas_map.get(subtema, subtema)
                
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
