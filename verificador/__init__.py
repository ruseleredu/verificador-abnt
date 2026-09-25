"""Verificador de formatação de trabalhos acadêmicos (ABNT/UTFPR) a partir do PDF."""
from __future__ import annotations

import yaml

from . import relatorio
from .estrutura import analisar
from .modelo import extrair
from .regras import verificar

__all__ = ["verificar_pdf"]


def carregar_regras(caminho: str) -> dict:
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


def verificar_pdf(pdf: str, regras: dict) -> tuple[dict, list]:
    """Executa todas as verificações. Devolve (relatório em dicionário, lista de Achados)."""
    margens = regras.get("pagina", {}).get("margens", {})
    doc = extrair(pdf, topo_mm=margens.get("superior_mm", 30), base_mm=margens.get("inferior_mm", 20))
    try:
        est = analisar(doc)
        achados, executadas, medicoes = verificar(doc, est, regras)
        rel = relatorio.montar(pdf, regras.get("nome", ""), achados, executadas, medicoes, len(doc.paginas))
        return rel, achados
    finally:
        doc.pdf.close()
