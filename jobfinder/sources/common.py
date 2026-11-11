import html
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobFinder/1.0"


def client(headers: dict | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})},
        timeout=30,
        follow_redirects=True,
    )


def get_json(c: httpx.Client, url: str, params: dict | None = None, retries: int = 3):
    for attempt in range(retries):
        r = c.get(url, params=params)
        if r.status_code in (429, 502, 503, 504) and attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def too_old(iso_date: str | None, max_age_days: int) -> bool:
    if not iso_date:
        return False
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc) - timedelta(days=max_age_days)


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def browser_client() -> httpx.Client:
    """Para sites que só respondem bem a um navegador (HTML)."""
    return httpx.Client(
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "pt-BR,pt;q=0.9"},
        timeout=30,
        follow_redirects=True,
    )


def known_ids(source: str) -> set[str]:
    """IDs externos já salvos no banco: evita baixar de novo o detalhe de vagas conhecidas."""
    from jobfinder import db

    conn = db.connect()
    return {r[0] for r in conn.execute("SELECT external_id FROM jobs WHERE source = ?", (source,))}


def discarded(job, profile: dict) -> bool:
    """True se o pré-filtro já descartaria a vaga só pelo título/local (não vale baixar o detalhe)."""
    from jobfinder.matcher import prefilter

    reason = prefilter(job, profile)
    # Sem a descrição ainda não dá para checar palavras-chave: só título, nível e local contam aqui.
    return reason is not None and reason != "nenhuma palavra-chave do perfil"


def days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")


def job_key(url: str) -> str:
    """Identificador canônico da vaga a partir do link, igual entre fontes diferentes.

    A Gupy usa /jobs/<id> e /job/<base64 com o id>; o LinkedIn usa /jobs/view/<id> e
    /jobs/view/<slug>-<id>. Nos demais sites vale o link sem parâmetros.
    """
    import base64
    import json

    url = (url or "").split("#")[0]
    m = re.search(r"([\w-]+)\.gupy\.io/jobs?/([^?/]+)", url)
    if m:
        ident = m.group(2)
        if not ident.isdigit():
            try:
                ident = str(json.loads(base64.b64decode(ident + "==").decode())["jobId"])
            except Exception:  # noqa: BLE001
                pass
        return f"gupy:{ident}"
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{6,})", url)
    if m:
        return f"linkedin:{m.group(1)}"
    # Indeed e Glassdoor identificam a vaga por parâmetro (jk / jl), não pelo caminho.
    m = re.search(r"indeed\.com/.*[?&]jk=(\w+)", url)
    if m:
        return f"indeed:{m.group(1)}"
    m = re.search(r"glassdoor\.com(?:\.br)?/.*[?&]jl=(\d+)", url)
    if m:
        return f"glassdoor:{m.group(1)}"
    return url.split("?")[0].rstrip("/").lower()
