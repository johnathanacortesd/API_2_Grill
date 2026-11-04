# -*- coding: utf-8 -*-
"""
Grill API en Google Colab: la MISMA app de Streamlit (app.py) corriendo en Colab.

No es una copia ni una reimplementacion: clona este repositorio, instala
requirements.txt y ejecuta `streamlit run app.py`. Por eso tiene las mismas
pestanas, funciones, modelos y precision que la app publicada en Streamlit Cloud.
La interfaz web se abre con un enlace publico (tunel de Cloudflare).

USO
1. Abra un cuaderno nuevo en https://colab.research.google.com
2. En la barra izquierda, icono de llave (Secrets), cree estos secretos y
   active "Acceso del cuaderno" en cada uno:
     APP_PASSWORD       contrasena de la app                 (obligatorio)
     OPENAI_API_KEY     clave de OpenAI                      (obligatorio)
     REGIONES_CSV_URL   CSV publicado de regiones            (obligatorio)
     INTERNET_CSV_URL   CSV publicado de internet            (obligatorio)
     TYPESAFE_API_KEY   solo si la usa                       (opcional)
     GITHUB_TOKEN       solo si el repositorio es privado    (opcional)
   Son los mismos valores que tiene en Streamlit Cloud -> Settings -> Secrets.
3. Pegue este archivo completo en una celda y ejecutela.
4. Abra el enlace https://....trycloudflare.com que aparece al final.
   La celda queda corriendo: si la detiene, la app se apaga.
"""

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

# ---------------------------------------------------------------- opciones
REPO = "johnathanacortesd/API_2_Grill"
RAMA = "main"
CARPETA = "/content/API_2_Grill"
PUERTO = 8501
# True = perfiles de cliente e historial se guardan en Google Drive y no se
# pierden al cerrar Colab (pide permiso para montar Drive).
GUARDAR_EN_DRIVE = False
CARPETA_DRIVE = "/content/drive/MyDrive/Grill_API"

SECRETOS = ["APP_PASSWORD", "OPENAI_API_KEY", "REGIONES_CSV_URL",
            "INTERNET_CSV_URL", "TYPESAFE_API_KEY", "HISTORIAL_DIR"]
OBLIGATORIOS = ["APP_PASSWORD", "OPENAI_API_KEY", "REGIONES_CSV_URL", "INTERNET_CSV_URL"]


def leer_secreto(nombre):
    """Colab Secrets primero; si no existe, variable de entorno."""
    try:
        from google.colab import userdata
        valor = userdata.get(nombre)
        if valor:
            return str(valor).strip()
    except Exception:
        pass
    return (os.environ.get(nombre) or "").strip()


def correr(cmd, **kw):
    print("$", " ".join(cmd) if isinstance(cmd, list) else cmd)
    subprocess.run(cmd, check=True, **kw)


def toml_str(valor):
    escapado = valor.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escapado}"'


# ---------------------------------------------------------------- 1. secretos
secretos = {n: leer_secreto(n) for n in SECRETOS}
faltan = [n for n in OBLIGATORIOS if not secretos[n]]
if faltan:
    raise SystemExit("Faltan secretos en Colab (icono de llave): " + ", ".join(faltan))

# ---------------------------------------------------------------- 2. codigo
token = leer_secreto("GITHUB_TOKEN")
url_repo = (f"https://x-access-token:{token}@github.com/{REPO}.git" if token
            else f"https://github.com/{REPO}.git")
if os.path.isdir(os.path.join(CARPETA, ".git")):
    correr(["git", "-C", CARPETA, "fetch", "--depth", "1", "origin", RAMA])
    correr(["git", "-C", CARPETA, "reset", "--hard", f"origin/{RAMA}"])
else:
    shutil.rmtree(CARPETA, ignore_errors=True)
    correr(["git", "clone", "--depth", "1", "--branch", RAMA, url_repo, CARPETA])
print("Version del codigo:",
      subprocess.run(["git", "-C", CARPETA, "log", "-1", "--format=%h %s"],
                     capture_output=True, text=True).stdout.strip())

# ---------------------------------------------------------------- 3. dependencias
correr([sys.executable, "-m", "pip", "install", "-q", "-r",
        os.path.join(CARPETA, "requirements.txt")])

# ---------------------------------------------------------------- 4. Drive (opcional)
entorno = dict(os.environ)
if GUARDAR_EN_DRIVE:
    from google.colab import drive
    drive.mount("/content/drive")
    for sub in ("clientes", "historial"):
        destino = os.path.join(CARPETA_DRIVE, sub)
        if not os.path.isdir(destino):
            shutil.copytree(os.path.join(CARPETA, sub), destino)
    entorno["CLIENTES_DIR"] = os.path.join(CARPETA_DRIVE, "clientes")
    secretos["HISTORIAL_DIR"] = secretos["HISTORIAL_DIR"] or os.path.join(CARPETA_DRIVE, "historial")
    entorno["HISTORIAL_DIR"] = secretos["HISTORIAL_DIR"]

# ---------------------------------------------------------------- 5. secrets.toml
os.makedirs(os.path.join(CARPETA, ".streamlit"), exist_ok=True)
with open(os.path.join(CARPETA, ".streamlit", "secrets.toml"), "w", encoding="utf-8") as fh:
    for nombre, valor in secretos.items():
        if valor:
            fh.write(f"{nombre} = {toml_str(valor)}\n")

# ---------------------------------------------------------------- 6. Streamlit
subprocess.run(["pkill", "-f", "streamlit run"], check=False)
log_app = open("/content/streamlit.log", "w")
app = subprocess.Popen(
    [sys.executable, "-m", "streamlit", "run", "app.py",
     "--server.port", str(PUERTO), "--server.headless", "true",
     "--browser.gatherUsageStats", "false"],
    cwd=CARPETA, env=entorno, stdout=log_app, stderr=subprocess.STDOUT)

for _ in range(120):
    try:
        if urllib.request.urlopen(f"http://localhost:{PUERTO}/_stcore/health", timeout=2).status == 200:
            break
    except Exception:
        pass
    if app.poll() is not None:
        print(open("/content/streamlit.log").read())
        raise SystemExit("Streamlit se cerro al arrancar (log arriba).")
    time.sleep(1)
else:
    print(open("/content/streamlit.log").read())
    raise SystemExit("Streamlit no respondio en 2 minutos (log arriba).")
print("Streamlit corriendo en el puerto", PUERTO)

# ---------------------------------------------------------------- 7. enlace publico
url_publica = None
try:
    binario = "/content/cloudflared"
    if not os.path.exists(binario):
        urllib.request.urlretrieve(
            "https://github.com/cloudflare/cloudflared/releases/latest/download/"
            "cloudflared-linux-amd64", binario)
        os.chmod(binario, 0o755)
    subprocess.run(["pkill", "-f", "cloudflared tunnel"], check=False)
    log_tunel = "/content/cloudflared.log"
    tunel = subprocess.Popen(
        [binario, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{PUERTO}"],
        stdout=open(log_tunel, "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        hallado = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", open(log_tunel).read())
        if hallado:
            url_publica = hallado.group(0)
            break
        time.sleep(1)
except Exception as e:
    print("No se pudo abrir el tunel de Cloudflare:", e)

print("\n" + "=" * 70)
if url_publica:
    print("ABRA LA APP AQUI:", url_publica)
    print("(espere 10-20 s si la primera carga da error de DNS)")
else:
    # Respaldo: proxy interno de Colab (solo funciona en este mismo navegador).
    try:
        from google.colab import output
        print("Abriendo la app con el proxy de Colab (ventana debajo).")
        output.serve_kernel_port_as_window(PUERTO)
    except Exception:
        print(f"App local en http://localhost:{PUERTO}")
print("=" * 70)
print("Deje esta celda corriendo. Para apagar la app: detenga la celda.\n")

# ---------------------------------------------------------------- 8. mantener viva
try:
    while app.poll() is None:
        time.sleep(30)
    print(open("/content/streamlit.log").read()[-5000:])
    print("Streamlit se detuvo (log arriba).")
except KeyboardInterrupt:
    app.terminate()
    subprocess.run(["pkill", "-f", "cloudflared tunnel"], check=False)
    print("App detenida.")
