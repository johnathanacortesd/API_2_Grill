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
