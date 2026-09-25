"""Linha de comando: python -m verificador.cli trabalho.pdf [--html rel.html] [--json rel.json] [--anotado saida.pdf]"""
from __future__ import annotations

import argparse
import os
import sys

from . import carregar_regras, relatorio, verificar_pdf

REGRAS_PADRAO = os.environ.get("REGRAS", os.path.join(os.path.dirname(__file__), "..", "regras", "utfpr.yaml"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verifica a formatação de um trabalho acadêmico em PDF (ABNT/UTFPR).")
    ap.add_argument("pdf", help="arquivo PDF a verificar")
    ap.add_argument("--regras", default=REGRAS_PADRAO, help="arquivo YAML de regras (padrão: regras/utfpr.yaml)")
    ap.add_argument("--json", help="grava o relatório em JSON")
    ap.add_argument("--html", help="grava o relatório em HTML")
    ap.add_argument("--anotado", help="grava uma cópia do PDF com os problemas marcados")
    ap.add_argument("--silencioso", action="store_true", help="não imprime o relatório no terminal")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.pdf):
        print(f"Arquivo não encontrado: {a.pdf}", file=sys.stderr)
        return 2
    rel, achados = verificar_pdf(a.pdf, carregar_regras(a.regras))
    if a.json:
        relatorio.para_json(rel, a.json)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(relatorio.para_html(rel))
    if a.anotado:
        relatorio.pdf_anotado(a.pdf, achados, a.anotado)
    if not a.silencioso:
        print(relatorio.para_texto(rel))
    return 1 if rel["status"] == "reprovado" else 0   # útil em CI: código 1 = reprovado


if __name__ == "__main__":
    sys.exit(main())
