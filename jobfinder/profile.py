"""Monta o perfil profissional: lê o currículo e faz uma pequena entrevista."""

import base64
import json
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from jobfinder.config import PROFILE_PATH
from jobfinder.llm import ARR_STR, LLM, BOOL, INT, NUM, STR, arr, enum, obj

console = Console()

SKILL = obj({"name": STR, "level": enum("basico", "intermediario", "avancado"), "years": NUM})
PROFILE_SCHEMA = obj({
    "name": STR,
    "email": STR,
    "phone": STR,
    "city": STR,
    "state": STR,
    "linkedin": STR,
    "github": STR,
    "portfolio": STR,
    "headline": STR,
    "summary": STR,
    "seniority": enum("estagio", "junior", "pleno", "senior", "especialista", "lideranca"),
    "years_experience": NUM,
    "areas": ARR_STR,
    "target_titles": ARR_STR,
    "skills": arr(SKILL),
    "languages": arr(obj({"language": STR, "level": STR})),
    "education": ARR_STR,
    "certifications": ARR_STR,
    "experiences": arr(obj({
        "company": STR, "role": STR, "start": STR, "end": STR, "highlights": ARR_STR,
    })),
    "preferences": obj({
        "modalities": arr(enum("remote", "hybrid", "onsite")),
        "accepted_cities": ARR_STR,
        "relocate": BOOL,
        "contract_types": ARR_STR,
        "salary_min_brl": INT,
        "salary_notes": STR,
        "availability": STR,
        "deal_breakers": ARR_STR,
        "interests": ARR_STR,
    }),
    "search_queries": ARR_STR,
    "keywords": ARR_STR,
    "form_answers": arr(obj({"question": STR, "answer": STR})),
})

QUESTION = obj({"id": STR, "question": STR, "why": STR, "suggestion": STR})

EXTRACT_SYSTEM = """Você é um recrutador técnico sênior especializado no mercado de TI brasileiro.
Sua tarefa é entender a posição de mercado de um profissional a partir do currículo e
preparar uma entrevista curta para completar o que falta.

Regras:
- Extraia somente o que o currículo sustenta. Campos desconhecidos: string vazia, lista vazia, 0 ou false.
- seniority: avalie pelo conteúdo real (escopo, autonomia, anos, impacto), não só pelo cargo escrito.
- target_titles: 4 a 8 títulos de vaga que o mercado brasileiro usa para esse perfil
  (ex.: "Desenvolvedor Backend Python Pleno", "Engenheiro de Dados", "Analista de Suporte N2").
- search_queries: 6 a 12 termos curtos para buscar em portais de vagas (1 a 3 palavras cada,
  ex.: "python", "backend", "engenheiro de dados", "react native"). Inclua variações em inglês se o perfil
  permitir vagas em inglês.
- keywords: 15 a 40 palavras-chave (tecnologias, cargos, áreas) que, se aparecerem numa vaga, indicam
  possível aderência. Minúsculas.
- form_answers: respostas prontas para perguntas comuns de formulários de candidatura
  (ex.: "Por que quer trabalhar aqui?" não; foque em fatos: pretensão salarial, disponibilidade,
  nível de inglês, tempo de experiência com X, modelo de contratação aceito).
- preferences: o candidato busca principalmente vagas REMOTAS no Brasil; presencial/híbrido só nas
  cidades que ele aceitar.

Depois, gere de 6 a 12 perguntas para a entrevista, em português, diretas e respondíveis em uma linha.
Cubra obrigatoriamente o que estiver faltando entre: contato (e-mail, telefone, LinkedIn, GitHub),
cidade/UF, modalidades aceitas e cidades aceitas para presencial/híbrido, disposição para mudança,
regime (CLT/PJ), pretensão salarial mínima, disponibilidade para início, nível de inglês,
cargos/áreas que quer buscar (pode querer migrar de área), e o que NÃO aceita (deal-breakers).
Pergunte também sobre lacunas relevantes do currículo (ex.: anos de experiência com a stack principal).
Em "suggestion" coloque o valor que você inferiu (ou vazio), para a pessoa só confirmar com Enter."""

MERGE_SYSTEM = """Você é um recrutador técnico sênior do mercado de TI brasileiro.
Atualize o perfil do candidato com as respostas da entrevista. As respostas têm prioridade sobre
o que foi inferido do currículo. Resposta vazia significa "manter a sugestão".
Recalcule target_titles, search_queries, keywords e form_answers considerando as respostas
(ex.: se a pessoa quer migrar de área, inclua os cargos da nova área).
Mantenha campos desconhecidos como string vazia, lista vazia, 0 ou false."""


def read_cv(path: Path, pdf_as_document: bool) -> list:
    """Converte o currículo em blocos de conteúdo para o Claude.

    Na API o PDF vai inteiro (o Claude enxerga o layout). Pelo Claude Code, vai o texto extraído.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Currículo não encontrado em {path}. Coloque o arquivo lá ou ajuste cv_path no config.yaml."
        )
    suffix = path.suffix.lower()
    if suffix == ".pdf" and pdf_as_document:
        data = base64.standard_b64encode(path.read_bytes()).decode()
        return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}]
    if suffix == ".pdf":
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        if len(text.strip()) < 200:
            raise ValueError(
                "Não consegui extrair texto do PDF (parece ser imagem/escaneado). "
                "Exporte o currículo como PDF de texto, DOCX ou TXT."
            )
    elif suffix == ".docx":
        import docx

        text = "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    return [{"type": "text", "text": f"<curriculo>\n{text}\n</curriculo>"}]


def interview(questions: list[dict]) -> list[dict]:
    console.print(
        "\n[bold cyan]Entrevista rápida[/] — responda em uma linha. "
        "Enter aceita a sugestão entre colchetes; '-' deixa em branco.\n"
    )
    answers = []
    for i, q in enumerate(questions, 1):
        console.print(f"[dim]{q['why']}[/]")
        ans = Prompt.ask(f"[bold]{i}. {q['question']}[/]", default=q["suggestion"] or "", show_default=bool(q["suggestion"]))
        answers.append({"question": q["question"], "answer": "" if ans.strip() == "-" else ans.strip()})
    console.print("\n[bold]Algo mais que eu deva saber?[/] (objetivos, empresas dos sonhos, restrições...)")
    extra = Prompt.ask("Opcional", default="")
    if extra.strip():
        answers.append({"question": "Informações adicionais", "answer": extra.strip()})
    return answers


def build_profile(llm: LLM, cvs: list[dict]) -> dict:
    # Um currículo por área (de preferência em português): as versões em outro idioma repetem o conteúdo.
    by_track: dict[str, dict] = {}
    for c in cvs:
        current = by_track.get(c["track"])
        if current is None or (current["language"] != "pt" and c["language"] == "pt"):
            by_track[c["track"]] = c
    content = []
    for track, c in by_track.items():
        console.print(f"Lendo currículo ({track}): [bold]{c['path'].name}[/] ...")
        content.append({"type": "text", "text": f"Currículo da área '{track}':"})
        content += read_cv(c["path"], pdf_as_document=llm.backend == "api")
    note = (
        "O candidato tem currículos diferentes para cada área acima; o perfil deve cobrir todas elas "
        "(target_titles, search_queries e keywords de cada área)."
        if len(by_track) > 1 else ""
    )
    first = llm.json(
        system=EXTRACT_SYSTEM,
        content=content + [{"type": "text", "text": f"Analise o(s) currículo(s) acima. {note}".strip()}],
        schema=obj({"profile": PROFILE_SCHEMA, "questions": arr(QUESTION)}),
        effort="high",
        max_tokens=32000,
    )
    draft = first["profile"]
    console.print(f"\nPerfil inicial: [bold]{draft['headline'] or draft['name']}[/] "
                  f"({draft['seniority']}, ~{draft['years_experience']:g} anos)")

    answers = interview(first["questions"])

    console.print("\nConsolidando seu perfil ...")
    final = llm.json(
        system=MERGE_SYSTEM,
        content=(
            f"<perfil_inferido>\n{json.dumps(draft, ensure_ascii=False, indent=1)}\n</perfil_inferido>\n\n"
            f"<entrevista>\n{json.dumps(answers, ensure_ascii=False, indent=1)}\n</entrevista>"
        ),
        schema=PROFILE_SCHEMA,
        effort="high",
        max_tokens=32000,
    )
    save_profile(final)
    return final


def save_profile(profile: dict):
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        raise SystemExit("Perfil não encontrado. Rode primeiro:  python -m jobfinder setup")
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def print_profile(p: dict):
    pref = p["preferences"]
    console.print(f"\n[bold]{p['name']}[/] — {p['headline']}")
    console.print(f"Senioridade: {p['seniority']} | Experiência: ~{p['years_experience']:g} anos | {p['city']}/{p['state']}")
    console.print(f"Áreas: {', '.join(p['areas'])}")
    console.print(f"Cargos-alvo: {', '.join(p['target_titles'])}")
    console.print(f"Skills: {', '.join(s['name'] for s in p['skills'][:25])}")
    console.print(f"Modalidades: {', '.join(pref['modalities'])} | Cidades: {', '.join(pref['accepted_cities']) or '-'}")
    console.print(f"Regime: {', '.join(pref['contract_types']) or '-'} | Pretensão mínima: R$ {pref['salary_min_brl']:,}".replace(",", "."))
    console.print(f"Não aceita: {', '.join(pref['deal_breakers']) or '-'}")
    console.print(f"Buscas: {', '.join(p['search_queries'])}")
    console.print(f"\nArquivo editável: [cyan]{PROFILE_PATH}[/]")
