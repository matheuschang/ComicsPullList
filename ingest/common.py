"""Regras de catalogo compartilhadas entre as fontes de dados.

A fonte de hoje sao os CSVs da League of Comic Geeks (via comics_releases).
A fonte da fase 2 e a API da Metron. Tudo que for logica de catalogo -- o que
conta como serie, o que e ruido, como se ordena uma edicao -- mora aqui, para
que trocar a fonte nao mude o comportamento do app.
"""

import datetime as dt
import json
import re
import unicodedata

# Uma serie conta como "em publicacao" se lancou algo nesta janela, contada a
# partir da data de referencia do catalogo.
JANELA_EM_PUBLICACAO = dt.timedelta(days=90)

# Editoras que entram no catalogo. A chave e o que vem na fonte; o valor e o
# codigo curto usado no JSON e nos filtros da interface.
EDITORAS = {
    "DC Comics": "dc",
    "Marvel Comics": "marvel",
}

# "Absolute Batman #13" -> ("Absolute Batman", "13", "")
# O numero nao e so digito: existe "#C-23" (Limited Collectors' Edition).
_TITULO = re.compile(r"^(?P<serie>.+?)\s*#(?P<numero>[0-9]+(?:\.[0-9]+)?|[A-Z]+-[0-9]+)(?P<resto>.*)$", re.S)

# Marcadores de reimpressao que podem aparecer em qualquer lugar do titulo. A
# LOCG poe "Facsimile Edition" tanto depois do numero ("Civil War #1 Facsimile
# Edition 2025") quanto antes ("Marvel / DC: Spider-Boy Facsimile Edition #1").
# Sao seguros de casar no titulo inteiro: nenhuma serie real se chama assim.
_RUIDO_TITULO = re.compile(r"facsimile|\b\d+(?:st|nd|rd|th)\s+printing", re.I)

# Estes so valem depois do numero. Casar "variant" no titulo inteiro derrubaria
# series legitimas -- a Marvel publicou "The Variants" em 2022.
_RUIDO_RESTO = re.compile(r"\bvariant\b|\breprint\b|\bprinting\b", re.I)

# Series que nao pertencem a uma linha de publicacao mensal (webtoons digitais).
# "Infinity Comic" (Marvel) e "... Go! Edition" (DC) sao capitulos verticais
# digitais, com dezenas de "edicoes" semanais -- nao os floppies que se segue.
# "Go! Edition" exige o "Edition" de proposito: nao pega "Teen Titans Go!".
_SERIE_RUIDO = re.compile(r"infinity comic|\bgo!?\s+edition\b", re.I)

# Catalogos de divulgacao, nao HQ: "Marvel Previews", "DC Connect". Entram como
# Regular Issue na LOCG, mas sao so os folhetos mensais do que vai sair.
_SERIE_PREVIEW = re.compile(r"\bpreviews\b|\bdc connect\b", re.I)

# Prefixos ignorados ao agrupar: a LOCG alterna entre "The Amazing Spider-Man"
# e "Amazing Spider-Man" para a mesma serie.
_PREFIXO_ARTIGO = re.compile(r"^(the)\s+", re.I)


def parse_titulo(titulo):
    """Quebra "Serie #N" em componentes. Devolve None se nao casar."""
    if not titulo:
        return None
    # Titulos multilinha da LOCG: a segunda linha e o subtitulo/reimpressao.
    limpo = " ".join(titulo.split())
    m = _TITULO.match(limpo)
    if not m:
        return None
    return {
        "serie": m.group("serie").strip(),
        "numero": m.group("numero").strip(),
        "resto": m.group("resto").strip(),
    }


def motivo_descarte(serie, resto):
    """Por que esta edicao nao entra no catalogo, ou None se ela entra."""
    if _RUIDO_TITULO.search(serie) or _RUIDO_TITULO.search(resto):
        return "reimpressao"
    if _RUIDO_RESTO.search(resto):
        return "reimpressao"
    if _SERIE_RUIDO.search(serie):
        return "digital-vertical"
    if _SERIE_PREVIEW.search(serie):
        return "preview-catalogo"
    return None


def chave_serie(editora, serie):
    """Identidade da serie para agrupar edicoes.

    Na fase 2 isso vira o id da Metron, que e estavel de verdade. Ate la, um
    slug normalizado -- suficiente para agrupar, mas incapaz de distinguir
    relancamentos ("Batman #1" de 2016 e de 2025 colidem). O campo `fonte` no
    JSON marca essa limitacao para quem consumir os dados.
    """
    nome = _PREFIXO_ARTIGO.sub("", serie)
    nome = unicodedata.normalize("NFKD", nome)
    nome = "".join(c for c in nome if not unicodedata.combining(c))
    nome = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")
    # Underscore separa a editora do slug: o slug nunca contem underscore
    # (a regex acima mapeia tudo que nao e alfanumerico para "-"), entao a
    # chave e reversivel e serve como nome de arquivo em qualquer sistema.
    return f"{editora}_{nome}"


def ordem_numero(numero):
    """Chave de ordenacao de edicao. Numeros vem antes de codigos tipo C-23."""
    try:
        return (0, float(numero))
    except ValueError:
        return (1, 0.0)


def escrever_catalogo(catalogo, edicoes_por_serie, meta, saida):
    """Grava series.json, meta.json e um issues/<id>.json por serie.

    Usado pelas duas fontes -- e o contrato entre a ingestao e o site.
    """
    saida.mkdir(parents=True, exist_ok=True)
    pasta = saida / "issues"

    # Remove JSONs de series que sumiram do catalogo. Sem isto, trocar a fonte
    # (ou uma serie deixar de se qualificar) deixa arquivo orfao sendo servido.
    if pasta.exists():
        validos = {f"{s['id']}.json" for s in catalogo}
        for antigo in pasta.glob("*.json"):
            if antigo.name not in validos:
                antigo.unlink()
    pasta.mkdir(exist_ok=True)

    def gravar(caminho, dados):
        caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")

    gravar(saida / "series.json", catalogo)
    gravar(saida / "meta.json", meta)
    for serie in catalogo:
        gravar(pasta / f"{serie['id']}.json", {
            "id": serie["id"],
            "nome": serie["nome"],
            "editora": serie["editora"],
            "edicoes": edicoes_por_serie[serie["id"]],
        })
