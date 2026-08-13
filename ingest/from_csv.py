"""Gera o catalogo a partir dos CSVs semanais do projeto comics_releases.

Fonte de partida da fase 1. Serve para ter dados reais na interface antes de a
chave da Metron existir, e para provar que o schema aguenta dados de verdade.
A cobertura e propositalmente magra -- sao as 8 semanas que foram raspadas --
e o meta.json diz exatamente qual e a janela, para a interface nao fingir que
conhece o ano inteiro.

    python ingest/from_csv.py --origem "../comics_releases/comics"
"""

import argparse
import csv
import datetime as dt
import pathlib
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import (EDITORAS, JANELA_EM_PUBLICACAO, chave_serie, escrever_catalogo,
                    motivo_descarte, ordem_numero, parse_titulo)

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def ler_csvs(origem):
    """Le os *_mergecomics.csv e devolve as linhas cruas."""
    arquivos = sorted(pathlib.Path(origem).glob("*_mergecomics.csv"))
    if not arquivos:
        sys.exit(f"nenhum *_mergecomics.csv em {origem}")
    linhas = []
    for arq in arquivos:
        with open(arq, encoding="utf-8") as fh:
            linhas.extend(csv.DictReader(fh))
    print(f"{len(arquivos)} arquivos, {len(linhas)} linhas")
    return linhas


def montar_edicoes(linhas, descartes):
    """Converte linhas do CSV em edicoes agrupadas por serie."""
    por_serie = defaultdict(dict)
    nomes = defaultdict(Counter)

    for linha in linhas:
        editora = EDITORAS.get((linha.get("publisher") or "").strip())
        if not editora:
            descartes["outra-editora"] += 1
            continue

        partes = parse_titulo(linha.get("title_x"))
        if not partes:
            descartes["titulo-sem-numero"] += 1
            continue

        motivo = motivo_descarte(partes["serie"], partes["resto"])
        if motivo:
            descartes[motivo] += 1
            continue

        try:
            data = dt.date.fromtimestamp(int(linha["date"])).isoformat()
        except (KeyError, TypeError, ValueError):
            descartes["sem-data"] += 1
            continue

        chave = chave_serie(editora, partes["serie"])
        nomes[chave][partes["serie"]] += 1

        read_link = (linha.get("read_link") or "").strip()
        # A mesma edicao aparece em mais de uma semana quando o scraper rodou
        # duas vezes na mesma janela (19 e 20/08, por exemplo). A ultima leitura
        # vence, mas nunca troca um read_link existente por vazio.
        anterior = por_serie[chave].get(partes["numero"], {})
        por_serie[chave][partes["numero"]] = {
            "numero": partes["numero"],
            "titulo": " ".join((linha.get("title_x") or "").split()),
            "data": data,
            "preco": (linha.get("price") or "").strip(),
            "capa": (linha.get("cover") or "").strip(),
            "link": (linha.get("link") or "").strip(),
            "read_link": read_link or anterior.get("read_link", ""),
        }

    return por_serie, nomes


def montar_series(por_serie, nomes, referencia):
    """Monta o indice do catalogo, uma entrada por serie."""
    catalogo = []
    for chave, edicoes in por_serie.items():
        ordenadas = sorted(edicoes.values(), key=lambda e: (ordem_numero(e["numero"]), e["data"]))
        primeira, ultima = ordenadas[0], ordenadas[-1]
        # Entre as grafias vistas ("Amazing Spider-Man" / "The Amazing
        # Spider-Man"), exibe a mais frequente.
        nome = nomes[chave].most_common(1)[0][0]
        em_publicacao = dt.date.fromisoformat(ultima["data"]) >= referencia - JANELA_EM_PUBLICACAO
        # Capa do indice: a da edicao mais recente que tenha uma.
        capa = next((e["capa"] for e in reversed(ordenadas) if e["capa"]), "")

        catalogo.append({
            "id": chave,
            "nome": nome,
            "editora": chave.split("_", 1)[0],
            "capa": capa,
            "edicoes_conhecidas": len(ordenadas),
            "primeira_edicao": {"numero": primeira["numero"], "data": primeira["data"]},
            "ultima_edicao": {"numero": ultima["numero"], "data": ultima["data"]},
            "status": "em-publicacao" if em_publicacao else "sem-noticia",
            "fonte": "locg-csv",
        })

    catalogo.sort(key=lambda s: (s["editora"], s["nome"].lower()))
    return catalogo


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--origem", default="../comics_releases/comics",
                    help="pasta com os *_mergecomics.csv")
    # Os JSONs moram dentro de web/ para que a pasta seja um site completo,
    # publicavel no Pages sem nenhuma etapa de montagem.
    ap.add_argument("--saida", default=str(RAIZ / "web" / "data"))
    args = ap.parse_args()

    origem = (RAIZ / args.origem).resolve() if not pathlib.Path(args.origem).is_absolute() else pathlib.Path(args.origem)
    linhas = ler_csvs(origem)

    descartes = Counter()
    por_serie, nomes = montar_edicoes(linhas, descartes)

    datas = [e["data"] for edicoes in por_serie.values() for e in edicoes.values()]
    # A referencia e a ultima data COBERTA pelos dados, nao hoje. Com hoje, uma
    # base parada em 2025 marcaria o catalogo inteiro como encerrado. A
    # interface mostra essa data para o usuario saber ate quando o app enxerga.
    referencia = dt.date.fromisoformat(max(datas))

    catalogo = montar_series(por_serie, nomes, referencia)
    meta = {
        "gerado_em": dt.datetime.now().replace(microsecond=0).isoformat(),
        "fonte": "locg-csv",
        "referencia": referencia.isoformat(),
        "cobertura": {"de": min(datas), "ate": max(datas)},
        "total_series": len(catalogo),
        "total_edicoes": sum(len(e) for e in por_serie.values()),
        "em_publicacao": sum(1 for s in catalogo if s["status"] == "em-publicacao"),
        "descartes": dict(descartes),
    }

    edicoes = {
        chave: sorted(valores.values(), key=lambda e: (ordem_numero(e["numero"]), e["data"]))
        for chave, valores in por_serie.items()
    }
    escrever_catalogo(catalogo, edicoes, meta, pathlib.Path(args.saida))

    print(f"{meta['total_series']} series, {meta['total_edicoes']} edicoes")
    print(f"cobertura: {meta['cobertura']['de']} a {meta['cobertura']['ate']}")
    print(f"em publicacao: {meta['em_publicacao']}")
    print("descartes:", dict(descartes))


if __name__ == "__main__":
    main()
