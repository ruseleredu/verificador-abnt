"""Verificações de formatação. Cada verificação devolve uma lista de Achados."""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pikepdf

from .estrutura import RE_APENDICE, Estrutura, _caixa_alta
from .modelo import Documento, mm, normalizar, pt


@dataclass
class Achado:
    regra: str
    severidade: str                 # erro | aviso | info
    mensagem: str
    paginas: list = field(default_factory=list)       # números físicos (1-based)
    caixas: list = field(default_factory=list)        # [(indice_pagina, (x0,y0,x1,y1))]


@dataclass
class Contexto:
    doc: Documento
    est: Estrutura
    cfg: dict
    medicoes: dict = field(default_factory=dict)


def faixas(paginas) -> str:
    """[3,4,5,9] -> '3–5, 9'"""
    ps = sorted(set(paginas))
    if not ps:
        return ""
    partes, ini, ant = [], ps[0], ps[0]
    for p in ps[1:] + [None]:
        if p is not None and p == ant + 1:
            ant = p
            continue
        partes.append(f"{ini}" if ini == ant else f"{ini}–{ant}")
        if p is not None:
            ini = ant = p
    return ", ".join(partes)


def _f(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


REGISTRO: list = []


def regra(caminho: str, titulo: str):
    """Registra a verificação; `caminho` aponta a seção do YAML (ex.: 'pagina.margens')."""
    def deco(fn):
        REGISTRO.append((caminho, titulo, fn))
        return fn
    return deco


def _cfg(cfg: dict, caminho: str) -> dict:
    no = cfg
    for parte in caminho.split("."):
        no = no.get(parte, {}) if isinstance(no, dict) else {}
    return no or {}


# ---------------------------------------------------------------- documento
@regra("documento.texto_pesquisavel", "Texto pesquisável (não digitalizado)")
def texto_pesquisavel(ctx: Contexto, c: dict):
    sem_texto = [p.indice + 1 for p in ctx.doc.paginas if not p.linhas and p.imagens]
    if sem_texto:
        yield Achado("", c["severidade"], f"Páginas só com imagem, sem texto extraível: {faixas(sem_texto)}. "
                     "O PDF parece digitalizado; gere o PDF a partir do editor de texto.", sem_texto)


@regra("documento.fontes_incorporadas", "Fontes incorporadas ao PDF")
def fontes_incorporadas(ctx: Contexto, c: dict):
    faltando = defaultdict(set)
    for p in ctx.doc.pdf:
        for xref, ext, tipo, nome, *_ in p.get_fonts(full=True):
            if ext == "n/a" and tipo != "Type3":
                faltando[nome.split("+")[-1]].add(p.number + 1)
    for nome, pags in faltando.items():
        yield Achado("", c["severidade"], f"Fonte '{nome}' não incorporada (páginas {faixas(pags)}).", sorted(pags))


@regra("documento.pdfa", "PDF/A")
def pdfa(ctx: Contexto, c: dict):
    try:
        with pikepdf.open(ctx.doc.caminho) as pdf:
            with pdf.open_metadata() as meta:
                parte = meta.get("pdfaid:part")
                conf = meta.get("pdfaid:conformance")
    except Exception as exc:  # noqa: BLE001
        yield Achado("", c["severidade"], f"Não foi possível ler os metadados XMP ({exc}).")
        return
    ctx.medicoes["pdfa"] = f"PDF/A-{parte}{(conf or '').lower()}" if parte else "não"
    if not parte:
        yield Achado("", c["severidade"], "O arquivo não declara conformidade PDF/A (metadado pdfaid:part ausente). "
                     "Obs.: esta verificação lê a declaração; a validação completa exige o veraPDF.")


# ---------------------------------------------------------------- página
@regra("pagina.formato", "Formato A4")
def formato(ctx: Contexto, c: dict):
    tol = c["tolerancia_mm"]
    erradas = defaultdict(list)
    for p in ctx.doc.paginas:
        w, h = mm(p.largura), mm(p.altura)
        if abs(w - c["largura_mm"]) > tol or abs(h - c["altura_mm"]) > tol:
            erradas[(round(w), round(h))].append(p.indice + 1)
    for (w, h), pags in erradas.items():
        yield Achado("", c["severidade"], f"Páginas {faixas(pags)} com {w} × {h} mm (esperado 210 × 297 mm).", pags)


@regra("pagina.margens", "Margens (3 cm sup./esq., 2 cm inf./dir.)")
def margens(ctx: Contexto, c: dict):
    tol = c["tolerancia_mm"]
    lados = {"esquerda": c["esquerda_mm"], "superior": c["superior_mm"],
             "direita": c["direita_mm"], "inferior": c["inferior_mm"]}
    por_problema = defaultdict(list)          # (lado, mm arredondado) -> [(pag, caixas)]
    minimos = {k: 999.0 for k in lados}
    for p in ctx.doc.paginas:
        elementos = [l.bbox for l in p.conteudo] + p.imagens + p.desenhos
        if not c.get("ignorar_cabecalho", True) and p.linha_numero is not None:
            elementos.append(p.linha_numero.bbox)
        viol = defaultdict(list)
        for b in elementos:
            x0, y0, x1, y1 = b
            if x1 - x0 < 0.5 and y1 - y0 < 0.5:
                continue
            dist = {"esquerda": mm(x0), "superior": mm(y0),
                    "direita": mm(p.largura - x1), "inferior": mm(p.altura - y1)}
            for lado, d in dist.items():
                minimos[lado] = min(minimos[lado], d)
                if d < lados[lado] - tol:
                    viol[lado].append((d, b))
        for lado, itens in viol.items():
            menor = min(d for d, _ in itens)
            por_problema[lado].append((p.indice, menor, [b for _, b in itens]))
    ctx.medicoes["margens_minimas_mm"] = {k: round(v, 1) for k, v in minimos.items() if v < 999}
    for lado, casos in por_problema.items():
        pags = [i + 1 for i, _, _ in casos]
        pior = min(d for _, d, _ in casos)
        caixas = [(i, b) for i, _, bs in casos for b in bs[:5]]
        yield Achado("", c["severidade"],
                     f"Margem {lado} menor que {lados[lado]} mm em {len(pags)} página(s): {faixas(pags)} "
                     f"(menor distância: {_f(pior)} mm).", pags, caixas)


# ---------------------------------------------------------------- fonte
def _familia(nome: str, permitidas: dict, ignorar: list) -> str | None:
    chave = re.sub(r"[\s_-]", "", nome).lower()
    for ig in ignorar:
        if chave.startswith(re.sub(r"[\s_-]", "", ig).lower()):
            return None
    for familia, nomes in permitidas.items():
        for n in nomes:
            if chave.startswith(re.sub(r"[\s_-]", "", n).lower()):
                return familia
    return "outra:" + re.split(r"[-,]", nome)[0]


@regra("fonte.familia", "Fonte Arial ou Times, uma só família")
def familia(ctx: Contexto, c: dict):
    cont = Counter()
    exemplos = defaultdict(set)
    for p in ctx.doc.paginas:
        for l in p.linhas:
            for t in l.trechos:
                f = _familia(t.fonte, c["permitidas"], c.get("ignorar", []))
                if f:
                    cont[f] += len(t.texto.strip())
                    exemplos[f].add(p.indice + 1)
    if not cont:
        return
    total = sum(cont.values())
    principal, qtd = cont.most_common(1)[0]
    ctx.medicoes["familia_principal"] = principal
    if principal.startswith("outra:"):
        yield Achado("", c["severidade"], f"A fonte principal do texto é '{principal[6:]}', que não é Arial nem Times "
                     f"({100 * qtd / total:.0f}% dos caracteres).", sorted(exemplos[principal])[:50])
    for f, n in cont.items():
        if f == principal:
            continue
        pct = 100 * n / total
        if pct > c.get("max_outras_familias_pct", 5):
            nome = f[6:] if f.startswith("outra:") else f.capitalize()
            yield Achado("", c["severidade"], f"Mistura de famílias: {pct:.0f}% do texto em '{nome}' "
                         f"(páginas {faixas(exemplos[f])}). Use uma única fonte em todo o trabalho.",
                         sorted(exemplos[f]))


@regra("fonte.tamanho_corpo", "Tamanho 12 no texto")
def tamanho_corpo(ctx: Contexto, c: dict):
    corpo = ctx.est.tamanho_corpo
    ctx.medicoes["tamanho_corpo_pt"] = corpo
    if abs(corpo - c["pt"]) > c["tolerancia_pt"]:
        dica = ""
        if 10.6 <= corpo <= 10.85:
            dica = " (Valor típico da Helvetica carregada com 'scaled=0.9' no LaTeX.)"
        yield Achado("", c["severidade"], f"O texto principal está em {_f(corpo)} pt (esperado {c['pt']} pt).{dica}",
                     [ctx.est.inicio_textual + 1] if ctx.est.inicio_textual is not None else [])


@regra("fonte.tamanho_reduzido", "Tamanho 10 em citações, notas, legendas e fontes")
def tamanho_reduzido(ctx: Contexto, c: dict):
    corpo = ctx.est.tamanho_corpo
    limite = c["pt"] - c["tolerancia_pt"]
    total, abaixo = 0, Counter()
    paginas = defaultdict(set)
    for i in range(len(ctx.doc.paginas)):
        if ctx.est.inicio_textual is not None and i < ctx.est.inicio_textual:
            continue  # capa e pré-textuais têm tamanhos próprios
        for l in ctx.doc.paginas[i].conteudo:
            for t in l.trechos:
                n = len(t.texto.strip())
                total += n
                if t.tamanho < limite and t.tamanho < corpo - 0.5 and not re.fullmatch(r"[\d*†‡§]+", t.texto.strip()):
                    abaixo[round(t.tamanho, 1)] += n
                    paginas[round(t.tamanho, 1)].add(i + 1)
    if not total:
        return
    pct = 100 * sum(abaixo.values()) / total
    ctx.medicoes["texto_abaixo_de_10pt_pct"] = round(pct, 1)
    if pct > c.get("max_abaixo_pct", 2):
        dist = ", ".join(f"{_f(t)} pt em {faixas(paginas[t])}" for t, _ in abaixo.most_common(4))
        todas = sorted(set().union(*paginas.values()))
        yield Achado("", c["severidade"], f"{pct:.0f}% do texto está abaixo de {_f(limite)} pt ({dist}). "
                     f"O tamanho reduzido previsto pela ABNT é {c['pt']} pt.", todas)


# ---------------------------------------------------------------- espaçamento e parágrafo
def _linhas_corpo(ctx: Contexto):
    """Pares de linhas consecutivas de corpo de texto na parte textual."""
    corpo, esq = ctx.est.tamanho_corpo, ctx.est.margem_texto_esq
    for i in ctx.est.parte_textual(ctx.doc):
        ls = [l for l in ctx.doc.paginas[i].conteudo
              if abs(l.tamanho - corpo) <= 0.3 and l.x0 - esq < pt(40) and l.x0 >= esq - 2]
        for a, b in zip(ls, ls[1:]):
            yield i, a, b


@regra("espacamento.entrelinha", "Entrelinha 1,5")
def entrelinha(ctx: Contexto, c: dict):
    corpo = ctx.est.tamanho_corpo
    razoes, fora = [], defaultdict(int)
    for i, a, b in _linhas_corpo(ctx):
        d = b.base - a.base
        if d <= 0 or d > 2.3 * corpo:        # quebra de parágrafo, título, figura...
            continue
        r = d / corpo
        razoes.append(r)
        if not c["razao_min"] <= r <= c["razao_max"]:
            fora[i + 1] += 1
    if len(razoes) < 10:
        return
    med = statistics.median(razoes)
    ok = 100 * sum(c["razao_min"] <= r <= c["razao_max"] for r in razoes) / len(razoes)
    ctx.medicoes["entrelinha_mediana"] = round(med, 2)
    ctx.medicoes["entrelinha_conforme_pct"] = round(ok, 0)
    if ok < c["min_pct_conforme"]:
        tipo = "simples" if med < 1.3 else ("dupla" if med > 2.0 else "diferente de 1,5")
        piores = [p for p, _ in sorted(fora.items(), key=lambda x: -x[1])]
        yield Achado("", c["severidade"], f"Entrelinha {tipo}: mediana de {med:.2f} × o tamanho da fonte "
                     f"(esperado entre {c['razao_min']} e {c['razao_max']}); só {ok:.0f}% das linhas conformes.",
                     sorted(piores)[:60])


@regra("paragrafo.recuo", "Recuo da primeira linha do parágrafo")
def recuo(ctx: Contexto, c: dict):
    esq = ctx.est.margem_texto_esq
    amostras = Counter()
    for i, a, b in _linhas_corpo(ctx):
        rec = mm(a.x0 - esq)
        # primeira linha recuada seguida de linha na margem = início de parágrafo
        if 3 <= rec <= 40 and abs(b.x0 - esq) < 2 and not re.match(r"^[\d•\-–]", a.texto):
            amostras[round(rec * 2) / 2] += 1
    if not amostras:
        n_linhas = sum(1 for _ in _linhas_corpo(ctx))
        if n_linhas > 30:
            ctx.medicoes["recuo_paragrafo_mm"] = 0
            yield Achado("", c["severidade"], f"Parágrafos sem recuo na primeira linha (esperado {_f(c['mm'])} mm).",
                         [ctx.est.inicio_textual + 1] if ctx.est.inicio_textual is not None else [])
        return
    moda = amostras.most_common(1)[0][0]
    ctx.medicoes["recuo_paragrafo_mm"] = moda
    if abs(moda - c["mm"]) > c["tolerancia_mm"]:
        yield Achado("", c["severidade"], f"Recuo de parágrafo de {_f(moda)} mm (esperado {_f(c['mm'])} mm).",
                     [ctx.est.inicio_textual + 1] if ctx.est.inicio_textual is not None else [])


# ---------------------------------------------------------------- paginação
@regra("paginacao", "Numeração das páginas")
def paginacao(ctx: Contexto, c: dict):
    est, doc = ctx.est, ctx.doc
    if est.inicio_textual is None:
        yield Achado("", "aviso", "Início da parte textual (1 INTRODUÇÃO) não encontrado; numeração não verificada.")
        return
    off = c.get("paginas_nao_contadas_no_inicio", 1)
    pre_com_numero = [p.indice + 1 for p in doc.paginas[:est.inicio_textual] if p.linha_numero is not None]
    if c.get("pretextuais_sem_numero", True) and pre_com_numero:
        yield Achado("", c["severidade"], f"Páginas pré-textuais não devem exibir número: {faixas(pre_com_numero)}.",
                     pre_com_numero, [(n - 1, doc.paginas[n - 1].linha_numero.bbox) for n in pre_com_numero])
    titulos = {t.pagina for t in est.apendices}
    sem, sem_titulo, errado, posicao = [], [], [], []
    pos = c.get("posicao", {})
    for p in doc.paginas[est.inicio_textual:]:
        esperado = p.indice + 1 - off
        if p.linha_numero is None:
            (sem_titulo if p.indice in titulos and len(p.conteudo) <= 3 else sem).append(p.indice + 1)
            continue
        if p.numero is not None and p.numero != esperado:
            errado.append((p.indice + 1, p.numero, esperado))
        x0, y0, x1, y1 = p.linha_numero.bbox
        dd, dt = mm(p.largura - x1), mm(y0)
        tol = pos.get("tolerancia_mm", 6)
        if abs(dd - pos.get("distancia_direita_mm", 20)) > tol or abs(dt - pos.get("distancia_topo_mm", 20)) > tol:
            posicao.append((p.indice + 1, dd, dt))
    if sem:
        yield Achado("", c["severidade"], f"Páginas da parte textual/pós-textual sem número: {faixas(sem)}.", sem)
    if sem_titulo:
        yield Achado("", c.get("paginas_de_titulo_sem_numero", "aviso"),
                     f"Páginas de título de apêndice/anexo sem número: {faixas(sem_titulo)} "
                     "(todas as folhas a partir da parte textual devem ser numeradas).", sem_titulo)
    if errado:
        ex = "; ".join(f"p. {f} mostra {n}, esperado {e}" for f, n, e in errado[:5])
        yield Achado("", c["severidade"], f"Numeração fora da sequência em {len(errado)} página(s): {ex}. "
                     f"(Contagem: todas as folhas desde a folha de rosto; {off} folha(s) inicial(is) não contada(s).)",
                     [f for f, _, _ in errado], [(f - 1, doc.paginas[f - 1].linha_numero.bbox) for f, _, _ in errado])
    if posicao:
        f, dd, dt = posicao[0]
        yield Achado("", c["severidade"], f"Número de página fora do canto superior direito em {faixas([p for p, _, _ in posicao])} "
                     f"(ex.: p. {f} a {_f(dd)} mm da borda direita e {_f(dt)} mm do topo; esperado ≈ 20 mm e 20 mm).",
                     [p for p, _, _ in posicao], [(p - 1, doc.paginas[p - 1].linha_numero.bbox) for p, _, _ in posicao])


# ---------------------------------------------------------------- estrutura
@regra("estrutura.elementos_obrigatorios", "Elementos obrigatórios e sua ordem")
def elementos(ctx: Contexto, c: dict):
    rotulos = {normalizar(r): r for r in c["ordem"]}
    mapa = {normalizar(k): v for k, v in ctx.est.elementos.items()}
    ctx.medicoes["elementos"] = {k: v + 1 for k, v in sorted(ctx.est.elementos.items(), key=lambda x: x[1])}
    presentes = []
    for chave, rotulo in rotulos.items():
        if chave not in mapa:
            yield Achado("", c["severidade"], f"Elemento obrigatório não encontrado: {rotulo} "
                         "(procurado como título no topo de uma página).")
        else:
            presentes.append((mapa[chave], rotulo))
    ordem_doc = [r for _, r in sorted(presentes)]
    ordem_esp = [r for r in c["ordem"] if r in ordem_doc]
    if ordem_doc != ordem_esp:
        yield Achado("", c["severidade"], f"Ordem dos elementos: {' → '.join(ordem_doc)} "
                     f"(esperado {' → '.join(ordem_esp)}).", sorted(p + 1 for p, _ in presentes))


@regra("estrutura.secao_primaria_nova_pagina", "Seções primárias iniciam em página nova")
def secao_nova_pagina(ctx: Contexto, c: dict):
    ruins = [t for t in ctx.est.primarios if ctx.doc.paginas[t.pagina].primeira_linha is not t.linha]
    ctx.medicoes["secoes_primarias"] = [f"{t.texto} (p. {t.pagina + 1})" for t in ctx.est.primarios]
    if ruins:
        yield Achado("", c["severidade"], "Seções primárias que não começam em página nova: "
                     + "; ".join(f"'{t.texto[:50]}' (p. {t.pagina + 1})" for t in ruins) + ".",
                     [t.pagina + 1 for t in ruins], [(t.pagina, t.linha.bbox) for t in ruins])


@regra("estrutura.titulos_primarios", "Títulos primários em caixa alta e negrito")
def titulos_primarios(ctx: Contexto, c: dict):
    for t in ctx.est.primarios:
        titulo = re.sub(r"^\d+\s+", "", t.texto)
        falta = []
        if c.get("caixa_alta") and not _caixa_alta(titulo):
            falta.append("caixa alta")
        if c.get("negrito") and not t.linha.negrito:
            falta.append("negrito")
        if falta:
            yield Achado("", c["severidade"], f"Título '{t.texto[:60]}' sem {' e '.join(falta)}.",
                         [t.pagina + 1], [(t.pagina, t.linha.bbox)])


# ---------------------------------------------------------------- resumo
def _texto_elemento(ctx: Contexto, rotulo: str) -> tuple[int | None, list]:
    i = ctx.est.elementos.get(rotulo)
    if i is None:
        return None, []
    return i, [l.texto for l in ctx.doc.paginas[i].conteudo[1:]]


def _analisar_resumo(linhas: list, marcador: str):
    texto = " ".join(linhas)
    m = re.search(marcador + r"\s*:?\s*(.*)$", texto, re.I)
    corpo = texto[:m.start()] if m else texto
    chaves = [k.strip(" .") for k in re.split(r"[;]", m.group(1))] if m else []
    chaves = [k for k in chaves if k]
    if m and len(chaves) == 1 and "," in m.group(1):          # separadas por vírgula (fora da NBR 6028)
        chaves = [k.strip(" .") for k in m.group(1).split(",") if k.strip(" .")]
    return len(re.findall(r"\w+", corpo)), chaves, bool(m)


@regra("resumo", "Resumo/Abstract: extensão e palavras-chave")
def resumo(ctx: Contexto, c: dict):
    for rotulo, marcador, nome in (("resumo", r"Palavras[- ]chave", "Palavras-chave"),
                                   ("abstract", r"Key[- ]?words", "Keywords")):
        i, linhas = _texto_elemento(ctx, rotulo)
        if i is None:
            continue
        palavras, chaves, tem = _analisar_resumo(linhas, marcador)
        ctx.medicoes[f"{rotulo}_palavras"] = palavras
        ctx.medicoes[f"{rotulo}_palavras_chave"] = len(chaves)
        if not c["palavras_min"] <= palavras <= c["palavras_max"]:
            yield Achado("", c["severidade"], f"{rotulo.capitalize()} com {palavras} palavras "
                         f"(esperado de {c['palavras_min']} a {c['palavras_max']}).", [i + 1])
        if not tem:
            yield Achado("", c["severidade"], f"{rotulo.capitalize()} sem a linha '{nome}:'.", [i + 1])
        elif not c["palavras_chave_min"] <= len(chaves) <= c["palavras_chave_max"]:
            yield Achado("", c["severidade"], f"{nome}: {len(chaves)} termo(s) "
                         f"(esperado de {c['palavras_chave_min']} a {c['palavras_chave_max']}, separados por ponto e vírgula).",
                         [i + 1])


# ---------------------------------------------------------------- ilustrações e tabelas
@regra("ilustracoes.fonte_obrigatoria", "Ilustrações e tabelas com indicação de fonte")
def fonte_obrigatoria(ctx: Contexto, c: dict):
    tipos = "|".join(re.escape(t) for t in c["tipos"])
    re_leg = re.compile(rf"^({tipos})\s+(\d+)\s*[–—-]", re.I)
    ini = ctx.est.inicio_textual or 0
    legendas = []                                   # (pag, idx_linha, tipo, num, linha)
    for p in ctx.doc.paginas[ini:]:
        for j, l in enumerate(p.conteudo):
            m = re_leg.match(l.texto)
            if m and not re.search(r"\.{4,}", l.texto):      # ignora entradas de listas
                legendas.append((p.indice, j, m.group(1).capitalize(), int(m.group(2)), l))
    ctx.medicoes["legendas"] = len(legendas)
    sem_fonte = []
    for k, (pi, j, tipo, num, l) in enumerate(legendas):
        prox = legendas[k + 1] if k + 1 < len(legendas) else None
        seguintes = ctx.doc.paginas[pi].conteudo[j + 1:]
        if prox and prox[0] == pi:
            seguintes = ctx.doc.paginas[pi].conteudo[j + 1:prox[1]]
        elif pi + 1 < len(ctx.doc.paginas):          # tabela que continua na página seguinte
            seguintes = seguintes + ctx.doc.paginas[pi + 1].conteudo[:40]
        if not any(re.match(r"^(Fonte|Source)\s*:", s.texto, re.I) for s in seguintes):
            sem_fonte.append((pi, tipo, num, l))
    if sem_fonte:
        yield Achado("", c["severidade"], "Sem 'Fonte:' após a legenda: "
                     + "; ".join(f"{t} {n} (p. {pi + 1})" for pi, t, n, _ in sem_fonte) + ".",
                     [pi + 1 for pi, *_ in sem_fonte], [(pi, l.bbox) for pi, _, _, l in sem_fonte])
    ctx._legendas = legendas  # reaproveitado pela numeração


@regra("ilustracoes.numeracao_sequencial", "Numeração sequencial de ilustrações e tabelas")
def numeracao_sequencial(ctx: Contexto, c: dict):
    legendas = getattr(ctx, "_legendas", [])
    vistos = defaultdict(list)
    for pi, _, tipo, num, l in legendas:
        if num not in [n for n, _ in vistos[tipo]]:
            vistos[tipo].append((num, pi))
    for tipo, nums in vistos.items():
        seq = [n for n, _ in nums]
        if seq != list(range(1, len(seq) + 1)):
            yield Achado("", c["severidade"], f"{tipo}s numeradas fora de sequência: {seq}.",
                         sorted({pi + 1 for _, pi in nums}))


# ---------------------------------------------------------------- sumário
RE_ENTRADA = re.compile(r"^(?P<titulo>.*?\S)[\s.·…_]*?(?P<pag>\d{1,4})$")


@regra("sumario.paginas_conferem", "Páginas indicadas no sumário")
def sumario(ctx: Contexto, c: dict):
    est, doc = ctx.est, ctx.doc
    ini = est.elementos.get("sumário")
    if ini is None or est.inicio_textual is None:
        return
    off = _cfg(ctx.cfg, "paginacao").get("paginas_nao_contadas_no_inicio", 1)
    entradas, pendente = [], ""
    for p in doc.paginas[ini:est.inicio_textual]:
        for l in p.conteudo:
            t = l.texto
            if normalizar(t) == "SUMARIO":
                continue
            m = RE_ENTRADA.match(t)
            if m and re.search(r"(\.{3,}|\s{2,}|…)", t + "  ") or (m and pendente):
                entradas.append(((pendente + " " + m.group("titulo")).strip(" ."), int(m.group("pag")), p.indice, l))
                pendente = ""
            elif m is None:
                pendente = (pendente + " " + t).strip()
    ctx.medicoes["entradas_sumario"] = len(entradas)

    # linhas de todas as páginas, normalizadas, para localizar os títulos
    textos = {p.indice: [normalizar(l.texto) for l in p.conteudo] for p in doc.paginas[est.inicio_textual:]}
    erradas, nao_achadas = [], []
    for titulo, pag, ip, l in entradas:
        alvo = normalizar(titulo)[:40]
        if len(alvo) < 3:
            continue
        idx = pag - 1 + off
        achou_em = [i for i, ls in textos.items() if any(x.startswith(alvo) or (len(x) > 8 and alvo.startswith(x)) for x in ls)]
        if idx in achou_em:
            continue
        if achou_em:
            real = achou_em[0] + 1 - off
            erradas.append((titulo, pag, real, ip, l))
        else:
            nao_achadas.append((titulo, ip, l))
    if erradas:
        yield Achado("", c["severidade"], "Sumário aponta página errada: "
                     + "; ".join(f"'{t[:40]}' indica {p}, está na {r}" for t, p, r, *_ in erradas[:8]) + ".",
                     sorted({ip + 1 for *_, ip, _ in erradas}), [(ip, l.bbox) for *_, ip, l in erradas])
    if nao_achadas:
        yield Achado("", "aviso", "Títulos do sumário não localizados no texto (confira se são idênticos): "
                     + "; ".join(f"'{t[:40]}'" for t, *_ in nao_achadas[:8]) + ".",
                     sorted({ip + 1 for _, ip, _ in nao_achadas}), [(ip, l.bbox) for _, ip, l in nao_achadas])


# ---------------------------------------------------------------- referências
@regra("referencias.ordem_alfabetica", "Referências em ordem alfabética")
def referencias(ctx: Contexto, c: dict):
    ini = ctx.est.inicio_referencias
    if ini is None:
        return
    fim = len(ctx.doc.paginas)
    for p in ctx.doc.paginas[ini + 1:]:
        l = p.primeira_linha
        if l and (RE_APENDICE.match(normalizar(l.texto)) or normalizar(l.texto) in ("GLOSSARIO", "INDICE", "APENDICES", "ANEXOS")):
            fim = p.indice
            break
    linhas = [l for p in ctx.doc.paginas[ini:fim] for l in p.conteudo][1:]
    if len(linhas) < 3:
        return
    gaps = [b.base - a.base for a, b in zip(linhas, linhas[1:]) if a.pagina == b.pagina and b.base > a.base]
    passo = statistics.median(gaps) if gaps else 12
    entradas, atual = [], [linhas[0]]
    for a, b in zip(linhas, linhas[1:]):
        nova = re.match(r"^[A-ZÀ-Ý][A-ZÀ-Ý'\- ]{1,}, ", b.texto) and abs(b.x0 - a.x0) < 2   # "SOBRENOME, "
        if b.pagina != a.pagina or b.base - a.base > 1.3 * passo or nova:
            entradas.append(atual)
            atual = []
        atual.append(b)
    entradas.append(atual)
    chaves = [(normalizar(re.split(r"[,.]", e[0].texto)[0]), e[0]) for e in entradas if e and e[0].texto[:1].isalpha()]
    ctx.medicoes["referencias"] = len(chaves)
    fora = [(a, b) for a, b in zip(chaves, chaves[1:]) if b[0] < a[0]]
    if fora:
        yield Achado("", c["severidade"], "Referências fora da ordem alfabética: "
                     + "; ".join(f"'{b[1].texto[:35]}…' vem depois de '{a[1].texto[:35]}…'" for a, b in fora[:5]) + ".",
                     sorted({b[1].pagina + 1 for _, b in fora}), [(b[1].pagina, b[1].bbox) for _, b in fora])


# ---------------------------------------------------------------- execução
def verificar(doc: Documento, est: Estrutura, cfg: dict) -> tuple[list, list, dict]:
    ctx = Contexto(doc, est, cfg)
    achados, executadas = [], []
    # PDF digitalizado: sem texto não há o que medir; só as verificações do arquivo fazem sentido
    sem_texto = sum(1 for p in doc.paginas if not p.linhas)
    digitalizado = sem_texto > len(doc.paginas) / 2
    ctx.medicoes["paginas_sem_texto"] = sem_texto
    for caminho, titulo, fn in REGISTRO:
        c = _cfg(cfg, caminho)
        if not c or not c.get("ativa", True):
            continue
        if digitalizado and not caminho.startswith("documento."):
            continue
        c.setdefault("severidade", "erro")
        encontrados = []
        for a in fn(ctx, c) or []:
            a.regra = caminho
            encontrados.append(a)
        achados.extend(encontrados)
        executadas.append({"regra": caminho, "titulo": titulo, "severidade": c["severidade"],
                           "ok": not any(a.severidade == "erro" for a in encontrados),
                           "achados": len(encontrados)})
    return achados, executadas, ctx.medicoes
