"""Descoberta na web: o Claude pesquisa posts e páginas de vagas que os portais não cobrem."""

import hashlib
import json
from datetime import date

from jobfinder.llm import LLM, LLMError, STR, arr, enum, obj
from jobfinder.models import Job

SCHEMA = obj({
    "jobs": arr(obj({
        "title": STR,
        "company": STR,
        "url": STR,
        "location": STR,
        "workplace": enum("remote", "hybrid", "onsite", "unknown"),
        "posted_at": STR,
        "description": STR,
    })),
})

SYSTEM = """Você é um caçador de vagas de TI no Brasil. Use a busca na web para encontrar vagas
ABERTAS e RECENTES compatíveis com o perfil do candidato, priorizando vagas remotas para quem mora
no Brasil. Procure além dos grandes portais: páginas de carreira de empresas, posts públicos de
"estamos contratando" (LinkedIn, X, comunidades), Programathor, Trampos, InHire, Remotar, GeekHunter etc.

Regras:
- Inclua somente vagas cuja URL apareceu de fato nos resultados da busca. Nunca invente URL, empresa ou vaga.
- Ignore agregadores que escondem a empresa ou exigem cadastro para ver o link (ex.: melhoresempregos.com,
  vagas confidenciais genéricas do sylision.com) e anúncios com "salário estimado" pelo próprio site.
  Prefira o link do site/ATS da empresa (Gupy, InHire, Greenhouse, Lever, página de carreiras).
- Descarte vagas claramente encerradas, antigas (mais de {max_age} dias) ou fora do Brasil
  (exceto remotas que aceitem candidatos no Brasil).
- Em "description" resuma requisitos, stack, senioridade, regime, salário e como se candidatar
  (inclua e-mail de contato se a publicação pedir CV por e-mail).
- posted_at no formato AAAA-MM-DD quando souber; senão string vazia.
- Hoje é {today}."""


def fetch(profile: dict, cfg: dict, log, llm: LLM) -> list[Job]:
    wcfg = cfg["search"]["sources"]["web"]
    brief = {
        "headline": profile["headline"],
        "seniority": profile["seniority"],
        "target_titles": profile["target_titles"],
        "top_skills": [s["name"] for s in profile["skills"][:15]],
        "preferences": profile["preferences"],
        "city": f"{profile['city']}/{profile['state']}",
    }
    try:
        result = llm.json(
            system=SYSTEM.format(max_age=cfg["search"]["max_age_days"], today=date.today().isoformat()),
            content=f"Perfil do candidato:\n{json.dumps(brief, ensure_ascii=False, indent=1)}\n\n"
                    "Encontre até 25 vagas compatíveis.",
            schema=SCHEMA,
            effort="medium",
            max_tokens=32000,
            tools=[{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": wcfg["max_searches"],
                "user_location": {"type": "approximate", "country": "BR"},
            }],
        )
    except LLMError as e:
        log(f"web: {e}")
        return []
    jobs = []
    for it in result["jobs"]:
        if not it["url"].startswith("http"):
            continue
        jobs.append(Job(
            source="web",
            external_id=hashlib.sha1(it["url"].encode()).hexdigest()[:16],
            title=it["title"],
            company=it["company"],
            url=it["url"],
            description=it["description"],
            location=it["location"],
            workplace=it["workplace"],
            posted_at=it["posted_at"],
        ))
    return jobs
