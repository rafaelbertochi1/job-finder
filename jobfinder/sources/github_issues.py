"""Repositórios da comunidade (backend-br/vagas, frontendbr/vagas...) onde vagas são issues."""

import os
from datetime import datetime, timedelta, timezone

from jobfinder.models import Job
from jobfinder.sources.common import client, get_json


def _workplace(labels: list[str], title: str) -> str:
    text = " ".join(labels + [title]).lower()
    if "remoto" in text or "remote" in text:
        return "remote"
    if "híbrido" in text or "hibrido" in text or "hybrid" in text:
        return "hybrid"
    if "presencial" in text:
        return "onsite"
    return "unknown"


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    repos = cfg["search"]["sources"]["github"]["repos"]
    since = (datetime.now(timezone.utc) - timedelta(days=cfg["search"]["max_age_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Accept": "application/vnd.github+json"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    jobs = []
    with client(headers) as c:
        for repo in repos:
            try:
                for page in range(1, 6):
                    items = get_json(c, f"https://api.github.com/repos/{repo}/issues", {
                        "state": "open", "since": since, "per_page": 100, "page": page,
                        "sort": "created", "direction": "desc",
                    })
                    for it in items:
                        if "pull_request" in it or it.get("created_at", "") < since:
                            continue
                        labels = [l["name"] for l in it.get("labels", [])]
                        jobs.append(Job(
                            source="github",
                            external_id=f"{repo}#{it['number']}",
                            title=it["title"],
                            company="",
                            url=it["html_url"],
                            description=it.get("body") or "",
                            location="",
                            workplace=_workplace(labels, it["title"]),
                            posted_at=it["created_at"],
                            tags=labels,
                        ))
                    if len(items) < 100:
                        break
            except Exception as e:  # noqa: BLE001
                log(f"github {repo}: {e}")
    return jobs
