"""Relatório HTML com as vagas ranqueadas (output/relatorio.html)."""

import html
from datetime import datetime

from jobfinder import db
from jobfinder.config import OUTPUT_DIR

WORKPLACE = {"remote": "Remoto", "hybrid": "Híbrido", "onsite": "Presencial", "unknown": "?"}
STATUS = {"scored": "Na fila", "applied": "Candidatado", "skipped": "Pulada", "error": "Erro"}

CSS = """
:root{--bg:#f7f7f8;--card:#fff;--fg:#1d1d1f;--muted:#6b6b72;--line:#e4e4e8;--accent:#2457d6;
--good:#1a7f37;--mid:#9a6700;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1d1d20;--fg:#ececf0;--muted:#9a9aa3;
--line:#2c2c31;--accent:#7aa2ff;--good:#4ac26b;--mid:#d4a72c;--bad:#ff7b72}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin-bottom:20px}.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;
padding:6px 12px;cursor:pointer}.filters button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.job{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px;
display:grid;grid-template-columns:56px 1fr;gap:14px}.score{font-size:22px;font-weight:700;text-align:center}
.s-good{color:var(--good)}.s-mid{color:var(--mid)}.s-bad{color:var(--bad)}
.title{font-weight:600;font-size:16px}.title a{color:var(--fg);text-decoration:none}.title a:hover{color:var(--accent)}
.meta{color:var(--muted);font-size:13px;margin:2px 0 6px}.tag{display:inline-block;border:1px solid var(--line);
border-radius:6px;padding:0 6px;margin:2px 4px 0 0;font-size:12px}.ok{color:var(--good)}.miss{color:var(--bad)}
.id{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--muted)}
"""

JS = """
document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 const f=b.dataset.f;document.querySelectorAll('.job').forEach(j=>{
 j.style.display=(f==='all'||j.dataset.w===f||j.dataset.s===f)?'':'none'})});
"""


def build(conn, min_score: int) -> str:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE score >= ? AND status IN ('scored','applied') ORDER BY score DESC, found_at DESC",
        (min_score,),
    ).fetchall()
    e = html.escape
    cards = []
    for r in rows:
        a = db.analysis(r)
        sc = r["score"] or 0
        cls = "s-good" if sc >= 70 else "s-mid" if sc >= 50 else "s-bad"
        w = a.get("workplace") or r["workplace"]
        meta = " · ".join(x for x in [
            e(r["company"] or ""), WORKPLACE.get(w, w), e(a.get("location") or r["location"] or ""),
            e(a.get("contract", "")), e(a.get("salary", "")), e(r["source"]), STATUS.get(r["status"], r["status"]),
        ] if x and x != "indefinido")
        skills = "".join(f'<span class="tag ok">{e(s)}</span>' for s in a.get("matched_skills", [])[:10])
        skills += "".join(f'<span class="tag miss">{e(s)}</span>' for s in a.get("missing_skills", [])[:6])
        flags = "".join(f'<span class="tag miss">⚠ {e(s)}</span>' for s in a.get("red_flags", []))
        cards.append(
            f'<div class="job" data-w="{e(w)}" data-s="{e(r["status"])}"><div class="score {cls}">{sc}</div><div>'
            f'<div class="title"><a href="{e(r["url"])}" target="_blank" rel="noopener">{e(r["title"])}</a></div>'
            f'<div class="meta">{meta}</div><div>{e(a.get("summary", ""))}</div><div>{skills}{flags}</div>'
            f'<div class="id">id {r["id"]} · python -m jobfinder apply --job {r["id"]}</div></div></div>'
        )
    page = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Vagas encontradas</title>
<style>{CSS}</style></head><body><main><h1>Vagas encontradas</h1>
<div class="sub">{len(rows)} vagas com nota ≥ {min_score} · gerado em {datetime.now():%d/%m/%Y %H:%M}</div>
<div class="filters"><button class="on" data-f="all">Todas</button><button data-f="remote">Remoto</button>
<button data-f="hybrid">Híbrido</button><button data-f="onsite">Presencial</button>
<button data-f="scored">Na fila</button><button data-f="applied">Candidatado</button></div>
{''.join(cards) or '<p>Nenhuma vaga ainda.</p>'}</main><script>{JS}</script></body></html>"""
    path = OUTPUT_DIR / "relatorio.html"
    path.write_text(page, encoding="utf-8")
    return str(path)
