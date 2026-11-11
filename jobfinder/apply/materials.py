"""Gera carta de apresentação, e-mail e pitch personalizados para cada vaga."""

import json

from jobfinder.config import OUTPUT_DIR
from jobfinder.llm import LLM, STR, obj
from jobfinder.models import Job

SCHEMA = obj({
    "email_subject": STR,
    "email_body": STR,
    "cover_letter": STR,
    "short_pitch": STR,
})

SYSTEM = """Você escreve candidaturas para profissionais de TI. Tom: direto, profissional, humano,
sem clichês ("sou apaixonado por desafios") e sem exageros. Escreva no idioma indicado.

Regras inegociáveis:
- Use SOMENTE fatos do perfil do candidato. Nunca invente experiência, empresa, número ou tecnologia.
- Conecte 2 ou 3 experiências/resultados reais do candidato aos requisitos mais importantes da vaga.
- Se faltar algum requisito, não minta; destaque o que é transferível.

Formatos:
- email_subject: assunto curto com o cargo e o nome do candidato.
- email_body: e-mail de candidatura (até 180 palavras) que menciona o currículo em anexo, com assinatura
  (nome, telefone, LinkedIn/GitHub quando existirem).
- cover_letter: carta de apresentação (até 250 palavras) para colar em formulários.
- short_pitch: 2 a 3 frases para campos curtos tipo "Por que você é um bom candidato?"."""


def generate(llm: LLM, profile: dict, job: Job, analysis: dict) -> dict:
    lang = "inglês" if analysis.get("language") == "en" else "português do Brasil"
    return llm.json(
        system=SYSTEM,
        content=(
            f"<perfil_do_candidato>\n{json.dumps(profile, ensure_ascii=False, indent=1)}\n</perfil_do_candidato>\n\n"
            f"<vaga>\n{job.as_prompt()}\n</vaga>\n\n"
            f"<analise_de_aderencia>\n{json.dumps(analysis, ensure_ascii=False, indent=1)}\n</analise_de_aderencia>\n\n"
            f"Escreva os materiais em {lang}."
        ),
        schema=SCHEMA,
        effort="medium",
        max_tokens=8000,
    )


def save(job_id: str, job: Job, materials: dict):
    folder = OUTPUT_DIR / "applications" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "materiais.md").write_text(
        f"# {job.title} — {job.company}\n\n{job.url}\n\n"
        f"## Pitch curto\n\n{materials['short_pitch']}\n\n"
        f"## Carta de apresentação\n\n{materials['cover_letter']}\n\n"
        f"## E-mail\n\n**Assunto:** {materials['email_subject']}\n\n{materials['email_body']}\n",
        encoding="utf-8",
    )
    return folder
