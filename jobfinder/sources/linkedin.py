"""LinkedIn Vagas: busca pública (sem login) do linkedin.com/jobs."""

import html
import random
import re
import time

from jobfinder.models import Job
from jobfinder.sources.common import browser_client, discarded, known_ids, strip_html

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
# f_WT do LinkedIn: 1 presencial, 2 remoto, 3 híbrido.
WORKPLACE = {"onsite": "1", "remote": "2", "hybrid": "3"}
PAGE = 10
# Esperas (s) antes de tentar de novo quando o LinkedIn responde 429.
BACKOFF = (30, 90)

CARD = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"(.*?)</li>', re.S)


class RateLimited(Exception):
    pass


def _text(pattern: str, block: str) -> str:
    m = re.search(pattern, block, re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


def _cards(page: str) -> list[dict]:
    out = []
    for jid, block in CARD.findall(page):
        out.append({
            "id": jid,
            "title": _text(r'class="base-search-card__title"[^>]*>(.*?)</h3>', block),
            "company": _text(r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>', block),
            "location": _text(r'class="job-search-card__location"[^>]*>(.*?)</span>', block),
            "posted_at": (re.search(r'datetime="([\d-]+)"', block) or [None, ""])[1],
        })
    return out


def _get(c, url: str, delay: float, log, **params):
    """GET com pausa aleatória; em 429 espera e tenta de novo antes de desistir."""
    for wait in (0, *BACKOFF):
        if wait:
            log(f"linkedin: limite de requisições, aguardando {wait}s")
            time.sleep(wait)
        time.sleep(delay * random.uniform(0.7, 1.5))
        r = c.get(url, params=params or None)
        if r.status_code != 429:
            return r
    raise RateLimited


def _detail(c, jid: str, delay: float, log) -> str:
    r = _get(c, DETAIL.format(id=jid), delay, log)
    if r.status_code != 200:
        return ""
    m = re.search(r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', r.text, re.S)
    desc = strip_html(m.group(1)) if m else ""
    crit = re.findall(r'description__job-criteria-subheader">(.*?)</h3>\s*<span[^>]*>(.*?)</span>', r.text, re.S)
    if crit:
        desc += "\n\n" + "\n".join(f"{strip_html(k)}: {strip_html(v)}" for k, v in crit)
    return desc


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    """Primeiro coleta as listagens; depois baixa as descrições das vagas que interessam.

    Se o limite do LinkedIn persistir, as vagas sem descrição ficam de fora (não vão para o
    banco) e voltam na próxima busca, em vez de serem analisadas às cegas.
    """
    scfg = cfg["search"]["sources"]["linkedin"]
    seen = known_ids("linkedin")
    jobs: dict[str, Job] = {}
    with browser_client() as c:
        try:
            for query in scfg["queries"]:
                for modality in cfg["search"]["modalities"]:
                    location = scfg["remote_location"] if modality == "remote" else scfg["local_location"]
                    for page in range(scfg["pages"]):
                        try:
                            r = _get(c, SEARCH, scfg["delay"], log, keywords=query, location=location,
                                     f_WT=WORKPLACE[modality], f_TPR=f"r{cfg['search']['max_age_days'] * 86400}",
                                     start=page * PAGE)
                        except RateLimited:
                            raise
                        except Exception as e:  # noqa: BLE001
                            log(f"linkedin '{query}' ({modality}): {e}")
                            break
                        cards = _cards(r.text) if r.status_code == 200 else []
                        for card in cards:
                            if card["id"] in seen:
                                continue
                            if card["id"] in jobs:
                                # O LinkedIn completa a busca de uma modalidade com vagas de outras
                                # quando há poucos resultados: vaga vista em duas modalidades fica
                                # indefinida e a análise decide pela descrição.
                                if jobs[card["id"]].workplace != modality:
                                    jobs[card["id"]].workplace = "unknown"
                                continue
                            jobs[card["id"]] = Job(
                                source="linkedin",
                                external_id=card["id"],
                                title=card["title"],
                                company=card["company"],
                                url=f"https://www.linkedin.com/jobs/view/{card['id']}/",
                                location=card["location"],
                                workplace=modality,
                                posted_at=card["posted_at"],
                            )
                        if len(cards) < PAGE:
                            break
        except RateLimited:
            log(f"linkedin: limite persistente nas buscas; seguindo com {len(jobs)} vagas listadas")

        out, details = [], 0
        for job in jobs.values():
            if discarded(job, profile):
                out.append(job)
                continue
            if details >= scfg["max_details"]:
                continue
            try:
                job.description = _detail(c, job.external_id, scfg["delay"], log)
            except RateLimited:
                log(f"linkedin: limite persistente nas descrições; {details} baixadas, o resto fica para a próxima busca")
                break
            details += 1
            out.append(job)
    return out
