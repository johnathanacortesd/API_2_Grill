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
    if not text or str(text).strip().lower() in ("nan", "none"):
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
    return {w for w in text_norm.split() if len(w) > 2 and w not in STOPWORDS_ES}

def generate_brand_variants(brand: str, aliases: List[str]) -> List[str]:
    """
    Genera automáticamente todas las variantes y sinónimos lógicos de cualquier cliente.
    Ejemplos:
    - 'Fundación Santa Fe' -> fundacion santa fe, santa fe, santafe, clinica santa fe, hospital santa fe
    - 'Serena del Mar' -> serena del mar, serena, hospital serena, hospital serena del mar, clinica serena del mar
    - 'Asociación Colombiana de Hospitales y Clínicas' -> asociacion colombiana de hospitales y clinicas, hospitales y clinicas, achc
    """
    raw_inputs = [brand] + [a for a in aliases if a.strip()]
    variants_set = set()

    for item in raw_inputs:
        base = unidecode(item.lower().strip())
        if not base:
            continue
        variants_set.add(base)

        # Si el usuario puso siglas entre paréntesis, ej: Asociación Colombiana (...) (ACHC)
        acronym_match = re.search(r"\(([a-z0-9]{2,6})\)", base)
        if acronym_match:
            acronym = acronym_match.group(1)
            variants_set.add(acronym)
            variants_set.add(r"\b" + r"\.?\s*".join(list(acronym)) + r"\.?\b")
            base = re.sub(r"\([a-z0-9]{2,6}\)", "", base).strip()
            variants_set.add(base)

        # Manejo directo de siglas
        if len(base) <= 5 and base.isalpha():
            variants_set.add(r"\b" + r"\.?\s*".join(list(base)) + r"\.?\b")
            continue

        # Generar variaciones de Santa Fe / Santafe
        if "santa fe" in base:
            variants_set.add(base.replace("santa fe", "santafe"))
            variants_set.add("santa fe")
            variants_set.add("santafe")
            variants_set.add("clinica santa fe")
            variants_set.add("hospital santa fe")
            variants_set.add("fundacion santa fe")

        # Generar variaciones tipo Serena del Mar / Serena
        if "serena del mar" in base:
            variants_set.add("serena")
            variants_set.add("hospital serena")
            variants_set.add("hospital serena del mar")
            variants_set.add("clinica serena")
            variants_set.add("clinica serena del mar")

        # Casos con prefijos institucionales comunes
        for prefix in ["fundacion", "clinica", "hospital", "universidad", "instituto", "asociacion"]:
            if base.startswith(prefix + " "):
                core = base[len(prefix):].strip()
                if len(core) >= 4:
                    variants_set.add(core)
                    # Recombinaciones naturales
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
    """
    Extrae rigurosamente las oraciones del Resumen - Aclaración donde se menciona la marca o variantes.
    NUNCA devuelve únicamente el título si hay contenido en el resumen.
    """
    t_clean = clean_text_simple(titulo)
    r_clean = clean_text_simple(resumen)
    
    r_norm = unidecode(r_clean.lower())
    t_norm = unidecode(t_clean.lower())
    
    matched_sentences = []
    
    # 1. Buscar en el cuerpo del Resumen oración por oración
    if r_clean:
        # Separar por puntos, saltos de línea o signos
        sentences = [s.strip() for s in re.split(r'(?<=[.!?\n])\s+', r_clean) if s.strip()]
        for idx, s in enumerate(sentences):
            s_norm = unidecode(s.lower())
            if any(re.search(rx, s_norm) for rx in brand_regexes):
                block = s
                # Si la oración es corta, agregar la siguiente para contexto
                if len(s.split()) < 10 and idx + 1 < len(sentences):
                    block = f"{s} {sentences[idx + 1]}"
                if block not in matched_sentences:
                    matched_sentences.append(block)

        # 2. Si el texto no tiene puntos, buscar por fragmento alrededor de la coincidencia
        if not matched_sentences:
            for rx in brand_regexes:
                for m in re.finditer(rx, r_norm):
                    start = max(0, m.start() - 120)
                    end = min(len(r_clean), m.end() + 150)
                    snippet = r_clean[start:end].strip()
                    if snippet and snippet not in matched_sentences:
                        matched_sentences.append(f"...{snippet}..." if start > 0 else snippet)
                    if len(matched_sentences) >= 2:
                        break
                if matched_sentences:
                    break

    # 3. Comprobar si el título menciona la marca
    title_matches = any(re.search(rx, t_norm) for rx in brand_regexes)

    # 4. Consolidar el resultado
    if matched_sentences:
        resumen_context = " ".join(matched_sentences).strip()
        # Si el título también menciona la marca y no está ya en el texto del resumen, unirlos
        if title_matches and t_clean and t_clean.lower() not in resumen_context.lower():
            return f"{t_clean}. {resumen_context}"[:800]
        return resumen_context[:800]

    # Si la marca solo estaba en el título, anexarle las primeras líneas del resumen para contexto
    if title_matches:
        if r_clean:
            return f"{t_clean}. {r_clean[:380]}".strip()[:800]
        return t_clean

    # Si la marca no aparece explícitamente en el texto (noticia de sector):
    if t_clean and r_clean:
        return f"{t_clean}. {r_clean[:400]}".strip()[:800]
    return t_clean or r_clean[:500]

def is_byline_or_student_author(context_text: str, brand_regexes: List[str]) -> bool:
    """Detecta si la mención es de autoría o redacción por un estudiante/egresado."""
    ctx_norm = unidecode(context_text.lower())
    for rx in brand_regexes:
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
    
    if not res or res_words.issubset(brand_words) or res_lower in ["universidad", "autonoma", "fundacion", "clinica", "hospital", "institucion", "asociacion"]:
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

def cluster_similar_rows(rows: List[dict], km: dict, brand_regexes: List[str]) -> Dict[int, int]:
    """
    Agrupa noticias que traten del mismo hecho.
    Detecta automáticamente:
    1. Si un título está contenido dentro de otro (ej: ACHC con coletilla de revista).
    2. Si los dos títulos empiezan igual (prefijo idéntico de más de 35 caracteres).
    3. Similitud parcial (partial_ratio >= 85) o palabras clave compartidas.
    """
    n = len(rows)
    cluster_map = {}
    clusters_rep = {}
    current_cluster = 0
    
    for i in range(n):
        if rows[i].get("is_duplicate"):
            continue
            
        t_raw = str(rows[i].get(km.get("titulo", "Título"), ""))
        r_raw = str(rows[i].get("Resumen - Aclaracion") or rows[i].get("resumen corto") or "")
        
        t_norm = normalize_text_for_matching(t_raw)
        c_words = get_content_words_set(t_norm)
        r_norm = normalize_text_for_matching(r_raw[:350])
        
        assigned = False
        for cid, rep in clusters_rep.items():
            rep_t = rep["title_norm"]
            rep_words = rep["content_words"]
            rep_r = rep["body_norm"]
            
            # REGLA 1: Contención o prefijo idéntico (caso exacto de ACHC)
            if t_norm and rep_t:
                # Si un título está dentro del otro
                if t_norm in rep_t or rep_t in t_norm:
                    cluster_map[i] = cid
                    assigned = True
                    break
                # Si empiezan igual con más de 35 caracteres
                min_len = min(len(t_norm), len(rep_t))
                if min_len >= 35 and t_norm[:35] == rep_t[:35]:
                    cluster_map[i] = cid
                    assigned = True
                    break
                # Similitud parcial (uno es subsección del otro)
                if fuzz.partial_ratio(t_norm, rep_t) >= 86:
                    cluster_map[i] = cid
                    assigned = True
                    break
            
            # REGLA 2: Conjunto de palabras clave compartidas
            overlap = c_words & rep_words
            if len(overlap) >= 4 or (len(overlap) >= 3 and any(re.search(rx, " ".join(overlap)) for rx in brand_regexes)):
                cluster_map[i] = cid
                assigned = True
                break
                
            # REGLA 3: Similitud difusa estándar de título
            if t_norm and rep_t:
                if fuzz.token_set_ratio(t_norm, rep_t) >= 72:
                    cluster_map[i] = cid
                    assigned = True
                    break
            
            # REGLA 4: Mismo cable de agencia en el resumen
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
            if norm1 == norm2 or fuzz.token_set_ratio(norm1, norm2) >= 72 or fuzz.token_sort_ratio(norm1, norm2) >= 72:
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
    """Llamada a la API con reglas reputacionales estrictas."""
    if is_byline_or_student_author(ctx, brand_regexes):
        return "Neutro", "Estudiantes", "Redacción de artículo"

    prompt = f"""Analiza esta noticia para el cliente: "{brand}" (Alias: {', '.join(aliases) if aliases else 'Ninguno'}).

Contexto analizado:
\"\"\"{ctx}\"\"\"

Instrucciones:
1. "tono": Evalúa el impacto directo en la reputación del cliente ("{brand}"):
   - "Positivo": Exalta logros, aniversarios, convenios, vocería gremial o reconocimientos.
   - "Negativo": Críticas, quejas, denuncias o perjuicio directo.
   - "Neutro": Noticia informativa, técnica u objetiva.
   Valores válidos: "Positivo", "Negativo", "Neutro".

2. "subtema": Especifica el HECHO O SUCESO CONCRETO de la noticia:
   - Máximo 6 palabras.
   - Sin signos de puntuación, comas ni puntos.
   - PROHIBIDO terminar en preposiciones (de, en, para, por, con), artículos o verbos.
   - PROHIBIDO usar la palabra "Mención" o nombrar únicamente al cliente.

Responde únicamente un JSON: {{"tono": "...", "subtema": "..."}}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Auditor senior de medios. Responde estrictamente en JSON válido sin usar la palabra Mención."},
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
    
    # 1. Generar variantes automáticas de la marca
    brand_regexes = generate_brand_variants(brand, aliases)
    
    # 2. Extraer contexto analizado buscando en Resumen y Título
    if progress_callback:
        progress_callback(71, "Extrayendo contexto de la marca y sus variantes para auditoría…")
    for row in rows:
        if row.get("is_duplicate"):
            row["Contexto analizado"] = "-"
        else:
            # Obtener el resumen con respaldo en 'resumen corto' si viene vacío
            resumen_val = row.get("Resumen - Aclaracion") or row.get("resumen corto") or row.get("Resumen") or ""
            titulo_val = row.get(km.get("titulo", "Título")) or ""
            
            ctx = extract_brand_context(
                str(resumen_val),
                str(titulo_val),
                brand_regexes
            )
            row["Contexto analizado"] = ctx

    # 3. Agrupamiento semántico con detección de prefijos y contención
    if progress_callback:
        progress_callback(74, "Agrupando noticias similares y hechos compartidos…")
    cluster_map = cluster_similar_rows(rows, km, brand_regexes)
    
    unique_clusters = sorted(set(cluster_map.values()))
    total_clusters = len(unique_clusters)
    
    cluster_to_sample_idx = {}
    for row_idx, cid in cluster_map.items():
        if cid not in cluster_to_sample_idx:
            cluster_to_sample_idx[cid] = row_idx
            
    cluster_results: Dict[int, Tuple[str, str, str]] = {}
    
    # 4. Clasificación concurrente (1 sola llamada por grupo)
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
            tono, tema_pre, subtema = fut.result()
            cluster_results[cid] = (tono, tema_pre, subtema)
            completed += 1
            if progress_callback and (completed % 15 == 0 or completed == total_clusters):
                pct = 77 + int((completed / total_clusters) * 14)
                progress_callback(pct, f"Analizando con IA… {completed}/{total_clusters} procesados")

    # 5. Unificar subtemas canónicos
    cluster_results = canonicalize_subtopics(cluster_results)

    # 6. Generación de Macro-temas específicos (sin 'Otros')
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
2. Si un subtema no se agrupa con los demás, asígnale un Tema propio de su sector (ejemplos: "Sector Salud", "Gremial y Hospitalario", "Gestión Institucional", "Infraestructura").

Devuelve exclusivamente un JSON objeto key-value."""

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

    # 7. Mapear a todas las filas en el orden original
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
