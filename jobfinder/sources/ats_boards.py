"""Quadros públicos de vagas de empresas específicas (Greenhouse e Lever)."""

from datetime import datetime, timezone

from jobfinder.models import Job
from jobfinder.sources.common import client, get_json, strip_html, too_old


def _workplace(text: str) -> str:
    t = text.lower()
    if "remote" in t or "remoto" in t:
        return "remote"
    if "hybrid" in t or "híbrido" in t or "hibrido" in t:
        return "hybrid"
    return "unknown"


def fetch_greenhouse(profile: dict, cfg: dict, log) -> list[Job]:
    jobs = []
    max_age = cfg["search"]["max_age_days"]
    with client() as c:
        for board in cfg["search"]["sources"]["greenhouse"]["boards"]:
            try:
                data = get_json(c, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", {"content": "true"})
            except Exception as e:  # noqa: BLE001
                log(f"greenhouse {board}: {e}")
                continue
            for it in data.get("jobs", []):
                if too_old(it.get("updated_at"), max_age):
                    continue
                loc = (it.get("location") or {}).get("name", "")
                jobs.append(Job(
                    source="greenhouse",
                    external_id=f"{board}:{it['id']}",
                    title=it.get("title", ""),
                    company=it.get("company_name") or board,
                    url=it.get("absolute_url", ""),
                    description=strip_html(it.get("content")),
                    location=loc,
                    workplace=_workplace(loc + " " + it.get("title", "")),
                    posted_at=it.get("updated_at", ""),
                ))
    return jobs


def fetch_lever(profile: dict, cfg: dict, log) -> list[Job]:
    jobs = []
    max_age = cfg["search"]["max_age_days"]
    with client() as c:
        for company in cfg["search"]["sources"]["lever"]["companies"]:
            try:
                items = get_json(c, f"https://api.lever.co/v0/postings/{company}", {"mode": "json"})
            except Exception as e:  # noqa: BLE001
                log(f"lever {company}: {e}")
                continue
            for it in items:
                created = datetime.fromtimestamp(it.get("createdAt", 0) / 1000, timezone.utc).isoformat()
                if too_old(created, max_age):
                    continue
                cats = it.get("categories") or {}
                loc = cats.get("location", "")
                desc = it.get("descriptionPlain", "") + "\n\n" + "\n\n".join(
                    f"{l.get('text', '')}:\n{strip_html(l.get('content'))}" for l in it.get("lists", [])
                )
                jobs.append(Job(
                    source="lever",
                    external_id=f"{company}:{it['id']}",
                    title=it.get("text", ""),
                    company=company,
                    url=it.get("applyUrl") or it.get("hostedUrl", ""),
                    description=desc,
                    location=loc,
                    workplace=_workplace(f"{loc} {it.get('workplaceType', '')}"),
                    posted_at=created,
                    tags=[v for v in (cats.get("commitment"), cats.get("team")) if v],
                ))
    return jobs
