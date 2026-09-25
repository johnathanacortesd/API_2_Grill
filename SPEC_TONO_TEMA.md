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
| `perfil_cliente.py` | **Perfiles de cliente (multicliente)**: CRUD de JSON en `clientes/` con marca, alias, voceros, criterio de tono (catálogo o texto libre `criterio_custom`) y lista fija de Temas opcional. |
| `historial_cliente.py` | Historial de corridas por cliente (JSONL). `pipeline` lo alimenta, `app.py` lo muestra. |
| `clientes/` | Perfiles de ejemplo (`universidad_simon_bolivar.json`, `fenavi.json`). |
| `catalogo_tono_tema.py` | **Rúbrica del motor**: `CRITERIOS_TONO`, `TONOS`, `REGLAS_SUBTEMA`, `EJEMPLOS`, `CUBO_PROHIBIDO`, `MIN_PAL`/`MAX_PAL`. Las taxonomías nombradas son solo **nombres candidatos** opcionales; el tema del lote no se clasifica contra una lista cerrada. |
| `ai_analyzer.py` | Legado. Solo se usan `extract_brand_context`, `generate_brand_variants`, `ensure_subtema_distinct_from_tema`. Su `enrich_rows_with_ai` ya **no se ejecuta**. |
| `pkl_classifier.py` | Clasificadores PKL del cliente (opcionales) que **ganan** sobre el motor de lote/LLM en tono y/o tema. El quality-gate de frases del lote **no** reescribe las clases del PKL. El subtema nunca usa PKL. |
| `tests/` | Pruebas sin API de clustering, canonización, un tema por subtema, tema ≠ subtema, etiquetado solo-del-lote y camino PKL (el predict se invoca y gana). |

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
4. **Tema bottom-up de ESTE LOTE** — `asignar_temas` agrupa subtemas canónicos afines
   (`cluster_familias_subtema`: puentes solo con stems distintivos —descarta palabras
   genéricas de evento como `congreso`/`internacional` y stems omnipresentes en el lote—,
   equivalencias `criminalidad`/`criminología`→`crimen`, `juvenil`/`juventud`/`jóvenes`→`joven`,
   y anti-encadenamiento: un puente de titular solo une si toca el núcleo temático)
   y nombra cada familia **una sola vez**. El nombre es una **frase nominal temática COMPLETA** (la que un
   analista pondría en Power BI): LLM con few-shot buenos/malos → gate duro → una reparación →
   frase segura. Prohibido bag-of-words, unir stems y recortes que suelten el núcleo o el objeto.
   No hay lista cerrada ni memoria entre corridas. Un subtema canónico implica exactamente un tema.
   `volcar_analisis_en_filas` no reasigna por fila. Si el gate rechaza, **no se descarta a vacío**.
   **Excepción PKL:** si hay `theme_model`, se **omite** `asignar_temas` / Jev y `Tema_IA` son las
   clases de ese modelo. El gate de frases (`tema_frase_natural`, `forzar_un_tema_por_subtema`)
   no las sustituye. Si hay `tone_model`, `Tono_IA` son las clases de ese modelo (sin guarda LLM).
5. **Guarda de generalidad y de lengua** — el tema es más general que el subtema
   (`_tema_distinto_de_subtema`) y pasa `problemas_calidad_tema` / `tema_frase_natural`:
   frase completa, no verbo/cláusula, no PP truncado (ni **inicio** con preposición:
   `Para Carnaval 2027` → `Carnaval 2027`, con reparación determinista
   `_reparar_inicio_preposicional` que solo ignora los flags cosméticos
   `mash_keywords`/`empieza_por_adjetivo`), no solo adjetivos, no persona, no sigla
   suelta, no rótulo vacío, **no copia ni prefijo del titular** (sustantivo + año como
   `Carnaval 2027` ya no cuenta como `mash_keywords`). El subtema sigue siendo el
   hecho concreto.
6. **Calidad del subtema** — `validar` detecta `copia_titular` (el subtema repite el
   titular en el mismo orden de palabras, sin reformular; los nombres de evento como
   `Cumbre internacional de criminología` están exentos) y `subtema_vago`
   (solo evento genérico + sujeto genérico, sin objeto: `Reunión de expertos en crimen`).
   El prompt de reparación exige reformular el primero y concretar el segundo.
7. **Nunca "Otros"** — `CUBO_PROHIBIDO`, `cubo_valido`. Jev (TypeSafe) es opcional y **solo**
   audita temas ya nombrados (boolean/choice, alta confianza): nunca genera subtemas ni
   decide el tono. El tono siempre sale de la API de OpenAI + las guardas deterministas,
   que codifican el criterio del cliente (crítica dirigida, vocero experto, tragedia).
   Sin `TYPESAFE_API_KEY` el pipeline funciona igual; la app lo avisa sin bloquear.

Estilo de los temas ("estilo Muse", v4.1): el prompt de nombrado (`sys_tema`,
`prompt_temas_familias`, `prompt_reparacion_tema`) pide español natural, sobrio y preciso —
lo concreto antes que lo abstracto ("Empleo juvenil", no "Fortalecimiento de la
empleabilidad juvenil")— y `TEMAS_EJEMPLO_BUENOS` se curó con 16 ejemplos impecables
(todos pasan `tema_frase_natural`, verificado por test). El gate también se calibró:
sustantivos como "carnaval"/"festival" ya no se confunden con adjetivos, y
sustantivo + adjetivos ("Movilidad urbana sostenible") no cuenta como `mash_keywords`.

Estabilizadores porque el modelo es pequeño (sesgo sistemático, no ruido):

- **Votación de tono** — `_voto_mayoria` (N veces por grupo, empate → Neutro).
- **Guarda determinista** — `aplicar_guarda_tono`: sin señalamiento **dirigido** (el blanco a ≤35
  caracteres del verbo de crítica) no hay Negativo. El tema trágico no hace negativo al cliente.
- **Guarda positiva de vocero** — `aplicar_guarda_positiva`: si la marca o un vocero del
  perfil aparece citado como fuente experta (verbo de habla: `dijo`, `afirmó`, `señaló`,
  `advirtió`, `consideró`…; `reveló` está excluido porque introduce hallazgos) y no hay
  señalamiento ni petición, un Neutro sube a Positivo.
- **Un hecho, un tono** — `unificar_tono_mismo_hecho`: los grupos con el mismo subtema
  canónico votan su tono (empate → Neutro); si alguno es Negativo no se toca el grupo
  (no se borra un posible señalamiento).
- **Tragedia con experto de la casa** — `aplicar_regla_tragedia` (+ excepción en
  `aplicar_guarda_positiva` y línea en el criterio `Aspectual estricto`): si la nota es
  una tragedia (muerte) y el experto/docente de la marca solo aparece citado como
  fuente, el tono es Neutro aunque sea voz experta. No aplica si la marca actúa frente
  al problema (ayuda, dona, organiza, propone): eso sigue Positivo. No toca Negativos.

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
  ni recortes), **salvo** si el cliente subió un PKL de tema: entonces las clases de ese modelo
  son `Tema_IA`. Si subió un PKL de tono, esas clases son `Tono_IA`. No hay vocabulario persistente
  entre corridas. Una lista JSON o una taxonomía nombrada, si se carga, solo aporta **nombres
  candidatos** para esas familias cuando no hay PKL de tema.

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
| Modelos PKL del cliente | tono/tema por PKL (autoridad), subtema intacto | `pkl_classifier` + `aplicar_pkl_del_cliente` |

`ai_analyzer.py` **no se toca**: sigue alimentando `Contexto analizado`, la variante Sucre y el
camino PKL. Si algún día se unifica el motor, hay que migrar Sucre en el mismo cambio; no antes.

## 11. Perfiles de cliente (multicliente)

El análisis se adapta por cliente sin tocar código, mediante `perfil_cliente.py`:

- Cada perfil (`clientes/<slug>.json`) guarda: `brand`, `aliases`, `voceros`,
  `criterio` (clave de `CRITERIOS_TONO`), `criterio_custom` (texto libre que, si
  existe, **reemplaza** la regla del catálogo en `prompt_sistema` vía
  `cfg['criterio_texto']`) y `taxonomia` opcional (`{"temas": [...]}`).
- En `app.py`, el selector "Perfil de cliente" precarga los campos del formulario;
  el usuario puede editarlos antes de procesar. Desde "Ajustes finos" puede guardar
  la configuración actual como perfil nuevo. Si el perfil ya existe, el guardado se
  detiene con un aviso salvo que se marque "Sobrescribir".
- El modo manual (digitar marca, alias y voceros en cada corrida) sigue siendo el
  valor por defecto del selector: los perfiles son un atajo opcional, no un requisito.
- Precedencia de la lista de Temas: JSON subido en el formulario > opción elegida
  en "Lista de Temas" > `taxonomia` del perfil > automática bottom-up del lote.
- `historial_cliente.py` registra cada corrida por marca (JSONL en `HISTORIAL_DIR`
  o `./historial/`); es best-effort y nunca interrumpe el pipeline.
- `auditoria_mail.py` envía un correo de auditoría de uso tras cada corrida con IA
  (`enviar_auditoria_desde_resultado`, invocado desde `pipeline.process_dossier`):
  marca/cliente, alias, voceros, criterio, filas, tonos y duración, para saber qué
  clientes consumen la API. Lee `SMTP_HOST/PORT/USER/PASSWORD/FROM` y
  `USAGE_NOTIFY_EMAIL` de los Secrets (fallback a entorno); best-effort, nunca
  interrumpe. Con Gmail, `SMTP_PASSWORD` debe ser una contraseña de aplicación.
- Tema visual v4.5: oscuro forzado con `color-scheme: dark` (no depende del tema
  claro/oscuro del sistema del usuario); fondo negro `#000000`, tarjetas `#0b0b0c`,
  acento rojo-naranja `#ff4d1c` (botón primario, progreso, estados activos,
  insignia IA, enlaces); sin degradados, glows ni animaciones decorativas. Vista
  previa estática en `tema_muse_preview.html`.
- Sin agrupamiento forzado (`asignar_temas`): el tema de la familia se asigna por
  miembro con `_tema_relevante_para_miembro` (comparación exacta sobre tokens
  canonizados y distintivos: 'suicidología' canoniza a 'suicidio'). El miembro
  cuyo subtema/evidencia no toca el léxico distintivo del tema se nombra como
  singleton con tema propio desde su subtema/titular/contexto (origen
  `tema_propio_sin_agrupar`). Nunca se conserva el tema familiar "porque la
  mayoría manda": cada noticia recibe el tema que la describe. Evita casos
  como "Prevención del suicidio" en "Ascenso político de Gutiérrez" o en las
  noticias del Foro de periodismo científico.
- Clustering exacto sobre stems canonizados (v4.6, `cluster_familias_subtema`):
  `_EQUIV_STEMS` une familias morfológicas ('suicidología'→'suicidio',
  'criminalidad'/'criminología'→'crimen', 'educativo'→'educación',
  'juvenil'/'juventud'→'joven') ANTES de comparar; la unión exige igualdad
  exacta. Regla A1: mismo hecho o 2+ stems distintivos en los núcleos; A2: un
  solo stem en ambos núcleos solo si es raro en el lote (df_nucleo<=3) y no
  demográfico ('joven' no une suicidio juvenil con desempleo juvenil); B: 2+
  stems distintivos en la evidencia (subtema + títulos). La afinidad por
  prefijo se eliminó: generaba uniones sorpresa ('empleo'~'desempleo',
  'medica'~'medicina', 'periodistico'~'periodismo') que encadenaban asuntos
  distintos en una sola familia gigante. Corrige el caso del dossier donde
  "Congreso iberoamericano de suicidología" quedaba fuera de "Prevención del
  suicidio" y el caso grave donde tres noticias del Foro de periodismo
  científico heredaban el tema "Prevención del suicidio".
- Stems de entidad excluidos del clustering (`_stems_entidad`): marca, alias y
  voceros no unen familias por sí solos (evita unir asuntos distintos solo
  porque todos mencionan la marca).
- Términos genéricos que no unen por sí solos (`MODIFICADOR_GENERICO_NO_UNE`:
  prevención, desafío, impacto, debate, frente…; `ATRIBUTO_DEMOGRAFICO`: joven,
  mujer, adolescente…): no unen suicidio juvenil con desempleo juvenil ni
  criminología con juventud.
- Post-pase `fusionar_temas_casi_identicos()`: tras `forzar_un_tema_por_subtema`,
  fusiona nombres de tema casi idénticos (similitud ≥ 0.72 y mismo nucleo de
  stems tras quitar genéricos), p. ej. "Impacto de educación" ~ "Impacto de
  inteligencia artificial en educación". Es una renombradura, no una
  reasignación: no toca los subtemas.
- UX de procesamiento: al enviar el formulario se marca `st.session_state["procesando"]`
  y toda la vista (encabezado + configuración + formulario + pie) vive dentro de un
  contenedor raíz `ui = st.empty()` que queda vacío durante el proceso, de modo que
  solo se ve la tarjeta "Procesando dossier de noticias"; al terminar o fallar se
  restablece con try/finally.
- Variable de entorno opcional `CLIENTES_DIR` para mover la carpeta de perfiles.

## 13. v4.7: mismo hecho por contexto + tono hacia la marca

**Agrupación por `Contexto analizado` (`construir_grupos`, `_contextos_mismo_hecho`):**
la similitud de noticias ya no depende solo de título/cuerpo. Cada fila calcula
5-gramas ordenados de su `Contexto analizado`; dos filas se unen si comparten
>= 6 5-gramas y el solapamiento sobre el contexto menor es >= 0,55. Detecta el
mismo hecho con titulares y cuerpos redactados distinto (p. ej. dos medios que
citan la misma declaración). No agrupa por keywords sueltas: exige secuencias
ordenadas compartidas.

**Consistencia por contexto:** `unificar_subtemas_noticias_similares` y
`unificar_tono_mismo_hecho` también unen por contexto de marca casi idéntico
(>= 10 5-gramas, solapamiento >= 0,65): noticias iguales o similares reciben el
mismo tono, tema y subtema aunque sus titulares difieran. La política de voto se
mantiene: con un Negativo en el lote no se reconcilia; empate -> Neutro.

**Tono hacia la marca (nunca hacia la noticia en general):**
- `CRITICA_PAT` ampliado: ataca/ataque, escándalo, crisis, fraude, malversación,
  despilfarro (además de denuncia, cuestionamiento, acusación, sobrecostos, corrupción…).
- `aplicar_regla_critica_con_respuesta` (nueva, determinista): crítica dirigida
  + respuesta atribuida a marca/alias/vocero (respondió, descargo, pronunciamiento,
  comunicado, desmintió, negó, rechazó las acusaciones…) -> Negativo pasa a Neutro,
  porque la información se equilibra. Solo toca Negativos; una respuesta de un
  tercero no neutraliza. El Neutro resultante es "pegajoso": `aplicar_guarda_positiva`
  no lo sube a Positivo.
- `aplicar_guarda_positiva` ampliada: reconoce autoría propia de estudios,
  informes, investigaciones, encuestas y publicaciones (`_AUTORIA_PROPIA_PAT`:
  elaborado/realizado/publicado/liderado/coordinado por…) y verbos publica/publicó,
  socializa/socializó. Gestiones, estudios y acciones propias -> Positivo.
- Catálogo `catalogo_tono_tema.py`: el criterio "Aspectual estricto" y el de
  "Favorabilidad del sector" incorporan las tres reglas (gestión/estudio/acción
  propia = Positivo; crítica/ataque/crisis dirigida = Negativo; crítica con
  respuesta = Neutro) y los ejemplos few-shot se recalibraron (estudios propios
  que antes salían Neutro ahora salen Positivo).

**Tema que corresponde (`prompt_temas_familias`):** el prompt de nombrado de
familias temáticas ahora incluye hasta 2 contextos por familia (400 caracteres
cada uno), además de subtemas y titulares, para que el nombre describa el
contenido real y no solo el agregado léxico.

`tests/test_precision_v47.py` (17 tests) cubre: unión por contexto con titulares
distintos, no-unión de contextos distintos, subtemas y tono iguales por contexto,
gestión/estudio propio -> Positivo, crítica sin respuesta -> Negativo, crítica con
respuesta -> Neutro (y que no sube a Positivo), ataque dirigido detectado, ataque
sin blanco ignorado, tema ajeno no asignado al miembro, y contexto presente en el
prompt temático.

## 12. Estado conocido de las pruebas

`python -m unittest discover -s tests` cubre las invariantes de tema/subtema del lote
(clustering, canonización, un tema por subtema, tema ≠ subtema, sin memoria entre corridas),
el camino PKL (el predict del PKL se invoca y gana sobre el nombrado libre/LLM del lote)
y los perfiles de cliente (`tests/test_perfil_cliente.py`: validación, roundtrip de
guardado/carga, historial y `criterio_texto` en el prompt). `tests/test_calidad_tema_tono.py`
cubre las mejoras de calidad 2026-09-20: clustering con stems distintivos (PISA/IA educativa
no se mezcla con criminología; juventud no se mezcla con suicidio), temas que no empiezan
con preposición y son estrictamente más generales que el subtema, subtemas que no copian
el titular ni quedan vagos, guarda positiva para vocero citado como fuente experta y
unificación de tono por hecho. `tests/test_tema_sin_agrupamiento_forzado.py` cubre que un
miembro ajeno no hereda el tema de la familia (caso "Prevención del suicidio" en
"Ascenso político de Gutiérrez") y que las familias legítimas no se fragmentan.
`tests/test_tema_agrupacion_calidad.py` (v4.5/v4.6) cubre con casos reales del dossier:
canonización morfológica (suicidio/suicidología, criminalidad/criminología,
educativo/educación se unen por igualdad exacta tras canonizar), anti-agrupación (prevención/desafío genéricos, joven demográfico,
marca no une familias, PISA no se mezcla con criminología),
`fusionar_temas_casi_identicos` (fusiona casi-idénticos, no fusiona distintos) y el
guard por miembro en `asignar_temas` (cada noticia recibe el tema que la describe;
el miembro ajeno se separa con tema propio — casos Gutiérrez y Foro de periodismo
científico — y las familias legítimas no se fragmentan). No hay llamadas a API.

## 14. v4.8: modo sin tema (solo tono + subtema)

El cliente puede desactivar la columna `Tema_IA` desde "Ajustes finos"
(checkbox "Generar columna Tema_IA", default activado). Con
`incluir_tema=False` en la config de IA:

- `enrich_rows_with_ai` omite por completo la etapa de temas: no llama a
  `asignar_temas` (ahorra las llamadas LLM secuenciales de
  `nombrar_familias_tema`), ni a `corregir_temas_con_jev`, ni aplica el PKL de
  tema. El tono y el subtema siguen el flujo normal (lotes con votos,
  guardas deterministas, unificación por mismo hecho).
- `volcar_analisis_en_filas(..., incluir_tema=False)` deja `Tema_IA` vacío y
  omite el fallback determinista.
- `output_columns_for_export(..., include_tema=False)` excluye `Tema_IA` del
  xlsx; el archivo sale con `Tono_IA` y `Subtema_IA` después de Audiencia.
- El resumen reporta `modo_taxonomia='omitido'`; la UI no muestra sección de
  temas del lote.

No cambia el default: sin el checkbox, el comportamiento es idéntico a v4.7.

## 15. v4.9 — Selector de modelo: gpt-6-luna (2026-09-22)

OpenAI lanzó el 2026-09-22 los modelos GPT-6 Sol y GPT-6 Luna, disponibles en
la API como `gpt-6-sol` y `gpt-6-luna`. Luna cuesta $0.10/1M tokens de entrada
y $0.50/1M de salida (mitad que su predecesor) y está descrito por OpenAI como
su modelo más eficiente para tareas enfocadas de alto volumen — justo el perfil
del etiquetado por lotes de esta app.

- La app ya hablaba con la API vía `/chat/completions` con `model` como
  parámetro libre: ningún hardcode impedía usar otro modelo.
- Nuevo `st.selectbox` "Modelo de IA" en Ajustes finos con opciones
  `gpt-4.1-nano-2025-04-14` (default, comportamiento sin cambios),
  `gpt-6-luna` y `gpt-6-sol`. El valor viaja `pending_ai_config["model"]` →
  `enrich_rows_with_ai(model=...)` → `cfg['model']` → payload de
  `llamar_llm`, verificado por `tests/test_modelo_luna.py`.
- v4.9 mantenía `gpt-4.1-nano-2025-04-14` como default (los prompts y el gate
  se calibraron contra ese modelo).

## 16. v4.10 — Defaults: gpt-6-luna y sin columna Tema_IA (2026-09-22)

Decisión del usuario: el default del selector pasa a `gpt-6-luna`
(`MODELO_DEFECTO` en `analyzer_tono_tema.py` y el fallback de
`pipeline.py` también apuntan a luna), y el checkbox "Generar columna
Tema_IA" sale **desmarcado** por defecto — el Excel sale solo con
`Tono_IA` y `Subtema_IA` salvo que el usuario active el tema a mano.

## 17. v4.11 — Fix crítico: `max_completion_tokens` para GPT-6 (2026-09-22)

Fallo reportado con gpt-6-luna: `HTTP 400: Unsupported parameter: 'max_tokens'
is not supported with this model. Use 'max_completion_tokens' instead.`
`llamar_llm` enviaba siempre `max_tokens`; la API lo rechazaba y **todas**
las llamadas fallaban. Cada grupo caía entonces al fallback determinista
(tono `Neutro` + subtema = primeras palabras del título), lo que explicaba a
la vez los 377 s (reintentos inútiles en cada lote) y la calidad destruida.
La lógica de calidad (prompts, gate, guardas) no se tocó en v4.9–v4.10: lo
que se vio fue el fallback total, no un cambio de criterio.

- Nuevo `_param_limite(modelo)`: `max_completion_tokens` para familias nuevas
  (`gpt-5/6…`, serie `o`); `max_tokens` para el resto (nano sin cambios).
- Autocorrección reactiva en `llamar_llm`: si un 400 menciona
  `max_completion_tokens`, reintenta con el parámetro corregido; si un 400
  rechaza `temperature`, reintenta sin ella. Cada ajuste ocurre una sola vez
  por llamada y cubre modelos futuros sin cambiar código.
- Tests: `tests/test_llamar_llm_params.py` (6 ok: payload inicial de luna,
  swap reactivo ante 400, retiro de temperature).

## 18. v4.12 — Velocidad sin tocar calidad + nano por defecto (2026-09-22)

Decisión del usuario: el default vuelve a `gpt-4.1-nano-2025-04-14`
(selector, `MODELO_DEFECTO` y fallback de `pipeline.py`); luna/sol siguen
como opciones. El fix de `max_completion_tokens` (v4.11) se conserva.

Auditoría de tiempos con el dossier Cotelco (445 filas, LLM simulado
instantáneo): `enrich_rows_with_ai` tardaba 26.9 s en puro CPU local.
El profiling mostró que `construir_grupos` consumía ~60 s por un bug de
indentación: el pase "Titulares cortos casi iguales" quedó anidado dentro
del loop de bolsa de palabras y se ejecutaba `len(base)` veces (88 M de
llamadas a `find`). Los merges son idempotentes, así que al sacarlo a pase
único la partición es bit a bit idéntica (verificado: 234 grupos iguales
antes/después) y el tiempo cae a 2.1 s.

Mejoras adicionales, todas neutras en calidad:
- `_http_post` con `requests.Session` reutilizada: evita renegociar TLS en
  cada una de las ~50-90 llamadas (tests en `test_llamar_llm_params.py`).
- Loop de reparación de etiquetas en paralelo (mismos workers, mismos
  trozos de 12, merge por id de grupo: resultado idéntico).
- Default de "Llamadas en paralelo": 4 → 8 (rango hasta 16); los lotes son
  independientes, no afecta el etiquetado. Los 429 se siguen manejando con
  backoff.

Estimación para 445 filas con nano: ~6 oleadas de llamadas (234 grupos,
lotes de 10, votos=2, 8 workers) + ~5 s locales → del orden de 2 minutos,
frente a los 400+ s medidos con luna fallando.

## 19. v4.13 — Costo aprox. en tarjetas + PKL de tema manda (2026-09-22)

Costo aproximado de IA en la tarjeta de resultados finales:
- `llamar_llm(..., uso=...)` acumula `prompt_tokens`/`completion_tokens` del
  `usage` real de cada respuesta (seguro entre hilos con `_USO_LOCK`).
- `PRECIOS_MODELO_USD` (USD/millón): nano $0.10/$0.40 (tarifas indicadas por
  el cliente), luna $0.10/$0.50, sol $2.00/$10.00 (anuncio OpenAI).
- `enrich_rows_with_ai` guarda en `_ULTIMO_RESUMEN`: `uso_tokens`
  (input/output/llamadas), `costo_aprox_usd`, `costo_modelo`, `costo_precios`.
- `app.py` muestra quinta tarjeta "Costo IA aprox." + caption con el detalle
  (tokens in/out, llamadas, modelo). Solo cubre llamadas OpenAI; Jev no
  entra en el cálculo.

PKL de tema verificado y reforzado:
- Con `theme_model` presente, `incluir_tema` se fuerza a True dentro de
  `enrich_rows_with_ai` y en `pipeline._ai_extra_con_pkl` (para la columna
  del Excel): la clasificación del PKL es local, sin llamadas LLM ni demora,
  así que siempre se aplica aunque el checkbox "Generar columna Tema_IA"
  venga desmarcado. Sin PKL, el checkbox sigue mandando.
- Verificado: `aplicar_pkl_del_cliente` conserva las clases verbatim (solo
  `strip` vía `format_theme_label`), marca `origen='pkl'`, nunca toca el
  subtema, y se salta `asignar_temas`/`corregir_temas_con_jev`.
- Tests: `tests/test_costo_pkl_tema.py` (12 ok).

## 20. v4.14 — Unificación de subtemas entre lotes + Tema visible + badge PKL (2026-09-22)

Pase final de unificación entre lotes (1 llamada LLM):
- `unificar_subtemas_llm(cfg, grupos, etiquetas, uso=...)` corre después de
  `canonizar_subtemas` + `unificar_subtemas_noticias_similares`, antes de las
  guardas de tono (el voto de tono por subtema usa los subtemas ya unificados).
- Recibe la lista completa de subtemas únicos (con un titular corto de ejemplo
  por subtema para desambiguar) y pide al modelo agrupar solo los que son
  EXACTAMENTE el mismo hecho/asunto. Prompt conservador: ante la duda no
  fusiona; no une por genéricos, marca, ciudad/persona/fecha distintas.
- `_sanitizar_fusiones`: índices 1-based válidos, ≥2 distintos por grupo, sin
  repetir un índice en dos grupos. `_canonico_de_fusion`: gana el más
  frecuente; en empate, el más corto (mismo criterio que `canonizar_subtemas`);
  conserva el texto original verbatim.
- Si la llamada falla, devuelve 0 sin romper el pipeline. No llama si hay ≤1
  subtema único. La llamada suma a `uso` (tarjeta de costo).
- `_ULTIMO_RESUMEN['subtemas_unificados_llm']` con el conteo; `app.py` lo
  muestra en la línea informativa del análisis.

Tema más visible:
- Nuevo radio "Columna Tema_IA" en la sección 2 de configuración (junto a
  "Lista de Temas"): "Solo Tono_IA + Subtema_IA (rápido)" /
  "Agregar Tema_IA con IA (etapa adicional)". Default: rápido (igual que antes).
- Eliminado el checkbox "Generar columna Tema_IA" de Ajustes finos (duplicaba
  el control). El help del radio aclara que con PKL de tema la columna se
  genera igual, sin costo extra de IA.
- Texto de ayuda de la sección 3 (PKL) actualizado: subir un PKL de tema
  activa Tema_IA automáticamente aunque se elija el modo rápido.

Badge "Tema: PKL activo":
- En resultados, si `modo_taxonomia == 'pkl'`, banner `st.success`:
  "◆ Tema: PKL del cliente activo — N grupos clasificados con las clases del
  modelo (verbatim, sin reescritura)."

Tests: `tests/test_unificacion_subtemas_llm.py` (15 ok: sanitización,
canónico por frecuencia/empate/verbatim, fusión aplicada, sin fusiones,
fallo LLM no rompe, sin llamada con ≤1 subtema, suma a `uso`).

## 21. v4.15 — Fix calidad subtema v4.14 + participación en reuniones es Positivo (2026-09-22)

Regresión reportada de v4.14 (pase `unificar_subtemas_llm`): subtemas menos
específicos y Positivos volteados a Neutro. Causas y correcciones:
- Empate de frecuencia elegía el subtema MÁS CORTO → ahora el MÁS LARGO
  (más específico). `_canonico_de_fusion` conserva verbatim.
- La pasada fusionaba subtemas con tonos distintos y el voto de tono por
  subtema (`unificar_tono_mismo_hecho`) volteaba Positivos a Neutro → ahora
  se salta cualquier fusión con tonos heterogéneos (ante la duda, separar).
- `_sanitizar_fusiones` tolera índices 0-based además de 1-based (antes una
  respuesta 0-based se descartaba en silencio y no se fusionaba nada).

Regla de tono (pedido del cliente): participación de la marca en reuniones /
conversatorios / foros es Positivo.
- En `aplicar_guarda_positiva`: verbo de participación (participa, asistió,
  hizo parte, intervino…) + nombre del evento (reunión, conversatorio, mesa,
  encuentro, foro…) + actor en la misma oración → Neutro a Positivo.
- No toca Negativos; no aplica en tragedia sin acción de la marca (la regla
  tragedia corre después y sigue mandando).
- `eventos` ampliado con reunión/reuniones/conversatorio (organiza/convoca/
  realiza/celebra + reunión también cuentan).

Tests: `tests/test_unificacion_subtemas_llm.py` (24 ok, incl. nueva clase
`TestGuardaParticipacion` con 6 casos).

## 22. v4.16 — Freno anti-encadenamiento + Negativo calibrado + contexto mínimo (2026-09-23)

Tres mejoras derivadas de la auditoría del dossier Cotelco (445 noticias, 23 columnas).

1. **Freno anti-encadenamiento en `construir_grupos`.** Las palabras
   omnipresentes del dossier (>12% de los titulares, mínimo 8) no sirven como
   puente distintivo entre hechos. Los cuatro pases por similitud de título
   ahora exigen que las palabras compartidas sean DISTINTIVAS
   (`_puente_distintivo`). Caso real: 'Traslado de adultos mayores' había
   absorbido '36 hoteles resultaron afectados' por compartir solo
   {cali, hoteles, terremoto}. No baja umbrales ni fuerza familias: solo
   elimina fusiones, nunca agrega ("ante la duda, separar").

2. **Negativo calibrado: el señalamiento debe apuntar a la marca**
   (`aplicar_regla_negativo_sin_blanco`, corre tras crítica-con-respuesta).
   Baja Negativo a Neutro cuando la marca no es blanco del señalamiento Y no
   protagoniza la noticia (mención incidental). `_marca_blanco_de_critica`
   exige verbo de acusación en construcción direccional ("cuestionaron a
   Cotelco", "Cotelco fue sancionada"); un sustantivo-tema como 'multa' en
   'fotomultas' no cuenta. `_marca_protagonista`: marca en titular o ≥2
   menciones. Caso real: el único Negativo del dossier Cotelco (fotomultas,
   mención incidental) pasa a Neutro.

3. **Contexto mínimo** (`_contexto_minimo_util`). Si la extracción por mención
   devolvió solo un fragmento (<140 caracteres o <18 palabras, p. ej. "Edwin
   Bernal, director ejecutivo de Cotelco."), se completa con titular + texto
   más completo (resumen). Los contextos útiles no se tocan.

Tests: `tests/test_calidad_v416.py` (10 ok, incl. réplica del caso real
traslado-vs-afectados y verificación de que el código viejo sí fusionaba el
par de prueba). Suite completa: 196/196 OK.

### Calibración 2026-09-23 (mismo v4.16, sin bump de versión)

- **Generalidad por cliente:** las tres mejoras son agnósticas al cliente
  (verificado por AST: ningún literal de cliente en el código). El freno
  anti-encadenamiento calcula las palabras omnipresentes por dossier; el
  Negativo calibrado y el contexto mínimo se parametrizan con
  marca/alias/voceros del perfil o del modo manual.
- **Contexto analizado recalibrado** (`_contexto_exacto_marca`): antes
  devolvía párrafos completos con la mención (tope 6000) → ahora devuelve el
  extracto exacto: solo las oraciones con mención de marca/alias/vocero,
  literales, deduplicadas y en orden. Topes: 1200 caracteres en
  radiodifusión (Radio, Televisión, Aire, Cable, AM, FM, TV — se lee de
  `km["tipodemedio"]`) y 2000 en el resto. El tipo de medio se pasa desde el
  flujo principal; sin vecinas: solo la oración-mención.
- Tests nuevos: `TestContextoExactoCalibrado` (8 ok). Suite: 204/204 OK.

## 23. v4.17 — Fix caso Unisimón «La Universidad de Atalaya» (2026-09-23)

Dos errores graves en una nota (cliente Universidad Simón Bolívar):
subtema «Investigación sobre Carnaval 2027» (hecho ajeno) y tono Neutro
(debía ser Positivo: la marca participa en la creación del campus).

- **Causa 1 (subtema):** `unificar_subtemas_noticias_similares` unía grupos
  por señales débiles y el canon por frecuencia sobrescribía el subtema
  correcto de la minoría con el de la mayoría, sin verificar mismo hecho.
- **Fix 1:** el canon solo se adopta con evidencia FUERTE y directa entre la
  pareja (`_puede_adoptar_canon`): subtemas ya similares, titulares casi
  duplicados (token_set_ratio ≥ 80 y ≥2 palabras de contenido), o pasajes de
  contexto casi idénticos (`_solapamiento_contexto ≥ 0.75`; calibrado: mismo
  hecho real ≈0.83, boilerplate compartido ≈0.62). Ante la duda, separar: la
  unión débil ya no renombra.
- **Causa 2 (tono):** `unificar_tono_mismo_hecho` corría DESPUÉS de las
  guardas, así que el voto por subtema revertía el Positivo que la guarda
  positiva sí había detectado (verificado por simulación con el texto real).
- **Fix 2:** el voto por hecho corre ANTES que las guardas deterministas;
  las reglas de criterio del cliente (guarda positiva, tragedia, crítica con
  respuesta…) tienen la última palabra. Efecto colateral correcto: la regla
  tragedia ya no puede ser revertida por el voto a Positivo.
- Todo paramétrico por marca/alias/voceros y por dossier: nada atado a cliente.
- Tests nuevos: `tests/test_caso_atalaya_unisimon.py` (5 ok). Suite: 209/209 OK.

## 24. v4.18 — Ajustes tras auditoría del dossier Unisimón (2026-09-23)

Auditoría fila por fila del dossier real (41 noticias) tras v4.17: cuatro
subtemas cruzados entre noticias del mismo archivo (el subtema de un grupo
describía el hecho de otro grupo) y dos calibraciones de tono.

- **F1 — Empate de subtema gana el más largo:** `_voto_mayoria` y el canon
  determinista de `unificar_subtemas_noticias_similares` elegían en empate el
  subtema MÁS CORTO. Un solo voto cruzado del modelo (corto y ajeno) le
  ganaba al voto correcto. Ahora en empate gana el más específico (más
  palabras, desempate por caracteres), mismo criterio ya adoptado en v4.15
  para el pase LLM. `reparar_subtemas_ajenos` revierte a `_subtema_desde_titulo`.
- **F2 — Guarda contra subtemas ajenos** (`reparar_subtemas_ajenos`): tras las
  unificaciones de subtema y antes del voto de tono, detecta si el subtema de
  un grupo comparte ≥2 palabras distintivas con el titular de OTRO grupo y
  ≤1 con su propio contenido (título+texto+contexto); si se confirma, lo
  reemplaza por un rótulo honesto derivado del propio titular
  (`_subtema_desde_titulo`: quita artículo inicial, usa lo que sigue a «:»,
  últimas 7 palabras en titulares largos, siglas en mayúsculas conservadas).
  Solo actúa con evidencia fuerte; ignora subtemas genéricos y conjuntos
  distintivos de <2 palabras. Registra `subtemas_ajenos_reparados` en el
  resumen. Casos reales reparados: Atalaya→«Universidad de Atalaya»,
  Estefanel→«Ascenso político de Estefanel Gutiérrez»,
  «LA IA Y LA RECONVERSIÓN LABORAL»→«IA y la reconversión laboral»,
  congreso de psicología→«Congreso Internacional de Innovación en
  Intervención Psicológica».
- **F3 — Guarda positiva con participios pasivos:** `aplicar_guarda_positiva`
  reconoce «organizado/realizado/presentado/publicado… por la marca» (actor
  después del verbo, introducido por «por»). Caso real: foro de periodismo
  climático «organizado por la Universidad Simón Bolívar» → Neutro a Positivo.
- **F4 — Alma máter no es acción de la marca:** `_mencion_biografica` evita
  que «recordó su formación en la Universidad…, donde participó…»,
  «egresado de…», «alma máter», «estudió en…» suban a Positivo (participó la
  persona, no la marca). Solo cuenta si el actor va DESPUÉS de la marca
  biográfica («estudió en la Universidad»); «la Universidad estudió…» es
  acción propia y no se excluye. Caso real: proyecto de paz barrial de
  Estefanel → permanece Neutro.
- Todo paramétrico por marca/alias/voceros y por dossier (verificado por AST:
  los únicos literales del dominio son recursos lingüísticos generales —
  geografía y sustantivos en -al — preexistentes).
- Tests nuevos: `tests/test_ajustes_v418.py` (18 ok; 4 cruces reales, falsos
  positivos, empate→largo, participio pasivo, alma máter). Dos tests de
  `test_pkl_tono_tema.py` actualizados al comportamiento correcto: el fake
  que etiquetaba la nota de robótica como «PAE» ahora se repara (caso 1), y
  el caso «mismo subtema, distintas clases PKL» usa una nota que comparte el
  subtema legítimamente. Suite: 227/227 OK.

## 25. v4.19 — Precisión de tono: marca como sede + cita experta (2026-09-23)

Regla del cliente: «eventos en la marca/alias o participación de voceros es
positivo». Caso real (ID 60761392): «Salud Consciencia 2026, realizado el 26
de agosto en la Universidad Simón Bolívar» quedó Neutro.

- **Sede del evento** (nueva rama en `aplicar_guarda_positiva`): verbo de
  realización (`realiz|celebr|organiz|desarroll` en participio, futuro,
  presente y pasados; `llev(ad[oa]s?|ara|a) a cabo`; `tuvo/tiene/tendrá
  lugar`) o sustantivo de evento (congreso, foro, simposio, panel…) +
  «en + marca/alias» en la misma oración → Positivo. La marca es anfitriona.
  No aplica en tragedia sin acción de la marca; tampoco en menciones
  biográficas («realiza sus estudios en la Universidad» se excluye por
  sustantivo académico entre verbo y marca) ni en alianzas («en alianza con
  … la Universidad» no es sede: el «en» no precede a la marca). «encuentro»
  no cuenta tras «me » (verbo, no evento).
- **HABLA_PAT suma «de acuerdo con»**: «de acuerdo con Hernando Sánchez,
  biólogo y docente de la Universidad Simón Bolívar, …» → Positivo (vocero
  citado como fuente experta). «según» se excluyó deliberadamente: cero
  verdaderos positivos en el dossier real y riesgo de marcar la marca como
  simple punto de referencia («según la Policía, ocurrió frente a la
  Universidad»).
- Verificado sobre el dossier real: voltean a Positivo 60761392 (Salud
  Consciencia), 60816125 y 60794120 (Ruta del cuidado, sede Unisimón),
  60879489 (simposio científico en Unisimón) y 60843713 (docente experto en
  ciénaga). Siguen Neutro: alianzas (11874683), tesis biográfica (60775126),
  tragedia (60887853), asistencia como invitado (60763705).
- Todo paramétrico por marca/alias/voceros (verificado por AST).
- Tests nuevos: `tests/test_precision_tono_v419.py` (14 ok). Suite: 241/241 OK.

## 26. v4.20 — Mismo hecho por ancla de persona + voto final de tono (2026-09-24)

Caso real (Fundación Santa Fe, dossier 2026-09-24, 484 filas): un mismo
paciente produjo 8+ variantes de subtema («Estado de salud de Yamid Amat»,
«Hospitalización de Yamid Amat», «Yamid Amat en UCI», «Ingreso a UCI de
Yamid»…) y el mismo hecho quedó con tonos divididos (194 Positivo / 67
Neutro). Criterio del cliente: para salud es esencial ver los pacientes
tratados; noticias similares deben compartir subtema Y tono.

- **Clases de evento** (`_CLASES_EVENTO`, vocabulario de dominio, no de
  cliente): `salud` (hospitalización, UCI, pronóstico, complicación,
  tratamiento…), `nacimiento` (nacimiento, parto, cesárea), `cirugia`,
  `lanzamiento`, `reunion`, `reconocimiento`, `ranking`, `inauguracion`,
  `firma`. Las etapas asistenciales van separadas: el ingreso ≠ el
  nacimiento aunque compartan paciente.
- **`unificar_hecho_por_ancla`** (tras `reparar_subtemas_ajenos`, antes del
  voto de tono): une grupos con ancla de persona compartida (nombre propio
  multi-palabra, nunca marca/alias/voceros/geografía) + clase de evento
  compatible + firma de evento sin más de una diferencia. Referencia cruzada
  madre/hijo solo si ambos titulares se nombran. El canon es el subtema más
  frecuente (empate: nombra el ancla, luego el más largo) y se escribe con
  sus mayúsculas originales. Ante la duda, no une.
- **Veto de anclas no-persona** (`_ancla_vetada` + `_ORG_PAT`): un «nombre
  propio» con vocabulario de evento («Así Vamos en Salud», «Sistema de Salud
  Colombiano») o sustantivo común («Conversatorio», «Estado») no es un
  paciente y no ancla.
- **Guarda positiva: episodio de atención en la marca**
  (`_atencion_paciente_en_marca`): ancla de persona + clase asistencial +
  marca/alias como lugar («en/de/a la Fundación Santa Fe») → Positivo. Para
  clientes de salud el episodio asistencial es contenido propio. No aplica en
  tragedia sin acción ni con crítica dirigida.
- **Guarda positiva: alianza/convenio con la marca** («alianza entre Morphy
  y la Fundación…») → Positivo; «de acuerdo con» se excluye (atribución).
- **Guarda positiva: sede en construcción de sujeto** («Serena del Mar vivió
  una gran fiesta deportiva») → Positivo.
- **`aplicar_regla_positivo_incidental`** (espejo de la v4.16): el Positivo
  también se evalúa hacia la marca. Baja Positivo→Neutro la mención
  incidental (una sola mención, sin protagonismo). La guarda se valida en
  una copia: si ella misma encontraría evidencia (atención al paciente,
  alianza, sede, vocero…), el Positivo se conserva. No toca Negativos ni
  Duplicadas; lo bajado queda `neutro_pegajoso`.
- **`voto_final_tono_por_subtema`**: tras las guardas, mayoría ≥60% dentro de
  cada subtema unificado; respeta `neutro_pegajoso` y nunca toca Negativos.
- **Alias ambiguo** (`_marca_protagonista`): la forma corta («Santa Fe»)
  solo cuenta como protagonismo fuera de contexto deportivo local
  (`_DEPORTE_PAT`: partido, empate/empató, campín, gol, fútbol…); la forma
  larga siempre resuelve primero.
- Verificado sobre el dossier real (simulación determinista, sin LLM):
  Yamid 280/284 en «Estado de salud de Yamid Amat» (281 Positivo);
  «Vargas se pronuncia sobre Yamid», «Edad y trayectoria» e «Información
  sobre el EPOC» siguen separados; Lina/Gael por etapas (12 ingreso, 15
  nacimiento); 25 Positivos incidentales → Neutro (rankings de otros
  hospitales, inmobiliaria, Mhoni Vidente, Morphy en config Santa Fe…).
- Tests nuevos: `tests/test_v420_ancla_tono.py` (27 ok). Suite: 268/268 OK.

## 27. v4.21 — El subtema nunca es el titular (2026-09-24)

El modelo a veces devuelve el titular tal cual como subtema. La revisión
exhaustiva sobre el dossier Fundación Santa Fe (484 filas) encontró el
panorama real:

- `copia_titular` tenía 46 falsos positivos: etiquetas nominales válidas
  («Estado de salud de Yamid Amat») que aparecen dentro del titular iban a
  la vuelta de reparación LLM, desperdiciando llamadas y arriesgando que el
  modelo «arreglara» lo que estaba bien. Quedan 2 casos genuinos.
- 4 subtemas-cita: `"Septiembre era el momento perfecto"` como etiqueta.
- El fallback final recortaba 5 palabras crudas del titular (medio titular
  como subtema).

Cambios (generales, no atados a cliente):

1. `_es_etiqueta_valida(sub)`: el subtema es etiqueta válida por sí misma
   (2–7 palabras, nominal, sin ¡!¿? ni comillas envolventes) reusando
   `validar(..., _con_copia=False)` — sin el flag habría recursión infinita.
2. `validar()` solo marca `copia_titular` cuando el subtema NO es etiqueta
   válida: una etiqueta buena que coincide con (parte de) el titular no es
   pereza del modelo.
3. `reparar_subtema_determinista()`: el titular copiado tal cual, la cita
   como etiqueta y la pregunta como etiqueta se reparan sin LLM derivando
   un rótulo honesto del titular. Corre antes de la vuelta LLM; solo los
   problemas puramente mecánicos (`copia_titular`, `caracter_marcador`) van
   por esta vía. Contador en `_ULTIMO_RESUMEN['subtemas_reparados_determinista']`.
4. `_subtema_desde_titulo` endurecido: quita interjecciones («¡Atención!»,
   «Última hora:»), signos ¡!¿? en bordes, comillas envolventes; elimina
   oraciones-pregunta («¿Cuántos años…? Inició en…» → «Inició en…»); ante
   `:` prefiere el segmento no-cita y detecta frase destacada sin comillas
   («…Gael: Septiembre era el momento perfecto» → el hecho, no el destacado;
   `_es_frase_destacada`: último ≤6 palabras sin clase de evento + primero
   ≥2× más largo con clase); tras recortar a 7 palabras quita verbos
   iniciales («revela detalles…» → «detalles…»).
5. El fallback final usa `_subtema_desde_titulo` en vez del recorte crudo.
- La cita parcial dentro de etiqueta nominal («Lanzamiento de álbum
  'Arriba La L'») se conserva: solo se reescribe la cita envolvente.
- Una etiqueta válida idéntica al titular no se toca (el titular ya era
  etiqueta; no hay nada que reparar).
- Validación dossier real: `copia_titular` 46 → 2; los 2 se reparan sin LLM
  («Septiembre…» → «Detalles del nacimiento de su hijo Gael», que luego la
  unificación por ancla v4.20 lleva a «Nacimiento de Gael en Santa Fe»).
- Tests nuevos: `tests/test_v421_subtema_no_titular.py` (21 ok); 2 tests de
  `test_calidad_tema_tono.py` actualizados al criterio v4.21 (los ejemplos
  que marcaban son etiquetas válidas). Suite: 258/258 OK (3 módulos no
  cargan: falta `openai`, ambiental preexistente, no instalable por
  conflicto debian).

## 28. v4.22 — «Señaló» no es crítica dirigida (2026-09-24)

Segunda vuelta de auditoría sobre el dossier Fundación Santa Fe (484
filas), corriendo la cadena determinista v4.21 sobre la salida ya
procesada: 151 cambios potenciales, de los cuales el hallazgo
implementable fue uno solo, pero de alto impacto.

Hallazgo: `CRITICA_PAT` incluía `se[nñ]al`, que calzaba el verbo de habla
«señaló/señala» («La Fundación Santa Fe señaló que los próximos
comunicados…»). En el dossier, **28 de 32** activaciones de
`_critica_dirigida` venían de ahí — todas falsas. El propio código se
contradecía: `HABLA_PAT` ya trata «señaló» como verbo de habla (fuente
experta). Efectos del falso positivo:

- Vetaba la rama asistencial de `aplicar_guarda_positiva` («estado
  crítico» no es crítica a la marca): 5 episodios de atención al paciente
  (Yamid Amat en UCI, parte médico de la Fundación) quedaban
  desprotegidos y `aplicar_regla_positivo_incidental` los bajaba de
  Positivo a Neutro.
- Podía disparar `aplicar_regla_critica_con_respuesta` cuando había un
  «comunicado» cerca (el parte médico no es un descargo).

Cambio (general, no atado a cliente): en `CRITICA_PAT`, `se[nñ]al` →
`se[ñn]alamientos?`. Solo el sustantivo conserva sentido acusatorio
inequívoco («los señalamientos contra la Fundación»). Las formas verbales
acusatorias con blanco explícito las sigue cazando
`_marca_blanco_de_critica` por construcción direccional (no tocado: exige
`a|al|contra|hacia` + marca, «señaló que» nunca calza).

Validación dossier real: `positivo incidental` 20 → 15; los 4 episodios
«parte médico» vuelven a Positivo (el 5.º, en inglés —«in Critical
Condition in Bogota ICU»—, queda fuera: las clases de evento y las
preposiciones de sede son de español por diseño). Los 3 subtemas que
siguen con tono dividido (cardiología, pediatría, EPS: 2N/1P) son
divisiones defendibles: la guarda encuentra evidencia positiva real según
las reglas del usuario («fue reconocida en…», «ocupó el puesto 134»,
«organizado por… la Fundación Santa Fe») y el voto final respeta los
Neutros pegajosos por diseño.

- Tests nuevos: `tests/test_v422_senalo_no_es_critica.py` (8 ok).
- Limitación conocida: cobertura en inglés fuera de alcance (1 fila).

## 29. v4.23 — Columna Prominencia (2026-09-24)

Nueva columna `Prominencia` en el xlsx de resultados: métrica determinista
(sin LLM) de la presencia de la marca en cada noticia. Cuenta menciones de
la marca y sus alias —exactamente lo digitado en "Marca o Cliente Principal"
y "Alias o términos relacionados" (coma o punto y coma)— en Título y
CuerpoEs ("Resumen - Aclaracion" como respaldo).

Búsqueda por palabras y similitudes: insensible a mayúsculas y tildes;
tolera "santa fe" = "santa-fe" = "santafe". La alternancia ordena el término
más largo primero para no contar dos veces ("Fundación Santa Fe de Bogotá"
cuenta una vez, no dos con "Santa Fe").

Solo tres categorías (reglas del usuario):
- **Exclusiva**: 4+ menciones en total; o marca en el Título con 2+
  menciones en el cuerpo.
- **Compartida**: 2-3 menciones en total; o marca en el Título con 0-1
  menciones en el cuerpo (el titular solo no basta).
- **Referencial**: 0-1 menciones y sin presencia en el título.

La columna se genera siempre que haya marca configurada, con o sin
análisis IA, y va justo después de las columnas IA (o tras Audiencia si no
hay IA). No toca ninguna otra lógica: es una adición.

- Funciones: `calcular_prominencia()`, `aplicar_prominencia()`,
  `clasificar_prominencia()`, `contar_menciones_prominencia()` en
  `analyzer_tono_tema.py`; hook en `pipeline.py`.
- Tests nuevos: `tests/test_prominencia.py`.
- Limitación conocida: "marca junto a otras marcas" (comparativos) no se
  detecta sin una lista de competidores; la banda 2-3 la cubre mecánicamente.

## 30. v4.24 — Comunicado de la marca + interruptor de Prominencia (2026-09-24)

Dos adiciones a la columna Prominencia (v4.23), sin tocar lo demás:

1. **Regla del comunicado**: si la noticia contiene "comunicado(s) de
   [la/el/los/las/del] <marca o alias>" —es decir, la noticia ES la voz de
   la propia marca— basta con **2 o más menciones** para ser Exclusiva
   (antes esas 2-3 menciones caían en Compartida). Con 1 mención sigue
   siendo Referencial. Detección con regex sobre texto normalizado
   (`_patron_comunicado`); "comunicado de prensa" (ajeno) no dispara la
   regla.

2. **Interruptor en la app**: checkbox "Agregar columna Prominencia
   (presencia de la marca)" junto a la opción de Tema_IA (default
   activado). Viaja en `ai_config["incluir_prominencia"]`; el pipeline
   solo genera la columna si hay marca configurada Y el interruptor está
   activo.

- Tests nuevos: clase `TestComunicadoMarca` en `tests/test_prominencia.py`
  (6 pruebas).

## 31. v4.25 — Título de auditor no cuenta en radio/TV (2026-09-24)

Solo para prominencia: cuando el Tipo de Medio es Aire, Cable, AM, FM,
Radio o Televisión, el título lo pone un auditor (no el medio), así que se
ignora y la prominencia se calcula solo con el contenido de la noticia.
Cubre valores crudos y normalizados del pipeline (AM/FM→Radio,
Aire/Cable→Televisión); comparación insensible a mayúsculas y tildes.
Prensa/Internet/Revistas siguen contando el título. No toca ninguna otra
lógica (tono, temas, subtemas).

- Tests nuevos: clase `TestTituloAuditor` en `tests/test_prominencia.py`
  (4 pruebas).

## 32. v4.26 — Prominencia desmarcada por defecto y al final (2026-09-25)

El checkbox "Agregar columna Prominencia (presencia de la marca)" ahora
sale DESMARCADO por defecto (`value=False` en la app; el fallback del
pipeline para `incluir_prominencia` también pasó a False). Cuando el usuario
lo activa, la columna "Prominencia" se ubica al final del xlsx, después de
"Contexto analizado" (con o sin IA), en vez de junto a las columnas IA.
Además se corrigió el borde de v4.24: activar Prominencia sin IA ni PKL ya
construye el config y calcula la columna (es determinista, no necesita LLM).

- Tests nuevos: clase `TestColumnaProminenciaAlFinal` en
  `tests/test_prominencia.py` (3 pruebas de posición de columna).
