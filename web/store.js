/**
 * Estado do usuario: o que ele segue, o que ja leu, quando visitou por ultimo.
 *
 * Fase 3: isto agora vive no Supabase (antes era localStorage). A interface
 * continua a mesma e toda `async`, entao o app.js quase nao muda -- foi para isso
 * que ela ja nascia assincrona.
 *
 * Estrategia: no login, `iniciar()` carrega tudo do usuario para um cache em
 * memoria (uma ida ao banco). As leituras (seguindo/lidas) saem do cache, rapidas;
 * as escritas atualizam o cache na hora e disparam a gravacao no banco. Assim o
 * app.js, que chama store.lidas(id) muitas vezes por render, nao vira N requisicoes.
 */

import { supabase } from './supabaseClient.js';

let cache = null; // { userId, seguindo:Set, lidas:Map<id,Set>, ultimaVisita, perfil }

// "JWT issued at future" e afins: o token acabou de ser emitido com o relogio do
// PC uns segundos adiantado, e o servidor recusa ate a hora dele alcancar. Em vez
// de exigir que o usuario sincronize o relogio, esperamos e tentamos de novo -- o
// mesmo token passa a valer em poucos segundos.
const ERRO_RELOGIO = /issued at future|not yet valid|before its|token used before/i;

async function comRetry(fazer, tentativas = 5) {
  for (let i = 0; ; i++) {
    const res = await fazer();
    if (res?.error && ERRO_RELOGIO.test(res.error.message || '') && i < tentativas) {
      await new Promise((r) => setTimeout(r, 1500));
      continue;
    }
    if (res?.error && ERRO_RELOGIO.test(res.error.message || '')) {
      throw new Error('Relógio do Windows dessincronizado — sincronize a hora '
        + '("Definir horário automaticamente") e recarregue.');
    }
    return res;
  }
}

async function carregar() {
  if (cache) return cache;
  // getSession() le a sessao local (sem rede); getUser() ia ao servidor e podia
  // pendurar o "Carregando..." se a rede engasgasse.
  const { data: { session } } = await supabase.auth.getSession();
  const user = session?.user;
  if (!user) throw new Error('sem sessão');

  const [follows, reads, perfil] = await Promise.all([
    comRetry(() => supabase.from('follows').select('serie_id').eq('user_id', user.id)),
    comRetry(() => supabase.from('reads').select('serie_id, numero').eq('user_id', user.id)),
    comRetry(() => supabase.from('profiles').select('email, role, ultima_visita').eq('id', user.id).single()),
  ]);
  if (follows.error) throw follows.error;
  if (reads.error) throw reads.error;

  const lidas = new Map();
  for (const r of reads.data || []) {
    if (!lidas.has(r.serie_id)) lidas.set(r.serie_id, new Set());
    lidas.get(r.serie_id).add(r.numero);
  }

  cache = {
    userId: user.id,
    seguindo: new Set((follows.data || []).map((f) => f.serie_id)),
    lidas,
    ultimaVisita: perfil.data?.ultima_visita || null,
    perfil: perfil.data || { email: user.email, role: 'user' },
  };
  return cache;
}

export const store = {
  /** Carrega o estado do usuario logado. Chamado uma vez, apos o login. */
  async iniciar() { return carregar(); },

  /** Esquece o cache (usado no logout). */
  limpar() { cache = null; },

  /** Perfil do usuario logado: { email, role }. */
  async perfil() { return (await carregar()).perfil; },

  async seguindo() {
    return new Set((await carregar()).seguindo);
  },

  async segue(id) {
    return (await carregar()).seguindo.has(id);
  },

  async alternarSeguir(id) {
    const c = await carregar();
    if (c.seguindo.has(id)) {
      c.seguindo.delete(id);
      await comRetry(() => supabase.from('follows').delete().eq('user_id', c.userId).eq('serie_id', id));
      return false;
    }
    c.seguindo.add(id);
    await comRetry(() => supabase.from('follows').insert({ user_id: c.userId, serie_id: id }));
    return true;
  },

  async lidas(idSerie) {
    return new Set((await carregar()).lidas.get(idSerie) || []);
  },

  async marcarLida(idSerie, numero, lida) {
    const c = await carregar();
    let set = c.lidas.get(idSerie);
    if (!set) { set = new Set(); c.lidas.set(idSerie, set); }
    if (lida) {
      set.add(numero);
      await comRetry(() => supabase.from('reads').upsert({ user_id: c.userId, serie_id: idSerie, numero }));
    } else {
      set.delete(numero);
      await comRetry(() => supabase.from('reads').delete()
        .eq('user_id', c.userId).eq('serie_id', idSerie).eq('numero', numero));
    }
  },

  /** Marca um lote de uma vez -- "li tudo ate a #12". */
  async marcarVarias(idSerie, numeros, lida) {
    const c = await carregar();
    let set = c.lidas.get(idSerie);
    if (!set) { set = new Set(); c.lidas.set(idSerie, set); }
    if (lida) {
      numeros.forEach((n) => set.add(n));
      await comRetry(() => supabase.from('reads').upsert(
        numeros.map((n) => ({ user_id: c.userId, serie_id: idSerie, numero: n }))));
    } else {
      numeros.forEach((n) => set.delete(n));
      await comRetry(() => supabase.from('reads').delete()
        .eq('user_id', c.userId).eq('serie_id', idSerie).in('numero', numeros));
    }
  },

  async ultimaVisita() {
    return (await carregar()).ultimaVisita;
  },

  async registrarVisita(quando) {
    const c = await carregar();
    c.ultimaVisita = quando;
    await comRetry(() => supabase.from('profiles').update({ ultima_visita: quando }).eq('id', c.userId));
  },
};
