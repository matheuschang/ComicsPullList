"""Servidor de desenvolvimento para a pasta web/.

    python serve.py            # http://localhost:8765
    python serve.py 9000

Existe por um motivo so: no Windows, o http.server do Python le os MIME types
do registro, onde .js costuma estar mapeado como text/plain. O navegador entao
recusa os modulos ES ("Strict MIME type checking") e o app nao carrega. Aqui os
tipos ficam fixos no codigo. Em producao o GitHub Pages ja serve certo.
"""

import functools
import http.server
import pathlib
import sys

WEB = pathlib.Path(__file__).resolve().parent / "web"

TIPOS = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    def guess_type(self, path):
        tipo = TIPOS.get(pathlib.Path(path).suffix.lower())
        return tipo or super().guess_type(path)

    def end_headers(self):
        # Sem cache: reingerir os dados e dar F5 tem que mostrar o resultado novo.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        # Ignora If-Modified-Since. Sem isto, um arquivo que ja esteve no cache
        # com o Content-Type errado volta como 304 e o navegador reaproveita o
        # tipo antigo -- o bug continua mesmo com o servidor ja corrigido.
        del self.headers["If-Modified-Since"]
        return super().send_head()


def main():
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    if not (WEB / "data" / "series.json").exists():
        print("aviso: web/data/series.json nao existe -- rode 'python ingest/from_locg.py' antes")
    handler = functools.partial(Handler, directory=str(WEB))
    print(f"servindo {WEB} em http://localhost:{porta}  (ctrl+c para parar)")
    http.server.ThreadingHTTPServer(("127.0.0.1", porta), handler).serve_forever()


if __name__ == "__main__":
    main()
