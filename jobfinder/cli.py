import argparse
import json
import sys
import webbrowser
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jobfinder import apply, db, report
from jobfinder.config import all_cvs, load_config, tracks
from jobfinder.llm import BOOL, LLM, LLMError, obj
from jobfinder.matcher import Matcher, prefilter
from jobfinder.profile import build_profile, load_profile, print_profile
from jobfinder.sources import collect

console = Console()


def log(msg: str):
    console.print(f"[dim]{msg}[/]")


def cmd_setup(args, cfg):
    cvs = [{"path": Path(args.cv), "track": "geral", "language": "pt"}] if args.cv else all_cvs(cfg)
    profile = build_profile(LLM(cfg["model"]), cvs)
    print_profile(profile)
    console.print("\n[green]Perfil pronto.[/] Próximo passo:  python -m jobfinder search")


def cmd_profile(args, cfg):
    print_profile(load_profile())


def cmd_search(args, cfg):
    profile = load_profile()
    llm = LLM(cfg["model"])
    if args.no_web:
        cfg["search"]["sources"]["web"]["enabled"] = False

    console.print("[bold]Buscando vagas...[/]")
    conn = db.connect()
    # Vagas cuja análise falhou antes (erro temporário) entram de novo na fila.
    conn.execute("UPDATE jobs SET status = 'new' WHERE status = 'error'")
    conn.commit()
    # Uma chamada antes do paralelismo: renova o login do Claude Code uma vez só.
    llm.json(system="Responda em JSON.", content="ok?", schema=obj({"ok": BOOL}), effort="low", max_tokens=500)
    if getattr(args, "skip_fetch", False):
        console.print("Pulando a coleta: só as vagas pendentes no banco serão analisadas.")
    else:
        found = collect(profile, cfg, llm, log)
        new = db.insert_jobs(conn, found)
        console.print(f"{len(found)} vagas encontradas, [bold]{new} novas[/].")

    to_score = []
    for row in conn.execute("SELECT * FROM jobs WHERE status = 'new'").fetchall():
        reason = prefilter(db.row_to_job(row), profile)
        if reason:
            db.set_status(conn, row["id"], "filtered", apply_log=reason)
        else:
            to_score.append(db.row_to_job(row))
    console.print(f"{len(to_score)} passaram no pré-filtro e serão analisadas pelo Claude.")

    matcher = Matcher(llm, profile, tracks(cfg))
    done = 0
    for job, analysis, err in matcher.analyze_many(to_score, cfg["matching"]["workers"]):
        done += 1
        if err:
            db.set_status(conn, job.id, "error", apply_log=err)
            log(f"[{done}/{len(to_score)}] erro em '{job.title}': {err}")
            continue
        db.set_status(conn, job.id, "scored", score=analysis["score"],
                      analysis=json.dumps(analysis, ensure_ascii=False))
        log(f"[{done}/{len(to_score)}] {analysis['score']:>3} · {job.title}")

    show_table(conn, cfg["matching"]["min_score_report"], limit=20)
    path = report.build(conn, cfg["matching"]["min_score_report"])
    console.print(f"\nRelatório completo: [cyan]{path}[/]")


def show_table(conn, min_score: int, limit: int = 50, status: str | None = None):
    sql = "SELECT * FROM jobs WHERE score >= ?"
    params = [min_score]
    if status:
        sql += " AND status = ?"
        params.append(status)
    rows = conn.execute(sql + " ORDER BY score DESC LIMIT ?", (*params, limit)).fetchall()
    t = Table(title=f"Melhores vagas (nota ≥ {min_score})", show_lines=False)
    for col in ("Nota", "Vaga", "Empresa", "Modalidade", "Status", "ID"):
        t.add_column(col)
    for r in rows:
        a = db.analysis(r)
        t.add_row(str(r["score"]), r["title"][:60], (r["company"] or r["source"])[:25],
                  a.get("workplace", r["workplace"]), r["status"], r["id"])
    console.print(t)


def cmd_list(args, cfg):
    conn = db.connect()
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    console.print("Resumo: " + " · ".join(f"{k}: {v}" for k, v in counts.items()))
    show_table(conn, args.min if args.min is not None else cfg["matching"]["min_score_report"], args.limit, args.status)


def cmd_apply(args, cfg):
    mode = args.mode or cfg["apply"]["mode"]
    missing = [str(c["path"]) for c in all_cvs(cfg) if not c["path"].exists()]
    if missing:
        sys.exit("Currículo(s) não encontrado(s): " + ", ".join(missing))
    apply.run(db.connect(), cfg, load_profile(), LLM(cfg["model"]),
              mode=mode, limit=args.limit or cfg["apply"]["max_per_run"], job_id=args.job, dry_run=args.dry_run)


def cmd_report(args, cfg):
    path = report.build(db.connect(), cfg["matching"]["min_score_report"])
    console.print(f"Relatório: [cyan]{path}[/]")
    if args.open:
        webbrowser.open(Path(path).as_uri())


def cmd_import(args, cfg):
    from jobfinder import importer

    text = importer.read_clipboard() if args.clipboard else Path(args.file).read_text(encoding="utf-8")
    rows = importer.parse(text)
    new = importer.import_rows(rows, log)
    console.print(f"{len(rows)} vagas recebidas, [bold]{new} novas[/].")
    if args.score:
        args.no_web, args.skip_fetch = True, True
        cmd_search(args, cfg)


def cmd_run(args, cfg):
    cmd_search(args, cfg)
    if cfg["apply"]["mode"] != "off":
        args.mode, args.limit, args.job = None, None, None
        cmd_apply(args, cfg)


def main():
    p = argparse.ArgumentParser(prog="jobfinder", description="Busca e candidatura automática a vagas de TI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="lê o currículo, faz a entrevista e monta seu perfil")
    s.add_argument("--cv", help="usar só este currículo (padrão: todos de 'cvs' no config.yaml)")
    s.set_defaults(func=cmd_setup)

    sub.add_parser("profile", help="mostra o perfil atual").set_defaults(func=cmd_profile)

    s = sub.add_parser("search", help="busca vagas novas e dá nota para cada uma")
    s.add_argument("--no-web", action="store_true", help="não usa a busca na web (mais barato)")
    s.add_argument("--skip-fetch", action="store_true",
                   help="não busca vagas novas; só analisa as pendentes (ex.: retomar uma busca interrompida)")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("import", help="importa vagas colhidas no navegador logado (LinkedIn/Indeed/Glassdoor)")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--clipboard", action="store_true", help="lê da área de transferência (botão do export.js)")
    src.add_argument("--file", help="arquivo com uma vaga JSON por linha")
    s.add_argument("--score", action="store_true", help="em seguida filtra e analisa as vagas novas")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("list", help="lista as vagas já analisadas")
    s.add_argument("--min", type=int, help="nota mínima")
    s.add_argument("--status", choices=["scored", "applied", "skipped", "filtered", "error"])
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("apply", help="candidata-se às melhores vagas")
    s.add_argument("--mode", choices=["off", "assisted", "auto"], help="sobrescreve apply.mode do config")
    s.add_argument("--limit", type=int, help="máximo de vagas nesta rodada")
    s.add_argument("--job", help="ID de uma vaga específica")
    s.add_argument("--dry-run", action="store_true", help="simula: gera materiais e preenche, mas não envia")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("report", help="gera o relatório HTML")
    s.add_argument("--open", action="store_true", help="abre no navegador")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("run", help="search + report + apply (para agendar)")
    s.add_argument("--no-web", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_run)

    args = p.parse_args()
    cfg = load_config()
    try:
        args.func(args, cfg)
    except LLMError as e:
        console.print(f"[red]Erro na IA:[/] {e}")
        sys.exit(1)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Erro:[/] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\nInterrompido.")
