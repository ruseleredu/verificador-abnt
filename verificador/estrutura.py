"""Reconhecimento da estrutura do trabalho a partir do texto das páginas."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .modelo import Documento, Linha, normalizar

# Títulos de elementos pré e pós-textuais (normalizados, sem acento)
TITULOS = {
    "ERRATA": "errata",
    "FOLHA DE APROVACAO": "folha de aprovação",
    "TERMO DE APROVACAO": "folha de aprovação",
    "DEDICATORIA": "dedicatória",
    "AGRADECIMENTOS": "agradecimentos",
    "EPIGRAFE": "epígrafe",
    "RESUMO": "resumo",
    "ABSTRACT": "abstract",
    "RESUMEN": "resumen",
    "LISTA DE ILUSTRACOES": "lista de ilustrações",
    "LISTA DE FIGURAS": "lista de figuras",
    "LISTA DE QUADROS": "lista de quadros",
    "LISTA DE GRAFICOS": "lista de gráficos",
    "LISTA DE FOTOGRAFIAS": "lista de fotografias",
    "LISTA DE TABELAS": "lista de tabelas",
    "LISTA DE ABREVIATURAS E SIGLAS": "lista de abreviaturas e siglas",
    "LISTA DE SIGLAS": "lista de siglas",
    "LISTA DE ABREVIATURAS": "lista de abreviaturas",
    "LISTA DE SIMBOLOS": "lista de símbolos",
    "LISTA DE ALGORITMOS": "lista de algoritmos",
    "SUMARIO": "sumário",
    "REFERENCIAS": "referências",
    "GLOSSARIO": "glossário",
    "INDICE": "índice",
}

RE_PRIMARIA = re.compile(r"^(\d{1,2})\s+(\S.*)$")          # "1 INTRODUÇÃO"
RE_APENDICE = re.compile(r"^(APENDICE|ANEXO)\s+[A-Z]\b")


@dataclass
class Titulo:
    pagina: int          # índice 0-based
    texto: str
    linha: Linha
    numero: int | None = None


@dataclass
class Estrutura:
    elementos: dict = field(default_factory=dict)   # rótulo -> índice da página
    inicio_textual: int | None = None               # índice da página da introdução
    inicio_referencias: int | None = None
    primarios: list = field(default_factory=list)   # [Titulo]
    apendices: list = field(default_factory=list)   # [Titulo]
    tamanho_corpo: float = 0.0
    margem_texto_esq: float = 0.0                   # x mais frequente do início das linhas (pt)

    def parte_textual(self, doc: Documento) -> range:
        ini = self.inicio_textual if self.inicio_textual is not None else 0
        fim = self.inicio_referencias if self.inicio_referencias is not None else len(doc.paginas)
        return range(ini, max(ini, fim))


def _caixa_alta(texto: str) -> bool:
    letras = [c for c in texto if c.isalpha()]
    return bool(letras) and sum(c.isupper() for c in letras) / len(letras) >= 0.85


def analisar(doc: Documento) -> Estrutura:
    e = Estrutura()

    # 1) elementos com título na primeira linha da página
    for p in doc.paginas:
        l = p.primeira_linha
        if l is None:
            continue
        n = normalizar(l.texto)
        if n in TITULOS and TITULOS[n] not in e.elementos:
            e.elementos[TITULOS[n]] = p.indice
        m = RE_APENDICE.match(n)
        if m:
            e.apendices.append(Titulo(p.indice, l.texto, l))

    # 2) início da parte textual: primeiro "1 <TÍTULO EM CAIXA ALTA>" no topo de página, depois do sumário
    depois_de = e.elementos.get("sumário", -1)
    for p in doc.paginas:
        if p.indice <= depois_de:
            continue
        l = p.primeira_linha
        if l is None:
            continue
        m = RE_PRIMARIA.match(l.texto)
        if m and m.group(1) == "1" and _caixa_alta(m.group(2)):
            e.inicio_textual = p.indice
            break
        if normalizar(l.texto) == "INTRODUCAO":
            e.inicio_textual = p.indice
            break
    if e.inicio_textual is not None:
        e.elementos["introdução"] = e.inicio_textual

    ref = e.elementos.get("referências")
    if ref is not None and (e.inicio_textual is None or ref > e.inicio_textual):
        e.inicio_referencias = ref

    # 3) tamanho do corpo do texto e margem esquerda usual (parte textual)
    tam, xs = Counter(), Counter()
    for i in e.parte_textual(doc):
        for l in doc.paginas[i].conteudo:
            for t in l.trechos:
                tam[round(t.tamanho, 1)] += len(t.texto.strip())
            xs[round(l.x0)] += 1
    if not tam:  # sem parte textual reconhecida: usa o documento todo
        for p in doc.paginas:
            for l in p.conteudo:
                for t in l.trechos:
                    tam[round(t.tamanho, 1)] += len(t.texto.strip())
                xs[round(l.x0)] += 1
    e.tamanho_corpo = tam.most_common(1)[0][0] if tam else 0.0
    e.margem_texto_esq = xs.most_common(1)[0][0] if xs else 0.0

    # 4) títulos de seção primária na parte textual (e até as referências)
    if e.inicio_textual is not None:
        esperado = 1
        for i in e.parte_textual(doc):
            for l in doc.paginas[i].conteudo:
                m = RE_PRIMARIA.match(l.texto)
                if not m or abs(l.x0 - e.margem_texto_esq) > 3:
                    continue
                num = int(m.group(1))
                # título: número inteiro seguido de texto curto, tamanho >= corpo, em sequência
                if num == esperado and len(l.texto) < 150 and l.tamanho >= e.tamanho_corpo - 0.5 \
                        and (_caixa_alta(m.group(2)) or l.negrito):
                    e.primarios.append(Titulo(i, l.texto, l, num))
                    esperado += 1
    return e
