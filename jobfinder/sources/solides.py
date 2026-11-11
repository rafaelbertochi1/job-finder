"""Sólides Vagas: API pública do portal vagas.solides.com.br."""

from jobfinder.models import Job
from jobfinder.sources.common import client, get_json, strip_html, too_old

URL = "https://vagas.solides.com.br/api/vacancies"
WORKPLACE = {"remoto": "remote", "hibrido": "hybrid", "presencial": "onsite"}


def _job(it: dict) -> Job:
    desc = strip_html(it.get("description"))
    skills = [s.get("name", "") for s in it.get("hardSkills") or []]
    if skills:
        desc += "\n\nRequisitos:\n" + "\n".join(f"- {s}" for s in skills)
    salary = it.get("salary") or {}
    if salary.get("initialRange") or salary.get("finalRange"):
        desc += f"\n\nSalário: {salary.get('initialRange') or ''} - {salary.get('finalRange') or ''}"
    city = (it.get("city") or {}).get("name") or ""
    state = (it.get("state") or {}).get("code") or ""
    return Job(
        source="solides",
        external_id=it["id"],
        title=it.get("title", ""),
        company=it.get("companyName", ""),
        # O redirectLink da API vem quebrado; a página da vaga fica no subdomínio da empresa
        # (empresa confidencial não tem slug, mas o portal abre a vaga pelo id).
        url=f"https://{it['slug']}.vagas.solides.com.br/vaga/{it['id']}" if it.get("slug")
        else f"https://vagas.solides.com.br/vaga/{it['id']}",
        description=desc,
        location=" - ".join(p for p in (city.title(), state) if p),
        workplace=WORKPLACE.get(it.get("jobType") or "", "remote" if it.get("homeOffice") else "unknown"),
        posted_at=it.get("createdAt", ""),
        tags=[s.get("name", "") for s in it.get("seniority") or []],
    )


def fetch(profile: dict, cfg: dict, log) -> list[Job]:
    scfg = cfg["search"]["sources"]["solides"]
    max_age = cfg["search"]["max_age_days"]
    pcd = profile.get("diversity", {}).get("pcd", False)
    jobs: dict[str, Job] = {}
    with client() as c:
        for query in scfg["queries"]:
            for page in range(1, scfg["pages"] + 1):
                try:
                    data = get_json(c, URL, {"page": page, "take": 20, "title": query, "locations": ""})
                except Exception as e:  # noqa: BLE001
                    log(f"solides '{query}': {e}")
                    break
                items = data.get("data", [])
                for it in items:
                    if it.get("isDisabled") or (it.get("pcdOnly") and not pcd) or too_old(it.get("createdAt"), max_age):
                        continue
                    jobs[it["id"]] = _job(it)
                # A API devolve as mais recentes primeiro: página cheia de vagas antigas encerra o termo.
                if page >= data.get("totalPages", 1) or (items and too_old(items[-1].get("createdAt"), max_age)):
                    break
    return list(jobs.values())
