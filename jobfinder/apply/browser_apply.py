"""Preenchimento de formulários de candidatura com Playwright + Claude.

- O Claude lê os campos visíveis da página e decide o que preencher a partir do seu perfil.
- No modo automático, só envia se o domínio for permitido, não houver captcha e nenhum campo
  obrigatório depender de você. Caso contrário, a vaga fica para o modo assistido.
- O navegador usa um perfil persistente (data/browser_profile): logins que você fizer
  (Gupy, LinkedIn...) ficam salvos para as próximas vezes.
"""

import json
from pathlib import Path

from playwright.sync_api import BrowserContext, Frame, Page, sync_playwright

from jobfinder.config import BROWSER_PROFILE_DIR
from jobfinder.llm import ARR_STR, LLM, STR, arr, enum, obj

APPLY_TEXTS = [
    "candidatar-se", "candidatar", "quero me candidatar", "aplicar", "aplicar para vaga",
    "apply for this job", "apply now", "apply", "inscrever-se", "inscreva-se",
]
SUBMIT_TEXTS = [
    "enviar candidatura", "submit application", "finalizar candidatura", "enviar", "submit",
    "concluir", "candidatar",
]
SUCCESS_TEXTS = [
    "obrigado", "obrigada", "recebemos sua candidatura", "candidatura enviada", "candidatura realizada",
    "thank you", "application received", "application has been submitted", "we have received",
]
CAPTCHA_HINTS = ["recaptcha", "hcaptcha", "challenges.cloudflare.com", "turnstile", "arkoselabs", "funcaptcha"]

# Marca cada campo visível com data-jf-id e devolve uma descrição legível para o Claude.
SCAN_JS = """
(prefix) => {
  const visible = el => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return (r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none') || el.type === 'file';
  };
  const labelOf = el => {
    let t = '';
    if (el.labels && el.labels.length) t = [...el.labels].map(l => l.innerText).join(' ');
    if (!t && el.getAttribute('aria-label')) t = el.getAttribute('aria-label');
    if (!t && el.getAttribute('aria-labelledby')) {
      t = el.getAttribute('aria-labelledby').split(' ').map(id => document.getElementById(id)?.innerText || '').join(' ');
    }
    if (!t) {
      // Sobe até o menor bloco com texto curto (o rótulo visual do campo), sem pegar o formulário inteiro.
      let p = el.parentElement;
      for (let k = 0; k < 6 && p; k++, p = p.parentElement) {
        const txt = (p.innerText || '').trim();
        if (txt.length > 250) break;
        if (txt) { t = txt; break; }
      }
    }
    return (t || '').replace(/\\s+/g, ' ').trim().slice(0, 200);
  };
  // Contador global: campos que surgem depois (ex.: Cidade) não reaproveitam ids já usados.
  const out = []; window.__jfNext = window.__jfNext || 0;
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type) || el.disabled) continue;
    // Radios/checkboxes costumam ser escondidos e estilizados via label; os demais precisam estar visíveis.
    if (!visible(el) && !['radio', 'checkbox'].includes(type)) continue;
    const id = el.dataset.jfId || (prefix + (window.__jfNext++));
    el.dataset.jfId = id;
    const d = { id, tag: el.tagName.toLowerCase(), type, name: el.name || '', label: labelOf(el),
                placeholder: el.placeholder || '', required: el.required || el.getAttribute('aria-required') === 'true',
                value: (type === 'checkbox' || type === 'radio') ? String(el.checked) : (el.value || '').slice(0, 100) };
    if (type === 'file') d.accept = el.accept || '';
    // Listas com busca (país, cidade...): precisam de "choose" (digitar e clicar na opção).
    const role = el.getAttribute('role') || '';
    if (role === 'combobox' || el.getAttribute('aria-autocomplete') || el.getAttribute('aria-haspopup') ||
        (el.tagName === 'INPUT' && type === 'input' && /selecione|select|escolha|busque|search/i.test(d.label + ' ' + d.placeholder)))
      d.widget = 'lista-com-busca';
    if (el.tagName === 'SELECT') d.options = [...el.options].map(o => o.text.trim()).filter(Boolean).slice(0, 60);
    if (type === 'radio' || type === 'checkbox') {
      d.option_label = (el.labels && el.labels[0]?.innerText || el.value || '').trim().slice(0, 100);
      // Pergunta do grupo: menor bloco acima que tenha mais texto que a própria opção.
      let g = el.closest('fieldset, [role=radiogroup], [role=group], .question, .field, .form-group');
      if (!g || !(g.innerText || '').trim()) {
        g = el.parentElement;
        for (let k = 0; k < 6 && g; k++, g = g.parentElement) {
          const txt = (g.innerText || '').trim();
          if (txt.length > 300) { g = null; break; }
          if (txt.length > d.option_label.length + 3) break;
        }
      }
      d.question = (g ? g.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 200);
    }
    out.push(d);
  }
  return out;
}
"""

ACTION = obj({
    "id": STR,
    "action": enum("fill", "select", "choose", "check", "uncheck", "upload_cv", "skip"),
    "value": STR,
})
PLAN_SCHEMA = obj({"actions": arr(ACTION), "needs_human": ARR_STR})

PLAN_SYSTEM = """Você preenche formulários de candidatura em nome do candidato, usando SOMENTE os
dados do perfil e os materiais fornecidos. Para cada campo recebido, devolva uma ação:
- fill: texto (inputs/textarea). Para carta/apresentação/"por que você", use cover_letter ou short_pitch.
- select: valor deve ser exatamente um dos textos em "options".
- choose: listas com busca/autocomplete (widget "lista-com-busca", ou campos como país, cidade, estado,
  instituição de ensino que não sejam <select>): value = texto da opção a escolher (ex.: "Brasil", "Campinas").
- Telefone: se existir um campo separado de código do país/DDI, preencha o telefone sem "+55".
- check/uncheck: checkboxes e radios (escolha o radio correto do grupo pelo option_label).
- upload_cv: campos de arquivo para currículo/CV/resume. Outros uploads (portfólio, foto): skip.
- skip: campo já preenchido corretamente, irrelevante, ou que você não sabe responder.

Nunca invente informação. Se um campo OBRIGATÓRIO exigir algo que não está no perfil
(ex.: CPF, data de nascimento, dados sensíveis, aceite de termos, perguntas pessoais), use skip e
descreva-o em needs_human. Termos de privacidade/consentimento: sempre skip + needs_human.
Pretensão salarial: use preferences.salary_min_brl quando existir.
Se a página for de login, criação de conta ou busca (não um formulário de candidatura), use skip em
tudo e explique em needs_human.
Perguntas de teste/avaliação técnica (quiz de conhecimento, questões de múltipla escolha sobre
tecnologia, lógica, raciocínio) avaliam o próprio candidato: use skip e liste em needs_human
"teste técnico: responda você"."""


def _frames(page: Page) -> list[Frame]:
    return [f for f in page.frames if not f.is_detached()]


def scan_fields(page: Page) -> dict[str, tuple[Frame, dict]]:
    fields = {}
    for n, frame in enumerate(_frames(page)):
        try:
            for d in frame.evaluate(SCAN_JS, f"f{n}_"):
                fields[d["id"]] = (frame, d)
        except Exception:  # noqa: BLE001 - frames de terceiros podem negar acesso
            continue
    return fields


def has_captcha(page: Page) -> bool:
    if any(h in (f.url or "").lower() for f in page.frames for h in CAPTCHA_HINTS):
        return True
    return page.locator(".g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]").count() > 0


def _click_text(page: Page, texts: list[str], roles=("button", "link")) -> bool:
    for frame in _frames(page):
        for text in texts:
            for role in roles:
                loc = frame.get_by_role(role, name=text, exact=False)
                try:
                    if loc.count() and loc.first.is_visible():
                        loc.first.click(timeout=5000)
                        page.wait_for_timeout(2500)
                        return True
                except Exception:  # noqa: BLE001
                    continue
    return False


CLOSED_TEXTS = [
    "link que você acessou não é válido", "vaga encerrada", "vaga não está mais disponível",
    "esta vaga foi encerrada", "processo seletivo encerrado", "inscrições encerradas", "vaga expirada",
    "page not found", "job not found", "no longer accepting applications", "this job is no longer available",
    "position has been filled", "job has expired",
]


def job_closed(page: Page) -> bool:
    """A página indica que a vaga saiu do ar (404, encerrada, expirada)?"""
    try:
        text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:  # noqa: BLE001
        return False
    return any(t in text for t in CLOSED_TEXTS) or (len(text) < 400 and "404" in text)


def open_application(page: Page, url: str):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    # Página de descrição da vaga sem formulário: tenta o botão "Candidatar".
    if not any(d["type"] not in ("checkbox", "radio") for _, d in scan_fields(page).values()):
        _click_text(page, APPLY_TEXTS)


def plan(llm: LLM, profile: dict, materials: dict, job_title: str, fields: dict) -> dict:
    descriptors = [d for _, d in fields.values()]
    return llm.json(
        system=PLAN_SYSTEM,
        content=(
            f"<perfil>\n{json.dumps(profile, ensure_ascii=False)}\n</perfil>\n\n"
            f"<materiais>\n{json.dumps(materials, ensure_ascii=False)}\n</materiais>\n\n"
            f"Vaga: {job_title}\n\n<campos>\n{json.dumps(descriptors, ensure_ascii=False, indent=1)}\n</campos>"
        ),
        schema=PLAN_SCHEMA,
        effort="medium",
        max_tokens=16000,
    )


CV_HINTS = ("resume", "curriculo", "currículo", "cv", "curriculum")


def _is_cv_input(d: dict) -> bool:
    text = f"{d['name']} {d['label']} {d.get('accept', '')}".lower()
    return d["type"] == "file" and any(h in text for h in CV_HINTS)


def _choose(frame: Frame, loc, value: str):
    """Lista com busca: clica, digita e escolhe a opção que bate com o texto."""
    loc.click(timeout=5000)
    loc.fill("", timeout=5000)
    loc.type(value, delay=40, timeout=10000)
    frame.page.wait_for_timeout(1500)
    for option in (
        frame.get_by_role("option", name=value, exact=True),
        frame.get_by_role("option", name=value),
        frame.locator("[role=listbox] >> text=" + value),
        frame.locator(f"li:has-text('{value}'), [class*=option]:has-text('{value}')"),
    ):
        try:
            if option.count() and option.first.is_visible():
                option.first.click(timeout=5000)
                return
        except Exception:  # noqa: BLE001
            continue
    loc.press("Enter")


def execute(fields: dict, actions: list[dict], cv: Path) -> list[str]:
    """Aplica as ações; devolve a lista de erros (campos que não deu para preencher)."""
    errors = []
    acted = {a["id"] for a in actions if a["action"] != "skip"}
    # Campo de currículo nunca fica para trás, mesmo que o plano tenha pulado.
    actions = actions + [
        {"id": fid, "action": "upload_cv", "value": ""}
        for fid, (_, d) in fields.items() if fid not in acted and _is_cv_input(d)
    ]
    for a in actions:
        if a["action"] == "skip" or a["id"] not in fields:
            continue
        frame, d = fields[a["id"]]
        loc = frame.locator(f'[data-jf-id="{a["id"]}"]').first
        try:
            if a["action"] == "fill":
                loc.fill(a["value"], timeout=5000)
            elif a["action"] == "choose":
                _choose(frame, loc, a["value"])
            elif a["action"] == "select":
                loc.select_option(label=a["value"], timeout=5000)
            elif a["action"] == "check":
                loc.check(timeout=5000, force=True)
            elif a["action"] == "uncheck":
                loc.uncheck(timeout=5000, force=True)
            elif a["action"] == "upload_cv":
                loc.set_input_files(str(cv), timeout=10000)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{d['label'] or d['name']}: {str(e).splitlines()[0]}")
    return errors


def fill_visible(page: Page, llm: LLM, profile: dict, materials: dict, job_title: str, cv: Path) -> dict:
    fields = scan_fields(page)
    if not fields:
        return {"filled": 0, "needs_human": ["nenhum campo de formulário encontrado na página"], "errors": []}
    # Login/criação de conta é sempre com você: o robô não digita credenciais.
    if any(d["type"] == "password" for _, d in fields.values()):
        return {"filled": 0, "needs_human": ["página de login/cadastro: entre na sua conta e avance até o formulário"], "errors": []}
    filled, needs_human, errors, seen = 0, [], [], set()
    # Alguns campos só aparecem depois de outros (ex.: Cidade após escolher o País): até 3 passadas.
    for _ in range(3):
        new = {k: v for k, v in fields.items() if k not in seen}
        if not new:
            break
        seen |= set(new)
        result = plan(llm, profile, materials, job_title, new)
        errors += execute(new, result["actions"], cv)
        filled += sum(1 for a in result["actions"] if a["action"] != "skip")
        needs_human += result["needs_human"]
        page.wait_for_timeout(1000)
        fields = scan_fields(page)
    return {"filled": filled, "needs_human": needs_human, "errors": errors}


def submit_and_verify(page: Page) -> bool:
    before = page.url
    clicked = False
    for frame in _frames(page):
        btn = frame.locator("button[type=submit], input[type=submit]")
        try:
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=5000)
                clicked = True
                break
        except Exception:  # noqa: BLE001
            continue
    if not clicked:
        clicked = _click_text(page, SUBMIT_TEXTS, roles=("button",))
    if not clicked:
        return False
    page.wait_for_timeout(6000)
    text = " ".join(f.locator("body").inner_text(timeout=5000).lower() for f in _frames(page)[:3])
    return page.url != before or any(s in text for s in SUCCESS_TEXTS)


class Browser:
    """Contexto do Playwright com perfil persistente."""

    def __init__(self, headless: bool):
        self._pw = sync_playwright().start()
        self.ctx: BrowserContext = self._pw.chromium.launch_persistent_context(
            str(BROWSER_PROFILE_DIR), headless=headless, locale="pt-BR", viewport={"width": 1280, "height": 900},
        )

    def new_page(self) -> Page:
        return self.ctx.new_page()

    def close(self):
        self.ctx.close()
        self._pw.stop()
