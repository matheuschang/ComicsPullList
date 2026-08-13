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

## Próximo

PWA + **push**: no dia em que uma edição de um título seguido lança, notificar às
8h do horário local (service worker + `push_subscriptions` no Supabase + Edge
Function agendada por `pg_cron`).
