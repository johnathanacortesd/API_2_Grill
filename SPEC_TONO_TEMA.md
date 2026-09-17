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
| `catalogo_tono_tema.py` | **Rúbrica del motor**: `CRITERIOS_TONO`, `TONOS`, `REGLAS_SUBTEMA`, `EJEMPLOS`, `CUBO_PROHIBIDO`, `MIN_PAL`/`MAX_PAL`, taxonomías fijas. Fuente de verdad del criterio. |
| `ai_analyzer.py` | Legado. Solo se usan `extract_brand_context`, `generate_brand_variants`, `ensure_subtema_distinct_from_tema`. Su `enrich_rows_with_ai` ya **no se ejecuta**. |
| `pkl_classifier.py` | Clasificadores PKL del cliente (opcionales) que sobreescriben tono y/o tema. |
| `tests/` | 45 pruebas sin API (modelo simulado). |

**Punto de contacto único:** `pipeline.process_dossier` llama
`analyzer_tono_tema.enrich_rows_with_ai(...)` con `extra=ai_config`, y el resumen de auditoría sale
por `analyzer_tono_tema.ultimo_resumen()` en `resultado["analisis"]`.

## 3. Invariantes (no negociables)

- Las 4 columnas de análisis —`Contexto analizado`, `Tono_IA`, `Tema_IA`, `Subtema_IA`— se insertan
  **después de `revalorización` y antes de `resumen corto`** (`BASE_OUTPUT_COLUMNS`).
- Las filas duplicadas conservan `Tono_IA = "Duplicada"` y `Tema_IA = Subtema_IA = "-"`.
- Firma de `enrich_rows_with_ai` y de `process_dossier`: no cambian (los llamadores no se tocan).
- El motor **no** debe depender del paquete `openai` para arrancar: hace HTTP con `requests`.
- Sin refactors, sin renombres, sin archivos nuevos fuera de esta lista.

## 4. Las cinco piezas del motor (quitar una = volver al prompt suelto)

1. **Agrupación previa** — `construir_grupos`: se etiqueta por grupo de notas equivalentes, nunca
   fila por fila (rapidfuzz sobre titulares + 5-gramas del cuerpo).
2. **Sub-tema primero, después el tono** — `prompt_lote` + `etiquar_grupos`; los sub-temas ya usados
   viajan en cada lote como **CANDIDATOS** y `canonizar_subtemas` unifica variantes.
3. **Validador duro + reparación** — `validar` (3-7 palabras, sin verbo conjugado inicial, sin
   preposición final, sin rótulos vacíos, sin `:` `;` `|`) y `prompt_reparacion` en ciclo contra el
   propio modelo.
4. **Tema por reglas sobre lista cerrada** — `derivar_reglas` + `asignar_temas`; el modelo solo elige
   dentro de la lista o propone un cubo nuevo específico (`prompt_cubos`).
5. **Nunca "Otros"** — `CUBO_PROHIBIDO`, `cubo_valido`, `_cubo_mas_cercano`.

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
- Criterio de tono y lista de Temas se eligen en la interfaz (`criterio`, `taxonomia`); la lista de
  Temas se puede generar del archivo y **descargar en JSON** para reutilizarla el mes siguiente del
  mismo cliente (si cambia, el cruce en Power BI se rompe).

## 6. Criterios de aceptación

```bash
python -m unittest discover -s tests          # 45 pruebas, todas OK, sin API
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

`python -m unittest discover -s tests` en `main` ya traía **2 fallos** en
`tests/test_link_export_style.py` (estilo de `Link Nota` / `Streaming`). No provienen de este motor:
existen igual en `main` y se mantienen idénticos. Todo lo demás (125 pruebas) pasa.
