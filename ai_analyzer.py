import os
import re
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional, Callable
import pandas as pd
from openai import OpenAI
from rapidfuzz import fuzz

logger = logging.getLogger("ai_analyzer")

FORBIDDEN_TRAILING_WORDS = {
    "de", "del", "la", "el", "los", "las", "en", "para", "por", "con", "a", "al",
    "y", "o", "u", "e", "un", "una", "unos", "unas", "su", "sus", "sobre", "tras",
    "hacia", "desde", "sin", "que", "se"
}

def clean_subtema(text: str) -> str:
    """Asegura max 6 palabras, sin comas/puntos y sin conectores/preposiciones finales."""
    if not text:
        return "Mención General"
    # Eliminar signos de puntuación
    clean = re.sub(r'[,.;:!?¿¡"\'\(\)\[\]\{\}\-_/\\|]', ' ', str(text))
    words = [w for w in clean.split() if w]
    
    # Máximo 6 palabras
    if len(words) > 6:
        words = words[:6]
        
    # Eliminar palabras prohibidas al final (preposiciones, artículos, conjunciones)
    while words and words[-1].lower() in FORBIDDEN_TRAILING_WORDS:
        words.pop()
        
    res = " ".join(words).strip()
    return res.capitalize() if res else "Mención General"

def extract_brand_context(text: str, title: str, brand: str, aliases: List[str]) -> str:
    """Extrae localmente solo las oraciones donde se menciona a la marca o alias."""
    targets = [brand.lower()] + [a.lower().strip() for a in aliases if a.strip()]
    full_text = f"{title}. {text}".strip()
    
    # Separar en oraciones
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    matched_blocks = []
    
    for idx, sentence in enumerate(sentences):
        s_lower = sentence.lower()
        if any(re.search(rf"\b{re.escape(t)}\b", s_lower) for t in targets):
            prev_s = sentences[idx - 1] if idx > 0 else ""
            next_s = sentences[idx + 1] if idx + 1 < len(sentences) else ""
            block = f"{prev_s} {sentence} {next_s}".strip()
            if block not in matched_blocks:
                matched_blocks.append(block)
                
    if matched_blocks:
        return " [...] ".join(matched_blocks)[:650]
    
    # Si no hay coincidencia exacta de la marca, mandar titular y primer párrafo
    return full_text[:450]

def cluster_similar_rows(rows: List[dict], km: dict, threshold: int = 82) -> Dict[int, int]:
    """
    Agrupa filas por fecha y similitud de título/resumen.
    Retorna un diccionario {row_index: cluster_id}.
    """
    n = len(rows)
    cluster_map = {}
    clusters_rep = {}  # {cluster_id: normalized_title}
    current_cluster = 0
    
    for i in range(n):
        if rows[i].get("is_duplicate"):
            continue
        
        titulo_raw = str(rows[i].get(km.get("titulo", "Título"), "")).lower().strip()
        titulo_norm = re.sub(r"\W+", " ", titulo_raw)
        fecha_raw = str(rows[i].get(km.get("fecha", "Fecha"), ""))[:10]
        
        assigned = False
        # Comparar con clusters existentes (priorizando fecha cercana si existe)
        for cid, (rep_title, rep_fecha) in clusters_rep.items():
            if fecha_raw and rep_fecha and fecha_raw != rep_fecha:
                continue
            sim = fuzz.token_sort_ratio(titulo_norm, rep_title)
            if sim >= threshold:
                cluster_map[i] = cid
                assigned = True
                break
                
        if not assigned:
            cluster_map[i] = current_cluster
            clusters_rep[current_cluster] = (titulo_norm, fecha_raw)
            current_cluster += 1
            
    return cluster_map

def _call_openai_cluster(client: OpenAI, model: str, brand: str, aliases: List[str], ctx: str) -> Tuple[str, str]:
    """Llamada a la API para un cluster específico."""
    prompt = f"""Analiza la siguiente mención de noticias para el cliente: "{brand}" (Alias: {', '.join(aliases) if aliases else 'Ninguno'}).

Contexto relevante extraído:
\"\"\"{ctx}\"\"\"

Instrucciones estrictas:
1. "tono": Evalúa exclusivamente el impacto PARA EL CLIENTE/MARCA.
   - Positivo: Si exalta, favorece, destaca logros, convenios, vocería favorable o soluciones del cliente.
   - Negativo: Si critica, cuestiona, denuncia, sanciona o perjudica directamente la imagen del cliente.
   - Neutro: Mención informativa objetiva, datos generales, o si la noticia es trágica/negativa pero el cliente no tiene culpa y solo es citado de paso o como referente neutro.
   Valores permitidos: "Positivo", "Negativo", "Neutro".

2. "subtema": Especifica el hecho central relacionado con la marca:
   - Máximo 6 palabras.
   - Debe ser completo y coherente.
   - Prohibido usar comas, puntos o signos de puntuación.
   - Prohibido terminar en verbos, preposiciones (de, en, para, por, con, etc.) o artículos (el, la, los, un).

Responde únicamente un JSON con claves "tono" y "subtema"."""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Eres un analista experto en auditoría y monitoreo de medios de comunicación. Responde estrictamente en JSON válido."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=80
        )
        data = json.loads(resp.choices[0].message.content)
        tono_raw = str(data.get("tono", "Neutro")).strip().capitalize()
        tono = tono_raw if tono_raw in ["Positivo", "Negativo", "Neutro"] else "Neutro"
        subtema = clean_subtema(data.get("subtema", "Mención General"))
        return tono, subtema
    except Exception as e:
        logger.error(f"Error en llamada OpenAI: {e}")
        return "Neutro", "Mención General"

def enrich_rows_with_ai(
    rows: List[dict],
    km: dict,
    brand: str,
    aliases: List[str],
    api_key: str,
    model: str = "gpt-4.1-nano-2025-04-14",
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> List[dict]:
    """Enriquece las filas con Tono_IA, Tema_IA y Subtema_IA."""
    client = OpenAI(api_key=api_key)
    
    # 1. Agrupamiento semántico previo
    if progress_callback:
        progress_callback(72, "Identificando grupos de noticias similares (deduplicación semántica)…")
    cluster_map = cluster_similar_rows(rows, km)
    
    unique_clusters = sorted(set(cluster_map.values()))
    total_clusters = len(unique_clusters)
    
    # Seleccionar la fila más representativa de cada cluster
    cluster_to_sample_idx = {}
    for row_idx, cid in cluster_map.items():
        if cid not in cluster_to_sample_idx:
            cluster_to_sample_idx[cid] = row_idx
            
    cluster_results: Dict[int, Tuple[str, str]] = {}
    
    # 2. Análisis paralelo de clusters
    if progress_callback:
        progress_callback(75, f"Analizando {total_clusters} eventos únicos con IA ({model})…")
        
    completed = 0
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_cid = {}
        for cid, row_idx in cluster_to_sample_idx.items():
            row = rows[row_idx]
            ctx = extract_brand_context(
                str(row.get(km.get("resumen", "Resumen - Aclaracion"), "")),
                str(row.get(km.get("titulo", "Título"), "")),
                brand,
                aliases
            )
            fut = executor.submit(_call_openai_cluster, client, model, brand, aliases, ctx)
            future_to_cid[fut] = cid
            
        for fut in as_completed(future_to_cid):
            cid = future_to_cid[fut]
            tono, subtema = fut.result()
            cluster_results[cid] = (tono, subtema)
            completed += 1
            if progress_callback and (completed % 15 == 0 or completed == total_clusters):
                pct = 75 + int((completed / total_clusters) * 15)
                progress_callback(pct, f"Analizando con IA… {completed}/{total_clusters} eventos procesados")

    # 3. Macro-agrupación de Temas a partir de Subtemas únicos
    if progress_callback:
        progress_callback(91, "Agrupando subtemas en Temas macro…")
        
    all_subtemas = list({res[1] for res in cluster_results.values() if res[1]})
    temas_map = {}
    
    if all_subtemas:
        macro_prompt = f"""A continuación tienes la lista de subtemas únicos obtenidos sobre la marca "{brand}":
{json.dumps(all_subtemas, ensure_ascii=False)}

Tu tarea es agrupar estos subtemas bajo "Temas" macro coherentes (ejemplos: "Gestión Institucional", "Infraestructura", "Sostenibilidad", "Academia", "Seguridad").
- Cada Tema debe tener entre 1 y 4 palabras.
- Si algún subtema es muy específico o no encaja, asígnale un Tema propio representativo.

Devuelve un JSON plano donde cada clave sea el subtema exacto y el valor sea el Tema asignado. Ejemplo:
{{"Entrega de nuevas becas": "Educación y Becas", "Falla en plataforma": "Operaciones"}}"""

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Eres un categorizador taxonómico de noticias. Responde exclusivamente un JSON objeto key-value."},
                    {"role": "user", "content": macro_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            temas_map = json.loads(resp.choices[0].message.content)
        except Exception as e:
            logger.error(f"Error generando macro-temas: {e}")
            temas_map = {st: st for st in all_subtemas}

    # 4. Asignar los valores a las filas (conservando el orden exacto)
    for i, row in enumerate(rows):
        if row.get("is_duplicate"):
            row["Tono_IA"] = "Duplicada"
            row["Tema_IA"] = "-"
            row["Subtema_IA"] = "-"
            continue
            
        cid = cluster_map.get(i)
        if cid is not None and cid in cluster_results:
            tono, subtema = cluster_results[cid]
            tema = temas_map.get(subtema, subtema)
            row["Tono_IA"] = tono
            row["Tema_IA"] = tema
            row["Subtema_IA"] = subtema
        else:
            row["Tono_IA"] = "Neutro"
            row["Tema_IA"] = "General"
            row["Subtema_IA"] = "Mención General"
            
    return rows
