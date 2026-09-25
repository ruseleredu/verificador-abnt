"""Validação PDF/A completa com o veraPDF (https://verapdf.org).

Dois modos, escolhidos pela configuração:
  * REST: envia o PDF ao serviço veraPDF-rest (imagem Docker verapdf/rest, porta 8080),
          POST {url}/api/validate/{perfil}  com o campo multipart "file"; resposta em JSON.
  * CLI:  executa o veraPDF instalado localmente:  verapdf --format json --flavour {perfil} arquivo.pdf

Nos dois casos o JSON segue o relatório do veraPDF:
  report.jobs[].validationResult[] -> {compliant, profileName, details.{ruleSummaries[]}}
  ruleSummaries[] -> {ruleStatus, specification, clause, testNumber, description, failedChecks, checks[]}
  checks[] -> {status, context, errorMessage}     (context contém "pages[N]" quando o erro é de uma página)
(Em versões antigas validationResult é um objeto único; os dois formatos são aceitos.)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

PERFIS = {"auto", "0", "1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u", "4", "4e", "4f", "ua1", "ua2"}


class VeraPDFIndisponivel(Exception):
    """O serviço/programa veraPDF não pôde ser usado (fora do ar, tempo esgotado, não instalado)."""


@dataclass
class RegraFalha:
    especificacao: str
    clausula: str
    teste: int | None
    descricao: str
    falhas: int
    paginas: list = field(default_factory=list)      # números físicos (1-based)
    exemplos: list = field(default_factory=list)     # mensagens de erro (até 3)

    @property
    def codigo(self) -> str:
        return f"{self.clausula}-{self.teste}" if self.teste is not None else self.clausula


@dataclass
class ResultadoVeraPDF:
    conforme: bool | None
    perfil: str = ""
    regras_falhas: list = field(default_factory=list)
    regras_aprovadas: int | None = None
    verificacoes_falhas: int | None = None
    erro: str = ""          # falha do veraPDF ao processar o arquivo (PDF corrompido, criptografado...)


# ------------------------------------------------------------------ transporte
def _multipart(caminho: str) -> tuple[bytes, str]:
    limite = uuid.uuid4().hex
    with open(caminho, "rb") as f:
        dados = f.read()
    nome = os.path.basename(caminho).replace('"', "")
    corpo = (f"--{limite}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{nome}\"\r\n"
             "Content-Type: application/pdf\r\n\r\n").encode() + dados + f"\r\n--{limite}--\r\n".encode()
    return corpo, f"multipart/form-data; boundary={limite}"


def validar_rest(caminho: str, url: str, perfil: str = "auto", timeout: float = 180,
                 tentativas: int = 3, espera: float = 3.0) -> dict:
    """Envia o PDF ao veraPDF-rest. Repete algumas vezes se o serviço ainda estiver subindo."""
    corpo, tipo = _multipart(caminho)
    destino = f"{url.rstrip('/')}/api/validate/{perfil}"
    ultimo = None
    for n in range(tentativas):
        req = urllib.request.Request(destino, data=corpo, method="POST",
                                     headers={"Content-Type": tipo, "Accept": "application/json",
                                              "X-File-Size": str(os.path.getsize(caminho))})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            texto = exc.read().decode("utf-8", "replace")[:300]
            if exc.code >= 500 and n + 1 < tentativas:
                ultimo = f"HTTP {exc.code}: {texto}"
            else:
                raise VeraPDFIndisponivel(f"veraPDF respondeu HTTP {exc.code}: {texto}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            ultimo = str(getattr(exc, "reason", exc))
        except json.JSONDecodeError as exc:
            raise VeraPDFIndisponivel(f"resposta do veraPDF não é JSON ({exc})") from exc
        if n + 1 < tentativas:
            time.sleep(espera)
    raise VeraPDFIndisponivel(f"não foi possível acessar o veraPDF em {url} ({ultimo})")


def validar_cli(caminho: str, executavel: str = "verapdf", perfil: str = "auto", timeout: float = 300) -> dict:
    """Executa o veraPDF de linha de comando (versão 1.24 ou superior, com saída JSON)."""
    exe = shutil.which(executavel) or (executavel if os.path.isfile(executavel) else None)
    if not exe:
        raise VeraPDFIndisponivel(f"executável do veraPDF não encontrado: {executavel}")
    cmd = [exe, "--format", "json"]
    if perfil != "auto":
        cmd += ["--flavour", perfil]
    cmd.append(caminho)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise VeraPDFIndisponivel(f"veraPDF excedeu {timeout:.0f} s") from exc
    saida = p.stdout[p.stdout.find("{"):] if "{" in p.stdout else ""
    try:
        return json.loads(saida)
    except json.JSONDecodeError as exc:
        raise VeraPDFIndisponivel(f"saída do veraPDF não é JSON (código {p.returncode}): {p.stderr[:300]}") from exc


# ------------------------------------------------------------------ interpretação
_RE_PAGINA = re.compile(r"pages\[(\d+)\]")


def _achar_excecao(no) -> str:
    """Procura mensagens de exceção do veraPDF (arquivo que não pôde ser processado)."""
    if isinstance(no, dict):
        for k, v in no.items():
            if "xception" in k and v:
                if isinstance(v, dict):
                    return str(v.get("message") or v.get("exceptionMessage") or v)
                return str(v)
            achado = _achar_excecao(v)
            if achado:
                return achado
    elif isinstance(no, list):
        for v in no:
            achado = _achar_excecao(v)
            if achado:
                return achado
    return ""


def interpretar(relatorio: dict) -> ResultadoVeraPDF:
    rel = relatorio.get("report", relatorio)
    jobs = rel.get("jobs") or []
    resultados = []
    for job in jobs:
        vr = job.get("validationResult")
        if isinstance(vr, dict):
            resultados.append(vr)
        elif isinstance(vr, list):
            resultados.extend(x for x in vr if isinstance(x, dict))
    if not resultados:
        return ResultadoVeraPDF(conforme=None, erro=_achar_excecao(rel) or "o veraPDF não devolveu resultado de validação")

    conforme = all(bool(r.get("compliant")) for r in resultados)
    perfil = "; ".join(r.get("profileName", "") for r in resultados if r.get("profileName"))
    falhas, aprovadas, checagens = [], 0, 0
    for r in resultados:
        d = r.get("details") or {}
        aprovadas += int(d.get("passedRules") or 0)
        checagens += int(d.get("failedChecks") or 0)
        for s in d.get("ruleSummaries") or []:
            status = (s.get("ruleStatus") or s.get("status") or "").upper()
            if status not in ("FAILED", "FAIL"):
                continue
            checks = [c for c in s.get("checks") or [] if (c.get("status") or "failed").lower().startswith("fail")]
            paginas = sorted({int(m) + 1 for c in checks for m in _RE_PAGINA.findall(c.get("context") or "")})
            exemplos = []
            for c in checks:
                msg = (c.get("errorMessage") or "").strip()
                if msg and msg not in exemplos:
                    exemplos.append(msg)
                if len(exemplos) == 3:
                    break
            falhas.append(RegraFalha(
                especificacao=s.get("specification", ""), clausula=str(s.get("clause", "")),
                teste=s.get("testNumber"), descricao=(s.get("description") or "").strip(),
                falhas=int(s.get("failedChecks") or len(checks) or 1), paginas=paginas, exemplos=exemplos))
    return ResultadoVeraPDF(conforme=conforme, perfil=perfil, regras_falhas=falhas,
                            regras_aprovadas=aprovadas, verificacoes_falhas=checagens)


def validar(caminho: str, cfg: dict) -> ResultadoVeraPDF:
    """Escolhe REST ou CLI conforme a configuração (variáveis de ambiente têm prioridade)."""
    perfil = str(cfg.get("perfil", "auto")).lower()
    if perfil not in PERFIS:
        raise ValueError(f"perfil veraPDF inválido: {perfil} (use um de {sorted(PERFIS)})")
    cli = os.environ.get("VERAPDF_CLI") or cfg.get("cli")
    url = os.environ.get("VERAPDF_URL") or cfg.get("url")
    if url:
        bruto = validar_rest(caminho, url, perfil, float(cfg.get("timeout_s", 180)),
                             int(cfg.get("tentativas", 3)), float(cfg.get("espera_s", 3)))
    elif cli:
        bruto = validar_cli(caminho, cli, perfil, float(cfg.get("timeout_s", 300)))
    else:
        raise VeraPDFIndisponivel("nenhum veraPDF configurado (defina VERAPDF_URL ou VERAPDF_CLI)")
    return interpretar(bruto)
