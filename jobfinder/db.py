import json
import sqlite3
from datetime import datetime

from jobfinder.config import DB_PATH
from jobfinder.models import Job

# Status de uma vaga:
#   new       -> coletada, ainda não analisada
#   filtered  -> descartada pelo pré-filtro (modalidade/cidade/palavras-chave)
#   scored    -> analisada pelo Claude
#   applied   -> candidatura enviada
#   skipped   -> você decidiu pular
#   error     -> falha ao analisar/candidatar (veja apply_log)
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT, external_id TEXT, title TEXT, company TEXT, url TEXT,
    location TEXT, workplace TEXT, description TEXT, posted_at TEXT, tags TEXT,
    found_at TEXT, status TEXT DEFAULT 'new',
    score INTEGER, analysis TEXT,
    applied_at TEXT, apply_channel TEXT, apply_log TEXT
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def insert_jobs(conn, jobs: list[Job]) -> int:
    """Insere vagas novas; ignora as já conhecidas. Retorna quantas eram novas."""
    new = 0
    # Mesma vaga já salva por outra fonte (mesmo link): não entra de novo, evita candidatura duplicada.
    from jobfinder.sources.common import job_key

    known_urls = {job_key(r[0]) for r in conn.execute("SELECT url FROM jobs") if r[0]}
    # Mesma vaga (título + empresa) já aplicada ou descartada, vinda por outro site/agregador.
    done = {(r[0].lower().strip(), r[1].lower().strip()) for r in conn.execute(
        "SELECT title, company FROM jobs WHERE status IN ('applied', 'skipped') AND company != ''")}
    for j in jobs:
        if j.company and (j.title.lower().strip(), j.company.lower().strip()) in done:
            continue
        if j.url and job_key(j.url) in known_urls and not conn.execute(
                "SELECT 1 FROM jobs WHERE id = ?", (j.id,)).fetchone():
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO jobs (id, source, external_id, title, company, url, location,"
            " workplace, description, posted_at, tags, found_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (j.id, j.source, j.external_id, j.title, j.company, j.url, j.location,
             j.workplace, j.description, j.posted_at, json.dumps(j.tags, ensure_ascii=False), now()),
        )
        new += cur.rowcount
    conn.commit()
    return new


def row_to_job(row) -> Job:
    return Job(
        source=row["source"], external_id=row["external_id"], title=row["title"], url=row["url"],
        company=row["company"] or "", description=row["description"] or "",
        location=row["location"] or "", workplace=row["workplace"] or "unknown",
        posted_at=row["posted_at"] or "", tags=json.loads(row["tags"] or "[]"),
    )


def set_status(conn, job_id: str, status: str, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    sql = f"UPDATE jobs SET status = ?{', ' + cols if cols else ''} WHERE id = ?"
    conn.execute(sql, (status, *fields.values(), job_id))
    conn.commit()


def analysis(row) -> dict:
    return json.loads(row["analysis"]) if row["analysis"] else {}
