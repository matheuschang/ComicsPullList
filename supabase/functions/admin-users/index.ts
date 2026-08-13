// Edge Function: admin-users
//
// Cria e lista usuarios do Pull List. So um usuario com role 'admin' consegue
// chamar -- a checagem e feita no servidor, com a service_role key, que NUNCA sai
// daqui (nao vai pro cliente). Criar contas de auth exige essa chave, por isso
// esta parte e server-side.
//
// Roda no Deno (Supabase Edge Functions). SUPABASE_URL, SUPABASE_ANON_KEY e
// SUPABASE_SERVICE_ROLE_KEY ja vem prontos no ambiente da funcao -- nao precisa
// configurar segredo nenhum.
//
// Deploy pelo painel: Edge Functions -> Create a new function -> nome "admin-users"
// -> cola este arquivo -> Deploy.

import { createClient } from 'npm:@supabase/supabase-js@2';

// O supabase-js manda tambem os headers `apikey` e `x-client-info` -- o preflight
// exige que TODOS os headers pedidos estejam liberados, senao o navegador bloqueia
// ("Failed to send a request"). Faltavam esses dois.
const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};

// Sempre responde 200 com { ok, ... } -- o cliente so olha `ok`, sem ter que ler
// corpo de erro de status != 2xx (que o supabase-js esconde).
const json = (body: unknown) =>
  new Response(JSON.stringify(body), { headers: { ...cors, 'Content-Type': 'application/json' } });

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: cors });

  const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
  const ANON = Deno.env.get('SUPABASE_ANON_KEY')!;
  const SERVICE = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;

  // 1. Quem chamou? (usa o token do header)
  const comoUsuario = createClient(SUPABASE_URL, ANON, {
    global: { headers: { Authorization: req.headers.get('Authorization') ?? '' } },
  });
  const { data: { user } } = await comoUsuario.auth.getUser();
  if (!user) return json({ ok: false, error: 'Não autenticado.' });

  // 2. E admin? (service role ignora RLS)
  const admin = createClient(SUPABASE_URL, SERVICE);
  const { data: perfil } = await admin.from('profiles').select('role').eq('id', user.id).single();
  if (perfil?.role !== 'admin') return json({ ok: false, error: 'Só administradores.' });

  // 3. Acao
  const { action, email, password, role, id } = await req.json().catch(() => ({}));

  if (action === 'list') {
    const { data: perfis, error } = await admin.from('profiles')
      .select('id, email, role, criado_em').order('criado_em', { ascending: true });
    if (error) return json({ ok: false, error: error.message });
    // Cruza com a auth pra saber quem esta bloqueado (banned_until no futuro).
    const { data: lista } = await admin.auth.admin.listUsers();
    const ban = new Map((lista?.users ?? []).map((u) => [u.id, (u as { banned_until?: string }).banned_until]));
    const users = perfis.map((p) => ({ ...p, blocked: !!ban.get(p.id) }));
    return json({ ok: true, users });
  }

  if (action === 'create') {
    if (!email || !password) return json({ ok: false, error: 'Email e senha são obrigatórios.' });
    const { data, error } = await admin.auth.admin.createUser({ email, password, email_confirm: true });
    if (error) return json({ ok: false, error: error.message });
    // O trigger cria o perfil como 'user'; se for admin, promove.
    if (role === 'admin') await admin.from('profiles').update({ role: 'admin' }).eq('id', data.user.id);
    return json({ ok: true, user: { id: data.user.id, email: data.user.email, role: role || 'user' } });
  }

  if (action === 'reset') {
    if (!id || !password) return json({ ok: false, error: 'ID e nova senha são obrigatórios.' });
    const { error } = await admin.auth.admin.updateUserById(id, { password });
    return error ? json({ ok: false, error: error.message }) : json({ ok: true });
  }

  if (action === 'block' || action === 'unblock' || action === 'delete') {
    if (!id) return json({ ok: false, error: 'ID obrigatório.' });
    if (id === user.id) return json({ ok: false, error: 'Não dá para fazer isso com a própria conta.' });
    if (action === 'delete') {
      // As tabelas follows/reads/profiles tem ON DELETE CASCADE -- somem junto.
      const { error } = await admin.auth.admin.deleteUser(id);
      return error ? json({ ok: false, error: error.message }) : json({ ok: true });
    }
    // Bloqueio = ban longo (~100 anos); desbloqueio = 'none'. Bloqueado nao loga.
    const { error } = await admin.auth.admin.updateUserById(id, {
      ban_duration: action === 'block' ? '876000h' : 'none',
    });
    return error ? json({ ok: false, error: error.message }) : json({ ok: true });
  }

  return json({ ok: false, error: 'Ação inválida.' });
});
