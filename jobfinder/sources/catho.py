"""Catho: busca pública (HTML) por termo no estado de SP, das mais recentes para as mais antigas.

A Catho não expõe a modalidade na listagem nem tem filtro de remoto por URL; por isso a busca
fica restrita a SP (onde presencial/híbrido interessam) e a modalidade sai da descrição.
"""

import html
import re
import time
from datetime import datetime, timezone

from jobfinder.models import Job
from jobfinder.sources.common import browser_client, days_ago, discarded, known_ids, strip_html, too_old

BASE = "https://www.catho.com.br"


def _clean(s: str) -> str:
    # Os nomes vêm com entidades escapadas duas vezes (ex.: "S&amp;#xE3;o").
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", "", s)))).strip()


def _posted(label: str) -> str:
    """'Hoje', 'Ontem' ou 'em 09/10' (sem ano: assume o corrente, ou o anterior se cair no futuro)."""
    if label in ("Hoje", "Ontem"):
        return days_ago(0 if label == "Hoje" else 1)
    ddmm = label.removeprefix("em ")
    now = datetime.now(timezone.utc)
    try:
        dt = datetime.strptime(f"{ddmm}/{now.year}", "%d/%m/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    if dt > now:
        dt = dt.replace(year=now.year - 1)
    return dt.isoformat(timespec="seconds")


def _cards(page: str) -> list[dict]:
    out = []
    for block in page.split('data-offer-item="')[1:]:
        jid = block.split('"', 1)[0]
        link = re.search(r'class="title_offer">\s*<a href="(/vagas/[^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not jid.isdigit() or not link:
            continue
        company = re.search(r'<p class="mb-2">\s*<span class="text-12">(.*?)</span>', block, re.S)
        location = re.search(r'i_job_location"></span>\s*<strong>[^<]*</strong>\s*-\s*([^<]*)</p>', block, re.S)
        posted = re.search(r"(?:Publicada|Atualizada) (Hoje|Ontem|em \d\d/\d\d)", block)
        out.append({
            "id": jid,
            "path": link.group(1),
            "title": _clean(link.group(2)),
            "company": _clean(company.group(1)) if company else "",
            "location": _clean(location.group(1)) if location else "",
            "posted_at": _posted(posted.group(1)) if posted else "",
        })
    return out


def _detail(c, path: str) -> str:
    r = c.get(BASE + path)
    if r.status_code != 200:
        return ""
    text = strip_html(re.sub(r"<(script|style)[^>]*>.*?</\1>", "", r.text, flags=re.S))
    start, end = text.find("Sobre a vaga"), text.find("Denunciar esta vaga")
    return text[start:end].strip() if start >= 0 else ""


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    scfg = cfg["search"]["sources"]["catho"]
    max_age = cfg["search"]["max_age_days"]
    seen = known_ids("catho")
    jobs: dict[str, Job] = {}
    details = 0
    with browser_client() as c:
        for query in scfg["queries"]:
            slug = re.sub(r"\s+", "-", query.strip().lower())
            for page in range(1, scfg["pages"] + 1):
                try:
                    r = c.get(f"{BASE}/vagas/{slug}/{scfg['state']}/", params={"order": "dataAtualizacao", "page": page})
                except Exception as e:  # noqa: BLE001
                    log(f"catho '{query}': {e}")
                    break
                cards = _cards(r.text) if r.status_code == 200 else []
                for card in cards:
                    if card["id"] in seen or card["id"] in jobs or too_old(card["posted_at"], max_age):
                        continue
                    job = Job(
                        source="catho",
                        external_id=card["id"],
                        title=card["title"],
                        company=card["company"],
                        url=BASE + card["path"],
                        location=f"{card['location']} - SP" if card["location"] else "",
                        posted_at=card["posted_at"],
                    )
                    if not discarded(job, profile) and details < scfg["max_details"]:
                        job.description = _detail(c, card["path"])
                        details += 1
                        time.sleep(scfg["delay"])
                    jobs[card["id"]] = job
                # Ordenada por data: página cheia de vagas antigas encerra o termo.
                if not cards or all(too_old(x["posted_at"], max_age) for x in cards if x["posted_at"]):
                    break
                time.sleep(scfg["delay"])
    return list(jobs.values())
