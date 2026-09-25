"""Relatórios: dicionário/JSON, HTML, texto para terminal e PDF anotado."""
from __future__ import annotations

import html
import json
import os
from datetime import datetime

import pymupdf

from .regras import Achado

ROTULO = {"erro": "Erro", "aviso": "Aviso", "info": "Info"}
COR = {"erro": (0.85, 0.1, 0.1), "aviso": (0.95, 0.55, 0.0), "info": (0.2, 0.4, 0.9)}


def montar(caminho: str, nome_regras: str, achados: list, executadas: list, medicoes: dict, paginas: int) -> dict:
    cont = {s: sum(a.severidade == s for a in achados) for s in ("erro", "aviso", "info")}
    return {
        "arquivo": os.path.basename(caminho),
        "regras": nome_regras,
        "data": datetime.now().isoformat(timespec="seconds"),
        "paginas": paginas,
        "status": "reprovado" if cont["erro"] else "aprovado",
        "contagem": cont,
        "verificacoes": executadas,
        "medicoes": medicoes,
        "achados": [
            {"regra": a.regra, "severidade": a.severidade, "mensagem": a.mensagem, "paginas": a.paginas}
            for a in sorted(achados, key=lambda a: ("erro", "aviso", "info").index(a.severidade))
        ],
    }


def para_json(rel: dict, destino: str) -> None:
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(rel, f, ensure_ascii=False, indent=2)


def para_texto(rel: dict) -> str:
    c = rel["contagem"]
    linhas = [f"Arquivo: {rel['arquivo']} ({rel['paginas']} páginas) — regras: {rel['regras']}",
              f"Resultado: {rel['status'].upper()}  ({c['erro']} erro(s), {c['aviso']} aviso(s))", ""]
    for v in rel["verificacoes"]:
        marca = "OK " if v["achados"] == 0 else ("ERR" if not v["ok"] else "AV ")
        linhas.append(f"  [{marca}] {v['titulo']}")
    if rel["achados"]:
        linhas.append("")
    for a in rel["achados"]:
        linhas.append(f"- {ROTULO[a['severidade']].upper()}: {a['mensagem']}")
    return "\n".join(linhas)


_CSS = """
:root{--bg:#f7f7f5;--card:#fff;--tx:#1d1d1f;--mut:#6b6b70;--bd:#e3e3e0;--err:#c62828;--av:#b26a00;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--card:#202023;--tx:#ededed;--mut:#a0a0a8;--bd:#34343a;--err:#ef5350;--av:#ffb74d;--ok:#66bb6a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px;margin:10px 0}
.status{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.pill{font-weight:600;padding:4px 12px;border-radius:99px;color:#fff}
.reprovado{background:var(--err)}.aprovado{background:var(--ok)}
table{width:100%;border-collapse:collapse}td{padding:6px 8px;border-top:1px solid var(--bd);vertical-align:top}
td:first-child{width:28px}.erro{color:var(--err)}.aviso{color:var(--av)}.ok{color:var(--ok)}
.achado{border-left:4px solid var(--bd);padding:10px 12px;margin:8px 0;background:var(--card);border-radius:6px}
.achado.erro{border-color:var(--err);color:var(--tx)}.achado.aviso{border-color:var(--av);color:var(--tx)}
.tag{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0}dt{color:var(--mut)}dd{margin:0;overflow-wrap:anywhere}
"""


def para_html(rel: dict) -> str:
    e = html.escape
    c = rel["contagem"]
    verif = "".join(
        f"<tr><td class='{'ok' if v['achados'] == 0 else ('erro' if not v['ok'] else 'aviso')}'>"
        f"{'✓' if v['achados'] == 0 else ('✗' if not v['ok'] else '!')}</td><td>{e(v['titulo'])}</td>"
        f"<td class='mut'>{e(v['regra'])}</td></tr>" for v in rel["verificacoes"])
    achados = "".join(
        f"<div class='achado {a['severidade']}'><span class='tag {a['severidade']}'>{ROTULO[a['severidade']]}</span> "
        f"<span class='mut'>· {e(a['regra'])}</span><div>{e(a['mensagem'])}</div></div>" for a in rel["achados"]
    ) or "<p class='ok'>Nenhum problema encontrado.</p>"
    med = "".join(f"<dt>{e(str(k))}</dt><dd>{e(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))}</dd>"
                  for k, v in rel["medicoes"].items())
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Verificação de formatação</title>
<style>{_CSS}</style></head><body><main>
<h1>Verificação de formatação</h1>
<p class="mut">{e(rel['arquivo'])} · {rel['paginas']} páginas · {e(rel['regras'])} · {e(rel['data'])}</p>
<div class="card status"><span class="pill {rel['status']}">{rel['status'].upper()}</span>
<span><b class="erro">{c['erro']}</b> erro(s)</span><span><b class="aviso">{c['aviso']}</b> aviso(s)</span></div>
<h2>Problemas encontrados</h2>{achados}
<h2>Verificações executadas</h2><div class="card"><table>{verif}</table></div>
<h2>Medições</h2><div class="card"><dl>{med}</dl></div>
<p class="mut">Verificação automática por heurísticas sobre o PDF. Não substitui a conferência da biblioteca:
conteúdo das referências, citações e redação não são avaliados.</p>
</main></body></html>"""


def pdf_anotado(origem: str, achados: list[Achado], destino: str) -> None:
    """Copia o PDF marcando em cada página os trechos com problema (retângulo + nota)."""
    doc = pymupdf.open(origem)
    notas_pag: dict[int, list] = {}
    for a in achados:
        cor = COR[a.severidade]
        marcadas = set()
        for i, r in a.caixas:
            rect = pymupdf.Rect(r) + (-2, -2, 2, 2)
            annot = doc[i].add_rect_annot(rect)
            annot.set_colors(stroke=cor)
            annot.set_border(width=1.2)
            annot.set_info(title=f"{ROTULO[a.severidade]} — {a.regra}", content=a.mensagem)
            annot.update()
            marcadas.add(i)
        for p in a.paginas:
            if p - 1 not in marcadas and 0 < p <= len(doc):
                notas_pag.setdefault(p - 1, []).append(a)
    for i, lista in notas_pag.items():
        pg = doc[i]
        for k, a in enumerate(lista[:6]):
            annot = pg.add_text_annot((8, 8 + 18 * k), a.mensagem, icon="Note")
            annot.set_colors(stroke=COR[a.severidade])
            annot.set_info(title=f"{ROTULO[a.severidade]} — {a.regra}")
            annot.update()
    doc.save(destino, garbage=3, deflate=True)
