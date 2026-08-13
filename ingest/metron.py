"""Cliente da API da Metron (https://metron.cloud/api/).

Autenticacao e HTTP Basic com usuario e senha da conta do site -- nao existe
token. Por isso a credencial sai de variavel de ambiente ou de um .env na raiz
do projeto, nunca do codigo:

    METRON_USER=seu_usuario
    METRON_PASS=sua_senha

Antes de confiar em qualquer coisa, rode o autoteste. Ele gasta poucas
requisicoes e mostra campo por campo o que a API devolveu, para o caso de os
nomes terem mudado desde que este cliente foi escrito:

    python ingest/metron.py --selftest
"""

import argparse
import base64
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://metron.cloud/api"

# A Metron pede no maximo 30 requisicoes por minuto. 2,2s deixa margem.
DELAY_PADRAO = 2.2


def carregar_env():
    """Le o .env da raiz. Variavel de ambiente de verdade tem prioridade."""
    arquivo = RAIZ / ".env"
    if arquivo.exists():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))


class MetronErro(Exception):
    pass


class Metron:
    def __init__(self, usuario=None, senha=None, delay=DELAY_PADRAO, cache=True):
        carregar_env()
        self.usuario = usuario or os.environ.get("METRON_USER")
        self.senha = senha or os.environ.get("METRON_PASS")
        if not self.usuario or not self.senha:
            raise MetronErro(
                "faltam METRON_USER e METRON_PASS.\n"
                f"Crie {RAIZ / '.env'} com:\n"
                "  METRON_USER=seu_usuario\n"
                "  METRON_PASS=sua_senha"
            )
        cru = f"{self.usuario}:{self.senha}".encode()
        self._auth = "Basic " + base64.b64encode(cru).decode()
        self.delay = delay
        self.pasta_cache = (RAIZ / ".cache" / "metron") if cache else None
        self.requisicoes = 0
        self.acertos_cache = 0
        self._ultima = 0.0

    # ------------------------------------------------------------- transporte

    def _caminho_cache(self, url):
        digest = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self.pasta_cache / f"{digest}.json"

    def _esperar(self):
        falta = self.delay - (time.monotonic() - self._ultima)
        if falta > 0:
            time.sleep(falta)
        self._ultima = time.monotonic()

    def get(self, caminho, **params):
        """GET numa rota da API. Devolve o JSON ja decodificado."""
        url = f"{BASE}/{caminho.strip('/')}/"
        if params:
            limpos = {k: v for k, v in params.items() if v not in (None, "")}
            url += "?" + urllib.parse.urlencode(limpos)

        if self.pasta_cache:
            cache = self._caminho_cache(url)
            if cache.exists():
                self.acertos_cache += 1
                return json.loads(cache.read_text(encoding="utf-8"))

        dados = self._buscar(url)

        if self.pasta_cache:
            self.pasta_cache.mkdir(parents=True, exist_ok=True)
            self._caminho_cache(url).write_text(
                json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        return dados

    def _buscar(self, url, tentativa=1):
        self._esperar()
        req = urllib.request.Request(url, headers={
            "Authorization": self._auth,
            "User-Agent": "pulllist/0.1 (uso pessoal)",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                self.requisicoes += 1
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise MetronErro(
                    "401: usuario ou senha recusados pela Metron.\n"
                    "Confira o .env e que a conta esta ativa (confirme o e-mail "
                    "de cadastro -- conta nao confirmada tambem devolve 401)."
                ) from None
            if e.code == 429 and tentativa <= 5:
                # A Metron manda Retry-After quando estoura o limite.
                espera = int(e.headers.get("Retry-After") or 60)
                print(f"  limite atingido, esperando {espera}s", file=sys.stderr)
                time.sleep(espera)
                return self._buscar(url, tentativa + 1)
            if e.code >= 500 and tentativa <= 4:
                espera = 2 ** tentativa
                print(f"  {e.code} do servidor, tentando de novo em {espera}s", file=sys.stderr)
                time.sleep(espera)
                return self._buscar(url, tentativa + 1)
            raise MetronErro(f"{e.code} em {url}: {e.read()[:200].decode('utf-8', 'ignore')}") from None
        except urllib.error.URLError as e:
            if tentativa <= 4:
                espera = 2 ** tentativa
                print(f"  rede falhou ({e.reason}), tentando de novo em {espera}s", file=sys.stderr)
                time.sleep(espera)
                return self._buscar(url, tentativa + 1)
            raise MetronErro(f"rede falhou em {url}: {e.reason}") from None

    def paginar(self, caminho, **params):
        """Percorre todas as paginas de uma listagem, item a item."""
        pagina = 1
        while True:
            dados = self.get(caminho, page=pagina, **params)
            yield from dados.get("results", [])
            if not dados.get("next"):
                return
            pagina += 1

    # ------------------------------------------------------------- conveniencia

    def id_editora(self, nome):
        """Descobre o id de uma editora pelo nome. Nunca chute este numero."""
        for p in self.paginar("publisher", name=nome):
            if p.get("name", "").lower() == nome.lower():
                return p["id"]
        raise MetronErro(f"editora '{nome}' nao encontrada na Metron")


# ------------------------------------------------------------------ autoteste

def _mostrar(rotulo, dado, campos):
    print(f"\n  {rotulo}:")
    for campo in campos:
        valor = dado.get(campo, "<AUSENTE>")
        if isinstance(valor, dict):
            valor = f"{{{', '.join(f'{k}={v!r}' for k, v in list(valor.items())[:4])}}}"
        marca = "[!!]" if valor == "<AUSENTE>" else "[ok]"
        print(f"    {marca} {campo:18} {str(valor)[:80]}")


def selftest(delay):
    """Confere autenticacao, ids de editora e os campos de que dependemos."""
    api = Metron(delay=delay, cache=False)
    print(f"autenticando como {api.usuario!r}...")

    editoras = {}
    for nome in ("DC Comics", "Marvel"):
        try:
            editoras[nome] = api.id_editora(nome)
            print(f"  [ok] {nome} -> id {editoras[nome]}")
        except MetronErro as e:
            print(f"  [!!] {nome}: {e}")

    if not editoras:
        print("\nnenhuma editora encontrada -- confira os nomes com:")
        print("  python ingest/metron.py --editoras")
        return 1

    id_dc = next(iter(editoras.values()))
    pagina = api.get("issue", publisher_id=id_dc, store_date_range_after="2025-01-01",
                     store_date_range_before="2025-12-31")
    print(f"\nedicoes DC com store_date em 2025: {pagina.get('count')}")
    if not pagina.get("results"):
        print("  [!!] listagem vazia -- os nomes dos filtros de data podem ter mudado")
        return 1

    edicao = pagina["results"][0]
    _mostrar("edicao (listagem)", edicao, ["id", "series", "number", "issue", "cover_date", "store_date", "image"])

    detalhe = api.get(f"issue/{edicao['id']}")
    _mostrar("edicao (detalhe)", detalhe, ["id", "number", "store_date", "cover_date", "price", "page", "image", "resource_url"])

    id_serie = (edicao.get("series") or {}).get("id")
    if id_serie:
        serie = api.get(f"series/{id_serie}")
        _mostrar("serie", serie, ["id", "name", "volume", "year_began", "year_end",
                                  "issue_count", "series_type", "publisher", "status"])
        todas = api.get("issue", series_id=id_serie)
        print(f"\n  edicoes desta serie na base: {todas.get('count')}"
              f"  (issue_count da serie: {serie.get('issue_count')})")

    tipos = [t.get("name") for t in api.paginar("series_type")]
    print(f"\n  series_type disponiveis: {tipos}")

    print(f"\n{api.requisicoes} requisicoes. Se todos os campos vieram [ok], "
          f"rode: python ingest/from_metron.py")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="confere credencial e campos")
    ap.add_argument("--editoras", action="store_true", help="lista as editoras e seus ids")
    ap.add_argument("--delay", type=float, default=DELAY_PADRAO)
    args = ap.parse_args()

    try:
        if args.editoras:
            api = Metron(delay=args.delay)
            for p in api.paginar("publisher"):
                print(f"{p['id']:5}  {p['name']}")
            return 0
        if args.selftest:
            return selftest(args.delay)
    except MetronErro as e:
        print(f"\nerro: {e}", file=sys.stderr)
        return 1

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
