"""Pré-filtro barato (sem IA) + análise de aderência de cada vaga com o Claude."""

import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

from jobfinder.llm import ARR_STR, INT, LLM, LLMError, STR, enum, obj
from jobfinder.models import Job

ANALYSIS_SCHEMA = obj({
    "score": INT,
    "verdict": enum("aplicar", "talvez", "descartar"),
    "summary": STR,
    "matched_skills": ARR_STR,
    "missing_skills": ARR_STR,
    "seniority_fit": enum("abaixo", "adequada", "acima", "indefinida"),
    "workplace": enum("remote", "hybrid", "onsite", "unknown"),
    "location": STR,
    "contract": enum("CLT", "PJ", "estagio", "outro", "indefinido"),
    "salary": STR,
    "red_flags": ARR_STR,
    "apply_method": enum("email", "form", "platform", "unknown"),
    "apply_email": STR,
    "apply_url": STR,
    "language": enum("pt", "en"),
})

SYSTEM = """Você é um recrutador técnico sênior do mercado de TI brasileiro avaliando se uma vaga
vale a candidatura deste candidato. Seja honesto e criterioso: uma nota alta deve significar
chance real de avançar no processo.

<perfil_do_candidato>
{profile}
</perfil_do_candidato>

Como pontuar (score de 0 a 100):
- Aderência técnica (stack obrigatória x skills do candidato) é o fator principal.
- Senioridade: vaga muito acima ou muito abaixo do nível do candidato reduz bastante a nota.
- Modalidade: o candidato prioriza REMOTO. Remoto no Brasil = sem penalidade. Híbrido/presencial
  só é aceitável nas cidades aceitas pelo candidato (ou se ele aceita mudança); fora disso, score no máximo 20.
- Qualquer deal-breaker do candidato presente na vaga => verdict "descartar" e score no máximo 25.
- Vagas exclusivas para fora do Brasil sem aceitar candidatos daqui => score no máximo 15.
- Estágio: só vale em empresa muito grande (bancos, big techs e as de "internship_companies"); nesses
  casos avalie como uma vaga de nível adequado (não penalize por ser abaixo de júnior nem por a bolsa
  ficar abaixo do piso de CLT). Estágio em empresa que não é grande => verdict "descartar".
- verdict: "aplicar" (>= 70, boa chance), "talvez" (50-69), "descartar" (< 50).

Campos de candidatura:
- apply_method: "email" se a vaga pede envio de currículo por e-mail; "form" se há link para formulário
  próprio/ATS (Greenhouse, Lever, InHire, site da empresa); "platform" se exige conta em plataforma
  (Gupy, LinkedIn, Indeed...); "unknown" se não dá para saber.
- apply_email: e-mail de candidatura informado na vaga (ou string vazia).
- apply_url: link direto de candidatura se houver no texto; senão o link da vaga.
- language: idioma em que a candidatura deve ser escrita.
- summary: 1 a 2 frases em português explicando a nota."""


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return text.lower()


# Palavras no título que indicam nível acima do candidato.
ABOVE = {
    "estagio": [],
    "junior": ["senior", "sr", "pleno", "pl", "especialista", "specialist", "lead", "lider", "staff",
               "principal", "coordenador", "coordenadora", "gerente", "manager", "head", "tech lead", "arquiteto"],
    "pleno": ["senior", "sr", "especialista", "specialist", "staff", "principal", "coordenador", "gerente", "manager", "head"],
}
INTERNSHIP = ["estagio", "estagiario", "estagiaria", "intern", "internship", "trainee", "aprendiz"]
# Palavras do perfil que aparecem em quase toda vaga (modalidade, contrato, nível): sozinhas não
# indicam aderência e deixavam passar vagas de vendas, jurídico etc.
GENERIC_TERMS = {"remoto", "hibrido", "presencial", "clt", "pj", "junior", "jr", "estagio"}
# Termos fracos: só contam se vierem acompanhados de outro termo do perfil.
WEAK_TERMS = {"excel", "automacao", "http", "git"}
# Título de TI passa mesmo sem palavra-chave na descrição (descrição curta ou não baixada).
TECH_TITLE = ["desenvolvedor", "desenvolvedora", "developer", "programador", "programadora", "software",
              "engenheiro de dados", "engenheira de dados", "data", "dados", "bi", "business intelligence",
              "backend", "back-end", "back end", "analista de sistemas", "sistemas", "devops", "ti"]


def _has_word(text: str, words: list[str]) -> str | None:
    for w in words:
        if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text):
            return w
    return None


def prefilter(job: Job, profile: dict) -> str | None:
    """Retorna o motivo do descarte, ou None se a vaga segue para análise."""
    pref = profile["preferences"]
    title = _norm(job.title)
    # Vagas que mencionam também o nível do candidato ("Júnior/Pleno") seguem para análise.
    own_level = _has_word(title, [_norm(profile["seniority"]), "jr"] if profile["seniority"] == "junior" else [_norm(profile["seniority"])])
    above = _has_word(title, ABOVE.get(profile["seniority"], []))
    if above and not own_level:
        return f"nível acima ({above})"
    if _has_word(title, INTERNSHIP):
        # Estágio só passa em empresa muito grande (bancos, big techs...), quando o perfil lista quais.
        big = [_norm(c) for c in pref.get("internship_companies", [])]
        company = _norm(job.company)
        if not any(_has_word(company, [b]) for b in big if b):
            return "estágio fora de empresa grande"
    modalities = pref["modalities"] or ["remote"]
    if job.workplace in ("hybrid", "onsite"):
        if job.workplace not in modalities:
            return f"modalidade {job.workplace} não aceita"
        loc = _norm(job.location)
        cities = [_norm(c) for c in pref["accepted_cities"]]
        if loc and cities and not pref["relocate"] and not any(c and c in loc for c in cities):
            return f"{job.workplace} em {job.location}, fora das cidades aceitas"
    elif job.workplace == "remote" and "remote" not in modalities:
        return "remoto não aceito"

    haystack = _norm(" ".join([job.title, job.description, " ".join(job.tags)]))
    terms = {_norm(k) for k in profile["keywords"] + profile["search_queries"] + profile["target_titles"]}
    matched = {t for t in terms - GENERIC_TERMS
               if t and re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", haystack)}
    if matched - WEAK_TERMS or len(matched) >= 2 or _has_word(title, TECH_TITLE):
        return None
    return "nenhuma palavra-chave do perfil"


class Matcher:
    def __init__(self, llm: LLM, profile: dict, tracks: list[str]):
        self.llm = llm
        profile_text = json.dumps(
            {k: v for k, v in profile.items() if k != "form_answers"}, ensure_ascii=False, indent=1
        )
        self.system = SYSTEM.format(profile=profile_text)
        # "track": qual dos currículos do candidato (por área) combina mais com a vaga.
        self.system += (
            f"\n- track: área da vaga entre {', '.join(tracks)} — define qual currículo será enviado."
        )
        self.schema = {
            **ANALYSIS_SCHEMA,
            "properties": {**ANALYSIS_SCHEMA["properties"], "track": enum(*tracks)},
            "required": ANALYSIS_SCHEMA["required"] + ["track"],
        }

    def analyze(self, job: Job) -> dict:
        return self.llm.json(
            system=self.system,
            content=f"<vaga>\n{job.as_prompt()}\n</vaga>",
            schema=self.schema,
            effort="low",
            max_tokens=4000,
        )

    def analyze_many(self, jobs: list[Job], workers: int):
        """Gera (job, analysis | None, erro | None) conforme as análises terminam."""
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.analyze, j): j for j in jobs}
            for fut in as_completed(futures):
                job = futures[fut]
                try:
                    yield job, fut.result(), None
                except LLMError as e:
                    yield job, None, str(e)
