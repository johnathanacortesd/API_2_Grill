# Grill-API — News Analytics & NLP Platform

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://grill-api.streamlit.app/)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.28+-FF4B4B.svg?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Production-brightgreen.svg)]()

```text
  ____ ____  ___ _     _        _    ____ ___ 
 / ___|  _ \|_ _| |   | |      / \  |  _ \_ _|
| |  _| |_) || || |   | |     / _ \ | |_) | | 
| |_| |  _ < | || |___| |___ / ___ \|  __/| | 
 \____|_| \_\___|_____|_____/_/   \_\_|  |___|
```

**Grill-API** es un motor de procesamiento y visualización interactiva para el análisis de noticias generando un tono, tema y subtema. La plataforma ingiere información del admin Grill de GlobalNews Group, aplicando técnicas de Procesamiento de Lenguaje Natural (NLP) para evaluar el sentimiento, extraer entidades nombradas y detectar tendencias informativas de forma automatizada agrupando noticias similares en mismos temas y subtemas y enfocando el tono al impacto de la marca analizada y no al conjunto total de la noticia.

---

## 🌐 Dashboard y Acceso

La plataforma se encuentra desplegada y disponible para pruebas en vivo:

* **URL de Producción:** [https://grill-api.streamlit.app/](https://grill-api.streamlit.app/)

### 🔒 Autenticación

El acceso a la interfaz de producción está resguardado mediante autenticación.

1. Al ingresar a la URL del proyecto, la aplicación solicitará una **contraseña de acceso**.
2. Ingrese las credenciales en el campo de entrada ubicado en el módulo de autenticación (o menú lateral).
3. Una vez validada la contraseña, se desbloquearán los módulos de ingesta, análisis de métricas y gráficos en tiempo real.

> **Nota para evaluadores:** Solicite la contraseña de acceso directamente al mantenedor del proyecto ([@johnathanacortesd](https://github.com/johnathanacortesd)).

---

## 🚀 Funcionalidades

### Agrupación consistente (actualizada)

El pipeline crea un identificador `Grupo noticia` para republicaciones y noticias equivalentes. La pertenencia se valida con título normalizado, palabras distintivas y similitud semántica; no se fusionan hechos con acciones contradictorias (por ejemplo, aprobación frente a rechazo). Una vez clasificado el grupo, se propagan sus valores canónicos de `Tono IA`, `Tema` y `Subtema` a todas sus filas, evitando que una misma noticia cambie de tono o tema entre medios.

Los subtemas se limpian y limitan a un máximo de seis palabras, como frases nominales completas, sin collages de keywords, verbos conjugados ni etiquetas genéricas. Las filas marcadas como duplicadas conservan su relación mediante `ID duplicada`; las filas equivalentes no eliminadas conservan toda su clasificación y el nuevo `Grupo noticia`.

`Marca principal` es el eje obligatorio del análisis. El motor busca el nombre completo, sus alias y coincidencias distintivas relacionadas en `Título` y `Resumen - Aclaración`. El tono mide exclusivamente el impacto reputacional sobre esa marca; el tema y el subtema describen el hecho relacionado con ella. Una etiqueta que sea solamente el nombre de la marca, una versión incompleta del nombre o una frase genérica se rechaza y se regenera.

Las validaciones no contienen nombres ni reglas especiales para clientes concretos. El mecanismo es reutilizable: separa los tokens de cualquier `Marca principal`, identifica el tipo de acontecimiento y conserva las palabras que describen su objeto. Por ejemplo, puede formar etiquetas como `Lanzamiento de carrera deportiva`, `Convenio de formación profesional` o `Investigación por fallas operativas`, según el contenido de cada noticia.

### Una noticia = un subtema, un tono y un tema (v4.72)

Pedido: noticias iguales o realmente similares (mismo título, mismo
`CuerpoEs`, textos similares en esas columnas o en `Contexto analizado`)
deben llevar el mismo subtema, y el subtema debe ser el correcto. Se
conservan el modelo de API y el orden de columnas del xlsx final.

**Qué fallaba (medido con un banco de pruebas nuevo).** Después del modelo,
unos trece pases tocaban el subtema por grupo y luego por FILA (fidelidad
por fila, anti-copia, título final, forma final). Cada uno podía reemplazar
el rótulo de una fila por un recorte de su propio titular, así que la misma
noticia terminaba con varios subtemas («Disminución de donantes de órganos»
en unas filas, «Lista de espera» y «Preocupación en hospitales» en otras),
o con un rótulo peor que el del modelo («Requisitos» en lugar de
«Prevención del cáncer de mama»). Además, una nota puente cuyo titular no
nombra el lugar («…sede de urgencias en el norte de Bogotá») encadenaba
«…en Chía» con «…en Usaquén».

- **Pase final por noticia** (`unificar_por_noticia`), después de volcar el
  análisis en las filas, así que nada posterior lo deshace:
  - *Noticias*: filas del mismo grupo, del mismo hecho consolidado, con el
    mismo titular comparable (sin «VIDEO |», sin « - Medio», sin cola de
    llamado, sin comillas ni mayúsculas), el mismo `CuerpoEs`, un pasaje
    literal compartido (8 palabras seguidas con cifra, o dos pasajes) o
    coseno TF-IDF ≥ 0.50 con enlace promedio ≥ 0.35 entre bloques (sin
    encadenar). Nunca une dos notas del mismo molde que nombran, cada una,
    un lugar o una persona ausente en la otra.
  - *Subtema*: uno por noticia, elegido entre los de sus filas y los que el
    modelo escribió para sus grupos, cotejado contra la noticia COMPLETA
    (todos sus titulares y textos). Orden: subtema fijo del perfil > bien
    formado y con asunto (solo la marca no vale) > fiel > sin copia de
    ningún titular > escrito por el modelo para esa noticia > más filas.
    La paráfrasis del modelo se acepta aunque un medio use otras palabras;
    un rótulo que describe mejor a OTRA noticia del dossier se rechaza.
  - *Molde en otra ciudad*: si dos noticias distintas comparten rótulo y
    chocan por lugar/persona, cada una recupera su rótulo propio o se
    especifica con su lugar («Apertura de sede de urgencias en Chía»).
  - *Tono*: uno por noticia y por subtema compartido (mayoría; empate por la
    evidencia del texto). *Tema*: uno por subtema (con PKL de tema, la
    clase del PKL más frecuente de la noticia).
- **Agrupación** (`construir_grupos`, consolidación por evidencia): las
  uniones se aplican de la más fuerte a la más débil y la guarda de lugar
  o persona se revisa entre BLOQUES y titulares del mismo molde; si un
  titular no nombra lugar, se usa el de la primera oración del cuerpo
  (sin la entradilla «BOGOTÁ (Colprensa).»).
- **Rótulos de emergencia** más fieles: sin colas de servicio
  («: requisitos», «: así funciona…»), sin atribución («…, advierte la
  Fundación»), sin la segunda oración coordinada («…y crece la lista de
  espera») ni entradillas de agencia. Si la reformulación anti-copia falla,
  se conserva el rótulo del modelo cuando el respaldo derivado del titular
  es otra copia.
- **Modo solo PKL**: el tono y el tema se unifican con el mismo
  agrupamiento por evidencia (el agrupador anterior partía o mezclaba
  noticias).
- **Agilidad**: `ctrl()` (la función más llamada) usa una expresión regular
  y `sq()` se memoriza; el pase nuevo cuesta ~1-2 s de CPU en 700 filas.

**Banco de pruebas** (`tools/banco_agrupacion.py`, sin API): dossier
sintético con verdad de base (20 noticias, 70-120 notas por semilla, con
titulares parafraseados, prefijos/sufijos del medio, mayúsculas, resúmenes
de radio, mismo cuerpo con otro titular y trampas «mismo molde en otra
ciudad» / «mismo asunto, otro hecho») y un modelo simulado que redacta el
subtema de forma distinta por grupo.

| 10 semillas (172 noticias con varias notas) | v4.71 | v4.72 |
|---|---|---|
| Noticias con subtema partido | 12 | 0 |
| Fusiones de noticias distintas | 4 | 0 |
| Noticias con tono / tema partido | 1 / 1 | 0 / 0 |
| Filas con un subtema correcto de su noticia | 91.4 % | 95.4 % |
| Con 30 % de rótulos malos del modelo: partidas / fusiones / correctas | 12 / 5 / 88.8 % | 0 / 0 / 93.6 % |
| Modo solo PKL (agrupamiento): noticias partidas / grupos mezclados | 68 / 32 | 0 / 0 |

```bash
python3 tools/banco_agrupacion.py --semillas 10            # resumen
python3 tools/banco_agrupacion.py --semillas 10 --ruido 0.3 --detalle
```

### Agrupación estricta y rendimiento (v4.71)

Pedido: un dossier de 250 notas tardaba 857 s; y noticias iguales o muy
similares deben quedar con el mismo tono y subtema (con PKL), o con el mismo
tono, tema y subtema (sin PKL), como en API_Secretaria_General_Bogota.

**Por qué tardaba.** No era la API: era CPU del código determinista. Los
pases por pares (fila×fila, grupo×grupo, rótulo×fila) volvían a normalizar y
tokenizar el `CuerpoEs` completo en cada par. El peor caso era
`unificar_etiquetas_titulos_similares`, que recalculaba la «huella de
historia de riesgo» de las dos filas en cada uno de los ~31.000 pares de un
dossier de 250 notas; `_es_geografia` además reconstruía y normalizaba toda
la lista de geografía en cada llamada.

- `nz()`, `words()`, `raiz()`, `_contenido_discriminante`, 5-gramas de
  contexto, fechas/bigramas de evento y raíces del texto se memorizan por
  texto (caché acotada, se limpia al inicio de cada corrida). Mismo
  resultado; cada texto se procesa una vez.
- La huella de riesgo se calcula una vez por fila y solo se comparan las
  filas que la tienen.
- La reformulación anti-copia final (`reparar_copias_finales`) llama al
  modelo en paralelo (antes, en serie).
- Medición con un dossier sintético de 250 notas (cuerpos de ~4.000
  caracteres, modelo simulado sin latencia): v4.70 tardaba 359 s de CPU;
  v4.71 tarda ~7 s (500 notas: ~18 s). El resto del tiempo real es la
  espera de la API.

**Reglas de agrupación de Secretaría (v1.29-v1.35), ahora en Grill.** El
analizador es el mismo de Secretaría; lo específico de Bogotá (TIPO/GÉNERO,
tono del distrito, tono solo por gestión, menciones ajenas) queda detrás de
flags del perfil que los clientes de Grill no activan.

- **Agrupación estricta por defecto** (`agrupacion_estricta`, un perfil
  puede poner `false`): dos notas comparten subtema solo con evidencia
  (título igual o muy parecido, Contexto analizado o CuerpoEs con pasajes
  compartidos). Sin uniones por parecido del rótulo ni por «ancla» temática.
- Consolidación por evidencia (`consolidar_por_evidencia`), variantes del
  mismo rótulo, un subtema por asunto repetido en titulares, garantía
  final `garantizar_coherencia_hecho` (misma noticia ⇒ mismo subtema; mismo
  subtema ⇒ mismo tono, también cuando el tono viene del PKL) y revisión
  final de forma del subtema (sin comas, puntos, pronombre inicial ni
  «sujeto + verbo»).
- Tono: `CRITICA_PAT` con inicio de palabra («atac» ya no coincide dentro
  de «contratación»), críticas al vocero cuentan como críticas a la marca,
  empates resueltos con la evidencia del texto, homónimo de otro país =
  Neutro.

**Nuevas guardas v4.71 (aplican con o sin PKL).**

- *Mismo molde, otro lugar o persona*: «…becas en Cartagena» y «…becas en
  Medellín» no son la misma noticia. Si cada titular nombra un lugar o una
  persona que no aparece en ninguna parte de la otra nota, no se unen
  (agrupación, grafo de evidencia, coherencia por texto, título final,
  asunto del titular).
- La cola de llamado del titular («…: lo que dijo X», «…: esto se sabe»,
  «…: detalles») no cuenta para comparar titulares ni se usa como subtema.
- Un subtema nunca empieza con un pretérito con tilde («Informó este
  miércoles», «Conoció el pasado jueves»: `RE_VERBO` comparaba terminaciones
  con tilde sobre texto ya sin tildes), ni es solo una persona («Ricardo
  Salazar») o solo una expresión de tiempo («Fin de semana»).
- La revisión final de forma solo cambia un subtema por otro que
  corresponda a esa fila.

Resultado en el dossier sintético (50 noticias, 250 notas): historias con
subtema partido 35 → 0-1 (subtemas distintos 81 → ~48 para 50 noticias); historias con tono o tema distinto entre sus notas:
0, con y sin PKL.

### Coherencia de tema y subtema con el texto (v4.70)

Regla: noticias iguales o realmente similares (columnas `Título` y sobre todo
`CuerpoEs`) llevan el mismo subtema; un subtema nunca se pone a una noticia
que no tiene que ver con él, y los subtemas solo se agrupan en un tema si
guardan relación con el texto de `Contexto analizado`. Aplica con o sin PKL.

- **Similitud por texto** (`coherencia_texto.py`): TF-IDF sin tildes, sin
  palabras vacías y sin los tokens de marca/alias/voceros (están en todas
  las notas y no distinguen hechos). Calibración: la misma noticia reescrita
  por otro medio ≈ 0.55-0.70; hechos distintos del mismo asunto ≈ 0.15-0.20;
  notas ajenas ≈ 0.00-0.05.
- **Agrupación de notas**: las uniones solo por parecido del titular son
  débiles y se vetan si los dos `CuerpoEs` (250+ caracteres) no se parecen
  (coseno < 0.20). Las uniones fuertes (cuerpo idéntico o contenido,
  pasajes compartidos, titular casi copia) se conservan.
- **Coherencia de subtemas** (`coherencia_subtemas_por_texto`), después de
  todas las unificaciones por rótulo y antes del voto de tono y del tema:
  - *Separar*: dentro de un subtema, las notas sin relación textual con el
    grupo principal lo pierden, salvo que el rótulo completo esté en su
    titular/entrada/Contexto y el etiquetador se lo haya dado. Recuperan su
    subtema propio (si es fiel a su noticia) o reciben uno nuevo.
  - *Unir*: notas con similitud ≥ 0.45 (la misma noticia) y subtemas
    distintos adoptan el rótulo de la mayoría si es fiel a todas.
  - La reparación anti-copia final ya no puede devolver un subtema que este
    pase rechazó.
- **Tema** (`refinar_familias_por_contexto`): una familia de subtemas se
  separa si sus `Contexto analizado` no tienen relación (coseno < 0.08), y
  dos subtemas con `Contexto analizado` muy parecido (≥ 0.30) van al mismo
  tema aunque sus rótulos no compartan palabras.
- **Contexto analizado**: se extrae del `CuerpoEs` cuando trae la marca, un
  alias o un vocero (antes: el texto más largo disponible). Los voceros
  también se buscan por nombre + primer apellido («Miguel Silva» para
  «Miguel Silva Moyano»; «Juan Pérez» para «Juan Carlos Pérez»).
- **Columna `texto`**: Contexto analizado sin el Título aunque el título
  difiera en forma (comillas, espacios, entidades HTML); no corta dentro de
  una palabra.
- **Columna `Link Ext`**: solo el texto de la URL, sin hipervínculo, sin
  azul ni subrayado.
- **Rótulo de emergencia**: el verbo del titular se nominaliza
  («Secretaría General lanza ventanilla…» → «Lanzamiento de ventanilla…»).

### 👥 Perfiles de cliente (multicliente)

El tono, los alias, los voceros y la lista de Temas se adaptan por cliente sin tocar código:

- **Selector "Perfil de cliente"** en la interfaz: carga marca, alias, voceros, criterio de tono y lista de Temas guardados. Los campos se pueden editar antes de procesar.
- **Guardar perfil**: en *Ajustes finos* puedes guardar la configuración actual como un JSON en `clientes/` para reutilizarla en próximas corridas.
- **Criterio de tono personalizado**: cada perfil puede traer su propia regla de tono en texto libre (`criterio_custom`), que reemplaza a las del catálogo para ese cliente.
- **Historial por cliente**: cada corrida queda registrada y se muestra en "Historial del cliente".

- **Ingesta Multi-fuente:** Captura y normalización de artículos desde RSS, sitios web y conectores de API.
- **Análisis de Sentimientos:** Clasificación automatizada de titulares y contenido en espectros positivo, neutro y negativo.
- **Extracción de Entidades y Palabras Clave:** Detección de organizaciones, personajes públicos y términos recurrentes.
- **Visualización Dinámica:** Cuadros de mando interactivos con filtros temporales, categoría y medio.
- **Estructuración de Datos:** Exportación y procesamiento estructurado listo para integración con otros sistemas.

---

## 🏗️ Arquitectura del Sistema

```text
+-----------------------------------------------------------------------+
|                         FUENTES DE NOTICIAS                           |
|            [ RSS Feeds ]     [ Web Portals ]     [ APIs ]             |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
|                              GRILL-API                                |
|  +-------------------+  +--------------------+  +-------------------+ |
|  | Extractor / Ingesta|  | Modulo de Auth     |  | Pipeline NLP /    | |
|  | (Data Collector)  |  | (Secrets & Session)|  | Sentiment Engine  | |
|  +-------------------+  +--------------------+  +-------------------+ |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
|                          INTERFAZ STREAMLIT                           |
|        [ Dashboard Web ]  <--->  https://grill-api.streamlit.app/     |
+-----------------------------------------------------------------------+
```

---

## 🛠️ Stack Tecnológico

- **Core Engine:** Python 3.10+
- **Frontend / Dashboard:** Streamlit
- **Procesamiento de Datos:** Pandas, NumPy
- **NLP & Analítica:** NLTK, TextBlob, SpaCy *(según módulo)*
- **Visualización:** Plotly, Altair
- **Infraestructura:** Streamlit Cloud Container Runtime

---

## ⚙️ Instalación y Ejecución Local

### Prerrequisitos

- Python 3.10 o superior
- `pip` y `virtualenv`

### 1. Clonar el repositorio

```bash
git clone https://github.com/johnathanacortesd/Grill-API.git
cd Grill-API
```

### 2. Configurar el entorno virtual

```bash
# Crear entorno virtual
python -m venv venv

# Activar en Linux/macOS
source venv/bin/activate

# Activar en Windows
# venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
```

### 3. Configuración de Secretos

Cree la carpeta `.streamlit` y el archivo `secrets.toml` dentro de la raíz del proyecto para definir la contraseña de acceso local:

```bash
mkdir -p .streamlit
cat <<EOF > .streamlit/secrets.toml
password = "tu_contrasena_local"
EOF
```

### 4. Ejecutar la aplicación

```bash
streamlit run app.py
```

La aplicación estará disponible en `http://localhost:8501`.

### 5. Alternativa Colab (si Streamlit Cloud se queda en Etiquetando k/N)

Abra **`Grill_API_Colab.txt`** en Google Colab (*Runtime → Run all*). Es un script **autosuficiente**: no importa `app.py` ni ningún otro archivo.

- `pip install -q` de las dependencias (sin Streamlit).
- `OPENAI_API_KEY` **solo** desde Colab Secrets (`userdata.get`) o la variable de entorno. No hace falta pegarla.
- Interfaz Gradio: xlsx (+ PKL opcionales de tema/tono) → xlsx con `Contexto analizado`, `Tono IA`, `Tema`, `Subtema` y `Grupo noticia`.
- Modelo **hardcoded** `gpt-4.1-nano-2025-04-14` (se ignora `OPENAI_CLASIF_MODEL`). Embeddings: `text-embedding-3-small`.
- Subtemas: heurística para todos los grupos + pulido LLM en lotes de 25–40 (un dossier de ~300 filas hace como máximo ~8 ChatCompletions de subtema).

---

## ☁️ Despliegue en Producción

Para desplegar esta aplicación en **Streamlit Community Cloud**:

1. Vincule el repositorio `johnathanacortesd/Grill-API`.
2. Configure el archivo de inicio como `app.py`.
3. En la sección **Advanced Settings -> Secrets**, agregue la variable de entorno correspondiente a la contraseña:

```toml
password = "tu_contrasena_de_produccion"
```

---

## 📄 Licencia

Este proyecto está distribuido bajo la licencia **MIT**. Para más detalles, consulte el archivo [LICENSE](LICENSE).

---

**Mantenedor:** [Johnathan A. Cortés D.](https://github.com/johnathanacortesd)
```
