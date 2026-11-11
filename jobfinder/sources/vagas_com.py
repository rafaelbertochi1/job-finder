"""Vagas.com.br: busca pública (HTML) por termo, uma página por modalidade."""

import html
import re
import time
from datetime import datetime, timezone

from jobfinder.models import Job
from jobfinder.sources.common import browser_client, days_ago, discarded, known_ids, strip_html, too_old

BASE = "https://www.vagas.com.br"
# Filtro "m[]" do site para cada modalidade.
WORKPLACE = {"remote": "100% Home Office", "hybrid": "Empresa e Home Office", "onsite": "Na empresa"}


def _text(pattern: str, block: str) -> str:
    m = re.search(pattern, block, re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip() if m else ""


def _posted(label: str) -> str:
    """'Hoje', 'Ontem', 'Há 3 dias' ou 'dd/mm/aaaa' -> ISO 8601."""
    label = label.strip().lower()
    if label == "hoje":
        return days_ago(0)
    if label == "ontem":
        return days_ago(1)
    m = re.search(r"(\d+)\s+dias?", label)
    if m:
        return days_ago(int(m.group(1)))
    try:
        return datetime.strptime(label, "%d/%m/%Y").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return ""


def _cards(page: str) -> list[dict]:
    out = []
    for block in page.split('<li class="vaga')[1:]:
        link = re.search(r'data-id-vaga="(\d+)".*?href="(/vagas/v\d+/[^"]+)"', block, re.S)
        if not link:
            continue
        out.append({
            "id": link.group(1),
            "path": link.group(2),
            "title": _text(r'class="link-detalhes-vaga"[^>]*>(.*?)</a>', block),
            "company": _text(r'class="emprVaga">(.*?)</span>', block),
            "level": _text(r'class="nivelVaga">(.*?)</span>', block),
            "location": _text(r'class="vaga-local">\s*<i[^>]*></i>([^<]*)', block),
            "posted_at": _posted(_text(r'class="data-publicacao">(.*?)</span>', block)),
        })
    return out


def _detail(c, path: str) -> str:
    r = c.get(BASE + path)
    if r.status_code != 200:
        return ""
    m = re.search(r'data-testid="JobDescription">(.*?)</div>', r.text, re.S)
    desc = strip_html(m.group(1)) if m else ""
    contract = re.search(r'class="info-modelo-contratual" title="([^"]+)"', r.text)
    if contract:
        desc += f"\n\nContratação: {contract.group(1)}"
    return desc


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    scfg = cfg["search"]["sources"]["vagas_com"]
    max_age = cfg["search"]["max_age_days"]
    seen = known_ids("vagas_com")
    jobs: dict[str, Job] = {}
    details = 0
    with browser_client() as c:
        for query in scfg["queries"]:
            slug = re.sub(r"\s+", "-", query.strip().lower())
            for modality in cfg["search"]["modalities"]:
                try:
                    r = c.get(f"{BASE}/vagas-de-{slug}", params={"ordenar_por": "mais_recentes", "m[]": WORKPLACE[modality]})
                except Exception as e:  # noqa: BLE001
                    log(f"vagas_com '{query}' ({modality}): {e}")
                    continue
                for card in _cards(r.text) if r.status_code == 200 else []:
                    if card["id"] in seen or card["id"] in jobs or too_old(card["posted_at"], max_age):
                        continue
                    job = Job(
                        source="vagas_com",
                        external_id=card["id"],
                        title=card["title"],
                        company=card["company"],
                        url=BASE + card["path"],
                        location="" if modality == "remote" else card["location"],
                        workplace=modality,
                        posted_at=card["posted_at"],
                        tags=[card["level"]] if card["level"] else [],
                    )
                    if not discarded(job, profile) and details < scfg["max_details"]:
                        job.description = _detail(c, card["path"])
                        details += 1
                        time.sleep(scfg["delay"])
                    jobs[card["id"]] = job
                time.sleep(scfg["delay"])
    return list(jobs.values())
