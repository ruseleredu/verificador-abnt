# Verificador de formatação ABNT/UTFPR
# Build:  docker build -t verificador-abnt .
# API:    docker run --rm -p 8000:8000 verificador-abnt            -> http://localhost:8000
# CLI:    docker run --rm -v "$PWD:/dados" verificador-abnt verificar /dados/tcc.pdf --html /dados/relatorio.html
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    REGRAS=/app/regras/utfpr.yaml

WORKDIR /app
COPY requirements.txt .
# PyMuPDF e pikepdf trazem wheels prontas (MuPDF/qpdf embutidos): nenhuma dependência de sistema
RUN pip install -r requirements.txt

COPY verificador/ verificador/
COPY regras/ regras/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && useradd --create-home --uid 1000 verificador
USER verificador

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/saude')" || exit 1

ENTRYPOINT ["entrypoint.sh"]
CMD ["api"]
