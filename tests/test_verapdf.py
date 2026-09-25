"""Testes da integração com o veraPDF (usam um servidor falso com o mesmo protocolo do veraPDF-rest)."""
import json
import os
import shutil
import stat
import sys

import pytest

from verificador import carregar_regras, verificar_pdf, verapdf
from tests import fake_verapdf

AQUI = os.path.dirname(__file__)
EX = os.path.join(AQUI, "..", "exemplos")
FIX = os.path.join(AQUI, "fixtures")
REGRAS = os.path.join(AQUI, "..", "regras", "utfpr.yaml")


def fixture(nome):
    with open(os.path.join(FIX, nome), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def servidor():
    s = fake_verapdf.iniciar()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


@pytest.fixture
def com_verapdf(monkeypatch, servidor):
    monkeypatch.delenv("VERAPDF_CLI", raising=False)
    monkeypatch.setenv("VERAPDF_URL", servidor)


def achados(rel, regra="documento.pdfa_verapdf"):
    return [a for a in rel["achados"] if a["regra"] == regra]


# ---------------------------------------------------------------- interpretação do JSON
def test_interpretar_nao_conforme():
    r = verapdf.interpretar(fixture("verapdf_nao_conforme.json"))
    assert r.conforme is False and r.perfil == "PDF/A-1B validation profile"
    assert [f.codigo for f in r.regras_falhas] == ["6.2.3.3-1", "6.7.2-1", "6.1.3-1"]   # PASSED é ignorada
    rgb = r.regras_falhas[0]
    assert rgb.falhas == 7 and rgb.paginas == [2, 4, 5, 6]          # pages[N] é 0-based
    assert rgb.exemplos == ["DeviceRGB colour space is used without RGB output intent profile"]
    assert r.regras_aprovadas == 118 and r.verificacoes_falhas == 9


def test_interpretar_conforme():
    r = verapdf.interpretar(fixture("verapdf_conforme.json"))
    assert r.conforme is True and not r.regras_falhas and r.perfil.startswith("PDF/A-3B")


def test_interpretar_formato_antigo():
    r = verapdf.interpretar(fixture("verapdf_formato_antigo.json"))      # validationResult objeto, não lista
    assert r.conforme is False and r.regras_falhas[0].clausula == "6.2.11.4.1" and r.regras_falhas[0].paginas == [1]


def test_interpretar_excecao():
    r = verapdf.interpretar(fixture("verapdf_excecao.json"))
    assert r.conforme is None and "invalid PDF header" in r.erro


# ---------------------------------------------------------------- REST
def test_rest_envia_multipart_com_perfil(servidor):
    fake_verapdf.Handler.recebidos.clear()
    bruto = verapdf.validar_rest(os.path.join(EX, "ruim.pdf"), servidor, "3b")
    assert fake_verapdf.Handler.recebidos == [("3b", "ruim.pdf", "application/json")]
    assert bruto["report"]["jobs"][0]["validationResult"][0]["compliant"] is False


def test_verificador_reprova_pdf_nao_conforme(com_verapdf):
    rel = verificar_pdf(os.path.join(EX, "ruim.pdf"), carregar_regras(REGRAS))[0]
    msgs = [a["mensagem"] for a in achados(rel)]
    assert rel["status"] == "reprovado"
    assert "não conforme com o PDF/A-1B" in msgs[0] and "3 regra(s)" in msgs[0]
    assert any("§6.2.3.3-1" in m and "Páginas: 2, 4–6" in m for m in msgs)
    assert any("§6.7.2-1" in m for m in msgs)
    assert all(a["severidade"] == "erro" for a in achados(rel))
    assert "NÃO conforme" in rel["medicoes"]["pdfa_verapdf"]


def test_verificador_aprova_pdf_conforme(com_verapdf):
    rel = verificar_pdf(os.path.join(EX, "modelo-utfpr.pdf"), carregar_regras(REGRAS))[0]
    assert not achados(rel)
    assert rel["medicoes"]["pdfa_verapdf"].startswith("PDF/A-3B validation profile: conforme")


def test_verificador_pdf_que_verapdf_nao_processa(com_verapdf, tmp_path):
    quebrado = tmp_path / "quebrado.pdf"
    shutil.copy(os.path.join(EX, "ruim.pdf"), quebrado)
    rel = verificar_pdf(str(quebrado), carregar_regras(REGRAS))[0]
    assert any("não conseguiu processar" in a["mensagem"] for a in achados(rel))


# ---------------------------------------------------------------- indisponibilidade
def test_indisponivel_vira_aviso(monkeypatch):
    monkeypatch.setenv("VERAPDF_URL", "http://127.0.0.1:9")           # porta fechada
    regras = carregar_regras(REGRAS)
    regras["documento"]["pdfa_verapdf"]["tentativas"] = 1
    rel = verificar_pdf(os.path.join(EX, "modelo-utfpr.pdf"), regras)[0]
    a = achados(rel)
    assert len(a) == 1 and a[0]["severidade"] == "aviso" and "não executada" in a[0]["mensagem"]


def test_indisponivel_obrigatorio_reprova(monkeypatch):
    monkeypatch.setenv("VERAPDF_URL", "http://127.0.0.1:9")
    regras = carregar_regras(REGRAS)
    regras["documento"]["pdfa_verapdf"].update(tentativas=1, obrigatorio=True)
    rel = verificar_pdf(os.path.join(EX, "modelo-utfpr.pdf"), regras)[0]
    assert achados(rel)[0]["severidade"] == "erro"


def test_sem_configuracao_avisa(monkeypatch):
    monkeypatch.delenv("VERAPDF_URL", raising=False)
    monkeypatch.delenv("VERAPDF_CLI", raising=False)
    rel = verificar_pdf(os.path.join(EX, "modelo-utfpr.pdf"), carregar_regras(REGRAS))[0]
    assert "VERAPDF_URL" in achados(rel)[0]["mensagem"]


def test_perfil_invalido():
    with pytest.raises(ValueError):
        verapdf.validar("x.pdf", {"perfil": "9z", "url": "http://x"})


# ---------------------------------------------------------------- CLI
def test_modo_cli(monkeypatch, tmp_path):
    exe = tmp_path / "verapdf"
    exe.write_text(f"#!{sys.executable}\nimport sys\nassert sys.argv[1:4] == ['--format', 'json', '--flavour']\n"
                   f"print(open({os.path.join(FIX, 'verapdf_nao_conforme.json')!r}).read())\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.delenv("VERAPDF_URL", raising=False)
    monkeypatch.setenv("VERAPDF_CLI", str(exe))
    r = verapdf.validar(os.path.join(EX, "ruim.pdf"), {"perfil": "2b"})
    assert r.conforme is False and len(r.regras_falhas) == 3
