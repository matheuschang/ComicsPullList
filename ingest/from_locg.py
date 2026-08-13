"""Monta o catalogo raspando o League of Comics Geeks (LOCG).

Substitui a fase 2 da Metron (que nunca funcionou). O objetivo e o mesmo: um
catalogo com TODAS as edicoes de cada serie DC/Marvel -- inclusive as anteriores
a data de corte -- para dar pra marcar a serie inteira como lida.

Requisitos combinados:
  - todo titulo DC e Marvel com edicao a partir de --desde (padrao 2026-01-01);
  - se a serie teve a #1 antes disso, buscar as edicoes anteriores tambem;
  - so DC e Marvel;
  - so "regular issue" e "annual" (fora coletaneas, variantes, reimpressoes).

Como funciona, em duas etapas:
  1. DESCOBERTA -- percorre as paginas semanais de lancamento
     (leagueofcomicgeeks.com/comics/new-comics/AAAA/MM/DD) de --desde ate o fim do
     mes seguinte ao atual, junta as edicoes DC/Marvel e delas extrai as SERIES.
  2. SERIE COMPLETA -- para cada serie, abre a pagina da serie e raspa TODAS as
     suas edicoes, inclusive as de antes de --desde.

O scraper roda no SEU Chrome local (o Cloudflare da LOCG barra headless/bots).

CLOUDFLARE EM LOOP? Um Chrome controlado pelo Selenium as vezes nunca passa o
desafio "managed" -- re-desafia sem parar. A saida certeira e o modo --anexar:
voce abre o Chrome na mao, passa o Cloudflare uma vez, e o script se conecta
nessa sessao ja liberada.

    # 1. feche TODO o Chrome. Depois, num Prompt de Comando, abra um Chrome com
    #    porta de depuracao e um perfil proprio (ajuste o caminho se preciso):
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\\Users\\mathe\\chrome-locg"

    # 2. NESSA janela, entre em https://leagueofcomicgeeks.com e passe o Cloudflare.
    #    Deixe a janela aberta.

    # 3. rode o script anexando nessa sessao (funciona com qualquer comando abaixo):
    python ingest/from_locg.py --anexar 127.0.0.1:9222 --probe-semana 2026/01/07

IMPORTANTE -- rode os probes primeiro. A pagina semanal ja foi validada pelo
scraper do comics_releases, mas a PAGINA DE SERIE ainda nao: os seletores dela
sao um palpite ate voce rodar o probe e a gente conferir o HTML de verdade.

    # 1. confere que a pagina semanal ainda casa os seletores em 2026:
    python ingest/from_locg.py --probe-semana 2026/01/07

    # 2. despeja a estrutura da pagina de serie (pegue uma URL de edicao da
    #    saida do probe acima, ou qualquer /comic/<id>/<slug> da LOCG):
    python ingest/from_locg.py --probe-serie https://leagueofcomicgeeks.com/comic/2862456/action-comics-1089

    # 3. so depois de os seletores baterem, a raspagem completa:
    python ingest/from_locg.py
    python ingest/from_locg.py --desde 2026-01-01 --limite 5   # teste rapido
"""

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import (JANELA_EM_PUBLICACAO, chave_serie, escrever_catalogo,
                    motivo_descarte, ordem_numero, parse_titulo)

RAIZ = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://leagueofcomicgeeks.com"

# Cache em disco por pagina raspada: um crash no meio do run nao joga fora o que
# ja foi buscado -- rodar de novo pega do cache e continua de onde parou. Tambem
# deixa a atualizacao semanal barata. Ligado por padrao; --sem-cache ignora.
PASTA_CACHE = RAIZ / ".cache" / "locg"
USAR_CACHE = True


def cache_ler(chave):
    if not USAR_CACHE:
        return None
    arq = PASTA_CACHE / f"{hashlib.sha256(chave.encode()).hexdigest()[:24]}.json"
    if arq.exists():
        try:
            return json.loads(arq.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
    return None


def cache_gravar(chave, dados):
    if not USAR_CACHE:
        return
    PASTA_CACHE.mkdir(parents=True, exist_ok=True)
    arq = PASTA_CACHE / f"{hashlib.sha256(chave.encode()).hexdigest()[:24]}.json"
    arq.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

# Texto do publisher na LOCG -> codigo curto usado no site.
EDITORAS_LOCG = {"DC Comics": "dc", "Marvel Comics": "marvel"}

# Card de edicao PRINCIPAL. As variantes/reprints sao `li.issue` com data-parent
# apontando para o pai; a edicao real tem data-parent="0". Na pagina de serie
# elas vem todas soltas (nao ha a barra de filtro da pagina semanal), entao esse
# recorte e o que impede a serie de virar centenas de cards.
CARD_CSS = "li.issue[data-parent='0']"

def fim_do_mes_seguinte(hoje):
    """Ultimo dia do mes seguinte ao de `hoje` -- ate onde a raspagem vai.

    Ex.: em 13/08/2026 devolve 30/09/2026. Cobre as solicitacoes do proximo mes
    sem varrer meses de futuro que ainda nem foram anunciados.
    """
    # Primeiro dia do mes +2; menos um dia = ultimo dia do mes +1.
    m = hoje.month + 2
    ano, mes = hoje.year + (m - 1) // 12, (m - 1) % 12 + 1
    return dt.date(ano, mes, 1) - dt.timedelta(days=1)

# So Regular Issue e Annual entram. A LOCG rotula o formato de cada edicao;
# quando esse rotulo aparece no card usamos ele. Confirme os textos exatos com
# --probe-semana (linha "fmt=" de cada card).
FORMATOS_ACEITOS = ("regular", "annual")
FORMATOS_EXCLUIDOS = (
    "trade paperback", "tpb", "hardcover", "hard cover", "omnibus",
    "graphic novel", "digital", "collected", "box set", "deluxe",
    "compendium", "epic collection", "one-shot", "one shot",
)

# Quando o card NAO traz rotulo de formato, caimos numa heuristica pelo titulo:
# coletaneas e edicoes especiais que escaparam do parse "#" ainda somem por aqui.
COLECAO_TITULO = (
    "tpb", "omnibus", "hardcover", "hard cover", " vol.", " volume ",
    "collection", "collected", "compendium", "box set", "deluxe edition",
    "epic collection", "director's cut", "ashcan",
)


# --------------------------------------------------------------------- driver

def criar_driver(chromedriver, headless, anexar="", perfil=""):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()

    # MODO ANEXAR: conecta num Chrome que VOCE ja abriu e onde passou o Cloudflare
    # na mao. Como a sessao e um Chrome de verdade que voce pilotou, o Cloudflare
    # nao re-desafia -- some o loop de verificacao. Veja o cabecalho do arquivo.
    if anexar:
        options.add_experimental_option("debuggerAddress", anexar)
        # Sem Service com caminho fixo: o Selenium Manager baixa o driver certo.
        return webdriver.Chrome(options=options)

    # LANCAMENTO NORMAL: disfarca a automacao. Isso reduz o desafio, mas o
    # Cloudflare "managed" ainda pode entrar em loop num Chrome controlado --
    # se acontecer, use o modo --anexar.
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    # Perfil persistente: uma vez que o Cloudflare libera, o cookie de clearance
    # fica salvo aqui e as proximas execucoes ja entram passadas.
    if perfil:
        options.add_argument(f"--user-data-dir={perfil}")
    if headless:
        # Aviso: headless quase sempre cai no desafio do Cloudflare.
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1400,1000")

    # Sem --chromedriver, o Selenium Manager (embutido no Selenium 4.6+) baixa o
    # driver que casa com o Chrome instalado. O chromedriver local do
    # comics_releases fica preso a uma versao e quebra a cada update do Chrome.
    service = Service(str(chromedriver)) if chromedriver else Service()
    driver = webdriver.Chrome(service=service, options=options)
    # Apaga o sinal mais obvio de automacao antes de qualquer pagina carregar.
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


def aguardar_conteudo(driver, css, timeout=120):
    """Espera `css` aparecer, dando tempo do desafio do Cloudflare passar.

    O Turnstile da LOCG ("Executando verificacao de seguranca") reescreve a
    pagina alguns segundos depois de carregar. Raspar antes disso pega a pagina
    do desafio, nao o conteudo. Se o desafio exigir um clique, resolva na janela
    do Chrome que esta aberta -- este loop continua esperando ate o timeout.
    Devolve True se o conteudo apareceu, False se estourou o tempo.
    """
    from selenium.webdriver.common.by import By
    inicio = time.monotonic()
    fim = inicio + timeout
    prox_aviso = 0
    while time.monotonic() < fim:
        if driver.find_elements(By.CSS_SELECTOR, css):
            return True
        s = int(time.monotonic() - inicio)
        if s >= prox_aviso:
            titulo = (driver.title or "")[:45]
            cloudflare = "momento" in titulo.lower() or "just a moment" in titulo.lower()
            dica = "  <- Cloudflare; passe o desafio nessa janela do Chrome" if cloudflare else ""
            print(f"  ... esperando '{css}' ({s}/{timeout}s) titulo={titulo!r}{dica}")
            prox_aviso = s + 10
        time.sleep(2)
    return False


def rolar_ate_o_fim(driver, pausa=2.0, estaveis=4, max_rolagens=150):
    """Rola ate a lista de `li.issue` parar de crescer.

    Conta os cards (nao a altura da pagina: anuncios mexem na altura e enganam) e
    so conclui quando a contagem fica igual por `estaveis` verificacoes seguidas.
    Isso sobrevive a um lote de lazy-load lento -- a causa de series virem cortadas
    (ex.: Absolute Batman so com #13+ em vez de #1). So parar na primeira altura
    estavel deixava series longas pela metade.
    """
    from selenium.webdriver.common.by import By
    anterior, repetido = -1, 0
    for _ in range(max_rolagens):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pausa)
        n = len(driver.find_elements(By.CSS_SELECTOR, "li.issue"))
        if n == anterior:
            repetido += 1
            if repetido >= estaveis:
                return
        else:
            anterior, repetido = n, 0


def _texto(el, css):
    from selenium.common.exceptions import NoSuchElementException
    from selenium.webdriver.common.by import By
    try:
        return el.find_element(By.CSS_SELECTOR, css).text.strip()
    except NoSuchElementException:
        return ""


def _attr(el, css, attr):
    from selenium.common.exceptions import NoSuchElementException
    from selenium.webdriver.common.by import By
    try:
        return (el.find_element(By.CSS_SELECTOR, css).get_attribute(attr) or "").strip()
    except NoSuchElementException:
        return ""


# ------------------------------------------------------- card de edicao (comum)

def card_para_dict(card):
    """Extrai os campos de um card de edicao (`li.issue`).

    E o mesmo componente na pagina semanal e (provavelmente) na pagina da serie,
    por isso o parser e um so. `data-date` e um timestamp unix em segundos.
    """
    publisher = _texto(card, "div.publisher")
    titulo = _texto(card, "div.title a")
    comic_url = _attr(card, "div.title a", "href") or _attr(card, "div.cover a", "href")
    data_txt = _texto(card, "div.details span.date")
    data_ts = _attr(card, "div.details span.date", "data-date")
    preco = _texto(card, "div.details span.price")
    # A capa real fica em data-src (lazy load); o src e um placeholder base64.
    capa = _attr(card, "div.cover img", "data-src") or _attr(card, "div.cover img", "src")
    return {
        "publisher": publisher,
        "titulo": titulo,
        "comic_url": comic_url,
        "data_txt": data_txt,
        "data_ts": data_ts,
        "preco": preco,
        "capa": capa if "s3.amazonaws" in capa else "",
        "formato": "",  # o card da LOCG nao expoe formato; o filtro e on-site
    }


def so_preco(txt):
    """'&nbsp;·&nbsp; $4.99' -> '4.99'. Sem cifrao vira ''."""
    txt = txt or ""
    return txt.split("$")[-1].strip() if "$" in txt else ""


def data_iso(card):
    """Converte o data-date (timestamp unix) do card em AAAA-MM-DD, ou ''.

    Usa epoch + timedelta em vez de utcfromtimestamp: este ultimo estoura com
    OSError no Windows para timestamps fora de faixa (a LOCG as vezes tem
    data-date lixo). timedelta cobre de ano 1 a 9999 e so estoura no absurdo.
    """
    ts = card.get("data_ts")
    if not ts:
        return ""
    try:
        segundos = int(ts)
    except (ValueError, TypeError):
        return ""
    try:
        return (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=segundos)).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def tipo_aceito(card, partes):
    """So Regular Issue e Annual.

    Se o card traz o rotulo de formato, ele decide. Senao, cai numa heuristica
    pelo titulo: o parse ja exigiu "#", e aqui derrubamos coletaneas/especiais
    que passaram mesmo assim. Anuais entram (o titulo costuma trazer "Annual").
    Devolve (ok, motivo) -- motivo so quando descarta, para o probe explicar.
    """
    f = (card.get("formato") or "").lower()
    if f:
        if any(x in f for x in FORMATOS_EXCLUIDOS):
            return False, f"formato:{f}"
        if any(x in f for x in FORMATOS_ACEITOS):
            return True, ""
        return False, f"formato:{f}"
    # Sem rotulo de formato no card -> heuristica pelo titulo.
    alvo = f" {partes['serie'].lower()} {partes['resto'].lower()} "
    if any(x in alvo for x in COLECAO_TITULO):
        return False, "titulo-colecao"
    return True, ""


# ------------------------------------------------------------ raspagem: semana

def quartas(desde, ate):
    """Quartas-feiras (dia de lancamento da LOCG) que cobrem [desde, ate].

    Comeca na quarta da SEMANA que contem `desde` (a semana da LOCG vai de quarta
    a terca), para nao perder edicoes dos primeiros dias do ano que caem na
    semana da virada. O piso exato por data fica no `rodar`, que descarta o que
    for anterior a `desde`.
    """
    d = desde - dt.timedelta(days=(desde.weekday() - 2) % 7)  # quarta <= desde
    while d <= ate:
        yield d
        d += dt.timedelta(days=7)


def url_semana(quarta):
    return f"{BASE}/comics/new-comics/{quarta:%Y/%m/%d}"


# Ajusta o filtro FORMAT da LOCG: deixa so Regular Issues + Annuals ligados.
# O card nao expoe formato, entao excluir Variants & Reprints (e coletaneas) so
# da pra fazer aqui. Clica no .option-name (o handler de selecao vive nele) e so
# quando o estado atual difere do desejado. Devolve o dict de acoes (vazio = nada
# mudou). Publishers ficam com o filtro em Python.
_JS_CONFIG_FILTROS = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const querer = {"Regular Issues": true, "Annuals": true,
                "Variants & Reprints": false, "Digital Chapters": false,
                "Trade Paperbacks": false};
const acoes = {};
for (const li of document.querySelectorAll('li.filter-options-formats')) {
  const n = li.querySelector('.option-name');
  if (!n) continue;
  const txt = norm(n.textContent);
  if (!(txt in querer)) continue;
  const sel = li.classList.contains('selected');
  if (sel !== querer[txt]) { n.click(); acoes[txt] = querer[txt] ? 'ligou' : 'desligou'; }
}
return acoes;
"""


def configurar_filtros(driver):
    """Garante Regular+Annual e nada de Variants/coletaneas. Idempotente."""
    try:
        return driver.execute_script(_JS_CONFIG_FILTROS) or {}
    except Exception as e:
        print(f"    [!!] nao consegui ajustar o filtro FORMAT: {e}")
        return {}


def raspar_semana(driver, url):
    """Cards de edicao de uma pagina semanal, ja com o filtro FORMAT aplicado."""
    from selenium.webdriver.common.by import By
    cache = cache_ler("semana:" + url)
    if cache is not None:
        return cache
    driver.get(url)
    if not aguardar_conteudo(driver, "li.issue"):
        return []
    acoes = configurar_filtros(driver)
    if acoes:
        print(f"    filtro FORMAT ajustado: {acoes}")
        time.sleep(3)  # o clique recarrega a lista via AJAX
        aguardar_conteudo(driver, "li.issue")
    rolar_ate_o_fim(driver)
    cards = [card_para_dict(c) for c in driver.find_elements(By.CSS_SELECTOR, CARD_CSS)]
    if cards:  # nao cacheia vazio (falha transiente do Cloudflare)
        cache_gravar("semana:" + url, cards)
    return cards


# ------------------------------------------------------------- raspagem: serie

def achar_url_serie(driver, comic_url):
    """Abre a pagina de uma edicao e devolve a URL da SERIE dela.

    O botao certo e o `a.series` ("Series") -- href /comics/series/<id>/<slug>.
    (Ha outros links /comics/series/ na pagina que levam a submeter edicao.)
    """
    from selenium.webdriver.common.by import By
    cache = cache_ler("urlserie:" + comic_url)
    if cache:
        return cache
    driver.get(comic_url)
    if not aguardar_conteudo(driver, "a.series[href*='/comics/series/']"):
        return ""
    for a in driver.find_elements(By.CSS_SELECTOR, "a.series[href*='/comics/series/']"):
        href = a.get_attribute("href") or ""
        if "/comics/series/" in href:
            cache_gravar("urlserie:" + comic_url, href)
            return href
    return ""


def raspar_serie(driver, url_serie):
    """TODAS as edicoes PRINCIPAIS de uma serie (variantes excluidas via CARD_CSS).

    A pagina de serie reusa o card `li.issue` da pagina semanal, mas lista tambem
    cada variante/reprint como um card -- por isso CARD_CSS filtra data-parent=0.
    """
    from selenium.webdriver.common.by import By
    # Chave "v2": o rolar_ate_o_fim antigo parava cedo e cacheava series cortadas.
    # Bump invalida so o cache de serie; semanas e links de serie continuam validos.
    cache = cache_ler("serie:v2:" + url_serie)
    if cache is not None:
        return cache
    driver.get(url_serie)
    if not aguardar_conteudo(driver, "li.issue"):
        return []
    rolar_ate_o_fim(driver)
    cards = [card_para_dict(c) for c in driver.find_elements(By.CSS_SELECTOR, CARD_CSS)]
    if cards:  # nao cacheia vazio (falha transiente do Cloudflare)
        cache_gravar("serie:v2:" + url_serie, cards)
    return cards


# --------------------------------------------------------------------- probes

# Os filtros da LOCG nao sao <input type=checkbox> -- sao controles customizados
# num drawer. Este JS acha, para cada texto de filtro, o menor elemento que
# contem exatamente aquele texto e devolve tag/classe/HTML dele e do pai. E o que
# revela como clicar cada filtro (casando pelo texto, nao por id chutado).
_JS_FILTROS = r"""
const alvos = arguments[0];
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const res = [];
for (const t of alvos) {
  let best = null;
  for (const el of document.querySelectorAll('*')) {
    const own = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent).join('');
    if (norm(own) === t) { best = el; break; }
  }
  if (best) {
    const p = best.parentElement;
    res.push({alvo: t, tag: best.tagName, cls: norm(best.className),
              html: best.outerHTML.slice(0, 260),
              paiTag: p ? p.tagName : '', paiCls: p ? norm(p.className) : '',
              paiHtml: p ? p.outerHTML.slice(0, 320) : ''});
  } else {
    res.push({alvo: t, tag: 'NAO-ACHOU'});
  }
}
return res;
"""

_ALVOS_FILTRO = ["Format", "Publishers", "Select All", "Regular Issues", "Annuals",
                 "Variants & Reprints", "Digital Chapters", "Trade Paperbacks",
                 "DC Comics", "Marvel Comics"]


def probe_filtros(driver, dia):
    """Despeja a estrutura da barra de filtros (FORMAT/PUBLISHERS) e do card."""
    from selenium.webdriver.common.by import By
    url = f"{BASE}/comics/new-comics/{dia}"
    print(f"abrindo {url}")
    driver.get(url)
    tem_cards = aguardar_conteudo(driver, "li.issue", timeout=90)

    # 1. Estrutura do card (finalmente): confirma os seletores internos.
    cards = driver.find_elements(By.CSS_SELECTOR, "li.issue")
    print(f"\n=== CARDS: {len(cards)} 'li.issue' ===")
    if cards:
        print("--- outerHTML do 1o card ---")
        print(cards[0].get_attribute("outerHTML")[:2200])
    else:
        print("[!!] 'li.issue' nao casou. Candidatos:")
        for css in ("div.issue", "[class*='issue']", ".comic-list li", "li[class*='comic']"):
            print(f"  {css!r:22} -> {len(driver.find_elements(By.CSS_SELECTOR, css))}")

    # 2. Estrutura dos filtros: como clicar cada um.
    print("\n=== FILTROS (barra lateral) ===")
    try:
        dados = driver.execute_script(_JS_FILTROS, _ALVOS_FILTRO)
    except Exception as e:
        dados = []
        print(f"[!!] JS falhou: {e}")
    for d in dados:
        if d.get("tag") == "NAO-ACHOU":
            print(f"\n[{d['alvo']}] NAO ACHOU esse texto na pagina")
            continue
        print(f"\n[{d['alvo']}] <{d['tag'].lower()} class='{d['cls']}'>")
        print(f"    el:  {d['html']}")
        print(f"    pai: <{d['paiTag'].lower()} class='{d['paiCls']}'> {d['paiHtml']}")

    print("\n=== cookies (nome=valor curto) ===")
    for c in driver.get_cookies():
        v = str(c.get("value", ""))
        if len(v) <= 70:
            print(f"  {c['name']} = {v}")
    print(f"\nURL atual: {driver.current_url}  (cards carregaram: {tem_cards})")
    print("\nCola tudo -- com o card + os filtros eu finalizo o scraper e o "
          "configurar_filtros.")


def probe_semana(driver, dia):
    """Roda a pagina semanal e mostra o que casou/foi filtrado, com o HTML cru
    do primeiro card para conferir os seletores."""
    from selenium.webdriver.common.by import By
    url = f"{BASE}/comics/new-comics/{dia}"
    print(f"abrindo {url}")
    driver.get(url)
    if not aguardar_conteudo(driver, "li.issue"):
        print("[!!] o conteudo nao apareceu em 120s -- o Cloudflare barrou. "
              "Rode de novo e, se aparecer um checkbox, clique nele na janela.")
        print(driver.find_element(By.TAG_NAME, "body").get_attribute("outerHTML")[:1500])
        return
    rolar_ate_o_fim(driver)

    cards = driver.find_elements(By.CSS_SELECTOR, "li.issue")
    print(f"\n{len(cards)} cards 'li.issue' encontrados")
    if not cards:
        print("[!!] nenhum card. O seletor 'li.issue' mudou, ou o Cloudflare "
              "barrou a pagina (rode sem --headless). HTML do topo:")
        print(driver.find_element(By.TAG_NAME, "body").get_attribute("outerHTML")[:1500])
        return

    print("\n--- outerHTML do 1o card (confira os seletores internos) ---")
    print(cards[0].get_attribute("outerHTML")[:2500])

    # Aplica os 3 filtros exatamente como a pipeline e mostra a decisao por card.
    print("\n--- os 3 filtros, card a card (DC/Marvel; primeiros 15) ---")
    dc_marvel = mantidos = mostrados = 0
    for c in cards:
        d = card_para_dict(c)
        if d["publisher"] not in EDITORAS_LOCG:
            continue
        dc_marvel += 1
        partes = parse_titulo(d["titulo"])
        if not partes:
            decisao = "DESCARTE: sem-numero"
        elif motivo_descarte(partes["serie"], partes["resto"]):
            decisao = "DESCARTE: " + motivo_descarte(partes["serie"], partes["resto"])
        else:
            ok, motivo = tipo_aceito(d, partes)
            decisao = "ok" if ok else "DESCARTE: " + motivo
        if decisao == "ok":
            mantidos += 1
        if mostrados < 15:
            mostrados += 1
            print(f"  [{d['publisher'][:6]:6}] {d['titulo'][:44]:44} "
                  f"data={data_iso(d) or d['data_txt'] or '?':10} "
                  f"fmt={d['formato'] or '?':10} {decisao}")
    print(f"\n{dc_marvel} cards DC/Marvel de {len(cards)} no total; "
          f"{mantidos} passariam os 3 filtros.")
    print("Se 'fmt=?' em todos, o card nao expoe o formato -- me diga e eu ajusto "
          "o seletor pelo outerHTML acima (ou o filtro de tipo fica so na heuristica).")


# Acha na pagina da edicao as abas/links cujo texto comeca com um dos alvos
# ("Series", "Issues"...). E a aba "Series" que lista as outras edicoes -- nao um
# link /comics/series/ (esse leva a submeter edicao). Devolve tag/classe/atributos.
_JS_ABAS = r"""
const alvos = arguments[0];
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const res = [];
for (const el of document.querySelectorAll("a, button, [role='tab'], [data-toggle]")) {
  const t = norm(el.textContent);
  for (const alvo of alvos) {
    if (t === alvo || t.startsWith(alvo + ' ') || t.startsWith(alvo + '(')) {
      res.push({alvo, texto: t.slice(0, 30), tag: el.tagName,
                cls: (el.className || '').toString().slice(0, 70),
                href: el.getAttribute('href') || '',
                toggle: el.getAttribute('data-toggle') || '',
                target: el.getAttribute('data-target') || el.getAttribute('aria-controls') || ''});
      break;
    }
  }
}
return res;
"""

# Clica na primeira aba cujo texto comeca com `alvo`. Devolve true se clicou.
_JS_CLICAR_ABA = r"""
const alvo = arguments[0];
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
for (const el of document.querySelectorAll("a, button, [role='tab'], [data-toggle]")) {
  if (norm(el.textContent).startsWith(alvo)) { el.click(); return true; }
}
return false;
"""


def probe_serie(driver, comic_url):
    """A partir de uma URL de edicao, acha a aba 'Series', clica e despeja a lista."""
    from selenium.webdriver.common.by import By
    print(f"abrindo a edicao {comic_url}")
    driver.get(comic_url)

    # Espera montar: procura em loop pelas abas Series/Issues (tambem espera o CF).
    abas = []
    inicio = time.monotonic()
    while time.monotonic() - inicio < 90:
        try:
            abas = driver.execute_script(_JS_ABAS, ["Series", "Issues", "All Issues", "Other Issues"])
        except Exception:
            abas = []
        if abas:
            break
        print(f"  ... esperando a pagina montar — titulo={(driver.title or '')[:40]!r}")
        time.sleep(3)

    print("\n=== abas/links candidatos (Series/Issues) ===")
    if not abas:
        print("[!!] nao achei 'Series'/'Issues'. Trecho do HTML pra eu procurar:")
        print(driver.find_element(By.TAG_NAME, "body").get_attribute("outerHTML")[:3000])
        return
    for a in abas:
        print(f"  <{a['tag'].lower()} class='{a['cls']}'> "
              f"toggle={a['toggle']!r} target={a['target']!r} href={a['href'][:40]!r} texto={a['texto']!r}")

    # Clica na aba 'Series' (ou 'Issues' se nao houver) e ve o que carrega.
    alvo = next((a for a in abas if a["alvo"] == "Series"), abas[0])
    print(f"\nclicando na aba {alvo['texto']!r}...")
    try:
        driver.execute_script(_JS_CLICAR_ABA, alvo["alvo"])
    except Exception as e:
        print(f"[!!] clique falhou: {e}")
    aguardar_conteudo(driver, "li.issue", timeout=30)

    # SEM rolar ainda: quantas edicoes ja vem no DOM (a lista da serie deve estar
    # num container proprio; o resto e feed global embaixo).
    antes = len(driver.find_elements(By.CSS_SELECTOR, "li.issue"))
    print(f"\n=== 'li.issue' antes de rolar: {antes} ===")

    # Arvore de ancestrais do 1o card: qual container tem SO as edicoes da serie.
    _JS_ANC = r"""
    const first = document.querySelector('li.issue');
    if (!first) return [];
    const res = []; let el = first.parentElement; let n = 0;
    while (el && n < 6) {
      res.push({tag: el.tagName, id: el.id || '',
                cls: (el.className || '').toString().slice(0, 55),
                issues: el.querySelectorAll('li.issue').length});
      el = el.parentElement; n++;
    }
    return res;
    """
    print("\n=== ancestrais do 1o card (qtd de li.issue em cada nivel) ===")
    for a in driver.execute_script(_JS_ANC):
        print(f"  <{a['tag'].lower()} id='{a['id']}' class='{a['cls']}'> -> {a['issues']} issues")

    rolar_ate_o_fim(driver)
    depois = len(driver.find_elements(By.CSS_SELECTOR, "li.issue"))
    print(f"\n=== 'li.issue' depois de rolar: {depois} (era {antes}) ===")

    cards = driver.find_elements(By.CSS_SELECTOR, "li.issue")
    if cards:
        print(f"\n--- 1o 'li.issue' da serie ---")
        print(cards[0].get_attribute("outerHTML")[:1600])


# ---------------------------------------------------------------- pipeline

def _edicao_do_card(c, partes):
    """Molda um card ja filtrado no formato de edicao gravado no JSON."""
    return {
        "numero": partes["numero"],
        "titulo": c["titulo"],
        "data": data_iso(c) or c.get("data_txt", ""),
        "preco": so_preco(c["preco"]),
        "capa": c["capa"],
        "link": c["comic_url"],
        "read_link": "",
    }


def montar_serie_dict(chave, nome, editora, edicoes, hoje):
    """Monta o registro de uma serie no series.json a partir das suas edicoes."""
    edicoes = sorted(edicoes, key=lambda e: (ordem_numero(e["numero"]), e["data"]))
    publicadas = [e for e in edicoes if e["data"] <= hoje.isoformat()]
    futuras = [e for e in edicoes if e["data"] > hoje.isoformat()]
    ultima = publicadas[-1] if publicadas else edicoes[-1]
    recente = dt.date.fromisoformat(ultima["data"]) >= hoje - JANELA_EM_PUBLICACAO
    return {
        "id": chave,  # "editora_slug"; estavel o bastante por ora
        "nome": nome,
        "editora": editora,
        "capa": next((e["capa"] for e in reversed(edicoes) if e["capa"]), ""),
        "edicoes_conhecidas": len(edicoes),
        "total_anunciado": None,   # so o back-fill/pagina de serie sabe o total
        "primeira_edicao": {"numero": edicoes[0]["numero"], "data": edicoes[0]["data"]},
        "ultima_edicao": {"numero": ultima["numero"], "data": ultima["data"]},
        "proxima_edicao": ({"numero": futuras[0]["numero"], "data": futuras[0]["data"]}
                           if futuras else None),
        "status": "em-publicacao" if (recente or futuras) else "sem-noticia",
        "fonte": "locg",
    }


def rodar(driver, desde, ate, limite, completo, semanas):
    """Monta o catalogo a partir dos lancamentos semanais de --desde ate ate.

    Modelo simples (padrao): cada serie fica com as edicoes que apareceram nas
    semanas raspadas -- ou seja, de --desde pra frente. As edicoes anteriores a
    --desde ficam faltando de proposito; entram depois, com --completo, que
    visita a pagina de cada serie (a parte que ainda vamos calibrar).

    Os tres filtros sao aplicados JA na descoberta, card a card:
      - editora em {DC Comics, Marvel Comics};
      - edicao valida (tem "#", nao e variante/facsimile/reimpressao);
      - tipo Regular Issue ou Annual.
    """
    hoje = dt.date.today()

    # Edicoes acumuladas por serie, chaveadas por numero para nao duplicar a
    # mesma edicao vista em duas semanas.
    serie_edicoes = defaultdict(dict)   # id -> {numero: edicao}
    serie_info = {}                     # id -> {nome, editora, url}
    descartes = Counter()

    todas = list(quartas(desde, ate))
    if semanas:
        todas = todas[:semanas]
        print(f"--semanas: so as primeiras {len(todas)} semanas")
    print(f"raspando {len(todas)} semanas de {desde} ate {ate}")
    for quarta in todas:
        vistos = 0
        try:
            cards = raspar_semana(driver, url_semana(quarta))
        except Exception as e:
            print(f"  {quarta}: [!!] semana pulada: {type(e).__name__}: {e}")
            continue
        for c in cards:
            codigo = EDITORAS_LOCG.get(c["publisher"])
            if not codigo:
                descartes["outra-editora"] += 1
                continue
            partes = parse_titulo(c["titulo"])
            if not partes:
                descartes["sem-numero"] += 1
                continue
            motivo = motivo_descarte(partes["serie"], partes["resto"])
            if motivo:
                descartes[motivo] += 1
                continue
            ok, motivo = tipo_aceito(c, partes)
            if not ok:
                descartes[motivo] += 1
                continue
            data = data_iso(c)
            if not data:
                descartes["sem-data"] += 1
                continue
            if data < desde.isoformat():
                descartes["antes-do-corte"] += 1  # semana da virada, edicao de 2025
                continue
            chave = chave_serie(codigo, partes["serie"])
            serie_edicoes[chave][partes["numero"]] = _edicao_do_card(c, partes)
            serie_info.setdefault(chave, {"nome": partes["serie"],
                                          "editora": codigo, "url": c["comic_url"]})
            vistos += 1
        print(f"  {quarta}: {vistos} edicoes DC/Marvel  (series ate agora: {len(serie_edicoes)})")

    if limite:
        manter = list(serie_edicoes)[:limite]
        serie_edicoes = {k: serie_edicoes[k] for k in manter}
        serie_info = {k: serie_info[k] for k in manter}
        print(f"  --limite: processando so {len(serie_edicoes)} series")

    # BACK-FILL opcional: completa com as edicoes anteriores a --desde as series
    # que comecaram antes e seguem publicando. So visita a pagina de serie de quem
    # AINDA NAO TEM a #1 na base -- se ja temos a #1, a serie comecou dentro da
    # janela e ja esta completa desde o inicio. So edicoes principais entram
    # (CARD_CSS filtra variantes na pagina de serie).
    if completo:
        faltam = {k: info for k, info in serie_info.items()
                  if "1" not in serie_edicoes[k]}
        print(f"\n--completo: {len(serie_info) - len(faltam)} series ja tem a #1 "
              f"(comecaram em {desde.year}); completando as {len(faltam)} que comecaram antes...")
        for n, (chave, info) in enumerate(sorted(faltam.items()), 1):
            try:
                url_serie = achar_url_serie(driver, info["url"])
                if not url_serie:
                    print(f"  {n}/{len(faltam)} {info['nome'][:30]:30} sem pagina de serie")
                    continue
                antes = len(serie_edicoes[chave])
                for c in raspar_serie(driver, url_serie):
                    partes = parse_titulo(c["titulo"])
                    if not partes or motivo_descarte(partes["serie"], partes["resto"]):
                        continue
                    # So edicoes DESTA serie: rejeita feed global vazando na pagina
                    # e o "X Annual" se misturando ao "X" (evita colisao de numero).
                    cod = EDITORAS_LOCG.get(c["publisher"])
                    if not cod or chave_serie(cod, partes["serie"]) != chave:
                        continue
                    d = data_iso(c)
                    if not tipo_aceito(c, partes)[0] or not d:
                        continue
                    if d > ate.isoformat():
                        continue  # respeita o horizonte (fim do mes seguinte)
                    # Nao sobrescreve o que veio da semana (dado melhor); so completa.
                    serie_edicoes[chave].setdefault(partes["numero"], _edicao_do_card(c, partes))
                ganhou = len(serie_edicoes[chave]) - antes
                print(f"  {n}/{len(faltam)} {info['nome'][:30]:30} {antes}->{len(serie_edicoes[chave])} (+{ganhou})")
            except Exception as e:
                # Uma serie problematica nao pode derrubar o run inteiro.
                print(f"  {n}/{len(faltam)} {info['nome'][:30]:30} [!!] pulada: {type(e).__name__}: {e}")

    # Monta o catalogo a partir do que foi acumulado.
    catalogo, por_serie = [], {}
    for chave, mapa in serie_edicoes.items():
        edicoes = sorted(mapa.values(), key=lambda e: (ordem_numero(e["numero"]), e["data"]))
        info = serie_info[chave]
        por_serie[chave] = edicoes
        catalogo.append(montar_serie_dict(chave, info["nome"], info["editora"], edicoes, hoje))

    catalogo.sort(key=lambda s: (s["editora"], s["nome"].lower()))
    meta = {
        "gerado_em": dt.datetime.now().replace(microsecond=0).isoformat(),
        "fonte": "locg",
        "referencia": hoje.isoformat(),
        "cobertura": {"de": desde.isoformat(), "ate": ate.isoformat()},
        "total_series": len(catalogo),
        "total_edicoes": sum(len(e) for e in por_serie.values()),
        "em_publicacao": sum(1 for s in catalogo if s["status"] == "em-publicacao"),
        "com_proxima": sum(1 for s in catalogo if s["proxima_edicao"]),
        "descartes": dict(descartes),
    }
    escrever_catalogo(catalogo, por_serie, meta, RAIZ / "web" / "data")
    print(f"\n{meta['total_series']} series, {meta['total_edicoes']} edicoes")
    print(f"em publicacao: {meta['em_publicacao']} | com proxima: {meta['com_proxima']}")
    print(f"descartes: {dict(descartes)}")


def reparar(driver, ate):
    """Re-raspa SO as series cortadas (count < maior numero) e regrava.

    Le o web/data que ja existe, acha as series onde faltam edicoes (o scroll
    truncou), re-raspa a pagina de serie de cada uma com o rolar corrigido e faz
    merge por uniao (nunca remove o que ja havia). Evita re-raspar o catalogo
    inteiro. Series de numeracao legada (nunca comecaram no #1) podem seguir com
    count < ultimo numero e ja estarem completas -- o merge so acrescenta o que a
    pagina de serie realmente tiver.
    """
    saida = RAIZ / "web" / "data"
    series = json.loads((saida / "series.json").read_text(encoding="utf-8"))
    meta = json.loads((saida / "meta.json").read_text(encoding="utf-8"))
    hoje = dt.date.today()

    por_serie = {}
    for x in series:
        d = json.loads((saida / "issues" / f"{x['id']}.json").read_text(encoding="utf-8"))
        por_serie[x["id"]] = d["edicoes"]

    def maior_numero(edicoes):
        nums = [int(e["numero"]) for e in edicoes if e["numero"].isdigit()]
        return max(nums) if nums else 0

    problema = [x for x in series if len(por_serie[x["id"]]) < maior_numero(por_serie[x["id"]])]
    print(f"{len(problema)} series com count < ultimo numero -- re-raspando so essas:")

    reparadas = 0
    for n, x in enumerate(problema, 1):
        chave = x["id"]
        edicoes = por_serie[chave]
        comic_url = next((e["link"] for e in reversed(edicoes) if e.get("link")), "")
        if not comic_url:
            print(f"  {n}/{len(problema)} {x['nome'][:30]:30} sem link -- pulada")
            continue
        try:
            url_serie = achar_url_serie(driver, comic_url)
            if not url_serie:
                print(f"  {n}/{len(problema)} {x['nome'][:30]:30} sem pagina de serie")
                continue
            mapa = {e["numero"]: e for e in edicoes}
            antes = len(mapa)
            for c in raspar_serie(driver, url_serie):
                partes = parse_titulo(c["titulo"])
                if not partes or motivo_descarte(partes["serie"], partes["resto"]):
                    continue
                cod = EDITORAS_LOCG.get(c["publisher"])
                if not cod or chave_serie(cod, partes["serie"]) != chave:
                    continue
                d = data_iso(c)
                if not tipo_aceito(c, partes)[0] or not d or d > ate.isoformat():
                    continue
                mapa.setdefault(partes["numero"], _edicao_do_card(c, partes))
            por_serie[chave] = list(mapa.values())
            ganhou = len(mapa) - antes
            if ganhou:
                reparadas += 1
            print(f"  {n}/{len(problema)} {x['nome'][:30]:30} {antes}->{len(mapa)} (+{ganhou})")
        except Exception as e:
            print(f"  {n}/{len(problema)} {x['nome'][:30]:30} [!!] {type(e).__name__}: {e}")

    # Reordena as edicoes de todas as series (as reparadas foram acumuladas fora
    # de ordem: existentes + as novas anexadas do mais novo pro mais velho).
    for k in por_serie:
        por_serie[k] = sorted(por_serie[k],
                              key=lambda e: (ordem_numero(e["numero"]), e["data"]))

    # Reconstroi o catalogo (todas as series; so as reparadas mudaram de edicoes).
    catalogo = [montar_serie_dict(x["id"], x["nome"], x["editora"],
                                  por_serie[x["id"]], hoje) for x in series]
    catalogo.sort(key=lambda s: (s["editora"], s["nome"].lower()))
    meta["gerado_em"] = dt.datetime.now().replace(microsecond=0).isoformat()
    meta["total_series"] = len(catalogo)
    meta["total_edicoes"] = sum(len(v) for v in por_serie.values())
    meta["em_publicacao"] = sum(1 for s in catalogo if s["status"] == "em-publicacao")
    meta["com_proxima"] = sum(1 for s in catalogo if s["proxima_edicao"])
    escrever_catalogo(catalogo, por_serie, meta, saida)
    print(f"\n{reparadas} series reparadas de {len(problema)}. "
          f"total: {meta['total_series']} series, {meta['total_edicoes']} edicoes")


def atualizar(driver, atras, frente):
    """Atualizacao semanal incremental: raspa as ultimas `atras` semanas e as
    proximas `frente`, e faz MERGE no catalogo que ja existe -- adiciona edicoes
    novas, atualiza preco/data/capa das existentes, e cadastra serie nova que
    apareceu na janela. NAO re-raspa o ano nem toca no historico ja coletado.

    E o que o .bat semanal chama. Rapido: so a janela recente.
    """
    saida = RAIZ / "web" / "data"
    series = json.loads((saida / "series.json").read_text(encoding="utf-8"))
    meta = json.loads((saida / "meta.json").read_text(encoding="utf-8"))
    hoje = dt.date.today()

    # Carrega o catalogo atual; edicoes por numero para dar upsert.
    por_serie = {}
    serie_info = {}
    for s in series:
        edicoes = json.loads((saida / "issues" / f"{s['id']}.json").read_text(encoding="utf-8")).get("edicoes", [])
        por_serie[s["id"]] = {e["numero"]: e for e in edicoes}
        serie_info[s["id"]] = {"nome": s["nome"], "editora": s["editora"]}

    desde = hoje - dt.timedelta(weeks=atras)
    ate = hoje + dt.timedelta(weeks=frente)
    print(f"atualizacao incremental: {desde} a {ate} (janela de {atras} sem atras + {frente} a frente)")

    vistos = 0
    novas_series = 0
    for quarta in quartas(desde, ate):
        for c in raspar_semana(driver, url_semana(quarta)):
            codigo = EDITORAS_LOCG.get(c["publisher"])
            if not codigo:
                continue
            partes = parse_titulo(c["titulo"])
            if not partes or motivo_descarte(partes["serie"], partes["resto"]):
                continue
            if not tipo_aceito(c, partes)[0] or not data_iso(c):
                continue
            chave = chave_serie(codigo, partes["serie"])
            if chave not in por_serie:
                por_serie[chave] = {}
                serie_info[chave] = {"nome": partes["serie"], "editora": codigo}
                novas_series += 1
            por_serie[chave][partes["numero"]] = _edicao_do_card(c, partes)
            vistos += 1
        print(f"  {quarta}: {vistos} edicoes na janela ({len(por_serie)} series, +{novas_series} novas)")

    # Reconstroi o catalogo com o merge.
    catalogo, saida_por_serie = [], {}
    for chave, mapa in por_serie.items():
        edicoes = sorted(mapa.values(), key=lambda e: (ordem_numero(e["numero"]), e["data"]))
        saida_por_serie[chave] = edicoes
        info = serie_info[chave]
        catalogo.append(montar_serie_dict(chave, info["nome"], info["editora"], edicoes, hoje))
    catalogo.sort(key=lambda s: (s["editora"], s["nome"].lower()))

    meta["gerado_em"] = dt.datetime.now().replace(microsecond=0).isoformat()
    meta["referencia"] = hoje.isoformat()   # o app usa isto como "hoje"
    meta["fonte"] = "locg"
    meta.setdefault("cobertura", {})["ate"] = ate.isoformat()
    meta["total_series"] = len(catalogo)
    meta["total_edicoes"] = sum(len(v) for v in saida_por_serie.values())
    meta["em_publicacao"] = sum(1 for s in catalogo if s["status"] == "em-publicacao")
    meta["com_proxima"] = sum(1 for s in catalogo if s["proxima_edicao"])
    escrever_catalogo(catalogo, saida_por_serie, meta, saida)
    print(f"\natualizado: {meta['total_series']} series, {meta['total_edicoes']} edicoes "
          f"(+{novas_series} series novas nesta janela)")


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe-semana", metavar="AAAA/MM/DD",
                    help="despeja a estrutura de uma pagina semanal e sai")
    ap.add_argument("--probe-filtros", metavar="AAAA/MM/DD",
                    help="lista os checkboxes de FORMAT/PUBLISHERS da barra lateral")
    ap.add_argument("--probe-serie", metavar="URL_EDICAO",
                    help="a partir de uma URL de edicao, despeja a estrutura da serie")
    ap.add_argument("--desde", default="2026-01-01", help="inicio da janela (AAAA-MM-DD)")
    ap.add_argument("--completo", action="store_true",
                    help="tambem busca as edicoes anteriores a --desde na pagina de "
                         "cada serie (fase seguinte; seletores ainda a calibrar)")
    ap.add_argument("--reparar", action="store_true",
                    help="re-raspa SO as series cortadas (count < ultimo numero) do "
                         "web/data atual e regrava -- sem re-raspar tudo")
    ap.add_argument("--atualizar", action="store_true",
                    help="atualizacao semanal: raspa a janela recente e faz merge no "
                         "catalogo existente (nao re-raspa o ano). Usa --atras/--frente")
    ap.add_argument("--atras", type=int, default=4, help="semanas para tras na atualizacao (padrao 4)")
    ap.add_argument("--frente", type=int, default=1, help="semanas para frente na atualizacao (padrao 1)")
    ap.add_argument("--limite", type=int, help="mantem so N series (teste rapido)")
    ap.add_argument("--semanas", type=int, help="raspa so as N primeiras semanas (teste rapido)")
    ap.add_argument("--sem-cache", action="store_true",
                    help="ignora o cache em disco (.cache/locg) e raspa tudo do zero")
    ap.add_argument("--chromedriver", default="",
                    help="caminho do chromedriver; vazio = Selenium Manager baixa "
                         "o que casa com o seu Chrome (recomendado)")
    ap.add_argument("--anexar", default="", metavar="HOST:PORTA",
                    help="conecta num Chrome ja aberto (ex.: 127.0.0.1:9222) onde "
                         "voce passou o Cloudflare na mao -- veja o cabecalho")
    ap.add_argument("--perfil", default="", metavar="DIR",
                    help="pasta de perfil persistente (guarda o cookie do Cloudflare "
                         "entre execucoes)")
    ap.add_argument("--headless", action="store_true",
                    help="sem janela (cuidado: quase sempre cai no Cloudflare)")
    args = ap.parse_args()

    global USAR_CACHE
    USAR_CACHE = not args.sem_cache

    print(f"criando driver ({'anexando em '+args.anexar if args.anexar else 'lancando Chrome'})...")
    driver = criar_driver(args.chromedriver, args.headless, args.anexar, args.perfil)
    try:
        try:
            print(f"driver conectado: {len(driver.window_handles)} aba(s); "
                  f"url atual: {driver.current_url}")
        except Exception as e:
            print(f"[!!] driver criado mas nao respondeu: {e}")
        if args.probe_filtros:
            return probe_filtros(driver, args.probe_filtros)
        if args.probe_semana:
            return probe_semana(driver, args.probe_semana)
        if args.probe_serie:
            return probe_serie(driver, args.probe_serie)
        if args.reparar:
            return reparar(driver, fim_do_mes_seguinte(dt.date.today()))
        if args.atualizar:
            return atualizar(driver, args.atras, args.frente)
        desde = dt.date.fromisoformat(args.desde)
        ate = fim_do_mes_seguinte(dt.date.today())
        rodar(driver, desde, ate, args.limite, args.completo, args.semanas)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
