"""InfoJobs: busca pública (HTML) por termo; remoto no Brasil todo, híbrido/presencial só em SP."""

import html
import re
import time
from datetime import datetime, timezone

from jobfinder.models import Job
from jobfinder.sources.common import browser_client, discarded, known_ids, strip_html, too_old

BASE = "https://www.infojobs.com.br"
# Filtro "idw" do site: 1 presencial, 2 home office, 3 híbrido.
WORKPLACE = {"onsite": "1", "remote": "2", "hybrid": "3"}
LABEL = {"presencial": "onsite", "home office": "remote", "remoto": "remote", "híbrido": "hybrid"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _cards(page: str) -> list[dict]:
    out = []
    for block in page.split('class="card card-shadow card-shadow-hover')[1:]:
        head = re.search(r'data-id="(\d+)"[^>]*data-href="([^"]+)"', block)
        title = re.search(r'js_vacancyTitle">(.*?)</h2>', block, re.S)
        if not head or not title:
            continue
        # O nome da empresa pode vir quebrado em spans (e seguido do selo de verificada).
        company = re.search(r'<div class="text-body">(.*?)(?:<span onclick|</div>)', block, re.S)
        location = re.search(r">\s*([^<>]+? - [A-Z]{2})\s*<", block)
        date = re.search(r'js_date" data-value="([\d/]+ [\d:]+)"', block)
        label = re.search(r">\s*(Presencial|Home office|Remoto|Híbrido)\s*<", block)
        posted = ""
        if date:
            posted = datetime.strptime(date.group(1), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()
        out.append({
            "id": head.group(1),
            "path": head.group(2),
            "title": _clean(title.group(1)),
            "company": _clean(company.group(1)) if company else "",
            "location": _clean(location.group(1)) if location else "",
            "posted_at": posted,
            "workplace": LABEL.get(label.group(1).lower(), "unknown") if label else "unknown",
        })
    return out


def _detail(c, path: str) -> str:
    r = c.get(BASE + path)
    if r.status_code != 200:
        return ""
    text = strip_html(re.sub(r"<(script|style)[^>]*>.*?</\1>", "", r.text, flags=re.S))
    # A descrição vem logo depois dos blocos "Sua adequação / Total de inscritos".
    start = text.find("Total de inscritos")
    end = text.find("Área Profissional", start)
    return text[start + len("Total de inscritos"):end if end > 0 else start + 6000].replace("Visualizar", "", 1).strip() if start >= 0 else ""


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    scfg = cfg["search"]["sources"]["infojobs"]
    max_age = cfg["search"]["max_age_days"]
    seen = known_ids("infojobs")
    jobs: dict[str, Job] = {}
    details = 0
    with browser_client() as c:
        for query in scfg["queries"]:
            slug = re.sub(r"\s+", "-", query.strip().lower())
            for modality in cfg["search"]["modalities"]:
                path = f"/vagas-de-emprego-{slug}.aspx" if modality == "remote" else f"/vagas-de-emprego-{slug}-em-{scfg['state']}.aspx"
                try:
                    r = c.get(BASE + path, params={"idw": WORKPLACE[modality], "campo": "griddate", "orden": "desc"})
                except Exception as e:  # noqa: BLE001
                    log(f"infojobs '{query}' ({modality}): {e}")
                    continue
                for card in _cards(r.text) if r.status_code == 200 else []:
                    if card["id"] in seen or card["id"] in jobs or too_old(card["posted_at"], max_age):
                        continue
                    workplace = card["workplace"] if card["workplace"] != "unknown" else modality
                    job = Job(
                        source="infojobs",
                        external_id=card["id"],
                        title=card["title"],
                        company=card["company"],
                        url=BASE + card["path"],
                        location="" if workplace == "remote" else card["location"],
                        workplace=workplace,
                        posted_at=card["posted_at"],
                    )
                    if not discarded(job, profile) and details < scfg["max_details"]:
                        job.description = _detail(c, card["path"])
                        details += 1
                        time.sleep(scfg["delay"])
                    jobs[card["id"]] = job
                time.sleep(scfg["delay"])
    return list(jobs.values())
