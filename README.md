# Pull List

Catálogo de séries DC e Marvel em publicação, com acompanhamento de quais
edições você já leu e aviso quando sai uma nova.

Site estático: os dados são JSON gerados fora do ar e commitados junto. Não há
servidor para manter — dá para publicar no GitHub Pages como está.

**Fase 1.** Catálogo navegável, seguir títulos, marcar edições lidas, aba
Novidades. Estado salvo no navegador. Sem login, sem push.

**Fase 2 (aqui).** Fonte trocada para a Metron: 2025 inteiro, séries completas,
edições futuras já anunciadas. Falta rodar contra a API de verdade.

## Rodar

Com os CSVs (fase 1, sem credencial nenhuma):

```bash
python ingest/from_csv.py
```

Com a Metron (fase 2 — veja a seção abaixo antes):

```bash
python ingest/from_metron.py
```

E o site:

```bash
python serve.py
```

Abre em <http://localhost:8765>. Tudo usa só a biblioteca padrão do Python —
não há nada para instalar.

Abrir `web/index.html` direto pelo Explorer **não funciona**: o `fetch` dos
JSONs é bloqueado em `file://`. Tem que ser pelo servidor.

## Metron

A Metron autentica com **usuário e senha da conta do site**, via HTTP Basic —
não existe token. Duas consequências:

- Vale a pena **criar uma conta dedicada** para o script, em vez de usar a sua.
- A credencial vai num `.env` na raiz (já ignorado pelo git), nunca no código:

```
METRON_USER=usuario
METRON_PASS=senha
```

Antes de qualquer coisa, o autoteste — ele gasta poucas requisições e mostra
campo por campo o que a API devolveu:

```bash
python ingest/metron.py --selftest
```

Ele confere a credencial, descobre os ids de DC e Marvel (nunca chute esses
números), e imprime os campos de uma série e de uma edição. Se algum sair
`[!!] AUSENTE`, o nome mudou na API e o `from_metron.py` precisa de ajuste.

> **Este cliente ainda não rodou contra a API de verdade.** Ele foi escrito com
> o metron.cloud fora do ar — o host resolve mas não responde. Os nomes de campo
> vêm da documentação, não de uma resposta observada. É exatamente para isso que
> o `--selftest` existe: rode-o primeiro e o desvio aparece de cara, em vez de
> virar catálogo torto.

Respostas ficam em `.cache/metron/`, então uma execução interrompida continua de
onde parou sem custo. Use `--sem-cache` para reconferir preços e datas do zero.

A Metron limita a 30 requisições por minuto; o `--delay` padrão de 2,2s respeita
isso, e o cliente trata `429` obedecendo ao `Retry-After`.

## Como está montado

```
ingest/common.py      regras de catálogo e a escrita dos JSONs
ingest/from_csv.py    fonte 1 — os CSVs semanais do comics_releases
ingest/metron.py      cliente da API: auth, paginação, cache, autoteste
ingest/from_metron.py fonte 2 — monta o catálogo a partir da Metron
web/                  o site inteiro, publicável como está
  data/               gerado pelo ingest; não editar à mão
  store.js            estado do usuário (hoje localStorage, depois Supabase)
serve.py              servidor de desenvolvimento
```

A separação que importa: **`common.py` decide o que é catálogo e como gravar;
as fontes só sabem buscar dados.** As duas escrevem o mesmo formato em
`web/data/`, e o site não sabe de qual delas veio — só lê `meta.fonte` para
ajustar o texto do aviso. Trocar de fonte é trocar de comando.

Do mesmo jeito, todas as funções de `store.js` são `async` mesmo sem precisar
ser hoje — é o que deixa trocar localStorage por Supabase sem tocar no `app.js`.

## Dados

**Com a Metron** (`from_metron.py`): pega toda edição DC ou Marvel com data de
venda entre `--desde` (padrão 01/01/2025) e hoje + 180 dias, junta as séries
dessas edições e então busca **todas** as edições de cada uma — inclusive as
anteriores a 2025. É isso que permite marcar a série inteira como lida.

Os 180 dias à frente entram porque as solicitações saem com ~3 meses de
antecedência: é o que alimenta "próxima edição".

Coletâneas ficam de fora (`Trade Paperback`, `Omnibus`, `Hard Cover`, `Graphic
Novel`, `Digital Chapter`) — quem segue *Batman* quer a #167, não o encadernado
que reúne 1 a 6. O filtro é por **exclusão**, não por lista de permitidos, para
que um `series_type` novo entre no catálogo em vez de sumir calado.

Os links `[LER]` do getcomics não existem na Metron. O `from_metron.py` os
reaproveita dos CSVs do `comics_releases`, casando série e número.

**Com os CSVs** (`from_csv.py`, fase 1): são **8 semanas** (12/08 a 10/10/2025),
das quais saem 173 séries DC e Marvel e 249 edições. Duas limitações que a
interface admite na tela, em vez de esconder:

- **A série nunca vem completa.** Se *Absolute Batman* teve 13 edições, o app
  conhece as 3 que caíram na janela.
- **`status` é relativo à data de referência dos dados** (10/10/2025), não a
  hoje. Medindo contra hoje, um arquivo parado em 2025 marcaria o catálogo
  inteiro como encerrado.

As duas somem com a Metron. É o motivo de a fase 2 existir.

### O que é descartado

| Motivo | Quantos |
|---|---|
| `outra-editora` | 256 — Image, IDW, Dark Horse, Dynamite, BOOM! |
| `digital-vertical` | 31 — os *Infinity Comic* da Marvel |
| `reimpressao` | 15 — `Facsimile Edition`, `3rd Printing` |

O `Facsimile Edition` aparece dos dois lados do `#` (`Civil War #1 Facsimile
Edition 2025` e `Marvel / DC: Spider-Boy Facsimile Edition #1`), por isso o
filtro olha o título inteiro. Já `variant` só é filtrado **depois** do número:
a Marvel publicou uma série chamada *The Variants* em 2022.

### O id de série muda quando a fonte muda

Com os CSVs o id é um slug do nome (`marvel_amazing-spider-man`), normalizado
para juntar as grafias que a LOCG alterna — *The Amazing Spider-Man* e *Amazing
Spider-Man* caem na mesma série. Mas slug não distingue relançamento: `Batman #1`
de 2016 e de 2025 colidem. Com a Metron o id vira `metron_<id>`, estável e
imune a isso.

**O que você já marcou não migra sozinho.** Os ids mudam de forma, então o que
está no navegador aponta para séries que deixaram de existir no catálogo — vira
lixo silencioso. Se já tiver marcado bastante coisa antes de ligar a Metron,
exporte pelo botão **Dados** antes de trocar; a reconciliação por nome é fácil
de escrever, mas ainda não existe.

## O que a fase 2 muda na tela

Tudo isto já está no `app.js`, testado contra um catálogo no formato da Metron, e
liga sozinho quando `meta.fonte` for `metron`:

- **Filtro "só em publicação"**, que só faz sentido com `status` confiável.
- **`9 de 14 edições`** — quando a Metron diz que a série tem mais do que
  conhecemos, a tela admite em vez de deixar você supor que a lista fechou.
- **Próxima edição anunciada**, na ficha e como fita no pé da capa do cartão.
- **Edições futuras aparecem, mas não dão para marcar** (checkbox desabilitada,
  etiqueta "a sair") e não entram em "marcar todas". Marcar como lida algo que
  nem saiu é o tipo de estado que depois faz perder uma edição de vista.
- **Novidades só conta o que já saiu.** Solicitação de daqui a 4 meses fica na
  ficha da série; se entrasse na aba, o aviso dispararia meses antes.
- **Nome com o ano** (`Batman (2025)`), que é o que separa relançamento de
  série antiga agora que o id vem da Metron.

## Próximas fases

| | O que entra | Precisa de |
|---|---|---|
| 3 | Login, sincronia entre dispositivos | conta Supabase |
| 4 | PWA + push no celular | fase 3 |

A atualização semanal automática (GitHub Action rodando o `from_metron.py` e
commitando o `web/data/`) fica para quando o repo existir — não faz sentido
antes.

O `comics_releases` não é tocado por nada disto — ele continua mandando o
e-mail semanal como sempre.

## Antes de publicar no GitHub

- [ ] Revogar a senha de app do Gmail que está em texto puro no
      `comics_releases/utils/emails.py` (`myaccount.google.com/apppasswords`).
      Ela não está neste repositório, mas está num projeto vizinho prestes a
      virar público.
- [ ] Repo **público** — Pages em repo privado exige GitHub Pro.
