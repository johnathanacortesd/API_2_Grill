"""Auditoria de uso por correo (SMTP). Best-effort: nunca interrumpe la corrida.

Lee la configuracion de los Secrets de Streamlit (con fallback a variables
de entorno para corridas locales):

    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, USAGE_NOTIFY_EMAIL

Cada vez que se procesa un dossier con IA, envia un correo resumiendo que
cliente/marca uso la API: marca, alias, voceros, criterio, filas, tonos y
duracion. Asi el dueno sabe que clientes consumen la API.

Nota Gmail: SMTP_PASSWORD debe ser una *contrasena de aplicacion* de Google
(16 caracteres), no la contrasena normal de la cuenta. Requiere tener la
verificacion en dos pasos activada.
"""
import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

logger = logging.getLogger("auditoria_mail")

_TIMEOUT_SEG = 20


def _leer_config():
    """Config SMTP desde st.secrets (Streamlit Cloud) o entorno (local)."""
    cfg = {}
    try:
        import streamlit as st
        secrets = st.secrets
        for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
                  "SMTP_FROM", "USAGE_NOTIFY_EMAIL"):
            try:
                v = secrets.get(k)
            except Exception:
                v = None
            if v:
                cfg[k] = str(v).strip()
    except Exception:
        pass
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
              "SMTP_FROM", "USAGE_NOTIFY_EMAIL"):
        if not cfg.get(k) and os.environ.get(k):
            cfg[k] = os.environ[k].strip()
    return cfg


def _es_config_valida(cfg):
    # Contrasena de aplicacion de Gmail: 16 caracteres (con o sin espacios).
    pwd = (cfg.get("SMTP_PASSWORD") or "").replace(" ", "")
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_USER")
                and len(pwd) >= 8 and cfg.get("USAGE_NOTIFY_EMAIL"))


def construir_mensaje(result, ai_config, cfg):
    """Arma el EmailMessage con el resumen de uso. No hace red."""
    brand = (ai_config or {}).get("brand") or "sin marca"
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    asunto = "[Grill-API] Uso de API — %s — %s" % (brand, ahora)

    filas = result or {}
    tonos = filas.get("_conteo_tonos") or {}
    analisis = filas.get("analisis") or {}
    lineas = [
        "Uso de la API de analisis (tono/tema/subtema)",
        "",
        "Marca/cliente : %s" % brand,
        "Alias         : %s" % (", ".join((ai_config or {}).get("aliases") or []) or "-"),
        "Voceros       : %s" % (", ".join((ai_config or {}).get("voceros") or []) or "-"),
        "Criterio      : %s" % ((ai_config or {}).get("criterio") or "-"),
        "Archivo       : %s" % (filas.get("output_filename") or "-"),
        "Fecha         : %s" % ahora,
        "",
        "Filas totales : %s" % (filas.get("total_rows") if filas.get("total_rows") is not None else "-"),
        "Unicas        : %s" % (filas.get("unique_rows") if filas.get("unique_rows") is not None else "-"),
        "Duplicadas    : %s" % (filas.get("duplicates") if filas.get("duplicates") is not None else "-"),
        "Duracion      : %s" % (filas.get("process_duration") or "-"),
    ]
    if tonos:
        lineas.append("")
        lineas.append("Tonos: " + ", ".join("%s=%s" % (k, v) for k, v in sorted(tonos.items())))
    if analisis:
        ult = analisis.get("_ULTIMO_RESUMEN") or analisis
        if isinstance(ult, dict) and ult.get("taxonomia"):
            lineas.append("")
            lineas.append("Temas: " + ", ".join(str(t) for t in ult["taxonomia"][:12]))
    lineas += ["", "--", "Grill-API v4.1 · auditoria automatica de uso"]

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = cfg.get("SMTP_FROM") or cfg.get("SMTP_USER")
    msg["To"] = cfg["USAGE_NOTIFY_EMAIL"]
    msg.set_content("\n".join(lineas))
    return msg


def enviar_auditoria_desde_resultado(result, ai_config):
    """Envia el correo de auditoria. Devuelve True si se envio.

    Nunca lanza excepciones: cualquier fallo se registra en el log.
    """
    try:
        cfg = _leer_config()
        if not _es_config_valida(cfg):
            logger.warning("Auditoria por correo omitida: faltan secrets SMTP.")
            return False
        msg = construir_mensaje(result, ai_config, cfg)
        pwd = cfg["SMTP_PASSWORD"].replace(" ", "")
        with smtplib.SMTP(cfg["SMTP_HOST"], int(cfg.get("SMTP_PORT") or 587),
                          timeout=_TIMEOUT_SEG) as smtp:
            smtp.starttls()
            smtp.login(cfg["SMTP_USER"], pwd)
            smtp.send_message(msg)
        logger.info("Auditoria de uso enviada a %s.", cfg["USAGE_NOTIFY_EMAIL"])
        return True
    except Exception:
        logger.exception("Fallo al enviar la auditoria por correo (no interrumpe).")
        return False
