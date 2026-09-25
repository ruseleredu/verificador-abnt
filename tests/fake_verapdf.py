"""Servidor falso do veraPDF-rest para testes (mesma rota e mesmo formato de resposta).

POST /api/validate/{perfil} com multipart "file". Responde com uma fixture de tests/fixtures:
nome do arquivo contendo "ruim" -> não conforme; "quebrado" -> exceção; demais -> conforme.
Também pode ser usado à mão:  python tests/fake_verapdf.py 8080
"""
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


class Handler(BaseHTTPRequestHandler):
    recebidos: list = []

    def do_POST(self):  # noqa: N802
        m = re.fullmatch(r"/api/validate/([a-z0-9]+)", self.path)
        corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not m or b'name="file"' not in corpo or b"%PDF" not in corpo:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"requisicao invalida")
            return
        nome = re.search(rb'filename="([^"]+)"', corpo).group(1).decode()
        Handler.recebidos.append((m.group(1), nome, self.headers.get("Accept")))
        fixture = ("verapdf_nao_conforme.json" if "ruim" in nome else
                   "verapdf_excecao.json" if "quebrado" in nome else "verapdf_conforme.json")
        with open(os.path.join(FIX, fixture), "rb") as f:
            dados = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, *a):
        pass


def iniciar(porta: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    s = iniciar(int(sys.argv[1]) if len(sys.argv) > 1 else 8080)
    print(f"veraPDF falso em http://127.0.0.1:{s.server_address[1]}")
    threading.Event().wait()
