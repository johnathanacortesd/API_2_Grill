# ======================================
# Importaciones
# ======================================
import html
import io
import json
import logging
import re
import time
import streamlit as st
import pandas as pd

from catalogo_tono_tema import CRITERIOS_TONO
from perfil_cliente import cargar_perfil, guardar_perfil, listar_perfiles, perfil_a_resumen, slugify
from pipeline import process_dossier
from pkl_classifier import PklClassifierError, load_sklearn_estimator

logger = logging.getLogger("limpieza_grill")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

# ======================================
# CSS Personalizado · Tema oscuro
# ======================================
THEME_OSCURO = """
:root,[data-testid="stApp"]{
    color-scheme:dark;
    --bg:#000000;--s1:#0b0b0c;--s2:#111113;--s3:#17171a;
    --border:#1e1e22;--border2:#2b2b31;--border-focus:#ff4d1c;
    --text:#f2f0eb;--text2:#a8a8b0;--text3:#6f6f78;--text4:#48484f;--text-label:#f2f0eb;
    --acento:#ff4d1c;--acento-hover:#ff6a3d;--acento-soft:rgba(255,77,28,0.10);--acento-line:rgba(255,77,28,0.45);
    --green:#3ddc97;--green-bg:rgba(61,220,151,0.07);--green-bdr:rgba(61,220,151,0.28);
    --red:#f87171;--amber:#fbbf24;--blue:#7aa2f7;
    --success-bg:rgba(61,220,151,0.06);
    --success-title:#3ddc97;
    --r:6px;--r2:8px;--r3:12px;
    --shadow-sm:0 1px 2px rgba(0,0,0,0.6);
    --shadow-md:0 6px 20px rgba(0,0,0,0.65);
    --transition:all 0.15s ease-out;
}
"""


def load_custom_css():
    theme_vars = THEME_OSCURO
    dark_extra = ""
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Google+Sans+Text:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
""" + theme_vars + dark_extra + """
html,body,[data-testid="stApp"]{
    background:var(--bg)!important;color:var(--text)!important;
    font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    font-size:14px;-webkit-font-smoothing:antialiased;letter-spacing:0.002em;
}
[data-testid="stApp"]{
    background:var(--bg)!important;
}
::selection{background:rgba(255,77,28,0.28);color:#fff;}
#MainMenu,footer,header{visibility:hidden}.stDeployButton{display:none}
.block-container{padding-top:1.25rem!important;padding-bottom:0!important;max-width:1100px!important}
[data-testid="stAppViewBlockContainer"]{padding-top:1.25rem!important}
[data-testid="stStatusWidget"]{background:var(--s2)!important;color:var(--text2)!important;border:1px solid var(--border)!important;}
.app-header{background:#0b0b0c;border:1px solid var(--border);border-radius:var(--r3);padding:1rem 1.4rem;margin-bottom:1.1rem;display:flex;align-items:center;gap:0.9rem;box-shadow:var(--shadow-md);position:relative;overflow:hidden;}
.app-header::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--acento-line),transparent);}
.app-header-icon{width:38px;height:38px;background:#000000;border:1px solid var(--acento-line);border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:1.1rem;color:var(--acento);flex-shrink:0;box-shadow:none;}
.app-header-text{flex:1}
.app-header-title{font-family:'Inter',sans-serif;font-size:1.2rem;font-weight:700;color:var(--text);letter-spacing:-0.015em;line-height:1.3}
.app-header-version{font-family:'Roboto Mono',monospace;font-size:0.64rem;color:var(--text3);letter-spacing:0.14em;margin-top:0.2rem;text-transform:uppercase}
.app-header-badge{background:var(--acento-soft);border:1px solid var(--acento-line);color:var(--acento);font-family:'Roboto Mono',monospace;font-size:0.62rem;font-weight:500;padding:0.28rem 0.75rem;border-radius:6px;letter-spacing:0.12em;text-transform:uppercase;white-space:nowrap;}
.metrics-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0.75rem;margin:0.9rem 0}
.metric-card{background:var(--s1);border:1px solid var(--border);border-radius:var(--r2);padding:0.85rem 0.6rem;text-align:center;box-shadow:var(--shadow-sm);position:relative;overflow:hidden;}
.metric-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;border-radius:var(--r2) var(--r2) 0 0}
.metric-card.m-total::before{background:#6d6d7a}
.metric-card.m-unique::before{background:var(--green)}
.metric-card.m-dup::before{background:var(--amber)}
.metric-card.m-time::before{background:var(--acento)}
.metric-val{font-family:'Inter',sans-serif;font-size:1.55rem;font-weight:700;line-height:1;margin-bottom:0.3rem;letter-spacing:-0.02em;color:var(--text)}
.metric-lbl{font-family:'Roboto Mono',monospace;font-size:0.6rem;color:var(--text3);text-transform:uppercase;letter-spacing:0.12em;font-weight:500}
[data-testid="stForm"]{background:var(--s1)!important;border:1px solid var(--border)!important;border-radius:var(--r3)!important;padding:1.25rem 1.5rem!important;box-shadow:var(--shadow-md)!important;}
.sec-label{font-family:'Roboto Mono',monospace;font-size:0.68rem;font-weight:500;color:var(--text2);letter-spacing:0.16em;text-transform:uppercase;padding-bottom:0.35rem;border-bottom:1px solid var(--border);margin:0.9rem 0 0.6rem;display:flex;align-items:center;gap:0.55rem;}
.sec-label::before{content:'';display:inline-block;width:3px;height:13px;background:var(--acento);border-radius:1px;box-shadow:none}
.upload-zone{display:grid;grid-template-columns:1fr;gap:0.6rem;margin:0.3rem 0}
.upload-zone-card{background:var(--s2);border:1px dashed var(--border2);border-radius:var(--r2);padding:0.65rem 0.85rem;display:flex;align-items:center;gap:0.65rem;transition:var(--transition);}
.upload-zone-card:hover{border-color:var(--acento-line);background:#111113}
.upload-zone-icon{width:34px;height:34px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0;background:var(--s1);border:1px solid var(--border);color:var(--text2)}
.upload-zone-text{flex:1;min-width:0}
.upload-zone-title{font-family:'Inter',sans-serif;font-size:0.84rem;font-weight:600;color:var(--text);line-height:1.2}
.upload-zone-desc{font-size:0.72rem;color:var(--text3);line-height:1.35}
[data-testid="stFileUploader"]{background:var(--s2)!important;border:1px dashed var(--border2)!important;border-radius:var(--r)!important;padding:0.45rem 0.65rem!important;transition:var(--transition)!important;min-height:auto!important;}
[data-testid="stFileUploader"]:hover{border-color:var(--acento-line)!important;background:#111113!important;}
[data-testid="stFileUploader"] section{padding:0.2rem!important}
[data-testid="stFileUploader"] section>div{font-size:0.78rem!important;color:var(--text2)!important}
[data-testid="stFileUploader"] section small{font-size:0.7rem!important;color:var(--text3)!important}
[data-testid="stFileUploader"] button{background:var(--s1)!important;border:1px solid var(--border2)!important;color:var(--text)!important;font-weight:500!important;font-size:0.75rem!important;border-radius:6px!important;padding:0.3rem 0.85rem!important;font-family:'Inter',sans-serif!important;transition:var(--transition)!important;}
[data-testid="stFileUploader"] button:hover{background:var(--acento)!important;color:#1c0b06!important;border-color:var(--acento)!important}
[data-testid="stTextInput"] input,[data-testid="stNumberInput"] input{background:#000000!important;border:1px solid var(--border2)!important;color:var(--text)!important;border-radius:var(--r)!important;font-family:'Inter',sans-serif!important;font-size:0.9rem!important;padding:0.5rem 0.75rem!important;transition:var(--transition)!important;}
[data-testid="stTextInput"] input:focus,[data-testid="stNumberInput"] input:focus{border-color:var(--acento)!important;box-shadow:0 0 0 3px rgba(255,77,28,0.15)!important;outline:none!important;}
label[data-testid="stWidgetLabel"] p{font-family:'Inter',sans-serif!important;color:var(--text-label)!important;font-size:0.82rem!important;font-weight:600!important;margin-bottom:0.15rem!important;}
[data-testid="stTextInput"] input::placeholder,[data-baseweb="input"]::placeholder{color:var(--text4)!important;opacity:1!important;}
.stButton>button,[data-testid="stDownloadButton"]>button{background:var(--s2)!important;border:1px solid var(--border2)!important;color:var(--text)!important;border-radius:8px!important;font-family:'Inter',sans-serif!important;font-weight:500!important;font-size:0.88rem!important;transition:var(--transition)!important;padding:0.5rem 1.2rem!important;box-shadow:var(--shadow-sm)!important;}
.stButton>button:hover,[data-testid="stDownloadButton"]>button:hover{border-color:var(--acento-line)!important;background:#17171a!important;color:var(--text)!important;}
.stButton>button[kind="primary"],[data-testid="stDownloadButton"]>button[kind="primary"]{background:var(--acento)!important;border:1px solid var(--acento)!important;color:#1c0b06!important;font-weight:700!important;font-size:0.9rem!important;padding:0.55rem 1.4rem!important;border-radius:8px!important;box-shadow:none!important;letter-spacing:0.02em}
.stButton>button[kind="primary"]:hover,[data-testid="stDownloadButton"]>button[kind="primary"]:hover{background:#ff6a3d!important;border-color:#ff6a3d!important;color:#1c0b06!important;}
.success-banner{background:var(--success-bg);border:1px solid var(--green-bdr);border-left:3px solid var(--green);border-radius:var(--r2);padding:0.8rem 1.1rem;margin:0.5rem 0 0.8rem;display:flex;align-items:center;gap:0.8rem;}
.success-icon{width:32px;height:32px;background:rgba(61,220,151,0.15);border:1px solid var(--green-bdr);border-radius:50%;display:flex;align-items:center;justify-content:center;color:var(--green);font-size:0.95rem;flex-shrink:0;}
.success-title{font-family:'Inter',sans-serif;font-size:0.95rem;font-weight:700;color:var(--success-title);margin-bottom:0.1rem}
.success-sub{font-size:0.8rem;color:var(--text2)}
.auth-wrap{max-width:380px;margin:10vh auto 0;text-align:center}
.auth-icon{width:56px;height:56px;background:#000000;border:1px solid var(--acento-line);border-radius:12px;display:inline-flex;align-items:center;justify-content:center;font-size:1.5rem;color:var(--acento);margin-bottom:1rem;box-shadow:none;}
.auth-title{font-family:'Inter',sans-serif;font-size:1.35rem;font-weight:700;color:var(--text);margin-bottom:0.3rem;letter-spacing:-0.01em}
.auth-sub{font-size:0.85rem;color:var(--text3);margin-bottom:2rem}
[data-testid="stProgressBar"]>div>div{background:var(--acento)!important;border-radius:4px!important;height:6px!important;box-shadow:none!important;}
[data-testid="stDataFrame"]{border:1px solid var(--border)!important;border-radius:var(--r2)!important;box-shadow:var(--shadow-sm)!important;overflow:hidden!important;}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#2b2b38;border-radius:5px;border:2px solid var(--bg)}
::-webkit-scrollbar-thumb:hover{background:#3a3a4a}
.footer{font-family:'Roboto Mono',monospace;font-size:0.6rem;color:var(--text4);text-align:center;padding:0.9rem 0 0.6rem;letter-spacing:0.14em;text-transform:uppercase;border-top:1px solid var(--border);margin-top:1.2rem;}
.stElementContainer{margin-bottom:0!important}
[data-testid="stVerticalBlock"]>div{gap:0.35rem!important}
[data-testid="stHorizontalBlock"]>div{gap:0.5rem!important}
hr{border-color:var(--border)!important;margin:0.6rem 0!important}
.config-badge{display:inline-flex;align-items:center;gap:0.4rem;background:var(--s2);border:1px solid var(--border);border-radius:6px;padding:0.25rem 0.7rem;font-family:'Roboto Mono',monospace;font-size:0.6rem;color:var(--text3);margin-bottom:0.6rem;letter-spacing:0.08em;}
.live-panel{background:#0b0b0c;border:1px solid var(--border2);border-radius:var(--r3);padding:1.2rem 1.4rem;margin:1.5rem auto;max-width:720px;box-shadow:var(--shadow-md);position:relative;overflow:hidden;}
.live-panel::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--acento-line),transparent);}
.live-head{display:flex;align-items:center;gap:0.75rem;margin-bottom:0.8rem;}
.live-pulse{width:10px;height:10px;border-radius:50%;background:var(--acento);box-shadow:none;animation:livePulse 1.6s ease-out infinite;flex-shrink:0;}
@keyframes livePulse{0%{box-shadow:0 0 0 0 rgba(255,77,28,0.45)}70%{box-shadow:0 0 0 10px rgba(255,77,28,0)}100%{box-shadow:0 0 0 0 rgba(255,77,28,0)}}
.live-title{font-family:'Roboto Mono',monospace;font-size:0.95rem;font-weight:500;color:var(--text);line-height:1.25;letter-spacing:0.18em;text-transform:uppercase}
.live-sub{font-size:0.78rem;color:var(--text3);margin-top:0.25rem}
.live-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:0.6rem;margin:0.5rem 0 0.8rem}
.live-metric{background:var(--s2);border:1px solid var(--border);border-radius:var(--r);padding:0.6rem 0.5rem;text-align:center}
.live-metric-val{font-family:'Inter',sans-serif;font-size:1.15rem;font-weight:700;color:var(--acento);line-height:1.1}
.live-metric-lbl{font-family:'Roboto Mono',monospace;font-size:0.56rem;color:var(--text3);text-transform:uppercase;letter-spacing:0.12em;margin-top:0.2rem}
.step-list{display:flex;flex-direction:column;gap:0.3rem;margin:0.2rem 0 0.7rem}
.step-item{display:flex;align-items:center;gap:0.55rem;font-size:0.82rem;color:var(--text3);padding:0.22rem 0.1rem}
.step-item .dot{width:18px;height:18px;border-radius:50%;border:1.5px solid var(--border2);display:flex;align-items:center;justify-content:center;font-size:0.62rem;flex-shrink:0;background:var(--s1);color:transparent}
.step-item.is-done{color:var(--text2)}
.step-item.is-done .dot{background:rgba(61,220,151,0.18);border-color:var(--green);color:var(--green)}
.step-item.is-active{color:var(--text);font-weight:600}
.step-item.is-active .dot{border-color:var(--acento);color:var(--acento);box-shadow:none;animation:livePulse 1.6s ease-out infinite}
.live-hint{background:var(--acento-soft);border:1px solid var(--acento-line);color:#ffb59d;border-radius:var(--r);padding:0.55rem 0.75rem;font-size:0.78rem;line-height:1.4}
.live-detail{font-size:0.8rem;color:var(--text2);margin-top:0.5rem;font-family:'Inter',sans-serif}
.pkl-hint{font-size:0.78rem;color:var(--text3);margin:0.15rem 0 0.55rem;line-height:1.4}
div[data-testid="stAlert"]{border-radius:var(--r2)!important}
[data-testid="stAlert"]>div{background:var(--s2)!important;color:var(--text2)!important;}
[data-testid="stCheckbox"] p,[data-testid="stToggle"] p{color:var(--text-label)!important}
[role="radiogroup"] label p,[data-testid="stRadio"] label p{color:var(--text-label)!important;font-size:0.85rem!important;}
[data-baseweb="select"]>div{background:#000000!important;color:var(--text)!important;border-color:var(--border2)!important;border-radius:6px!important;}
[data-baseweb="select"]>div:focus-within{border-color:var(--acento)!important;box-shadow:0 0 0 3px rgba(255,77,28,0.15)!important;}
div[data-baseweb="popover"] div[role="listbox"],ul[data-baseweb="menu"]{background:#111113!important;border:1px solid var(--border2)!important;}
ul[data-baseweb="menu"] li{color:var(--text2)!important;}
ul[data-baseweb="menu"] li:hover,ul[data-baseweb="menu"] li[aria-selected="true"]{background:var(--acento-soft)!important;color:var(--text)!important;}
[data-baseweb="input"]{background:#000000!important;color:var(--text)!important}
[data-baseweb="textarea"]{background:#000000!important;color:var(--text)!important;border-color:var(--border2)!important;border-radius:6px!important;}
[data-baseweb="textarea"]:focus-within{border-color:var(--acento)!important;box-shadow:0 0 0 3px rgba(255,77,28,0.15)!important;}
.stMarkdown,.stCaption{color:var(--text2)}
.stMarkdown a{color:var(--acento)!important;}
[data-testid="stExpander"]{background:var(--s1)!important;border:1px solid var(--border)!important;border-radius:var(--r2)!important;}
[data-testid="stExpander"] summary{color:var(--text2)!important;}
code{color:var(--acento)!important;background:var(--acento-soft)!important;}
@media(max-width:768px){
    .metrics-grid{grid-template-columns:repeat(2,1fr)}
    .live-metrics{grid-template-columns:1fr 1fr 1fr}
    .app-header{flex-direction:column;text-align:center;gap:0.5rem;padding:1rem}
}
</style>
""", unsafe_allow_html=True)

# ======================================
# Autenticación Básica
# ======================================
def check_password():
    if st.session_state.get("password_correct", False):
        return True
    configurada = bool(st.secrets.get("APP_PASSWORD"))
    st.markdown("""
    <div class="auth-wrap">
        <div class="auth-icon">◈</div>
        <div class="auth-title">Sistema de Limpieza y Análisis</div>
        <div class="auth-sub">Ingresa tus credenciales para continuar</div>
    </div>""", unsafe_allow_html=True)
    if not configurada:
        st.warning("⚠️ No hay APP_PASSWORD en los Secrets: configura una contraseña antes de "
                   "publicar la app (Streamlit Cloud → Settings → Secrets).")
    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("pw"):
            pw = st.text_input("Contraseña", type="password", placeholder="Ingresa tu contraseña")
            if st.form_submit_button("Ingresar", use_container_width=True, type="primary"):
                if configurada and pw == st.secrets.get("APP_PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta")
    return False

# ======================================
# Configuración vía Google Sheets
# ======================================
CONFIG_CACHE_TTL = 300

@st.cache_data(ttl=CONFIG_CACHE_TTL, show_spinner=False)
def _fetch_map_from_csv(csv_url: str) -> dict:
    df = pd.read_csv(csv_url, header=None, dtype=str)
    df = df.dropna(how="all")
    mapping = pd.Series(
        df.iloc[:, 1].values,
        index=df.iloc[:, 0].astype(str).str.lower().str.strip()
    ).to_dict()
    mapping = {k: v for k, v in mapping.items() if k not in ("nan", "")}
    return mapping

def load_config_from_sheets():
    regiones_url = st.secrets.get("REGIONES_CSV_URL")
    internet_url = st.secrets.get("INTERNET_CSV_URL")

    if not regiones_url or not internet_url:
        st.error("❌ Faltan REGIONES_CSV_URL e INTERNET_CSV_URL en st.secrets.")
        st.stop()

    try:
        region_map = _fetch_map_from_csv(regiones_url)
        internet_map = _fetch_map_from_csv(internet_url)
    except Exception as e:
        st.error(f"❌ No se pudo leer la configuración desde Google Sheets: {e}")
        st.stop()

    return region_map, internet_map

def refresh_config_cache():
    _fetch_map_from_csv.clear()

# ======================================
# Proceso Principal
# ======================================
PIPELINE_STEPS = [
    ("config", "Cargar configuración"),
    ("read", "Leer el Excel"),
    ("norm", "Limpiar y normalizar"),
    ("dups", "Menciones y duplicadas"),
    ("ai", "Análisis IA (Tono, Tema, Subtema)"),
    ("export", "Generar archivo de resultado"),
]

def _fmt_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60:02d} s"

def _fmt_size(n_bytes: int) -> str:
    if not n_bytes: return ""
    mb = n_bytes / (1024 * 1024)
    if mb < 0.1: return f"{n_bytes / 1024:.0f} KB"
    return f"{mb:.1f} MB"

def _active_step(pct: int, msg: str) -> str:
    if pct >= 100 or "completad" in msg.lower():
        return "done"
    if pct >= 94 or "Generando archivo" in msg or "Guardando" in msg:
        return "export"
    if pct >= 70 or "IA" in msg or "Analizando" in msg or "semántica" in msg:
        return "ai"
    if pct >= 55 or "duplicad" in msg.lower() or "Expandiendo" in msg:
        return "dups"
    if pct >= 40 or "Normaliz" in msg or "Columnas" in msg:
        return "norm"
    if pct >= 8 or "Excel" in msg or "Leyendo" in msg:
        return "read"
    return "config"

def _render_live_html(pct, msg, elapsed, file_label, active_key):
    steps_html = []
    reached_active = False
    for key, label in PIPELINE_STEPS:
        if active_key == "done":
            cls, mark = "is-done", "✓"
        elif key == active_key:
            cls, mark = "is-active", "●"
            reached_active = True
        elif not reached_active:
            cls, mark = "is-done", "✓"
        else:
            cls, mark = "", ""
        steps_html.append(f'<div class="step-item {cls}"><span class="dot">{mark}</span>{label}</div>')
        
    file_line = f" · {html.escape(file_label)}" if file_label else ""
    title = "Limpieza completada" if active_key == "done" else "Procesando dossier de noticias"
    safe_msg = html.escape(str(msg or ""))
    
    return f"""
    <div class="live-panel">
      <div class="live-head">
        <div class="live-pulse"></div>
        <div>
          <div class="live-title">{title}</div>
          <div class="live-sub">El proceso sigue activo{file_line}. No cierres esta pestaña.</div>
        </div>
      </div>
      <div class="live-metrics">
        <div class="live-metric"><div class="live-metric-val">{int(pct)}%</div><div class="live-metric-lbl">Avance</div></div>
        <div class="live-metric"><div class="live-metric-val">{elapsed}</div><div class="live-metric-lbl">Tiempo</div></div>
        <div class="live-metric"><div class="live-metric-val">en curso</div><div class="live-metric-lbl">Estado</div></div>
      </div>
      <div class="step-list">{''.join(steps_html)}</div>
      <div class="live-hint">La deduplicación previa agrupa notas idénticas para procesar hasta 2.000 filas con alta velocidad.</div>
      <div class="live-detail">{safe_msg}</div>
    </div>
    """

def run_cleaning_process(df_file, file_meta=None, ai_config=None):
    file_meta = file_meta or {}
    file_label = file_meta.get("name", "")
    size_lbl = _fmt_size(file_meta.get("size") or 0)
    if file_label and size_lbl:
        file_label = f"{file_label} ({size_lbl})"

    t_start = time.time()
    panel = st.empty()
    progress_bar = st.progress(0, text="Iniciando…")

    def paint(pct, msg):
        elapsed = _fmt_elapsed(time.time() - t_start)
        active = _active_step(pct, msg)
        panel.markdown(_render_live_html(pct, msg, elapsed, file_label, active), unsafe_allow_html=True)
        progress_bar.progress(min(100, max(0, int(pct))), text=msg)

    paint(1, "Cargando configuración…")

    with st.status("Procesando dossier…", expanded=True) as status_widget:
        def on_progress(pct, msg):
            paint(pct, msg)
            status_widget.update(label=f"{int(pct)}% · {msg}")

        try:
            region_map, internet_map = load_config_from_sheets()
            result = process_dossier(
                df_file,
                region_map,
                internet_map,
                progress=on_progress,
                ai_config=ai_config
            )
            paint(100, "Limpieza completada")
            status_widget.update(label="✓ Limpieza completada con éxito", state="complete")
            time.sleep(0.4)
        except Exception as exc:
            logger.exception("Fallo en el proceso de limpieza")
            status_widget.update(label="Error durante el procesamiento", state="error")
            st.error(f"El proceso se interrumpió: {exc}")
            raise

    st.session_state["medios_sin_mapear"] = result.get("medios_sin_mapear") or None
    st.session_state["analisis"] = result.get("analisis") or {}
    st.session_state["output_data"] = result["output_data"]
    st.session_state["output_filename"] = result["output_filename"]
    st.session_state["processing_complete"] = True
    st.session_state.update({
        "total_rows": result["total_rows"],
        "unique_rows": result["unique_rows"],
        "duplicates": result["duplicates"],
        "process_duration": result["process_duration"],
    })
    if ai_config and ai_config.get("brand"):
        st.session_state["ai_config"] = ai_config
    if result.get("analisis"):
        st.session_state["analisis_usado"] = result["analisis"]


# ======================================
# Interfaz de Usuario
# ======================================
def main():
    st.set_page_config(
        page_title="Limpieza y Análisis de Noticias",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    load_custom_css()
    if not check_password(): return

    _procesando = bool(st.session_state.get("procesando"))

    # `ui` es el contenedor raíz de la vista. Durante el procesamiento queda
    # vacío a propósito: al crearse en la misma posición del script reemplaza
    # de inmediato el contenido anterior (encabezado + formulario), sin depender
    # de que el frontend limpie los elementos de la vista previa.
    ui = st.empty()

    if _procesando:
        # Solo se muestra la tarjeta "Procesando dossier de noticias", que se
        # dibuja más abajo junto a la barra de progreso. Nada más.
        pass
    elif not st.session_state.get("processing_complete", False):
        with ui.container():
            st.markdown("""
            <div class="app-header">
                <div class="app-header-icon">◈</div>
                <div class="app-header-text">
                    <div class="app-header-title">Limpieza y Análisis de Noticias</div>
                    <div class="app-header-version">v4.5 · Tono/Tema/Subtema por reglas + IA · Realizado por Johnathan Cortés</div>
                </div>
                <div class="app-header-badge">Estructurador + IA</div>
            </div>""", unsafe_allow_html=True)
            col_cfg1, col_cfg2 = st.columns([4, 1])
            with col_cfg1:
                st.markdown(
                    '<span class="config-badge">⚙ Configuración: Google Sheets (Regiones / Internet)</span>',
                    unsafe_allow_html=True
                )
            with col_cfg2:
                if st.button("↻ Refrescar config", use_container_width=True):
                    refresh_config_cache()
                    st.success("Config recargada")

            # --- Perfil de cliente (multicliente): precarga marca, alias, voceros,
            #     criterio de tono y lista de Temas guardados para cada cliente. ---
            _SIN_PERFIL = "Sin perfil (configuración manual)"
            try:
                _perfiles = listar_perfiles()
            except Exception:
                _perfiles = []
            _opciones_perfil = [_SIN_PERFIL] + [p["nombre"] for p in _perfiles]
            sel_perfil = st.selectbox(
                "Perfil de cliente",
                _opciones_perfil,
                help="Carga la configuración guardada del cliente. Puedes editar los campos "
                     "abajo antes de procesar; también puedes guardar la configuración actual "
                     "como un perfil nuevo desde 'Ajustes finos'.",
            )
            _perfil_actual = None
            if sel_perfil != _SIN_PERFIL:
                _pid = next((p["id"] for p in _perfiles if p["nombre"] == sel_perfil), None)
                if _pid:
                    _perfil_actual = cargar_perfil(_pid)
                    if st.session_state.get("_perfil_aplicado") != sel_perfil:
                        st.session_state["brand_input"] = _perfil_actual.get("brand", "")
                        st.session_state["alias_input"] = "; ".join(_perfil_actual.get("aliases", []))
                        st.session_state["voceros_input"] = "; ".join(_perfil_actual.get("voceros", []))
                        st.session_state["criterio_input"] = (
                            _perfil_actual.get("criterio") or list(CRITERIOS_TONO)[0])
                        st.session_state["criterio_custom_input"] = _perfil_actual.get("criterio_custom", "")
                        st.session_state["_perfil_taxonomia"] = _perfil_actual.get("taxonomia")
                        st.session_state["_perfil_aplicado"] = sel_perfil
                        st.rerun()
                    st.caption("📋 " + perfil_a_resumen(_perfil_actual))
            else:
                st.session_state["_perfil_aplicado"] = None
                st.session_state["_perfil_taxonomia"] = None

            with st.form("main_form"):
                st.markdown('<div class="sec-label">1. Sube el archivo de entrada</div>', unsafe_allow_html=True)
                st.markdown("""
                <div class="upload-zone">
                    <div class="upload-zone-card">
                        <div class="upload-zone-icon uz-dossier">📋</div>
                        <div class="upload-zone-text">
                            <div class="upload-zone-title">Dossier de Noticias</div>
                            <div class="upload-zone-desc">Sube el .xlsx con las columnas Título y Resumen - Aclaracion.</div>
                        </div>
                    </div>
                </div>""", unsafe_allow_html=True)
            
                f1 = st.file_uploader("Dossier", type=["xlsx"], label_visibility="collapsed", key="f1")

                st.markdown('<div class="sec-label">2. Configuración de Análisis IA (Tono, Tema, Subtema)</div>', unsafe_allow_html=True)
                enable_ai = st.checkbox("Activar análisis reputacional con IA + Jev para el tono", value=True)
            
                c_brand, c_alias = st.columns(2)
                with c_brand:
                    brand_input = st.text_input(
                        "Marca o Cliente Principal*",
                        placeholder="Ej: Universidad de Antioquia, Ecopetrol, Bancolombia",
                        help="El tono se mide solo sobre esta marca, sus voceros y sus alias.",
                        key="brand_input",
                    )
                with c_alias:
                    alias_input = st.text_input(
                        "Alias o términos relacionados (separados por coma o punto y coma)",
                        placeholder="Ej: UdeA; Alma Mater; rectoría; la universidad",
                        help="Variantes del nombre que deban atribuirse al cliente.",
                        key="alias_input",
                    )

                c_crit, c_voc = st.columns([3, 2])
                with c_crit:
                    criterio = st.radio(
                        "Criterio del tono",
                        list(CRITERIOS_TONO.keys()),
                        index=0,
                        horizontal=False,
                        key="criterio_input",
                        help=("Aspectual estricto: la crítica dirigida a la marca es lo único Negativo "
                              "(gobiernos, alcaldías, entidades públicas). Favorabilidad del sector: "
                              "cuenta cómo queda parado el sector aunque la marca no sea el actor (gremios, "
                              "cámaras, empresas de un sector)."),
                    )
                with c_voc:
                    voceros_input = st.text_input(
                        "Vocero(s) de la marca (opcional)",
                        placeholder="Ej: Gonzalo Moreno; el rector",
                        help="Personas cuyo nombre se atribuye a la marca para el tono.",
                        key="voceros_input",
                    )
                    tax_nombre = st.selectbox(
                        "Lista de Temas",
                        ["Automática según el archivo (recomendada)",
                         "Gobierno territorial (21 cubos)",
                         "Gremio o sector (16 cubos)"],
                        index=0,
                        help="Por defecto los Temas se arman bottom-up en ESTE lote: se agrupan subtemas "
                             "afines y se nombra cada familia. Si subes un PKL de tema, se usan las clases "
                             "de ese modelo y no se inventan temas del lote. No hay memoria entre corridas. "
                             "Las listas fijas o un JSON solo se usan como nombres candidatos cuando no hay PKL.",
                    )

                with st.expander("⚙ Ajustes finos del análisis (opcional)"):
                    ca, cb, cc, cd = st.columns(4)
                    with ca:
                        tam_lote_input = st.slider("Grupos por llamada", 5, 30, 10, 1,
                                                   help="Con gpt-4.1-nano 10 funciona mejor.")
                    with cb:
                        workers_input = st.slider("Llamadas en paralelo", 1, 8, 4, 1,
                                                  help="Sube para dossiers grandes; más hilos, más velocidad.")
                    with cc:
                        umbral_titulo_input = st.slider("Similitud de titulares (%)", 75, 100, 92, 1,
                                                        help="Bájalo para fusionar la misma noticia publicada por "
                                                             "muchos medios con titulares distintos.")
                    with cd:
                        umbral_cuerpo_input = st.slider("Similitud de resúmenes (%)", 70, 100, 85, 1)
                    tax_file = st.file_uploader(
                        "Reutilizar la lista de Temas de un cliente (JSON, opcional)",
                        type=["json"], key="tax_json",
                        help="Si subes la lista que descargaste de un período anterior del mismo cliente, "
                             "los Temas se mantienen idénticos entre meses (mejor para comparar).",
                    )
                    cubos_objetivo_input = st.slider("Cubos objetivo cuando la lista es automática", 8, 25, 16, 1)
                    votos_input = st.slider(
                        "Verificaciones del tono por grupo", 1, 3, 2, 1,
                        help="Cada grupo se etiqueta N veces y gana la mayoría; un empate cae a Neutro. "
                             "Con 2 se reducen los vaivenes de los modelos pequeños; con 3 sube el costo "
                             "una vez más.")
                    st.markdown("**💾 Perfil de cliente**")
                    guardar_chk = st.checkbox(
                        "Guardar esta configuración como perfil al procesar",
                        help="Crea o actualiza el JSON del cliente (marca, alias, voceros, criterio y "
                             "lista de Temas si subiste un JSON) para reutilizarlo en próximas corridas.",
                    )
                    sobrescribir_chk = st.checkbox(
                        "Sobrescribir el perfil si ya existe uno con ese nombre",
                        help="Por seguridad, si el perfil ya existe y no marcas esta casilla, "
                             "el proceso se detiene con un aviso en lugar de reemplazarlo.",
                    )
                    nombre_perfil = st.text_input(
                        "Nombre del perfil",
                        key="nombre_perfil_input",
                        placeholder="Por defecto se usa la marca",
                    )
                    criterio_custom = st.text_area(
                        "Criterio de tono personalizado (opcional)",
                        key="criterio_custom_input",
                        height=80,
                        placeholder="Si lo llenas, este texto reemplaza al criterio del catálogo para este cliente.",
                        help="Texto libre con la regla de tono propia del cliente. Tiene prioridad sobre "
                             "el criterio seleccionado arriba.",
                    )

                st.markdown('<div class="sec-label">3. Modelos PKL del cliente (opcional)</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="pkl-hint">Puedes subir el PKL de tono, el de tema, ambos o ninguno. '
                    "Si un eje no tiene PKL, se mantiene el análisis actual (IA). "
                    "El subtema nunca se reemplaza por PKL.</div>",
                    unsafe_allow_html=True,
                )
                st.markdown("""
                <div class="upload-zone">
                    <div class="upload-zone-card">
                        <div class="upload-zone-icon uz-pkl">◆</div>
                        <div class="upload-zone-text">
                            <div class="upload-zone-title">Clasificadores sklearn (joblib)</div>
                            <div class="upload-zone-desc">Archivos .pkl con pipeline de texto (pasos tfidf + clf). No son obligatorios.</div>
                        </div>
                    </div>
                </div>""", unsafe_allow_html=True)
                c_tono, c_tema = st.columns(2)
                with c_tono:
                    f_tono = st.file_uploader(
                        "PKL de tono",
                        type=["pkl"],
                        key="pkl_tono",
                        help="Modelo opcional de scikit-learn para tono. Si no se sube, se usa la IA existente.",
                    )
                with c_tema:
                    f_tema = st.file_uploader(
                        "PKL de tema",
                        type=["pkl"],
                        key="pkl_tema",
                        help="Modelo opcional de scikit-learn para tema. Si no se sube, se usa la IA existente.",
                    )

                if st.form_submit_button("▶ Iniciar Limpieza y Análisis", use_container_width=True, type="primary"):
                    if not f1:
                        st.error("Por favor, sube un archivo Excel.")
                    elif enable_ai and not brand_input.strip():
                        st.error("Por favor indica la Marca o Cliente Principal para realizar el análisis enfocado.")
                    else:
                        api_key = st.secrets.get("OPENAI_API_KEY")
                        typesafe_api_key = st.secrets.get("TYPESAFE_API_KEY")
                        if enable_ai and not api_key:
                            st.error("❌ Falta configurar OPENAI_API_KEY en los Secrets de Streamlit: se usa para subtema y tema.")
                            st.stop()
                        if enable_ai and not typesafe_api_key:
                            st.warning("Sin TYPESAFE_API_KEY la verificación de temas con Jev queda desactivada "
                                       "(es opcional); el tono, el subtema y el tema se generan igual con la API de OpenAI.")
                    
                        aliases_parsed = [
                            a.strip() for a in re.split(r"[,;]", alias_input) if a.strip()
                        ]
                        tax_cargada = None
                        if tax_file is not None:
                            try:
                                tax_cargada = json.loads(tax_file.getvalue().decode("utf-8"))
                                if not isinstance(tax_cargada, dict) or not tax_cargada.get("temas"):
                                    raise ValueError("el JSON debe traer la clave 'temas' con la lista de cubos")
                                tax_cargada.setdefault("reglas", [])
                            except Exception as exc:
                                st.error(f"La lista de Temas (JSON) no es válida: {exc}")
                                st.stop()
                        tone_bytes = f_tono.getvalue() if f_tono else None
                        theme_bytes = f_tema.getvalue() if f_tema else None
                        try:
                            if tone_bytes:
                                load_sklearn_estimator(tone_bytes, "tono")
                            if theme_bytes:
                                load_sklearn_estimator(theme_bytes, "tema")
                        except PklClassifierError as exc:
                            st.error(str(exc))
                            st.stop()

                        st.session_state["pending_dossier"] = f1.getvalue()
                        st.session_state["pending_meta"] = {
                            "name": f1.name,
                            "size": int(getattr(f1, "size", 0) or len(st.session_state["pending_dossier"])),
                        }
                        criterio_texto = (criterio_custom or "").strip()
                        if guardar_chk:
                            _nombre_final = (nombre_perfil or "").strip() or brand_input.strip()
                            _pid_cand = slugify(_nombre_final)
                            _existe = any(p["id"] == _pid_cand for p in listar_perfiles())
                            if _existe and not sobrescribir_chk:
                                st.warning(
                                    "Ya existe un perfil llamado '%s'. Si quieres actualizarlo, "
                                    "marca 'Sobrescribir el perfil si ya existe' y procesa de nuevo; "
                                    "si es otro cliente, cambia el nombre del perfil." % _nombre_final
                                )
                                st.stop()
                            try:
                                _pid = guardar_perfil({
                                    "nombre": _nombre_final,
                                    "brand": brand_input.strip(),
                                    "aliases": aliases_parsed,
                                    "voceros": [v.strip() for v in re.split(r"[,;]", voceros_input) if v.strip()],
                                    "criterio": criterio,
                                    "criterio_custom": criterio_texto,
                                    "taxonomia": tax_cargada,
                                    "notas": "",
                                })
                                st.toast(f"Perfil de cliente guardado: {_pid}")
                            except ValueError as exc:
                                st.error(str(exc))
                                st.stop()
                        # Precedencia de la lista de Temas: JSON subido > opción elegida >
                        # taxonomía del perfil > automática del lote.
                        _TAX_AUTO = "Automática según el archivo (recomendada)"
                        _perfil_tax = st.session_state.get("_perfil_taxonomia")
                        if tax_cargada:
                            tax_eff = tax_cargada
                        elif tax_nombre != _TAX_AUTO:
                            tax_eff = tax_nombre
                        elif _perfil_tax:
                            tax_eff = _perfil_tax
                        else:
                            tax_eff = tax_nombre
                        if enable_ai or tone_bytes or theme_bytes:
                            st.session_state["pending_ai_config"] = {
                                "enabled": bool(enable_ai),
                                "brand": brand_input.strip(),
                                "aliases": aliases_parsed,
                                "voceros": [v.strip() for v in re.split(r"[,;]", voceros_input) if v.strip()],
                                "criterio": criterio,
                                "criterio_texto": criterio_texto,
                                "taxonomia": tax_eff,
                                "cubos_objetivo": int(cubos_objetivo_input),
                                "votos": int(votos_input),
                                "permitir_cubos_nuevos": True,
                                "tam_lote": int(tam_lote_input),
                                "workers": int(workers_input),
                                "umbral_titulo": int(umbral_titulo_input),
                                "umbral_cuerpo": int(umbral_cuerpo_input),
                                "api_key": api_key if enable_ai else None,
                                "typesafe_api_key": typesafe_api_key if enable_ai else None,
                                "typesafe_model": "jev-latest",
                                "model": "gpt-4.1-nano-2025-04-14",
                                "historial_dir": st.secrets.get("HISTORIAL_DIR"),
                                "tone_pkl_bytes": tone_bytes,
                                "theme_pkl_bytes": theme_bytes,
                            }
                        else:
                            st.session_state["pending_ai_config"] = None

                        st.session_state["procesando"] = True
                        st.session_state["processing_complete"] = False
                        st.rerun()
    else:
        with ui.container():
            total = st.session_state.total_rows
            uniq  = st.session_state.unique_rows
            dups  = st.session_state.duplicates
            dur   = st.session_state.process_duration
        
            st.markdown(
                '<div class="success-banner"><div class="success-icon">✓</div>'
                '<div><div class="success-title">Proceso completado</div>'
                '<div class="success-sub">El archivo estructurado y analizado con IA se encuentra listo para descargar</div></div></div>',
                unsafe_allow_html=True
            )

            medios_sin_mapear = st.session_state.get("medios_sin_mapear")
            if medios_sin_mapear:
                st.warning(
                    "⚠️ Medios sin región asignada en Sheets (quedaron N/A): "
                    f"{', '.join(medios_sin_mapear)}."
                )

            analisis = st.session_state.get("analisis") or {}
            if analisis:
                grupos = analisis.get("grupos")
                cubos_nuevos = analisis.get("cubos_nuevos") or []
                reglas = analisis.get("temas_por_regla")
                por_llm = analisis.get("temas_por_llm")
                por_pkl = analisis.get("temas_por_pkl")
                tonos_pkl = analisis.get("tonos_por_pkl")
                fallback = len(analisis.get("grupos_con_fallback") or [])
                errores = analisis.get("errores_api") or []
                guarda = len(analisis.get("tono_corregido_por_guarda") or [])
                votos = analisis.get("votos_tono")
                piezas = []
                if grupos:
                    piezas.append(f"{grupos} hechos únicos agrupados")
                if tonos_pkl:
                    piezas.append(f"tono clasificado con PKL del cliente ({tonos_pkl} grupos)")
                elif votos:
                    piezas.append(f"tono verificado {votos}× por grupo")
                if guarda:
                    piezas.append(f"guarda del tono: {guarda} Negativos sin señalamiento pasaron a Neutro")
                if por_pkl:
                    piezas.append(f"Tema por PKL del cliente: {por_pkl}")
                elif reglas is not None:
                    piezas.append(f"Tema por reglas: {reglas} · por IA: {por_llm or 0}")
                if cubos_nuevos:
                    piezas.append("Cubos nuevos específicos: " + ", ".join(cubos_nuevos[:4]))
                if fallback:
                    piezas.append(f"⚠️ {fallback} etiquetas con respaldo determinista")
                if piezas:
                    st.info("Análisis de Tono/Tema/Sub-tema · " + " · ".join(piezas))
                if errores:
                    st.caption("Avisos del modelo: " + " | ".join(map(str, errores[:2])))
                temas_gen = analisis.get("taxonomia") or []
                detalle_tax = analisis.get("taxonomia_detalle") or {}
                if temas_gen:
                    modo = analisis.get("modo_taxonomia")
                    if modo == "pkl":
                        etiqueta = "clases del PKL del cliente"
                    elif modo in ("lote", "automatica"):
                        etiqueta = "de este lote"
                    else:
                        etiqueta = "nombres candidatos del cliente"
                    titulo_exp = ("Temas del PKL (%d, %s)" % (len(temas_gen), etiqueta)
                                  if modo == "pkl"
                                  else "Temas de este lote (%d, %s)" % (len(temas_gen), etiqueta))
                    with st.expander(titulo_exp, expanded=(modo in ("lote", "automatica", "pkl"))):
                        st.markdown(" · ".join("`%s`" % t for t in temas_gen))
                        if detalle_tax:
                            st.download_button(
                                "⬇ Descargar Temas de este lote (JSON)",
                                data=json.dumps(detalle_tax, ensure_ascii=False, indent=1),
                                file_name="temas_%s.json" % str(
                                    st.session_state.get("output_filename", "cliente")).replace(".xlsx", ""),
                                mime="application/json",
                            )
                            if modo == "pkl":
                                st.caption("Son las clases del PKL de tema del cliente. No se inventan "
                                           "nombres bottom-up ni se reescriben con el quality-gate del lote.")
                            else:
                                st.caption("Son los Temas armados bottom-up en esta corrida. El próximo lote "
                                           "vuelve a agrupar sus propios subtemas; no se reutiliza el vocabulario.")
        
            st.markdown(f"""
            <div class="metrics-grid">
              <div class="metric-card m-total"><div class="metric-val" style="color:var(--text)">{total}</div><div class="metric-lbl">Total Registros</div></div>
              <div class="metric-card m-unique"><div class="metric-val" style="color:var(--green)">{uniq}</div><div class="metric-lbl">Únicos</div></div>
              <div class="metric-card m-dup"><div class="metric-val" style="color:var(--amber)">{dups}</div><div class="metric-lbl">Duplicados</div></div>
              <div class="metric-card m-time"><div class="metric-val" style="color:var(--blue)">{dur}</div><div class="metric-lbl">Tiempo de Ejecución</div></div>
            </div>""", unsafe_allow_html=True)
        
            _historial = []
            try:
                from historial_cliente import listar_historial
                _sl = (st.session_state.get("ai_config") or {}).get("brand", "") or \
                    (st.session_state.get("pending_ai_config") or {}).get("brand", "")
                if _sl:
                    _historial = listar_historial(_sl, extra=st.session_state.get("ai_config_extra") or {})
            except Exception:
                _historial = []
            if _historial:
                with st.expander(f"Historial del cliente ({len(_historial)} corridas previas)"):
                    for h in _historial[:10]:
                        st.markdown(f"- **{h.get('fecha','')}** · {h.get('unique_rows','')} únicas"
                                    f" · {h.get('total_rows','')} filas · {h.get('process_duration','')}s"
                                    f" · {len(h.get('taxonomia') or [])} temas · `{h.get('archivo','')}`")

            c1, c2 = st.columns(2)
            c1.download_button(
                "⬇ Descargar Xlsx Estructurado con IA",
                data=st.session_state.output_data,
                file_name=st.session_state.output_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary"
            )
            if c2.button("Nuevo análisis", use_container_width=True):
                pwd = st.session_state.get("password_correct")
                st.session_state.clear()
                st.session_state.password_correct = pwd
                st.rerun()

    if st.session_state.get("pending_dossier"):
        blob = st.session_state.pop("pending_dossier")
        meta = st.session_state.pop("pending_meta", {}) or {}
        ai_cfg = st.session_state.pop("pending_ai_config", None)
        try:
            run_cleaning_process(io.BytesIO(blob), meta, ai_config=ai_cfg)
        finally:
            st.session_state["procesando"] = False
        st.rerun()

    # El pie solo se muestra fuera del procesamiento.
    if not _procesando:
        st.markdown(
            '<div class="footer">Estructuración y Limpieza · Johnathan Cortés ©</div>',
            unsafe_allow_html=True
        )

if __name__ == "__main__":
    main()
