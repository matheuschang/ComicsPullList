# Pull List

Catálogo de séries **DC e Marvel** em publicação, com login, acompanhamento de
edições lidas, aba de novidades e dashboard. Site estático (o catálogo é JSON
gerado por raspagem e servido junto), com uma camada de usuários no Supabase.

- **Catálogo** navegável, com busca, filtros (editora, em publicação) e ordenação.
- **Seguir** títulos e **marcar edições lidas** — sincronizado entre dispositivos.
- **Novidades**: lançamentos das séries que você segue, agrupados por semana.
- **Dashboard**: visão geral do catálogo (edições/mês, ranking) e da sua coleção
  (progresso, gasto estimado, próximos lançamentos), com janela de tempo.
- **Login por conta** (Supabase Auth) e **painel admin** (criar/bloquear/deletar
  usuário, resetar senha). Cada usuário só enxerga a própria coleção (RLS).

## Como está montado

```
ingest/common.py        regras de catálogo + escrita dos JSONs (o contrato)
ingest/from_locg.py     raspagem do League of Comics Geeks (a fonte de dados)
ingest/stats.py         pré-computa web/data/stats.json para o dashboard
web/                    o site inteiro (SPA estática), publicável como está
  data/                 catálogo gerado (series.json, meta.json, issues/, stats.json)
  app.js                a aplicação; store.js = estado do usuário (Supabase)
  supabaseClient.js     cliente Supabase (a anon key é pública de propósito)
supabase/functions/     Edge Functions (admin-users: gestão de usuários)
serve.py                servidor de desenvolvimento local
atualizar_catalogo.bat  atualização semanal do catálogo (raspa + commit + push)
.github/workflows/      deploy do site no GitHub Pages
```

A separação central: **`common.py` decide o que é catálogo e como gravar; a
raspagem só busca dados.** O site lê `web/data/` e não sabe de onde veio.

## Rodar localmente

```bash
python serve.py
```

Abre em <http://localhost:8765>. Abrir `web/index.html` direto **não funciona**
(o `fetch` dos JSONs é bloqueado em `file://`; e o Supabase precisa de uma origem
HTTP). O site usa só a stdlib do Python; a raspagem precisa de `selenium`.

## Os dados (raspagem da LOCG)

O League of Comics Geeks fica atrás do **Cloudflare**, que barra headless/bots.
Por isso a raspagem roda no **seu Chrome**, no modo *anexar*: você abre o Chrome
uma vez, passa o desafio, e o script se conecta nessa sessão.

```bash
# 1. abra um Chrome de depuração e passe o Cloudflare em leagueofcomicgeeks.com:
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\Users\<você>\chrome-locg"

# 2. raspagem completa (o ano + back-fill das séries que começaram antes):
python ingest/from_locg.py --anexar 127.0.0.1:9222 --completo

# 3. gere os agregados do dashboard:
python ingest/stats.py
```

Regras do catálogo: só **DC e Marvel**, só **Regular Issue e Annual** (variantes,
reimpressões, coletâneas, webtoons digitais e previews ficam de fora). O filtro
de formato é aplicado na própria barra lateral da LOCG; o resto, em `common.py`.

Modos úteis do `from_locg.py`:

- `--completo` — raspagem cheia (semanas do ano + série completa de cada título).
- `--atualizar` — **incremental**: raspa as últimas 4 semanas + 1 à frente
  (`--atras`/`--frente`) e faz *merge* no catálogo, sem re-raspar o ano. É o que
  o `.bat` semanal usa.
- `--reparar` — re-raspa só as séries que ficaram cortadas (edições faltando).
- `--limite N`, `--semanas N`, `--sem-cache` — para testes. O cache fica em
  `.cache/locg/` (um crash não perde o que já foi raspado).
- `--enriquecer` — visita a página de cada edição e grava sinopse, criadores e
  personagens no catálogo (é o que a ficha rica da edição no site usa).

## A base de fichas (cadastro manual)

`ingest/fichas_locg.py` é um scraper **paralelo**, para um propósito diferente do
site: gerar uma planilha que preenche um formulário de cadastro de quadrinhos
(título, título original, tipo, número/volume, ano, editora, país, páginas,
roteirista, artista, descrição, capa).

Ela **deriva do catálogo do site**: a lista de trabalho sai de
`web/data/issues/*.json`, então o escopo é o mesmo (DC/Marvel, regular issues +
annuals, mesma janela) sem re-descobrir nada. Por isso a ordem é sempre: o site
atualiza primeiro, a base de fichas depois.

```bash
# histórico (~2700 edições) — em lotes, é retomável
python ingest/fichas_locg.py --anexar 127.0.0.1:9222 --limite 300

# futuro (semanal, depois do --atualizar) — pega só os links novos
python ingest/fichas_locg.py --anexar 127.0.0.1:9222

# capas grandes, uma por edição (sem browser, direto do S3)
python ingest/fichas_locg.py --so-capas

# calibrar os seletores de páginas/formato numa edição
python ingest/fichas_locg.py --anexar 127.0.0.1:9222 --probe <url da edição>
```

Sai em `fichas/` (**fora do repo**, veja o `.gitignore` — o repo é público e a
pasta de capas passa de meio giga): `fichas.json` é a base canônica,
`fichas.csv` é o entregável (UTF-8 com BOM, abre no Excel) e `capas/` tem uma
capa por edição, nomeada com o id da LOCG para nunca colidir.

Incremental e idempotente: a base é chaveada pelo **link** da edição, então
rodar de novo só visita o que falta. O `.bat` semanal atualiza as duas bases.

## Deploy

**Site → GitHub Pages** (estático, grátis, sempre no ar). O workflow
`.github/workflows/pages.yml` publica a pasta `web/` a cada push (em
**Settings → Pages**, use *Source: GitHub Actions*; o repo precisa ser público).

**Atualização semanal.** Como o Cloudflare impede rodar a raspagem em CI grátis,
o `atualizar_catalogo.bat` roda **na sua máquina** (agende no Agendador de
Tarefas): abre o Chrome de depuração, roda `--atualizar`, regenera o
`stats.json`, e faz `commit`/`push` — o Pages republica e todos os clientes
pegam o catálogo novo. Se o Cloudflare aparecer, passe o desafio na janela.

**Usuários → Supabase** (free tier). O que precisa existir no projeto:

- Tabelas `profiles`, `follows`, `reads` com **RLS** (cada um só vê o seu; admin
  lê tudo). Trigger que cria o `profiles` ao criar usuário.
- Edge Function `admin-users` (gestão de usuários; usa a *service role key*, que
  fica só no ambiente da função — nunca no repo). No deploy dela, deixe
  **"Verify JWT" desligado** (ela valida o papel por dentro).
- Em `web/supabaseClient.js`, a **URL do projeto** e a **anon key** (públicas).

O catálogo (séries/edições) é estático; só o estado do usuário (segue/lidas) vive
no Supabase. Auth por email + senha; contas são criadas pelo admin.


