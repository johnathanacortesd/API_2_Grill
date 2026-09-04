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

# Conectores y artículos a eliminar de los extremos
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
    """
    Normaliza texto para comparación:
    - Quita tildes, signos y stopwords.
    - Reduce plurales comunes a singular ('labores' -> 'labor', 'rescates' -> 'rescate').
    """
    if not text:
        return ""
    t = unidecode(str(text).lower().strip())
    words = re.findall(r"\b[a-z0-9]+\b", t)
    
    stemmed = []
    for w in words:
        if w in STOPWORDS_ES or len(w) < 2:
            continue
        # Despluralización básica en español
        if w.endswith("ces") and len(w) > 4:
            w = w[:-3] + "z"
        elif w.endswith("es") and len(w) > 4:
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("is") and len(w) > 3:
            w = w[:-1]
        stemmed.append(w)
        
    return " ".join(stemmed)

def clean_subtema(text: str, brand: str = "") -> str:
    """Valida que el subtema no sea genérico, tenga max 6 palabras y sin puntuación."""
    if not text:
        return "Hecho Informativo"
        
    clean = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', str(text))
    words = [w for w in clean.split() if w]
    
    if len(words) > 6:
        words = words[:6]
        
    while words and words[-1].lower() in FORBIDDEN_TRAILING_WORDS:
        words.pop()
        
    res = " ".join(words).strip()
    
    # Si la IA aún intenta poner "Mención de...", limpiarlo
    res_lower = res.lower()
    bad_prefixes = ["mencion de", "mencion a", "mencion en", "presencia de", "declaraciones de", "noticia sobre"]
    for bp in bad_prefixes:
        if res_lower.startswith(bp):
            trimmed = res[len(bp):].strip()
            if trimmed:
                res = trimmed
            break
            
    return res.capitalize() if res else "Hecho Informativo"

def extract_brand_context(text: str, title: str, brand: str, aliases: List[str]) -> str:
    """Extrae las oraciones clave alrededor de la marca y sus alias."""
    targets = [brand.lower()] + [a.lower().strip() for a in aliases if a.strip()]
    full_text = f"{title}. {text}".strip()
    
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
        return " [...] ".join(matched_blocks)[:650]
        
    return full_text[:450]

def cluster_similar_rows(rows: List[dict], km: dict) -> Dict[int, int]:
    """
    Agrupa filas que hablen del mismo hecho por similitud profunda:
    1. Título normalizado con singularización (ej: 'labor de rescate' == 'labores de rescate').
    2. Resumen normalizado (detecta cables y notas replicadas aunque el medio cambie el titular).
    """
    n = len(rows)
    cluster_map = {}
    clusters_rep = {}  # {cid: {"title_norm": str, "body_norm": str}}
    current_cluster = 0
    
    for i in range(n):
        if rows[i].get("is_duplicate"):
            continue
            
        t_raw = str(rows[i].get(km.get("titulo", "Título"), ""))
        r_raw = str(rows[i].get(km.get("resumen", "Resumen - Aclaracion"), ""))
        
        t_norm = normalize_text_for_matching(t_raw)
        r_norm = normalize_text_for_matching(r_raw[:350])  # Primeros caracteres del resumen
        
        assigned = False
        for cid, rep in clusters_rep.items():
            rep_t = rep["title_norm"]
            rep_r = rep["body_norm"]
            
            # Coincidencia 1: Títulos idénticos o casi idénticos
            if t_norm and rep_t:
                if t_norm == rep_t:
                    cluster_map[i] = cid
                    assigned = True
                    break
                sim_title = fuzz.token_set_ratio(t_norm, rep_t)
                if sim_title >= 84:
                    cluster_map[i] = cid
                    assigned = True
                    break
            
            # Coincidencia 2: Resúmenes iguales o muy similares (cables de noticias)
            if r_norm and rep_r and len(r_norm) > 40 and len(rep_r) > 40:
                sim_resumen = fuzz.token_set_ratio(r_norm, rep_r)
                if sim_resumen >= 86:
                    cluster_map[i] = cid
                    assigned = True
                    break
                    
        if not assigned:
            cluster_map[i] = current_cluster
            clusters_rep[current_cluster] = {"title_norm": t_norm, "body_norm": r_norm}
            current_cluster += 1
            
    return cluster_map

def canonicalize_subtopics(cluster_results: Dict[int, Tuple[str, str]]) -> Dict[int, Tuple[str, str]]:
    """
    Post-procesador: Si dos clusters distintos produjeron subtemas casi idénticos
    (ej: 'Labor de rescate en cali' y 'Labores de rescate en cali'), los unifica al más repetido.
    """
    subtemas_list = [sub for _, sub in cluster_results.values() if sub]
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
                # Conservar el que más menciones tenga
                chosen = s1 if counts[s1] >= counts[s2] else s2
                mapping[s1] = chosen
                mapping[s2] = chosen

    final_results = {}
    for cid, (tono, sub) in cluster_results.items():
        canonical_sub = mapping.get(sub, sub)
        final_results[cid] = (tono, canonical_sub)
        
    return final_results

def _call_openai_cluster(client: OpenAI, model: str, brand: str, aliases: List[str], ctx: str) -> Tuple[str, str]:
    """Clasificación enfocada en hechos específicos, sin frases tautológicas."""
    prompt = f"""Eres un auditor senior de monitoreo de medios. Analiza esta mención referente al cliente: "{brand}" (y sus alias: {', '.join(aliases) if aliases else 'Ninguno'}).

Contexto analizado:
\"\"\"{ctx}\"\"\"

Instrucciones estrictas:
1. "tono": Evalúa el impacto reputacional directo PARA EL CLIENTE ("{brand}"):
   - "Positivo": Se exalta su gestión, logros, vocería, convenios, reconocimientos o soluciones.
   - "Negativo": Se cuestiona, critica, denuncia, sanciona o perjudica directamente su imagen.
   - "Neutro": Es una mención informativa, cita técnica o referencia objetiva. (Si la noticia relata un hecho trágico o problema pero el cliente no es el culpable ni causante, es "Neutro").
   Valores válidos: "Positivo", "Negativo", "Neutro".

2. "subtema": Especifica el HECHO, ACCIÓN O SUCESO CONCRETO de la noticia:
   - Máximo 6 palabras.
   - Debe ser completo y coherente.
   - PROHIBIDO usar signos de puntuación, comas o puntos.
   - PROHIBIDO terminar en preposiciones (de, en, para, por, con), artículos o verbos.
   - PROHIBIDO ROTUNDAMENTE usar frases genéricas o vacías como: "Mención de {brand}", "Presencia de {brand}", "Mención general", "Noticia sobre {brand}". Identifica el tema de fondo (ejemplo: "Acreditación de nuevo programa de medicina", "Labores de rescate en Cali", "Pronunciamiento sobre paro de transporte", "Incautación de contrabando").

Devuelve únicamente un JSON: {{"tono": "...", "subtema": "..."}}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Experto en taxonomía y reputación en medios de comunicación. Responde estrictamente en JSON válido."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=80
        )
        data = json.loads(resp.choices[0].message.content)
        tono_raw = str(data.get("tono", "Neutro")).strip().capitalize()
        tono = tono_raw if tono_raw in ["Positivo", "Negativo", "Neutro"] else "Neutro"
        subtema = clean_subtema(data.get("subtema", "Hecho Informativo"), brand)
        return tono, subtema
    except Exception as e:
        logger.error(f"Error en llamada OpenAI: {e}")
        return "Neutro", "Hecho Informativo"

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
    
    # 1. Extraer y guardar la columna 'Contexto analizado' en cada fila
    if progress_callback:
        progress_callback(71, "Extrayendo contexto de la marca para auditoría…")
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

    # 2. Agrupamiento semántico profundo
    if progress_callback:
        progress_callback(74, "Agrupando noticias similares (deduplicación semántica)…")
    cluster_map = cluster_similar_rows(rows, km)
    
    unique_clusters = sorted(set(cluster_map.values()))
    total_clusters = len(unique_clusters)
    
    cluster_to_sample_idx = {}
    for row_idx, cid in cluster_map.items():
        if cid not in cluster_to_sample_idx:
            cluster_to_sample_idx[cid] = row_idx
            
    cluster_results: Dict[int, Tuple[str, str]] = {}
    
    # 3. Clasificación con modelo concurrente
    if progress_callback:
        progress_callback(77, f"Analizando {total_clusters} grupos con {model}…")
        
    completed = 0
    with ThreadPoolExecutor(max_workers=14) as executor:
        future_to_cid = {}
        for cid, row_idx in cluster_to_sample_idx.items():
            ctx = rows[row_idx]["Contexto analizado"]
            fut = executor.submit(_call_openai_cluster, client, model, brand, aliases, ctx)
            future_to_cid[fut] = cid
            
        for fut in as_completed(future_to_cid):
            cid = future_to_cid[fut]
            tono, subtema = fut.result()
            cluster_results[cid] = (tono, subtema)
            completed += 1
            if progress_callback and (completed % 15 == 0 or completed == total_clusters):
                pct = 77 + int((completed / total_clusters) * 14)
                progress_callback(pct, f"Analizando con IA… {completed}/{total_clusters} procesados")

    # 4. Unificación canónica de subtemas levemente diferentes
    cluster_results = canonicalize_subtopics(cluster_results)

    # 5. Generación de Macro-temas específicos (PROHIBIDO 'Otros' o 'General')
    if progress_callback:
        progress_callback(92, "Consolidando Temas macro específicos…")
        
    all_subtemas = list({res[1] for res in cluster_results.values() if res[1]})
    temas_map = {}
    
    if all_subtemas:
        macro_prompt = f"""Aquí tienes la lista de subtemas extraídos de noticias sobre "{brand}":
{json.dumps(all_subtemas, ensure_ascii=False)}

Tu tarea es categorizar cada subtema bajo un "Tema" macro formal y profesional (de 1 a 4 palabras).
Reglas indispensables:
1. ESTÁ PROHIBIDO devolver categorías como: "Otros", "General", "Varios", "Miscelánea", "Sin agrupar" o "Sin clasificar".
2. Si un subtema es aislado o no se puede agrupar con los demás, asígnale un Tema propio representativo de su sector (ejemplos: "Seguridad Ciudadana", "Educación Superior", "Gestión Institucional", "Operaciones de Emergencia", "Medio Ambiente").

Devuelve exclusivamente un JSON objeto key-value donde cada clave sea el subtema exacto y su valor sea el Tema asignado. Ejemplo:
{{"Labores de rescate en Cali": "Gestión de Emergencias", "Acreditación de medicina": "Academia y Acreditación"}}"""

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
            temas_map = json.loads(resp.choices[0].message.content)
        except Exception as e:
            logger.error(f"Error generando macro-temas: {e}")
            temas_map = {st: st for st in all_subtemas}

    # 6. Mapear a todas las filas en el orden original
    for i, row in enumerate(rows):
        if row.get("is_duplicate"):
            row["Tono_IA"] = "Duplicada"
            row["Tema_IA"] = "-"
            row["Subtema_IA"] = "-"
            continue
            
        cid = cluster_map.get(i)
        if cid is not None and cid in cluster_results:
            tono, subtema = cluster_results[cid]
            tema_asignado = temas_map.get(subtema, subtema)
            # Salvaguarda final contra 'Otros'
            if str(tema_asignado).lower() in ["otros", "otro", "general", "varios", "sin clasificar"]:
                tema_asignado = subtema
            row["Tono_IA"] = tono
            row["Tema_IA"] = tema_asignado
            row["Subtema_IA"] = subtema
        else:
            row["Tono_IA"] = "Neutro"
            row["Tema_IA"] = "Hecho Noticioso"
            row["Subtema_IA"] = "Hecho Informativo"
            
    return rows
