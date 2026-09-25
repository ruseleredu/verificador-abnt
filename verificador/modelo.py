"""Extração do PDF para um modelo simples: páginas, linhas visuais, imagens e desenhos."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pymupdf

PT_POR_MM = 72 / 25.4
ACENTOS_SOLTOS = set("´`^~¨¸˜ˆ˚˝˘ˇ¯·")


def mm(pt: float) -> float:
    return pt / PT_POR_MM


def pt(mm_: float) -> float:
    return mm_ * PT_POR_MM


def normalizar(texto: str) -> str:
    """Maiúsculas, sem acentos, sem pontuação e com espaços simples (para comparar títulos)."""
    t = unicodedata.normalize("NFKD", texto)
    # acentos soltos (fontes Type1 antigas compõem "Ú" como "U" + "´") são removidos sem virar espaço
    t = "".join(c for c in t if not unicodedata.combining(c) and c not in ACENTOS_SOLTOS)
    t = re.sub(r"[^0-9A-Za-z]+", " ", t)
    return re.sub(r"\s+", " ", t).strip().upper()


def nome_fonte(nome: str) -> str:
    """Remove o prefixo de subconjunto (ABCDEF+Arial-Bold -> Arial-Bold)."""
    return nome.split("+", 1)[1] if "+" in nome else nome


@dataclass
class Trecho:
    texto: str
    fonte: str
    tamanho: float
    negrito: bool
    bbox: tuple


@dataclass
class Linha:
    """Uma linha visual: todos os trechos que compartilham a mesma linha-base."""
    pagina: int                      # índice 0-based
    trechos: list = field(default_factory=list)
    base: float = 0.0                # coordenada y da linha-base (pt, origem no topo)

    @property
    def texto(self) -> str:
        partes, fim_anterior = [], None
        for t in self.trechos:
            if fim_anterior is not None and t.bbox[0] - fim_anterior > 1.5:
                partes.append(" ")
            partes.append(t.texto)
            fim_anterior = t.bbox[2]
        return re.sub(r"\s+", " ", "".join(partes)).strip()

    @property
    def bbox(self) -> tuple:
        xs0, ys0, xs1, ys1 = zip(*(t.bbox for t in self.trechos))
        return (min(xs0), min(ys0), max(xs1), max(ys1))

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def tamanho(self) -> float:
        """Tamanho predominante (ponderado pelo número de caracteres)."""
        pesos: dict[float, int] = {}
        for t in self.trechos:
            pesos[round(t.tamanho, 1)] = pesos.get(round(t.tamanho, 1), 0) + len(t.texto.strip())
        return max(pesos, key=pesos.get) if pesos else 0.0

    @property
    def negrito(self) -> bool:
        letras = [t for t in self.trechos if t.texto.strip()]
        return bool(letras) and all(t.negrito for t in letras)


@dataclass
class Pagina:
    indice: int
    largura: float
    altura: float
    linhas: list = field(default_factory=list)
    imagens: list = field(default_factory=list)     # bboxes
    desenhos: list = field(default_factory=list)    # bboxes
    numero: int | None = None                       # número de página impresso detectado
    linha_numero: Linha | None = None

    @property
    def conteudo(self) -> list:
        """Linhas de texto, exceto a do número de página."""
        return [l for l in self.linhas if l is not self.linha_numero]

    @property
    def primeira_linha(self) -> Linha | None:
        c = self.conteudo
        return c[0] if c else None


@dataclass
class Documento:
    caminho: str
    paginas: list
    pdf: pymupdf.Document


def _eh_negrito(span: dict) -> bool:
    nome = span["font"].lower()
    return bool(span["flags"] & 16) or any(k in nome for k in ("bold", "black", "heavy", "semibold", "-bd", "demi"))


def _linhas_visuais(pagina_idx: int, trechos: list) -> list:
    """Agrupa trechos pela linha-base (tolerância de 2 pt) e ordena da esquerda para a direita."""
    trechos.sort(key=lambda tb: (round(tb[1], 0), tb[0].bbox[0]))
    linhas: list[Linha] = []
    for trecho, base in trechos:
        alvo = None
        for l in reversed(linhas[-6:]):
            if abs(l.base - base) <= 2.0:
                alvo = l
                break
        if alvo is None:
            alvo = Linha(pagina=pagina_idx, base=base)
            linhas.append(alvo)
        alvo.trechos.append(trecho)
    for l in linhas:
        l.trechos.sort(key=lambda t: t.bbox[0])
    linhas.sort(key=lambda l: (l.base, l.x0))
    return _juntar_sobrescritos(linhas)


def _juntar_sobrescritos(linhas: list) -> list:
    """Sobrescritos/subscritos (ex.: o "º" de "N.º", chamadas de nota) viram parte da linha vizinha."""
    resultado = list(linhas)
    for pequena in linhas:
        tam = max(t.tamanho for t in pequena.trechos)
        x0, y0, x1, y1 = pequena.bbox
        melhor = None
        for outra in resultado:
            if outra is pequena or tam > 0.85 * outra.tamanho:
                continue
            ox0, oy0, ox1, oy1 = outra.bbox
            sobrepoe = min(y1, oy1) - max(y0, oy0)
            if sobrepoe > 0.3 * (y1 - y0) and ox0 - 2 <= x0 and x1 <= ox1 + 20:
                melhor = outra
                break
        if melhor is not None:
            melhor.trechos.extend(pequena.trechos)
            melhor.trechos.sort(key=lambda t: t.bbox[0])
            resultado.remove(pequena)
    return resultado


_ROMANO = re.compile(r"^[ivxlcdm]+$", re.I)


def _detectar_numero(p: Pagina, topo_mm: float, base_mm: float) -> None:
    """Número de página: só dígitos, no topo (margem superior) ou no rodapé, isolado na linha."""
    candidatas = []
    for l in p.linhas:
        t = l.texto
        if not (t.isdigit() or _ROMANO.match(t)) or len(t) > 4:
            continue
        x0, y0, x1, y1 = l.bbox
        if y1 < pt(topo_mm) or y0 > p.altura - pt(base_mm):
            candidatas.append(l)
    if candidatas:
        l = candidatas[0]
        p.linha_numero = l
        p.numero = int(l.texto) if l.texto.isdigit() else None


def extrair(caminho: str, topo_mm: float = 30, base_mm: float = 20) -> Documento:
    pdf = pymupdf.open(caminho)
    paginas = []
    for i, pg in enumerate(pdf):
        p = Pagina(indice=i, largura=pg.rect.width, altura=pg.rect.height)
        trechos = []
        info = pg.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
        for bloco in info["blocks"]:
            if bloco["type"] == 1:
                p.imagens.append(tuple(bloco["bbox"]))
                continue
            for linha in bloco.get("lines", []):
                for s in linha["spans"]:
                    if not s["text"].strip() or all(ch in ACENTOS_SOLTOS for ch in s["text"].strip()):
                        continue   # acentos compostos à parte (fontes Type1 antigas) não são texto
                    trechos.append((Trecho(s["text"], nome_fonte(s["font"]), s["size"],
                                           _eh_negrito(s), tuple(s["bbox"])), s["origin"][1]))
        p.linhas = _linhas_visuais(i, trechos)
        for img in pg.get_image_info():
            b = tuple(img["bbox"])
            if b not in p.imagens:
                p.imagens.append(b)
        for d in pg.get_drawings():
            r = d["rect"]
            # ignora retângulos do tamanho da página (fundos) e traços degenerados
            if r.width >= p.largura * 0.98 and r.height >= p.altura * 0.98:
                continue
            p.desenhos.append((r.x0, r.y0, r.x1, r.y1))
        _detectar_numero(p, topo_mm, base_mm)
        paginas.append(p)
    return Documento(caminho=caminho, paginas=paginas, pdf=pdf)
