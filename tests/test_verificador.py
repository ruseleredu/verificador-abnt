"""Testes: rode com  python -m pytest -q  (ou dentro do container: docker run --rm verificador-abnt python -m pytest -q)"""
import os

import pytest
from fastapi.testclient import TestClient

from verificador import carregar_regras, verificar_pdf
from verificador.api import app

AQUI = os.path.dirname(__file__)
EX = os.path.join(AQUI, "..", "exemplos")
REGRAS = carregar_regras(os.path.join(AQUI, "..", "regras", "utfpr.yaml"))


def mensagens(rel, regra):
    return [a["mensagem"] for a in rel["achados"] if a["regra"] == regra]


@pytest.fixture(scope="module")
def ruim():
    return verificar_pdf(os.path.join(EX, "ruim.pdf"), REGRAS)[0]


@pytest.fixture(scope="module")
def modelo():
    return verificar_pdf(os.path.join(EX, "modelo-utfpr.pdf"), REGRAS)[0]


def test_ruim_reprovado(ruim):
    assert ruim["status"] == "reprovado"


@pytest.mark.parametrize("regra,trecho", [
    ("pagina.margens", "Margem esquerda"),
    ("pagina.margens", "Margem superior"),
    ("fonte.familia", "CMR10"),
    ("fonte.tamanho_corpo", "10,9 pt"),
    ("espacamento.entrelinha", "simples"),
    ("paragrafo.recuo", "sem recuo"),
    ("paginacao", "canto superior direito"),
    ("estrutura.elementos_obrigatorios", "ABSTRACT"),
    ("estrutura.secao_primaria_nova_pagina", "2 DESENVOLVIMENTO"),
    ("resumo", "5 palavras"),
    ("resumo", "Palavras-chave: 1"),
    ("ilustracoes.fonte_obrigatoria", "Figura 1"),
    ("sumario.paginas_conferem", "indica 9"),
    ("referencias.ordem_alfabetica", "ALMEIDA"),
])
def test_ruim_detecta_erros_plantados(ruim, regra, trecho):
    assert any(trecho in m for m in mensagens(ruim, regra)), mensagens(ruim, regra)


def test_modelo_estrutura_ok(modelo):
    for regra in ("estrutura.elementos_obrigatorios", "estrutura.secao_primaria_nova_pagina",
                  "sumario.paginas_conferem", "referencias.ordem_alfabetica", "ilustracoes.fonte_obrigatoria",
                  "ilustracoes.numeracao_sequencial", "espacamento.entrelinha", "fonte.familia"):
        assert not mensagens(modelo, regra), (regra, mensagens(modelo, regra))
    assert modelo["medicoes"]["pdfa"].startswith("PDF/A-3")


def test_modelo_divergencias_conhecidas(modelo):
    # divergências reais do modelo LaTeX em relação às regras (ver README)
    assert any("10,8 pt" in m for m in mensagens(modelo, "fonte.tamanho_corpo"))
    assert any("inferior" in m for m in mensagens(modelo, "pagina.margens"))


def test_digitalizado():
    rel = verificar_pdf(os.path.join(EX, "digitalizado.pdf"), REGRAS)[0]
    assert rel["status"] == "reprovado"
    assert [a["regra"] for a in rel["achados"] if a["severidade"] == "erro"] == ["documento.texto_pesquisavel"]


def test_api():
    c = TestClient(app)
    assert c.get("/saude").json() == {"status": "ok"}
    with open(os.path.join(EX, "ruim.pdf"), "rb") as f:
        r = c.post("/api/verificar?anotado=true", files={"arquivo": ("ruim.pdf", f, "application/pdf")})
    assert r.status_code == 200 and r.json()["status"] == "reprovado" and r.json()["pdf_anotado_base64"]
    r = c.post("/api/verificar", files={"arquivo": ("x.pdf", b"nao sou pdf", "application/pdf")})
    assert r.status_code == 415
    with open(os.path.join(EX, "ruim.pdf"), "rb") as f:
        r = c.post("/verificar", files={"arquivo": ("ruim.pdf", f, "application/pdf")})
    assert r.status_code == 200 and "REPROVADO" in r.text
