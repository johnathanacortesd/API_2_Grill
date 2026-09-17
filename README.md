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
