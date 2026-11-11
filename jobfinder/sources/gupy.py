"""Gupy: maior ATS do Brasil. Usa a mesma busca do portal público portal.gupy.io."""

from jobfinder.models import Job
from jobfinder.sources.common import client, get_json, too_old

URL = "https://portal.gupy.io/api/job-search/jobs"
WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "on-site"}
PAGE = 100


# A busca da Gupy procura o texto no título: "desenvolvedor python júnior" acha bem menos que "python".
NOISE = {"júnior", "junior", "jr", "jr.", "pleno", "sênior", "senior", "remoto", "remota", "home", "office", "vaga", "vagas"}


def queries(profile: dict, scfg: dict) -> list[str]:
    if scfg.get("queries"):
        return scfg["queries"]
    out = []
    for q in profile["search_queries"]:
        short = " ".join(w for w in q.lower().split() if w not in NOISE)
        if short and short not in out:
            out.append(short)
    return out


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    scfg = cfg["search"]["sources"]["gupy"]
    max_age = cfg["search"]["max_age_days"]
    jobs: dict[str, Job] = {}
    with client() as c:
        for query in queries(profile, scfg):
            for modality in cfg["search"]["modalities"]:
                offset = 0
                while offset < scfg["max_per_query"]:
                    try:
                        data = get_json(c, URL, {
                            "jobName": query, "limit": PAGE, "offset": offset,
                            "workplaceType": WORKPLACE[modality],
                        })
                    except Exception as e:  # noqa: BLE001 - uma busca falha, as outras seguem
                        log(f"gupy '{query}' ({modality}): {e}")
                        break
                    items = data.get("data", [])
                    for it in items:
                        if too_old(it.get("publishedDate"), max_age):
                            continue
                        jid = str(it["id"])
                        jobs[jid] = Job(
                            source="gupy",
                            external_id=jid,
                            title=it.get("name", ""),
                            company=it.get("careerPageName", ""),
                            url=it.get("jobUrl", ""),
                            description=it.get("description", ""),
                            location=", ".join(x for x in (it.get("city"), it.get("state")) if x),
                            workplace={"on-site": "onsite"}.get(it.get("workplaceType"), it.get("workplaceType") or "unknown"),
                            posted_at=it.get("publishedDate", ""),
                            tags=[t for t in (it.get("type"),) if t],
                        )
                    if len(items) < PAGE:
                        break
                    offset += PAGE
    return list(jobs.values())
