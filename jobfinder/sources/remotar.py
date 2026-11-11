"""Remotar: portal brasileiro só de vagas remotas."""

from jobfinder.models import Job
from jobfinder.sources.common import client, get_json, strip_html, too_old

URL = "https://api.remotar.com.br/jobs"


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    if "remote" not in cfg["search"]["modalities"]:
        return []
    pages = cfg["search"]["sources"]["remotar"]["pages"]
    max_age = cfg["search"]["max_age_days"]
    jobs: dict[str, Job] = {}
    with client() as c:
        for query in profile["search_queries"]:
            for page in range(1, pages + 1):
                try:
                    data = get_json(c, URL, {"search": query, "page": page})
                except Exception as e:  # noqa: BLE001
                    log(f"remotar '{query}': {e}")
                    break
                for it in data.get("data", []):
                    if it.get("expired") or not it.get("active", True) or too_old(it.get("createdAt"), max_age):
                        continue
                    jid = str(it["id"])
                    desc = strip_html(it.get("description"))
                    reqs = [r.get("description", "") for r in it.get("jobRequirements") or []]
                    if reqs:
                        desc += "\n\nRequisitos:\n" + "\n".join(f"- {r}" for r in reqs)
                    salary = it.get("jobSalary") or {}
                    if salary.get("from") or salary.get("to"):
                        desc += f"\n\nSalário: {salary.get('from') or ''} - {salary.get('to') or ''} {salary.get('currency') or ''}"
                    jobs[jid] = Job(
                        source="remotar",
                        external_id=jid,
                        title=it.get("title", ""),
                        company=(it.get("company") or {}).get("name") or it.get("companyDisplayName") or "",
                        url=it.get("externalLink") or f"https://remotar.com.br/job/{jid}",
                        description=desc,
                        location=it.get("city") or "",
                        workplace="remote" if it.get("type") == "remote" else (it.get("type") or "unknown"),
                        posted_at=it.get("createdAt", ""),
                        tags=[(t.get("tag") or {}).get("name", "") for t in it.get("jobTags") or []],
                    )
                meta = data.get("meta", {})
                if page >= meta.get("last_page", 1):
                    break
    return list(jobs.values())
