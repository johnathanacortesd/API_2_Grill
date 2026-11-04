#@title ◈ Grill API — Limpieza y Análisis de Noticias { display-mode: "form" }
#@markdown Ejecuta **la misma app** que Streamlit Cloud (`app.py`): mismas funciones, modelos y precisión.
#@markdown
#@markdown **Antes de ejecutar:** las claves deben estar en Colab *Secrets* (icono 🔑 a la izquierda),
#@markdown con *Acceso del cuaderno* activado: `APP_PASSWORD`, `OPENAI_API_KEY`, `TYPESAFE_API_KEY` (Jev),
#@markdown `REGIONES_CSV_URL`, `INTERNET_CSV_URL`.
#@markdown
#@markdown **⚠ Deje este cuaderno abierto mientras usa la app.** La app corre dentro de esta celda:
#@markdown si cierra la pestaña, detiene la celda o Colab desconecta la sesión (≈90 min sin actividad,
#@markdown máximo ≈12 h en la versión gratuita), la app se apaga y el enlace deja de funcionar.

#@markdown ---
#@markdown ### ⚙ Opciones
#@markdown Guardar perfiles de cliente e historial en Google Drive (no se pierden al cerrar Colab):
GUARDAR_EN_DRIVE = False  #@param {type:"boolean"}

import html
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = "johnathanacortesd/API_2_Grill"
RAMA = "main"
CARPETA = "/content/API_2_Grill"
CARPETA_DRIVE = "/content/drive/MyDrive/Grill_API"
PUERTO = 8501
LOG_APP = "/content/streamlit.log"
LOG_TUNEL = "/content/cloudflared.log"
CLOUDFLARED = "/content/cloudflared"
OBLIGATORIOS = ["APP_PASSWORD", "OPENAI_API_KEY", "TYPESAFE_API_KEY",
                "REGIONES_CSV_URL", "INTERNET_CSV_URL"]
OPCIONALES = ["HISTORIAL_DIR"]

# ------------------------------------------------------------------ interfaz
try:
    from IPython.display import HTML, display
    EN_NOTEBOOK = "ipykernel" in sys.modules
except Exception:
    EN_NOTEBOOK = False

ESTILO = """
<style>
.gr{font-family:Inter,system-ui,sans-serif;max-width:640px;color:#1f2328}
.gr .t{font-size:18px;font-weight:700;margin:0 0 2px}
.gr .s{font-size:12px;color:#656d76;margin:0 0 12px}
.gr ul{list-style:none;padding:0;margin:0 0 12px}
.gr li{padding:3px 0;font-size:14px}
.gr .ok{color:#1a7f37}.gr .run{color:#9a6700}.gr .err{color:#cf222e}
.gr .btn{display:inline-block;background:#ff4b4b;color:#fff!important;text-decoration:none;
  font-weight:700;padding:12px 22px;border-radius:8px;font-size:15px;margin:4px 0 6px}
.gr .url{font-family:monospace;font-size:12px;color:#656d76;word-break:break-all}
.gr .box{border-radius:8px;padding:10px 14px;font-size:13px;margin-top:12px;line-height:1.5}
.gr .warn{background:#fff8c5;border:1px solid #d4a72c}
.gr .bad{background:#ffebe9;border:1px solid #ff8182}
.gr .live{font-size:12px;color:#656d76;margin-top:8px}
@media (prefers-color-scheme: dark){
 .gr{color:#e6edf3}.gr .s,.gr .url,.gr .live{color:#9198a1}
 .gr .ok{color:#3fb950}.gr .run{color:#d29922}.gr .err{color:#f85149}
 .gr .warn{background:#3b2e00;border-color:#9e6a03}.gr .bad{background:#3d0b0b;border-color:#f85149}}
</style>"""

PASOS = [("claves", "Leer claves de Secrets"), ("codigo", "Descargar la app"),
         ("deps", "Instalar dependencias (1-2 min)"), ("drive", "Conectar Google Drive"),
         ("app", "Iniciar la app"), ("tunel", "Crear enlace")]
if not GUARDAR_EN_DRIVE:
    PASOS = [p for p in PASOS if p[0] != "drive"]
estado = {k: "pend" for k, _ in PASOS}
ui = {"version": "", "url": None, "error": None, "vivo": ""}
panel = display(HTML(""), display_id=True) if EN_NOTEBOOK else None


def pintar():
    if not EN_NOTEBOOK:
        return
    iconos = {"pend": "○", "run": "◌", "ok": "✓", "err": "✗"}
    clases = {"pend": "", "run": "run", "ok": "ok", "err": "err"}
    filas = "".join(f'<li class="{clases[estado[k]]}">{iconos[estado[k]]} {txt}</li>'
                    for k, txt in PASOS)
    cuerpo = f'<p class="t">◈ Grill API</p><p class="s">{html.escape(ui["version"]) or "&nbsp;"}</p><ul>{filas}</ul>'
    if ui["url"]:
        u = html.escape(ui["url"])
        cuerpo += (f'<a class="btn" href="{u}" target="_blank">Abrir la app ↗</a>'
                   f'<div class="url">{u}</div>'
                   '<div class="box warn"><b>⚠ Deje este cuaderno abierto</b> mientras usa la app. '
                   'Si cierra la pestaña, detiene la celda (■) o Colab desconecta la sesión, '
                   'la app se apaga. Si la primera carga da error, espere 10-20 s y recargue.</div>')
        if ui["vivo"]:
            cuerpo += f'<div class="live">{ui["vivo"]}</div>'
    if ui["error"]:
        cuerpo += f'<div class="box bad">{ui["error"]}</div>'
    panel.update(HTML(ESTILO + f'<div class="gr">{cuerpo}</div>'))


def marcar(clave, valor):
    estado[clave] = valor
    if not EN_NOTEBOOK:
        print({"run": "▸", "ok": "  ✓", "err": "  ✗"}.get(valor, ""), dict(PASOS)[clave], flush=True)
    pintar()


def fallar(clave, mensaje, detalle=""):
    marcar(clave, "err")
    ui["error"] = html.escape(mensaje)
    pintar()
    if detalle:
        print(detalle[-4000:])
    raise SystemExit(mensaje)


def correr(clave, cmd, **kw):
    """Ejecuta en silencio; solo muestra la salida si falla."""
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        fallar(clave, f"Falló: {' '.join(cmd[:3])}", r.stdout + r.stderr)
    return r


def leer_secreto(nombre):
    """Colab Secrets > variable de entorno."""
    try:
        from google.colab import userdata
        valor = userdata.get(nombre)
        if valor:
            return str(valor).strip()
    except Exception:
        pass
    return (os.environ.get(nombre) or "").strip()


def toml_str(valor):
    return '"' + valor.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def enlace_responde(url, intentos=1):
    """True si la app contesta a través del enlace público."""
    for i in range(intentos):
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=10) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        if i < intentos - 1:
            time.sleep(3)
    return False


def abrir_tunel(reintentos=3):
    """Lanza cloudflared y devuelve (proceso, url) solo cuando el enlace ya funciona.

    Error 1033 = el enlace existe pero el túnel no está conectado. Para evitarlo:
    - protocolo http2 (TCP 443): QUIC/UDP suele estar bloqueado en Colab;
    - se espera a "Registered tunnel connection" y a que /_stcore/health
      responda por el enlace público antes de mostrarlo.
    """
    for intento in range(1, reintentos + 1):
        subprocess.run(["pkill", "-x", "cloudflared"], check=False)
        proc = subprocess.Popen([CLOUDFLARED, "tunnel", "--no-autoupdate", "--protocol", "http2",
                                 "--url", f"http://localhost:{PUERTO}"],
                                stdout=open(LOG_TUNEL, "w"), stderr=subprocess.STDOUT)
        url = None
        for _ in range(90):
            log = open(LOG_TUNEL).read()
            if not url:
                hallado = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log)
                url = hallado.group(0) if hallado else None
            if url and "Registered tunnel connection" in log:
                break
            if proc.poll() is not None:
                break
            time.sleep(1)
        if url and proc.poll() is None and enlace_responde(url, intentos=20):
            return proc, url
        print(f"Túnel no disponible (intento {intento}/{reintentos}). Log:")
        print(open(LOG_TUNEL).read()[-1500:])
        proc.kill()
    return None, None


pintar()

# ------------------------------------------------------------------ 1. claves
marcar("claves", "run")
secretos = {n: leer_secreto(n) for n in OBLIGATORIOS + OPCIONALES}
faltan = [n for n in OBLIGATORIOS if not secretos[n]]
if faltan:
    fallar("claves", "Faltan en Colab Secrets (o sin Acceso del cuaderno): " + ", ".join(faltan))
marcar("claves", "ok")

# ------------------------------------------------------------------ 2. código
marcar("codigo", "run")
url_repo = f"https://github.com/{REPO}.git"
if os.path.isdir(os.path.join(CARPETA, ".git")):
    correr("codigo", ["git", "-C", CARPETA, "fetch", "-q", "--depth", "1", url_repo, RAMA])
    correr("codigo", ["git", "-C", CARPETA, "reset", "-q", "--hard", "FETCH_HEAD"])
else:
    shutil.rmtree(CARPETA, ignore_errors=True)
    correr("codigo", ["git", "clone", "-q", "--depth", "1", "--branch", RAMA, url_repo, CARPETA])
ui["version"] = "Versión " + correr("codigo", ["git", "-C", CARPETA, "log", "-1",
                                               "--format=%h · %s"]).stdout.strip()
marcar("codigo", "ok")

# ------------------------------------------------------------------ 3. dependencias
marcar("deps", "run")
correr("deps", [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                "-r", os.path.join(CARPETA, "requirements.txt")])
marcar("deps", "ok")

# ------------------------------------------------------------------ 4. Drive (opcional)
entorno = dict(os.environ)
if GUARDAR_EN_DRIVE:
    marcar("drive", "run")
    from google.colab import drive
    drive.mount("/content/drive")
    for sub in ("clientes", "historial"):
        destino = os.path.join(CARPETA_DRIVE, sub)
        if not os.path.isdir(destino):
            shutil.copytree(os.path.join(CARPETA, sub), destino)
    entorno["CLIENTES_DIR"] = os.path.join(CARPETA_DRIVE, "clientes")
    secretos["HISTORIAL_DIR"] = secretos["HISTORIAL_DIR"] or os.path.join(CARPETA_DRIVE, "historial")
    entorno["HISTORIAL_DIR"] = secretos["HISTORIAL_DIR"]
    marcar("drive", "ok")

# ------------------------------------------------------------------ 5. app
marcar("app", "run")
os.makedirs(os.path.join(CARPETA, ".streamlit"), exist_ok=True)
with open(os.path.join(CARPETA, ".streamlit", "secrets.toml"), "w", encoding="utf-8") as fh:
    for nombre, valor in secretos.items():
        if valor:
            fh.write(f"{nombre} = {toml_str(valor)}\n")
subprocess.run(["pkill", "-f", "streamlit run app.py"], check=False)
app = subprocess.Popen(
    [sys.executable, "-m", "streamlit", "run", "app.py",
     "--server.port", str(PUERTO), "--server.headless", "true",
     "--browser.gatherUsageStats", "false"],
    cwd=CARPETA, env=entorno, stdout=open(LOG_APP, "w"), stderr=subprocess.STDOUT)
for _ in range(120):
    try:
        if urllib.request.urlopen(f"http://localhost:{PUERTO}/_stcore/health", timeout=2).status == 200:
            break
    except Exception:
        pass
    if app.poll() is not None:
        fallar("app", "La app se cerró al arrancar (log abajo).", open(LOG_APP).read())
    time.sleep(1)
else:
    fallar("app", "La app no respondió en 2 minutos (log abajo).", open(LOG_APP).read())
marcar("app", "ok")

# ------------------------------------------------------------------ 6. enlace Cloudflare
marcar("tunel", "run")
tunel = None
try:
    if not os.path.exists(CLOUDFLARED):
        urllib.request.urlretrieve("https://github.com/cloudflare/cloudflared/releases/"
                                   "latest/download/cloudflared-linux-amd64", CLOUDFLARED)
        os.chmod(CLOUDFLARED, 0o755)
    tunel, ui["url"] = abrir_tunel()
except Exception as e:
    print("No se pudo abrir el túnel de Cloudflare:", e)

if not ui["url"]:
    # Respaldo: enlace propio de Colab (solo funciona en este navegador).
    try:
        from google.colab.output import eval_js
        ui["url"] = eval_js(f"google.colab.kernel.proxyPort({PUERTO}, {{'cache': false}})")
        ui["error"] = ("Cloudflare no respondió: se usa el enlace de Colab, que solo abre en "
                       "este navegador. Si la subida del xlsx falla, vuelva a ejecutar la celda.")
    except Exception:
        fallar("tunel", f"No se pudo crear el enlace. La app corre en http://localhost:{PUERTO}.")
marcar("tunel", "ok")
if not EN_NOTEBOOK:
    print(f"\nABRA LA APP: {ui['url']}\nDeje el cuaderno abierto: si se detiene la celda, la app se apaga.")

# ------------------------------------------------------------------ 7. mantener viva
inicio = time.time()
try:
    fallos = 0
    while app.poll() is None:
        # Si el túnel se cae o deja de responder 3 veces seguidas (~1.5 min),
        # se reabre; el enlace cambia y el botón pasa a apuntar al nuevo.
        if tunel is not None:
            fallos = 0 if (tunel.poll() is None and enlace_responde(ui["url"])) else fallos + 1
            if fallos >= 3:
                ui["vivo"] = "◌ Reconectando el enlace…"
                pintar()
                tunel, nueva = abrir_tunel()
                if nueva:
                    ui["url"], fallos = nueva, 0
                    print("El enlace se renovó:", nueva)
        minutos = int((time.time() - inicio) // 60)
        ui["vivo"] = (f"● App activa · {minutos // 60} h {minutos % 60:02d} min · "
                      f"última revisión {time.strftime('%H:%M')}")
        pintar()
        time.sleep(30)
    ui["url"], ui["error"] = None, "La app se detuvo (log abajo). Vuelva a ejecutar la celda."
    pintar()
    print(open(LOG_APP).read()[-4000:])
except KeyboardInterrupt:
    app.terminate()
    subprocess.run(["pkill", "-x", "cloudflared"], check=False)
    ui["url"], ui["error"] = None, "App detenida. Ejecute la celda de nuevo para volver a abrirla."
    pintar()
