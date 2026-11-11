from concurrent.futures import ThreadPoolExecutor, as_completed

from jobfinder.llm import LLM
from jobfinder.models import Job
from jobfinder.sources.common import job_key
from jobfinder.sources import (ats_boards, catho, github_issues, gupy, infojobs, linkedin, programathor, recrutei,
                               remotar, solides, vagas_com, web)

SOURCES = {
    "gupy": gupy.fetch,
    "remotar": remotar.fetch,
    "github": github_issues.fetch,
    "greenhouse": ats_boards.fetch_greenhouse,
    "lever": ats_boards.fetch_lever,
    "linkedin": linkedin.fetch,
    "vagas_com": vagas_com.fetch,
    "programathor": programathor.fetch,
    "solides": solides.fetch,
    "catho": catho.fetch,
    "infojobs": infojobs.fetch,
    "recrutei": recrutei.fetch,
}


def collect(profile: dict, cfg: dict, llm: LLM, log) -> list[Job]:
    """Roda todas as fontes habilitadas em paralelo (cada uma é um site diferente) e junta as vagas."""
    enabled = cfg["search"]["sources"]
    tasks = {name: (lambda f=fetch: f(profile, cfg, log))
             for name, fetch in SOURCES.items() if enabled.get(name, {}).get("enabled")}
    if enabled.get("web", {}).get("enabled"):
        tasks["web"] = lambda: web.fetch(profile, cfg, log, llm)
    found: list[Job] = []
    with ThreadPoolExecutor(max_workers=max(len(tasks), 1)) as pool:
        futures = {pool.submit(task): name for name, task in tasks.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                jobs = fut.result()
            except Exception as e:  # noqa: BLE001
                log(f"{name}: falhou ({e})")
                continue
            log(f"{name}: {len(jobs)} vagas")
            found += jobs
    return _dedupe(found)


def _dedupe(jobs: list[Job]) -> list[Job]:
    """A mesma vaga costuma aparecer em mais de uma fonte (ex.: Remotar apontando para a Gupy,
    busca na web achando a vaga do Vagas.com): descarta repetidas pelo link ou por título + empresa."""
    seen, out = set(), []
    for j in jobs:
        keys = {("url", job_key(j.url))}
        if j.company:
            keys.add(("vaga", j.title.lower().strip(), j.company.lower().strip()))
        if keys & seen:
            continue
        seen |= keys
        out.append(j)
    return out
