#@title ◈ Grill API — Limpieza y Análisis de Noticias (Colab) { display-mode: "form" }
#@markdown Ejecuta **la misma app** (`app.py`) que Streamlit Cloud: mismas funciones, modelos y precisión.
#@markdown
#@markdown Las claves se leen de Colab *Secrets* (icono de llave, *Acceso del cuaderno* activado):
#@markdown `APP_PASSWORD`, `OPENAI_API_KEY`, `TYPESAFE_API_KEY` (Jev), `REGIONES_CSV_URL`, `INTERNET_CSV_URL`.

#@markdown ---
#@markdown ### ⚙ Opciones
GUARDAR_EN_DRIVE = False  #@param {type:"boolean"}
#@markdown Por defecto la app se abre con el enlace propio de Colab (sin servicios externos).
#@markdown Marque esta opción solo si al subir el xlsx aparece un error 403/500:
USAR_ENLACE_CLOUDFLARE = False  #@param {type:"boolean"}

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
OBLIGATORIOS = ["APP_PASSWORD", "OPENAI_API_KEY", "TYPESAFE_API_KEY",
                "REGIONES_CSV_URL", "INTERNET_CSV_URL"]
OPCIONALES = ["HISTORIAL_DIR"]


def paso(texto):
    print(f"▸ {texto}", flush=True)


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


def correr(cmd, **kw):
    """Ejecuta en silencio; solo muestra la salida si falla."""
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print((r.stdout + r.stderr)[-4000:])
        raise SystemExit(f"Falló: {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}")
    return r


def toml_str(valor):
    return '"' + valor.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


# 1. Claves
paso("Leyendo claves")
secretos = {n: leer_secreto(n) for n in OBLIGATORIOS + OPCIONALES}
faltan = [n for n in OBLIGATORIOS if not secretos[n]]
if faltan:
    raise SystemExit("Faltan claves en Colab Secrets: " + ", ".join(faltan))

# 2. Código
paso(f"Descargando el código ({REPO}@{RAMA})")
url_repo = f"https://github.com/{REPO}.git"
if os.path.isdir(os.path.join(CARPETA, ".git")):
    correr(["git", "-C", CARPETA, "fetch", "-q", "--depth", "1", url_repo, RAMA])
    correr(["git", "-C", CARPETA, "reset", "-q", "--hard", "FETCH_HEAD"])
else:
    shutil.rmtree(CARPETA, ignore_errors=True)
    correr(["git", "clone", "-q", "--depth", "1", "--branch", RAMA, url_repo, CARPETA])
version = correr(["git", "-C", CARPETA, "log", "-1", "--format=%h %s"]).stdout.strip()
print(f"  versión: {version}")

# 3. Dependencias
paso("Instalando dependencias (1-2 min)")
correr([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
        "-r", os.path.join(CARPETA, "requirements.txt")])

# 4. Drive (opcional): perfiles de cliente e historial persistentes
entorno = dict(os.environ)
if GUARDAR_EN_DRIVE:
    paso("Montando Google Drive")
    from google.colab import drive
    drive.mount("/content/drive")
    for sub in ("clientes", "historial"):
        destino = os.path.join(CARPETA_DRIVE, sub)
        if not os.path.isdir(destino):
            shutil.copytree(os.path.join(CARPETA, sub), destino)
    entorno["CLIENTES_DIR"] = os.path.join(CARPETA_DRIVE, "clientes")
    secretos["HISTORIAL_DIR"] = secretos["HISTORIAL_DIR"] or os.path.join(CARPETA_DRIVE, "historial")
    entorno["HISTORIAL_DIR"] = secretos["HISTORIAL_DIR"]

# 5. secrets.toml (lo lee app.py con st.secrets)
os.makedirs(os.path.join(CARPETA, ".streamlit"), exist_ok=True)
with open(os.path.join(CARPETA, ".streamlit", "secrets.toml"), "w", encoding="utf-8") as fh:
    for nombre, valor in secretos.items():
        if valor:
            fh.write(f"{nombre} = {toml_str(valor)}\n")

# 6. Streamlit
paso("Iniciando la app")
subprocess.run(["pkill", "-f", "streamlit run app.py"], check=False)
LOG_APP = "/content/streamlit.log"
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
        print(open(LOG_APP).read()[-4000:])
        raise SystemExit("La app se cerró al arrancar (log arriba).")
    time.sleep(1)
else:
    print(open(LOG_APP).read()[-4000:])
    raise SystemExit("La app no respondió en 2 minutos (log arriba).")

# 7. Enlace para abrir la app
url_app = None
if not USAR_ENLACE_CLOUDFLARE:
    # Proxy propio de Colab: solo lo abre este navegador, con la sesión de Google.
    paso("Abriendo enlace de Colab")
    try:
        from google.colab.output import eval_js
        url_app = eval_js(f"google.colab.kernel.proxyPort({PUERTO}, {{'cache': false}})")
    except Exception as e:
        print("  no se pudo abrir el enlace de Colab:", e)
else:
    # Respaldo: túnel público de Cloudflare (trycloudflare.com, sin cuenta).
    paso("Abriendo enlace de Cloudflare")
    try:
        binario = "/content/cloudflared"
        if not os.path.exists(binario):
            urllib.request.urlretrieve("https://github.com/cloudflare/cloudflared/releases/"
                                       "latest/download/cloudflared-linux-amd64", binario)
            os.chmod(binario, 0o755)
        subprocess.run(["pkill", "-f", "cloudflared tunnel"], check=False)
        LOG_TUNEL = "/content/cloudflared.log"
        subprocess.Popen([binario, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{PUERTO}"],
                         stdout=open(LOG_TUNEL, "w"), stderr=subprocess.STDOUT)
        for _ in range(60):
            hallado = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", open(LOG_TUNEL).read())
            if hallado:
                url_app = hallado.group(0)
                break
            time.sleep(1)
    except Exception as e:
        print("  no se pudo abrir el túnel:", e)

print("\n" + "═" * 64)
if url_app:
    print(f"  ✅ ABRA LA APP:  {url_app}")
    if USAR_ENLACE_CLOUDFLARE:
        print("  (si da error de DNS, espere 10-20 s y recargue)")
    else:
        print("  Si al subir el xlsx sale error 403/500: detenga la celda, marque")
        print("  USAR_ENLACE_CLOUDFLARE y ejecútela de nuevo.")
else:
    print(f"  App corriendo en http://localhost:{PUERTO}, pero no se pudo crear el enlace.")
print("═" * 64)
print("  Deje esta celda corriendo. Para apagar la app, deténgala (■).\n")

# 8. Mantener viva la app
try:
    while app.poll() is None:
        time.sleep(30)
    print(open(LOG_APP).read()[-4000:])
    print("La app se detuvo (log arriba).")
except KeyboardInterrupt:
    app.terminate()
    subprocess.run(["pkill", "-f", "cloudflared tunnel"], check=False)
    print("App detenida.")
