# Verificador de formatação ABNT/UTFPR (PDF)

Recebe o PDF final de um TCC, dissertação ou tese e confere as regras de formatação da UTFPR/ABNT
(NBR 14724, 6024, 6027, 6028). Produz um relatório em **texto**, **JSON** e **HTML**, além de uma **cópia
do PDF com os problemas marcados** em vermelho (erros) e laranja (avisos).

Funciona com PDFs gerados por qualquer editor (LaTeX, Word, LibreOffice, Google Docs): tudo é medido
no próprio PDF.

## Como funciona

```
PDF ──► extração (PyMuPDF) ──► estrutura ──► regras (YAML) ──► relatório JSON/HTML + PDF anotado
         linhas visuais,        resumo,        margens, fonte,
         fontes, tamanhos,      sumário,       entrelinha, recuo,
         posições, imagens      capítulos,     paginação, ...
                                referências
```

1. **Extração** (`verificador/modelo.py`): lê cada trecho de texto com fonte, tamanho, negrito e posição
   exata, junta os trechos da mesma linha-base em "linhas visuais", detecta o número de página, as imagens
   e os desenhos (tabelas, filetes).
2. **Estrutura** (`verificador/estrutura.py`): reconhece os elementos pelo título no topo da página
   (RESUMO, ABSTRACT, SUMÁRIO, REFERÊNCIAS, APÊNDICE…), o início da parte textual (`1 INTRODUÇÃO`) e os
   títulos de seção primária. Mede o tamanho predominante do corpo do texto e a margem esquerda usual.
3. **Regras** (`verificador/regras.py`): cada verificação é uma função registrada com `@regra(...)` e
   parametrizada pelo arquivo `regras/utfpr.yaml`.
4. **Relatório** (`verificador/relatorio.py`): JSON, HTML e PDF anotado.

## Uso com Docker

```bash
docker compose up -d --build          # sobe a API em http://localhost:8000 (formulário de envio)
```

Linha de comando, sem subir o serviço:

```bash
docker build -t verificador-abnt .
docker run --rm -v "$PWD:/dados" verificador-abnt verificar /dados/tcc.pdf \
       --html /dados/relatorio.html --json /dados/relatorio.json --anotado /dados/tcc-anotado.pdf
```

O código de saída é `0` (aprovado) ou `1` (reprovado): dá para usar em CI, por exemplo para verificar o
PDF gerado a cada commit do repositório do TCC.

API:

| Método | Rota | Resposta |
|---|---|---|
| GET | `/` | formulário de envio |
| POST | `/verificar` (campo `arquivo`) | relatório HTML |
| POST | `/api/verificar?anotado=true` | JSON (com o PDF anotado em base64, se pedido) |
| GET | `/api/regras` | regras em uso |
| GET | `/saude` | `{"status": "ok"}` (healthcheck) |

```bash
curl -F "arquivo=@tcc.pdf" http://localhost:8000/api/verificar
```

Variáveis de ambiente: `REGRAS` (arquivo YAML), `VERAPDF_URL` (serviço veraPDF), `VERAPDF_CLI` (executável
do veraPDF, alternativa sem Docker), `TAMANHO_MAX_MB` (padrão 60), `WORKERS`, `PORTA`.
O `docker-compose.yml` monta `./regras` somente leitura: ajuste o YAML e reinicie, sem reconstruir.

## Validação PDF/A com o veraPDF

A biblioteca exige o depósito em PDF/A. Ler a declaração no arquivo (`documento.pdfa`) não basta: um PDF
pode declarar PDF/A e violar a norma (fonte não incorporada, cor sem perfil ICC, transparência...). Por isso
o `docker-compose.yml` sobe um segundo container com o [veraPDF](https://verapdf.org/), o validador de
referência da PDF Association, e a regra `documento.pdfa_verapdf` envia o PDF a ele:

```
verificador ──POST /api/validate/{perfil} (multipart "file")──► verapdf (verapdf/rest, porta 8080, rede interna)
            ◄──────────── relatório JSON (report.jobs[].validationResult[]) ─────────────
```

- Cada regra violada da ISO 19005 vira um achado: `[ISO 19005-1:2005 §6.7.2-1] descrição (n ocorrências).
  Páginas: …`, mais um resumo "não conforme com o PDF/A-xx".
- `perfil: auto` valida contra a parte declarada no arquivo. Para exigir uma parte específica, use `1b`,
  `2b`, `3b`, etc. no YAML.
- Se o veraPDF estiver fora do ar, a regra gera um **aviso** e o resto da verificação segue. Com
  `obrigatorio: true` passa a ser **erro**. O cliente repete a chamada (`tentativas`, `espera_s`) porque
  o serviço Java leva alguns segundos para subir.
- Sem Docker: instale o veraPDF (1.24 ou superior) e defina `VERAPDF_CLI=/caminho/verapdf`. O verificador
  roda `verapdf --format json --flavour {perfil} arquivo.pdf` e interpreta o mesmo JSON.
- Para testar sem o veraPDF: `python tests/fake_verapdf.py 8080` sobe um servidor falso com o mesmo
  protocolo (usado pelos testes automáticos).

Validação direta, sem o verificador (descomente `ports` do serviço `verapdf` no compose):

```bash
curl -F "file=@tcc.pdf" http://localhost:8080/api/validate/auto
```

## Sem Docker

```bash
pip install -r requirements.txt
python -m verificador.cli tcc.pdf --html relatorio.html
python -m pytest -q                   # testes (exigem também: pip install pytest httpx)
```

## O que é verificado

| Regra (YAML) | O que confere |
|---|---|
| `documento.texto_pesquisavel` | PDF com texto (não digitalizado). Se for digitalizado, só as verificações do arquivo rodam |
| `documento.fontes_incorporadas` | todas as fontes embutidas |
| `documento.pdfa` | declaração de PDF/A nos metadados XMP (triagem rápida) |
| `documento.pdfa_verapdf` | **validação PDF/A completa (ISO 19005) com o veraPDF**: cada regra violada da norma vira um achado, com cláusula e páginas |
| `pagina.formato` | A4 (210 × 297 mm) |
| `pagina.margens` | 3 cm superior/esquerda, 2 cm inferior/direita, para texto, imagens e desenhos (o número de página é ignorado) |
| `fonte.familia` | Arial ou Times (e equivalentes métricos), uma só família no texto |
| `fonte.tamanho_corpo` | tamanho predominante do texto da parte textual = 12 pt |
| `fonte.tamanho_reduzido` | texto menor que 10 pt (citações, notas, legendas, fontes) |
| `espacamento.entrelinha` | distância entre linhas-base ÷ tamanho da fonte, na faixa de 1,5 |
| `paragrafo.recuo` | recuo da primeira linha dos parágrafos |
| `paginacao` | pré-textuais sem número; número correto (contando desde a folha de rosto) e no canto superior direito a partir da introdução |
| `estrutura.elementos_obrigatorios` | Resumo, Abstract, Sumário, Introdução e Referências presentes e na ordem |
| `estrutura.secao_primaria_nova_pagina` | cada seção primária (`2 TÍTULO`) começa em página nova |
| `estrutura.titulos_primarios` | títulos primários em caixa alta e negrito |
| `resumo` | 150 a 500 palavras; 3 a 5 palavras-chave separadas por `;` (Resumo e Abstract) |
| `ilustracoes.fonte_obrigatoria` | toda legenda `Figura/Quadro/Tabela… N –` seguida de `Fonte:` |
| `ilustracoes.numeracao_sequencial` | numeração 1, 2, 3… por tipo |
| `sumario.paginas_conferem` | a página indicada no sumário é a página onde o título está |
| `referencias.ordem_alfabetica` | entradas em ordem alfabética |

Cada regra pode ser desligada (`ativa: false`) ou ter a severidade trocada (`erro`, `aviso`, `info`).
Só `erro` reprova o documento. **Confira os valores do YAML com o manual de normalização vigente da
biblioteca**: foram tirados da NBR 14724 e das instruções escritas no próprio modelo LaTeX da UTFPR.

## Como acrescentar uma regra

```python
# verificador/regras.py
@regra("citacoes.recuo_4cm", "Citações longas com recuo de 4 cm")
def citacao_longa(ctx, c):
    for i in ctx.est.parte_textual(ctx.doc):
        for l in ctx.doc.paginas[i].conteudo:
            ...                                   # medir
            yield Achado("", c["severidade"], "mensagem", [i + 1], [(i, l.bbox)])
```

```yaml
# regras/utfpr.yaml
citacoes:
  recuo_4cm: {ativa: true, severidade: aviso, mm: 40}
```

## Resultado com o modelo LaTeX da UTFPR

O PDF do próprio modelo (em `exemplos/modelo-utfpr.pdf`) passa em estrutura, sumário, legendas,
referências, entrelinha e família de fonte, e revela divergências reais do modelo em relação às regras:

- corpo do texto em **10,8 pt**: com a opção Arial, a classe carrega a Helvetica com `scaled=0.9`;
- **margem inferior de 12,5 mm** (`bottom = 12.5mm` no `\newgeometry` da classe) e margem esquerda de
  25,6 mm na capa e folha de rosto (`adjustwidth{-.5cm}`);
- recuo de parágrafo de **12,5 mm**, enquanto o texto do modelo pede 1,5 cm;
- legendas em 8,1 pt e notas em 7,2 pt (abaixo dos 10 pt);
- páginas de título dos apêndices/anexos sem número.

O verificador também encontrou um erro que as listas de siglas/símbolos em `longtable` introduziram:
as tabelas saíam numeradas como 3 e 4. Isso já foi corrigido na `utfprabntex.cls`.

## Limitações

- **Heurísticas sobre o PDF.** O PDF não diz o que é título, citação ou legenda: isso é inferido por
  posição, tamanho e padrões de texto. Documentos muito fora do padrão podem gerar falsos positivos, por
  isso o relatório mostra as medições e o PDF anotado, para conferência humana.
- **PDF/A:** a validação é a do veraPDF; o verificador só traduz o relatório. As regras da norma aparecem
  em inglês, como o veraPDF as descreve.
- **Não avalia conteúdo:** a formatação de cada referência (NBR 6023), as citações (NBR 10520) e a
  redação não são verificadas. Esses pontos pedem regras por tipo de referência ou um modelo de
  linguagem, e revisão humana.
- **Entrelinha "1,5":** o LaTeX usa 1,5 × o tamanho da fonte e o Word, cerca de 1,72×. A faixa padrão
  (1,40–1,90) aceita os dois.
