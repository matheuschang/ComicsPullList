"""Monta o catalogo a partir da API da Metron.

Substitui o from_csv.py e resolve as duas limitacoes da fase 1: o catalogo passa
a ter todos os titulos DC e Marvel de 2025 para ca, e cada titulo vem com TODAS
as suas edicoes -- inclusive as anteriores a 2025 -- para que dê para marcar a
serie inteira como lida.

    python ingest/metron.py --selftest      # confira a credencial antes
    python ingest/from_metron.py            # 2025 ate hoje + solicitacoes
    python ingest/from_metron.py --desde 2024-01-01

Respostas ficam em .cache/metron/, entao rodar de novo depois de uma queda
custa quase nada. Use --sem-cache para reconferir precos e datas do zero.
"""

import argparse
import csv
import datetime as dt
import glob
import pathlib
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import (JANELA_EM_PUBLICACAO, chave_serie, escrever_catalogo,
                    motivo_descarte, ordem_numero, parse_titulo)
from metron import Metron, MetronErro

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# Nome na Metron -> codigo curto usado no site.
EDITORAS_METRON = {"DC Comics": "dc", "Marvel": "marvel"}

# Coletaneas nao sao edicao para acompanhar: quem segue "Batman" quer a #167,
# nao o encadernado que reune 1 a 6. Filtra por exclusao, e nao por lista de
# permitidos, para que um series_type novo entre no catalogo em vez de sumir
# calado. Confira os nomes reais com: python ingest/metron.py --selftest
TIPOS_EXCLUIDOS = {
    "trade paperback", "hard cover", "hardcover", "omnibus",
    "graphic novel", "digital chapter",
}

# Quanto do futuro entra. As solicitacoes saem com ~3 meses de antecedencia;
# 6 meses cobre com folga e é o que alimenta a aba Novidades.
HORIZONTE = dt.timedelta(days=180)


def series_no_periodo(api, id_editora, nome_editora, desde, ate, contagem):
    """Ids das series que tiveram alguma edicao a venda no periodo."""
    ids = set()
    for edicao in api.paginar("issue", publisher_id=id_editora,
                              store_date_range_after=desde.isoformat(),
                              store_date_range_before=ate.isoformat()):
        contagem[nome_editora] += 1
        serie = edicao.get("series") or {}
        if serie.get("id"):
            ids.add(serie["id"])
    return ids


def edicoes_da_serie(api, id_serie):
    """TODAS as edicoes da serie, sem recorte de data. É o ponto da fase 2."""
    edicoes = []
    for e in api.paginar("issue", series_id=id_serie):
        # store_date e a data de venda na loja; cover_date e a da capa, que a
        # industria adianta em ~2 meses. Sem store_date, cai para cover_date.
        data = e.get("store_date") or e.get("cover_date")
        if not data:
            continue
        edicoes.append({
            "numero": str(e.get("number", "")).strip(),
            "titulo": e.get("issue") or "",
            "data": data,
            "data_capa": e.get("cover_date") or "",
            "preco": str(e.get("price") or "").strip(),
            "capa": e.get("image") or "",
            "link": e.get("resource_url") or "",
            "read_link": "",
        })
    edicoes.sort(key=lambda e: (ordem_numero(e["numero"]), e["data"]))
    return edicoes


def indice_read_links(origem):
    """(chave_da_serie, numero) -> read_link, vindo dos CSVs do comics_releases.

    A Metron nao tem esse dado -- ele so existe no getcomics. Sem isto os links
    [LER] da fase 1 sumiriam da interface.
    """
    indice = {}
    for arquivo in sorted(glob.glob(str(pathlib.Path(origem) / "*_mergecomics.csv"))):
        with open(arquivo, encoding="utf-8") as fh:
            for linha in csv.DictReader(fh):
                link = (linha.get("read_link") or "").strip()
                if not link:
                    continue
                partes = parse_titulo(linha.get("title_x"))
                if not partes or motivo_descarte(partes["serie"], partes["resto"]):
                    continue
                editora = {"DC Comics": "dc", "Marvel Comics": "marvel"}.get(
                    (linha.get("publisher") or "").strip())
                if editora:
                    indice[(chave_serie(editora, partes["serie"]), partes["numero"])] = link
    return indice


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desde", default="2025-01-01", help="inicio da janela (AAAA-MM-DD)")
    ap.add_argument("--saida", default=str(RAIZ / "web" / "data"))
    ap.add_argument("--read-links", default="../comics_releases/comics",
                    help="pasta dos CSVs para reaproveitar os links [LER]")
    ap.add_argument("--delay", type=float, default=2.2)
    ap.add_argument("--sem-cache", action="store_true")
    ap.add_argument("--limite", type=int, help="processa so N series (teste rapido)")
    args = ap.parse_args()

    desde = dt.date.fromisoformat(args.desde)
    hoje = dt.date.today()
    ate = hoje + HORIZONTE

    api = Metron(delay=args.delay, cache=not args.sem_cache)

    print(f"janela: {desde} a {ate} (hoje + {HORIZONTE.days} dias de solicitacoes)")
    editoras = {}
    for nome, codigo in EDITORAS_METRON.items():
        editoras[codigo] = (nome, api.id_editora(nome))
        print(f"  {nome} -> id {editoras[codigo][1]}")

    print("\nprocurando series com edicao no periodo...")
    contagem = Counter()
    alvos = {}  # id da serie -> codigo da editora
    for codigo, (nome, id_editora) in editoras.items():
        ids = series_no_periodo(api, id_editora, nome, desde, ate, contagem)
        for i in ids:
            alvos[i] = codigo
        print(f"  {nome}: {contagem[nome]} edicoes, {len(ids)} series")

    if args.limite:
        alvos = dict(list(alvos.items())[:args.limite])
        print(f"  --limite: processando so {len(alvos)}")

    links = {}
    if args.read_links:
        pasta = (RAIZ / args.read_links).resolve()
        if pasta.exists():
            links = indice_read_links(pasta)
            print(f"\n{len(links)} links de leitura vindos dos CSVs")

    print(f"\nbuscando as edicoes completas de {len(alvos)} series...")
    catalogo, por_serie = [], {}
    descartes = Counter()
    achou_link = 0
    # slug da fase 1 -> id da Metron. Sem isto, quem ja marcou edicoes com a
    # fonte antiga perde tudo na troca: os ids mudam de forma e o que esta
    # salvo no navegador passa a apontar para series que nao existem mais.
    apelidos = {}

    for n, (id_serie, codigo) in enumerate(sorted(alvos.items()), 1):
        serie = api.get(f"series/{id_serie}")
        tipo = (serie.get("series_type") or {})
        nome_tipo = tipo.get("name", "") if isinstance(tipo, dict) else str(tipo)
        if nome_tipo.lower() in TIPOS_EXCLUIDOS:
            descartes[nome_tipo or "sem-tipo"] += 1
            continue

        edicoes = edicoes_da_serie(api, id_serie)
        if not edicoes:
            descartes["sem-edicoes"] += 1
            continue

        chave = chave_serie(codigo, serie.get("name", ""))
        for e in edicoes:
            link = links.get((chave, e["numero"]))
            if link:
                e["read_link"] = link
                achou_link += 1

        publicadas = [e for e in edicoes if e["data"] <= hoje.isoformat()]
        futuras = [e for e in edicoes if e["data"] > hoje.isoformat()]
        ultima = publicadas[-1] if publicadas else edicoes[-1]
        recente = dt.date.fromisoformat(ultima["data"]) >= hoje - JANELA_EM_PUBLICACAO

        identificador = f"metron_{id_serie}"
        # Se duas series compartilham o slug -- relancamento, exatamente o que o
        # slug nao sabia distinguir -- fica a que comecou mais tarde, que e a que
        # o usuario provavelmente estava acompanhando.
        ano = serie.get("year_began") or 0
        if apelidos.get(chave, (None, -1))[1] <= ano:
            apelidos[chave] = (identificador, ano)
        por_serie[identificador] = edicoes
        catalogo.append({
            "id": identificador,
            "nome": serie.get("name", ""),
            "editora": codigo,
            "volume": serie.get("volume"),
            "ano_inicio": serie.get("year_began"),
            "ano_fim": serie.get("year_end"),
            "tipo": nome_tipo,
            "capa": next((e["capa"] for e in reversed(edicoes) if e["capa"]), ""),
            "edicoes_conhecidas": len(edicoes),
            "total_anunciado": serie.get("issue_count"),
            "primeira_edicao": {"numero": edicoes[0]["numero"], "data": edicoes[0]["data"]},
            "ultima_edicao": {"numero": ultima["numero"], "data": ultima["data"]},
            "proxima_edicao": ({"numero": futuras[0]["numero"], "data": futuras[0]["data"]}
                               if futuras else None),
            "status": "em-publicacao" if (recente or futuras) else "sem-noticia",
            "fonte": "metron",
        })

        if n % 25 == 0 or n == len(alvos):
            print(f"  {n}/{len(alvos)}  ({api.requisicoes} req, {api.acertos_cache} do cache)")

    catalogo.sort(key=lambda s: (s["editora"], s["nome"].lower()))

    meta = {
        "gerado_em": dt.datetime.now().replace(microsecond=0).isoformat(),
        "fonte": "metron",
        "referencia": hoje.isoformat(),
        "cobertura": {"de": desde.isoformat(), "ate": ate.isoformat()},
        "total_series": len(catalogo),
        "total_edicoes": sum(len(e) for e in por_serie.values()),
        "em_publicacao": sum(1 for s in catalogo if s["status"] == "em-publicacao"),
        "com_proxima": sum(1 for s in catalogo if s["proxima_edicao"]),
        "descartes": dict(descartes),
        "apelidos": {slug: ident for slug, (ident, _) in apelidos.items()},
    }
    escrever_catalogo(catalogo, por_serie, meta, pathlib.Path(args.saida))

    print(f"\n{meta['total_series']} series, {meta['total_edicoes']} edicoes")
    print(f"em publicacao: {meta['em_publicacao']} | com proxima anunciada: {meta['com_proxima']}")
    print(f"links [LER] aproveitados: {achou_link}")
    print(f"descartes: {dict(descartes)}")
    print(f"{api.requisicoes} requisicoes, {api.acertos_cache} respostas do cache")


if __name__ == "__main__":
    try:
        main()
    except MetronErro as e:
        sys.exit(f"\nerro: {e}")
    except KeyboardInterrupt:
        sys.exit("\ninterrompido -- o cache foi preservado, rode de novo para continuar")
