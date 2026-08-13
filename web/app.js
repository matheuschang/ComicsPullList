import { store } from './store.js';
import { supabase } from './supabaseClient.js';

const tela = document.getElementById('tela');
const cacheEdicoes = new Map();

let catalogo = [];
let meta = {};
let filtro = { texto: '', editora: 'todas', soAtivas: false };
let ordemCampo = 'nome'; // 'nome' | 'lancamento' | 'edicoes' | 'pendencias'
let ordemDir = 'asc';    // 'asc' | 'desc'

const EDITORAS = { dc: 'DC', marvel: 'Marvel' };

// Icones de olho (mostrar/ocultar senha) -- SVG inline, sem emoji.
const OLHO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
const OLHO_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9.9 4.24A9 9 0 0 1 12 4c6.4 0 10 7 10 7a13.2 13.2 0 0 1-1.7 2.7M6.6 6.6C3.8 8.2 2 12 2 12s3.6 7 10 7a9.5 9.5 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="m2 2 20 20"/></svg>';

// ---------------------------------------------------------------- utilidades

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function dataBR(iso) {
  const [a, m, d] = iso.split('-');
  return `${d}/${m}/${a}`;
}

/** Iniciais para a capa de fallback: "Absolute Batman" -> "AB". */
function iniciais(nome) {
  return nome.split(/\s+/).filter((p) => /^[A-Za-z]/.test(p)).slice(0, 2)
    .map((p) => p[0].toUpperCase()).join('');
}

function capa(serie, classe) {
  if (serie.capa) {
    return `<img class="${classe}" src="${esc(serie.capa)}" alt="" loading="lazy"
      onerror="this.replaceWith(Object.assign(document.createElement('div'),
        {className:'${classe} capa-vazia',textContent:${JSON.stringify(iniciais(serie.nome))}}))">`;
  }
  return `<div class="${classe} capa-vazia">${esc(iniciais(serie.nome))}</div>`;
}

async function edicoesDe(id) {
  if (!cacheEdicoes.has(id)) {
    const r = await fetch(`data/issues/${id}.json`);
    if (!r.ok) throw new Error(`edições de ${id} não encontradas`);
    cacheEdicoes.set(id, (await r.json()).edicoes);
  }
  return cacheEdicoes.get(id);
}

/** Edicoes ja publicadas que ainda nao foram marcadas como lidas. */
async function naoLidas(id) {
  const [edicoes, lidas] = await Promise.all([edicoesDe(id), store.lidas(id)]);
  return edicoes.filter((e) => !lidas.has(e.numero) && e.data <= meta.referencia).length;
}

// ------------------------------------------------------------------ catalogo

// Filtros compartilhados entre as abas: editora e "so em publicacao". A busca por
// texto NAO entra aqui de proposito -- ela e so do catalogo; se entrasse, pesquisar
// no catalogo filtraria tambem a aba Seguindo.
function aplicarFiltro(lista) {
  return lista.filter((s) => (
    (filtro.editora === 'todas' || s.editora === filtro.editora)
    && (!filtro.soAtivas || s.status === 'em-publicacao')
  ));
}

// Ordenacao compartilhada por Catalogo e Seguindo. `pend` (mapa id->nao lidas) so
// existe no Seguindo; sem ele, "pendencias" cai para 0 (fica so o desempate por nome).
const ORDEM_ROTULO = { nome: 'A–Z', lancamento: 'Lançamento', edicoes: 'Edições', pendencias: 'Pendências' };

function ordenarLista(lista, pend) {
  const chave = {
    nome: (s) => s.nome.toLowerCase(),
    lancamento: (s) => s.ultima_edicao.data,
    edicoes: (s) => s.edicoes_conhecidas || 0,
    pendencias: (s) => (pend ? pend.get(s.id) || 0 : 0),
  }[ordemCampo] || ((s) => s.nome.toLowerCase());
  const dir = ordemDir === 'asc' ? 1 : -1;
  return [...lista].sort((a, b) => {
    const ka = chave(a);
    const kb = chave(b);
    if (ka < kb) return -dir;
    if (ka > kb) return dir;
    return a.nome.localeCompare(b.nome);
  });
}

function controleOrdem(campos) {
  return `
    <span class="janela-rot">ordenar:</span>
    <div class="segmentos">
      ${campos.map((c) => `<button data-ordem-campo="${c}" class="${ordemCampo === c ? 'ativo' : ''}">${ORDEM_ROTULO[c]}</button>`).join('')}
    </div>
    <button class="alternador" data-ordem-dir title="${ordemDir === 'asc' ? 'Crescente' : 'Decrescente'}">
      ${ordemDir === 'asc' ? '↑ cresc.' : '↓ decr.'}
    </button>`;
}

/** Nome com o volume quando ele distingue relancamentos: "Batman (2016)". */
function rotulo(serie) {
  return serie.ano_inicio ? `${serie.nome} (${serie.ano_inicio})` : serie.nome;
}

function cartao(serie, seguindo, pendentes) {
  const marca = pendentes > 0
    ? `<span class="marcador" title="${pendentes} não lidas">${pendentes}</span>` : '';
  const proxima = serie.proxima_edicao
    ? `<span class="fita">#${esc(serie.proxima_edicao.numero)} em ${dataBR(serie.proxima_edicao.data)}</span>`
    : '';
  return `
    <article class="cartao ed-${serie.editora} ${serie.status === 'sem-noticia' ? 'parada' : ''}">
      <a class="cartao-alvo" href="#/serie/${encodeURIComponent(serie.id)}">
        <div class="cartao-capa">${capa(serie, 'capa')}${marca}${proxima}</div>
        <h3>${esc(rotulo(serie))}</h3>
      </a>
      <p class="cartao-meta">
        ${EDITORAS[serie.editora]} · ${serie.edicoes_conhecidas} ${serie.edicoes_conhecidas === 1 ? 'edição' : 'edições'}
        · última #${esc(serie.ultima_edicao.numero)}
      </p>
      <button class="botao-seguir ${seguindo ? 'ativo' : ''}" data-seguir="${esc(serie.id)}">
        ${seguindo ? 'Seguindo' : 'Seguir'}
      </button>
    </article>`;
}

/** Os controles de editora e de "em publicação", compartilhados pelas telas. */
function controles() {
  return `
    <div class="segmentos">
      ${['todas', 'dc', 'marvel'].map((v) => `
        <button data-editora="${v}" class="${filtro.editora === v ? 'ativo' : ''}">
          ${v === 'todas' ? 'Todas' : EDITORAS[v]}
        </button>`).join('')}
    </div>
    <button class="alternador ${filtro.soAtivas ? 'ativo' : ''}" data-so-ativas>
      Só em publicação
    </button>`;
}

async function grade(lista, vazio) {
  if (!lista.length) return `<p class="vazio">${vazio}</p>`;
  const seguindo = await store.seguindo();
  const pendentes = await Promise.all(
    lista.map((s) => (seguindo.has(s.id) ? naoLidas(s.id) : Promise.resolve(0)))
  );
  return `<div class="grade">${
    lista.map((s, i) => cartao(s, seguindo.has(s.id), pendentes[i])).join('')
  }</div>`;
}

async function verCatalogo() {
  const texto = filtro.texto.trim().toLowerCase();
  const lista = ordenarLista(
    aplicarFiltro(catalogo).filter((s) => !texto || s.nome.toLowerCase().includes(texto)),
    null,
  );
  tela.innerHTML = `
    <div class="barra barra-dash">
      <input id="busca" type="search" placeholder="Buscar título…" value="${esc(filtro.texto)}">
      ${controles()}
      ${controleOrdem(['nome', 'lancamento', 'edicoes'])}
      <span class="contagem">${lista.length} de ${catalogo.length}</span>
    </div>
    ${await grade(lista, 'Nenhum título com esse filtro.')}`;

  const busca = document.getElementById('busca');
  busca.addEventListener('input', () => {
    filtro.texto = busca.value;
    clearTimeout(busca._t);
    busca._t = setTimeout(async () => {
      const foco = document.activeElement === busca;
      const pos = busca.selectionStart;
      await verCatalogo();
      if (foco) {
        const novo = document.getElementById('busca');
        novo.focus();
        novo.setSelectionRange(pos, pos);
      }
    }, 150);
  });
}

let segFiltro = 'todas'; // 'todas' | 'pendentes' | 'emdia'

async function verSeguindo() {
  const seguindo = await store.seguindo();
  let lista = aplicarFiltro(catalogo.filter((s) => seguindo.has(s.id)));

  // Pendências por série (edições já publicadas e não lidas) -- base do filtro
  // "com pendências/em dia" e da ordenação por pendências.
  const pend = new Map(await Promise.all(lista.map(async (s) => [s.id, await naoLidas(s.id)])));
  if (segFiltro === 'pendentes') lista = lista.filter((s) => pend.get(s.id) > 0);
  else if (segFiltro === 'emdia') lista = lista.filter((s) => pend.get(s.id) === 0);
  lista = ordenarLista(lista, pend);

  const filtros = [['todas', 'Todas'], ['pendentes', 'Com pendências'], ['emdia', 'Em dia']];
  tela.innerHTML = `
    <div class="barra barra-dash">
      <h2 class="titulo-secao">Seguindo</h2>
      <div class="segmentos">
        ${filtros.map(([v, r]) => `<button data-seg-filtro="${v}" class="${segFiltro === v ? 'ativo' : ''}">${r}</button>`).join('')}
      </div>
      ${controles()}
      ${controleOrdem(['nome', 'lancamento', 'edicoes', 'pendencias'])}
    </div>
    ${await grade(lista, seguindo.size
      ? 'Nenhum título seguido com esse filtro.'
      : 'Você ainda não segue nenhum título. Vá ao <a href="#/catalogo">catálogo</a> e clique em <b>Seguir</b>.')}`;
}

// ---------------------------------------------------------------- dashboard

let dashModo = 'geral'; // 'geral' | 'colecao'
let dashJanela = 12;    // meses da janela de tempo; 0 = tudo
let stats = null;

async function carregarStats() {
  if (stats) return stats;
  try {
    stats = await fetch('data/stats.json').then((r) => r.json());
  } catch {
    stats = { por_mes: [], top_series: [] };
  }
  return stats;
}

/** Catalogo filtrado por editora e "so em publicacao" (ignora a busca). */
function baseDash() {
  return catalogo.filter((s) => (
    (filtro.editora === 'todas' || s.editora === filtro.editora)
    && (!filtro.soAtivas || s.status === 'em-publicacao')
  ));
}

/** Primeiro mes (AAAA-MM) da janela atual; '' quando a janela e "tudo". */
function mesCorte() {
  if (!dashJanela) return '';
  const [a, m] = meta.referencia.split('-').map(Number);
  const idx = a * 12 + (m - 1) - (dashJanela - 1);
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, '0')}`;
}

/** Aplica editora + janela de tempo a uma lista {mes,dc,marvel}. */
function mesesFiltrados(lista) {
  const corte = mesCorte();
  return lista
    .filter((x) => !corte || x.mes >= corte)
    .map((x) => {
      const dc = filtro.editora === 'marvel' ? 0 : x.dc;
      const marvel = filtro.editora === 'dc' ? 0 : x.marvel;
      return { mes: x.mes, dc, marvel, total: dc + marvel };
    });
}

const tile = (valor, rotulo, cor) =>
  `<div class="tile">
    <span class="tile-num"${cor ? ` style="color:${cor}"` : ''}>${valor}</span>
    <span class="tile-rot">${rotulo}</span>
  </div>`;

/** Segmentos de janela de tempo por data de publicacao. */
function controleJanela() {
  const ops = [[3, '3m'], [6, '6m'], [12, '12m'], [0, 'Tudo']];
  return `<div class="segmentos">
    ${ops.map(([v, r]) => `<button data-dash-janela="${v}" class="${dashJanela === v ? 'ativo' : ''}">${r}</button>`).join('')}
  </div>`;
}

/** Grafico de barras empilhadas DC/Marvel por mes. Rola na horizontal se largo. */
function graficoMes(dados) {
  if (!dados.length) return '<p class="nota">Sem edições nessa janela.</p>';
  const max = Math.max(...dados.map((x) => x.total), 1);
  return `<div class="grafico">
    ${dados.map((x) => `
      <div class="col" title="${x.mes} · DC ${x.dc} · Marvel ${x.marvel} · total ${x.total}">
        <div class="col-bar">
          <span class="seg-dc" style="height:${(x.dc / max * 100).toFixed(1)}%"></span>
          <span class="seg-mv" style="height:${(x.marvel / max * 100).toFixed(1)}%"></span>
        </div>
        <span class="col-rot">${x.mes.slice(5)}<span class="col-ano">/${x.mes.slice(2, 4)}</span></span>
      </div>`).join('')}
  </div>`;
}

/** Ranking de titulos por numero de edicoes. */
function rankingSeries(lista) {
  const top = lista.filter((s) => filtro.editora === 'todas' || s.editora === filtro.editora).slice(0, 12);
  if (!top.length) return '<p class="nota">Sem títulos com esse filtro.</p>';
  const max = Math.max(...top.map((s) => s.edicoes), 1);
  return `<ul class="rank">
    ${top.map((s) => `<li>
      <a href="#/serie/${encodeURIComponent(s.id)}" class="rank-nome ed-${s.editora}">${esc(s.nome)}</a>
      <span class="rank-bar"><span class="seg-${s.editora === 'dc' ? 'dc' : 'mv'}" style="width:${(s.edicoes / max * 100).toFixed(1)}%"></span></span>
      <span class="rank-num">${s.edicoes}</span>
    </li>`).join('')}
  </ul>`;
}

const legendaMini = `<span class="leg-mini"><i class="pt seg-dc"></i>DC <i class="pt seg-mv"></i>Marvel</span>`;

function listaProximos(series, vazio) {
  const prox = series.filter((s) => s.proxima_edicao)
    .sort((a, b) => a.proxima_edicao.data.localeCompare(b.proxima_edicao.data)).slice(0, 10);
  if (!prox.length) return `<p class="nota">${vazio}</p>`;
  return `<ul class="dash-lista">
    ${prox.map((s) => `<li>
      <a href="#/serie/${encodeURIComponent(s.id)}" class="ed-${s.editora}">${esc(s.nome)} <b>#${esc(s.proxima_edicao.numero)}</b></a>
      <span class="dash-data">${dataBR(s.proxima_edicao.data)}</span>
    </li>`).join('')}
  </ul>`;
}

// -- Geral: leitura corporativa do catalogo -------------------------------

function dashGeral(base) {
  const meses = mesesFiltrados(stats.por_mes);
  const edPeriodo = meses.reduce((a, x) => a + x.total, 0);
  const emPub = base.filter((s) => s.status === 'em-publicacao').length;
  const media = meses.length ? Math.round(edPeriodo / meses.length) : 0;
  return `
    <div class="tiles">
      ${tile(base.length, 'títulos no catálogo')}
      ${tile(emPub, 'em publicação', 'var(--ok)')}
      ${tile(edPeriodo, 'edições na janela')}
      ${tile(media, 'média de edições/mês')}
    </div>
    <div class="paineis">
      <section class="painel painel-largo">
        <div class="painel-cabecalho">
          <h3 class="painel-titulo">Edições publicadas por mês</h3>${legendaMini}
        </div>
        ${graficoMes(meses)}
      </section>
      <section class="painel">
        <h3 class="painel-titulo">Títulos com mais publicações</h3>
        ${rankingSeries(stats.top_series)}
      </section>
      <section class="painel">
        <h3 class="painel-titulo">Próximos lançamentos anunciados</h3>
        ${listaProximos(base, 'Nenhuma próxima edição anunciada com esse filtro.')}
      </section>
    </div>`;
}

// -- Colecao: leitura de colecionador -------------------------------------

async function dashColecao(base) {
  const seguindo = await store.seguindo();
  const seguidas = base.filter((s) => seguindo.has(s.id));
  if (!seguidas.length) {
    return `<p class="vazio">Você não segue nenhum título${filtro.editora !== 'todas' ? ' dessa editora' : ''} ainda.
      Vá ao <a href="#/catalogo">catálogo</a> e clique em <b>Seguir</b>.</p>`;
  }
  const porMes = {};
  const dados = await Promise.all(seguidas.map(async (s) => {
    const [edicoes, lidas] = await Promise.all([edicoesDe(s.id), store.lidas(s.id)]);
    for (const e of edicoes) {
      const mes = (e.data || '').slice(0, 7);
      if (mes.length === 7) (porMes[mes] ||= { dc: 0, marvel: 0 })[s.editora]++;
    }
    const pub = edicoes.filter((e) => e.data <= meta.referencia);
    const lidasN = pub.filter((e) => lidas.has(e.numero));
    const gasto = lidasN.reduce((a, e) => a + (parseFloat(e.preco) || 0), 0);
    return { serie: s, pub: pub.length, lidas: lidasN.length, pend: pub.length - lidasN.length, gasto };
  }));
  const totPub = dados.reduce((a, d) => a + d.pub, 0);
  const totLidas = dados.reduce((a, d) => a + d.lidas, 0);
  const totPend = dados.reduce((a, d) => a + d.pend, 0);
  const gastoTot = dados.reduce((a, d) => a + d.gasto, 0);
  const prog = totPub ? Math.round((totLidas / totPub) * 100) : 0;
  const meses = mesesFiltrados(Object.keys(porMes).sort().map((m) => ({
    mes: m, dc: porMes[m].dc, marvel: porMes[m].marvel, total: porMes[m].dc + porMes[m].marvel,
  })));
  const pendentes = dados.filter((d) => d.pend > 0).sort((a, b) => b.pend - a.pend).slice(0, 10);
  return `
    <div class="tiles">
      ${tile(seguidas.length, 'séries seguidas')}
      ${tile(totLidas, 'edições lidas', 'var(--ok)')}
      ${tile(totPend, 'pendentes', totPend ? 'var(--marvel)' : '')}
      ${tile(prog + '%', 'progresso')}
      ${tile('US$ ' + gastoTot.toFixed(2), 'gasto estimado')}
    </div>
    <div class="paineis">
      <section class="painel painel-largo">
        <div class="painel-cabecalho">
          <h3 class="painel-titulo">Suas edições por mês</h3>${legendaMini}
        </div>
        ${graficoMes(meses)}
      </section>
      <section class="painel">
        <h3 class="painel-titulo">Seus próximos lançamentos</h3>
        ${listaProximos(seguidas, 'Nenhum lançamento anunciado nas séries que você segue.')}
      </section>
      <section class="painel">
        <h3 class="painel-titulo">Progresso de leitura</h3>
        <div class="barra-prop"><span class="seg" style="width:${prog}%;background:var(--ok)"></span></div>
        <div class="barra-leg"><span>${totLidas} lidas</span><span>${totPend} pendentes</span></div>
        <h3 class="painel-titulo" style="margin-top:18px">A pôr em dia</h3>
        ${pendentes.length ? `<ul class="dash-lista">
          ${pendentes.map((d) => `<li>
            <a href="#/serie/${encodeURIComponent(d.serie.id)}" class="ed-${d.serie.editora}">${esc(d.serie.nome)}</a>
            <span class="dash-data">${d.pend} não ${d.pend === 1 ? 'lida' : 'lidas'}</span>
          </li>`).join('')}
        </ul>` : '<p class="nota">Tudo em dia — nenhuma edição pendente.</p>'}
      </section>
    </div>`;
}

async function verDashboard() {
  await carregarStats();
  const base = baseDash();
  const corpo = dashModo === 'colecao' ? await dashColecao(base) : dashGeral(base);
  tela.innerHTML = `
    <div class="barra barra-dash">
      <div class="segmentos">
        <button data-dash-modo="geral" class="${dashModo === 'geral' ? 'ativo' : ''}">Geral</button>
        <button data-dash-modo="colecao" class="${dashModo === 'colecao' ? 'ativo' : ''}">Minha coleção</button>
      </div>
      ${controles()}
      <span class="janela-rot">janela:</span>${controleJanela()}
    </div>
    ${corpo}`;
}

// ----------------------------------------------------------------- novidades

/**
 * Contagem da pilula de alerta: lançamentos ainda NÃO lidos das séries seguidas,
 * publicados desde a última visita. Sem última visita, só os últimos 7 dias --
 * senão o back-catalogo inteiro de uma série recém-seguida viraria "novidade"
 * (era o "48" sem sentido). Edição futura não conta: ainda não saiu.
 */
async function contarNovidades() {
  const seguindo = await store.seguindo();
  const desde = await store.ultimaVisita();
  const piso = desde ? desde.slice(0, 10) : isoMaisDias(meta.referencia, -7);
  const porSerie = await Promise.all(
    catalogo.filter((s) => seguindo.has(s.id)).map(async (s) => {
      const [edicoes, lidas] = await Promise.all([edicoesDe(s.id), store.lidas(s.id)]);
      return edicoes.filter((e) => e.data > piso && e.data <= meta.referencia && !lidas.has(e.numero)).length;
    })
  );
  return porSerie.reduce((a, n) => a + n, 0);
}

// Estado da aba: janela em semanas, ou uma data especifica (mostra a semana dela).
let novSemanas = 4;
let novData = '';

/** Quarta-feira (ancora de semana da LOCG) da semana que contem `iso`. */
function quartaDaSemana(iso) {
  const d = new Date(iso + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() - 3 + 7) % 7)); // recua ate a quarta
  return d.toISOString().slice(0, 10);
}

function isoMaisDias(iso, dias) {
  const d = new Date(iso + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + dias);
  return d.toISOString().slice(0, 10);
}

/** Segmentos de editora (sem o "so em publicacao" do catalogo). */
function segmentosEditora() {
  return `<div class="segmentos">
    ${['todas', 'dc', 'marvel'].map((v) => `<button data-editora="${v}" class="${filtro.editora === v ? 'ativo' : ''}">${v === 'todas' ? 'Todas' : EDITORAS[v]}</button>`).join('')}
  </div>`;
}

function controleSemanas() {
  const ops = [[2, '2 sem'], [4, '4 sem'], [8, '8 sem'], [12, '12 sem']];
  return `<div class="segmentos">
    ${ops.map(([v, r]) => `<button data-nov-semanas="${v}" class="${!novData && novSemanas === v ? 'ativo' : ''}">${r}</button>`).join('')}
  </div>`;
}

async function verNovidades() {
  const seguindo = await store.seguindo();
  if (!seguindo.size) {
    tela.innerHTML = `<p class="vazio">Siga alguns títulos e esta aba passa a mostrar
      os lançamentos deles, semana a semana. Vá ao <a href="#/catalogo">catálogo</a>
      e clique em <b>Seguir</b>.</p>`;
    return;
  }
  const hoje = meta.referencia;
  const desde = await store.ultimaVisita();
  const series = catalogo.filter((s) => seguindo.has(s.id)
    && (filtro.editora === 'todas' || s.editora === filtro.editora));

  // Edicoes ja publicadas das series seguidas, com a marcacao de leitura.
  const itens = [];
  for (const serie of series) {
    const [edicoes, lidas] = await Promise.all([edicoesDe(serie.id), store.lidas(serie.id)]);
    for (const ed of edicoes) {
      if (ed.data <= hoje) {
        itens.push({ serie, ed, semana: quartaDaSemana(ed.data), lida: lidas.has(ed.numero) });
      }
    }
  }

  // Recorte: a semana de uma data especifica, ou as ultimas N semanas.
  const janela = novData
    ? itens.filter((i) => i.semana === quartaDaSemana(novData))
    : itens.filter((i) => i.ed.data >= isoMaisDias(hoje, -novSemanas * 7));

  const porSemana = janela.reduce((acc, i) => ((acc[i.semana] ||= []).push(i), acc), {});
  const semanas = Object.keys(porSemana).sort((a, b) => b.localeCompare(a));
  for (const s of semanas) {
    porSemana[s].sort((a, b) => b.ed.data.localeCompare(a.ed.data)
      || a.serie.nome.localeCompare(b.serie.nome));
  }
  const novos = desde ? janela.filter((i) => i.ed.data > desde).length : 0;

  tela.innerHTML = `
    <div class="barra barra-dash">
      <h2 class="titulo-secao">Novidades</h2>
      ${segmentosEditora()}
      ${controleSemanas()}
      <input type="date" id="nov-data" class="campo-data" value="${novData}" max="${hoje}" title="Ver uma semana específica">
      ${novData ? '<button class="alternador" id="nov-limpar">✕ data</button>' : ''}
    </div>
    <div class="barra-sub">
      <span class="contagem">${janela.length} ${janela.length === 1 ? 'lançamento' : 'lançamentos'}${novos ? ` · ${novos} novo${novos === 1 ? '' : 's'} desde ${dataBR(desde.slice(0, 10))}` : ''}</span>
      ${novos ? '<button id="btn-visto" class="botao-fantasma">Marcar tudo como visto</button>' : ''}
    </div>
    ${semanas.length ? semanas.map((sem) => `
      <section class="dia">
        <h3 class="dia-titulo">Semana de ${dataBR(sem)}<span class="dia-range"> — ${dataBR(isoMaisDias(sem, 6))}</span></h3>
        <ul class="lista-novidades">
          ${porSemana[sem].map(({ serie, ed, lida }) => `
            <li class="ed-${serie.editora}">
              <a href="#/serie/${encodeURIComponent(serie.id)}" class="ed-${serie.editora}">${esc(serie.nome)} <b>#${esc(ed.numero)}</b></a>
              <span class="nov-data">${dataBR(ed.data)}</span>
              ${desde && ed.data > desde ? '<span class="tag-novo">novo</span>' : ''}
              ${lida ? '<span class="tag-lida">lida</span>' : ''}
              ${ed.read_link ? `<a class="tag-ler" href="${esc(ed.read_link)}" target="_blank" rel="noopener">LER</a>` : ''}
            </li>`).join('')}
        </ul>
      </section>`).join('')
      : `<p class="vazio">Nenhum lançamento ${novData ? 'nessa semana' : `nas últimas ${novSemanas} semanas`}
        nas séries que você segue${filtro.editora !== 'todas' ? ' dessa editora' : ''}.</p>`}`;

  document.getElementById('btn-visto')?.addEventListener('click', async () => {
    await store.registrarVisita(hoje || new Date().toISOString().slice(0, 10));
    await atualizarPilulas();
    await verNovidades();
  });
  document.getElementById('nov-data')?.addEventListener('change', async (ev) => {
    novData = ev.target.value;
    await verNovidades();
  });
  document.getElementById('nov-limpar')?.addEventListener('click', async () => {
    novData = '';
    await verNovidades();
  });
}

// -------------------------------------------------------------------- admin

async function verAdmin() {
  const perfil = await store.perfil();
  if (perfil.role !== 'admin') {
    tela.innerHTML = '<p class="vazio">Acesso restrito — só administradores.</p>';
    return;
  }
  tela.innerHTML = '<p class="vazio">Carregando usuários…</p>';
  // A Edge Function confere o papel no servidor; aqui e so a interface.
  const { data } = await supabase.functions.invoke('admin-users', { body: { action: 'list' } });
  const usuarios = data?.users || [];
  const erroLista = data && !data.ok ? data.error : '';

  tela.innerHTML = `
    <div class="barra"><h2 class="titulo-secao">Administração</h2></div>
    <div class="paineis">
      <section class="painel">
        <h3 class="painel-titulo">Criar usuário</h3>
        <form id="form-usuario" class="form-admin">
          <label class="campo">Email
            <input type="email" id="novo-email" autocomplete="off" required>
          </label>
          <label class="campo">Senha inicial
            <span class="senha-wrap">
              <input type="password" id="novo-senha" autocomplete="off" minlength="6" required>
              <button type="button" class="olho" data-olho aria-label="Mostrar senha">${OLHO}</button>
            </span>
          </label>
          <label class="campo">Papel
            <select id="novo-role">
              <option value="user">Usuário</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <button type="submit" class="botao-seguir">Criar usuário</button>
          <p class="nota" id="status-usuario"></p>
        </form>
      </section>
      <section class="painel">
        <h3 class="painel-titulo">Usuários (${usuarios.length})</h3>
        ${erroLista ? `<p class="login-erro">${esc(erroLista)}</p>` : ''}
        <p class="nota" id="status-acao"></p>
        <ul class="dash-lista" id="lista-usuarios">
          ${usuarios.map((u) => {
            const eu = u.email === perfil.email;
            return `<li class="user-item ${u.blocked ? 'user-bloq' : ''}">
            <div class="user-linha">
              <span class="user-nome">${esc(u.email)}
                ${u.role === 'admin' ? '<span class="tag-admin">admin</span>' : ''}
                ${u.blocked ? '<span class="tag-bloq">bloqueado</span>' : ''}
              </span>
              <div class="user-acoes">
                <button class="botao-fantasma" data-reset="${esc(u.id)}">Resetar senha</button>
                ${eu ? '' : `<button class="botao-fantasma" data-block="${esc(u.id)}" data-blocked="${u.blocked ? '1' : '0'}">${u.blocked ? 'Desbloquear' : 'Bloquear'}</button>`}
                ${eu ? '' : `<button class="botao-fantasma btn-perigo" data-del="${esc(u.id)}" data-email="${esc(u.email)}">Deletar</button>`}
              </div>
            </div>
            <form class="reset-form" data-reset-form="${esc(u.id)}" hidden>
              <span class="senha-wrap">
                <input type="password" placeholder="Nova senha (mín. 6)" minlength="6" required autocomplete="off">
                <button type="button" class="olho" data-olho aria-label="Mostrar senha">${OLHO}</button>
              </span>
              <button type="submit" class="botao-seguir">Salvar</button>
              <button type="button" class="botao-fantasma" data-reset-cancel>Cancelar</button>
              <span class="nota reset-status"></span>
            </form>
          </li>`;
          }).join('') || '<li><span class="nota">Nenhum usuário.</span></li>'}
        </ul>
      </section>
    </div>`;

  document.getElementById('form-usuario').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const email = document.getElementById('novo-email').value.trim();
    const password = document.getElementById('novo-senha').value;
    const role = document.getElementById('novo-role').value;
    const status = document.getElementById('status-usuario');
    const btn = ev.target.querySelector('button');
    btn.disabled = true;
    status.textContent = 'Criando…';
    const { data: d, error } = await supabase.functions.invoke('admin-users',
      { body: { action: 'create', email, password, role } });
    if (error || !d?.ok) {
      status.textContent = `Erro: ${d?.error || error?.message || 'falha ao criar'}`;
      btn.disabled = false;
      return;
    }
    status.textContent = `Usuário ${d.user.email} criado (${d.user.role}).`;
    await verAdmin(); // recarrega a lista
  });

  const lista = document.getElementById('lista-usuarios');
  const statusAcao = document.getElementById('status-acao');
  const acaoUsuario = async (body, botao) => {
    botao.disabled = true;
    statusAcao.textContent = '…';
    const { data: d, error } = await supabase.functions.invoke('admin-users', { body });
    if (error || !d?.ok) {
      statusAcao.textContent = `Erro: ${d?.error || error?.message || 'falha'}`;
      botao.disabled = false;
      return;
    }
    await verAdmin(); // recarrega a lista
  };
  lista.addEventListener('click', (ev) => {
    const abrir = ev.target.closest('[data-reset]');
    if (abrir) {
      const f = lista.querySelector(`[data-reset-form="${CSS.escape(abrir.dataset.reset)}"]`);
      f.hidden = false;
      f.querySelector('input').focus();
      return;
    }
    const cancelar = ev.target.closest('[data-reset-cancel]');
    if (cancelar) { cancelar.closest('.reset-form').hidden = true; return; }
    const bloq = ev.target.closest('[data-block]');
    if (bloq) {
      acaoUsuario({ action: bloq.dataset.blocked === '1' ? 'unblock' : 'block', id: bloq.dataset.block }, bloq);
      return;
    }
    const del = ev.target.closest('[data-del]');
    if (del && confirm(`Deletar ${del.dataset.email}? Isso apaga a conta e a coleção dele — não dá para desfazer.`)) {
      acaoUsuario({ action: 'delete', id: del.dataset.del }, del);
    }
  });
  lista.addEventListener('submit', async (ev) => {
    const form = ev.target.closest('[data-reset-form]');
    if (!form) return;
    ev.preventDefault();
    const senha = form.querySelector('input').value;
    const status = form.querySelector('.reset-status');
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true;
    status.textContent = 'Salvando…';
    const { data: d, error } = await supabase.functions.invoke('admin-users',
      { body: { action: 'reset', id: form.dataset.resetForm, password: senha } });
    btn.disabled = false;
    if (error || !d?.ok) {
      status.textContent = `Erro: ${d?.error || error?.message || 'falha'}`;
      return;
    }
    status.textContent = 'Senha alterada.';
    form.querySelector('input').value = '';
  });
}

// -------------------------------------------------------------------- serie

async function verSerie(id) {
  const serie = catalogo.find((s) => s.id === id);
  if (!serie) {
    tela.innerHTML = '<p class="vazio">Título não encontrado.</p>';
    return;
  }
  const [edicoes, lidas, segue] = await Promise.all([
    edicoesDe(id), store.lidas(id), store.segue(id),
  ]);
  const hoje = meta.referencia;
  const pendentes = edicoes.filter((e) => !lidas.has(e.numero) && e.data <= hoje).length;
  // total_anunciado (quando existir) diz quantas edicoes a serie tem no total;
  // se soubermos menos, mostramos "X de Y" em vez de fingir que a lista fechou.
  const total = serie.total_anunciado;
  const contagem = (total && total > edicoes.length)
    ? `${edicoes.length} de ${total} edições`
    : `${edicoes.length} ${edicoes.length === 1 ? 'edição' : 'edições'}`;
  const resumo = (n) => `${contagem}
    · #${esc(serie.primeira_edicao.numero)} a #${esc(serie.ultima_edicao.numero)}
    · ${n} não ${n === 1 ? 'lida' : 'lidas'}`;

  tela.innerHTML = `
    <a class="voltar" href="#/catalogo">← catálogo</a>
    <div class="ficha ed-${serie.editora}">
      ${capa(serie, 'capa-grande')}
      <div class="ficha-info">
        <span class="selo">${EDITORAS[serie.editora]}${serie.tipo ? ` · ${esc(serie.tipo)}` : ''}</span>
        <h2>${esc(rotulo(serie))}</h2>
        <p class="ficha-meta">${resumo(pendentes)}</p>
        ${serie.proxima_edicao ? `<p class="anuncio">
          Próxima: <b>#${esc(serie.proxima_edicao.numero)}</b> em ${dataBR(serie.proxima_edicao.data)}
        </p>` : ''}
        <div class="linha-botoes">
          <button class="botao-seguir ${segue ? 'ativo' : ''}" data-seguir="${esc(id)}">
            ${segue ? 'Seguindo' : 'Seguir'}
          </button>
          <button class="botao-fantasma" data-todas="${pendentes ? '1' : '0'}">
            ${pendentes ? 'Marcar todas como lidas' : 'Desmarcar todas'}
          </button>
        </div>
      </div>
    </div>
    <ol class="edicoes">
      ${edicoes.map((e) => {
        const futura = e.data > hoje;
        return `
        <li class="${lidas.has(e.numero) ? 'lida' : ''} ${futura ? 'futura' : ''}">
          <label>
            <input type="checkbox" data-edicao="${esc(e.numero)}"
              ${lidas.has(e.numero) ? 'checked' : ''} ${futura ? 'disabled' : ''}>
            <span class="numero">#${esc(e.numero)}</span>
            <span class="data">${dataBR(e.data)}</span>
            ${futura ? '<span class="tag-futura">a sair</span>' : ''}
            ${e.preco ? `<span class="preco">US$ ${esc(e.preco)}</span>` : ''}
          </label>
          <span class="links">
            ${e.link ? `<a href="${esc(e.link)}" target="_blank" rel="noopener">ficha</a>` : ''}
            ${e.read_link ? `<a class="tag-ler" href="${esc(e.read_link)}" target="_blank" rel="noopener">LER</a>` : ''}
          </span>
        </li>`;
      }).join('')}
    </ol>`;

  tela.querySelectorAll('[data-edicao]').forEach((cx) => {
    cx.addEventListener('change', async () => {
      await store.marcarLida(id, cx.dataset.edicao, cx.checked);
      cx.closest('li').classList.toggle('lida', cx.checked);
      // Recalcula o cabecalho sem redesenhar a lista, para nao perder a rolagem.
      // Edicao que ainda nao saiu nao conta como pendente de leitura.
      const restantes = tela.querySelectorAll('.edicoes li:not(.lida):not(.futura)').length;
      tela.querySelector('.ficha-meta').innerHTML = resumo(restantes);
      const alternar = tela.querySelector('[data-todas]');
      alternar.dataset.todas = restantes ? '1' : '0';
      alternar.textContent = restantes ? 'Marcar todas como lidas' : 'Desmarcar todas';
      await atualizarPilulas();
    });
  });

  tela.querySelector('[data-todas]').addEventListener('click', async (ev) => {
    // Só as que já saíram: marcar como lida algo que ainda nem foi publicado
    // é o tipo de estado que depois faz o usuário perder uma edição de vista.
    const marcaveis = edicoes.filter((e) => e.data <= hoje).map((e) => e.numero);
    await store.marcarVarias(id, marcaveis, ev.currentTarget.dataset.todas === '1');
    await verSerie(id);
    await atualizarPilulas();
  });
}

// ------------------------------------------------------------------ chrome

async function atualizarPilulas() {
  const seguindo = await store.seguindo();
  const pSeg = document.getElementById('pilula-seguindo');
  pSeg.textContent = seguindo.size;
  pSeg.hidden = !seguindo.size;

  const nov = await contarNovidades();
  const pNov = document.getElementById('pilula-novidades');
  pNov.textContent = nov;
  pNov.hidden = !nov;
  // Espelha o alerta no botao do menu (que esconde a pilula quando fechado).
  document.getElementById('btn-menu').classList.toggle('tem-alerta', nov > 0);
}

function marcarAbaAtiva(aba) {
  document.querySelectorAll('.abas a').forEach((a) => {
    a.classList.toggle('ativa', a.dataset.aba === aba);
  });
}

async function rotear() {
  const rota = location.hash.slice(2) || 'catalogo';
  const [nome, arg] = rota.split('/');
  tela.scrollTo?.(0, 0);
  window.scrollTo(0, 0);

  if (nome === 'serie' && arg) {
    marcarAbaAtiva(null);
    await verSerie(decodeURIComponent(arg));
  } else if (nome === 'seguindo') {
    marcarAbaAtiva('seguindo');
    await verSeguindo();
  } else if (nome === 'novidades') {
    marcarAbaAtiva('novidades');
    await verNovidades();
  } else if (nome === 'dashboard') {
    marcarAbaAtiva('dashboard');
    await verDashboard();
  } else if (nome === 'admin') {
    marcarAbaAtiva('admin');
    await verAdmin();
  } else {
    marcarAbaAtiva('catalogo');
    await verCatalogo();
  }
}

// Delegacao: os botoes de seguir e de filtro sao recriados a cada render.
document.addEventListener('click', async (ev) => {
  const seguir = ev.target.closest('[data-seguir]');
  if (seguir) {
    ev.preventDefault();
    const ativo = await store.alternarSeguir(seguir.dataset.seguir);
    seguir.classList.toggle('ativo', ativo);
    seguir.textContent = ativo ? 'Seguindo' : 'Seguir';
    await atualizarPilulas();
    if (location.hash.startsWith('#/seguindo')) await verSeguindo();
    return;
  }
  const dashM = ev.target.closest('[data-dash-modo]');
  if (dashM) {
    dashModo = dashM.dataset.dashModo;
    await verDashboard();
    return;
  }
  const dashJ = ev.target.closest('[data-dash-janela]');
  if (dashJ) {
    dashJanela = Number(dashJ.dataset.dashJanela);
    await verDashboard();
    return;
  }
  const novS = ev.target.closest('[data-nov-semanas]');
  if (novS) {
    novSemanas = Number(novS.dataset.novSemanas);
    novData = '';
    await verNovidades();
    return;
  }
  const segF = ev.target.closest('[data-seg-filtro]');
  if (segF) {
    segFiltro = segF.dataset.segFiltro;
    await verSeguindo();
    return;
  }
  const oc = ev.target.closest('[data-ordem-campo]');
  if (oc) {
    ordemCampo = oc.dataset.ordemCampo;
    await rotear();
    return;
  }
  if (ev.target.closest('[data-ordem-dir]')) {
    ordemDir = ordemDir === 'asc' ? 'desc' : 'asc';
    await rotear();
    return;
  }
  const editora = ev.target.closest('[data-editora]');
  if (editora) {
    filtro.editora = editora.dataset.editora;
    await rotear();
    return;
  }
  if (ev.target.closest('[data-so-ativas]')) {
    filtro.soAtivas = !filtro.soAtivas;
    await rotear();
  }
});

// Olhinho de senha: mostra/oculta o campo. Vale para todo [data-olho] (login e admin).
document.addEventListener('click', (ev) => {
  const olho = ev.target.closest('[data-olho]');
  if (!olho) return;
  const input = olho.closest('.senha-wrap')?.querySelector('input');
  if (!input) return;
  const revelar = input.type === 'password';
  input.type = revelar ? 'text' : 'password';
  olho.innerHTML = revelar ? OLHO_OFF : OLHO;
  olho.classList.toggle('ativo', revelar);
  olho.setAttribute('aria-label', revelar ? 'Ocultar senha' : 'Mostrar senha');
});

// ------------------------------------------------------------- autenticacao

function traduzErro(msg) {
  if (/invalid login credentials/i.test(msg)) return 'Email ou senha incorretos.';
  if (/email not confirmed/i.test(msg)) return 'Conta ainda não confirmada — peça ao admin.';
  return msg;
}

function mostrarLogin(erro = '') {
  document.body.classList.add('deslogado');
  document.getElementById('usuario').hidden = true;
  tela.innerHTML = `
    <form id="form-login" class="login">
      <h2>Entrar</h2>
      <p class="nota">Pull List — acesso restrito. As contas são criadas pelo admin.</p>
      <label class="campo">Email
        <input type="email" id="login-email" autocomplete="username" required>
      </label>
      <label class="campo">Senha
        <span class="senha-wrap">
          <input type="password" id="login-senha" autocomplete="current-password" required>
          <button type="button" class="olho" data-olho aria-label="Mostrar senha">${OLHO}</button>
        </span>
      </label>
      ${erro ? `<p class="login-erro">${esc(erro)}</p>` : ''}
      <button type="submit" class="botao-seguir">Entrar</button>
    </form>`;
  document.getElementById('form-login').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const email = document.getElementById('login-email').value.trim();
    const senha = document.getElementById('login-senha').value;
    const btn = ev.target.querySelector('button');
    btn.disabled = true;
    btn.textContent = 'Entrando…';
    const { error } = await supabase.auth.signInWithPassword({ email, password: senha });
    if (error) mostrarLogin(traduzErro(error.message)); // sucesso: onAuthStateChange segue
  });
}

// ------------------------------------------------------------------- inicio

/** Boot do app depois de logado: carrega estado do usuario + catalogo e desenha. */
async function iniciarApp() {
  tela.innerHTML = '<p class="vazio">Carregando…</p>';
  let perfil;
  try {
    perfil = (await store.iniciar()).perfil;
    [catalogo, meta] = await Promise.all([
      fetch('data/series.json').then((r) => r.json()),
      fetch('data/meta.json').then((r) => r.json()),
    ]);
  } catch (e) {
    tela.innerHTML = `<p class="vazio">Não consegui carregar seus dados: ${esc(e.message)}.
      Recarregue a página.</p>`;
    return;
  }

  // So o admin vê a aba Admin.
  document.getElementById('aba-admin').hidden = perfil.role !== 'admin';
  document.getElementById('usuario-email').textContent = perfil.email || '';
  document.getElementById('usuario').hidden = false;
  document.body.classList.remove('deslogado');

  await atualizarPilulas();
  await rotear();
}

// Liga uma vez o que nao depende de login (roteamento, modal, logout).
let jaLigou = false;
function ligarChrome() {
  if (jaLigou) return;
  jaLigou = true;
  document.getElementById('btn-sair').addEventListener('click', () => supabase.auth.signOut());

  // Menu hamburguer (mobile): abre/fecha; fecha ao tocar num link ou trocar de rota.
  const btnMenu = document.getElementById('btn-menu');
  const fecharMenu = () => {
    document.body.classList.remove('menu-aberto');
    btnMenu.setAttribute('aria-expanded', 'false');
  };
  btnMenu.addEventListener('click', () => {
    const aberto = document.body.classList.toggle('menu-aberto');
    btnMenu.setAttribute('aria-expanded', String(aberto));
  });
  document.querySelector('.abas').addEventListener('click', (ev) => {
    if (ev.target.closest('a')) fecharMenu();
  });
  window.addEventListener('hashchange', () => { fecharMenu(); rotear(); });
}

let appIniciado = false;

async function iniciar() {
  document.body.classList.add('deslogado');
  ligarChrome();

  // Decide o estado inicial AQUI, fora do onAuthStateChange. Chamar metodos do
  // supabase dentro daquele callback trava (ele segura um lock interno).
  const { data: { session } } = await supabase.auth.getSession();
  if (session) { appIniciado = true; await iniciarApp(); } else mostrarLogin();

  // O callback so reage a login/logout, e defere o trabalho pesado com setTimeout
  // para rodar FORA do lock -- senao o getSession() do store.iniciar() deadlocka.
  supabase.auth.onAuthStateChange((evento, sessao) => {
    if (evento === 'SIGNED_IN' && !appIniciado) {
      appIniciado = true;
      setTimeout(iniciarApp, 0);
    } else if (evento === 'SIGNED_OUT') {
      appIniciado = false;
      store.limpar();
      cacheEdicoes.clear();
      mostrarLogin();
    }
  });
}

iniciar();
