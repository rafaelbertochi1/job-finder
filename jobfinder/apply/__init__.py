"""Orquestra as candidaturas: decide o canal, gera materiais e envia (auto) ou assiste você."""

import json
import subprocess
import sys
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from jobfinder import db
from jobfinder.apply import email_apply, materials as mat
from jobfinder.config import OUTPUT_DIR, pick_cv
from jobfinder.llm import BOOL, LLM, LLMError, STR, obj

console = Console()


def _host_matches(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def decide_channel(row, analysis: dict, cfg: dict) -> tuple[str, str]:
    """Retorna (canal, destino): email -> endereço; form/platform -> URL."""
    acfg = cfg["apply"]
    if analysis.get("apply_method") == "email" and "@" in analysis.get("apply_email", ""):
        return "email", analysis["apply_email"]
    url = analysis.get("apply_url") or row["url"]
    if not url.startswith("http"):
        url = row["url"]
    if analysis.get("apply_method") == "platform" or _host_matches(url, acfg["assisted_only_domains"]):
        return "platform", url
    return "form", url


# Agregadores: a vaga costuma existir também no site/ATS da própria empresa, onde dá para preencher.
AGGREGATORS = ["linkedin.com", "indeed.com", "glassdoor.com", "glassdoor.com.br"]

DIRECT_SYSTEM = """Você encontra o link OFICIAL de candidatura de uma vaga que só temos pelo LinkedIn/Indeed/Glassdoor.
Pesquise a mesma vaga (mesma empresa e mesmo cargo) no site de carreiras da empresa ou no ATS dela
(Gupy, InHire, Greenhouse, Lever, Workable, applicants.bairesdev.com, etc.).
Regras: retorne somente uma URL que apareceu nos resultados da busca; nunca invente. Ela precisa ser da
mesma vaga, ou da página de candidatura geral da empresa para esse cargo. Não aceite LinkedIn, Indeed,
Glassdoor nem outros agregadores. Se não tiver certeza, use found=false."""


def company_sites(conn, company: str) -> list[str]:
    """Links de candidatura que a mesma empresa usou em outras vagas já coletadas (pistas para a busca)."""
    if not company:
        return []
    rows = conn.execute(
        "SELECT url FROM jobs WHERE lower(company) = lower(?) LIMIT 50", (company,)
    ).fetchall()
    return list(dict.fromkeys(r["url"].split("?")[0] for r in rows if not _host_matches(r["url"], AGGREGATORS)))[:5]


def find_direct_link(conn, llm: LLM, row) -> str:
    hints = company_sites(conn, row["company"])
    hint_text = (
        "\n\nA empresa recebe candidaturas nestes sites (vistos em outras vagas dela); procure a vaga lá "
        "(ex.: buscando site:<domínio> + cargo):\n" + "\n".join(f"- {h}" for h in hints)
        if hints else ""
    )
    try:
        r = llm.json(
            system=DIRECT_SYSTEM,
            content=f"Vaga: {row['title']}\nEmpresa: {row['company']}\nLink atual: {row['url']}\n\n"
                    f"Resumo:\n{(row['description'] or '')[:1500]}{hint_text}",
            schema=obj({"found": BOOL, "url": STR, "note": STR}),
            effort="low",
            max_tokens=8000,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}],
        )
    except LLMError:
        return ""
    url = r["url"].strip()
    if r["found"] and url.startswith("http") and not _host_matches(url, AGGREGATORS):
        return url
    return ""


def resolve_direct_link(conn, llm: LLM, row, analysis: dict):
    """Troca o link do agregador pelo oficial (uma vez por vaga; o resultado fica salvo na análise)."""
    console.print("[dim]Vaga só pelo agregador; procurando o link oficial de candidatura...[/]")
    analysis["direct_url"] = find_direct_link(conn, llm, row)
    if analysis["direct_url"]:
        analysis["apply_url"] = analysis["direct_url"]
        analysis["apply_method"] = "form"
        console.print(f"[green]Link oficial:[/] {analysis['direct_url']}")
    else:
        console.print("[dim]Não achei; segue pelo agregador (modo assistido).[/]")
    conn.execute("UPDATE jobs SET analysis = ? WHERE id = ?", (json.dumps(analysis, ensure_ascii=False), row["id"]))
    conn.commit()


ANSWER_SYSTEM = """Você responde perguntas de formulários de candidatura em nome do candidato, em primeira pessoa.
- Use SOMENTE fatos do perfil. Nunca invente experiência, número, empresa ou tecnologia.
- Conecte as experiências reais do candidato ao que a vaga pede; seja honesto sobre o que falta.
- Tom direto e humano, sem clichês. Respeite o limite de caracteres se a pergunta informar
  (fique uns 10% abaixo); sem limite informado, até ~900 caracteres.
- Responda no idioma da pergunta. Texto corrido, sem markdown.
- Se for pergunta de teste técnico/conhecimento (quiz), não responda: devolva
  "Pergunta de teste técnico — responda você mesmo." """


def already_applied(conn, url_key: str, job_id: str) -> bool:
    """Já houve candidatura para este mesmo link (por outra entrada da vaga)?"""
    for r in conn.execute("SELECT url, analysis FROM jobs WHERE status = 'applied' AND id != ?", (job_id,)):
        a = json.loads(r["analysis"] or "{}")
        links = {r["url"], a.get("apply_url", ""), a.get("direct_url", "")}
        if url_key in {l.split("?")[0].rstrip("/") for l in links if l}:
            return True
    return False


def auto_allowed(channel: str, target: str, cfg: dict) -> bool:
    acfg = cfg["apply"]
    if channel not in acfg["auto_channels"]:
        return False
    if channel == "email":
        return email_apply.smtp_configured()
    if channel == "form":
        return _host_matches(target, acfg["auto_submit_domains"])
    return False  # plataformas (Gupy, LinkedIn...) nunca são automáticas


def candidates(conn, cfg: dict, job_id: str | None = None) -> list:
    if job_id:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchall()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'scored' AND score >= ? ORDER BY score DESC",
        (cfg["matching"]["min_score_apply"],),
    ).fetchall()
    return [r for r in rows if db.analysis(r).get("verdict") != "descartar"]


def get_materials(llm: LLM, profile: dict, row, analysis: dict) -> dict:
    cache = OUTPUT_DIR / "applications" / row["id"] / "materiais.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    job = db.row_to_job(row)
    m = mat.generate(llm, profile, job, analysis)
    folder = mat.save(row["id"], job, m)
    (folder / "materiais.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


LATER = {
    "n": "Deixar para depois (a vaga continua na fila)",
    "p": "Pular esta vaga (não quero me candidatar)",
    "q": "Parar por agora (sai do modo de candidatura)",
}


def ask(options: dict[str, str], default: str = "n") -> str:
    """Mostra a legenda de cada letra e pergunta a escolha."""
    console.print()
    for key, text in options.items():
        mark = "  [dim](Enter)[/]" if key == default else ""
        console.print(f"  [bold cyan]{key}[/]  {text}{mark}")
    return Prompt.ask("Digite a letra e aperte Enter", choices=list(options), default=default, show_choices=False)


def _copy(text: str):
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
    except Exception:  # noqa: BLE001 - área de transferência é só conveniência
        pass


class _Closed(Exception):
    pass


class Applier:
    def __init__(self, conn, cfg: dict, profile: dict, llm: LLM, dry_run: bool):
        self.conn, self.cfg, self.profile, self.llm, self.dry_run = conn, cfg, profile, llm, dry_run
        self.cv = None  # currículo da vaga atual (área + idioma), definido em run()
        self._browser = None
        self._headless = None

    # -- navegador sob demanda ------------------------------------------------
    def browser(self, headless: bool):
        from jobfinder.apply.browser_apply import Browser

        if self._browser and self._headless != headless:
            self._browser.close()
            self._browser = None
        if not self._browser:
            self._browser = Browser(headless=headless)
            self._headless = headless
        return self._browser

    def close(self):
        if self._browser:
            self._browser.close()

    def _done(self, row, channel: str, log: str):
        db.set_status(self.conn, row["id"], "applied", applied_at=db.now(), apply_channel=channel, apply_log=log)
        console.print(f"[green]✔ Candidatura registrada:[/] {row['title']}")

    def _closed(self, row):
        console.print(f"[yellow]Vaga encerrada ou fora do ar — pulando:[/] {row['title']}")
        if not self.dry_run:
            db.set_status(self.conn, row["id"], "skipped", apply_log="vaga encerrada (link inválido/404)")

    def _note(self, row, log: str):
        self.conn.execute("UPDATE jobs SET apply_log = ? WHERE id = ?", (log, row["id"]))
        self.conn.commit()

    # -- simulação (--dry-run) ------------------------------------------------
    def simulate(self, row, channel: str, target: str, materials: dict):
        """Gera materiais, abre a vaga num navegador invisível, preenche e tira print. Nunca envia."""
        folder = OUTPUT_DIR / "applications" / row["id"]
        folder.mkdir(parents=True, exist_ok=True)
        auto = auto_allowed(channel, target, self.cfg)
        lines = [
            f"# Simulação — {row['title']} ({row['company'] or row['source']})", "",
            f"- Nota: {row['score']}",
            f"- Canal: {channel}",
            f"- Destino: {target}",
            f"- Currículo que seria anexado: {self.cv.name}",
            f"- No modo automático: {'enviaria sozinho' if auto else 'ficaria para o modo assistido (você confirma)'}",
        ]
        if channel == "email":
            lines.append(f"- E-mail para {target} com o assunto \"{materials['email_subject']}\" (texto em materiais.md)")
        else:
            from jobfinder.apply.browser_apply import fill_visible, has_captcha, job_closed, open_application

            page = self.browser(headless=True).new_page()
            try:
                open_application(page, target)
                lines.append(f"- Página aberta: {page.url}")
                if job_closed(page):
                    lines.append("- Vaga encerrada / fora do ar: seria pulada")
                    raise _Closed
                lines.append(f"- Captcha: {'sim' if has_captcha(page) else 'não'}")
                result = fill_visible(page, self.llm, self.profile, materials, row["title"], self.cv)
                lines.append(f"- Campos que a IA preencheu: {result['filled']}")
                pend = result["needs_human"] + result["errors"]
                if pend:
                    lines += ["- Ficaria para você:"] + [f"  - {p}" for p in pend]
                page.screenshot(path=str(folder / "simulacao.png"), full_page=True)
                lines.append("- Print do formulário preenchido: simulacao.png")
            except _Closed:
                pass
            except Exception as e:  # noqa: BLE001
                lines.append(f"- Não consegui abrir/preencher a página: {str(e).splitlines()[0]}")
            finally:
                page.close()
        (folder / "simulacao.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for line in lines[2:]:
            console.print(line)
        console.print(f"[dim]Detalhes: {folder}[/]")

    # -- modo automático ------------------------------------------------------
    def auto(self, row, channel: str, target: str, materials: dict) -> bool:
        """Tenta enviar sem intervenção. Retorna True se enviou."""
        if channel == "email":
            msg = email_apply.build(self.profile, target, materials, self.cv)
            if self.dry_run:
                console.print(f"[yellow](simulação)[/] e-mail para {target}: {materials['email_subject']}")
                return False
            email_apply.send(msg)
            self._done(row, "email", f"e-mail enviado para {target}")
            return True

        from jobfinder.apply.browser_apply import fill_visible, has_captcha, job_closed, open_application, submit_and_verify

        page = self.browser(self.cfg["apply"]["headless_auto"]).new_page()
        try:
            open_application(page, target)
            if job_closed(page):
                self._closed(row)
                return True
            if has_captcha(page):
                self._note(row, "auto: captcha detectado; requer modo assistido")
                return False
            result = fill_visible(page, self.llm, self.profile, materials, row["title"], self.cv)
            if result["needs_human"] or result["errors"] or not result["filled"]:
                self._note(row, "auto: precisa de você -> " + "; ".join(result["needs_human"] + result["errors"]))
                return False
            if self.dry_run:
                console.print(f"[yellow](simulação)[/] formulário preenchido ({result['filled']} campos), não enviado")
                return False
            if submit_and_verify(page):
                self._done(row, "form", f"formulário enviado automaticamente: {target}")
                return True
            self._note(row, "auto: envio não confirmado; revise no modo assistido")
            return False
        finally:
            page.close()

    # -- modo assistido -------------------------------------------------------
    def assisted(self, row, channel: str, target: str, materials: dict) -> bool:
        """Prepara tudo e deixa você decidir. Retorna False se pediu para parar."""
        if channel == "email":
            if not email_apply.smtp_configured():
                console.print("[yellow]SMTP não configurado no .env.[/] Envie manualmente:")
                console.print(Panel(f"Para: {target}\nAssunto: {materials['email_subject']}\n\n{materials['email_body']}"))
                _copy(materials["email_body"])
                ans = ask({"s": "Já enviei o e-mail — registrar a candidatura", **LATER})
            else:
                console.print(Panel(
                    f"Para: {target}\nAssunto: {materials['email_subject']}\nAnexo: {self.cv.name}\n\n{materials['email_body']}",
                    title="E-mail de candidatura",
                ))
                ans = ask({"s": "Enviar este e-mail agora", **LATER})
                if ans == "s" and not self.dry_run:
                    email_apply.send(email_apply.build(self.profile, target, materials, self.cv))
            return self._resolve(row, "email", ans, target)

        if channel == "platform":
            return self._assisted_own_browser(row, target, materials)

        from jobfinder.apply.browser_apply import fill_visible, job_closed, open_application

        page = self.browser(headless=False).new_page()
        try:
            open_application(page, target)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]Não consegui abrir a página:[/] {e}")
        if job_closed(page):
            page.close()
            self._closed(row)
            return True
        _copy(materials["cover_letter"])
        login = (
            "1. No navegador, entre na sua conta do site (só na primeira vez; o login fica salvo)\n"
            "   e clique em [bold]Candidatar-se[/].\n2." if channel == "platform" else "1."
        )
        console.print(Panel(
            f"[bold]Vaga:[/] {row['title']} — {row['company'] or row['source']}   (nota {row['score']})\n"
            f"[bold]Currículo:[/] {self.cv.name}\n"
            f"[bold]Carta de apresentação:[/] já copiada — se o site pedir, é só colar (Ctrl+V).\n\n"
            f"[bold cyan]O que fazer agora[/]\n"
            f"{login} Aqui, aperte [bold]Enter[/] (letra f) para a IA preencher o formulário.\n"
            f"   Confira no navegador e complete o que ela avisar (CPF, aceite de privacidade, captcha).\n"
            f"   Formulário com várias etapas? Clique em Avançar no site e aperte Enter aqui de novo.\n"
            f"→ Depois de clicar em [bold]Enviar[/] no site, digite [bold]s[/] aqui para registrar.",
            title="Nova candidatura", border_style="cyan",
        ))
        while True:
            ans = ask({
                "f": "Preencher com a IA os campos que estão na tela (use de novo a cada etapa do formulário)",
                "s": "Já cliquei em enviar no site — registrar a candidatura",
                **LATER,
            }, default="f")
            if ans != "f":
                break
            try:
                console.print("[dim]Preenchendo... (uns 30 segundos)[/]")
                r = fill_visible(page, self.llm, self.profile, materials, row["title"], self.cv)
                console.print(f"\n[green]✔ Preenchi {r['filled']} campos.[/]")
                pend = r["needs_human"] + r["errors"]
                if pend:
                    console.print("[bold yellow]Falta você fazer no navegador:[/]")
                    for item in pend:
                        console.print(f"  [yellow]• {item}[/]")
                console.print("[dim]Confira tudo no navegador antes de enviar.[/]")
            except (LLMError, Exception) as e:  # noqa: BLE001
                console.print(f"[red]Falha ao preencher:[/] {e}")
        page.close()
        return self._resolve(row, channel, ans, target)

    def _assisted_own_browser(self, row, target: str, materials: dict) -> bool:
        """Gupy/LinkedIn bloqueiam login em navegador automatizado: abre no seu navegador normal
        (onde você já está logado) e entrega os materiais prontos para colar."""
        import webbrowser

        _copy(materials["cover_letter"])
        webbrowser.open(target)
        answers = "\n".join(f"  • {a['question']}: {a['answer']}" for a in self.profile.get("form_answers", [])[:12])
        console.print(Panel(
            f"[bold]Vaga:[/] {row['title']} — {row['company'] or row['source']}   (nota {row['score']})\n"
            f"[bold]Currículo para anexar:[/] {self.cv}\n\n"
            f"[bold cyan]O que fazer agora[/]\n"
            f"1. A vaga abriu no [bold]seu navegador normal[/] (onde você já está logado). Clique em Candidatar-se.\n"
            f"2. Campo de texto difícil (\"apresente-se\", \"por que você?\")? Digite [bold]r[/] aqui, cole a pergunta\n"
            f"   e a IA escreve a resposta e deixa copiada — é só colar no site (Ctrl+V).\n"
            f"3. Quando enviar, volte aqui e digite [bold]s[/].\n\n"
            f"[bold]Respostas prontas:[/]\n{answers}",
            title="Candidatura no seu navegador", border_style="cyan",
        ))
        job = db.row_to_job(row)
        while True:
            ans = ask({
                "r": "Responder uma pergunta do formulário com a IA (cole a pergunta)",
                "c": "Copiar a carta de apresentação de novo",
                "s": "Já enviei no site — registrar a candidatura",
                **LATER,
            }, default="r")
            if ans == "c":
                _copy(materials["cover_letter"])
                console.print("[green]✔ Carta copiada. Cole no site com Ctrl+V.[/]")
            elif ans == "r":
                self._answer_question(job, materials)
            else:
                break
        return self._resolve(row, "platform", ans, target)

    def _answer_question(self, job, materials: dict):
        question = Prompt.ask("\nCole aqui a pergunta do formulário (e o limite de caracteres, se tiver)").strip()
        if not question:
            return
        console.print("[dim]Escrevendo a resposta...[/]")
        try:
            r = self.llm.json(
                system=ANSWER_SYSTEM,
                content=(
                    f"<perfil>\n{json.dumps(self.profile, ensure_ascii=False)}\n</perfil>\n\n"
                    f"<vaga>\n{job.as_prompt()}\n</vaga>\n\n"
                    f"<carta_ja_escrita>\n{materials['cover_letter']}\n</carta_ja_escrita>\n\n"
                    f"Pergunta do formulário: {question}"
                ),
                schema=obj({"answer": STR}),
                effort="medium",
                max_tokens=4000,
            )
        except LLMError as e:
            console.print(f"[red]Não consegui gerar a resposta:[/] {e}")
            return
        _copy(r["answer"])
        console.print(Panel(r["answer"], title=f"Resposta ({len(r['answer'])} caracteres) — já copiada, cole com Ctrl+V",
                            border_style="green"))

    def _resolve(self, row, channel: str, ans: str, target: str) -> bool:
        if ans == "s":
            if self.dry_run:
                console.print("[yellow](simulação) nada foi registrado[/]")
            else:
                self._done(row, channel, f"assistido: {target}")
        elif ans == "p":
            db.set_status(self.conn, row["id"], "skipped")
        return ans != "q"


def run(conn, cfg: dict, profile: dict, llm: LLM, *, mode: str, limit: int,
        job_id: str | None = None, dry_run: bool = False):
    if mode == "off":
        console.print("Candidaturas desligadas (apply.mode: off).")
        return
    rows = candidates(conn, cfg, job_id)[:limit]
    if not rows:
        console.print("Nenhuma vaga na fila de candidatura. Rode 'search' ou reduza matching.min_score_apply.")
        return
    interactive = sys.stdin.isatty()
    applier = Applier(conn, cfg, profile, llm, dry_run)
    pending, seen_targets = [], set()
    try:
        for row in rows:
            analysis = db.analysis(row)
            channel, target = decide_channel(row, analysis, cfg)
            if channel == "platform" and _host_matches(target, AGGREGATORS) and "direct_url" not in analysis:
                resolve_direct_link(conn, llm, row, analysis)
                channel, target = decide_channel(row, analysis, cfg)
            # A mesma vaga pode vir de fontes diferentes (ex.: LinkedIn e o formulário oficial).
            key = target.split("?")[0].rstrip("/")
            if key in seen_targets or already_applied(conn, key, row["id"]):
                console.print(f"[dim]Pulando: mesma vaga de outra já {'na fila' if key in seen_targets else 'candidatada'} ({row['title']}).[/]")
                if not dry_run:
                    db.set_status(conn, row["id"], "skipped", apply_log=f"duplicada: {key}")
                continue
            seen_targets.add(key)
            applier.cv = pick_cv(cfg, analysis.get("track"), analysis.get("language"))
            console.rule(f"[bold]{row['score']}[/] · {row['title']} · {row['company'] or row['source']} · {channel} · CV {applier.cv.name}")
            try:
                materials = get_materials(llm, profile, row, analysis)
                if dry_run:
                    applier.simulate(row, channel, target, materials)
                    continue
                if mode == "auto" and auto_allowed(channel, target, cfg):
                    if applier.auto(row, channel, target, materials):
                        continue
                if mode == "auto":
                    pending.append((row, channel, target, materials, applier.cv))
                    continue
                if not applier.assisted(row, channel, target, materials):
                    break
            except Exception as e:  # noqa: BLE001 - uma vaga com problema não para as outras
                console.print(f"[red]Erro:[/] {e}")
                db.set_status(conn, row["id"], "scored", apply_log=f"erro: {e}")

        if pending:
            console.print(f"\n[bold]{len(pending)} vaga(s)[/] precisam de você (login, captcha ou campos pessoais).")
            if interactive and Confirm.ask("Abrir o modo assistido para elas agora?", default=True):
                for row, channel, target, materials, applier.cv in pending:
                    console.rule(f"[bold]{row['score']}[/] · {row['title']} · {channel}")
                    if not applier.assisted(row, channel, target, materials):
                        break
            else:
                console.print("Elas ficam na fila; rode:  python -m jobfinder apply --mode assisted")
    finally:
        applier.close()
