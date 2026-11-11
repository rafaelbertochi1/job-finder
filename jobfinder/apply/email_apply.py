"""Candidatura por e-mail (vagas que pedem "envie seu currículo para ...")."""

import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def smtp_configured() -> bool:
    return all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))


def build(profile: dict, to: str, materials: dict, cv: Path) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{profile['name']} <{os.environ['SMTP_USER']}>"
    msg["To"] = to
    msg["Subject"] = materials["email_subject"]
    if profile.get("email") and profile["email"] != os.environ["SMTP_USER"]:
        msg["Reply-To"] = profile["email"]
    msg.set_content(materials["email_body"])
    ctype = mimetypes.guess_type(cv.name)[0] or "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    msg.add_attachment(cv.read_bytes(), maintype=maintype, subtype=subtype, filename=cv.name)
    return msg


def send(msg: EmailMessage):
    host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)
