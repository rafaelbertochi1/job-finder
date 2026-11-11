"""Gera currículos novos (PDF + DOCX) a partir do perfil, um por área e idioma.

O conteúdo é escrito pelo Claude usando só fatos do perfil e dos currículos originais; o layout é
fixo, em uma coluna, sem tabelas nem imagens, para os sistemas de recrutamento (ATS) lerem bem.
"""

import html
import json
import re
import unicodedata
from pathlib import Path

from jobfinder.config import DATA_DIR, ROOT, all_cvs
from jobfinder.llm import ARR_STR, LLM, STR, arr, obj
from jobfinder.profile import read_cv

OUT_DIR = DATA_DIR / "cv" / "gerados"

CV_SCHEMA = obj({
    "headline": STR,
    "summary": STR,
    "skills": arr(obj({"group": STR, "items": ARR_STR})),
    "experiences": arr(obj({
        "role": STR, "company": STR, "location": STR, "period": STR, "bullets": ARR_STR,
    })),
    "projects": arr(obj({"name": STR, "description": STR, "tech": ARR_STR, "link": STR})),
    "education": arr(obj({"degree": STR, "institution": STR, "period": STR})),
    "certifications": ARR_STR,
    "languages": arr(obj({"language": STR, "level": STR})),
    "extras": ARR_STR,
    "tips": ARR_STR,
})

SYSTEM = """Você é um especialista em currículos de TI para o mercado brasileiro e internacional.
Escreva o conteúdo de um currículo de UMA página (no máximo duas se houver muita experiência
relevante), direcionado à área-alvo indicada.

Regras inegociáveis:
- Use SOMENTE fatos do perfil e dos currículos originais. Nunca invente empresa, cargo, data,
  número, certificação, projeto ou tecnologia. Se um resultado não tem número, não crie um.
- Não inclua dados sensíveis: CPF, RG, data de nascimento, endereço com rua, estado civil, foto,
  diversidade, pretensão salarial.

Como escrever:
- headline: cargo-alvo + 2 ou 3 tecnologias principais (ex.: "Desenvolvedor Back-end Júnior | Python · SQL").
- summary: 3 a 4 linhas, concretas, sem clichês ("apaixonado por desafios", "proativo").
- skills: 3 a 5 grupos (ex.: Linguagens, Dados, Ferramentas), começando pelo que a área-alvo mais pede.
  Omita o que for irrelevante para a área.
- experiences: da mais recente para a mais antiga. 2 a 5 bullets por experiência, começando com verbo
  de ação no passado (no presente para o emprego atual) e mostrando o que foi feito, com qual
  tecnologia e qual resultado. Experiências fora de TI ficam curtas (1 a 2 bullets).
- projects: só projetos reais citados no perfil/currículos (pessoais, acadêmicos, de trabalho).
- education: inclua status quando houver ("cursando, previsão dez/2027").
- period: no formato "jan/2024 – atual" (pt) ou "Jan 2024 – Present" (en).
- extras: voluntariado, eventos, prêmios e atividades relevantes; lista vazia se não houver.
- tips: 2 a 5 dicas PARA O CANDIDATO (não vão no currículo): informações que faltam e melhorariam
  o currículo, como números de resultados, links de projetos ou certificações. Escreva sempre em português.
- Escreva todo o currículo no idioma pedido. Em inglês, traduza cargos e cursos de forma natural."""

TITLES = {
    "pt": {"summary": "Resumo", "skills": "Competências", "experiences": "Experiência",
           "projects": "Projetos", "education": "Formação", "certifications": "Certificações",
           "languages": "Idiomas", "extras": "Atividades complementares"},
    "en": {"summary": "Summary", "skills": "Skills", "experiences": "Experience",
           "projects": "Projects", "education": "Education", "certifications": "Certifications",
           "languages": "Languages", "extras": "Additional activities"},
}


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "Candidato"


def default_languages(profile: dict) -> list[str]:
    """Português sempre; inglês se o perfil tiver inglês acima do básico."""
    langs = ["pt"]
    for item in profile.get("languages", []):
        name, level = item.get("language", "").lower(), item.get("level", "").lower()
        if ("ingl" in name or "english" in name) and not re.search(r"b[aá]sic|basic|a1|a2", level):
            langs.append("en")
            break
    return langs


def default_tracks(cfg: dict, profile: dict) -> list[str]:
    tracks = [c["track"] for c in all_cvs(cfg) if c["track"] != "geral"]
    return list(dict.fromkeys(tracks)) or (profile.get("areas") or ["geral"])[:1]


def write_content(llm: LLM, profile: dict, originals: list, track: str, lang: str) -> dict:
    public = {k: v for k, v in profile.items()
              if k not in ("preferences", "form_answers", "quiz_answers", "diversity", "birth_date",
                           "address", "search_queries", "keywords")}
    language = "inglês" if lang == "en" else "português do Brasil"
    return llm.json(
        system=SYSTEM,
        content=originals + [{"type": "text", "text": (
            f"<perfil>\n{json.dumps(public, ensure_ascii=False, indent=1)}\n</perfil>\n\n"
            f"Área-alvo: {track}. Cargos-alvo do candidato: {', '.join(profile.get('target_titles', []))}.\n"
            f"Escreva o currículo em {language}."
        )}],
        schema=CV_SCHEMA,
        effort="high",
        max_tokens=16000,
    )


def _contacts(profile: dict) -> list[str]:
    place = " - ".join(x for x in (profile.get("city"), profile.get("state")) if x)
    items = [place, profile.get("phone"), profile.get("email")]
    for key in ("linkedin", "github", "portfolio"):
        url = (profile.get(key) or "").strip()
        if url:
            items.append(re.sub(r"^https?://(www\.)?", "", url).rstrip("/"))
    return [i for i in items if i]


CSS = """
@page { size: A4; margin: 14mm 16mm; }
body { font-family: Calibri, 'Segoe UI', Arial, sans-serif; font-size: 10.5pt; color: #1a1a1a; line-height: 1.35;
       /* Sem ligaduras ("fi" vira um caractere só e o ATS lê errado). */
       font-variant-ligatures: none; font-feature-settings: "liga" 0, "clig" 0; }
h1 { font-size: 20pt; margin: 0; letter-spacing: .3px; }
.headline { font-size: 11.5pt; color: #2b4c7e; margin: 2px 0 4px; font-weight: 600; }
.contacts { font-size: 9.5pt; color: #444; margin-bottom: 6px; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: .8px; color: #2b4c7e;
     border-bottom: 1px solid #c9d3e3; padding-bottom: 2px; margin: 12px 0 5px; }
.item { margin-bottom: 7px; page-break-inside: avoid; }
.row { display: flex; justify-content: space-between; gap: 12px; }
.title { font-weight: 700; }
.muted { color: #555; font-size: 9.5pt; white-space: nowrap; }
ul { margin: 2px 0 0 0; padding-left: 16px; }
li { margin: 1px 0; }
p { margin: 0; }
"""


def render_html(profile: dict, cv: dict, lang: str) -> str:
    t, e = TITLES[lang], html.escape
    parts = [f"<h1>{e(profile.get('name', ''))}</h1>",
             f"<div class='headline'>{e(cv['headline'])}</div>",
             f"<div class='contacts'>{' · '.join(e(c) for c in _contacts(profile))}</div>"]

    def section(key: str, body: str):
        if body:
            parts.append(f"<h2>{t[key]}</h2>{body}")

    section("summary", f"<p>{e(cv['summary'])}</p>" if cv["summary"] else "")
    section("skills", "".join(
        f"<p><b>{e(s['group'])}:</b> {e(', '.join(s['items']))}</p>" for s in cv["skills"] if s["items"]))
    section("experiences", "".join(
        f"<div class='item'><div class='row'><span class='title'>{e(x['role'])} · {e(x['company'])}</span>"
        f"<span class='muted'>{e(x['period'])}{(' · ' + e(x['location'])) if x['location'] else ''}</span></div>"
        f"<ul>{''.join(f'<li>{e(b)}</li>' for b in x['bullets'])}</ul></div>" for x in cv["experiences"]))
    section("projects", "".join(
        f"<div class='item'><span class='title'>{e(p['name'])}</span>"
        f"{(' · <span class=muted>' + e(', '.join(p['tech'])) + '</span>') if p['tech'] else ''}"
        f"<p>{e(p['description'])}{(' — ' + e(p['link'])) if p['link'] else ''}</p></div>" for p in cv["projects"]))
    section("education", "".join(
        f"<div class='item row'><span><span class='title'>{e(d['degree'])}</span> · {e(d['institution'])}</span>"
        f"<span class='muted'>{e(d['period'])}</span></div>" for d in cv["education"]))
    section("certifications", "<ul>" + "".join(f"<li>{e(c)}</li>" for c in cv["certifications"]) + "</ul>"
            if cv["certifications"] else "")
    section("languages", "<p>" + " · ".join(f"{e(l['language'])}: {e(l['level'])}" for l in cv["languages"]) + "</p>"
            if cv["languages"] else "")
    section("extras", "<ul>" + "".join(f"<li>{e(x)}</li>" for x in cv["extras"]) + "</ul>" if cv["extras"] else "")
    return (f"<!doctype html><html lang='{'pt-BR' if lang == 'pt' else 'en'}'><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def render_pdf(page_html: str, path: Path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(page_html, wait_until="load")
        page.pdf(path=str(path), format="A4", print_background=True, prefer_css_page_size=True)
        browser.close()


def render_docx(profile: dict, cv: dict, lang: str, path: Path):
    import docx
    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.shared import Pt, RGBColor, Mm

    t = TITLES[lang]
    blue = RGBColor(0x2B, 0x4C, 0x7E)
    doc = docx.Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Mm(297), Mm(210)
    sec.top_margin = sec.bottom_margin = Mm(14)
    sec.left_margin = sec.right_margin = Mm(16)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10.5)
    style.paragraph_format.space_after = Pt(2)
    right_tab = sec.page_width - sec.left_margin - sec.right_margin

    def para(text: str = "", bold=False, size=None, color=None, after=2):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(after)
        if text:
            r = p.add_run(text)
            r.bold, r.font.color.rgb = bold, color
            if size:
                r.font.size = Pt(size)
        return p

    def heading(key: str):
        p = para(t[key].upper(), bold=True, size=11, color=blue, after=3)
        p.paragraph_format.space_before = Pt(10)

    def row(left: str, right: str):
        p = para(after=1)
        p.paragraph_format.tab_stops.add_tab_stop(right_tab, WD_TAB_ALIGNMENT.RIGHT)
        p.add_run(left).bold = True
        if right:
            r = p.add_run("\t" + right)
            r.font.size, r.font.color.rgb = Pt(9.5), RGBColor(0x55, 0x55, 0x55)

    def bullets(items: list[str]):
        for b in items:
            p = doc.add_paragraph(b, style="List Bullet")
            p.paragraph_format.space_after = Pt(1)

    para(profile.get("name", ""), bold=True, size=20, after=0)
    para(cv["headline"], bold=True, size=11.5, color=blue, after=1)
    para(" · ".join(_contacts(profile)), size=9.5, after=4)
    if cv["summary"]:
        heading("summary")
        para(cv["summary"])
    if cv["skills"]:
        heading("skills")
        for s in cv["skills"]:
            if s["items"]:
                p = para(after=1)
                p.add_run(f"{s['group']}: ").bold = True
                p.add_run(", ".join(s["items"]))
    if cv["experiences"]:
        heading("experiences")
        for x in cv["experiences"]:
            row(f"{x['role']} · {x['company']}", " · ".join(v for v in (x["period"], x["location"]) if v))
            bullets(x["bullets"])
    if cv["projects"]:
        heading("projects")
        for pr in cv["projects"]:
            row(pr["name"], ", ".join(pr["tech"]))
            para(pr["description"] + (f" — {pr['link']}" if pr["link"] else ""))
    if cv["education"]:
        heading("education")
        for d in cv["education"]:
            row(f"{d['degree']} · {d['institution']}", d["period"])
    if cv["certifications"]:
        heading("certifications")
        bullets(cv["certifications"])
    if cv["languages"]:
        heading("languages")
        para(" · ".join(f"{l['language']}: {l['level']}" for l in cv["languages"]))
    if cv["extras"]:
        heading("extras")
        bullets(cv["extras"])
    doc.save(str(path))


def use_in_config(entries: list[dict]):
    """Troca o bloco `cvs:` do config.yaml pelos currículos gerados, mantendo o resto do arquivo."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    block = "cvs:\n" + "".join(
        f"  - {{path: {e['path']}, track: {e['track']}, language: {e['language']}}}\n" for e in entries)
    new, n = re.subn(r"(?m)^cvs:\n(?:[ \t]+.*\n|[ \t]*#.*\n)*", block, text)
    if n != 1:
        raise ValueError("Não encontrei o bloco 'cvs:' no config.yaml; ajuste à mão.")
    cfg_path.write_text(new, encoding="utf-8")
