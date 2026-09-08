"""Base de FICHAS: raspa a LOCG para preencher cadastro manual de quadrinhos.

Script PARALELO ao `from_locg.py`. Nao mexe em `web/data/` e nao alimenta o site
-- gera uma base propria em `fichas/`, com os campos de um formulario de cadastro
(titulo, titulo original, tipo, numero/volume, ano, editora, pais, paginas,
roteiristas, artistas, descricao, capa).

Por que existe separado: o site precisa de pouca coisa por edicao (numero, data,
preco, capa) e roda semanalmente; o cadastro precisa de MAIS campos por edicao
(paginas, formato, creditos, sinopse, capa grande) e nao tem pressa. Misturar os
dois faria a atualizacao semanal do site pagar o custo de 2700 paginas de detalhe.

DERIVA DO CATALOGO DO SITE. A lista de trabalho vem de `web/data/issues/*.json`
-- ou seja, o mesmo escopo (Marvel/DC, regular issues + annuals, mesma janela)
sem re-descobrir nada. Consequencia pratica: rode o `from_locg.py --atualizar`
primeiro, depois este; o que entrou no site entra aqui na proxima rodada.

    # historico (2700 edicoes) -- faca em lotes, e retomavel
    python ingest/fichas_locg.py --anexar 127.0.0.1:9222 --limite 300

    # futuro (semanal, depois do --atualizar): pega so os links novos
    python ingest/fichas_locg.py --anexar 127.0.0.1:9222

    # capas grandes (sem browser, direto do S3) -- ~220 KB cada
    python ingest/fichas_locg.py --so-capas

    # calibrar os seletores de "paginas"/"formato" numa edicao
    python ingest/fichas_locg.py --anexar 127.0.0.1:9222 --probe URL_DA_EDICAO

Incremental e idempotente: a base e indexada pelo link da edicao na LOCG, entao
rodar de novo so visita o que falta. Salva a cada serie -- um crash no meio nao
perde o lote. Mesmo modo `--anexar` do from_locg.py (Cloudflare bloqueia Chrome
controlado; veja o cabecalho de lá).

Saida em `fichas/`:
    fichas.json   base canonica (uma entrada por edicao, chaveada pelo link)
    fichas.csv    a mesma coisa em CSV UTF-8 com BOM, pronto pro Excel
    capas/        capa grande de cada edicao (so com --capas / --so-capas)
"""

import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import ordem_numero
from from_locg import (aguardar_conteudo, cache_gravar, cache_ler, criar_driver,
                       EDITORAS_LOCG)
import from_locg

RAIZ = pathlib.Path(__file__).resolve().parent.parent
DADOS_SITE = RAIZ / "web" / "data"

# Nome completo da editora, como o formulario espera ("Ex.: DC Comics").
EDITORA_NOME = {v: k for k, v in EDITORAS_LOCG.items()}   # dc -> "DC Comics"

# Tudo que raspamos e edicao americana; o campo Pais e constante.
PAIS = "Estados Unidos"

# O escopo do site e "regular issue + annual", ou seja: sempre uma edicao avulsa,
# nunca encadernado. Se um dia entrar TPB/HC, o mapa cresce aqui.
TIPO_PADRAO = "Edição individual"

# Limite do campo Descricao no formulario.
MAX_DESCRICAO = 3000

# "Detective Comics 2026 Annual" -> serie "Detective Comics", numero "Annual N".
# O formulario separa titulo de numero/volume ("Exemplos: nº 1, vol. 2 ou
# Annual 1"), entao o "Annual" pertence ao numero, nao ao titulo.
_SERIE_ANNUAL = re.compile(r"^(?P<serie>.+?)\s+(?:\d{4}\s+)?annual$", re.I)


# ------------------------------------------------------------------ extracao

# Superset do _JS_ENRIQUECER do from_locg.py: alem de sinopse/criadores/
# personagens, colhe o bloco de detalhes (paginas, formato) e a capa.
#
# A LOCG nao documenta a marcacao dos detalhes, e ela varia entre edicoes. Em vez
# de apostar num seletor, varremos os formatos usuais de "rotulo: valor"
# (dl/dt/dd, tabela de 2 colunas, item com <strong> na frente) e devolvemos
# TAMBEM o texto cru do bloco -- o --probe imprime os dois, e a extracao no
# Python usa os pares e cai na regex do texto cru quando o par nao veio.
_JS_FICHA = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const out = {
  titulo_pagina: '', capa: '', sinopse: '', linha_detalhe: '',
  criadores: { escritor: [], arte: [], cor: [], letra: [], editor: [], capa: [] },
  personagens: [], detalhes: {}, detalhes_raw: '',
};

const h1 = document.querySelector('h1');
if (h1) out.titulo_pagina = norm(h1.textContent);

const desc = document.querySelector('.listing-description');
if (desc) out.sinopse = norm(desc.textContent);

// Capa: a primeira imagem que mora no bucket de capas da LOCG.
for (const img of document.querySelectorAll('img')) {
  const s = img.getAttribute('src') || img.getAttribute('data-src') || '';
  if (/comics\/covers\//.test(s)) { out.capa = s; break; }
}

// Criadores. Cada credito e um par de irmaos: <div class="role">Writer</div>
// + <div class="name"><a>Jeph Loeb</a></div>. Pegamos o par, nao o avatar: o
// avatar e uma <img> sem texto e ele MESMO carrega a classe .col-auto, entao
// subir com closest('.col-auto') devolvia o proprio avatar e o cargo se perdia.
//
// A pagina tem tres blocos de credito e eles nao valem o mesmo:
//   section#creators-      "Featured Creators" -> o credito da EDICAO (usamos)
//   #cover-artists         so a capa -> vai separado, nao e o artista de dentro
//   section#creators-<id>  por materia, repete gente -> ignorado
const secC = document.querySelector('section[id="creators-"]')
          || document.querySelector('section[id^="creators-"]');
const blocos = [[secC, false], [document.querySelector('#cover-artists'), true]];
for (const [bloco, ehCapa] of blocos) {
  if (!bloco) continue;
  for (const elRole of bloco.querySelectorAll('.role')) {
    const elNome = elRole.parentElement && elRole.parentElement.querySelector('.name');
    if (!elNome) continue;
    const nome = norm(elNome.textContent);
    const role = norm(elRole.textContent).toLowerCase();
    if (!nome) continue;
    const add = (arr) => { if (!arr.includes(nome)) arr.push(nome); };
    // "Cover Penciller" e artista de CAPA, nao de miolo -- sem este desvio ele
    // entraria no campo Artista(s) do formulario.
    if (ehCapa || /^cover\b/.test(role)) { add(out.criadores.capa); continue; }
    if (/writ|script|story by|plot/.test(role)) add(out.criadores.escritor);
    if (/pencil|inker|artist|illustrat|breakdown|finishes|layout/.test(role)) add(out.criadores.arte);
    if (/colou?r/.test(role)) add(out.criadores.cor);
    if (/letter/.test(role)) add(out.criadores.letra);
    if (/editor/.test(role)) add(out.criadores.editor);
  }
}

const secP = document.querySelector('section[id^="characters-"]');
if (secP) {
  const vis = new Set();
  for (const a of secP.querySelectorAll('a[href*="/character/"]')) {
    const n = norm(a.textContent);
    if (n && !vis.has(n)) { vis.add(n); out.personagens.push(n); }
  }
}

// Pares "rotulo -> valor" do bloco de detalhes.
const guardar = (k, v) => {
  k = norm(k).replace(/:$/, '').toLowerCase();
  v = norm(v);
  // Rotulo tem que ser palavra. Sem isto o histograma de notas da LOCG
  // ("1 -> 287", "2 -> 222", ...) entra como se fosse detalhe da edicao.
  if (!/[a-z]/.test(k)) return;
  if (k && v && k.length < 40 && !(k in out.detalhes)) out.detalhes[k] = v;
};
for (const dl of document.querySelectorAll('dl')) {
  const dts = dl.querySelectorAll('dt'), dds = dl.querySelectorAll('dd');
  for (let i = 0; i < Math.min(dts.length, dds.length); i++) {
    guardar(dts[i].textContent, dds[i].textContent);
  }
}
for (const tr of document.querySelectorAll('tr')) {
  const c = tr.querySelectorAll('td, th');
  if (c.length === 2) guardar(c[0].textContent, c[1].textContent);
}
// A linha de detalhe da edicao: "Comic · 36 pages · $4.99 · Connecting Cover".
// E daqui que saem formato e numero de paginas -- a LOCG nao usa rotulo nenhum,
// e tudo separado por "·" numa linha so, em div.copy-small.font-italic.
//
// CUIDADO: os itens de `section#stories` sao .copy-really-small e tem o MESMO
// formato ("Story · 22 pages", "Cover Gallery · 1 page"), mas sao a quebra por
// materia -- somam menos que o total. Por isso exigimos .copy-small e recusamos
// .copy-really-small.
for (const el of document.querySelectorAll('div.copy-small.font-italic')) {
  if (el.classList.contains('copy-really-small')) continue;
  const t = norm(el.textContent);
  if (/\d+\s*pages?\b/i.test(t) && t.length < 200) { out.linha_detalhe = t; break; }
}

const det = document.querySelector('#comic-details');
if (det) out.detalhes_raw = norm(det.textContent).slice(0, 800);
return out;
"""

# Rotulos possiveis para cada campo (a LOCG varia). Primeiro que casar, vence.
_ROTULOS_PAGINAS = ("page count", "pages", "page-count", "paginas")
_ROTULOS_FORMATO = ("format", "type", "publication type")

_RE_PAGINAS = re.compile(r"(\d{1,4})\s*pages?\b", re.I)
_RE_FORMATO = re.compile(
    r"\b(regular|annual|one[- ]shot|trade paperback|tpb|hardcover|omnibus|"
    r"graphic novel|digital|magazine)\b", re.I)


def _primeiro(detalhes, rotulos):
    """Valor do primeiro rotulo presente no mapa de detalhes."""
    for r in rotulos:
        if r in detalhes:
            return detalhes[r]
    return ""


def partes_detalhe(bruto):
    """Quebra "Comic · 36 pages · $4.99 · Connecting Cover" nos pedacos."""
    linha = bruto.get("linha_detalhe") or ""
    return [p.strip() for p in linha.split("·") if p.strip()]


def paginas_de(bruto):
    """Numero de paginas: da linha de detalhe, dos pares, ou do texto cru."""
    for parte in partes_detalhe(bruto):
        m = _RE_PAGINAS.match(parte) or _RE_PAGINAS.search(parte)
        if m:
            return m.group(1)
    valor = _primeiro(bruto.get("detalhes") or {}, _ROTULOS_PAGINAS)
    m = re.search(r"\d{1,4}", valor) if valor else None
    if m:
        return m.group(0)
    m = _RE_PAGINAS.search(bruto.get("detalhes_raw") or "")
    return m.group(1) if m else ""


def formato_de(bruto):
    """Formato da edicao na LOCG (Comic, Annual, ...) -- referencia, nao vai no form.

    E o primeiro pedaco da linha de detalhe; os outros sao paginas, preco e
    observacao de capa.
    """
    partes = partes_detalhe(bruto)
    if partes and not _RE_PAGINAS.search(partes[0]) and not partes[0].startswith("$"):
        return partes[0]
    valor = _primeiro(bruto.get("detalhes") or {}, _ROTULOS_FORMATO)
    if valor:
        return valor
    m = _RE_FORMATO.search(bruto.get("detalhes_raw") or "")
    return m.group(1) if m else ""


def capa_grande(url):
    """Troca a capa 'medium-' pela 'large-' (~220 KB em vez de ~29 KB).

    O formulario aceita ate 5 MB, e a medium fica pequena demais pra capa de
    ficha. 'original-' existe mas o bucket devolve 403.
    """
    return re.sub(r"/(?:small|medium)-", "/large-", url or "")


def capa_da_edicao(edicao, bruto):
    """A capa DESTA edicao, em tamanho grande -- uma so, nunca de variante.

    A pagina da LOCG tambem mostra capas de variantes e de "recomendados", e a
    primeira <img> do bucket nem sempre e a principal. O catalogo do site ja
    guarda a capa certa (`medium-<id>.jpg`, o mesmo `<id>` do link), entao ela
    manda; a raspada e so reserva, e so se casar com o id da edicao.
    """
    ident = id_locg(edicao.get("link"))
    for url in (edicao.get("capa"), bruto.get("capa")):
        if url and (not ident or ident in url):
            return capa_grande(url)
    return capa_grande(edicao.get("capa") or bruto.get("capa") or "")


def titulo_e_numero(nome_serie, numero, formato=""):
    """Separa o que vai em 'Titulo original' do que vai em 'Numero / volume'.

    O formulario quer o titulo da publicacao num campo e a identificacao no
    outro ("Exemplos: nº 1, vol. 2 ou Annual 1"). Para um mensal: "Batman" +
    "nº 163".

    Annual tem duas formas no catalogo do site. Quando o nome TERMINA em
    "(ano) Annual" ("Detective Comics 2026 Annual"), o "Annual" e so rotulo:
    sai do titulo e vira o numero. Quando vem no meio, seguido de subtitulo
    ("Wonder Woman 2026 Annual: Wonder War - The Matriarch Special"), recortar
    mutilaria o titulo -- mantemos o nome inteiro e so ajustamos o numero.
    Quem decide se e annual e o `formato` que a LOCG informa, nao o nome.
    """
    ehAnual = formato.strip().lower() == "annual"
    m = _SERIE_ANNUAL.match(nome_serie)
    if m:
        return m.group("serie"), f"Annual {numero}".strip()
    if ehAnual:
        return nome_serie, f"Annual {numero}".strip()
    return nome_serie, f"nº {numero}" if numero else ""


def cortar(texto, limite=MAX_DESCRICAO):
    """Trunca respeitando palavra, para caber no campo do formulario."""
    if len(texto) <= limite:
        return texto
    # -1 para o "…" caber DENTRO do limite: o campo tem maxlength 3000.
    corte = texto[:limite - 1]
    espaco = corte.rfind(" ")
    return (corte[:espaco] if espaco > limite * 0.8 else corte).rstrip() + "…"


def montar_ficha(serie, edicao, bruto):
    """Junta o que veio do catalogo do site com o que veio da pagina de detalhe."""
    editora = EDITORA_NOME.get(serie["editora"], serie["editora"])
    formato = formato_de(bruto)
    titulo, numero = titulo_e_numero(serie["nome"], edicao.get("numero", ""), formato)
    data = edicao.get("data") or ""
    criadores = bruto.get("criadores") or {}
    juntar = lambda k: ", ".join(criadores.get(k) or [])

    return {
        # --- campos do formulario, na ordem da tela ---
        "capa_arquivo": nome_capa(serie, edicao),
        "titulo": titulo,                 # em PT; vem preenchido com o original
        "titulo_original": titulo,
        "tipo_publicacao": TIPO_PADRAO,
        "numero_volume": numero,
        "ano": data[:4],
        "editora": editora,
        "pais": PAIS,
        "paginas": paginas_de(bruto),
        "roteiristas": juntar("escritor"),
        "artistas": juntar("arte"),
        "descricao": cortar(bruto.get("sinopse") or ""),
        # --- referencia (nao sao campos do formulario) ---
        "serie": serie["nome"],
        "serie_id": serie["id"],
        "data_lancamento": data,
        "preco_usd": edicao.get("preco") or "",
        "coloristas": juntar("cor"),
        "letristas": juntar("letra"),
        "editores": juntar("editor"),
        "artistas_capa": juntar("capa"),
        "personagens": ", ".join(bruto.get("personagens") or []),
        "formato_locg": formato,
        "capa_url": capa_da_edicao(edicao, bruto),
        "link": edicao.get("link") or "",
    }


# Ordem das colunas do CSV: primeiro os campos do formulario (de cima pra baixo,
# como na tela), depois as colunas de referencia.
COLUNAS = [
    "capa_arquivo", "titulo", "titulo_original", "tipo_publicacao",
    "numero_volume", "ano", "editora", "pais", "paginas",
    "roteiristas", "artistas", "descricao",
    "serie", "serie_id", "data_lancamento", "preco_usd",
    "coloristas", "letristas", "editores", "artistas_capa", "personagens",
    "formato_locg", "capa_url", "link",
]


# -------------------------------------------------------------- lista/estado

def id_locg(link):
    """Id da edicao na URL: .../comic/1126236/batman-163 -> '1126236'."""
    m = re.search(r"/comic/(\d+)", link or "")
    return m.group(1) if m else ""


def nome_capa(serie, edicao):
    """Nome do arquivo de capa: UMA capa por edicao/link, sem colisao.

    Leva o id da LOCG de proposito. Sem ele, duas edicoes diferentes podem cair
    no mesmo nome -- o `chave_serie` do site e um slug do nome, entao um
    relancamento ("Batman #1" de 2016 e de 2025) colide em serie+numero e a
    segunda capa sobrescreveria a primeira. Com o id, o nome e 1:1 com o link.
    """
    num = re.sub(r"[^A-Za-z0-9.-]+", "-", str(edicao.get("numero") or "sn"))
    ident = id_locg(edicao.get("link"))
    return f"{serie['id']}-{num}{'-' + ident if ident else ''}.jpg"


def lista_de_trabalho(ordem):
    """Todas as edicoes do catalogo do site que tem link, com a serie ao lado.

    E daqui que sai o escopo: o mesmo do site, sem re-descobrir nada.
    """
    series = json.loads((DADOS_SITE / "series.json").read_text(encoding="utf-8"))
    itens = []
    for s in series:
        arq = DADOS_SITE / "issues" / f"{s['id']}.json"
        if not arq.exists():
            continue
        for e in json.loads(arq.read_text(encoding="utf-8")).get("edicoes", []):
            if e.get("link"):
                itens.append((s, e))
    if ordem == "data":
        itens.sort(key=lambda p: (p[1].get("data") or "", p[0]["nome"]), reverse=True)
    else:
        itens.sort(key=lambda p: (p[0]["editora"], p[0]["nome"].lower(),
                                  ordem_numero(p[1].get("numero", ""))))
    return itens


def carregar_base(saida):
    """Base como {link: ficha}. Chavear pelo link e o que torna o run retomavel."""
    arq = saida / "fichas.json"
    if not arq.exists():
        return {}
    try:
        dados = json.loads(arq.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return {f["link"]: f for f in dados.get("fichas", []) if f.get("link")}


def _gravar_atomico(caminho, escrever, tentativas=5):
    """Grava via arquivo temporario + replace, com retentativa.

    Duas razoes, as duas ja custaram um run inteiro:

    - O projeto mora dentro do OneDrive, que trava o arquivo durante o sync
      (e o antivirus tambem, ao varrer). Deu OSError [Errno 22] no meio de uma
      rodada de 45 min e derrubou tudo. O lock e passageiro, entao insiste.
    - Escrever direto no destino deixa a base truncada se o processo morrer no
      meio; com replace, o arquivo antigo vale ate o novo estar pronto.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    for n in range(tentativas):
        try:
            escrever(tmp)
            os.replace(tmp, caminho)
            return True
        except OSError as erro:
            if n == tentativas - 1:
                print(f"  ! nao consegui gravar {caminho.name}: "
                      f"{type(erro).__name__} {erro.errno} -- a base em memoria segue,"
                      " tente de novo depois com --so-csv")
                return False
            time.sleep(1.5 * (n + 1))
            try:
                tmp.unlink()
            except OSError:
                pass
    return False


def gravar_base(saida, base, com_csv=True):
    """Grava fichas.json (canonico) e, se pedido, fichas.csv (o entregavel).

    O JSON e o estado de retomada, entao vai a cada serie. O CSV e derivado e
    custava um arquivo inteiro reescrito por serie (~700 vezes numa rodada
    cheia): fica para o fim, e o --so-csv regera quando preciso.
    """
    fichas = sorted(base.values(),
                    key=lambda f: (f.get("data_lancamento") or "", f.get("serie") or ""),
                    reverse=True)

    def escrever_json(destino):
        destino.write_text(json.dumps({
            "gerado_em": dt.datetime.now().replace(microsecond=0).isoformat(),
            "total": len(fichas),
            "fichas": fichas,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    def escrever_csv(destino):
        # utf-8-sig: sem o BOM o Excel no Windows abre os acentos errados.
        with destino.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUNAS, extrasaction="ignore")
            w.writeheader()
            w.writerows(fichas)

    _gravar_atomico(saida / "fichas.json", escrever_json)
    if com_csv:
        _gravar_atomico(saida / "fichas.csv", escrever_csv)


def regerar_csv(saida):
    """Refaz o fichas.csv a partir do fichas.json, sem raspar nada."""
    base = carregar_base(saida)
    if not base:
        print(f"base vazia em {saida} -- nada a regerar.")
        return
    gravar_base(saida, base)
    print(f"fichas.csv regerado: {len(base)} linhas, {len(COLUNAS)} colunas.")


# ------------------------------------------------------------------- modos

# Recursos que a extracao NAO usa. Sao ~2700 paginas: baixar capa, avatar, fonte
# e video de cada uma dominava o tempo (medido: 5,2s por pagina, ~3,8h no total).
# Bloquear isso derruba o tempo sem perder dado -- a <img> continua no DOM com o
# src intacto (o request e que falha), e nossos seletores sao por classe, nao
# dependem de layout.
_BLOQUEAR = [
    "*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.svg", "*.ico",
    "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot",
    "*.mp4", "*.webm", "*.mp3", "*.avi",
    "*googlesyndication*", "*doubleclick*", "*google-analytics*",
    "*googletagmanager*", "*facebook.net*", "*adservice*", "*taboola*",
]


def acelerar(driver):
    """Bloqueia imagem/fonte/video/ads na sessao do Chrome, via CDP.

    Vale para a JANELA TODA (e um Chrome anexado, nao nosso). Some quando o
    Chrome e fechado -- e um perfil dedicado a raspagem, entao nao incomoda.
    Se o CDP nao aceitar, segue sem acelerar: e otimizacao, nao requisito.
    """
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": _BLOQUEAR})
        return True
    except Exception as erro:
        print(f"  (sem aceleracao: {type(erro).__name__})")
        return False


def uma_ficha(driver, link):
    """Visita a pagina da edicao e devolve o payload cru (com cache em disco)."""
    # Chave propria: o cache "edicao:" do from_locg guarda um payload MENOR
    # (sem detalhes/capa), reaproveita-lo devolveria ficha sem paginas.
    #
    # A VERSAO faz parte da chave e sobe junto com o _JS_FICHA. A v1 nao tinha
    # `linha_detalhe` (de onde saem paginas e formato) nem separava artista de
    # capa: reaproveitar aquele payload devolvia ficha furada, e --refazer nao
    # resolvia porque ele le o cache. Ao mexer no _JS_FICHA, suba a versao.
    chave = "ficha:v2:" + link
    cache = cache_ler(chave)
    if cache is not None:
        return cache

    # Nada aqui pode derrubar o run: sao ~2700 paginas e uma so que trave
    # custaria a rodada inteira. Foi o que aconteceu -- uma pagina passou dos
    # 120s do cliente do chromedriver e o ReadTimeoutError subiu do driver.get.
    # Pagina problematica agora e so pulada; a proxima rodada tenta de novo.
    try:
        driver.get(link)
    except Exception:
        # Timeout de page load deixa o DOM meio montado -- ainda vale tentar
        # extrair, porque o que precisamos costuma ja estar la.
        pass
    try:
        if not aguardar_conteudo(driver, "section[id^='creators-'], .listing-description",
                                 timeout=45):
            print(f"    ! sem conteudo em {link}")
            return None
        bruto = driver.execute_script(_JS_FICHA)
    except Exception as erro:
        print(f"    ! falhou em {link}: {type(erro).__name__}")
        return None
    cache_gravar(chave, bruto)
    return bruto


def rodar(driver, saida, limite, ordem, refazer, acelerado=True):
    """Visita as edicoes que faltam e adiciona na base. Retomavel."""
    if acelerado:
        acelerar(driver)
    # Sem isto, driver.get espera a pagina inteira e pode passar dos 120s do
    # cliente do chromedriver -- que ai estoura por fora, sem dar pra tratar.
    # 40s e folgado: a pagina util monta em ~1s com a aceleracao ligada.
    for ajuste, valor in (("set_page_load_timeout", 40), ("set_script_timeout", 30)):
        try:
            getattr(driver, ajuste)(valor)
        except Exception:
            pass

    base = carregar_base(saida)
    itens = lista_de_trabalho(ordem)
    pendentes = [(s, e) for s, e in itens if refazer or e["link"] not in base]
    print(f"catalogo do site: {len(itens)} edicoes | base de fichas: {len(base)} | "
          f"a fazer: {len(pendentes)}" + (f" (lote de ate {limite})" if limite else ""))
    if not pendentes:
        print("nada a fazer -- a base ja cobre todo o catalogo.")
        return

    feitas = falhas = seguidas = 0
    serie_atual = None
    for serie, edicao in pendentes:
        if limite and feitas >= limite:
            break
        bruto = uma_ficha(driver, edicao["link"])
        if not bruto:
            falhas += 1
            seguidas += 1
            # Disjuntor: pagina ruim isolada e normal, mas 20 seguidas significa
            # sessao morta ou bloqueio -- nao ha porque queimar o resto da lista
            # marcando falha. A base ja esta salva e a proxima rodada retoma.
            if seguidas >= 20:
                print(f"\n! 20 falhas seguidas -- parando. Provavel Cloudflare ou "
                      f"Chrome fechado. Confira a janela e rode de novo (retoma daqui).")
                break
            continue
        seguidas = 0
        base[edicao["link"]] = montar_ficha(serie, edicao, bruto)
        feitas += 1
        # Salva ao trocar de serie: um crash perde no maximo uma serie. So o
        # JSON -- o CSV e derivado e sai no fim (ver gravar_base).
        if serie["id"] != serie_atual:
            if serie_atual is not None:
                gravar_base(saida, base, com_csv=False)
            serie_atual = serie["id"]
            print(f"  {feitas:4}/{len(pendentes)}  {serie['nome'][:40]}")

    gravar_base(saida, base)
    com_pag = sum(1 for f in base.values() if f.get("paginas"))
    com_cred = sum(1 for f in base.values() if f.get("roteiristas"))
    com_desc = sum(1 for f in base.values() if f.get("descricao"))
    print(f"\n{feitas} fichas nesta rodada"
          + (f", {falhas} paginas puladas (a proxima rodada tenta de novo)" if falhas else "")
          + f". Base: {len(base)} fichas.")
    print(f"preenchimento -> paginas {com_pag}/{len(base)} | "
          f"roteirista {com_cred}/{len(base)} | descricao {com_desc}/{len(base)}")
    if len(base) and not com_pag:
        print("AVISO: nenhuma pagina extraida -- rode --probe numa edicao e me mande a saida.")
    if limite and len(pendentes) > feitas:
        print(f"faltam ~{len(pendentes) - feitas}. Rode de novo pra continuar.")


def baixar_capas(saida, limite):
    """Baixa a capa grande de cada ficha. Nao usa browser -- e S3 direto."""
    base = carregar_base(saida)
    if not base:
        print("base vazia -- rode o script sem --so-capas primeiro.")
        return
    pasta = saida / "capas"
    pasta.mkdir(parents=True, exist_ok=True)

    pendentes = [f for f in base.values()
                 if f.get("capa_url") and not (pasta / f["capa_arquivo"]).exists()]
    print(f"capas: {len(base) - len(pendentes)} no disco, {len(pendentes)} a baixar "
          f"(~{len(pendentes) * 220 // 1024} MB)")
    baixadas = falhas = 0
    for f in pendentes:
        if limite and baixadas >= limite:
            break
        destino = pasta / f["capa_arquivo"]
        try:
            req = urllib.request.Request(f["capa_url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                destino.write_bytes(r.read())
            baixadas += 1
            if baixadas % 50 == 0:
                print(f"  {baixadas}/{len(pendentes)}")
        except (urllib.error.URLError, OSError, TimeoutError) as erro:
            falhas += 1
            print(f"  ! {f['capa_arquivo']}: {type(erro).__name__}")
        time.sleep(0.15)   # gentileza com o bucket
    print(f"\n{baixadas} capas baixadas em {pasta}" + (f", {falhas} falharam" if falhas else ""))


# Caca-seletor, so pro --probe: onde na pagina moram "pages"/"format"?
# Nao da pra adivinhar a marcacao dos detalhes da LOCG, e ela muda de pagina
# pra pagina. Isto acha os elementos FOLHA cujo texto fala de pagina/formato e
# mostra tag, classe e o texto do pai -- com isso o seletor sai na hora.
_JS_CACAR_CRIADORES = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const secoes = [];
for (const s of document.querySelectorAll('section[id]')) secoes.push(s.id.slice(0, 40));
const links = [];
const vistos = new Set();
for (const a of document.querySelectorAll('a[href*="/people/"], a[href*="/creator/"], a[href*="/profile/"]')) {
  const href = a.getAttribute('href') || '';
  if (vistos.has(href)) continue;
  vistos.add(href);
  const av = a.closest('.avatar');
  // Sobe do avatar ate achar um ancestral que TENHA texto: e nele que mora o
  // cargo. O avatar em si nao tem texto (e uma <img> dentro de um <a>).
  let caixa = av || a, niveis = 0;
  while (caixa && caixa.parentElement && niveis < 5) {
    if (norm(caixa.textContent).length > 2) break;
    caixa = caixa.parentElement; niveis++;
  }
  links.push({
    href: href.slice(0, 55),
    alt: norm((a.querySelector('img') || {}).alt || '').slice(0, 40),
    subiu: niveis,
    caixa_cls: caixa ? (caixa.className || '').toString().slice(0, 50) : '',
    caixa_txt: caixa ? norm(caixa.textContent).slice(0, 90) : '',
  });
  if (links.length >= 8) break;
}
const html = {};
for (const sel of ['#top-level-credits', 'section[id^="creators-"]']) {
  const el = document.querySelector(sel);
  if (el) html[sel] = el.innerHTML.replace(/\s+/g, ' ').slice(0, 1600);
}
return { secoes, links, html };
"""

_JS_CACAR = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const alvo = /(\d+\s*pages?\b)|(\bpage count\b)|(\bformat\b)|(\bregular\b)|(\bannual\b)/i;
const achados = [];
for (const el of document.querySelectorAll('*')) {
  if (el.children.length) continue;             // so folhas
  const t = norm(el.textContent);
  if (!t || t.length > 60 || !alvo.test(t)) continue;
  const pai = el.parentElement;
  achados.push({
    tag: el.tagName.toLowerCase(),
    cls: (el.className || '').toString().slice(0, 45),
    txt: t,
    pai_tag: pai ? pai.tagName.toLowerCase() : '',
    pai_cls: pai ? (pai.className || '').toString().slice(0, 45) : '',
    pai_txt: pai ? norm(pai.textContent).slice(0, 110) : '',
  });
  if (achados.length >= 25) break;
}
return achados;
"""


def probe(driver, link):
    """Despeja o que a pagina da edicao oferece, pra calibrar paginas/formato."""
    bruto = uma_ficha(driver, link)
    if not bruto:
        print("nao consegui ler a pagina.")
        return
    print(f"\n=== titulo da pagina ===\n  {bruto.get('titulo_pagina') or '(vazio)'}")
    print(f"\n=== capa ===\n  {bruto.get('capa') or '(vazio)'}"
          f"\n  large -> {capa_grande(bruto.get('capa') or '') or '(vazio)'}")

    detalhes = bruto.get("detalhes") or {}
    print(f"\n=== pares rotulo->valor ({len(detalhes)}) ===")
    for k, v in list(detalhes.items())[:40]:
        print(f"  {k!r:32} = {v[:70]!r}")
    print(f"\n=== texto cru do bloco de detalhes ===\n  "
          f"{(bruto.get('detalhes_raw') or '(nao achou o bloco)')[:700]}")

    print("\n=== o que a extracao tirou disso ===")
    print(f"  paginas = {paginas_de(bruto)!r}")
    print(f"  formato = {formato_de(bruto)!r}")
    cr = bruto.get("criadores") or {}
    print(f"  roteirista = {cr.get('escritor')}")
    print(f"  arte       = {cr.get('arte')}")
    print(f"  sinopse    = {(bruto.get('sinopse') or '')[:120]!r}")
    print(f"  personagens= {(bruto.get('personagens') or [])[:6]}")
    if not paginas_de(bruto) or not formato_de(bruto):
        print("\n=== caca-seletor: onde 'pages'/'format' aparecem na pagina ===")
        try:
            achados = driver.execute_script(_JS_CACAR)
        except Exception as erro:
            achados = []
            print(f"  (falhou: {type(erro).__name__})")
        for a in achados:
            print(f"  <{a['tag']} class='{a['cls']}'> {a['txt']!r}")
            print(f"      pai <{a['pai_tag']} class='{a['pai_cls']}'> {a['pai_txt']!r}")
        if not achados:
            print("  (nada -- talvez a pagina nem publique paginas/formato)")

    if not (bruto.get("criadores") or {}).get("escritor"):
        print("\n=== caca-seletor: onde estao os criadores ===")
        try:
            d = driver.execute_script(_JS_CACAR_CRIADORES)
        except Exception as erro:
            d = {}
            print(f"  (falhou: {type(erro).__name__})")
        print(f"  sections com id: {(d.get('secoes') or [])[:18]}")
        for l in d.get("links") or []:
            print(f"  {l['href']!r} alt={l['alt']!r} (subiu {l['subiu']} niveis)")
            print(f"      caixa class='{l['caixa_cls']}'")
            print(f"      caixa txt={l['caixa_txt']!r}")
        for sel, html in (d.get("html") or {}).items():
            print(f"\n  --- innerHTML de {sel} ---\n  {html}")

    print("\nSe 'paginas' ou 'formato' vieram vazios, me manda essa saida "
          "que eu ajusto os seletores.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", metavar="URL_EDICAO",
                    help="despeja a estrutura de uma edicao (calibra paginas/formato)")
    ap.add_argument("--limite", type=int,
                    help="para depois de N fichas (ou N capas com --so-capas)")
    ap.add_argument("--ordem", choices=("data", "serie"), default="data",
                    help="ordem de visita: 'data' (mais recentes primeiro) ou 'serie'")
    ap.add_argument("--refazer", action="store_true",
                    help="re-visita tambem as edicoes que ja estao na base")
    ap.add_argument("--capas", action="store_true",
                    help="baixa as capas grandes ao terminar a raspagem")
    ap.add_argument("--so-capas", action="store_true",
                    help="so baixa as capas do que ja esta na base (sem browser)")
    ap.add_argument("--so-csv", action="store_true",
                    help="so regera o fichas.csv a partir do fichas.json (sem browser)")
    ap.add_argument("--saida", default="fichas", metavar="DIR",
                    help="pasta de saida (padrao: fichas/)")
    ap.add_argument("--sem-cache", action="store_true", help="ignora o cache em disco")
    ap.add_argument("--sem-aceleracao", action="store_true",
                    help="nao bloqueia imagem/fonte/ads (mais lento; use se algo quebrar)")
    ap.add_argument("--chromedriver", default="",
                    help="caminho do chromedriver (vazio = Selenium Manager)")
    ap.add_argument("--anexar", default="", metavar="HOST:PORTA",
                    help="conecta num Chrome com --remote-debugging-port (recomendado)")
    ap.add_argument("--perfil", default="", metavar="DIR", help="perfil persistente do Chrome")
    ap.add_argument("--headless", action="store_true", help="sem janela (cai no Cloudflare)")
    args = ap.parse_args()

    # O console do Windows e cp1252 e estoura em titulo/sinopse com acento ou
    # caractere fora da tabela ("…", "—"). Sem isto o script morre no print.
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.sem_cache:
        from_locg.USAR_CACHE = False

    saida = pathlib.Path(args.saida)
    if not saida.is_absolute():
        saida = RAIZ / saida

    if args.so_csv:
        regerar_csv(saida)
        return

    if args.so_capas:
        baixar_capas(saida, args.limite)
        return

    if not (DADOS_SITE / "series.json").exists():
        print("web/data/series.json nao existe -- rode o from_locg.py antes: "
              "a lista de trabalho vem do catalogo do site.")
        return

    driver = criar_driver(args.chromedriver, args.headless, args.anexar, args.perfil)
    try:
        if args.probe:
            probe(driver, args.probe)
            return
        rodar(driver, saida, args.limite, args.ordem, args.refazer,
              acelerado=not args.sem_aceleracao)
    finally:
        # No modo --anexar a janela e do usuario: fechar seria rude (e perderia
        # o clearance do Cloudflare que ele passou na mao).
        if not args.anexar:
            driver.quit()

    if args.capas:
        baixar_capas(saida, None)


if __name__ == "__main__":
    main()
