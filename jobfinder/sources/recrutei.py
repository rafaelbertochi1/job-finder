"""Recrutei Empregos: portal que reúne as vagas das empresas que usam a Recrutei (ex.: Clicksoft)."""

import html
import json
import re
import time

from jobfinder.models import Job
from jobfinder.sources.common import browser_client, days_ago, discarded, known_ids, strip_html, too_old

BASE = "https://empregos.recrutei.com.br"
CARD = re.compile(r'<article class="d2-card d2-jobrow">(.*?)</article>', re.S)
UNITS = {"hora": 0, "horas": 0, "minuto": 0, "minutos": 0, "dia": 1, "dias": 1,
         "semana": 7, "semanas": 7, "mês": 30, "meses": 30, "ano": 365, "anos": 365}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _posted(label: str) -> str:
    """'Publicada há 4 horas' / 'há 1 semana' -> ISO 8601 aproximado."""
    m = re.search(r"há (\d+|um|uma) (\w+)", label)
    if not m:
        return ""
    n = 1 if m.group(1) in ("um", "uma") else int(m.group(1))
    return days_ago(n * UNITS.get(m.group(2), 0))


def _workplace(badges: list[str]) -> str:
    b = " ".join(badges).lower()
    if "remoto" in b:  # inclui "Presencial ou Remoto"
        return "remote"
    if "híbrido" in b or "hibrido" in b:
        return "hybrid"
    return "onsite" if "presencial" in b else "unknown"


def _cards(page: str) -> list[dict]:
    out = []
    for block in CARD.findall(page):
        link = re.search(r'd2-jobrow__title">\s*<a href="([^"?]+)[^"]*"\s*>(.*?)</a>', block, re.S)
        if not link:
            continue
        jid = re.search(r"/(\d+)-", link.group(1))
        meta = _clean((re.search(r'd2-jobrow__meta">(.*?)</p>', block, re.S) or [None, ""])[1])
        company, _, location = meta.partition(" · ")
        badges = [_clean(b) for b in re.findall(r'class="d2-badge[^"]*">(.*?)</span>', block, re.S)]
        salary = _clean((re.search(r'd2-jobrow__salary[^"]*">(.*?)</span>', block, re.S) or [None, ""])[1])
        when = _clean((re.search(r'd2-jobrow__time">(.*?)</span>', block, re.S) or [None, ""])[1])
        out.append({
            "id": jid.group(1) if jid else link.group(1),
            "url": link.group(1),
            "title": _clean(link.group(2)),
            "company": company,
            "location": location,
            "badges": badges,
            "salary": salary,
            "posted_at": _posted(when),
        })
    return out


def _detail(c, url: str) -> dict:
    """Descrição e data pelo JSON-LD (JobPosting); modelo e local pela ficha da página.

    Os selos da listagem às vezes dizem "Remoto" para vaga presencial: a ficha da vaga é que vale.
    """
    r = c.get(url)
    if r.status_code != 200:
        return {}
    out = {}
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if data.get("@type") == "JobPosting":
            out = {"description": strip_html(data.get("description")), "posted_at": data.get("datePosted", "")}
            break
    text = _clean(re.sub(r"<(script|style)[^>]*>.*?</\1>", "", r.text, flags=re.S))
    m = re.search(r"Modelo (.{3,25}?) Local (.{2,50}?) Publicada", text)
    if m:
        out["workplace"] = _workplace([m.group(1)])
        out["location"] = m.group(2).strip()
    return out


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    scfg = cfg["search"]["sources"]["recrutei"]
    max_age = cfg["search"]["max_age_days"]
    pcd = profile.get("diversity", {}).get("pcd", False)
    seen = known_ids("recrutei")
    jobs: dict[str, Job] = {}
    details = 0
    with browser_client() as c:
        for query in scfg["queries"]:
            for page in range(1, scfg["pages"] + 1):
                try:
                    r = c.get(f"{BASE}/busca", params={"keyword": query, "page": page})
                except Exception as e:  # noqa: BLE001
                    log(f"recrutei '{query}': {e}")
                    break
                cards = _cards(r.text) if r.status_code == 200 else []
                for card in cards:
                    if card["id"] in seen or card["id"] in jobs or too_old(card["posted_at"], max_age):
                        continue
                    if "Para PCD" in card["badges"] and not pcd and "PCD" in card["title"].upper():
                        continue
                    workplace = _workplace(card["badges"])
                    job = Job(
                        source="recrutei",
                        external_id=card["id"],
                        title=card["title"],
                        company=card["company"],
                        url=card["url"],
                        location="" if workplace == "remote" else card["location"],
                        workplace=workplace,
                        posted_at=card["posted_at"],
                        tags=[b for b in card["badges"] if b != "Para PCD"],
                    )
                    if not discarded(job, profile) and details < scfg["max_details"]:
                        info = _detail(c, card["url"])
                        salary = card["salary"] if card["salary"] and "Não" not in card["salary"] else ""
                        job.description = info.get("description", "") + (f"\n\nSalário: {salary}" if salary else "")
                        job.posted_at = info.get("posted_at") or job.posted_at
                        if info.get("workplace", "unknown") != "unknown":
                            job.workplace = info["workplace"]
                            job.location = "" if job.workplace == "remote" else info.get("location", job.location)
                        details += 1
                        time.sleep(scfg["delay"])
                    jobs[card["id"]] = job
                if len(cards) < 10:
                    break
                time.sleep(scfg["delay"])
    return list(jobs.values())
