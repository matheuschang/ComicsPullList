// Cliente Supabase do Pull List.
//
// A anon key e PUBLICA de proposito -- ela so identifica o projeto; o que protege
// os dados de cada usuario e o RLS (Row-Level Security) no banco. Pode ir para o
// repositorio e para o GitHub Pages sem problema. A service_role key (secreta)
// NUNCA entra aqui -- ela fica so na Edge Function da etapa 3b.
//
// O cliente vem por ESM CDN porque o site nao tem passo de build. Se um dia quiser
// tirar a dependencia de CDN, da para baixar o arquivo e servir de web/ (vendor).

// jsdelivr "+esm" entrega um bundle ESM limpo (sem eval/new Function), que passa
// ate em CSP estrito -- o esm.sh as vezes injeta eval e trava no preview sandbox.
import { createClient } from 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/+esm';

const SUPABASE_URL = 'https://bylzzmnrgaotjrxpvlcs.supabase.co';
const SUPABASE_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJ5bHp6bW5yZ2FvdGpyeHB2bGNzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODY1OTE0MDcsImV4cCI6MjEwMjE2NzQwN30.DUnPfwJMELDSivhy_0Ryg7mN8lx-7SxkFDqrdVIDqeA';

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON);
