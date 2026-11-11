"""ProgramaThor: portal de vagas de TI (HTML), listagens por tecnologia."""

import html
import re
import time

from jobfinder.models import Job
from jobfinder.sources.common import browser_client, discarded, known_ids, strip_html

BASE = "https://programathor.com.br"
CARD = re.compile(r'<a href="/jobs/(\d+)-([^"]+)">(.*?)</a>', re.S)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _span(icon: str, block: str) -> str:
    m = re.search(rf"<span><i class='[^']*{icon}[^']*'></i>(.*?)</span>", block, re.S)
    return _clean(m.group(1)) if m else ""


def _workplace(location: str) -> str:
    loc = location.lower()
    if "remoto" in loc:
        return "remote"
    if "híbrido" in loc or "hibrido" in loc:
        return "hybrid"
    return "onsite" if loc else "unknown"


def _cards(page: str) -> list[dict]:
    out = []
    for jid, slug, block in CARD.findall(page):
        if "Vencida" in block:
            continue
        title = re.search(r"<h3[^>]*>(.*?)</h3>", block, re.S)
        location = _span("fa-map-marker-alt", block)
        out.append({
            "id": jid,
            "path": f"/jobs/{jid}-{slug}",
            "title": _clean(title.group(1)) if title else "",
            "company": _span("fa-briefcase", block),
            "location": location,
            "level": _span("fa-chart-bar", block),
            "contract": _span("fa-file-alt", block),
            "tags": [_clean(t) for t in re.findall(r"<span class='tag-list[^']*'>(.*?)</span>", block)],
        })
    return out


def _detail(c, path: str) -> str:
    r = c.get(BASE + path)
    if r.status_code != 200:
        return ""
    m = re.search(r'class="wrapper-content-job-show"(.*?)class="bloc-job-offer-matching', r.text, re.S)
    return strip_html(m.group(1).split(">", 1)[-1]) if m else ""


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    scfg = cfg["search"]["sources"]["programathor"]
    seen = known_ids("programathor")
    jobs: dict[str, Job] = {}
    details = 0
    with browser_client() as c:
        for listing in scfg["listings"]:
            for page in range(1, scfg["pages"] + 1):
                try:
                    r = c.get(f"{BASE}/{listing}", params={"page": page})
                except Exception as e:  # noqa: BLE001
                    log(f"programathor '{listing}': {e}")
                    break
                cards = _cards(r.text) if r.status_code == 200 else []
                for card in cards:
                    if card["id"] in seen or card["id"] in jobs:
                        continue
                    location = re.sub(r"\s*\((Híbrido|Presencial)\)", "", card["location"])
                    job = Job(
                        source="programathor",
                        external_id=card["id"],
                        title=card["title"],
                        company=card["company"],
                        url=BASE + card["path"],
                        location="" if location.lower() == "remoto" else location,
                        workplace=_workplace(card["location"]),
                        tags=card["tags"] + [t for t in (card["level"], card["contract"]) if t],
                    )
                    if not discarded(job, profile) and details < scfg["max_details"]:
                        # Algumas vagas devolvem erro 500 no detalhe: fica com os dados do card.
                        job.description = _detail(c, card["path"])
                        details += 1
                        time.sleep(scfg["delay"])
                    jobs[card["id"]] = job
                if not cards:
                    break
                time.sleep(scfg["delay"])
    return list(jobs.values())
