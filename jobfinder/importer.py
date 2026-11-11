"""Importa vagas de um arquivo ou da área de transferência. Cada linha é um JSON com source, id, title, company, location, workplace e,
no Indeed/Glassdoor, url e description. Do LinkedIn vem só a listagem: a descrição é baixada
aqui pela página pública da vaga."""

import json
import random
import subprocess
import time
import unicodedata

from jobfinder import db
from jobfinder.models import Job
from jobfinder.sources import linkedin
from jobfinder.sources.common import browser_client, known_ids

URLS = {
    "linkedin": "https://www.linkedin.com/jobs/view/{id}/",
    "indeed": "https://br.indeed.com/viewjob?jk={id}",
    "glassdoor": "https://www.glassdoor.com.br/job-listing/vaga-JV_KO0,4_KE5,9.htm?jl={id}",
}


def read_clipboard() -> str:
    # Get-Clipboard devolve o texto como o navegador copiou (UTF-8), inclusive com várias linhas.
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return out.stdout


def parse(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            rows.append(json.loads(line))
    return rows


def _same_job(title: str, company: str) -> tuple[str, str]:
    """Chave para achar a mesma vaga vinda de outra fonte (ex.: Glassdoor que aponta para a Gupy).
    A empresa é cortada porque cada site escreve o nome de um jeito ("Brasilprev" x "Brasilprev
    Seguros e Previdência")."""
    norm = lambda s: "".join(ch for ch in unicodedata.normalize("NFKD", (s or "").lower()) if ch.isalnum())
    return norm(title), norm(company)[:8]


def import_rows(rows: list[dict], log) -> int:
    """Grava as vagas novas como 'new' (a análise vem depois). Retorna quantas entraram."""
    known = {src: known_ids(src) for src in URLS}
    seen = {_same_job(t, co) for t, co in db.connect().execute("SELECT title, company FROM jobs")}
    jobs = []
    with browser_client() as c:
        for r in rows:
            src, jid = r.get("source"), str(r.get("id", ""))
            if src not in URLS or not jid or jid in known[src]:
                continue
            key = _same_job(r.get("title", ""), r.get("company", ""))
            if key in seen:  # já veio por outra fonte (ou repetida no mesmo lote)
                continue
            seen.add(key)
            desc = r.get("description", "")
            if src == "linkedin" and not desc:
                try:
                    desc = linkedin._detail(c, jid, 1.5 * random.uniform(0.8, 1.3), log)
                except linkedin.RateLimited:
                    log("linkedin: limite de requisições; o resto fica para a próxima importação")
                    break
                time.sleep(0.5)
            jobs.append(Job(source=src, external_id=jid, title=r.get("title", ""), company=r.get("company", ""),
                            url=r.get("url") or URLS[src].format(id=jid), location=r.get("location", ""),
                            workplace=r.get("workplace") or "unknown", description=desc))
    return db.insert_jobs(db.connect(), jobs)
