"""API web (FastAPI).

GET  /                 formulário de envio (HTML)
POST /verificar        recebe o PDF (multipart, campo "arquivo") e devolve o relatório em HTML
POST /api/verificar    idem, devolve JSON  (?anotado=true inclui o PDF anotado em base64)
GET  /api/regras       regras em uso
GET  /saude            verificação de saúde do container
"""
from __future__ import annotations

import base64
import os
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from . import carregar_regras, relatorio, verificar_pdf

REGRAS_ARQ = os.environ.get("REGRAS", os.path.join(os.path.dirname(__file__), "..", "regras", "utfpr.yaml"))
TAMANHO_MAX_MB = int(os.environ.get("TAMANHO_MAX_MB", "60"))

app = FastAPI(title="Verificador de formatação ABNT/UTFPR", version="1.0")

FORM = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Verificador de formatação</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 16px;background:#f7f7f5;color:#1d1d1f}
form{background:#fff;border:1px solid #e3e3e0;border-radius:10px;padding:24px}button{margin-top:16px;padding:10px 18px;
border:0;border-radius:8px;background:#1a5fb4;color:#fff;font-size:15px;cursor:pointer}p{color:#6b6b70}</style></head>
<body><h1>Verificador de formatação</h1><p>Envie o PDF do trabalho para conferir margens, fonte, espaçamento,
paginação, estrutura, resumo, legendas, sumário e referências conforme as regras da UTFPR/ABNT.</p>
<form action="/verificar" method="post" enctype="multipart/form-data">
<input type="file" name="arquivo" accept="application/pdf" required><br><button>Verificar</button></form></body></html>"""


def _processar(arquivo: UploadFile, anotado: bool = False):
    dados = arquivo.file.read(TAMANHO_MAX_MB * 1024 * 1024 + 1)
    if len(dados) > TAMANHO_MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"Arquivo maior que {TAMANHO_MAX_MB} MB.")
    if not dados.startswith(b"%PDF"):
        raise HTTPException(415, "O arquivo enviado não é um PDF.")
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, os.path.basename(arquivo.filename or "trabalho.pdf"))
        with open(caminho, "wb") as f:
            f.write(dados)
        try:
            rel, achados = verificar_pdf(caminho, carregar_regras(REGRAS_ARQ))
        except Exception as exc:  # PDF corrompido/criptografado
            raise HTTPException(422, f"Não foi possível ler o PDF: {exc}") from exc
        if anotado:
            destino = os.path.join(tmp, "anotado.pdf")
            relatorio.pdf_anotado(caminho, achados, destino)
            with open(destino, "rb") as f:
                rel["pdf_anotado_base64"] = base64.b64encode(f.read()).decode()
    return rel


@app.get("/", response_class=HTMLResponse)
def inicio():
    return FORM


@app.post("/verificar", response_class=HTMLResponse)
def verificar_html(arquivo: UploadFile = File(...)):
    return relatorio.para_html(_processar(arquivo))


@app.post("/api/verificar")
def verificar_json(arquivo: UploadFile = File(...), anotado: bool = False):
    return JSONResponse(_processar(arquivo, anotado))


@app.get("/api/regras")
def regras():
    return carregar_regras(REGRAS_ARQ)


@app.get("/saude")
def saude():
    return {"status": "ok"}
