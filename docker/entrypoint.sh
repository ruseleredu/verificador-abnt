#!/bin/sh
# Modos: "api" (padrão) sobe o serviço web; "verificar <pdf> [opções]" roda a linha de comando.
set -e
case "$1" in
  api)
    shift
    exec uvicorn verificador.api:app --host 0.0.0.0 --port "${PORTA:-8000}" --workers "${WORKERS:-2}" "$@"
    ;;
  verificar)
    shift
    exec python -m verificador.cli "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
