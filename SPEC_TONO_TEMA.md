# SPEC — Motor de Tono, Tema y Sub-tema en API-3-Grill

Este archivo es el contrato del repo. Cualquier persona o agente que toque el análisis debe leerlo
**antes** de editar. Si algo no está aquí, se pregunta; no se infiere.

---

## 1. Objetivo

Reemplazar **solo** el análisis de Tono, Tema y Sub-tema por el motor de `analyzer_tono_tema.py`,
usando OpenAI `gpt-4.1-nano-2025-04-14` con la key de `st.secrets["OPENAI_API_KEY"]`.
La limpieza, la normalización, la expansión de menciones, la deduplicación y el formato de salida
quedan **iguales**: son las mismas de Grill-API.

## 2. Mapa del repo

| Archivo | Rol |
|---|---|
| `app.py` | Interfaz Streamlit. Tema claro/oscuro, `APP_PASSWORD`, panel de progreso, descarga. |
| `pipeline.py` | **Limpieza y estructuración (NO TOCAR)** + el hook al motor (`process_dossier`). |
| `analyzer_tono_tema.py` | **Motor nuevo** de Tono/Tema/Sub-tema. |
| `catalogo_tono_tema.py` | **Rúbrica del motor**: `CRITERIOS_TONO`, `TONOS`, `REGLAS_SUBTEMA`, `EJEMPLOS`, `CUBO_PROHIBIDO`, `MIN_PAL`/`MAX_PAL`. Las taxonomías nombradas son solo **nombres candidatos** opcionales; el tema del lote no se clasifica contra una lista cerrada. |
| `ai_analyzer.py` | Legado. Solo se usan `extract_brand_context`, `generate_brand_variants`, `ensure_subtema_distinct_from_tema`. Su `enrich_rows_with_ai` ya **no se ejecuta**. |
| `pkl_classifier.py` | Clasificadores PKL del cliente (opcionales) que sobreescriben tono y/o tema. Tras PKL se reafirma: un subtema ⇒ un tema. |
| `tests/` | Pruebas sin API de clustering, canonización, un tema por subtema, tema ≠ subtema y etiquetado solo-del-lote. |

**Punto de contacto único:** `pipeline.process_dossier` llama
`analyzer_tono_tema.enrich_rows_with_ai(...)` con `extra=ai_config`, y el resumen de auditoría sale
por `analyzer_tono_tema.ultimo_resumen()` en `resultado["analisis"]`.

## 3. Invariantes (no negociables)

- Las 4 columnas de análisis —`Contexto analizado`, `Tono_IA`, `Tema_IA`, `Subtema_IA`— se insertan
  **después de `revalorización` y antes de `resumen corto`** (`BASE_OUTPUT_COLUMNS`).
- Las filas duplicadas conservan `Tono_IA = "Duplicada"` y `Tema_IA = Subtema_IA = "-"`.
- **Tema nunca en blanco ni copia del titular.** `Tema_IA` de cada fila no duplicada es una
  frase nominal española no vacía (no `null`, no `""`, no solo espacios) y **nunca** un
  recorte/`title[:N]` ni un prefijo de las primeras palabras del titular. Rechazo del quality
  gate ≠ vacío: se repara o se usa un fallback temático derivado del **significado** (subtema +
  título como evidencia de stems). Preferir una frase mediocre precisa a una celda vacía.
- Firma de `enrich_rows_with_ai` y de `process_dossier`: no cambian (los llamadores no se tocan).
- El motor **no** debe depender del paquete `openai` para arrancar: hace HTTP con `requests`.
- Sin refactors, sin renombres, sin archivos nuevos fuera de esta lista.

## 4. Las seis piezas del motor (quitar una = volver al prompt suelto)

1. **Agrupación previa** — `construir_grupos`: se etiqueta por grupo de notas equivalentes, nunca
   fila por fila (rapidfuzz sobre titulares + 5-gramas del cuerpo).
2. **Sub-tema primero, después el tono** — `prompt_lote` + `etiquetar_grupos`; los sub-temas ya usados
   viajan en cada lote como **CANDIDATOS** y `canonizar_subtemas` unifica variantes del mismo hecho.
   `unificar_subtemas_noticias_similares` cubre el caso en que dos grupos siguen separados pero
   son la misma noticia.
3. **Validador duro + reparación** — `validar` (3-7 palabras, sin verbo conjugado inicial, sin
   preposición final, sin rótulos vacíos, sin `:` `;` `|`) y `prompt_reparacion` en ciclo contra el
   propio modelo.
4. **Tema bottom-up de ESTE LOTE** — `asignar_temas` agrupa subtemas canónicos afines y nombra cada
   familia **una sola vez**. El nombre es una **frase nominal temática COMPLETA** (la que un
   analista pondría en Power BI): LLM con few-shot buenos/malos → gate duro → una reparación →
   frase segura. Prohibido bag-of-words, unir stems y recortes que suelten el núcleo o el objeto.
   No hay lista cerrada ni memoria entre corridas. Un subtema canónico implica exactamente un tema.
   `volcar_analisis_en_filas` no reasigna por fila. Si el gate rechaza, **no se descarta a vacío**.
5. **Guarda de generalidad y de lengua** — el tema es más general que el subtema
   (`_tema_distinto_de_subtema`) y pasa `problemas_calidad_tema` / `tema_frase_natural`:
   frase completa, no verbo/cláusula, no PP truncado, no solo adjetivos, no persona, no sigla
   suelta, no rótulo vacío, **no copia ni prefijo del titular**. El subtema sigue siendo el
   hecho concreto.
6. **Nunca "Otros"** — `CUBO_PROHIBIDO`, `cubo_valido`. Jev (TypeSafe) es opcional y **solo**
   corrige temas mal clasificados (boolean/choice, alta confianza). No genera subtemas ni reemplaza
   el pipeline de tono.

Estabilizadores porque el modelo es pequeño (sesgo sistemático, no ruido):

- **Votación de tono** — `_voto_mayoria` (N veces por grupo, empate → Neutro).
- **Guarda determinista** — `aplicar_guarda_tono`: sin señalamiento **dirigido** (el blanco a ≤35
  caracteres del verbo de crítica) no hay Negativo. El tema trágico no hace negativo al cliente.

## 5. Modelo y credenciales

- `model` = `gpt-4.1-nano-2025-04-14` (`MODELO_DEFECTO`), `base_url` = `https://api.openai.com/v1`.
- **Key**: `st.secrets["OPENAI_API_KEY"]` (Streamlit Cloud → Settings → Secrets; local:
  `.streamlit/secrets.toml`).
- Secrets necesarios: `APP_PASSWORD`, `REGIONES_CSV_URL`, `INTERNET_CSV_URL`, `OPENAI_API_KEY`.
- Si falta `OPENAI_API_KEY` y la IA está activada, la app **avisa**; no cae en silencio a heurística.
- Criterio de tono se elige en la interfaz (`criterio`). Los Temas se arman bottom-up en el lote
  del día a partir de los subtemas, como frases nominales **completas** (no uniones de keywords
  ni recortes). No hay vocabulario persistente entre corridas. Una lista JSON o una taxonomía
  nombrada, si se carga, solo aporta **nombres candidatos** para esas familias.

## 6. Criterios de aceptación

```bash
python -m unittest discover -s tests          # invariantes de tema/subtema, sin API
python -m compileall -q app.py pipeline.py analyzer_tono_tema.py catalogo_tono_tema.py
```

Además, tras CUALQUIER edición de bloques portados de otra app:

- **Símbolos globales indefinidos** (`py_compile` NO los detecta): recorrer `symtable.symtable(...)`
  y listar los globales referenciados que no sean definición, import ni builtin.
- **Definiciones de nivel superior** contra la versión anterior
  (`git show HEAD:<archivo>`): que no haya desaparecido ninguna (reescribir `main()` de punta a punta
  borra funciones que quedaban debajo y el archivo igual compila).
- Arranque real: `streamlit run app.py` y comprobar que la página responde sin traceback.

## 7. Prohibiciones

- No tocar la limpieza, la deduplicación ni el orden de columnas del export.
- No entregar cubos genéricos ni "Otros".
- No agregar dependencias (el motor necesita `requests` y `numpy`; ya están declaradas).
- No declarar éxito sin pegar el output real de los comandos de la sección 6.

## 8. Definición de hecho

XLSX de salida con la estructura de Grill-API intacta, las 4 columnas de análisis en su posición,
etiquetas uniformes por grupo y el output literal de la sección 6.

## 9. Convivencia de motores (importante)

Este repo tiene **dos motores de análisis**, y el port solo cambia el de Grill:

| Camino | Motor | Agrupador |
|---|---|---|
| `app.py` → `pipeline.process_dossier` | `analyzer_tono_tema.py` (nuevo) | `analyzer_tono_tema.construir_grupos` |
| `app_sucre.py` → `sucre_pipeline.process_sucre_dossier` | `ai_analyzer.enrich_rows_with_ai` (legado) | `ai_analyzer.cluster_similar_rows` |
| Modelos PKL del cliente | tono/tema por PKL, subtema intacto | `pkl_classifier` (+ `ai_analyzer`) |

`ai_analyzer.py` **no se toca**: sigue alimentando `Contexto analizado`, la variante Sucre y el
camino PKL. Si algún día se unifica el motor, hay que migrar Sucre en el mismo cambio; no antes.

## 10. Estado conocido de las pruebas

`python -m unittest discover -s tests` cubre las invariantes de tema/subtema del lote
(clustering, canonización, un tema por subtema, tema ≠ subtema, sin memoria entre corridas).
No hay llamadas a API.
